"""Private local rendering & Telegram-approval worker for a self-hosted n8n workflow.

This container publishes NO host port. n8n calls its endpoints over Docker's private network.
Only the configured Telegram user may approve a job. All jobs use local files.
"""
from __future__ import annotations

import json
import math
import os
import re
import secrets
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from functools import lru_cache

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from zip_jobs import validate_image

ROOT = Path(os.getenv('JOBS_DIR', '/files/jobs')).resolve()
INBOX = Path(os.getenv('ZIP_INBOX_DIR', '/files/inbox')).resolve()
MODEL = Path(os.getenv('KOKORO_MODEL', '/files/voices/kokoro-v1.0.onnx'))
VOICES = Path(os.getenv('KOKORO_VOICES', '/files/voices/voices-v1.0.bin'))
VOICE = os.getenv('KOKORO_VOICE', 'af_heart')
SPEED = float(os.getenv('KOKORO_SPEED', '1.0'))
FPS = 25
TRANSITION = 0.4
RENDER_LOCK = threading.Lock()
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '').strip()
APPROVER_ID = os.getenv('TELEGRAM_APPROVER_USER_ID', '').strip()
LOCK = threading.RLock()
JOB_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,36}$')


def safe_job(job_id: str) -> Path:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError('job_id chỉ được gồm chữ, số, dấu gạch ngang/gạch dưới, tối đa 36 ký tự')
    directory = (ROOT / job_id).resolve()
    if directory.parent != ROOT:
        raise ValueError('Đường dẫn tác vụ không hợp lệ')
    return directory


def status_file(job: Path) -> Path:
    return job / '.workflow-status.json'


def read_state(job: Path) -> dict:
    path = status_file(job)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'status': 'new'}


def write_state(job: Path, **updates):
    previous = read_state(job)
    previous.update(updates)
    previous['updated_at'] = int(time.time())
    path = status_file(job)
    tmp = job / ('.state-' + secrets.token_hex(4) + '.tmp')
    tmp.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)
    return previous


def read_manifest(job: Path) -> dict:
    p = job / 'manifest.json'
    if not p.is_file():
        raise ValueError('Thiếu manifest.json')
    m = json.loads(p.read_text(encoding='utf-8-sig'))
    if not isinstance(m, dict) or not isinstance(m.get('title'), str) or not m['title'].strip():
        raise ValueError('Thiếu title')
    if m.get('language') != 'en-US':
        raise ValueError('manifest.json requires language = en-US (English).')
    scenes = m.get('scenes')
    if not isinstance(scenes, list) or len(scenes) < 3:
        raise ValueError('scenes phải có ít nhất 3 cảnh; mỗi cảnh có một ảnh')
    if len(m['title']) > 100:
        raise ValueError('YouTube title tối đa 100 ký tự')
    seen_images = set()
    for idx, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise ValueError(f'Cảnh {idx} không phải object')
        text = scene.get('narration')
        if not isinstance(text, str) or not (3 <= len(text.strip()) <= 650):
            raise ValueError(f'Cảnh {idx} narration phải dài 3-650 ký tự')
        name = scene.get('image', '')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.(png|jpg|jpeg)', name, re.I):
            raise ValueError(f'Cảnh {idx} tên file ảnh không hợp lệ')
        if name.casefold() in seen_images:
            raise ValueError(f'Cảnh {idx} phải dùng một ảnh riêng, không lặp tên file ảnh')
        seen_images.add(name.casefold())
        img = (job / name).resolve()
        if img.parent != job or not img.is_file():
            raise ValueError(f'Cảnh {idx} không tìm thấy ảnh {name}')
        validate_image(img)
    m['description'] = str(m.get('description') or '')[:5000]
    return m


def read_upload_metadata(job: Path) -> dict:
    metadata = json.loads((job / 'manifest.json').read_text(encoding='utf-8-sig'))
    if metadata.get('kind') != 'mp4_voiceover':
        return read_manifest(job)
    title = metadata.get('title')
    if metadata.get('language') != 'en-US' or not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise ValueError('Invalid MP4 upload metadata')
    metadata['description'] = str(metadata.get('description') or '')[:5000]
    return metadata


def cmd(args: list[str], cwd: Path | None = None, input_text: str | None = None) -> str:
    p = subprocess.run(args, cwd=cwd, text=True, input=input_text, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f'Chạy lệnh lỗi {args[0]}: {p.stderr[-1400:]}')
    return p.stdout


def duration(path: Path) -> float:
    output = cmd(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(path)])
    return float(output.strip())


