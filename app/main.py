"""YouTube Music ZimaOS: simple, authenticated, sequential audio download queue."""
from __future__ import annotations
import asyncio
import hashlib
import errno
import hmac
import logging
import os
import queue
import re
import secrets
import subprocess
import sys
import signal
import tempfile
import shutil
from io import BytesIO
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Annotated, Literal

import requests
import yt_dlp
from PIL import Image, ImageOps, UnidentifiedImageError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1, TPE2, WOAS
from mutagen.mp4 import MP4
from pydantic import BaseModel, Field

from metadata import VIDEO_ID, clean, clean_filename, guess_metadata, youtube_video_url

VERSION = '1.5.5'
CONFIG = Path(os.environ.get('CONFIG_DIR', '/config'))
MUSIC = Path(os.environ.get('MUSIC_DIR', '/music'))
DB = CONFIG / 'app.db'


def video_root() -> Path:
    # Default sibling of /music, mounted as ./videos:/videos by Docker Compose.
    # Derived dynamically to keep local test/storage paths isolated.
    return Path(os.environ.get('VIDEO_DIR', str(MUSIC.parent / 'videos')))

STATIC = Path(__file__).parent / 'static'
COOKIE = 'ytmusic_session'
SECURE_COOKIE = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
LOG = logging.getLogger('youtube-music-zimaos')
JOBS: queue.Queue[int] = queue.Queue()
CANCEL_EVENTS: dict[int, threading.Event] = {}
STREAM_SLOTS = threading.BoundedSemaphore(2)
STREAM_STATE_LOCK = threading.Lock()
STREAM_STATES: dict[str, dict] = {}
DB_LOCK = threading.RLock()
SETUP_LOCK = threading.Lock()
SEARCH_SEMAPHORE = asyncio.Semaphore(2)
COVER_REPAIR_LOCK = threading.Lock()
COVER_REPAIR_STATE = {"running": False, "total": 0, "processed": 0, "updated": 0, "skipped": 0, "failed": 0, "message": ""}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@contextmanager
def connect():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_storage():
    CONFIG.mkdir(parents=True, exist_ok=True)
    MUSIC.mkdir(parents=True, exist_ok=True)
    video_root().mkdir(parents=True, exist_ok=True)
    (CONFIG / 'work').mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires TEXT NOT NULL)')
        c.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT NOT NULL, url TEXT NOT NULL,
            artist TEXT NOT NULL, track TEXT NOT NULL, album TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        # Existing v1.4.0 databases are migrated in place; no data is deleted.
        columns = {row['name'] for row in c.execute('PRAGMA table_info(jobs)')}
        if 'media_type' not in columns:
            c.execute("ALTER TABLE jobs ADD COLUMN media_type TEXT NOT NULL DEFAULT 'audio'")
        if 'video_quality' not in columns:
            c.execute('ALTER TABLE jobs ADD COLUMN video_quality INTEGER NOT NULL DEFAULT 720')
        c.execute('CREATE INDEX IF NOT EXISTS idx_jobs_video ON jobs(video_id)')


def pwd_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 500_000)
    return f'{salt.hex()}:{digest.hex()}'


def pwd_verify(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split(':', 1)
        return hmac.compare_digest(
            hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 500_000),
            bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def make_session(user_id: int, response: Response):
    token = secrets.token_urlsafe(40)
    expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    with connect() as c:
        c.execute('INSERT INTO sessions(token_hash, user_id, expires) VALUES(?,?,?)',
                  (hashlib.sha256(token.encode()).hexdigest(), user_id, expiry))
        c.execute('DELETE FROM sessions WHERE expires < ?', (now(),))
    response.set_cookie(COOKIE, token, max_age=30 * 86400, path='/',
                        httponly=True, samesite='strict', secure=SECURE_COOKIE)


def user_id_for(request: Request) -> int | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with connect() as c:
        row = c.execute('SELECT user_id FROM sessions WHERE token_hash=? AND expires > ?',
                        (hashlib.sha256(token.encode()).hexdigest(), now())).fetchone()
    return row['user_id'] if row else None


def require_auth(request: Request) -> int:
    user = user_id_for(request)
    if not user:
        raise HTTPException(401, 'Faça login para continuar.')
    return user


def require_ajax(request: Request):
    if request.headers.get('x-app-request') != 'zimaos-music':
        raise HTTPException(403, 'Requisição não autorizada.')


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=6, max_length=128)


class VideoQuery(BaseModel):
    url: str = Field(min_length=15, max_length=2048)


class SearchQuery(BaseModel):
    query: str = Field(min_length=2, max_length=120)
    offset: int = Field(default=0, ge=0, le=400)
    limit: int = Field(default=20, ge=1, le=20)


class EditJob(BaseModel):
    artist: str = Field(min_length=2, max_length=120)
    track: str = Field(min_length=1, max_length=180)
    album: str = Field(default="", max_length=120)


class NewJob(VideoQuery):
    artist: str = Field(min_length=2, max_length=120)
    track: str = Field(min_length=1, max_length=180)
    album: str = Field(default='', max_length=120)
    media_type: Literal['audio', 'video'] = 'audio'
    video_quality: int = Field(default=720, ge=360, le=1080)


def status_update(job_id: int, status: str, message: str = '', progress: int | None = None, path: str | None = None):
    updates = ['status=?', 'message=?', 'updated_at=?']
    vals: list = [status, message[:700], now()]
    if progress is not None:
        updates.append('progress=?')
        vals.append(max(0, min(int(progress), 100)))
    if path is not None:
        updates.append('path=?')
        vals.append(path)
    vals.append(job_id)
    with connect() as c:
        c.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id=? AND status NOT IN ('cancelando','cancelado')", vals)



class JobCancelled(Exception):
    """Raised at a yt-dlp checkpoint if a user cancelled a job."""


def check_cancel(job_id: int):
    with DB_LOCK:
        event = CANCEL_EVENTS.get(job_id)
        if event and event.is_set():
            raise JobCancelled('Download cancelado pelo usuário.')
        with connect() as c:
            row = c.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row and row['status'] in ('cancelando', 'cancelado'):
            raise JobCancelled('Download cancelado pelo usuário.')


def editable_metadata(data: EditJob) -> tuple[str, str, str]:
    artist, track, album = clean(data.artist), clean(data.track), clean(data.album)
    if len(artist) < 2 or not track or clean_filename(artist) == 'Sem nome' or clean_filename(track) == 'Sem nome':
        raise HTTPException(400, 'Informe cantor e nome da música válidos.')
    return artist, track, album


def safe_media_path(rel_path: str, media_type: str = 'audio') -> Path:
    """Reject path traversal and symlink escapes in either media library."""
    if not rel_path:
        raise HTTPException(409, 'Esta tarefa não tem arquivo vinculado.')
    root = video_root() if media_type == 'video' else MUSIC
    suffix = '.mp4' if media_type == 'video' else '.mp3'
    path = (root / rel_path).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() != suffix:
        raise HTTPException(400, 'Caminho de mídia inválido.')
    return path


