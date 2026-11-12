import asyncio
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import yt_dlp
except ImportError:
    sys.modules['yt_dlp'] = types.ModuleType('yt_dlp')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import main
import httpx

ROOT = Path(__file__).resolve().parents[1]
ID = 'dQw4w9WgXcQ'


class AudioUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        main.CONFIG = Path(self.temp.name) / 'config'
        main.MUSIC = Path(self.temp.name) / 'music'
        main.DB = main.CONFIG / 'app.db'
        main.PREVIEW_STATES.clear()
        main.init_storage()

    def sample_mp3(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['ffmpeg', '-hide_banner','-loglevel','error','-f','lavfi','-i',
                        'sine=frequency=440:duration=2','-acodec','libmp3lame','-y',str(path)],
                       check=True)

    def test_auth_and_range_playback_of_prepared_file(self):
        audio = main.preview_cache_dir() / f'{ID}.mp3'
        self.sample_mp3(audio)
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://local') as client:
                self.assertEqual((await client.get(f'/api/audio/{ID}')).status_code, 401)
                await client.post('/api/setup',json={'username':'tester','password':'abc123'},headers={'x-app-request':'zimaos-music'})
                response = await client.get(f'/api/audio/{ID}', headers={'Range':'bytes=0-99'})
                self.assertEqual(response.status_code,206, response.text)
                self.assertEqual(response.headers['content-type'],'audio/mpeg')
                self.assertEqual(response.headers['content-range'].split('/')[0],'bytes 0-99')
                self.assertEqual(len(response.content),100)
                self.assertEqual((await client.get(f'/api/preview/{ID}')).json()['state'],'ready')
                self.assertEqual((await client.get('/icon.png')).status_code,200)
        asyncio.run(run())

    def test_preparation_returns_readable_mp3_and_progress(self):
        class FakeYDL:
            def __init__(self,opts): self.opts=opts
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def extract_info(self,url,download=True):
                self_test.assertEqual(url, f'https://www.youtube.com/watch?v={ID}')
                self_test.assertTrue(download)
                self_test.assertEqual(self.opts['format'], 'bestaudio[ext=m4a]/bestaudio/best')
                self.opts['progress_hooks'][0]({'status':'finished'})
                output = Path(self.opts['outtmpl'].replace('%(id)s',ID).replace('%(ext)s','mp3'))
                self_test.sample_mp3(output)
                return {'title':'Faixa'}
        self_test=self
        with patch.object(main.yt_dlp,'YoutubeDL',FakeYDL, create=True):
            main.prepare_preview(ID)
        self.assertEqual(main.preview_state(ID)['state'],'ready')
        self.assertTrue((main.CONFIG/'preview-cache'/f'{ID}.mp3').exists())
        self.assertFalse((main.CONFIG/'preview-work'/ID).exists())

    def test_preview_failure_reports_error_instead_of_silent_zero_seconds(self):
        class FailingYDL:
            def __init__(self,opts):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def extract_info(self,*args,**kwargs):raise RuntimeError('HTTP Error 403 Forbidden')
        with patch.object(main.yt_dlp,'YoutubeDL',FailingYDL,create=True):
            main.prepare_preview(ID)
        state = main.preview_state(ID)
        self.assertEqual(state['state'],'error')
        self.assertIn('403',state['message'])

    def test_previews_do_not_delete_user_songs(self):
        saved = main.MUSIC / 'Artista' / 'Música.mp3'
        self.sample_mp3(saved)
        for n in range(35):
            (main.preview_cache_dir()/f'{n:011}.mp3').write_bytes(b'content' * 200)
        main.clean_preview_cache()
        self.assertTrue(saved.is_file())
        self.assertLessEqual(len(list(main.preview_cache_dir().glob('*.mp3'))), main.PREVIEW_CACHE_MAX_FILES)

    def test_search_is_paginated_with_has_more(self):
        class FakeYDL:
            def __init__(self, options): self.options=options
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def extract_info(self,url,download=False):
                self_test.assertEqual(url,'ytsearchall:meu cantor')
                self_test.assertEqual(self.options['playlist_items'],'41:61')
                return {'entries':[{'id':ID,'title':'Faixa','channel':'Canal'} for _ in range(21)]}
        self_test=self
        with patch.object(main.yt_dlp,'YoutubeDL',FakeYDL,create=True):
            videos,more=main.search_videos('meu cantor',40,20)
        self.assertEqual(len(videos),20)
        self.assertTrue(more)

    def test_menu_tabs_inner_scroll_and_local_audio(self):
        html=(ROOT/'app/static/index.html').read_text()
        js=(ROOT/'app/static/app.js').read_text()
        css=(ROOT/'app/static/style.css').read_text()
        self.assertIn('id="results-scroll"',html)
        self.assertIn('data-tab="downloads"',html)
        self.assertIn('data-tab="link"',html)
        self.assertIn('data-tab="search"',html)
        self.assertIn('<audio id="player-audio"',html)
        self.assertNotIn('<iframe',html)
        self.assertIn('root: searchScroll',js)
        self.assertIn('overflow-y:auto',css)
        self.assertIn('player-dock',css)
        self.assertIn('api/stream/',js)
        self.assertIn('id="edit-dialog"', html)
        self.assertIn('id="player-toggle"', html)
        self.assertIn('x-casaos:',(ROOT/'compose.yaml').read_text())
        self.assertTrue((ROOT/'app/static/icon.png').is_file())

if __name__=='__main__': unittest.main()