def srt_clock(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def caption_segments(text: str, max_words: int = 4) -> list[str]:
    # Approximate timing: this is not speech-to-text word alignment.
    parts = re.split(r'(?<=[.!?…])\s+', text.strip())
    result = []
    for part in parts:
        words = part.split()
        for pos in range(0, len(words), max_words):
            result.append(' '.join(words[pos:pos + max_words]))
    return [s for s in result if s]


def make_subtitles(lines: list[tuple[float, float, str]], target: Path):
    sections = []
    for idx, (start, end, text) in enumerate(lines, 1):
        sections.append(f'{idx}\n{srt_clock(start)} --> {srt_clock(end)}\n{text}\n')
    target.write_text('\n'.join(sections), encoding='utf-8')


@lru_cache(maxsize=1)
def tts_engine():
    from kokoro_onnx import Kokoro
    if not MODEL.is_file() or not VOICES.is_file():
        raise RuntimeError('Missing Kokoro files. Run python download-voice.py first.')
    return Kokoro(str(MODEL), str(VOICES))


from subtitles import make_animated_subtitles


def render_video(job: Path, manifest: dict) -> Path:
    # One render at a time bounds CPU/RAM use and serializes the cached TTS model.
    with RENDER_LOCK:
        return _render_video(job, manifest)


def _render_video(job: Path, manifest: dict) -> Path:
    import soundfile as sf
    engine = tts_engine()
    if not 0.5 <= SPEED <= 2.0:
        raise ValueError('KOKORO_SPEED must be between 0.5 and 2.0')
    out = job / 'output.mp4'
    work = job / '_work'
    work.mkdir(exist_ok=True)
    captions, lengths = [], []
    cursor = 0.0
    scenes = manifest['scenes']
    for idx, scene in enumerate(scenes, 1):
        print(f'Kokoro: scene {idx}/{len(scenes)}', flush=True)
        wav = work / f'voice_{idx:02d}.wav'
        samples, rate = engine.create(scene['narration'], voice=VOICE, speed=SPEED, lang='en-us')
        sf.write(str(wav), samples, rate)
        seconds = len(samples) / rate
        if seconds <= 0:
            raise RuntimeError(f'Scene {idx} speech is too short')
        scene_length = math.ceil(seconds * FPS) / FPS
        lengths.append(scene_length)
        snippet = caption_segments(scene['narration'])
        weights = [max(1, len(s.split())) for s in snippet]
        t = cursor
        for j, (sub, weight) in enumerate(zip(snippet, weights)):
            end = cursor + seconds if j == len(snippet) - 1 else t + seconds * weight / sum(weights)
            captions.append((t, end, sub))
            t = end
        cursor += scene_length
        # Hold each still through the overlap; narration remains sequential.
        frames = round((scene_length + (TRANSITION if idx < len(scenes) else 0)) * FPS)
        cmd([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-loop', '1', '-framerate', str(FPS), '-i', str(job / scene['image']),
            '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1,format=yuv420p',
            '-frames:v', str(frames), '-an', '-c:v', 'libx264', '-threads', '2',
            '-preset', 'medium', '-crf', '18', '-movflags', '+faststart',
            str(work / f'scene_{idx:02d}.mp4'),
        ])
    make_subtitles(captions, work / 'captions.srt')
    make_animated_subtitles(captions, work / 'captions.ass')
    return assemble_video(work, lengths, out)


def assemble_video(work: Path, lengths: list[float], out: Path) -> Path:
    args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-filter_complex_threads', '1']
    for idx in range(1, len(lengths) + 1):
        args += ['-i', f'scene_{idx:02d}.mp4']
    for idx in range(1, len(lengths) + 1):
        args += ['-i', f'voice_{idx:02d}.wav']
    graph = [f'[{i}:v]settb=1/{FPS},setpts=PTS-STARTPTS,fps={FPS}[v{i}]' for i in range(len(lengths))]
    previous, offset = 'v0', 0.0
    transitions = ['fade', 'smoothleft', 'smoothup']
    for i in range(1, len(lengths)):
        offset += lengths[i - 1]
        graph.append(f'[{previous}][v{i}]xfade=transition={transitions[(i-1) % 3]}:duration={TRANSITION}:offset={offset:.6f},fps={FPS}[mix{i}]')
        previous = f'mix{i}'
    graph.append(f'[{previous}]ass=captions.ass,format=yuv420p[video]')
    for i, seconds in enumerate(lengths):
        graph.append(f'[{len(lengths)+i}:a]aresample=48000,apad,atrim=duration={seconds:.6f},asetpts=PTS-STARTPTS[a{i}]')
    graph.append(''.join(f'[a{i}]' for i in range(len(lengths))) + f'concat=n={len(lengths)}:v=0:a=1[audio]')
    print('FFmpeg: static images, transitions and animated captions', flush=True)
    cmd(args + [
        '-filter_complex', ';'.join(graph), '-map', '[video]', '-map', '[audio]',
        '-t', f'{sum(lengths):.6f}', '-r', str(FPS),
        '-c:v', 'libx264', '-threads', '2', '-preset', 'medium', '-crf', '18',
        '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(out),
    ], cwd=work)
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError('Kh?ng t?o ???c output.mp4')
    if out.stat().st_size >= 49 * 1024 * 1024:
        raise RuntimeError('Video l?n h?n 49MB, c?n gi?m bitrate/k?ch th??c tr??c khi g?i Telegram')
    return out


def telegram(method: str, data: dict | None = None, files: dict | None = None, timeout: int = 60) -> dict:
    if not BOT_TOKEN or not CHAT_ID or not APPROVER_ID or BOT_TOKEN.startswith('REPLACE_'):
        raise RuntimeError('Thiếu TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID hoặc TELEGRAM_APPROVER_USER_ID')
    endpoint = f'https://api.telegram.org/bot{BOT_TOKEN}/{method}'
    response = requests.post(endpoint, data=data or {}, files=files, timeout=timeout)
    response.raise_for_status()
    result = response.json()
    if not result.get('ok'):
        raise RuntimeError(f'Telegram error: {result.get("description", "unknown")}')
    return result


def send_review(job: Path, manifest: dict, video: Path):
    nonce = secrets.token_hex(4)
    job_id = job.name
    buttons = {'inline_keyboard': [[
        {'text': '✅ Approve & upload', 'callback_data': f'ok|{job_id}|{nonce}'},
        {'text': '❌ Reject', 'callback_data': f'no|{job_id}|{nonce}'},
    ]]}
    caption = f'🎬 Review English Short: {manifest["title"][:180]}\n\nOnly the authorized approver can publish this video.'
    with video.open('rb') as f:
        result = telegram('sendVideo', data={
            'chat_id': CHAT_ID, 'caption': caption,
            'reply_markup': json.dumps(buttons, ensure_ascii=False),
            'supports_streaming': 'true',
        }, files={'video': (video.name, f, 'video/mp4')}, timeout=180)
    write_state(job, status='pending_review', nonce=nonce,
                message_id=result.get('result', {}).get('message_id'))


def process_callback(update: dict):
    cb = update.get('callback_query')
    if not isinstance(cb, dict):
        return
    from_id = str(cb.get('from', {}).get('id', ''))
    msg = cb.get('message') or {}
    chat_id = str(msg.get('chat', {}).get('id', ''))
    raw = cb.get('data', '')
    parts = raw.split('|')
    valid = False
    if len(parts) == 3 and from_id == APPROVER_ID and chat_id == CHAT_ID:
        decision, job_id, nonce = parts
        try:
            with LOCK:
                job = safe_job(job_id)
                state = read_state(job)
                if state.get('status') == 'pending_review' and nonce == state.get('nonce') and decision in ('ok', 'no'):
                    write_state(job, status='approved' if decision == 'ok' else 'rejected', decided_by=from_id)
                    valid = True
        except ValueError:
            pass
    if valid:
        try:
            telegram('editMessageReplyMarkup', data={
                'chat_id': CHAT_ID, 'message_id': msg['message_id'],
                'reply_markup': json.dumps({'inline_keyboard': []}),
            })
        except Exception as exc:
            print('Could not clear review buttons:', exc, flush=True)
    try:
        telegram('answerCallbackQuery', data={
            'callback_query_id': cb['id'],
            'text': ('Approved. n8n will upload.' if parts[0] == 'ok' else 'Rejected.') if valid else 'Unauthorized or this review is no longer active.',
            'show_alert': 'false',
        })
    except Exception as exc:
        print('Telegram callback answer error:', exc, flush=True)


def poll_telegram():
    offset_file = ROOT.parent / '.telegram-offset.json'
    offset = 0
    if offset_file.exists():
        try:
            offset = int(json.loads(offset_file.read_text(encoding='utf-8')).get('offset', 0))
        except Exception:
            pass
    while True:
        try:
            if not BOT_TOKEN or BOT_TOKEN.startswith('REPLACE_'):
                time.sleep(15)
                continue
            data = telegram('getUpdates', data={
                'offset': str(offset), 'timeout': '25', 'allowed_updates': '["callback_query"]'
            }, timeout=40)['result']
            for update in data:
                try:
                    process_callback(update)
                except Exception as exc:
                    print('Callback processing error:', exc, flush=True)
                offset = max(offset, update['update_id'] + 1)
                tmp = offset_file.with_suffix('.tmp')
                tmp.write_text(json.dumps({'offset': offset}), encoding='utf-8')
                os.replace(tmp, offset_file)
        except Exception as exc:
            print('Telegram polling error:', exc, flush=True)
            time.sleep(5)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=poll_telegram, daemon=True, name='telegram-poller').start()
    yield