def safe_music_path(rel_path: str) -> Path:
    return safe_media_path(rel_path, 'audio')


def retag_mp4(path: Path, artist: str, track: str, album: str):
    """Update ordinary MP4/iTunes tags without transcoding video or audio."""
    media = MP4(path)
    media['\xa9nam'] = [track]
    media['\xa9ART'] = [artist]
    media['aART'] = [artist]
    if album:
        media['\xa9alb'] = [album]
    else:
        media.pop('\xa9alb', None)
    media.save()


def retag_mp3(path: Path, artist: str, track: str, album: str):
    """Edit only text fields; preserve thumbnail, original source URL and other tags."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for key in ('TIT2', 'TPE1', 'TPE2', 'TALB'):
        tags.delall(key)
    tags.add(TIT2(encoding=3, text=[track]))
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.add(TPE2(encoding=3, text=[artist]))
    if album:
        tags.add(TALB(encoding=3, text=[album]))
    tags.save(path, v2_version=3)


def set_stream_state(video_id: str, state: str, message: str):
    with STREAM_STATE_LOCK:
        STREAM_STATES[video_id] = {'state': state, 'message': message[:260], 'updated': time.monotonic()}
        if len(STREAM_STATES) > 120:
            for k in list(STREAM_STATES):
                if k != video_id and time.monotonic() - STREAM_STATES[k]['updated'] > 1800:
                    del STREAM_STATES[k]


def build_stream_commands(video_id: str, start_seconds: int = 0) -> tuple[list[str], list[str]]:
    """Fallback: native yt-dlp fetch piped to FFmpeg.

    Seeking after pipe input is slower than direct HTTP seeking, but also works
    on sources where FFmpeg is denied access to a temporary YouTube URL.
    """
    url = f'https://www.youtube.com/watch?v={video_id}'
    ytdlp = [sys.executable, '-m', 'yt_dlp', '--no-config', '--quiet', '--no-warnings',
             '--no-playlist', '--no-progress', '--socket-timeout', '25', '--retries', '2',
             '--fragment-retries', '2', '--cache-dir', str(CONFIG / 'cache'),
             '--downloader', 'native', '--format',
             'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio',
             '--output', '-', url]
    ffmpeg = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-i', 'pipe:0']
    if start_seconds:
        ffmpeg.extend(['-ss', str(start_seconds)])
    ffmpeg.extend(['-vn', '-map', '0:a:0', '-t', str(max(1, 1500 - start_seconds)),
                   '-codec:a', 'libmp3lame', '-b:a', '128k', '-f', 'mp3', 'pipe:1'])
    return ytdlp, ffmpeg


def direct_seek_command(video_id: str, start_seconds: int) -> list[str] | None:
    """Resolve a short-lived YouTube audio URL for an input-level FFmpeg seek.

    This is only tried for seeking. Normal playback keeps yt-dlp's downloader,
    which is more reliable against YouTube's occasional HTTP 403 responses.
    """
    options = {'quiet': True, 'no_warnings': True, 'noplaylist': True,
               'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio',
               'skip_download': True, 'socket_timeout': 20, 'retries': 1,
               'cachedir': str(CONFIG / 'cache')}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=False)
    url = info.get('url') if isinstance(info, dict) else None
    host = urlparse(url).hostname if isinstance(url, str) else None
    if not host or not (host.endswith('.googlevideo.com') or host == 'googlevideo.com'):
        return None
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
           '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '3']
    headers = info.get('http_headers') or {}
    allowed = ('User-Agent', 'Referer', 'Origin')
    safe_headers = ''.join(f'{key}: {str(headers[key]).replace(chr(13), "").replace(chr(10), "")}\\r\\n'
                           for key in allowed if key in headers)
    if safe_headers:
        cmd.extend(['-headers', safe_headers.replace('\\r\\n', '\r\n')])
    cmd.extend(['-ss', str(start_seconds), '-i', url,
                '-vn', '-map', '0:a:0', '-t', str(max(1, 1500 - start_seconds)),
                '-codec:a', 'libmp3lame', '-b:a', '128k', '-f', 'mp3', 'pipe:1'])
    return cmd


def stop_process(proc):
    if proc is None:
        return
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            proc.kill()
        proc.wait(timeout=2)


def stream_mp3(video_id: str, start_seconds: int = 0):
    """Send playable MP3 bytes as they arrive, with offset restart for seeking.

    Only a complete stream from 0:00 is cached. A seek from a later point
    never overwrites the whole-track MP3 cache with a truncated segment.
    """
    yt = ff = None
    temp = None
    output = preview_cache_dir() / f'{video_id}.mp3'
    total = 0
    successful = False
    with tempfile.TemporaryFile() as yt_err, tempfile.TemporaryFile() as ff_err:
        try:
            set_stream_state(video_id, 'connecting',
                             f'Avançando para {start_seconds}s...' if start_seconds else 'Conectando ao áudio do YouTube...')
            if start_seconds:
                local = existing_audio(video_id)
                if local:
                    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                           '-ss', str(start_seconds), '-i', str(local), '-vn', '-map', '0:a:0',
                           '-t', str(max(1, 1500 - start_seconds)), '-codec:a', 'libmp3lame',
                           '-b:a', '128k', '-f', 'mp3', 'pipe:1']
                    ff = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ff_err, start_new_session=True)
                else:
                    # Fast seek: let FFmpeg ask the source for data near the target position.
                    try:
                        cmd = direct_seek_command(video_id, start_seconds)
                    except Exception as exc:
                        LOG.info('Busca direta indisponível em %s: %s', video_id, type(exc).__name__)
                        cmd = None
                    if cmd:
                        ff = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ff_err, start_new_session=True)
                        first = ff.stdout.read1(32 * 1024)
                        if first:
                            set_stream_state(video_id, 'streaming', 'Reproduzindo a partir do ponto selecionado.')
                            total += len(first)
                            yield first
                        else:
                            stop_process(ff)
                            ff.stdout.close()
                            ff = None
                    if ff is None:
                        yt_cmd, ff_cmd = build_stream_commands(video_id, start_seconds)
                        yt = subprocess.Popen(yt_cmd, stdout=subprocess.PIPE, stderr=yt_err,
                                              start_new_session=True)
                        ff = subprocess.Popen(ff_cmd, stdin=yt.stdout, stdout=subprocess.PIPE,
                                              stderr=ff_err, start_new_session=True)
                        yt.stdout.close()
            else:
                yt_cmd, ff_cmd = build_stream_commands(video_id)
                yt = subprocess.Popen(yt_cmd, stdout=subprocess.PIPE, stderr=yt_err,
                                      start_new_session=True)
                ff = subprocess.Popen(ff_cmd, stdin=yt.stdout, stdout=subprocess.PIPE,
                                      stderr=ff_err, start_new_session=True)
                yt.stdout.close()
                temp = preview_cache_dir() / f'.{video_id}.{secrets.token_hex(8)}.part'
            with (temp.open('wb') if temp else contextmanager_none()) as cache:
                while True:
                    chunk = ff.stdout.read1(32 * 1024)
                    if not chunk:
                        break
                    if cache:
                        cache.write(chunk)
                    total += len(chunk)
                    set_stream_state(video_id, 'streaming', 'Reproduzindo em streaming.')
                    yield chunk
                if cache:
                    cache.flush()
            ff_ok = ff.wait(timeout=6) == 0
            yt_ok = yt is None or yt.wait(timeout=6) == 0
            successful = ff_ok and yt_ok and total > 1024
            if successful:
                if temp:
                    os.replace(temp, output)
                    clean_preview_cache()
                set_stream_state(video_id, 'ready', 'Reprodução concluída.' if start_seconds else 'Música em cache.')
            else:
                yt_err.seek(0); ff_err.seek(0)
                detail = (yt_err.read() + b' ' + ff_err.read()).decode('utf-8', errors='replace')[-300:]
                if '403' in detail:
                    detail = 'O YouTube recusou o áudio (HTTP 403).'
                elif '429' in detail:
                    detail = 'O YouTube limitou o acesso (HTTP 429).'
                set_stream_state(video_id, 'error', detail.strip() or 'Não foi possível reproduzir esta parte da música.')
        except (GeneratorExit, BrokenPipeError):
            set_stream_state(video_id, 'stopped', 'Reprodução interrompida.')
            raise
        except Exception as exc:
            LOG.warning('Falha no streaming %s: %s', video_id, exc, exc_info=True)
            set_stream_state(video_id, 'error', 'Falha no streaming: ' + str(exc)[:160])
        finally:
            stop_process(ff)
            stop_process(yt)
            for process in (ff, yt):
                if process:
                    for handle in (process.stdout, process.stdin):
                        if handle:
                            try: handle.close()
                            except OSError: pass
            if temp:
                temp.unlink(missing_ok=True)
            STREAM_SLOTS.release()


@contextmanager
def contextmanager_none():
    yield None


def get_info(url: str) -> dict:
    params = {'quiet': True, 'no_warnings': True, 'noplaylist': True,
              'skip_download': True, 'socket_timeout': 25, 'retries': 2,
              'cachedir': str(CONFIG / 'cache')}
    with yt_dlp.YoutubeDL(params) as ydl:
        info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict) or info.get('_type') in ('playlist', 'multi_video'):
            raise ValueError('Não foi possível analisar um vídeo individual.')
        return info


VIDEO_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')


def search_videos(query: str, offset: int = 0, limit: int = 20) -> tuple[list[dict], bool]:
    """Fetch a small page via yt-dlp's lazy search and playlist-items selector.

    No media is downloaded during search. The extra item determines has_more.
    Google/YouTube may stop returning items due to throttling or regional rules.
    """
    options = {
        'quiet': True, 'no_warnings': True, 'extract_flat': 'in_playlist',
        'skip_download': True, 'noplaylist': False, 'socket_timeout': 20,
        'retries': 1, 'lazy_playlist': True,
        'playlist_items': f'{offset + 1}:{offset + limit + 1}',
        'cachedir': str(CONFIG / 'cache'),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        result = ydl.extract_info(f'ytsearchall:{query}', download=False)
    if not isinstance(result, dict):
        raise ValueError('O YouTube não retornou resultados.')
    videos = []
    raw = list(result.get('entries') or [])
    for entry in raw[:limit]:
        if not isinstance(entry, dict):
            continue
        vid = str(entry.get('id') or '')
        if not VIDEO_ID_RE.fullmatch(vid):
            continue
        seconds = entry.get('duration')
        duration = int(seconds) if isinstance(seconds, (int, float)) and 0 <= seconds <= 86400 else None
        videos.append({
            'id': vid,
            'url': f'https://www.youtube.com/watch?v={vid}',
            'title': str(entry.get('title') or 'Sem título')[:230],
            'channel': str(entry.get('channel') or entry.get('uploader') or 'Canal não informado')[:150],
            'duration': duration,
            'thumbnail': f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg',
        })
    return videos, len(raw) > limit


# O link temporário googlevideo pode dar 403 antes de começar a reproduzir.
# Prévia: o yt-dlp faz o download/extração, e o FastAPI entrega um MP3 LOCAL
# com Content-Length e suporte a Range (inclusive para avançar/retroceder).
# Apenas a pasta /config/preview-cache é descartável. A biblioteca /music nunca é limpa.
PREVIEW_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix='music-preview')
PREVIEW_LOCK = threading.RLock()
PREVIEW_STATES: dict[str, dict] = {}
PREVIEW_CACHE_MAX_BYTES = 384 * 1024 * 1024
PREVIEW_CACHE_MAX_FILES = 30
PREVIEW_MAX_PENDING = 8


def preview_cache_dir() -> Path:
    directory = CONFIG / 'preview-cache'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def existing_audio(video_id: str) -> Path | None:
    """Prefer a previously downloaded track, when there is one."""
    with connect() as c:
        result = c.execute("SELECT path FROM jobs WHERE video_id=? AND media_type='audio' AND status='concluido' "
                           'ORDER BY id DESC LIMIT 1', (video_id,)).fetchone()
    if result and result['path']:
        file = (MUSIC / result['path']).resolve()
        if file.is_relative_to(MUSIC.resolve()) and file.is_file():
            return file
    file = preview_cache_dir() / f'{video_id}.mp3'
    return file if file.is_file() and file.stat().st_size > 0 else None


def clean_preview_cache() -> None:
    """LRU cleanup, only temporary playable previews, never user MP3s."""
    files = sorted(preview_cache_dir().glob('*.mp3'), key=lambda item: item.stat().st_mtime, reverse=True)
    total = 0
    for index, file in enumerate(files):
        try:
            size = file.stat().st_size
            if index >= PREVIEW_CACHE_MAX_FILES or (total + size > PREVIEW_CACHE_MAX_BYTES and index > 0):
                file.unlink(missing_ok=True)
            else:
                total += size
        except OSError:
            LOG.exception('Falha ao limpar cache de prévias')


def preview_state(video_id: str) -> dict:
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(400, 'Identificador da música inválido.')
    local = existing_audio(video_id)
    if local:
        try:
            if local.parent == preview_cache_dir():
                os.utime(local, None)
        except OSError:
            pass
        return {'state': 'ready', 'progress': 100, 'message': 'Pronto para ouvir.'}
    with PREVIEW_LOCK:
        cached = PREVIEW_STATES.get(video_id)
        if cached and cached['state'] == 'ready':
            del PREVIEW_STATES[video_id]  # cache expurgado: permitir nova preparação
        return dict(PREVIEW_STATES.get(video_id) or {
            'state': 'idle', 'progress': 0, 'message': 'Clique em tocar para preparar o áudio.'})


def update_preview(video_id: str, state: str, message: str, progress: int = 0):
    with PREVIEW_LOCK:
        PREVIEW_STATES[video_id] = {
            'state': state, 'message': message[:250], 'progress': max(0, min(int(progress), 100))}


def prepare_preview(video_id: str):
    work = CONFIG / 'preview-work' / video_id
    try:
        update_preview(video_id, 'preparing', 'Preparando áudio no servidor...', 1)
        work.mkdir(parents=True, exist_ok=True)
        preview_cache_dir().mkdir(parents=True, exist_ok=True)

        def limited_duration(info, *, incomplete=False):
            duration = info.get('duration')
            if duration and duration > 25 * 60:
                return 'Prévia limitada a vídeos de até 25 minutos.'
            return None

        last_update = [0.0]
        def progress_hook(data):
            if data.get('status') == 'downloading':
                total = data.get('total_bytes') or data.get('total_bytes_estimate')
                done = data.get('downloaded_bytes') or 0
                progress = min(92, int(done * 90 / total)) if total else 10
                if time.monotonic() - last_update[0] >= 0.8:
                    update_preview(video_id, 'preparing', 'Recebendo áudio do YouTube...', progress)
                    last_update[0] = time.monotonic()
            elif data.get('status') == 'finished':
                update_preview(video_id, 'preparing', 'Convertendo a prévia para MP3...', 94)

        opts = {
            'quiet': True, 'no_warnings': True, 'noplaylist': True,
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'socket_timeout': 25, 'retries': 3, 'fragment_retries': 3,
            'cachedir': str(CONFIG / 'cache'),
            'outtmpl': str(work / '%(id)s.%(ext)s'),
            'match_filter': limited_duration,
            'progress_hooks': [progress_hook],
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '128'}],
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=True)
        audio = work / f'{video_id}.mp3'
        if not audio.is_file():
            candidates = list(work.glob('*.mp3'))
            if len(candidates) != 1:
                raise RuntimeError('O YouTube não forneceu áudio reproduzível.')
            audio = candidates[0]
        if audio.stat().st_size == 0:
            raise RuntimeError('O áudio recebido estava vazio.')
        output = preview_cache_dir() / f'{video_id}.mp3'
        os.replace(audio, output)
        update_preview(video_id, 'ready', 'Pronto para ouvir.', 100)
        clean_preview_cache()
    except Exception as exc:
        LOG.warning('Falha ao preparar prévia %s: %s', video_id, exc, exc_info=True)
        error_text = str(exc)
        if '403' in error_text:
            reason = 'O YouTube recusou o áudio (HTTP 403). A faixa pode estar restrita ou temporariamente bloqueada.'
        elif '429' in error_text:
            reason = 'O YouTube limitou as solicitações (HTTP 429). Tente novamente mais tarde.'
        else:
            reason = 'Não foi possível preparar esta faixa: ' + error_text[:135]
        update_preview(video_id, 'error', reason)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def queue_preview(video_id: str) -> dict:
    current = preview_state(video_id)
    if current['state'] == 'ready':
        return current
    with PREVIEW_LOCK:
        state = PREVIEW_STATES.get(video_id)
        if state and state['state'] in ('queued', 'preparing'):
            return dict(state)
        running = sum(1 for v in PREVIEW_STATES.values() if v['state'] in ('queued', 'preparing'))
        if running >= PREVIEW_MAX_PENDING:
            raise HTTPException(429, 'Há muitas prévias sendo preparadas. Tente novamente em instantes.')
        if len(PREVIEW_STATES) > 120:
            for old in list(PREVIEW_STATES):
                if PREVIEW_STATES[old]['state'] not in ('queued', 'preparing'):
                    del PREVIEW_STATES[old]
                if len(PREVIEW_STATES) <= 80:
                    break
        PREVIEW_STATES[video_id] = {'state': 'queued', 'progress': 0, 'message': 'Na fila de reprodução...'}
        PREVIEW_POOL.submit(prepare_preview, video_id)
        return dict(PREVIEW_STATES[video_id])


def _youtube_thumbnail_candidates(info: dict):
    """Use artwork supplied with metadata first; fall back to the video's own still."""
    candidates = []
    thumbnails = info.get('thumbnails') or []
    if isinstance(thumbnails, list):
        for thumbnail in sorted((t for t in thumbnails if isinstance(t, dict)),
                                key=lambda t: (t.get('width') or 0) * (t.get('height') or 0), reverse=True):
            candidates.append(thumbnail.get('url') or '')
    candidates.insert(0, info.get('thumbnail') or '')
    video_id = str(info.get('id') or info.get('display_id') or '')
    if VIDEO_ID.fullmatch(video_id):
        candidates.extend([
            f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg',
            f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg',
        ])
    return list(dict.fromkeys(url for url in candidates if url))


