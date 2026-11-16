import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from review_bridge import Mp4ReviewBridge
from video_jobs import VideoJobs, set_state, signature, read_state as source_state


class SharedReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shared = self.root / 'jobs'
        self.sources = self.root / 'video_jobs'
        self.job = self.sources / 'demo'
        self.job.mkdir(parents=True)
        (self.job / 'input.mp4').write_bytes(b'fake original')
        (self.job / 'output.mp4').write_bytes(b'finished output bytes')
        (self.job / 'script.json').write_text(json.dumps({
            'language': 'en-US', 'title': 'MP4 title', 'description': 'MP4 description',
            'mode': 'continuous', 'narration': 'One connected voiceover for the whole video.',
        }))
        set_state(self.job, status='completed', attempt_signature=signature(self.job))
        self.source = VideoJobs(self.sources, threading.Lock(),
                                lambda: None, 'af_heart', 1, app.make_subtitles, app.caption_segments)
        self.sent = []

        def fake_send(job, metadata, preview):
            self.sent.append((job, metadata, preview))
            app.write_state(job, status='pending_review', nonce='test1234', message_id=1)

        self.sender = fake_send
        self.bridge = self.make_bridge(fake_send)
        self.mock_probe = patch('review_bridge.probe', return_value={'duration': 6})
        self.mock_probe.start()
        self.addCleanup(self.mock_probe.stop)

    def make_bridge(self, sender):
        return Mp4ReviewBridge(self.source, self.shared, threading.RLock(),
                               app.read_state, app.write_state, sender)

    def wait(self):
        for _ in range(100):
            if not self.bridge.busy:
                return
            time.sleep(0.02)
        self.fail('Review worker did not finish')

    def callback(self, decision='ok', user='123', nonce='test1234'):
        shared_id = self.bridge.shared_id('demo')
        update = {'callback_query': {'id': 'callback', 'from': {'id': user},
                  'message': {'chat': {'id': '456'}, 'message_id': 1},
                  'data': f'{decision}|{shared_id}|{nonce}'}}
        with patch.multiple(app, ROOT=self.shared, APPROVER_ID='123', CHAT_ID='456'), patch('app.telegram', return_value={}):
            app.process_callback(update)

    def test_pending_not_uploadable_then_approve_and_finalize(self):
        self.bridge.queue()
        self.wait()
        shared = self.shared / self.bridge.shared_id('demo')
        self.assertEqual(app.read_state(shared)['status'], 'pending_review')
        self.assertEqual((shared / 'output.mp4').read_bytes(), b'finished output bytes')
        self.assertEqual(shared.stat().st_mode & 0o777, 0o755)
        self.assertEqual((shared / 'output.mp4').stat().st_mode & 0o777, 0o644)
        with patch.object(app, 'ROOT', self.shared):
            self.assertFalse(app.approved_next()['has_job'])
        self.callback()
        with patch.object(app, 'ROOT', self.shared):
            result = app.approved_next()
            self.assertTrue(result['has_job'])
            self.assertEqual(result['title'], 'MP4 title')
            self.assertEqual(result['description'], 'MP4 description')
            self.assertEqual(result['video_file'], f'/files/jobs/{shared.name}/output.mp4')
            self.assertFalse(app.approved_next()['has_job'])
            app.finalize(app.FinalRequest(job_id=shared.name, video_id='abcdefghijk'))
        self.assertEqual(app.read_state(shared)['status'], 'published')
        self.assertEqual(self.bridge.summaries()[0]['youtube_video_id'], 'abcdefghijk')

    def test_rejected_and_unauthorized_cannot_upload(self):
        self.bridge.queue()
        self.wait()
        shared = self.shared / self.bridge.shared_id('demo')
        self.callback(user='wrong')
        self.assertEqual(app.read_state(shared)['status'], 'pending_review')
        self.callback(nonce='wrong')
        self.assertEqual(app.read_state(shared)['status'], 'pending_review')
        self.callback(decision='no')
        self.assertEqual(app.read_state(shared)['status'], 'rejected')
        with patch.object(app, 'ROOT', self.shared):
            self.assertFalse(app.approved_next()['has_job'])

    def test_repeated_scan_and_restart_do_not_send_twice(self):
        self.bridge.queue()
        self.bridge.queue()
        self.wait()
        self.bridge.queue()
        self.wait()
        self.assertEqual(len(self.sent), 1)
        self.bridge = self.make_bridge(self.sender)
        self.assertEqual(self.bridge.queue()['queued_for_review'], [])
        self.assertEqual(len(self.sent), 1)

    def test_send_failure_never_enters_upload_queue(self):
        def fail(*args):
            raise RuntimeError('Telegram unavailable')
        self.bridge = self.make_bridge(fail)
        self.bridge.queue()
        self.wait()
        shared = self.shared / self.bridge.shared_id('demo')
        self.assertEqual(app.read_state(shared)['status'], 'failed')
        self.assertEqual(source_state(self.job)['status'], 'completed')
        with patch.object(app, 'ROOT', self.shared):
            self.assertFalse(app.approved_next()['has_job'])
        self.assertEqual(self.bridge.queue()['queued_for_review'], [])

    def test_changed_script_after_render_is_rejected(self):
        (self.job / 'script.json').write_text('{}')
        self.bridge.queue()
        self.wait()
        self.assertFalse(self.sent)
        self.assertIn('changed after rendering', source_state(self.job)['review_error'])


if __name__ == '__main__':
    unittest.main()
