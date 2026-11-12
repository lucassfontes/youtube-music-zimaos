"""Music metadata extraction: by user preference, use YouTube channel name as artist."""
from __future__ import annotations
import re
import unicodedata
from urllib.parse import parse_qs, urlparse

VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
GENERIC_ARTISTS = {'topic', 'various artists', 'official', 'music', 'vevo', 'youtube', 'youtube music',
                   'unknown', 'desconhecido', 'canal oficial', 'audio oficial', 'letras', 'lyrics'}
TAG_RE = re.compile(r'(?i)\s*[\[(](?:official\s*(?:music\s*)?(?:video|audio|lyric(?:s)?(?:\s*video)?)|'
                    r'vídeo\s*oficial|video\s*oficial|áudio\s*oficial|audio\s*oficial|'
                    r'lyrics?(?:\s*video)?|letra(?:s)?|hd|4k|visualizer|clipe\s*oficial)\s*[\])]')


def youtube_video_url(url: str) -> str:
    """Return a clean single-video URL; deliberately reject playlists/other services."""
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('Cole um link válido de vídeo do YouTube.')
    p = urlparse(url.strip())
    try:
        port = p.port
    except ValueError as exc:
        raise ValueError('Endereço HTTPS inválido.') from exc
    if p.scheme != 'https' or p.username or p.password or port not in (None, 443):
        raise ValueError('Use um link HTTPS do YouTube.')
    domain = (p.hostname or '').lower().rstrip('.')
    if domain in ('youtu.be', 'www.youtu.be'):
        video_id = p.path.strip('/').split('/')[0]
    elif domain in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        if p.path == '/watch':
            video_id = parse_qs(p.query).get('v', [''])[0]
        elif p.path.startswith(('/shorts/', '/live/')):
            video_id = p.path.split('/')[2]
        else:
            raise ValueError('Use o link de um vídeo, não de um canal ou playlist.')
    else:
        raise ValueError('O endereço deve ser de um vídeo do YouTube.')
    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError('Não foi possível encontrar o código do vídeo no link.')
    canonical_host = 'music.youtube.com' if domain == 'music.youtube.com' else 'www.youtube.com'
    return f'https://{canonical_host}/watch?v={video_id}'


def clean(text: str) -> str:
    s = unicodedata.normalize('NFC', str(text or ''))
    s = TAG_RE.sub('', s)
    return re.sub(r'\s+', ' ', s).strip(' -|—–_\t\n')


def clean_filename(text: str, fallback: str = 'Sem nome', limit: int = 110) -> str:
    # Strip path separators, Windows-reserved symbols, control characters and dot-only names.
    s = re.sub(r'[\\/<>:"|?*\x00-\x1f\x7f]+', ' ', str(text or ''))
    s = re.sub(r'\s+', ' ', s).strip(' .')
    if s in ('', '.', '..'):
        s = fallback
    return s[:limit].rstrip(' .') or fallback


def artist_is_specific(s: str) -> bool:
    val = str(s or '').strip()
    folded = unicodedata.normalize('NFKD', val).encode('ascii', 'ignore').decode().lower()
    folded = re.sub(r'\s+', ' ', folded)
    if len(val) < 2 or len(val) > 120 or folded in GENERIC_ARTISTS:
        return False
    if any(marker in folded for marker in ('subscribe', 'inscreva-se', 'playlist', 'karaoke', 'compilation', 'radio online')):
        return False
    return True


def guess_metadata(info: dict) -> dict:
    """Use the YouTube channel name for the artist field, as requested.

    An uploader/channel is not a reliable identification of who sings the song;
    the user can edit the proposed artist before queueing the download.
    If neither channel nor uploader is available, fall back to recording metadata
    or a conventional 'Artist - Track' video title.
    """
    raw_title = str(info.get('title') or '').strip()
    track = clean(info.get('track') or '')
    album = clean(info.get('album') or '')
    channel = str(info.get('channel') or info.get('uploader') or '').strip()
    channel = re.sub(r'\s+', ' ', channel)[:120]
    artist = channel
    source = 'nome do canal do YouTube'
    # Canal é quem publicou, não necessariamente quem canta: revisão recomendada.
    confidence = 'media'
    metadata_artist = clean(info.get('artist') or '')
    if not metadata_artist:
        arr = info.get('artists') or []
        if isinstance(arr, list):
            metadata_artist = ', '.join(clean(a.get('name') if isinstance(a, dict) else a) for a in arr if a)
    match = re.match(r'^(.{2,100}?)\s+[-–—]\s+(.{2,150})$', raw_title)
    if not track and match:
        # O canal continua sendo o artista; só usamos o título para nomear a faixa.
        track = clean(match.group(2))
    if not track:
        track = clean(raw_title)
    if not artist:
        if artist_is_specific(metadata_artist):
            artist = metadata_artist
            source = 'metadados da música (canal indisponível)'
            confidence = 'media'
        elif match and artist_is_specific(clean(match.group(1))):
            artist = clean(match.group(1))
            source = 'título do vídeo (canal indisponível)'
            confidence = 'revisar'
        else:
            artist = ''
            source = 'canal não identificado — informe o artista antes de baixar'
            confidence = 'revisar'
    if not track:
        track = 'Música sem nome'
    return {'artist': artist, 'track': track, 'album': album,
            'channel': channel, 'uploader': channel, 'source': source, 'confidence': confidence,
            'thumbnail': info.get('thumbnail') or '', 'duration': info.get('duration')}