def thumbnail_bytes(info: dict) -> tuple[bytes, str] | None:
    """Fetch trusted YouTube thumbnails and normalize JPG/PNG/WebP to ID3-compatible JPEG.

    Never follow redirects or download arbitrary hosts. Image verification and pixel limits
    protect the server against oversized or malformed thumbnails.
    """
    for url in _youtube_thumbnail_candidates(info)[:8]:
        parsed = urlparse(str(url))
        host = (parsed.hostname or '').lower()
        if parsed.scheme != 'https' or host not in ('i.ytimg.com', 'img.youtube.com') or parsed.port not in (None, 443):
            continue
        try:
            with requests.get(url, timeout=(5, 12), stream=True, allow_redirects=False) as resp:
                resp.raise_for_status()
                mime = resp.headers.get('content-type', '').split(';')[0].lower()
                if mime not in ('image/jpeg', 'image/png', 'image/webp', 'application/octet-stream'):
                    continue
                payload = bytearray()
                for chunk in resp.iter_content(65536):
                    payload.extend(chunk)
                    if len(payload) > 4_000_000:
                        break
                if not payload or len(payload) > 4_000_000:
                    continue
            with Image.open(BytesIO(payload)) as raw:
                if raw.format not in ('JPEG', 'PNG', 'WEBP') or raw.width * raw.height > 16_000_000:
                    continue
                # Copy only a resized RGB image, not extraneous file metadata.
                photo = ImageOps.exif_transpose(raw)
                photo.thumbnail((1400, 1400))
                if photo.mode != 'RGB':
                    background = Image.new('RGB', photo.size, '#101724')
                    if 'A' in photo.getbands():
                        background.paste(photo.convert('RGBA'), mask=photo.getchannel('A'))
                    else:
                        background.paste(photo.convert('RGB'))
                    photo = background
                encoded = BytesIO()
                photo.save(encoded, format='JPEG', quality=87, optimize=True)
                return encoded.getvalue(), 'image/jpeg'
        except (requests.RequestException, ValueError, OSError, UnidentifiedImageError):
            continue
    return None


