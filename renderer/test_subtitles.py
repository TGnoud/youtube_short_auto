import tempfile
import unittest
from pathlib import Path

from subtitles import make_animated_subtitles


class SubtitleTests(unittest.TestCase):
    def render(self, lines, width=1080, height=1920):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'captions.ass'
            make_animated_subtitles(lines, target, width, height)
            return target.read_text(encoding='utf-8')

    def test_progressive_karaoke_fill_without_resizing(self):
        ass = self.render([(2, 5, 'One two six')])
        events = [line for line in ass.splitlines() if line.startswith('Dialogue:')]
        self.assertEqual(len(events), 1)
        self.assertIn(r'{\kf100}One {\kf100}two {\kf100}six', events[0])
        self.assertIn('0:00:02.00,0:00:05.00', events[0])
        self.assertIn(r'\an5\pos(540,1152)', events[0])
        self.assertNotIn(r'\fsc', ass)
        self.assertIn('&H0000D7FF,&H00FFFFFF', ass)

    def test_empty_and_sub_centisecond_captions_do_not_overrun(self):
        ass = self.render([(0, 0, 'skip'), (0, 1, ''), (0, .02, 'one two three four')])
        events = [line for line in ass.splitlines() if line.startswith('Dialogue:')]
        self.assertEqual(len(events), 1)
        self.assertIn('0:00:00.00,0:00:00.02', events[0])
        import re
        self.assertEqual(sum(map(int, re.findall(r'\\kf(\d+)', events[0]))), 2)

    def test_scales_and_repositions_for_landscape(self):
        ass = self.render([(0, 1, 'Hello')], 1920, 1080)
        self.assertIn('Style: Default,DejaVu Sans,84,', ass)
        self.assertIn('&H00000000,&H00000000,-1,', ass)
        self.assertIn(r'\pos(960,648)', ass)


if __name__ == '__main__':
    unittest.main()
