# English AI Faceless Shorts – ChatGPT Plus → n8n → Kokoro (EN-US)/FFmpeg → Telegram → YouTube

## Yêu cầu

- **ChatGPT Plus đã có sẵn** để chuẩn bị nội dung và tạo ảnh **thủ công**. Workflow KHÔNG gọi ChatGPT/OpenAI API.
- Máy **Windows 10/11 + Docker Desktop** (WSL2) hoặc Linux/macOS có Docker Compose. Máy phải bật khi xử lý/đợi đăng.
- Internet cho Telegram và YouTube; phần Kokoro/FFmpeg chạy trong máy. Docker images và voice model tải qua mạng lần đầu.
- Tài khoản Google/YouTube có quyền upload, Telegram bot do bạn tạo, Python 3 để tải voice và lấy Chat ID (có thể dùng Python có sẵn).
- Ổ cứng còn trống vài GB và CPU tương đối tốt. Không đòi hỏi GPU.

**Chi phí bổ sung:** có thể 0đ phí API/phần mềm với bản self-host, trong hạn mức dịch vụ miễn phí và phần cứng sẵn có; không tính Plus đã trả, điện, Internet, máy chủ ngoài hoặc chi phí phát sinh theo chính sách nền tảng. Không có API OpenAI, ElevenLabs, Creatomate, TTS trả phí.

## 1. Bật Docker và tải giọng Kokoro tiếng Anh Mỹ

Chạy `python download-voice.py` để tải model ONNX và bộ giọng từ dự án kokoro-onnx. Mặc định giọng nữ Mỹ **af_heart**, tốc độ **1.0**, chạy trên CPU trong máy, không cần API trả phí.

Trong `.env` dùng:

```dotenv
KOKORO_MODEL=/files/voices/kokoro-v1.0.onnx
KOKORO_VOICES=/files/voices/voices-v1.0.bin
KOKORO_VOICE=af_heart
KOKORO_SPEED=1.0
```

Nếu ổ C ít dung lượng, chạy `python download-voice.py --directory D:/shorts_plus_local/voices` rồi thêm `KOKORO_ASSETS_DIR=D:/shorts_plus_local/voices` vào `.env`. Có thể đặt `VIDEO_PREVIEWS_DIR=D:/shorts_plus_local/previews` để lưu bản thử trên ổ D. Các đường dẫn model bên trong container vẫn là `/files/voices/...`.

Sau khi cập nhật: `docker compose up -d --build --no-deps renderer`. Giọng Piper cũ không còn được dùng. Video đã render không tự đổi giọng; dùng job mới hoặc bản xem thử riêng để tránh thay video đang chờ duyệt.

## 2. Cấu hình Telegram

1. Mở Telegram, tạo bot qua `@BotFather` và nhận bot token.
2. Copy `.env.example` thành `.env` (Windows: `copy .env.example .env`, Linux/macOS: `cp .env.example .env`).
3. Điền `TELEGRAM_BOT_TOKEN=...` vào `.env`, lưu, **không chia sẻ file `.env`**.
4. Mở chat trực tiếp với bot, bấm Start, gửi `/start`.
5. Trước khi bật container, chạy `python get-telegram-ids.py` để hiện `Chat ID` và `Approver user ID`. Điền 2 ID vào `.env`.
6. Chỉ **đúng user ID cấu hình** mới duyệt video được, ngay cả khi người khác biết bot.

**Quan trọng:** bot dùng Telegram `getUpdates` (long polling), không dùng webhook; không cấu hình webhook cho bot đó trên nền tảng khác. Nếu bot đã có webhook, xóa webhook ở Telegram Bot API trước khi chạy và không dùng đồng thời bot này trong ứng dụng polling khác.

## 3. Bật n8n + renderer

```bash
docker compose up -d --build
```

