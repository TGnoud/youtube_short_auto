"""Local MP4 voiceovers from a continuous ChatGPT script, without frame preparation."""
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from subtitles import make_animated_subtitles

ID = re.compile(r'^[A-Za-z0-9_-]{1,36}$')
MAX_DURATION = 600
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
MAX_TEMPO = 1.25
MIN_TEMPO = 0.8


def run(args, timeout=180, cwd=None):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f'{args[0]} exceeded {timeout} seconds') from exc
    if result.returncode:
        raise ValueError(f'{args[0]}: {result.stderr[-1200:]}')
    return result.stdout


def probe(source):
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError('input.mp4 must be a regular file, at most 1 GB')
    info = json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                           '-of', 'json', str(source)], timeout=30))
    streams = [s for s in info['streams'] if s.get('codec_type') == 'video'
               and not s.get('disposition', {}).get('attached_pic')]
    if not streams:
        raise ValueError('input.mp4 contains no video stream')
    stream = streams[0]
    duration = float(info['format'].get('duration', stream.get('duration', 0)))
    if not math.isfinite(duration) or not 1 <= duration <= MAX_DURATION:
        raise ValueError('Video duration must be between 1 and 600 seconds')
    rotation = stream.get('tags', {}).get('rotate', 0)
    for side in stream.get('side_data_list', []):
        if 'rotation' in side:
            rotation = side['rotation']
    rotated = abs(round(float(rotation))) % 180 == 90
    display_width, display_height = ((stream['height'], stream['width']) if rotated
                                    else (stream['width'], stream['height']))
    return {'duration': duration, 'width': stream['width'], 'height': stream['height'],
            'display_width': display_width, 'display_height': display_height,
            'codec': stream['codec_name'], 'video_stream_index': stream['index'],
            'frame_rate': stream.get('avg_frame_rate'),
            'original_audio': any(s.get('codec_type') == 'audio' for s in info['streams'])}


def load_script(path, duration):
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError('script.json must be a regular file smaller than 1 MB')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('language') != 'en-US':
        raise ValueError('script.json requires language = en-US')
    title = data.get('title')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise ValueError('script.json requires a title of 1-100 characters')
    if data.get('mode') == 'continuous' or 'narration' in data:
        text = data.get('narration')
        if not isinstance(text, str) or not 3 <= len(text.strip()) <= 20000:
            raise ValueError('Continuous narration must be one string of 3-20000 characters')
        data['mode'] = 'continuous'
        return data
    segments = data.get('segments')
    if not isinstance(segments, list) or not 1 <= len(segments) <= 120:
        raise ValueError('script.json requires 1-120 timed segments')
    last = 0.0
    for i, segment in enumerate(segments, 1):
        if not isinstance(segment, dict):
            raise ValueError(f'Segment {i} must be an object')
        start, end = segment.get('start'), segment.get('end')
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= duration + 0.001 or start < last):
            raise ValueError(f'Segment {i} timestamps must be ordered, non-overlapping and inside the video')
        if end - start < 0.5:
            raise ValueError(f'Segment {i} needs at least 0.5 seconds')
        text = segment.get('narration')
        if not isinstance(text, str) or not 3 <= len(text.strip()) <= 1000:
            raise ValueError(f'Segment {i} narration must be 3-1000 characters')
        last = end
    return data


def signature(job):
    parts = []
    for name in ('input.mp4', 'script.json'):
        p = job / name
        if p.exists():
            info = p.stat()
            parts.append((name, info.st_size, info.st_mtime_ns))
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def read_state(job):
    p = job / '.voiceover-status.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'status': 'new'}


def set_state(job, **fields):
    state = read_state(job)
    state.update(fields, updated_at=int(time.time()))
    temp = job / '.voiceover-status.tmp'
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, job / '.voiceover-status.json')
    return state


