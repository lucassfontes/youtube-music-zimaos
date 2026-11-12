"""MP3 metadata read by VLC-style ID3 readers, no live YouTube access."""
import io
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if 'yt_dlp' not in sys.modules:
    try:
        import yt_dlp
    except ImportError:
        sys.modules['yt_dlp'] = types.ModuleType('yt_dlp')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import main
from mutagen.id3 import ID3
from PIL import Image

VID = 'dQw4w9WgXcQ'
URL = f'https://www.youtube.com/watch?v={VID}'


def image_jpeg():
    pic = Image.new('RGB', (120, 120), '#334466')
    out = io.BytesIO()
    pic.save(out, format='JPEG')
    return out.getvalue()


class MetadataForVlcTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        main.CONFIG, main.MUSIC = root / 'dados', root / 'Musica'
        main.DB = main.CONFIG / 'app.db'
        main.init_storage()
        self.mp3 = main.MUSIC / 'Cantor' / 'Música.mp3'
        self.mp3.parent.mkdir(parents=True)
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
                        'sine=frequency=330:duration=0.2','-acodec','libmp3lame',str(self.mp3)],check=True)

    def record(self, *, track='Música', artist='Cantor', album=''):
        with main.connect() as c:
            return c.execute("INSERT INTO jobs (video_id,url,artist,track,album,status,path,created_at,updated_at) "
                     "VALUES (?,?,?,?,?,'concluido',?,?,?)",(VID,URL,artist,track,album,'Cantor/Música.mp3',main.now(),main.now())).lastrowid

    def test_id3_v23_music_fields_and_artwork(self):
        info={'id':VID,'upload_date':'20241109','release_year':2020,'release_date':'20200421',
              'album':'Álbum oficial','genre':'Sertanejo','track_number':3,
              'record_label':'Gravadora','isrc':'BRABC1234567'}
        cover = (image_jpeg(),'image/jpeg')
        with patch.object(main,'thumbnail_bytes',return_value=cover):
            self.assertTrue(main.tag_mp3(self.mp3,'Cantor Canal','Título','',URL,info))
        tags = ID3(self.mp3)
        self.assertEqual(tags.version[0:2], (2,3))
        for id_, expected in [('TIT2','Título'),('TPE1','Cantor Canal'),('TALB','Álbum oficial'),
                              ('TCON','Sertanejo'),('TRCK','3'),('TPUB','Gravadora'),
                              ('TSRC','BRABC1234567')]:
            self.assertEqual(str(tags.get(id_).text[0]),expected,id_)
        self.assertEqual(str(tags.get('TDRC').text[0]),'2020-04-21')
        self.assertEqual(tags.get('WOAS').url, URL)
        self.assertEqual(main.embedded_cover(self.mp3),cover)
        self.assertTrue(self.mp3.read_bytes().count(b'ID3')>=1)

    def test_upload_date_is_not_assumed_release_date(self):
        self.assertNotIn('date',main.additional_music_metadata({'upload_date':'20251109'}))
        self.assertEqual(main.additional_music_metadata({'release_year':2021})['date'],'2021')
        self.assertEqual(main.additional_music_metadata({'release_date':'20210131'})['date'],'2021-01-31')
        self.assertEqual(main.additional_music_metadata({'release_year':1890}),{})

    def test_metadata_only_existing_mp3_preserves_artist_and_cover(self):
        job=self.record(artist='Canal Customizado',track='Música do usuário')
        main.write_cover(self.mp3,(image_jpeg(),'image/jpeg'))
        original_cover=main.embedded_cover(self.mp3)
        old_size=self.mp3.stat().st_size
        with patch.object(main,'youtube_music_info',return_value={'album':'Álbum novo','genre':'Rock',
                                                                   'release_year':2022}) as query,\
             patch.object(main,'thumbnail_bytes') as download_cover:
            output=main.refresh_job_metadata(job)
        self.assertTrue(output['retrieved_from_youtube'])
        query.assert_called_once_with(URL)
        download_cover.assert_not_called()  # cover is retained; no unnecessary requests
        tags=ID3(self.mp3)
        self.assertEqual(tags.get('TPE1').text[0],'Canal Customizado')
        self.assertEqual(tags.get('TIT2').text[0],'Música do usuário')
        self.assertEqual(tags.get('TALB').text[0],'Álbum novo')
        self.assertEqual(str(tags.get('TDRC').text[0]),'2022')
        self.assertEqual(main.embedded_cover(self.mp3), original_cover)
        self.assertGreaterEqual(self.mp3.stat().st_size,old_size)
        with main.connect() as c:
            self.assertEqual(c.execute('SELECT album FROM jobs WHERE id=?',(job,)).fetchone()['album'],'Álbum novo')

    def test_youtube_unavailable_still_saves_local_metadata(self):
        job=self.record(track='Canção sem detalhes')
        with patch.object(main,'youtube_music_info',side_effect=RuntimeError('429')):
            with patch.object(main,'thumbnail_bytes',return_value=None):
                result=main.refresh_job_metadata(job)
        self.assertFalse(result['retrieved_from_youtube'])
        self.assertEqual(ID3(self.mp3).get('TIT2').text[0],'Canção sem detalhes')
        self.assertTrue(self.mp3.is_file())

    def test_copy_before_tagging_protects_original_on_error(self):
        job=self.record()
        before=self.mp3.read_bytes()
        with patch.object(main,'youtube_music_info',return_value={}), \
             patch.object(main,'tag_mp3',side_effect=ValueError('Falha salvando tags')):
            with self.assertRaises(ValueError): main.refresh_job_metadata(job)
        self.assertEqual(self.mp3.read_bytes(),before)
        self.assertEqual(list(self.mp3.parent.glob('.*.metadata.mp3')),[])

    def test_retag_still_preserves_extended_tags(self):
        with patch.object(main,'thumbnail_bytes',return_value=None):
            main.tag_mp3(self.mp3,'Canal','Original','',URL,{'genre':'Rock','release_year':2018})
        main.retag_mp3(self.mp3,'Canal novo','Título novo','Novo Álbum')
        tags=ID3(self.mp3)
        self.assertEqual(tags.get('TPE1').text[0],'Canal novo')
        self.assertEqual(str(tags.get('TDRC').text[0]),'2018')
        self.assertEqual(tags.get('TCON').text[0],'Rock')


if __name__ == '__main__': unittest.main()