Mở `http://localhost:5678` trên chính máy cài Docker. `renderer` chạy riêng, chỉ có n8n container truy cập được, không mở cổng renderer ra ngoài. n8n chỉ mở cho `127.0.0.1`, không tự công khai internet.

Kiểm tra lỗi bằng:

```bash
docker compose logs -f renderer
```

Kiểm tra n8n container:

```bash
docker compose logs -f n8n
```

Trên Linux nếu n8n không đọc được file ở `data/jobs`, kiểm tra quyền đọc thư mục cho user UID 1000 trong container. Không nên dùng `chmod 777` trên thư mục chứa bí mật. `data` chỉ chứa nội dung media, file `.env` nằm ngoài volume này.

## 4. Import workflow

1. Vào n8n → Create Workflow → Import from File → chọn **n8n_workflow.json**.
2. Workflow gồm **17 node tác vụ/trigger + 2 ghi chú** (tổng 19). Ba nhánh:
   - Mỗi 5 phút: nhập ZIP từ inbox, nhận job mới rồi dựng và gửi Telegram duyệt.
   - Mỗi 1 phút: lấy job đã duyệt rồi upload YouTube.
   - Nhánh MP4: nhận `input.mp4` và `script.json`, lồng giọng Kokoro, gửi Telegram duyệt, rồi nối vào cùng nhánh upload YouTube.
3. Trong node **Upload YouTube - PRIVATE khi thu**, chọn **YouTube OAuth2 credentials** của bạn. **Không có tài khoản Google trong JSON**.
4. Để tạo OAuth2 tự host: vào Google Cloud Console → bật **YouTube Data API v3** → tạo OAuth consent screen và OAuth client dạng **Web application**, sau đó thêm chính xác redirect URL n8n hiển thị, ví dụ `http://localhost:5678/rest/oauth2-credential/callback`. Thêm Google account của bạn vào Test Users nếu app đang Testing; lưu client ID/secret trong Credentials của n8n.
5. Đăng ký workflow (Publish / Activate tùy phiên bản n8n) sau khi chạy thử.

**Chế độ an toàn:** Workflow mặc định upload video **Private** (đã được bạn duyệt nhưng chưa công khai). Khi thật sự muốn duyệt trên Telegram xong là đăng công khai, đổi `privacyStatus` trong node YouTube từ `private` → `public`, sau khi thử thành công cả hai nhánh.

## 5. Tạo ZIP bằng ChatGPT Plus và đưa vào workflow

- Copy `PROMPT_CHATGPT_PLUS.txt` vào ChatGPT Plus, điền chủ đề. Prompt bắt buộc **title, description, narration và image_prompt bằng tiếng Anh**, với giọng đọc Mỹ.
- Prompt yêu cầu ChatGPT tự chọn số cảnh/ảnh phù hợp nội dung, tối thiểu 3 ảnh; viết `manifest.json`, tạo đủ ảnh thật từ `image_prompt`, chuyển đúng định dạng PNG và đóng gói thành một ZIP tải xuống. Không cố định 4 ảnh. Phiên ChatGPT cần có công cụ tạo ảnh và tạo file; nếu thiếu công cụ hoặc hết hạn mức, prompt yêu cầu thông báo rõ thay vì đưa link ZIP giả.
- Tải ZIP hoàn chỉnh rồi đặt **nguyên file** vào `data/inbox/`. Không cần giải nén, tạo thư mục job hoặc file `READY`.
- Bấm **Chay thu thu cong** hoặc đợi lịch 5 phút. Node **Nhap ZIP tu inbox** kiểm tra ZIP, nhập nội dung và tạo job tự động. ZIP gồm `manifest.json` và `scene1.png` đến ảnh cuối theo số cảnh (ít nhất 3), tổng cộng N + 1 file cho N ảnh; hỗ trợ thêm một thư mục bao ngoài.
- ZIP đã nhập chuyển vào `data/inbox/processed/`. ZIP lỗi chuyển vào `data/inbox/failed/` kèm file `.zip.error.txt` ghi lý do. Xem output node nhập ZIP để biết các file đã nhập, trùng lặp, lỗi hoặc đang chờ copy xong.
- ZIP tối đa 100 MB, tổng file sau giải nén tối đa 200 MB. Nộp lại chính ZIP cũ không tạo job mới. Đóng gói lại sẽ đổi mã ZIP và có thể tạo job mới; tránh nộp lại nội dung đã upload nếu không muốn đăng trùng.
- Cách cũ vẫn dùng được: đặt `manifest.json` và ảnh vào `data/jobs/<job_id>/`, rồi tạo `READY` sau khi copy xong. `sample_job/manifest.json` chỉ là ví dụ, không phải job đang chạy.

