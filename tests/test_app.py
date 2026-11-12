import asyncio
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

# Unit tests can run without a network connection or downloading the real yt-dlp.
if 'yt_dlp' not in sys.modules:
    try:
        import yt_dlp
    except ImportError:
        sys.modules['yt_dlp'] = types.ModuleType('yt_dlp')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import httpx
import main
from mutagen.id3 import ID3

class AppTests(unittest.TestCase):
    def setUp(self):
        self.workdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.workdir.cleanup)
        root = Path(self.workdir.name)
        main.CONFIG = root / 'config'
        main.MUSIC = root / 'music'
        main.DB = main.CONFIG / 'app.db'
        main.JOBS = main.queue.Queue()
        main.init_storage()

    def test_http_auth_and_queue(self):
        async def run():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
                response = await client.get('/api/state')
                self.assertTrue(response.json()['needs_setup'])
                response = await client.post('/api/setup',json={'username':'teste','password':'abc123'})
                self.assertEqual(response.status_code,403) # custom header required
                headers = {'X-App-Request':'zimaos-music'}
                response = await client.post('/api/setup',json={'username':'teste','password':'abc123'}, headers=headers)
                self.assertEqual(response.status_code,200, response.text)
                self.assertTrue((await client.get('/api/state')).json()['logged_in'])
                response = await client.post('/api/setup',json={'username':'outra','password':'abc123'},headers=headers)
                self.assertEqual(response.status_code,403)
                response = await client.post('/api/jobs',json={'url':'https://youtu.be/dQw4w9WgXcQ','artist':'Artista','track':'Faixa','album':''},headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                job_id = response.json()['id']
                response = await client.post('/api/jobs',json={'url':'https://youtu.be/dQw4w9WgXcQ','artist':'Artista','track':'Faixa'},headers=headers)
                self.assertEqual(response.status_code,409)
                jobs = (await client.get('/api/jobs')).json()['jobs']
                self.assertEqual(jobs[0]['artist'],'Artista')
                response = await client.post(f'/api/jobs/{job_id}/cancel',headers=headers)
                self.assertEqual(response.status_code,200)
                response = await client.post('/api/logout',headers=headers)
                self.assertEqual(response.status_code,200)
                self.assertEqual((await client.get('/api/jobs')).status_code,401)
                self.assertEqual((await client.post('/api/login',json={'username':'teste','password':'wrong'},headers=headers)).status_code,422)
                self.assertEqual((await client.post('/api/login',json={'username':'teste','password':'foobar'},headers=headers)).status_code,401)
                self.assertEqual((await client.post('/api/login',json={'username':'teste','password':'abc123'},headers=headers)).status_code,200)
        asyncio.run(run())

    def test_search_requires_login_and_filters_ids(self):
        async def run():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
                headers = {'X-App-Request':'zimaos-music'}
                unauth = await client.post('/api/search',json={'query':'cantor'},headers=headers)
                self.assertEqual(unauth.status_code,401)
                await client.post('/api/setup', json={'username':'teste','password':'abc123'},headers=headers)
                bad = await client.post('/api/search',json={'query':'a '},headers=headers)
                self.assertEqual(bad.status_code,400)
                no_header = await client.post('/api/search',json={'query':'cantor'})
                self.assertEqual(no_header.status_code,403)
                with patch.object(main, 'search_videos', return_value=([{ 
                    'id':'dQw4w9WgXcQ', 'title':'Faixa','channel':'Canal terceiro',
                    'duration':210, 'url':'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                    'thumbnail':'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg'
                }], True)):
                    resp=await client.post('/api/search',json={'query':'cantor'},headers=headers)
                    self.assertEqual(resp.status_code,200, resp.text)
                    self.assertEqual(resp.json()['results'][0]['channel'],'Canal terceiro')
                    self.assertNotIn('artist',resp.json()['results'][0])
                    self.assertTrue(resp.json()['has_more'])
                    self.assertEqual(resp.json()['next_offset'],20)
        asyncio.run(run())

    def test_analyze_prefills_artist_from_channel(self):
        async def run():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
                headers = {'X-App-Request':'zimaos-music'}
                await client.post('/api/setup', json={'username':'teste','password':'abc123'}, headers=headers)
                with patch.object(main, 'get_info', return_value={
                    'title':'Banda do Teste - Música A', 'channel':'Canal de Teste',
                    'artist':'Outro Artista', 'track':'Música A', 'thumbnail':''
                }):
                    resp = await client.post('/api/analyze', json={'url':'https://youtu.be/dQw4w9WgXcQ'}, headers=headers)
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertEqual(resp.json()['artist'], 'Canal de Teste')
                self.assertEqual(resp.json()['track'], 'Música A')
        asyncio.run(run())

    def test_search_flat_extraction(self):
        class FakeYDL:
            def __init__(self, opts): self.opts=opts
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def extract_info(self, search, download=False):
                self_search.assertEqual(search, 'ytsearchall:Artista Musica')
                self_search.assertEqual(download, False)
                self_search.assertEqual(self.opts['extract_flat'], 'in_playlist')
                self_search.assertEqual(self.opts['playlist_items'], '1:21')
                self_search.assertTrue(self.opts['lazy_playlist'])
                return {'entries': [
                    {'id': 'dQw4w9WgXcQ', 'title': 'Artista - Música', 'channel':'Canal 1', 'duration':200},
                    {'id': '<script>alert(1)</script>', 'title':'Inseguro'},
                    {'id': '9bZkp7q19f0', 'title': 'Outro', 'uploader':'Canal 2'}
                ]}
        self_search = self
        with patch.object(main.yt_dlp, 'YoutubeDL', FakeYDL, create=True):
            videos, has_more=main.search_videos('Artista Musica')
        self.assertEqual(len(videos),2)
        self.assertEqual(videos[0]['duration'],200)
        self.assertEqual(videos[1]['channel'],'Canal 2')
        self.assertFalse(has_more)

    def test_project_local_persistence(self):
        compose=(Path(__file__).resolve().parents[1]/'compose.yaml').read_text()
        ignore=(Path(__file__).resolve().parents[1]/'.dockerignore').read_text()
        self.assertIn('YouTube_Music}/Biblioteca/Musicas:/music', compose)
        self.assertIn('YouTube_Music}/dados:/config', compose)
        self.assertIn('Biblioteca/', ignore)
        self.assertIn('dados/', ignore)

    def test_download_organizes_file_with_manual_artist_override(self):
        video = 'dQw4w9WgXcQ'
        url = 'https://www.youtube.com/watch?v=' + video
        with main.connect() as c:
            job_id = c.execute("""INSERT INTO jobs (video_id,url,artist,track,album,status,created_at,updated_at)
                VALUES (?,?,?,?,?,'pendente',?,?)""",
                (video, url, 'Cantor Correto', 'Canção Correta', '', main.now(), main.now())).lastrowid

        class FakeYDL:
            def __init__(self, params): self.params = params
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def extract_info(self, link, download=False):
                self.params['progress_hooks'][0]({'status':'finished'})
                outfile = Path(self.params['outtmpl'].replace('%(id)s',video).replace('%(ext)s','mp3'))
                subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                                'sine=frequency=440:duration=0.5','-acodec','libmp3lame','-y',str(outfile)],check=True)
                return {'title':'Canção Correta','uploader':'Canal Que Não É O Cantor'}

        with patch.object(main.yt_dlp, 'YoutubeDL', FakeYDL, create=True):
            main.download(job_id)
        mp3 = main.MUSIC / 'Cantor Correto' / 'Canção Correta.mp3'
        self.assertTrue(mp3.exists())
        self.assertEqual(ID3(mp3).get('TPE1').text[0], 'Cantor Correto')
        with main.connect() as c:
            row = c.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
        self.assertEqual(row['status'],'concluido')

    def test_tagging_mp3(self):
        output = Path(self.workdir.name)/'audio.mp3'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=0.5',
                        '-acodec','libmp3lame','-q:a','7','-y',str(output)],check=True)
        main.tag_mp3(output,'Artista','Minha Canção','Álbum','https://www.youtube.com/watch?v=dQw4w9WgXcQ',{})
        meta=ID3(output)
        self.assertEqual(meta.get('TPE1').text[0],'Artista')
        self.assertEqual(meta.get('TIT2').text[0],'Minha Canção')
        self.assertEqual(meta.get('TALB').text[0],'Álbum')

if __name__ == '__main__': unittest.main()
