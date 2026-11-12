"""Regression tests for the 1.5.4 embedded-artwork feature (no YouTube access)."""
import asyncio
from io import BytesIO
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if 'yt_dlp' not in sys.modules:
    try: import yt_dlp
    except ImportError: sys.modules['yt_dlp'] = types.ModuleType('yt_dlp')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import main
import httpx
from PIL import Image
from mutagen.id3 import ID3

VID = 'dQw4w9WgXcQ'


def sample_jpeg():
    image = Image.new('RGB',(400,400),'#428aff')
    data = BytesIO()
    image.save(data,format='JPEG')
    return data.getvalue()


def sample_webp():
    image = Image.new('RGB',(1200,675),'#ffa244')
    data = BytesIO()
    image.save(data,format='WEBP')
    return data.getvalue()


class FakeResponse:
    def __init__(self,content,mime): self.content=content; self.headers={'content-type':mime}
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def raise_for_status(self): pass
    def iter_content(self,chunk_size): yield self.content


class CoverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        main.CONFIG=root/'dados'
        main.MUSIC=root/'musicas'
        main.DB=main.CONFIG/'app.db'
        main.init_storage()
        self.path=main.MUSIC/'Cantor'/'Musica.mp3'
        self.path.parent.mkdir(parents=True)
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i',
                        'sine=frequency=440:duration=0.15','-acodec','libmp3lame','-y',str(self.path)],check=True)

    def record(self):
        with main.connect() as c:
            return c.execute("INSERT INTO jobs (video_id,url,artist,track,album,status,path,created_at,updated_at) VALUES (?,?,?,?,?,'concluido',?,?,?)",
                     (VID,'https://www.youtube.com/watch?v='+VID,'Cantor','Musica','Album',
                      'Cantor/Musica.mp3',main.now(),main.now())).lastrowid

    def test_webp_converted_to_jpeg_before_embedding(self):
        webp=sample_webp()
        def fake_get(url,**kw):
            self.assertEqual(kw['allow_redirects'],False)
            return FakeResponse(webp,'image/webp')
        with patch.object(main.requests,'get',side_effect=fake_get):
            blob,mime=main.thumbnail_bytes({'id':VID,'thumbnail':'https://i.ytimg.com/vi/'+VID+'/maxresdefault.webp'})
        self.assertEqual(mime,'image/jpeg')
        with Image.open(BytesIO(blob)) as img:
            self.assertEqual(img.format,'JPEG')
            self.assertLessEqual(img.width,1400)
        with patch.object(main,'thumbnail_bytes',return_value=(blob,mime)):
            self.assertTrue(main.tag_mp3(self.path,'Cantor','Musica','Album','https://www.youtube.com/watch?v='+VID,{'id':VID}))
        tags=ID3(self.path)
        self.assertEqual(tags.getall('APIC')[0].mime,'image/jpeg')
        self.assertEqual(tags.getall('APIC')[0].data,blob)
        self.assertEqual(tags.get('TIT2').text[0],'Musica')

    def test_untrusted_artwork_url_not_fetched(self):
        with patch.object(main.requests,'get',return_value=FakeResponse(sample_jpeg(),'image/jpeg')) as request:
            result=main.thumbnail_bytes({'thumbnail':'https://example.invalid/private-picture.jpg'})
        self.assertIsNone(result)
        request.assert_not_called()

    def test_preserve_cover_when_retagging_or_image_unavailable(self):
        cover=(sample_jpeg(),'image/jpeg')
        main.write_cover(self.path,cover)
        main.retag_mp3(self.path,'Artista Novo','Faixa Nova','Novo album')
        self.assertEqual(main.embedded_cover(self.path),cover)
        with patch.object(main,'thumbnail_bytes',return_value=None):
            self.assertTrue(main.tag_mp3(self.path,'Artista Novo','Faixa Nova','Album','https://www.youtube.com/watch?v='+VID,{'id':VID}))
        self.assertEqual(main.embedded_cover(self.path),cover)

    def test_repair_single_job_without_redownloading_audio(self):
        job_id=self.record()
        main.retag_mp3(self.path, 'Cantor', 'Musica', 'Album')
        before_size=self.path.stat().st_size
        with patch.object(main,'thumbnail_bytes',return_value=(sample_jpeg(),'image/jpeg')) as thumb:
            result=main.refresh_job_cover(job_id)
            skipped=main.refresh_job_cover(job_id)
        self.assertEqual(result,'updated')
        self.assertEqual(skipped,'skip')
        self.assertEqual(thumb.call_count,1)
        self.assertGreater(self.path.stat().st_size,before_size)
        self.assertTrue(main.embedded_cover(self.path))
        self.assertEqual(ID3(self.path).get('TIT2').text[0],'Musica')
        with main.connect() as c:
            self.assertIn('Capa incorporada',c.execute('SELECT message FROM jobs WHERE id=?',(job_id,)).fetchone()['message'])

    def test_bulk_repair_skips_existing_and_handles_errors(self):
        job_id=self.record()
        main.COVER_REPAIR_STATE.update(running=True,total=0,processed=0,updated=0,skipped=0,failed=0,message='')
        with patch.object(main,'thumbnail_bytes',return_value=(sample_jpeg(),'image/jpeg')):
            main.run_cover_repair()
        self.assertFalse(main.COVER_REPAIR_STATE['running'])
        self.assertEqual(main.COVER_REPAIR_STATE['updated'],1)
        self.assertEqual(main.COVER_REPAIR_STATE['total'],1)
        main.COVER_REPAIR_STATE.update(running=True,total=0,processed=0,updated=0,skipped=0,failed=0,message='')
        with patch.object(main,'thumbnail_bytes',side_effect=AssertionError('Should skip embedded cover')):
            main.run_cover_repair()
        self.assertEqual(main.COVER_REPAIR_STATE['skipped'],1)

    def test_cover_endpoint_requires_login_and_serves_embedded_image(self):
        job=self.record()
        main.write_cover(self.path,(sample_jpeg(),'image/jpeg'))
        async def run():
            transport=httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport,base_url='http://testserver') as client:
                self.assertEqual((await client.get(f'/api/jobs/{job}/cover')).status_code,401)
                headers={'X-App-Request':'zimaos-music'}
                self.assertEqual((await client.post('/api/setup',json={'username':'teste','password':'abc123'},headers=headers)).status_code,200)
                response=await client.get(f'/api/jobs/{job}/cover')
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.headers['content-type'],'image/jpeg')
                self.assertEqual(response.content,sample_jpeg())
        asyncio.run(run())

    def test_ui_offers_cover_tools(self):
        static=Path(__file__).resolve().parents[1]/'app/static'
        html=(static/'index.html').read_text()
        js=(static/'app.js').read_text()
        self.assertIn('id="repair-covers"',html)
        self.assertIn('id="cover-repair-status"',html)
        self.assertIn('/api/jobs/${job.id}/cover',js)
        self.assertIn("'Atualizar capa'",js)

if __name__=='__main__': unittest.main()
