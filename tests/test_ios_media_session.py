import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')


class IOSLockScreenTests(unittest.TestCase):
    def test_register_track_actions_and_remove_10s_seek_actions(self):
        registration = JS.split('function registerSystemMediaControls()', 1)[1].split('function resetSystemMediaSession()', 1)[0]
        self.assertIn("mediaAction('previoustrack'", registration)
        self.assertIn("mediaAction('nexttrack'", registration)
        self.assertIn("mediaAction('seekbackward', null)", registration)
        self.assertIn("mediaAction('seekforward', null)", registration)
        self.assertIn('skipTrack(-1)', registration)
        self.assertIn('skipTrack(1)', registration)

    def test_register_again_on_playing_event(self):
        event = JS.split("playerAudio.addEventListener('playing'", 1)[1].split("playerAudio.addEventListener('pause'", 1)[0]
        self.assertIn('registerSystemMediaControls()', event)
        self.assertIn('publishSystemMediaMetadata()', event)

    def test_native_media_info_and_position(self):
        self.assertIn('new MediaMetadata({', JS)
        self.assertIn('title: trackFromSearch(currentVideo)', JS)
        self.assertIn('artist: currentVideo.channel', JS)
        self.assertIn('session.setPositionState({duration, position:absolutePosition, playbackRate:1})', JS)
        self.assertIn("mediaAction('seekto'", JS)
        self.assertIn('seekPlayerTo(details.seekTime)', JS)

    def test_track_switch_keeps_audio_element_and_close_clears_session(self):
        switch = JS.split('function playAudio(video)', 1)[1].split('function togglePlayer()', 1)[0]
        self.assertNotIn('stopPlayer()', switch)
        self.assertIn("const audio = $('player-audio')", switch)
        self.assertIn('audio.src =', switch)
        stop = JS.split('function stopPlayer()', 1)[1].split('function syncPlayerUI()', 1)[0]
        self.assertIn('resetSystemMediaSession()', stop)


if __name__ == '__main__':
    unittest.main()