## 6. Kiểm tra quá trình

1. Đặt ZIP vào `data/inbox/`, rồi chạy nhánh "Chay thu thu cong" để nhập ZIP và nhận job mới, hoặc để lịch 5 phút kích hoạt.
2. n8n gọi renderer qua mạng Docker; renderer kiểm tra ảnh, gọi **Kokoro offline** để tạo WAV mỗi cảnh, dùng **FFmpeg** ghép 9:16 và ghi `output.mp4` cùng `captions.srt` và `captions.ass` trung gian. Phụ đề đổi màu theo từng từ và hiệu ứng xuất hiện nhẹ. Thời gian từ được ước lượng từ độ dài lời đọc, chưa phải forced alignment. Chuyển cảnh giữ nguyên tổng thời lượng lời đọc, không chồng tiếng giữa hai cảnh.
3. Video xem trước và hai nút **✅ Duyệt và đăng / ❌ Từ chối** được Telegram gửi vào chat của bạn.
4. Nếu từ chối, job trở thành `rejected` và **không được upload**.
5. Nếu duyệt, service đánh dấu `approved`; nhánh lịch 1 phút tìm video và n8n upload bằng OAuth lên YouTube.
6. Sau khi YouTube trả Video ID, workflow đánh dấu `published` trong `data/jobs/<id>/.workflow-status.json`.

Nút duyệt trên Telegram gọi bot trực tiếp; **không phải mở cổng n8n công khai hoặc dùng tunnel/webhook trả phí**. Bạn có thể duyệt Telegram từ điện thoại dù n8n chỉ mở localhost.

## 7. Xem trạng thái và xử lý lỗi

Mỗi job có `.workflow-status.json` với các trạng thái:

- `new` (chưa có state) → `claimed` → `rendering` → `pending_review`
- `pending_review` → `rejected` (dừng) hoặc `approved`
- `approved` → `uploading` → `published`
- `failed`: lỗi JSON/ảnh/Kokoro/FFmpeg/Telegram.

Nếu trạng thái mắc ở `uploading`, **không tự đặt lại thành approved ngay**: trước tiên kiểm tra YouTube Studio xem đã upload hay chưa để tránh video trùng. Nếu `failed`, đọc field `error` và log; sau khi sửa có thể tạo job mới với ID khác hoặc sửa trạng thái thận trọng. Nếu hệ thống tắt trong lúc `claimed` hoặc `rendering`, job đó có thể cần can thiệp thủ công.

## 8. Đầu ra tiếng Anh và kiểm tra trước khi upload