def embedded_cover(path: Path) -> tuple[bytes, str] | None:
    """Read only an existing MP3's ID3 cover. Never touch the MP3 audio frames."""
    try:
        frames = ID3(path).getall('APIC')
        for frame in frames:
            if frame.mime in ('image/jpeg', 'image/png') and frame.data:
                return frame.data, frame.mime
    except (ID3NoHeaderError, OSError, ValueError):
        pass
    return None


def write_cover(path: Path, cover: tuple[bytes, str]):
    """Attach a cover without modifying any existing title, singer, album or MP3 audio."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    image, mime = cover
    tags.delall('APIC')
    tags.add(APIC(encoding=3, mime=mime, type=3, desc='Front Cover', data=image))
    tags.save(path, v2_version=3)


def tag_mp3(path: Path, artist: str, track: str, album: str, url: str, info: dict) -> bool:
    """Write ID3 tags and embed the actual video thumbnail (if obtainable)."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    for key in ('TIT2', 'TPE1', 'TPE2', 'TALB', 'WOAS'):
        tags.delall(key)
    tags.add(TIT2(encoding=3, text=[track]))
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.add(TPE2(encoding=3, text=[artist]))
    if album:
        tags.add(TALB(encoding=3, text=[album]))
    tags.add(WOAS(url=url))
    tags.save(path, v2_version=3)
    # If image download fails, keep an existing cover (do not wipe it out).
    image_info = dict(info or {})
    image_info.setdefault('id', youtube_video_url(url).split('v=')[-1])
    cover = thumbnail_bytes(image_info)
    if cover:
        write_cover(path, cover)
    return embedded_cover(path) is not None


