"""Seek regression: no internet; ensures restart at offset and cache integrity."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from test_app import main
import httpx

ID='dQw4w9WgXcQ'
HEADERS={'x-app-request':'zimaos-music'}

class SeekTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        main.CONFIG=Path(self.tmp.name)/'config'
        main.MUSIC=Path(self.tmp.name)/'music'
        main.DB=main.CONFIG/'app.db'
        main.init_storage()
        main.STREAM_STATES.clear()

    def test_direct_seek_uses_input_level_ffmpeg_offset(self):
        class FakeDL:
            def __init__(self, options): self.options=options
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def extract_info(self, url, download=False):
                return {'url': 'https://r3---sn-abc.googlevideo.com/videoplayback?id=123',
                        'http_headers': {'User-Agent': 'test-user-agent', 'Referer':'https://www.youtube.com/'}}
        with patch.object(main.yt_dlp, 'YoutubeDL', FakeDL, create=True):
            cmd=main.direct_seek_command(ID, 95)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd[cmd.index('-ss')+1], '95')
        self.assertLess(cmd.index('-ss'),cmd.index('-i'))
        self.assertEqual(cmd[cmd.index('-t')+1], '1405')
        self.assertIn('test-user-agent',cmd[cmd.index('-headers')+1])
        class UnsafeDL(FakeDL):
            def extract_info(self, *_args,**_kwargs):
                return {'url':'http://127.0.0.1/private'}
        with patch.object(main.yt_dlp, 'YoutubeDL', UnsafeDL, create=True):
            self.assertIsNone(main.direct_seek_command(ID,95))

    def test_seek_fallback_command_positions(self):
        yt,cmd=main.build_stream_commands(ID, 105)
        self.assertIn('--output',yt)
        self.assertEqual(cmd[cmd.index('-ss')+1],'105')
        self.assertLess(cmd.index('-i'),cmd.index('-ss'))
        self.assertEqual(cmd[cmd.index('-t')+1],'1395')
        _,cmd_zero=main.build_stream_commands(ID)
        self.assertNotIn('-ss',cmd_zero)

    def test_seek_from_pipe_emits_short_segment_and_never_caches_partial(self):
        source=Path(self.tmp.name)/'source.webm'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi',
                        '-i','sine=frequency=660:duration=5','-c:a','libopus','-y',str(source)],check=True)
        original=main.build_stream_commands
        def fake_cmd(_vid,offset=0):
            _,ff=original(ID,offset)
            return ['cat',str(source)],ff
        with patch.object(main,'direct_seek_command',return_value=None),patch.object(main,'build_stream_commands',side_effect=fake_cmd):
            main.STREAM_SLOTS.acquire()
            output=b''.join(main.stream_mp3(ID,2))
        self.assertGreater(len(output),1000)
        target=Path(self.tmp.name)/'seek.mp3';target.write_bytes(output)
        result=subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration',
                                        '-of','default=nw=1:nk=1',str(target)],text=True)
        seconds=float(result)
        self.assertGreater(seconds,2.3)
        self.assertLess(seconds,4.0)
        self.assertFalse((main.preview_cache_dir()/(ID+'.mp3')).exists())
        self.assertEqual(main.STREAM_STATES[ID]['state'],'ready')

    def test_local_seek_endpoint_range_and_validation(self):
        audio=main.preview_cache_dir()/(ID+'.mp3')
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                        'sine=frequency=440:duration=5','-c:a','libmp3lame','-y',str(audio)],check=True)
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://testserver') as c:
                await c.post('/api/setup',json={'username':'demo','password':'abcdef'},headers=HEADERS)
                r=await c.get(f'/api/stream/{ID}?start=-1');self.assertEqual(r.status_code,400)
                r=await c.get(f'/api/stream/{ID}?start=2000');self.assertEqual(r.status_code,400)
                r=await c.get(f'/api/stream/{ID}',headers={'Range':'bytes=0-99'})
                self.assertEqual(r.status_code,206);self.assertEqual(len(r.content),100)
                r=await c.get(f'/api/stream/{ID}?start=2')
                self.assertEqual(r.status_code,200,r.text[:500]);self.assertGreater(len(r.content),1000)
                target=Path(self.tmp.name)/'local-offset.mp3';target.write_bytes(r.content)
                duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries',
                    'format=duration','-of','default=nw=1:nk=1',str(target)],text=True))
                self.assertGreater(duration,2.5)
                self.assertLess(duration,4)
                self.assertTrue(audio.exists())
        asyncio.run(run())

if __name__=='__main__':unittest.main()
