import base64
import json
import os
import stat
import struct
import socket
import tempfile
import threading
import time
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from app import read_manifest
from zip_jobs import import_inbox, import_zip

def png_chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


PNG = (b'\x89PNG\r\n\x1a\n'
       + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
       + png_chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff'))
       + png_chunk(b'IEND', b''))


class ZipImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.jobs = self.root / 'jobs'
        self.inbox = self.root / 'inbox'
        self.inbox.mkdir()
        self.manifest = {'language': 'en-US', 'title': 'Test', 'scenes': [
            {'image': 'scene1.png', 'narration': 'First scene.'},
            {'image': 'scene2.png', 'narration': 'Second scene.'},
            {'image': 'scene3.png', 'narration': 'Third scene.'},
        ]}

    def make_zip(self, prefix='', extras=None, omit=None):
        p = self.inbox / 'short.zip'
        files = {'manifest.json': json.dumps(self.manifest)}
        files.update({scene['image']: PNG for scene in self.manifest['scenes']})
        for name in omit or []:
            files.pop(name)
        with zipfile.ZipFile(p, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, value in files.items():
                z.writestr(prefix + name, value)
            for name, value in extras or []:
                z.writestr(name, value)
        return p

    def test_import_root_and_duplicate_preserves_status(self):
        p = self.make_zip()
        first = import_zip(p, self.jobs, read_manifest)
        job = self.jobs / first['job_id']
        self.assertTrue((job / 'READY').is_file())
        self.assertEqual(job.stat().st_mode & 0o777, 0o755)
        self.assertTrue(all(p.stat().st_mode & 0o004 for p in job.iterdir()))
        self.assertEqual(read_manifest(job)['title'], 'Test')
        (job / '.workflow-status.json').write_text('{"status":"published"}')
        self.assertTrue(import_zip(p, self.jobs, read_manifest)['duplicate'])
        self.assertEqual(json.loads((job / '.workflow-status.json').read_text())['status'], 'published')

    def test_single_folder(self):
        result = import_zip(self.make_zip('my-short/'), self.jobs, read_manifest)
        self.assertTrue((self.jobs / result['job_id'] / 'scene1.png').is_file())

    def test_variable_image_counts(self):
        for count in (3, 4, 6, 10):
            with self.subTest(count=count):
                self.manifest['scenes'] = [
                    {'image': f'scene{i}.png', 'narration': f'Scene number {i}.'}
                    for i in range(1, count + 1)
                ]
                result = import_zip(self.make_zip(), self.jobs, read_manifest)
                job = self.jobs / result['job_id']
                self.assertEqual(len(read_manifest(job)['scenes']), count)
                self.assertEqual(len(list(job.glob('*.png'))), count)

    def test_minimum_three_distinct_images(self):
        self.manifest['scenes'] = self.manifest['scenes'][:2]
        with self.assertRaises(ValueError):
            import_zip(self.make_zip(), self.jobs, read_manifest)
        self.manifest['scenes'].append(dict(self.manifest['scenes'][0]))
        with self.assertRaises(ValueError):
            import_zip(self.make_zip(), self.jobs, read_manifest)
        self.assertEqual(list(self.jobs.iterdir()), [])

    def test_unsafe_paths_and_status_file(self):
        for name in ('../escape.txt', '/escape.txt', 'C:/escape.txt', 'bad\\file.png', '.workflow-status.json'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    import_zip(self.make_zip(extras=[(name, 'bad')]), self.jobs, read_manifest)
        self.assertFalse((self.root / 'escape.txt').exists())

    def test_symlink(self):
        p = self.make_zip()
        with zipfile.ZipFile(p, 'a') as z:
            info = zipfile.ZipInfo('linked.png')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, '/etc/passwd')
        with self.assertRaises(ValueError):
            import_zip(p, self.jobs, read_manifest)

    def test_missing_image_and_invalid_manifest(self):
        with self.assertRaises(ValueError):
            import_zip(self.make_zip(omit=['scene2.png']), self.jobs, read_manifest)
        self.manifest['language'] = 'vi-VN'
        with self.assertRaises(ValueError):
            import_zip(self.make_zip(), self.jobs, read_manifest)
        self.assertEqual(list(self.jobs.iterdir()), [])

    def test_limits(self):
        p = self.make_zip()
        with patch('zip_jobs.MAX_EXPANDED_BYTES', 10):
            with self.assertRaises(ValueError):
                import_zip(p, self.jobs, read_manifest)

    def test_case_collisions(self):
        with self.assertRaises(ValueError):
            import_zip(self.make_zip(extras=[('Scene1.png', PNG)]), self.jobs, read_manifest)

    def test_corrupt_png_is_rejected_before_job_creation(self):
        for bad in (PNG[:-4], PNG[:-12], PNG[:45] + bytes([PNG[45] ^ 1]) + PNG[46:]):
            with self.subTest(size=len(bad)):
                p = self.inbox / 'corrupt.zip'
                with zipfile.ZipFile(p, 'w') as z:
                    z.writestr('manifest.json', json.dumps(self.manifest))
                    z.writestr('scene1.png', PNG)
                    z.writestr('scene2.png', bad)
                    z.writestr('scene3.png', PNG)
                with self.assertRaisesRegex(ValueError, 'scene2.png'):
                    import_zip(p, self.jobs, read_manifest)
        self.assertEqual(list(self.jobs.iterdir()), [])

    def test_http_import_then_claim_and_duplicate(self):
        import app
        import requests
        import uvicorn
        archive = self.make_zip()
        original = archive.read_bytes()
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app.app, log_level='error', access_log=False))
        with patch.multiple(app, ROOT=self.jobs, INBOX=self.inbox, poll_telegram=lambda: None):
            thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    if server.started:
                        break
                    time.sleep(0.05)
                self.assertTrue(server.started)
                base = f'http://127.0.0.1:{port}'
                response = requests.post(base + '/jobs/import-zips', timeout=5)
                self.assertEqual(response.status_code, 200)
                job_id = response.json()['imported'][0]
                claimed = requests.get(base + '/jobs/next', timeout=5).json()
                self.assertEqual(claimed['job_id'], job_id)
                archive.write_bytes(original)
                report = requests.post(base + '/jobs/import-zips', timeout=5).json()
                self.assertEqual(report['duplicates'], [job_id])
                state = json.loads((self.jobs / job_id / '.workflow-status.json').read_text())
                self.assertEqual(state['status'], 'claimed')
            finally:
                server.should_exit = True
                thread.join(timeout=5)
                sock.close()

    def test_inbox_moves_success_and_quarantines_errors(self):
        self.make_zip()
        report = import_inbox(self.inbox, self.jobs, read_manifest)
        self.assertEqual(len(report['imported']), 1)
        self.assertEqual(len(list((self.inbox / 'processed').glob('*.zip'))), 1)
        self.make_zip(omit=['scene2.png'])
        report = import_inbox(self.inbox, self.jobs, read_manifest)
        self.assertEqual(len(report['failed']), 1)
        self.assertEqual(len(list((self.inbox / 'failed').glob('*.error.txt'))), 1)

    def test_partial_copy_is_deferred_then_quarantined(self):
        p = self.inbox / 'copying.zip'
        p.write_bytes(b'PK-not-complete')
        self.assertEqual(import_inbox(self.inbox, self.jobs, read_manifest)['deferred'], ['copying.zip'])
        os.utime(p, (1, 1))
        self.assertEqual(len(import_inbox(self.inbox, self.jobs, read_manifest)['failed']), 1)


if __name__ == '__main__':
    unittest.main()
