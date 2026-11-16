"""Download Kokoro ONNX and its voice bundle once; synthesis runs locally."""
from pathlib import Path
from urllib.request import urlopen
import shutil
import argparse
import os

project = Path(__file__).resolve().parent
settings = {}
env_file = project / '.env'
if env_file.is_file():
    for line in env_file.read_text(encoding='utf-8-sig').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            settings[key.strip()] = value.strip().strip('\"\'')
configured = os.environ.get('KOKORO_ASSETS_DIR') or settings.get('KOKORO_ASSETS_DIR') or './data/voices'
parser = argparse.ArgumentParser()
parser.add_argument('--directory', type=Path, default=Path(configured))
root = parser.parse_args().directory
if not root.is_absolute():
    root = project / root
root = root.resolve()
print('Kokoro model directory:', root, flush=True)
root.mkdir(parents=True, exist_ok=True)
base = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/'
for name in ('kokoro-v1.0.onnx', 'voices-v1.0.bin'):
    target = root / name
    if target.is_file() and target.stat().st_size > 1000000:
        print('Already exists:', name, flush=True)
        continue
    print('Downloading:', name, flush=True)
    partial = target.with_suffix(target.suffix + '.part')
    try:
        with urlopen(base + name, timeout=120) as response, partial.open('wb') as output:
            shutil.copyfileobj(response, output)
        if partial.stat().st_size < 1000000:
            raise RuntimeError('Incomplete model download: ' + name)
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    print('Saved:', name, target.stat().st_size, 'bytes', flush=True)
print('Kokoro voice: af_heart / American English. Model: https://huggingface.co/hexgrad/Kokoro-82M')