def refresh_job_cover(job_id: int, *, force: bool = False) -> str:
    """Repair one completed MP3 using its video thumbnail without redownloading audio."""
    with DB_LOCK:
        with connect() as c:
            row = c.execute('SELECT status,path,media_type,video_id FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['status'] != 'concluido' or row['media_type'] != 'audio':
            raise HTTPException(409, 'Somente músicas MP3 concluídas podem receber capa.')
        path = safe_music_path(row['path'])
        if not path.is_file():
            raise HTTPException(404, 'O MP3 não foi encontrado no servidor.')
        if not force and embedded_cover(path):
            return 'skip'
        video_id = row['video_id']
        original_path = row['path']
    if not VIDEO_ID.fullmatch(video_id):
        raise HTTPException(400, 'Este download não possui um código de vídeo válido.')
    cover = thumbnail_bytes({'id': video_id})
    if not cover:
        raise HTTPException(424, 'Não foi possível obter uma capa do YouTube. O MP3 continua intacto.')
    with DB_LOCK:
        # The user may have edited/deleted/moved the file while the image was downloading.
        with connect() as c:
            latest = c.execute('SELECT status,path,media_type FROM jobs WHERE id=?', (job_id,)).fetchone()
            if (not latest or latest['status'] != 'concluido' or latest['media_type'] != 'audio'
                    or latest['path'] != original_path):
                raise HTTPException(409, 'O arquivo mudou durante a atualização da capa. Tente novamente.')
            path = safe_music_path(latest['path'])
            if not path.is_file():
                raise HTTPException(404, 'O arquivo não existe mais.')
            if not force and embedded_cover(path):
                return 'skip'
            try:
                write_cover(path, cover)
            except (ValueError, OSError) as exc:
                raise HTTPException(500, 'Falha ao salvar a capa no MP3.') from exc
            c.execute('UPDATE jobs SET message=?,updated_at=? WHERE id=?',
                      ('Capa incorporada no próprio MP3.', now(), job_id))
    return 'updated'


def run_cover_repair():
    """Background migration, at most one pass at a time, with progress visible in UI."""
    try:
        with connect() as c:
            rows = c.execute("SELECT id FROM jobs WHERE status='concluido' AND media_type='audio' ORDER BY id DESC").fetchall()
        with COVER_REPAIR_LOCK:
            COVER_REPAIR_STATE['total'] = len(rows)
            COVER_REPAIR_STATE['message'] = 'Atualizando capas das músicas antigas...'
        for row in rows:
            try:
                result = refresh_job_cover(row['id'])
                key = 'updated' if result == 'updated' else 'skipped'
            except Exception as exc:
                LOG.warning('Não foi possível atualizar capa do download %s: %s', row['id'], exc)
                key = 'failed'
            with COVER_REPAIR_LOCK:
                COVER_REPAIR_STATE['processed'] += 1
                COVER_REPAIR_STATE[key] += 1
        with COVER_REPAIR_LOCK:
            COVER_REPAIR_STATE['message'] = 'Verificação das capas concluída.'
    finally:
        with COVER_REPAIR_LOCK:
            COVER_REPAIR_STATE['running'] = False


def download(job_id: int):
    with DB_LOCK:
        with connect() as c:
            job = c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job or job['status'] != 'pendente':
            return
        CANCEL_EVENTS[job_id] = threading.Event()
        status_update(job_id, 'baixando', 'Conectando ao YouTube...', 0)
    video_id, url = job['video_id'], job['url']
    is_video = job['media_type'] == 'video'
    quality = job['video_quality']
    suffix = '.mp4' if is_video else '.mp3'
    root = video_root() if is_video else MUSIC
    work = CONFIG / 'work' / str(job_id)
    last_progress = 0.0
    try:
        work.mkdir(parents=True, exist_ok=True)

        def hook(data):
            nonlocal last_progress
            check_cancel(job_id)
            if data.get('status') == 'downloading':
                total = data.get('total_bytes') or data.get('total_bytes_estimate')
                done = data.get('downloaded_bytes') or 0
                percent = int(done * 95 / total) if total else 0
                if time.monotonic() - last_progress >= 0.7:
                    status_update(job_id, 'baixando', 'Recebendo vídeo...' if is_video else 'Recebendo áudio...', percent)
                    last_progress = time.monotonic()
            elif data.get('status') == 'finished':
                status_update(job_id, 'convertendo', 'Combinando vídeo e áudio (MP4)...' if is_video else 'Convertendo áudio para MP3...', 95)

        params = {
            'format': 'bestaudio/best', 'quiet': True, 'no_warnings': True,
            'noplaylist': True, 'socket_timeout': 30, 'retries': 4,
            'fragment_retries': 4, 'overwrites': False,
            'outtmpl': str(work / '%(id)s.%(ext)s'),
            'cachedir': str(CONFIG / 'cache'), 'progress_hooks': [hook],
            'postprocessor_hooks': [lambda _: check_cancel(job_id)],
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
        }
        if is_video:
            # Prefer native MP4 video + M4A audio, with compatible fallback.
            # Remux rather than transcode: much lighter on the ZimaOS server.
            params['format'] = (
                f'bv[ext=mp4][height<={quality}]+ba[ext=m4a]/'
                f'b[ext=mp4][height<={quality}]/'
                f'bv[height<={quality}]+ba/b[height<={quality}]'
            )
            params['merge_output_format'] = 'mp4'
            params['postprocessors'] = [
                {'key': 'FFmpegVideoRemuxer', 'preferedformat': 'mp4'}
            ]

        check_cancel(job_id)
        with yt_dlp.YoutubeDL(params) as ydl:
            info = ydl.extract_info(url, download=True)
        check_cancel(job_id)
        media = work / (video_id + suffix)
        if not media.exists():
            possibilities = list(work.glob('*' + suffix))
            if len(possibilities) != 1:
                raise RuntimeError('O arquivo ' + suffix + ' não foi gerado. Consulte os logs do container.')
            media = possibilities[0]

        # Edits made while downloading take effect now, not stale values from queueing.
        with DB_LOCK:
            check_cancel(job_id)
            with connect() as c:
                latest = c.execute('SELECT artist,track,album FROM jobs WHERE id=?',(job_id,)).fetchone()
            artist, track, album = latest['artist'], latest['track'], latest['album']
            artist_dir = root / clean_filename(artist)
            output = artist_dir / (clean_filename(track) + suffix)
            if output.exists():
                status_update(job_id, 'existente', 'Já existe ' + suffix + ' com esse canal e nome.', 100,
                              str(output.relative_to(root)))
                return
            status_update(job_id, 'finalizando', 'Salvando tags e capa...' if not is_video else 'Salvando arquivo de vídeo...', 98)

        if is_video:
            retag_mp4(media, artist, track, album)
        else:
            has_cover = tag_mp3(media, artist, track, album, url, info or {})
        check_cancel(job_id)
        # Serialize the final move with edits and cancellation (fast filesystem operation).
        with DB_LOCK:
            check_cancel(job_id)
            with connect() as c:
                latest = c.execute('SELECT artist,track,album FROM jobs WHERE id=?',(job_id,)).fetchone()
            if (latest['artist'], latest['track'], latest['album']) != (artist, track, album):
                # An edit in the tiny tagging window is incorporated before saving.
                artist, track, album = latest['artist'], latest['track'], latest['album']
                (retag_mp4 if is_video else retag_mp3)(media, artist, track, album)
                artist_dir = root / clean_filename(artist)
                output = artist_dir / (clean_filename(track) + suffix)
            artist_dir.mkdir(parents=True, exist_ok=True)
            if not os.access(artist_dir, os.W_OK):
                raise PermissionError(f'Sem permissão de escrita em {artist_dir}')
            if output.exists():
                status_update(job_id, 'existente', 'O arquivo já existia. Não foi sobrescrito.', 100,
                              str(output.relative_to(root)))
                return
            staged = artist_dir / f'.{clean_filename(track)}.{job_id}.part'
            try:
                with media.open('rb') as src, staged.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                try:
                    os.link(staged, output)
                except FileExistsError:
                    status_update(job_id, 'existente', 'O arquivo já existia. Não foi sobrescrito.', 100,
                                  str(output.relative_to(root)))
                    return
                except OSError as exc:
                    if exc.errno not in (errno.EOPNOTSUPP, errno.ENOTSUP, errno.EPERM, errno.EACCES, errno.ENOSYS):
                        raise
                    try:
                        with staged.open('rb') as src, output.open('xb') as dst:
                            shutil.copyfileobj(src, dst, 1024 * 1024)
                    except Exception:
                        output.unlink(missing_ok=True)
                        raise
            finally:
                staged.unlink(missing_ok=True)
            status_update(job_id, 'concluido', 'Vídeo MP4 salvo na pasta do canal.' if is_video else ('MP3 salvo com capa embutida.' if has_cover else 'MP3 salvo; capa indisponível. Use Atualizar capa em Downloads.'), 100,
                          str(output.relative_to(root)))
    except Exception as exc:
        # yt-dlp may wrap a hook exception in DownloadError; cancellation flag wins.
        with DB_LOCK:
            event = CANCEL_EVENTS.get(job_id)
            cancelled = isinstance(exc, JobCancelled) or (event is not None and event.is_set())
        if cancelled:
            with connect() as c:
                c.execute("UPDATE jobs SET status='cancelado', message='Cancelado pelo usuário.', progress=0, updated_at=? WHERE id=?",(now(),job_id))
        else:
            LOG.exception('Falha no download %s', video_id)
            status_update(job_id, 'erro', str(exc)[:350])
    finally:
        with DB_LOCK:
            CANCEL_EVENTS.pop(job_id, None)
        shutil.rmtree(work, ignore_errors=True)



def worker():
    while True:
        job_id = JOBS.get()
        try:
            download(job_id)
        except Exception:
            LOG.exception('Erro inesperado no trabalhador')
        finally:
            JOBS.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_storage()
    clean_preview_cache()
    with connect() as c:
        # Interrupted jobs can be safely retried; incomplete temporary files are in /config/work.
        c.execute("UPDATE jobs SET status='cancelado', progress=0, message='Cancelado durante reinício.' WHERE status='cancelando'")
        c.execute("UPDATE jobs SET status='pendente', progress=0, message='Retomando após reinício' WHERE status IN ('baixando', 'convertendo', 'finalizando')")
        rows = c.execute("SELECT id FROM jobs WHERE status='pendente' ORDER BY id").fetchall()
    for row in rows:
        JOBS.put(row['id'])
    threading.Thread(target=worker, daemon=True, name='audio-download-worker').start()
    yield


app = FastAPI(title='YouTube Music ZimaOS', version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)


@app.get('/')
def index():
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store'})


@app.get('/app.js')
def js():
    return FileResponse(STATIC / 'app.js', media_type='application/javascript', headers={'Cache-Control': 'no-store'})


@app.get('/style.css')
def css():
    return FileResponse(STATIC / 'style.css', media_type='text/css', headers={'Cache-Control': 'no-store'})


@app.get('/api/health')
def health():
    return {'ok': True, 'version': VERSION}


@app.get('/api/state')
def state(request: Request):
    with connect() as c:
        has_user = bool(c.execute('SELECT 1 FROM users LIMIT 1').fetchone())
        username = None
        user = user_id_for(request)
        if user:
            row = c.execute('SELECT username FROM users WHERE id=?', (user,)).fetchone()
            username = row['username'] if row else None
    return {'version': VERSION, 'needs_setup': not has_user,
            'logged_in': bool(username), 'username': username}


@app.post('/api/setup')
def setup(data: Credentials, response: Response, request: Request):
    require_ajax(request)
    username = data.username.strip()
    if len(username) < 3 or any(ord(c) < 32 for c in username):
        raise HTTPException(400, 'O nome de usuário precisa ter 3 caracteres ou mais.')
    with SETUP_LOCK:
        with connect() as c:
            if c.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise HTTPException(403, 'O primeiro acesso já foi configurado.')
            cursor = c.execute('INSERT INTO users(username, password_hash) VALUES (?,?)',
                               (username, pwd_hash(data.password)))
            user = cursor.lastrowid
    make_session(user, response)
    return {'ok': True}


@app.post('/api/login')
def login(data: Credentials, response: Response, request: Request):
    require_ajax(request)
    with connect() as c:
        row = c.execute('SELECT id, password_hash FROM users WHERE username=?', (data.username.strip(),)).fetchone()
    # Compute the hash for unknown accounts too, to limit timing differences.
    stored = row['password_hash'] if row else pwd_hash('placeholder-password')
    if not pwd_verify(data.password, stored) or not row:
        raise HTTPException(401, 'Usuário ou senha incorretos.')
    make_session(row['id'], response)
    return {'ok': True}


@app.post('/api/logout')
def logout(request: Request, response: Response, user: Annotated[int, Depends(require_auth)]):
    require_ajax(request)
    token = request.cookies.get(COOKIE, '')
    with connect() as c:
        c.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}