def fit_continuous(audio, rate, target_samples, work):
    """Fit the entire narration together, preserving speech and natural internal pauses."""
    import numpy as np
    import soundfile as sf
    if not len(audio) or not np.any(np.abs(audio) > 0.001):
        raise ValueError('Kokoro returned empty or silent narration')
    natural = len(audio) / rate
    target = target_samples / rate
    tempo = len(audio) / target_samples
    if not MIN_TEMPO <= tempo <= MAX_TEMPO:
        change = 'Expand' if tempo < MIN_TEMPO else 'Shorten'
        raise ValueError(f'{change} the continuous narration: natural speech is {natural:.1f}s '
                         f'for a {target:.1f}s video. Rewrite script.json to fit the whole video.')
    raw = work / 'full_narration.wav'
    fitted = work / 'full_narration_fit.wav'
    sf.write(raw, audio, rate)
    for _ in range(3):
        if not MIN_TEMPO <= tempo <= MAX_TEMPO:
            break
        if abs(tempo - 1) < 0.00001:
            adjusted = audio
        else:
            run(['ffmpeg', '-v', 'error', '-y', '-i', str(raw), '-af', f'atempo={tempo:.9f}',
                 '-ar', str(rate), str(fitted)], timeout=120)
            adjusted, _ = sf.read(fitted, dtype='float32')
        excess = len(adjusted) - target_samples
        if excess > 0:
            if excess <= round(0.1 * rate) and np.max(np.abs(adjusted[target_samples:]), initial=0) <= 0.01:
                return adjusted[:target_samples]
            tempo *= len(adjusted) / target_samples * 1.001
        elif excess >= -round(0.1 * rate):
            return adjusted
        else:
            tempo *= len(adjusted) / target_samples
    raise ValueError('Unable to fit narration smoothly without clipping speech; adjust its length in script.json')


def voiceover(job, info, script, engine, voice, speed, make_srt, split_captions, progress):
    import numpy as np
    import soundfile as sf
    work = job / '_voiceover_work'
    work.mkdir(exist_ok=True)
    sample_rate = 24000
    track = np.zeros(math.ceil(info['duration'] * sample_rate), dtype=np.float32)
    captions = []
    continuous = script.get('mode') == 'continuous'
    if continuous:
        progress(status='narrating', detail='Kokoro: synthesizing one continuous narration')
        audio, rate = engine.create(script['narration'], voice=voice, speed=speed, lang='en-us')
        if rate != sample_rate:
            raise ValueError('Unexpected Kokoro sample rate')
        audio = fit_continuous(audio, rate, len(track), work)
        track[:len(audio)] = audio
        chunks = split_captions(script['narration'])
        weights = [len(chunk.split()) for chunk in chunks]
        cursor = 0.0
        for chunk, weight in zip(chunks, weights):
            end = cursor + len(audio) / rate * weight / sum(weights)
            captions.append((cursor, end, chunk))
            cursor = end
    for i, segment in enumerate([] if continuous else script['segments'], 1):
        progress(status='narrating', detail=f"Kokoro segment {i}/{len(script['segments'])}")
        audio, rate = engine.create(segment['narration'], voice=voice, speed=speed, lang='en-us')
        if rate != sample_rate or len(audio) == 0:
            raise ValueError(f'Invalid Kokoro audio in segment {i}')
        start = round(segment['start'] * rate)
        available = round(segment['end'] * rate) - start
        tempo = len(audio) / available
        if tempo > MAX_TEMPO:
            raise ValueError(f'Segment {i} narration is too long ({len(audio)/rate:.1f}s for '
                             f"{available/rate:.1f}s). Shorten that segment in script.json and run again.")
        wav = work / f'segment_{i:03}.wav'
        sf.write(wav, audio, rate)
        if tempo > 1:
            fitted = work / f'segment_{i:03}_fit.wav'
            run(['ffmpeg', '-v', 'error', '-y', '-i', str(wav), '-af', f'atempo={tempo:.8f}',
                 '-ar', str(rate), str(fitted)], timeout=60)
            audio, rate = sf.read(fitted, dtype='float32')
        if len(audio) > available + round(0.08 * rate):
            raise ValueError(f'Segment {i} still exceeds its slot; shorten its narration')
        # atempo can leave a few milliseconds of residual silence at the boundary.
        if len(audio) > available:
            tail = audio[available:]
            if np.max(np.abs(tail), initial=0) > 0.01:
                raise ValueError(f'Segment {i} would clip audible speech; shorten its narration')
            audio = audio[:available]
        track[start:start+len(audio)] = audio
        chunks = split_captions(segment['narration'])
        weights = [len(chunk.split()) for chunk in chunks]
        cursor = float(segment['start'])
        for chunk, weight in zip(chunks, weights):
            end = cursor + len(audio) / rate * weight / sum(weights)
            captions.append((cursor, end, chunk))
            cursor = end
    voice_file = job / 'narration.wav'
    sf.write(voice_file, track, sample_rate)
    make_srt(captions, job / 'captions.srt')
    make_animated_subtitles(captions, job / 'captions.ass',
                           info.get('display_width', info['width']), info.get('display_height', info['height']))
    progress(status='muxing', detail='Burning animated captions and replacing original audio')
    staged = job / 'output.partial.mp4'
    run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
         '-i', str(job / 'input.mp4'), '-i', str(voice_file),
         '-map', f"0:{info['video_stream_index']}", '-map', '1:a:0',
         '-vf', 'ass=captions.ass,format=yuv420p', '-c:v', 'libx264', '-threads', '2',
         '-preset', 'medium', '-crf', '18', '-c:a', 'aac', '-b:a', '128k',
         '-t', f"{info['duration']:.6f}", '-map_metadata', '0', '-movflags', '+faststart', str(staged)],
        timeout=900, cwd=job)
    result = probe(staged)
    if abs(result['duration'] - info['duration']) > 0.15:
        raise ValueError('Output duration differs from the input; output not published')
    os.replace(staged, job / 'output.mp4')


