import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class PlayerControlsTests(unittest.TestCase):
    def test_standard_play_pause_icons_and_independent_indicator(self):
        html=(ROOT/'app/static/index.html').read_text()
        css=(ROOT/'app/static/style.css').read_text()
        js=(ROOT/'app/static/app.js').read_text()
        self.assertIn('id="player-toggle"',html)
        self.assertIn('class="play-icon"',html)
        self.assertIn('class="pause-icon"',html)
        self.assertIn('class="now-playing-bars"',html)
        self.assertIn('id="player-state-label"',html)
        self.assertIn('id="player-mute"',html)
        self.assertIn('function playbackIcon(type)',js)
        self.assertIn("playing:'REPRODUZINDO'",js)
        self.assertIn('.hero-play[data-state=playing] .pause-icon',css)
        self.assertIn('.player-dock[data-state=playing] .now-playing-bars i',css)
        self.assertNotIn('class="eq-bars"',html)
        self.assertNotIn('▶', html.split('id="player-panel"',1)[1].split('</section>',1)[0])
    def test_seek_slider_is_enabled_for_known_duration_and_uses_offset_endpoint(self):
        html=(ROOT/'app/static/index.html').read_text()
        js=(ROOT/'app/static/app.js').read_text()
        css=(ROOT/'app/static/style.css').read_text()
        self.assertIn('id="player-seek"',html)
        self.assertIn("seekBar.addEventListener('input'",js)
        self.assertIn("seekBar.addEventListener('change'",js)
        self.assertIn('playbackStartSeconds',js)
        self.assertIn('?start=${seconds}',js)
        self.assertIn('nativeSeekAvailable',js)
        self.assertIn('#player-seek::-webkit-slider-thumb',css)
        self.assertIn('aria-valuetext',js)
    def test_version_consistent(self):
        for rel in ('README.md','app/main.py','iniciar.sh','compose.yaml','app/static/index.html'):
            self.assertIn('1.5.6',(ROOT/rel).read_text(),rel)
if __name__=='__main__': unittest.main()