@app.post('/api/search')
async def search(data: SearchQuery, request: Request, user: Annotated[int, Depends(require_auth)]):
    require_ajax(request)
    query = data.query.strip()
    if len(query) < 2:
        raise HTTPException(400, 'Digite pelo menos 2 caracteres para buscar.')
    try:
        async with SEARCH_SEMAPHORE:
            results, has_more = await asyncio.to_thread(search_videos, query, data.offset, data.limit)
    except Exception as exc:
        LOG.warning('Falha na busca de vídeos: %s', exc)
        raise HTTPException(502, 'Não foi possível buscar no YouTube agora. Tente novamente.') from exc
    return {'results': results, 'count': len(results), 'offset': data.offset,
            'next_offset': data.offset + data.limit, 'has_more': bool(has_more) and data.offset + data.limit <= 400}


@app.post('/api/preview/{video_id}')
def start_preview(video_id: str, request: Request, user: Annotated[int, Depends(require_auth)]):
    require_ajax(request)
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(400, 'Vídeo inválido.')
    return queue_preview(video_id)


@app.get('/api/preview/{video_id}')
def get_preview(video_id: str, user: Annotated[int, Depends(require_auth)]):
    return preview_state(video_id)


@app.get('/api/audio/{video_id}')
def play_audio(video_id: str, user: Annotated[int, Depends(require_auth)]):
    """Local MP3 serving with byte ranges and a proper duration/seekable file."""
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(400, 'Vídeo inválido.')
    audio = existing_audio(video_id)
    if not audio:
        raise HTTPException(409, 'O áudio ainda está sendo preparado.')
    # FileResponse handles Range/Content-Range/Content-Length correctly.
    return FileResponse(audio, media_type='audio/mpeg', content_disposition_type='inline',
                        headers={'Cache-Control': 'private,max-age=1800'})


