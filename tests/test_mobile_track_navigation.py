import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

class MobileTrackNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / 'app/static/index.html').read_text(encoding='utf8')
        cls.css = (ROOT / 'app/static/style.css').read_text(encoding='utf8')
        cls.js = (ROOT / 'app/static/app.js').read_text(encoding='utf8')

    def test_previous_next_icons_are_track_symbols(self):
        self.assertIn('title="Faixa anterior" aria-label="Faixa anterior"', self.html)
        self.assertIn('title="Próxima faixa" aria-label="Próxima faixa"', self.html)
        self.assertIn('m2 5.5', self.html)  # forward double triangle
        self.assertIn('M14 5.5 6 12l8 6.5', self.html)  # backward double triangle

    def test_mobile_controls_touchable_and_above_seek_bar(self):
        self.assertIn('/* v1.5.4 — transporte de FAIXAS no celular', self.css)
        self.assertIn('.player-transport .transport-skip { width: 52px; height: 48px;', self.css)
        self.assertIn('.player-dock .seek-row { order: 1;', self.css)
        self.assertIn('.player-dock .player-transport { order: 2;', self.css)

    def test_track_navigation_does_not_seek_and_remembers_queue(self):
        self.assertIn('async function skipTrack(direction)', self.js)
        section = self.js.split('async function skipTrack(direction)',1)[1].split("$('player-prev').addEventListener",1)[0]
        self.assertIn('playAudio(playerList[to])', section)
        self.assertNotIn('currentTime', section)
        self.assertIn('await loadNextSearchPage()', section)
        self.assertIn('playerList = searchTracks;', self.js)
        self.assertIn('searchTracks = []; // playerList anterior permanece', self.js)
        self.assertIn("seekBar.addEventListener('change'", self.js)

if __name__ == '__main__': unittest.main()
