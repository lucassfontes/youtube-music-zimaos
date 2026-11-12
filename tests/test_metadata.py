import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from metadata import youtube_video_url, guess_metadata, clean_filename

class MetadataTests(unittest.TestCase):
    def test_youtube_urls(self):
        self.assertEqual(youtube_video_url('https://youtu.be/dQw4w9WgXcQ?t=15'),
                         'https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(youtube_video_url('https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RD'),
                         'https://music.youtube.com/watch?v=dQw4w9WgXcQ')
        with self.assertRaises(ValueError):youtube_video_url('https://notyoutube.com/watch?v=dQw4w9WgXcQ')
        with self.assertRaises(ValueError):youtube_video_url('https://youtube.com/playlist?list=123')
        with self.assertRaises(ValueError):youtube_video_url('http://youtube.com/watch?v=dQw4w9WgXcQ')
    def test_music_metadata(self):
        value = guess_metadata({'title':'Música original (Official Video)', 'artist':'Cantor Real',
                                'track':'Música original', 'uploader':'Canal Alheio'})
        self.assertEqual((value['artist'], value['track'], value['confidence']),
                         ('Canal Alheio', 'Música original', 'media'))
    def test_artist_in_video_title(self):
        value = guess_metadata({'title':'Artista Real - Música A [Official Video]', 'uploader':'Canal de música'})
        self.assertEqual(value['artist'], 'Canal de música')
        self.assertEqual(value['track'], 'Música A')
        self.assertEqual(value['confidence'], 'media')
    def test_use_uploader_as_artist_when_channel_missing(self):
        value = guess_metadata({'title':'Música sem autor', 'uploader':'Canal Qualquer'})
        self.assertEqual(value['artist'], 'Canal Qualquer')
        self.assertEqual(value['confidence'], 'media')
    def test_channel_priority_over_uploader_and_artist(self):
        value = guess_metadata({'title':'Cantor Antigo - Meu Som (Official Video)',
                                'channel':'Canal Principal', 'uploader':'Outro Publicador',
                                'artist':'Artista da Gravação'})
        self.assertEqual(value['artist'], 'Canal Principal')
        self.assertEqual(value['track'], 'Meu Som')
        self.assertEqual(value['source'], 'nome do canal do YouTube')
    def test_no_channel_falls_back_to_metadata(self):
        value = guess_metadata({'title':'Minha Música', 'artist':'Artista Registrado'})
        self.assertEqual(value['artist'], 'Artista Registrado')
        self.assertEqual(value['confidence'], 'media')
    def test_no_artist_requires_review(self):
        value = guess_metadata({'title':'Música sem autor'})
        self.assertEqual(value['artist'], '')
        self.assertEqual(value['confidence'], 'revisar')
    def test_channel_string_cannot_escape_project_folder(self):
        value = guess_metadata({'title':'Faixa', 'channel':'../../Canal\\Teste'})
        self.assertNotIn('/', clean_filename(value['artist']))
        self.assertNotIn('\\', clean_filename(value['artist']))
    def test_path_sanitization(self):
        self.assertEqual(clean_filename('../../Música: AO VIVO?'), 'Música AO VIVO')
        self.assertEqual(clean_filename('\\\\'), 'Sem nome')

if __name__ == '__main__': unittest.main()