@app.get('/api/stream/{video_id}')
def live_stream(video_id: str, user: Annotated[int, Depends(require_auth)], start: int = 0):
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(400, 'Vídeo inválido.')
    if start < 0 or start > 1499:
        raise HTTPException(400, 'Posição inválida; escolha de 0 a 1499 segundos.')
    local = existing_audio(video_id)
    if local and start == 0:
        set_stream_state(video_id, 'ready', 'Tocando arquivo local.')
        return FileResponse(local, media_type='audio/mpeg', content_disposition_type='inline',
                            headers={'Cache-Control': 'private,max-age=600'})
    if not STREAM_SLOTS.acquire(blocking=False):
        raise HTTPException(429, 'Dois áudios já estão sendo preparados. Pare um deles e tente novamente.')
    return StreamingResponse(stream_mp3(video_id, start), media_type='audio/mpeg',
                             headers={'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'})


@app.get('/api/playback/{video_id}')
def playback_state(video_id: str, user: Annotated[int, Depends(require_auth)]):
    if not VIDEO_ID_RE.fullmatch(video_id):
        raise HTTPException(400, 'Vídeo inválido.')
    with STREAM_STATE_LOCK:
        state = STREAM_STATES.get(video_id)
    if state:
        return {'state': state['state'], 'message': state['message']}
    return {'state': 'idle', 'message': 'Aguardando áudio...'}


@app.get('/icon.png')
def icon_png():
    return FileResponse(STATIC / 'icon.png', media_type='image/png', headers={'Cache-Control': 'public,max-age=86400'})