- Giọng đọc tự động: **Kokoro af_heart (en-US)**.
- Tiêu đề, mô tả, lời thoại và phụ đề: **tiếng Anh**, lấy từ `manifest.json` do bạn chuẩn bị bằng ChatGPT Plus.
- Các nút và lời nhắn duyệt Telegram: **tiếng Anh** (Approve & upload / Reject).
- Phụ đề lấy trực tiếp từ lời thoại tiếng Anh, không phải bản dịch hay nhận dạng tiếng nói; thời gian ước lượng theo số từ.
- Phụ đề chữ trắng in đậm, cỡ lớn, viền đen dày; đặt giữa ngang tại 60% chiều cao màn hình. Màu vàng chạy dần trong từng từ theo nhịp đọc, phần chưa đọc màu trắng; cỡ chữ giữ cố định. Ảnh được resize bằng Lanczos và video mã hóa CRF 18 để hạn chế mất chi tiết. Nếu ảnh gốc đã mờ hoặc có hiệu ứng xóa phông, cần thay ảnh gốc để nền rõ hơn.
- Workflow kiểm tra trường `language = en-US`, nhưng **không tự dịch** văn bản nếu bạn nhập nội dung tiếng Việt. Hãy kiểm tra JSON và nghe bản nháp trước khi duyệt.
- Workflow mặc định đăng **Private** khi được duyệt; muốn công khai đổi `privacyStatus` sang `public` sau khi thử.

## 9. Giới hạn và an toàn

- Các công cụ chạy trên **phần cứng có sẵn**, không miễn phí phần cứng/điện hoặc ChatGPT Plus.
- Gói ZIP do ChatGPT tạo cần được bạn tải xuống và đặt vào `data/inbox/`; workflow tự xử lý nội dung bên trong. Không dùng cách lấy cookie/session ChatGPT hay tự động hóa trình duyệt để giả lập API miễn phí.
- Kokoro tiếng Anh (en-US) có thể phát âm một số tên riêng/từ lạ chưa tốt. Nên nghe thử trước khi duyệt.
- Video dùng ảnh tĩnh, không zoom/pan; giữ chuyển cảnh fade/slide 0,4 giây giữa các ảnh. Giữ giọng Kokoro và phụ đề ASS tô vàng chạy dần theo nhịp đọc, cỡ chữ cố định. Không tự tạo video AI chuyển động hay nhạc nền.
- Chưa có cơ chế tự retry các API upload bị lỗi hoặc một hệ thống quản trị job hoàn chỉnh; đây là bản triển khai MVP dễ kiểm thử, không phải hệ thống production nhiều người.
- Tài khoản Google có thể cần xác minh và giới hạn API/quota; YouTube có thể yêu cầu khai báo nội dung tổng hợp thay đổi thực tế. Kiểm tra bản quyền ảnh/nhạc và nội dung để tuân thủ YouTube.
- Nếu muốn chạy khi máy tắt hoặc dùng từ xa, cần máy chủ/VPS hoặc máy luôn bật; không còn chắc 0đ hạ tầng.

## Nhánh thêm trong workflow chung: lồng giọng vào MP4 có sẵn

Dùng trigger **Chay MP4 Voiceover** trong workflow AI Shorts cũ. Đặt MP4 ở
`data/video_jobs/<job_id>/input.mp4`. Gửi video trực tiếp và `PROMPT_CHATGPT_VIDEO.txt`
cho ChatGPT Plus, chọn chủ đề rồi đặt `script.json` cạnh video. Chạy nhánh MP4 để
tạo lời đọc Kokoro liền mạch cho toàn video, tự căn nhịp chung theo thời lượng và tạo
`output.mp4` có phụ đề động giống nhánh ảnh. Âm thanh gốc được thay hoàn toàn,
giữ thứ tự cảnh, tốc độ và kích thước hiển thị. Video tự được
gửi Telegram duyệt và dùng chung nhánh upload YouTube. Xem hướng dẫn
chi tiết trong [docs/MP4_VOICEOVER.md](docs/MP4_VOICEOVER.md). File import cho máy
khác: `n8n_workflow.json`. Bản MP4 riêng cũ có tên bắt đầu bằng DA GOP và được giữ tắt. Không cần API trả phí.

## Link tài liệu

- https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.youtube/
- https://docs.n8n.io/integrations/builtin/credentials/google/oauth-single-service/
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.readwritefile/
- https://github.com/thewh1teagle/kokoro-onnx
- https://huggingface.co/hexgrad/Kokoro-82M
