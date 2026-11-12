"""Video download regression tests with local MP4 data (no YouTube access)."""
import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import asynccontextmanager

import httpx
from mutagen.mp4 import MP4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
from test_app import main

VID='dQw4w9WgXcQ'
URL='https://www.youtube.com/watch?v='+VID
HEADERS={'x-app-request':'zimaos-music'}

class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        main.CONFIG=self.root/'dados'
        main.MUSIC=self.root/'musicas'
        main.DB=main.CONFIG/'app.db'
        main.JOBS=main.queue.Queue()
        main.CANCEL_EVENTS.clear()
        main.STREAM_STATES.clear()
        main.init_storage()
        self.vidroot=self.root/'videos'

    @asynccontextmanager
    async def client(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://testserver') as client:
            await client.post('/api/setup',json={'username':'tester','password':'abc123'},headers=HEADERS)
            yield client

    def run_async(self, coro):return asyncio.run(coro)

    def test_migrate_existing_database_preserves_previous_mp3_jobs(self):
        with main.connect() as c:
            c.execute('INSERT INTO jobs(video_id,url,artist,track,album,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                (VID,URL,'Canal','Música','','concluido',main.now(),main.now()))
            row=c.execute('SELECT id FROM jobs ORDER BY id DESC LIMIT 1').fetchone()
            jid=row['id']
            # Simulate the original table schema by rebuilding it without the new columns.
            c.execute('ALTER TABLE jobs RENAME TO jobs_new')
            c.execute('''CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT NOT NULL, url TEXT NOT NULL,
                artist TEXT NOT NULL, track TEXT NOT NULL, album TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            c.execute('''INSERT INTO jobs(id,video_id,url,artist,track,album,status,progress,message,path,created_at,updated_at)
                SELECT id,video_id,url,artist,track,album,status,progress,message,path,created_at,updated_at FROM jobs_new''')
            c.execute('DROP TABLE jobs_new')
        main.init_storage()
        with main.connect() as c:
            row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(row['media_type'],'audio')
        self.assertEqual(row['video_quality'],720)
        self.assertEqual(row['track'],'Música')

    def test_queue_audio_and_video_for_same_url_and_validate_quality(self):
        async def run():
            async with self.client() as c:
                base={'url':URL,'artist':'Leo Magalhães','track':'Alô','album':'DVD 10 Anos'}
                a=await c.post('/api/jobs',json={**base,'media_type':'audio'},headers=HEADERS)
                b=await c.post('/api/jobs',json={**base,'media_type':'video','video_quality':1080},headers=HEADERS)
                self.assertEqual(a.status_code,200,a.text)
                self.assertEqual(b.status_code,200,b.text)
                self.assertNotEqual(a.json()['id'],b.json()['id'])
                dupe=await c.post('/api/jobs',json={**base,'media_type':'video','video_quality':720},headers=HEADERS)
                self.assertEqual(dupe.status_code,409,dupe.text)
                invalid=await c.post('/api/jobs',json={**base,'media_type':'video','video_quality':960},headers=HEADERS)
                self.assertEqual(invalid.status_code,400,invalid.text)
                rows=(await c.get('/api/jobs')).json()['jobs']
                self.assertEqual({r['media_type'] for r in rows},{'audio','video'})
        self.run_async(run())

    def make_mp4(self, target):
        target.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=160x90:r=15:d=1',
            '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','mpeg4','-c:a','aac','-shortest','-y',str(target)],check=True)

    def test_video_download_edit_tags_move_and_delete_independent_of_mp3(self):
        with main.connect() as c:
            jid=c.execute('''INSERT INTO jobs(video_id,url,artist,track,album,media_type,video_quality,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'pendente',?,?)''',
                (VID,URL,'Leo Magalhães','Alô','','video',720,main.now(),main.now())).lastrowid
        class OfflineYoutubeDL:
            def __init__(self,params):self.params=params
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def extract_info(self,_url,download=True):
                work=Path(self.params['outtmpl']).parent
                work.mkdir(parents=True,exist_ok=True)
                target=work/(VID+'.mp4')
                testcase.make_mp4(target)
                for hook in self.params['progress_hooks']:
                    hook({'status':'finished'})
                testcase.assertEqual(self.params['merge_output_format'],'mp4')
                testcase.assertIn('height<=720',self.params['format'])
                return {'id':VID,'title':'Alô'}
        testcase=self
        with patch.object(main.yt_dlp,'YoutubeDL',OfflineYoutubeDL,create=True):
            main.download(jid)
        with main.connect() as c:
            row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        self.assertEqual(row['status'],'concluido',row['message'])
        file=self.vidroot/row['path']
        self.assertTrue(file.is_file())
        self.assertEqual(MP4(file)['\xa9nam'][0],'Alô')
        self.assertEqual(MP4(file)['\xa9ART'][0],'Leo Magalhães')
        # A matching MP3, if present, must not be touched by video deletion.
        mp3=main.MUSIC/'Leo Magalhães'/'Alô.mp3'
        mp3.parent.mkdir(parents=True,exist_ok=True)
        mp3.write_bytes(b'leave me untouched')
        async def run():
            async with self.client() as c:
                r=await c.patch(f'/api/jobs/{jid}',headers=HEADERS,json={
                    'artist':'Novo Canal','track':'Faixa renomeada','album':'Novo álbum'})
                self.assertEqual(r.status_code,200,r.text)
                renamed=self.vidroot/'Novo Canal'/'Faixa renomeada.mp4'
                self.assertTrue(renamed.is_file())
                tags=MP4(renamed)
                self.assertEqual(tags['\xa9nam'][0],'Faixa renomeada')
                self.assertEqual(tags['\xa9ART'][0],'Novo Canal')
                self.assertEqual(tags['\xa9alb'][0],'Novo álbum')
                deleted=await c.delete(f'/api/jobs/{jid}',headers=HEADERS)
                self.assertEqual(deleted.status_code,200,deleted.text)
                self.assertFalse(renamed.exists())
                self.assertTrue(mp3.exists())
        self.run_async(run())

    def test_video_path_cannot_escape_library(self):
        outside=self.root/'escape.mp4'
        self.make_mp4(outside)
        with main.connect() as c:
            jid=c.execute('''INSERT INTO jobs(video_id,url,artist,track,album,media_type,status,path,created_at,updated_at)
                VALUES(?,?,?,?,?,'video','concluido',?,?,?)''',
                (VID,URL,'Canal','Vídeo','', '../escape.mp4',main.now(),main.now())).lastrowid
        async def run():
            async with self.client() as c:
                r=await c.delete(f'/api/jobs/{jid}',headers=HEADERS)
                self.assertEqual(r.status_code,400,r.text)
                self.assertTrue(outside.exists())
        self.run_async(run())

    def test_play_pause_icons_and_status_indicator_in_ui_and_events(self):
        static=Path(__file__).resolve().parents[1]/'app'/'static'
        html=(static/'index.html').read_text()
        css=(static/'style.css').read_text()
        js=(static/'app.js').read_text()
        self.assertIn('id="player-toggle"',html)
        self.assertIn('class="now-playing-bars"',html)
        self.assertIn('class="play-icon"',html)
        self.assertIn('class="pause-icon"',html)
        self.assertIn('setPlaybackState(\'playing\')',js)
        self.assertIn('setPlaybackState(\'loading\')',js)
        self.assertIn('setPlaybackState(\'paused\')',js)
        self.assertIn('playerAudio.addEventListener(\'error\'',js)
        self.assertIn('updatePlayButton(play)',js)
        self.assertIn('prefers-reduced-motion:reduce',css)
        self.assertIn('animation:mini-beat',css)

if __name__=='__main__':unittest.main()