@app.post('/api/analyze')
async def analyze(data: VideoQuery, request: Request, user: Annotated[int, Depends(require_auth)]):
    require_ajax(request)
    try:
        url = youtube_video_url(data.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        info = await asyncio.to_thread(get_info, url)
    except Exception as exc:
        LOG.warning('Falha na análise do vídeo: %s', exc)
        raise HTTPException(400, f'Não foi possível analisar o vídeo: {str(exc)[:180]}') from exc
    result = guess_metadata(info)
    result['url'] = url
    result['video_id'] = url.split('v=')[-1]
    return result


@app.post('/api/jobs')
def add_job(data: NewJob, request: Request, user: Annotated[int, Depends(require_auth)]):
    require_ajax(request)
    try:
        url = youtube_video_url(data.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    artist = clean(data.artist)
    track = clean(data.track)
    album = clean(data.album)
    if len(artist) < 2 or len(track) < 1 or clean_filename(artist) == 'Sem nome':
        raise HTTPException(400, 'Informe o nome do canal/artista e da música.')
    if data.media_type == 'video' and data.video_quality not in (360, 480, 720, 1080):
        raise HTTPException(400, 'Qualidade indisponível. Use 360p, 480p, 720p ou 1080p.')
    target_root = video_root() if data.media_type == 'video' else MUSIC
    if not target_root.is_dir() or not os.access(target_root, os.W_OK):
        raise HTTPException(500, 'A pasta de destino não permite gravação. Confira os volumes e permissões.')
    video_id = url.split('v=')[-1]
    with DB_LOCK:
        with connect() as c:
            old = c.execute("SELECT id, status, path FROM jobs WHERE video_id=? AND media_type=? AND status IN ('pendente','baixando','convertendo','finalizando','concluido') ORDER BY id DESC LIMIT 1", (video_id, data.media_type)).fetchone()
            if old:
                raise HTTPException(409, f'Este vídeo já foi adicionado (tarefa #{old["id"]}, estado: {old["status"]}).')
            cur = c.execute('''INSERT INTO jobs (video_id,url,artist,track,album,media_type,video_quality,status,created_at,updated_at,message)
                               VALUES (?,?,?,?,?,?,?,'pendente',?,?,?)''',
                            (video_id, url, artist, track, album, data.media_type, data.video_quality, now(), now(), 'Aguardando vez'))
            job_id = cur.lastrowid
        JOBS.put(job_id)
    return {'ok': True, 'id': job_id}


@app.get('/api/jobs')
def list_jobs(user: Annotated[int, Depends(require_auth)], limit: int = 60):
    limit = max(1, min(limit, 500))
    with connect() as c:
        rows = c.execute('SELECT id,video_id,artist,track,album,media_type,video_quality,status,progress,message,path,created_at,updated_at FROM jobs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        total = c.execute('SELECT COUNT(*) AS count FROM jobs').fetchone()['count']
    return {'jobs': [dict(row) for row in rows], 'total': total, 'has_more': total > len(rows),
            'music_root': str(MUSIC), 'video_root': str(video_root()), 'version': VERSION}


@app.get('/api/jobs/{job_id}/cover')
def get_job_cover(job_id: int, user: Annotated[int, Depends(require_auth)]):
    with connect() as c:
        row = c.execute('SELECT path,status,media_type FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row or row['status'] != 'concluido' or row['media_type'] != 'audio':
        raise HTTPException(404, 'Capa indisponível.')
    path = safe_music_path(row['path'])
    cover = embedded_cover(path) if path.is_file() else None
    if not cover:
        raise HTTPException(404, 'Esta música ainda não tem capa embutida.')
    return Response(cover[0], media_type=cover[1], headers={'Cache-Control': 'private,max-age=3600', 'X-Content-Type-Options': 'nosniff'})


@app.post('/api/jobs/{job_id}/cover')
async def update_job_cover(job_id: int, request: Request, user: Annotated[int, Depends(require_auth)]):
    require_ajax(request)
    result = await asyncio.to_thread(refresh_job_cover, job_id, force=True)
    return {'ok': True, 'result': result}


@app.post('/api/covers/repair')
def begin_cover_repair(request: Request, user: Annotated[int, Depends(require_auth)]):
    require_ajax(request)
    with COVER_REPAIR_LOCK:
        if COVER_REPAIR_STATE['running']:
            raise HTTPException(409, 'Já existe uma atualização de capas em andamento.')
        COVER_REPAIR_STATE.update(running=True,total=0,processed=0,updated=0,skipped=0,failed=0,
                                  message='Procurando músicas sem capa...')
    threading.Thread(target=run_cover_repair, name='cover-repair', daemon=True).start()
    return {'ok': True}


@app.get('/api/covers/repair')
def cover_repair_status(user: Annotated[int, Depends(require_auth)]):
    with COVER_REPAIR_LOCK:
        return dict(COVER_REPAIR_STATE)


@app.patch('/api/jobs/{job_id}')
def edit_job(job_id: int, data: EditJob, request: Request, user: Annotated[int, Depends(require_auth)]):
    require_ajax(request)
    artist, track, album = editable_metadata(data)
    with DB_LOCK:
        with connect() as c:
            row = c.execute('SELECT status,path,media_type FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Música não encontrada.')
            if row['status'] in ('cancelando','cancelado'):
                raise HTTPException(409, 'Esta tarefa está cancelada. Adicione-a novamente.')
            if row['status'] == 'finalizando':
                raise HTTPException(409, 'Finalizando o arquivo. Edite após concluir.')
            if row['status'] == 'concluido':
                media_type = row['media_type']
                root = video_root() if media_type == 'video' else MUSIC
                suffix = '.mp4' if media_type == 'video' else '.mp3'
                current = safe_media_path(row['path'], media_type)
                if not current.is_file():
                    raise HTTPException(404, 'O arquivo não existe mais no servidor.')
                dest = root / clean_filename(artist) / (clean_filename(track) + suffix)
                # Reject destination symlinks and outside-library paths.
                if not dest.resolve().is_relative_to(root.resolve()):
                    raise HTTPException(400, 'Caminho inválido para salvar a mídia.')
                if dest != current and dest.exists():
                    raise HTTPException(409, 'Já existe outro arquivo com esse canal e título.')
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation prevents overwriting files created concurrently.
                # Copy first, change tags on copy, then remove old file on success.
                try:
                    if dest != current:
                        with current.open('rb') as source, dest.open('xb') as target:
                            shutil.copyfileobj(source, target, 1024 * 1024)
                    (retag_mp4 if media_type == 'video' else retag_mp3)(dest, artist, track, album)
                    if dest != current:
                        current.unlink()
                        try: current.parent.rmdir()  # only an empty old singer directory
                        except OSError: pass
                except FileExistsError as exc:
                    raise HTTPException(409, 'Já existe outra música com esse cantor e título.') from exc
                except Exception as exc:
                    if dest != current:
                        dest.unlink(missing_ok=True)
                    raise HTTPException(500, 'Não foi possível mover ou atualizar o arquivo: ' + str(exc)[:120]) from exc
                path = str(dest.relative_to(root))
                c.execute('UPDATE jobs SET artist=?,track=?,album=?,path=?,message=?,updated_at=? WHERE id=?',
                          (artist,track,album,path,'Nome e tags atualizados.',now(),job_id))
                return {'ok':True,'path':path}
            if row['status'] not in ('pendente','baixando','convertendo','erro','existente'):
                raise HTTPException(409, 'Este download não pode ser editado no momento.')
            c.execute('UPDATE jobs SET artist=?,track=?,album=?,updated_at=? WHERE id=?',
                      (artist,track,album,now(),job_id))
            return {'ok':True,'path':row['path']}


@app.post('/api/jobs/{job_id}/cancel')
def cancel_job(job_id: int, request: Request, user: Annotated[int, Depends(require_auth)]):
    require_ajax(request)
    with DB_LOCK:
        with connect() as c:
            row = c.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Download não encontrado.')
            status = row['status']
            if status == 'pendente':
                c.execute("UPDATE jobs SET status='cancelado',progress=0,message='Cancelado antes de iniciar.',updated_at=? WHERE id=?",(now(),job_id))
            elif status in ('baixando','convertendo','finalizando'):
                ev = CANCEL_EVENTS.get(job_id)
                if ev:
                    ev.set()
                c.execute("UPDATE jobs SET status='cancelando',message='Cancelamento solicitado. Interrompendo...',updated_at=? WHERE id=?",(now(),job_id))
            elif status not in ('cancelado','cancelando'):
                raise HTTPException(409, 'Esta tarefa já terminou. Use Excluir para remover.')
    return {'ok':True}


@app.delete('/api/jobs/{job_id}')
def delete_job(job_id: int, request: Request, user: Annotated[int, Depends(require_auth)]):
    """Delete a completed MP3 and history, or remove an inactive history record."""
    require_ajax(request)
    with DB_LOCK:
        with connect() as c:
            row = c.execute('SELECT status,path,media_type FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Música não encontrada.')
            if row['status'] in ('pendente','baixando','convertendo','finalizando','cancelando'):
                raise HTTPException(409, 'Cancele e aguarde a tarefa parar antes de excluir.')
            if row['status'] == 'concluido':
                media = safe_media_path(row['path'], row['media_type'])
                other = c.execute("SELECT id FROM jobs WHERE status='concluido' AND path=? AND media_type=? AND id<>?",(row['path'], row['media_type'], job_id)).fetchone()
                if other:
                    raise HTTPException(409, 'Outro registro utiliza este arquivo. Exclua o duplicado primeiro.')
                if not media.is_file():
                    raise HTTPException(404, 'Arquivo não encontrado; o registro não foi excluído.')
                media.unlink()
                try:
                    media.parent.rmdir()  # only remove artist folder when empty
                except OSError:
                    pass
            # A status 'existente' points to someone else's file: never unlink it.
            c.execute('DELETE FROM jobs WHERE id=?',(job_id,))
    return {'ok':True}