class VideoJobs:
    def __init__(self, root, render_lock, engine_factory, voice, speed,
                 make_srt, split_captions):
        self.root = Path(root)
        self.render_lock = render_lock
        self.engine_factory = engine_factory
        self.voice, self.speed = voice, speed
        self.make_srt, self.split_captions = make_srt, split_captions
        self.lock = threading.RLock()
        self.active = set()
        self.on_complete = None

    def path(self, job_id):
        if not ID.fullmatch(job_id):
            raise ValueError('Invalid video job ID')
        job = (self.root / job_id).resolve()
        if job.parent != self.root.resolve() or not job.is_dir():
            raise ValueError('Video job does not exist')
        return job

    def list_jobs(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock:
            return [{'job_id': p.name, **read_state(p),
                     'folder': str(p), 'script_file': str(p / 'script.json')}
                    for p in sorted(self.root.iterdir())
                    if p.is_dir() and not p.is_symlink() and ID.fullmatch(p.name)]

    def scan(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock:
            for summary in self.list_jobs():
                job = self.path(summary['job_id'])
                if not (job / 'input.mp4').is_file() or job.name in self.active:
                    continue
                state = read_state(job)
                current = signature(job)
                if state['status'] == 'completed':
                    continue
                if not (job / 'script.json').is_file():
                    set_state(job, status='awaiting_script', detail='Save script.json from ChatGPT beside input.mp4; no frame or metadata files are needed',
                              attempt_signature=current, error=None)
                    continue
                if state['status'] == 'failed' and current == state.get('attempt_signature'):
                    continue
                self.active.add(job.name)
                set_state(job, status='queued', detail='Waiting for local worker', error=None,
                          attempt_signature=current)
                threading.Thread(target=self._work, args=(job, current), daemon=True,
                                 name='mp4-' + job.name).start()
            return {'jobs': self.list_jobs()}

    def _work(self, job, initial_signature):
        def progress(**fields):
            with self.lock:
                set_state(job, **fields)
        try:
            with self.render_lock:
                if signature(job) != initial_signature:
                    raise ValueError('Input or script changed while queued; run again after copying finishes')
                progress(status='preparing', detail='Checking video duration')
                info = probe(job / 'input.mp4')
                if not (job / 'script.json').is_file():
                    progress(status='awaiting_script', detail='Save script.json from ChatGPT beside input.mp4; no frame or metadata files are needed')
                    return
                script = load_script(job / 'script.json', info['duration'])
                if not 0.5 <= self.speed <= 2:
                    raise ValueError('KOKORO_SPEED must be between 0.5 and 2.0')
                voiceover(job, info, script, self.engine_factory(), self.voice, self.speed,
                          self.make_srt, self.split_captions, progress)
                if signature(job) != initial_signature:
                    raise ValueError('Input or script changed while processing; run again after copying finishes')
                progress(status='completed', detail='Voiceover ready', title=script['title'],
                         output_file=str(job / 'output.mp4'), duration=info['duration'])
                if self.on_complete:
                    try:
                        self.on_complete()
                    except Exception as exc:
                        progress(review_status='failed', review_error=str(exc))
        except Exception as exc:
            progress(status='failed', error=str(exc), detail='Fix input.mp4 or script.json, then run the workflow again')
        finally:
            with self.lock:
                self.active.discard(job.name)
