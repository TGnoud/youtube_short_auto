"""Run BEFORE starting docker compose. Send /start to your new Telegram bot first."""
from pathlib import Path
import json
from urllib.request import urlopen

env_file=Path(__file__).parent/'.env'
if not env_file.is_file():
    raise SystemExit('Tạo file .env từ .env.example trước khi chạy')
for line in env_file.read_text(encoding='utf-8').splitlines():
    if line.strip().startswith('TELEGRAM_BOT_TOKEN='):
        token=line.split('=',1)[1].strip()
        break
else:
    raise SystemExit('Không có token Telegram trong .env')
if not token or token.startswith('REPLACE'):
    raise SystemExit('Hãy điền TELEGRAM_BOT_TOKEN trong .env')
with urlopen(f'https://api.telegram.org/bot{token}/getUpdates',timeout=15) as response:
    data=json.load(response)
if not data.get('ok'):
    raise SystemExit(str(data))
for update in data.get('result',[]):
    msg=update.get('message') or {}
    if msg.get('chat',{}).get('type')=='private':
        print('Chat ID:',msg['chat']['id'],'| Approver user ID:',msg['from']['id'])
        break
else:
    print('Chưa có tin nhắn mới. Hãy nhấn START rồi gửi /start cho bot. Chỉ chạy khi renderer đang dừng.')
