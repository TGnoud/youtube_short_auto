import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

from app import caption_segments, make_subtitles
from video_jobs import VideoJobs, load_script, probe, read_state, run, voiceover


class FakeVoice:
    def create(self, text, **kwargs):
        rate = 24000
        samples = np.arange(rate, dtype=np.float32) / rate
        return (0.25 * np.sin(2 * np.pi * 880 * samples)).astype(np.float32), rate


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'script.json'
        self.data = {'language': 'en-US', 'title': 'Test', 'segments': [
            {'start': 0, 'end': 3, 'narration': 'First short sentence.'},
            {'start': 3, 'end': 6, 'narration': 'Second short sentence.'},
        ]}

    def save(self):
        self.path.write_text(json.dumps(self.data))
        return self.path

    def test_valid_timed_script(self):
        self.assertEqual(len(load_script(self.save(), 6)['segments']), 2)

    def test_continuous_script_needs_no_timestamps(self):
        self.data = {'language': 'en-US', 'mode': 'continuous', 'title': 'Test',
                     'narration': 'One connected story from beginning to end.'}
        parsed = load_script(self.save(), 6)
        self.assertEqual(parsed['mode'], 'continuous')
        self.assertNotIn('segments', parsed)

    def test_reject_overlap_and_past_end(self):
        for start, end in [(2, 6), (3, 7), (True, 6), (3, float('nan'))]:
            with self.subTest(start=start, end=end):
                self.data['segments'][1].update(start=start, end=end)
                with self.assertRaises(ValueError):
                    load_script(self.save(), 6)


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = self.root / 'demo'
        self.job.mkdir()
        run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=25',
             '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=24000', '-t', '6',
             '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
             str(self.job / 'input.mp4')], timeout=30)
        self.info = probe(self.job / 'input.mp4')
        self.script = {'language': 'en-US', 'title': 'Test', 'segments': [
            {'start': 0, 'end': 3, 'narration': 'First short sentence.'},
            {'start': 3, 'end': 6, 'narration': 'Second short sentence.'},
        ]}

    def render(self, engine=None):
        voiceover(self.job, self.info, self.script, engine or FakeVoice(), 'af_heart', 1,
                  make_subtitles, caption_segments, lambda **kwargs: None)

    def test_replaces_audio_adds_caption_files_and_preserves_geometry(self):
        import subprocess
        self.render()
        for p in ('input.mp4', 'output.mp4'):
            info = probe(self.job / p)
            self.assertAlmostEqual(info['duration'], 6, delta=0.15)
            self.assertEqual((info['width'], info['height']), (320, 180))
        audio = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(self.job / 'output.mp4'),
                                         '-ss', '0.2', '-t', '0.5', '-vn', '-ac', '1', '-ar', '24000',
                                         '-f', 'f32le', '-'])
        samples = np.frombuffer(audio, dtype=np.float32)
        spectrum = np.abs(np.fft.rfft(samples))
        frequency = np.fft.rfftfreq(len(samples), 1/24000)[spectrum.argmax()]
        self.assertAlmostEqual(frequency, 880, delta=5)
        self.assertTrue((self.job / 'captions.srt').is_file())
        ass = (self.job / 'captions.ass').read_text()
        self.assertIn('PlayResX: 320', ass)
        self.assertIn('PlayResY: 180', ass)
        self.assertIn(r'\kf', ass)
        self.assertNotIn(r'\fsc', ass)
        self.assertIn(r'\an5\pos(160,108)', ass)

    def test_subtitles_are_visible_and_word_highlight_changes_in_video(self):
        import subprocess
        black = self.job / 'black.mp4'
        run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
             'color=c=black:size=320x180:rate=25:duration=6', '-c:v', 'libx264',
             '-threads', '1', str(black)])
        black.replace(self.job / 'input.mp4')
        self.info = probe(self.job / 'input.mp4')
        self.render()

        def frame(t):
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(t),
                                           '-i', str(self.job / 'output.mp4'), '-frames:v', '1',
                                           '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
            return np.frombuffer(raw, dtype=np.uint8).reshape(180, 320, 3)

        early, late = frame(0.2), frame(0.8)
        yellow = lambda f: np.sum((f[:, :, 0] > 150) & (f[:, :, 1] > 100) & (f[:, :, 2] < 100))
        self.assertGreater(yellow(early), 10)
        self.assertGreater(yellow(late), 10)
        self.assertGreater(yellow(late), yellow(early) + 10)
        early_mask = (early[:, :, 0] > 150) & (early[:, :, 1] > 100) & (early[:, :, 2] < 100)
        late_mask = (late[:, :, 0] > 150) & (late[:, :, 1] > 100) & (late[:, :, 2] < 100)
        self.assertGreater(np.where(late_mask)[1].mean(), np.where(early_mask)[1].mean())
        white = (early[:, :, 0] > 180) & (early[:, :, 1] > 180) & (early[:, :, 2] > 180)
        self.assertGreater(white.sum(), 10)
        self.assertLess(early[:85].mean(), 1)
        self.assertAlmostEqual(np.where(early_mask | white)[0].mean(), 108, delta=12)

    def test_rotated_video_captions_use_display_dimensions(self):
        rotated = self.job / 'rotated.mp4'
        run(['ffmpeg', '-v', 'error', '-y', '-display_rotation:v:0', '90',
             '-i', str(self.job / 'input.mp4'), '-c', 'copy', str(rotated)])
        rotated.replace(self.job / 'input.mp4')
        self.info = probe(self.job / 'input.mp4')
        self.assertEqual((self.info['display_width'], self.info['display_height']), (180, 320))
        self.render()
        output = probe(self.job / 'output.mp4')
        self.assertEqual((output['display_width'], output['display_height']), (180, 320))
        ass = (self.job / 'captions.ass').read_text()
        self.assertIn('PlayResX: 180', ass)
        self.assertIn('PlayResY: 320', ass)

    def test_too_long_narration_fails_without_output(self):
        self.script['segments'] = [{'start': 0, 'end': 0.5, 'narration': 'Too long.'}]
        with self.assertRaisesRegex(ValueError, 'too long'):
            self.render()
        self.assertFalse((self.job / 'output.mp4').exists())

    def test_small_tempo_adjustment(self):
        self.script['segments'] = [{'start': 0, 'end': 0.9, 'narration': 'Fit this sentence.'}]
        self.render()
        self.assertTrue((self.job / 'output.mp4').exists())

    def test_video_without_original_audio(self):
        silent = self.job / 'silent.mp4'
        run(['ffmpeg', '-v', 'error', '-y', '-i', str(self.job / 'input.mp4'),
             '-map', '0:v:0', '-c:v', 'copy', '-an', str(silent)])
        silent.replace(self.job / 'input.mp4')
        self.info = probe(self.job / 'input.mp4')
        self.assertFalse(self.info['original_audio'])
        self.render()
        self.assertTrue(probe(self.job / 'output.mp4')['original_audio'])

    def test_real_kokoro_smoke(self):
        from app import tts_engine
        self.script = {'language': 'en-US', 'title': 'Continuous Kokoro', 'mode': 'continuous',
                       'narration': 'This is one continuous voiceover, flowing smoothly from the beginning to the very end.'}
        self.render(tts_engine())
        self.assertTrue((self.job / 'output.mp4').is_file())
        self.assertAlmostEqual(probe(self.job / 'output.mp4')['duration'], 6, delta=0.15)

    def test_continuous_narration_one_call_and_no_slot_gaps(self):
        import soundfile as sf
        calls = []

        class ContinuousVoice:
            def create(self, text, **kwargs):
                calls.append(text)
                rate = 24000
                audio = 0.25 * np.sin(2 * np.pi * 880 * np.arange(round(6.3 * rate)) / rate)
                return audio.astype(np.float32), rate

        text = 'A single connected narrative, without separate frame slots.'
        self.script = {'language': 'en-US', 'title': 'Continuous', 'mode': 'continuous', 'narration': text}
        self.render(ContinuousVoice())
        self.assertEqual(calls, [text])
        audio, rate = sf.read(self.job / 'narration.wav')
        self.assertEqual(len(audio), 6 * rate)
        # A frame-slot implementation would leave large silence blocks in this test.
        blocks = audio[round(0.2*rate):round(5.8*rate)].reshape(-1, round(0.1*rate))
        self.assertTrue(np.all(np.sqrt(np.mean(blocks**2, axis=1)) > 0.05))

    def test_continuous_too_short_or_long_is_not_padded_or_clipped(self):
        for seconds, message in ((1, 'Expand'), (8, 'Shorten')):
            with self.subTest(seconds=seconds):
                class Voice:
                    def create(self, *args, **kwargs):
                        rate = 24000
                        return np.full(round(seconds*rate), 0.2, dtype=np.float32), rate
                self.script = {'language': 'en-US', 'mode': 'continuous', 'title': 'Test', 'narration': 'Some narration.'}
                with self.assertRaisesRegex(ValueError, message):
                    self.render(Voice())
                self.assertFalse((self.job / 'output.mp4').exists())

    def test_mp4_and_script_without_frame_preparation(self):
        service = VideoJobs(self.root, threading.Lock(), FakeVoice, 'af_heart', 1,
                            make_subtitles, caption_segments)
        started = time.monotonic()
        service.scan()
        self.assertLess(time.monotonic() - started, 1)
        self.wait(service, 'awaiting_script')
        for name in ('contact-sheet.jpg', 'video-info.json', 'frames', 'PROMPT_FOR_THIS_VIDEO.txt'):
            self.assertFalse((self.job / name).exists())
        (self.job / 'script.json').write_text(json.dumps(self.script))
        service.scan()
        self.wait(service, 'completed')
        self.assertTrue((self.job / 'output.mp4').is_file())
        before = (self.job / 'output.mp4').stat().st_mtime_ns
        service.scan()
        self.assertEqual((self.job / 'output.mp4').stat().st_mtime_ns, before)

    def test_failed_job_retries_only_after_input_changes(self):
        service = VideoJobs(self.root, threading.Lock(), FakeVoice, 'af_heart', 1,
                            make_subtitles, caption_segments)
        bad = {'language': 'en-US', 'title': 'Too short', 'segments': [
            {'start': 0, 'end': 0.5, 'narration': 'This slot is too short.'},
        ]}
        script_path = self.job / 'script.json'
        script_path.write_text(json.dumps(bad))
        service.scan()
        for _ in range(300):
            if read_state(self.job)['status'] == 'failed' and self.job.name not in service.active:
                break
            time.sleep(0.1)
        self.assertEqual(read_state(self.job)['status'], 'failed')
        self.assertIn('too long', read_state(self.job)['error'])
        service.scan()
        self.assertEqual(read_state(self.job)['status'], 'failed')
        script_path.write_text(json.dumps(self.script))
        service.scan()
        self.wait(service, 'completed')

    def test_completed_voiceover_automatically_enters_shared_review(self):
        import app
        from review_bridge import Mp4ReviewBridge
        service = VideoJobs(self.root, threading.Lock(), FakeVoice, 'af_heart', 1,
                            make_subtitles, caption_segments)
        sent = []

        def fake_send(job, metadata, preview):
            sent.append(preview)
            app.write_state(job, status='pending_review', nonce='test1234')

        bridge = Mp4ReviewBridge(service, self.root / '_shared_jobs', threading.RLock(),
                                 app.read_state, app.write_state, fake_send)
        service.on_complete = bridge.queue
        (self.job / 'script.json').write_text(json.dumps(self.script))
        service.scan()
        self.wait(service, 'completed')
        for _ in range(100):
            if not bridge.busy:
                break
            time.sleep(0.02)
        self.assertEqual(len(sent), 1)
        shared = bridge.root / bridge.shared_id('demo')
        self.assertEqual(app.read_state(shared)['status'], 'pending_review')
        self.assertEqual((shared / 'output.mp4').read_bytes(), (self.job / 'output.mp4').read_bytes())

    def wait(self, service, wanted):
        for _ in range(300):
            state = read_state(self.job)
            if state['status'] == wanted and self.job.name not in service.active:
                return
            if state['status'] == 'failed':
                self.fail(state['error'])
            time.sleep(0.1)
        self.fail(f'Timed out waiting for {wanted}: {read_state(self.job)}')


if __name__ == '__main__':
    unittest.main()
