"""Hand finished MP4 voiceovers to the shared Telegram/YouTube approval queue."""
import hashlib
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path

from video_jobs import load_script, probe, read_state as source_state, set_state, run, signature


def review_proxy(job, original):
    if original.stat().st_size < 49 * 1024 * 1024:
        return original
    seconds = probe(original)['duration']
    bitrate = max(96, int(45 * 1024 * 1024 * 8 / seconds / 1000) - 96)
    preview = job / 'review.mp4'
    run(['ffmpeg', '-v', 'error', '-y', '-i', str(original),
         '-vf', 'scale=1280:1280:force_original_aspect_ratio=decrease:force_divisible_by=2',
         '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast',
         '-b:v', f'{bitrate}k', '-maxrate', f'{bitrate}k', '-bufsize', f'{bitrate*2}k',
         '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', str(preview)], timeout=900)
    if preview.stat().st_size >= 49 * 1024 * 1024:
        raise ValueError('Telegram preview still exceeds 49 MB; use a smaller input video')
    return preview


class Mp4ReviewBridge:
    def __init__(self, source, root, common_lock, read_state, write_state, send_review):
        self.source = source
        self.root = Path(root)
        self.common_lock = common_lock
        self.read_state, self.write_state = read_state, write_state
        self.send_review = send_review
        self.lock = threading.RLock()
        self.busy = set()

    @staticmethod
    def shared_id(source_id):
        return 'mp4-' + hashlib.sha256(source_id.encode()).hexdigest()[:24]

    def summaries(self):
        result = self.source.list_jobs()
        for item in result:
            shared = self.root / self.shared_id(item['job_id'])
            if shared.is_dir():
                with self.common_lock:
                    state = self.read_state(shared)
                item['shared_job_id'] = shared.name
                item['review_status'] = state['status']
                if state.get('error'):
                    item['review_error'] = state['error']
                if state.get('youtube_video_id'):
                    item['youtube_video_id'] = state['youtube_video_id']
        return result

    def queue(self):
        queued = []
        with self.lock:
            for item in self.source.list_jobs():
                if item['status'] != 'completed' or item['job_id'] in self.busy:
                    continue
                job = self.source.path(item['job_id'])
                target = self.root / self.shared_id(job.name)
                with self.common_lock:
                    if target.exists() and self.read_state(target)['status'] != 'ready_review':
                        continue
                # A failed review requires explicit recovery, not automatic repeated sends.
                if item.get('review_status') == 'failed':
                    continue
                self.busy.add(job.name)
                with self.source.lock:
                    set_state(job, review_status='queued', shared_job_id=target.name)
                threading.Thread(target=self._deliver, args=(job, target), daemon=True,
                                 name='mp4-review-' + job.name).start()
                queued.append(job.name)
        return {'queued_for_review': queued, 'jobs': self.summaries()}

    def _deliver(self, source, target):
        try:
            if signature(source) != source_state(source).get('attempt_signature'):
                raise ValueError('Input/script changed after rendering; use a new video job ID')
            info = probe(source / 'output.mp4')
            script = load_script(source / 'script.json', info['duration'])
            metadata = {'kind': 'mp4_voiceover', 'source_job_id': source.name,
                        'language': 'en-US', 'title': script['title'],
                        'description': str(script.get('description') or '')[:5000]}
            self.root.mkdir(parents=True, exist_ok=True)
            with self.common_lock:
                if not target.exists():
                    with tempfile.TemporaryDirectory(prefix='.mp4-review-', dir=self.root) as temp:
                        stage = Path(temp)
                        try:
                            os.link(source / 'output.mp4', stage / 'output.mp4')
                        except OSError:
                            shutil.copyfile(source / 'output.mp4', stage / 'output.mp4')
                        (stage / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
                        self.write_state(stage, status='ready_review', source_type='mp4_voiceover', source_job_id=source.name)
                        # TemporaryDirectory is 0700; n8n reads this volume as UID 1000.
                        (stage / 'output.mp4').chmod(0o644)
                        stage.chmod(0o755)
                        os.replace(stage, target)
                if self.read_state(target)['status'] != 'ready_review':
                    return
                self.write_state(target, status='review_sending')
            with self.source.render_lock:
                preview = review_proxy(target, target / 'output.mp4')
            # This is the exact same approval sender and nonce handling used by image jobs.
            self.send_review(target, metadata, preview)
            with self.source.lock:
                set_state(source, review_status='pending_review', shared_job_id=target.name)
        except Exception as exc:
            with self.common_lock:
                if target.exists():
                    self.write_state(target, status='failed', phase='telegram_review', error=str(exc))
            with self.source.lock:
                set_state(source, review_status='failed', review_error=str(exc))
        finally:
            with self.lock:
                self.busy.discard(source.name)
