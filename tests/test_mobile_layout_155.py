"""Regressions for the v1.5.5 mobile layout; browser screenshots are tested separately."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
CSS=(ROOT/'app/static/style.css').read_text()
HTML=(ROOT/'app/static/index.html').read_text()
JS=(ROOT/'app/static/app.js').read_text()

class MobileLayout155(unittest.TestCase):
    def test_safe_area_for_iphone(self):
        self.assertIn('viewport-fit=cover',HTML)
        self.assertIn('safe-area-inset-bottom',CSS)
        self.assertIn('safe-area-inset-top',CSS)

    def test_mobile_navigation_at_bottom(self):
        self.assertIn('/* v1.5.5 - mobile-first:',CSS)
        self.assertIn('grid-template-columns:repeat(3,minmax(0,1fr))',CSS)
        self.assertIn('inset:auto 0 0 0',CSS)
        self.assertIn('aria-orientation',JS)

    def test_independent_scroll_for_results_and_downloads(self):
        self.assertIn('.results-scroll{min-height:0;flex:1;overflow-y:auto',CSS)
        self.assertIn('#jobs-list{flex:1;min-height:0;overflow-y:auto',CSS)
        self.assertIn('const scrollContainer = list;',JS)

    def test_mobile_logout_and_svg_nav_icons(self):
        self.assertIn('id="mobile-logout"',HTML)
        self.assertIn("$('mobile-logout').addEventListener('click'",JS)
        self.assertEqual(HTML.count('class="nav-icon"><svg'),3)

    def test_job_updates_do_not_rebuild_unchanged_cards(self):
        self.assertIn('if (signature === lastJobsSignature) return;',JS)
        self.assertIn("art.loading = 'lazy'",JS)

    def test_version_updated(self):
        self.assertIn('v1.5.7',HTML)
        self.assertIn("VERSION = '1.5.7'",(ROOT/'app/main.py').read_text())

if __name__=='__main__': unittest.main()
