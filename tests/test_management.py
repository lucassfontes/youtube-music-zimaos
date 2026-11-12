"""Offline integration tests for user-managed downloads, editing, cancel and streaming."""
import asyncio
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import asynccontextmanager
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from test_app import main
from mutagen.id3 import ID3, APIC

ID = 'dQw4w9WgXcQ'
URL = 'https://www.youtube.com/watch?v=' + ID
HEADERS = {'x-app-request':'zimaos-music'}


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        main.CONFIG = Path(self.tmp.name) / 'data'
        main.MUSIC = Path(self.tmp.name) / 'musicas'
        main.DB = main.CONFIG / 'app.db'
        main.JOBS = main.queue.Queue()
        main.CANCEL_EVENTS.clear()
        main.STREAM_STATES.clear()
        main.init_storage()

    def create_job(self, status='pendente', artist='Canal', track='Musica', path=''):
        with main.connect() as c:
            return c.execute('''INSERT INTO jobs (video_id,url,artist,track,album,status,path,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',(ID,URL,artist,track,'',status,path,main.now(),main.now())).lastrowid

    def audio(self, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                        'sine=frequency=440:duration=0.7','-acodec','libmp3lame','-y',str(dest)],check=True)

    def api(self, coro):
        return asyncio.run(coro)

    @asynccontextmanager
    async def client(self):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport,base_url='http://testserver') as client:
            await client.post('/api/setup',json={'username':'tester','password':'abc123'},headers=HEADERS)
            yield client

    def test_edit_before_download_cancel_and_delete_history(self):
        async def run():
            async with self.client() as c:
                jid=self.create_job()
                r=await c.patch(f'/api/jobs/{jid}',headers=HEADERS,json={
                    'artist':'Novo Canal','track':'Nova Musica','album':'Novo Album'})
                self.assertEqual(r.status_code,200,r.text)
                with main.connect() as db:
                    row=db.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
                self.assertEqual(row['artist'],'Novo Canal')
                self.assertEqual(row['track'],'Nova Musica')
                self.assertEqual(row['album'],'Novo Album')
                self.assertEqual((await c.post(f'/api/jobs/{jid}/cancel',headers=HEADERS)).status_code,200)
                self.assertEqual((await c.request('DELETE',f'/api/jobs/{jid}',headers=HEADERS)).status_code,200)
                with main.connect() as db:
                    self.assertIsNone(db.execute('SELECT 1 FROM jobs WHERE id=?',(jid,)).fetchone())
        self.api(run())

    def test_cancel_active_sets_signal(self):
        async def run():
            async with self.client() as c:
                jid=self.create_job('baixando')
                ev=main.threading.Event()
                main.CANCEL_EVENTS[jid]=ev
                r=await c.post(f'/api/jobs/{jid}/cancel',headers=HEADERS)
                self.assertEqual(r.status_code,200,r.text)
                self.assertTrue(ev.is_set())
                with main.connect() as db:
                    self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()['status'],'cancelando')
                self.assertEqual((await c.request('DELETE',f'/api/jobs/{jid}',headers=HEADERS)).status_code,409)
        self.api(run())

    def test_completed_edit_renames_and_preserves_embedded_cover_then_delete(self):
        old=main.MUSIC / 'Artista Antigo' / 'Nome Antigo.mp3'
        self.audio(old)
        main.tag_mp3(old,'Artista Antigo','Nome Antigo','','https://www.youtube.com/watch?v='+ID,{})
        tags=ID3(old)
        tags.add(APIC(encoding=3,mime='image/png',type=3,desc='Cover',data=b'picture bytes'))
        tags.save(old)
        jid=self.create_job('concluido','Artista Antigo','Nome Antigo',str(old.relative_to(main.MUSIC)))
        async def run():
            async with self.client() as c:
                r=await c.patch(f'/api/jobs/{jid}',headers=HEADERS,json={
                    'artist':'Cantora Nova', 'track':'Titulo Novo', 'album':'Album Novo'})
                self.assertEqual(r.status_code,200,r.text)
                moved=main.MUSIC / 'Cantora Nova' / 'Titulo Novo.mp3'
                self.assertTrue(moved.is_file())
                self.assertFalse(old.exists())
                tags=ID3(moved)
                self.assertEqual(tags.get('TPE1').text[0],'Cantora Nova')
                self.assertEqual(tags.get('TIT2').text[0],'Titulo Novo')
                self.assertEqual(tags.get('TALB').text[0],'Album Novo')
                self.assertIsNotNone(tags.getall('APIC'))
                self.assertEqual((await c.request('DELETE',f'/api/jobs/{jid}',headers=HEADERS)).status_code,200)
                self.assertFalse(moved.exists())
        self.api(run())

    def test_edit_refuses_overwrite_and_existing_record_cannot_delete_file(self):
        old=main.MUSIC/'Artista A'/'Original.mp3'
        conflict=main.MUSIC/'Artista B'/'Outro.mp3'
        self.audio(old);self.audio(conflict)
        jid=self.create_job('concluido','Artista A','Original',str(old.relative_to(main.MUSIC)))
        other=self.create_job('existente','Artista B','Outro',str(conflict.relative_to(main.MUSIC)))
        async def run():
            async with self.client() as c:
                r=await c.patch(f'/api/jobs/{jid}',headers=HEADERS,json={'artist':'Artista B','track':'Outro','album':''})
                self.assertEqual(r.status_code,409,r.text)
                self.assertTrue(old.is_file())
                self.assertEqual((await c.request('DELETE',f'/api/jobs/{other}',headers=HEADERS)).status_code,200)
                self.assertTrue(conflict.is_file())
        self.api(run())

    def test_no_escape_outside_library(self):
        outside=Path(self.tmp.name)/'outside.mp3'
        self.audio(outside)
        jid=self.create_job('concluido','A','B','../outside.mp3')
        async def run():
            async with self.client() as c:
                r=await c.request('DELETE',f'/api/jobs/{jid}',headers=HEADERS)
                self.assertEqual(r.status_code,400,r.text)
                self.assertTrue(outside.exists())
        self.api(run())

    def test_progressive_stream_chunks_and_cache_file(self):
        source=Path(self.tmp.name)/'source.webm'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                        'sine=frequency=550:duration=3','-c:a','libopus','-y',str(source)],check=True)
        def fake_commands(_):
            return (['cat',str(source)],['ffmpeg','-hide_banner','-loglevel','error',
                   '-i','pipe:0','-vn','-c:a','libmp3lame','-b:a','128k','-f','mp3','pipe:1'])
        with patch.object(main,'build_stream_commands',fake_commands):
            main.STREAM_SLOTS.acquire()
            chunks=list(main.stream_mp3(ID))
        self.assertGreater(len(chunks),1)
        self.assertGreater(sum(map(len,chunks)),5_000)
        output=main.preview_cache_dir()/(ID+'.mp3')
        self.assertTrue(output.is_file())
        self.assertEqual(main.STREAM_STATES[ID]['state'],'ready')
        with output.open('rb') as f:
            self.assertEqual(f.read(3),b'ID3')

    def test_stream_from_cache_seekable_with_range(self):
        audio=main.preview_cache_dir()/(ID+'.mp3')
        self.audio(audio)
        async def run():
            async with self.client() as c:
                unauth = await c.get('/api/stream/not-an-id')
                self.assertEqual(unauth.status_code,400)
                res=await c.get('/api/stream/'+ID,headers={'Range':'bytes=0-99'})
                self.assertEqual(res.status_code,206,res.text)
                self.assertEqual(len(res.content),100)
                state=await c.get('/api/playback/'+ID)
                self.assertEqual(state.json()['state'],'ready')
        self.api(run())


if __name__=='__main__':unittest.main()
