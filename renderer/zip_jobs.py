"""Import local ZIP packages without exposing archive paths to the filesystem."""
import hashlib
import json
import os
import shutil
import stat
import struct
import tempfile
import time
import zipfile
import zlib
from pathlib import Path, PurePosixPath

MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_ENTRIES = 256


def validate_image(path: Path):
    """Reject truncated/corrupt PNGs before FFmpeg's looping image input."""
    with path.open('rb') as stream:
        header = stream.read(8)
        if path.suffix.lower() != '.png':
            if not header.startswith(b'\xff\xd8\xff'):
                raise ValueError(f'Invalid JPEG image: {path.name}')
            return
        if header != b'\x89PNG\r\n\x1a\n':
            raise ValueError(f'Invalid PNG image: {path.name}')
        size = path.stat().st_size
        first = True
        has_data = False
        while stream.tell() < size:
            chunk_header = stream.read(8)
            if len(chunk_header) != 8:
                break
            length, kind = struct.unpack('>I4s', chunk_header)
            if length > size - stream.tell() - 4:
                raise ValueError(f'Corrupt/truncated PNG: {path.name}; chunk exceeds file size. Replace this image and create a new ZIP.')
            if first and (kind != b'IHDR' or length != 13):
                raise ValueError(f'Invalid PNG header: {path.name}')
            first = False
            data = stream.read(length)
            expected = struct.unpack('>I', stream.read(4))[0]
            if zlib.crc32(data, zlib.crc32(kind)) & 0xffffffff != expected:
                raise ValueError(f'Corrupt PNG checksum: {path.name}. Replace this image and create a new ZIP.')
            if kind == b'IDAT':
                has_data = True
            if kind == b'IEND':
                if length != 0 or not has_data:
                    raise ValueError(f'Invalid PNG image data: {path.name}')
                return
        raise ValueError(f'Truncated PNG (missing IEND): {path.name}. Replace this image and create a new ZIP.')


def import_zip(archive: Path, jobs_root: Path, validate_manifest) -> dict:
    """Validate, stage, then atomically publish one job. Never overwrite jobs."""
    before = archive.stat()
    if archive.is_symlink() or before.st_size > MAX_ZIP_BYTES:
        raise ValueError('ZIP must be a regular file no larger than 100 MB')
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    job_id = 'zip-' + digest[:24]
    target = jobs_root / job_id
    if target.exists():
        receipt = target / '.zip-source.json'
        if receipt.is_file() and json.loads(receipt.read_text())['sha256'] == digest:
            return {'job_id': job_id, 'duplicate': True}
        raise ValueError('Job ID already exists; refusing to replace it')
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if not entries or len(entries) > MAX_ENTRIES:
            raise ValueError(f'ZIP must contain at most {MAX_ENTRIES} entries')
        if sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
            raise ValueError('Uncompressed ZIP content exceeds 200 MB')
        files = {}
        seen_names = set()
        for entry in entries:
            raw = entry.filename
            parts = raw.rstrip('/').split('/')
            mode = entry.external_attr >> 16
            if ('\\' in raw or ':' in raw or raw.startswith('/')
                    or any(p in ('', '.', '..') for p in parts)
                    or stat.S_ISLNK(mode) or entry.flag_bits & 1):
                raise ValueError('ZIP contains an unsafe, linked or encrypted path')
            if entry.is_dir():
                continue
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise ValueError('ZIP contains a non-regular file')
            if raw.casefold() in seen_names:
                raise ValueError('ZIP contains duplicate filenames')
            seen_names.add(raw.casefold())
            files[raw] = entry
        manifests = [name for name in files if PurePosixPath(name).name == 'manifest.json']
        if len(manifests) != 1:
            raise ValueError('ZIP must contain exactly one manifest.json')
        manifest_path = PurePosixPath(manifests[0])
        if len(manifest_path.parts) > 2:
            raise ValueError('Use ZIP root or a single enclosing folder')
        prefix = '' if manifest_path.parent == PurePosixPath('.') else str(manifest_path.parent) + '/'
        normalized = {}
        for name, entry in files.items():
            if not name.startswith(prefix) or '/' in name[len(prefix):]:
                raise ValueError('All package files must be beside manifest.json')
            normalized[name[len(prefix):]] = entry
        manifest_entry = normalized['manifest.json']
        if manifest_entry.file_size > 1024 * 1024:
            raise ValueError('manifest.json exceeds 1 MB')
        manifest = json.loads(package.read(manifest_entry).decode('utf-8-sig'))
        if not isinstance(manifest, dict) or not isinstance(manifest.get('scenes'), list):
            raise ValueError('manifest.json must contain a scenes array')
        images = set()
        for scene in manifest['scenes']:
            name = scene.get('image') if isinstance(scene, dict) else None
            if not isinstance(name, str) or '/' in name or '\\' in name or name in ('.', '..'):
                raise ValueError('Invalid scene image filename')
            images.add(name)
        if set(normalized) - ({'manifest.json', 'READY'} | images):
            raise ValueError('ZIP may contain only manifest.json, scene images and optional READY')
        if not images.issubset(normalized):
            raise ValueError('ZIP is missing one or more scene images')
        jobs_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.zip-import-', dir=jobs_root) as temp:
            stage = Path(temp)
            for name in {'manifest.json'} | images:
                with package.open(normalized[name]) as src, (stage / name).open('wb') as dst:
                    shutil.copyfileobj(src, dst)
            validate_manifest(stage)
            for name in images:
                validate_image(stage / name)
            after = archive.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('ZIP changed while importing; copy it completely before retrying')
            (stage / '.zip-source.json').write_text(json.dumps({
                'sha256': digest, 'filename': archive.name, 'imported_at': int(time.time()),
            }), encoding='utf-8')
            (stage / 'READY').touch()
            # Keep the published media traversable by the non-root n8n container.
            for child in stage.iterdir():
                child.chmod(0o644)
            stage.chmod(0o755)
            os.replace(stage, target)
    return {'job_id': job_id, 'duplicate': False}


def import_inbox(inbox: Path, jobs_root: Path, validate_manifest) -> dict:
    inbox.mkdir(parents=True, exist_ok=True)
    result = {'imported': [], 'duplicates': [], 'failed': [], 'deferred': []}
    for archive in sorted(inbox.iterdir()):
        if archive.suffix.lower() != '.zip' or not archive.is_file():
            continue
        try:
            job = import_zip(archive, jobs_root, validate_manifest)
            processed = inbox / 'processed'
            processed.mkdir(exist_ok=True)
            os.replace(archive, processed / (job['job_id'] + '-' + archive.name))
            result['duplicates' if job['duplicate'] else 'imported'].append(job['job_id'])
        except Exception as exc:
            # An incomplete copy often has no central directory yet; allow it to finish.
            if isinstance(exc, (zipfile.BadZipFile, EOFError)) and time.time() - archive.stat().st_mtime < 60:
                result['deferred'].append(archive.name)
                continue
            failed = inbox / 'failed'
            failed.mkdir(exist_ok=True)
            failed_path = failed / (str(time.time_ns()) + '-' + archive.name)
            os.replace(archive, failed_path)
            failed_path.with_suffix('.zip.error.txt').write_text(str(exc), encoding='utf-8')
            result['failed'].append({'file': archive.name, 'error': str(exc)})
    return result