app = FastAPI(title='Free local Shorts renderer & Telegram reviewer', lifespan=lifespan)

from video_jobs import VideoJobs

VIDEO_JOBS = VideoJobs(
    os.getenv('VIDEO_JOBS_DIR', '/files/video_jobs'),
    RENDER_LOCK,
    tts_engine, VOICE, SPEED, make_subtitles, caption_segments,
)
from review_bridge import Mp4ReviewBridge

MP4_REVIEWS = Mp4ReviewBridge(VIDEO_JOBS, ROOT, LOCK, read_state, write_state, send_review)
VIDEO_JOBS.on_complete = MP4_REVIEWS.queue


@app.post('/video-jobs/queue-reviews')
def queue_video_reviews():
    return MP4_REVIEWS.queue()


@app.post('/video-jobs/scan')
def scan_video_jobs():
    return VIDEO_JOBS.scan()


@app.get('/video-jobs')
def list_video_jobs():
    return {'jobs': MP4_REVIEWS.summaries()}


@app.get('/video-jobs/{job_id}')
def get_video_job(job_id: str):
    try:
        job = VIDEO_JOBS.path(job_id)
        from video_jobs import read_state as video_state
        return {'job_id': job_id, **video_state(job), 'folder': str(job)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class JobRequest(BaseModel):
    job_id: str


class FinalRequest(JobRequest):
    video_id: str


@app.get('/health')
def health():
    return {'ok': True, 'tts': 'kokoro', 'voice': VOICE, 'voice_installed': MODEL.is_file() and VOICES.is_file(), 'folder': str(ROOT)}


@app.get('/jobs/next')
def next_job():
    with LOCK:
        for job in sorted(ROOT.iterdir()):
            if not job.is_dir() or not JOB_ID_PATTERN.fullmatch(job.name):
                continue
            if not (job / 'READY').is_file() or not (job / 'manifest.json').is_file():
                continue
            if read_state(job).get('status') != 'new':
                continue
            try:
                manifest = read_manifest(job)
            except Exception as exc:
                write_state(job, status='failed', error=str(exc))
                continue
            write_state(job, status='claimed')
            return {'has_job': True, 'job_id': job.name, 'title': manifest['title']}
    return {'has_job': False}


@app.post('/jobs/import-zips')
def import_zip_packages():
    from zip_jobs import import_inbox
    with LOCK:
        return import_inbox(INBOX, ROOT, read_manifest)


@app.post('/jobs/render')
def render(req: JobRequest):
    try:
        with LOCK:
            job = safe_job(req.job_id)
            if read_state(job).get('status') != 'claimed':
                raise HTTPException(409, 'Job chưa được claim hoặc đã xử lý')
            write_state(job, status='rendering')
        manifest = read_manifest(job)
        video = render_video(job, manifest)
        send_review(job, manifest, video)
        return {'status': 'pending_review', 'job_id': job.name, 'video': str(video)}
    except HTTPException:
        raise
    except Exception as exc:
        if JOB_ID_PATTERN.fullmatch(req.job_id):
            with LOCK:
                write_state(safe_job(req.job_id), status='failed', error=str(exc))
        raise HTTPException(500, str(exc)) from exc


@app.get('/jobs/approved-next')
def approved_next():
    with LOCK:
        for job in sorted(ROOT.iterdir()):
            if not job.is_dir() or not JOB_ID_PATTERN.fullmatch(job.name):
                continue
            if read_state(job).get('status') != 'approved':
                continue
            video = job / 'output.mp4'
            if not video.is_file():
                write_state(job, status='failed', error='Không tìm thấy output.mp4')
                continue
            manifest = read_upload_metadata(job)
            write_state(job, status='uploading')
            return {'has_job': True, 'job_id': job.name,
                    'title': manifest['title'], 'description': manifest['description'],
                    'video_file': f'/files/jobs/{job.name}/output.mp4'}
    return {'has_job': False}


@app.post('/jobs/finalize')
def finalize(req: FinalRequest):
    with LOCK:
        job = safe_job(req.job_id)
        if read_state(job).get('status') != 'uploading':
            raise HTTPException(409, 'Job không ở trạng thái uploading')
        if not req.video_id.strip():
            raise HTTPException(400, 'video_id không hợp lệ')
        write_state(job, status='published', youtube_video_id=req.video_id.strip())
        return {'status': 'published', 'youtube_url': f'https://www.youtube.com/watch?v={req.video_id.strip()}'}
