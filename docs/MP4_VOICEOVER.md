# MP4 Voiceover — nhánh trong workflow chung

Mở workflow AI Shorts cũ, chọn trigger **Chay MP4 Voiceover** ở nhánh MP4 phía dưới.
File import workflow tổng cho máy khác: `n8n_workflow.json`. Bản workflow MP4 riêng
cũ được giữ tắt, tên bắt đầu bằng **DA GOP**, không cần chạy bản đó.

Bạn gửi video trực tiếp lên ChatGPT Plus cùng `PROMPT_CHATGPT_VIDEO.txt`, điền chủ
đề do mình chọn và nhận `script.json` chứa một lời thoại liền mạch. Không cần ảnh
khung hình, contact sheet hoặc file metadata từ workflow. Không gọi AI API.
Giọng đọc là Kokoro `af_heart` tiếng Anh Mỹ. Toàn bộ âm thanh gốc được thay bằng
giọng mới. MP4 được mã hóa H.264 chất lượng cao (CRF 18) để chèn phụ đề trực tiếp,
giữ thứ tự cảnh, tốc độ phát và kích thước hiển thị; không thêm crop/zoom/chuyển cảnh.

## Cách dùng

1. Gửi video trực tiếp cho ChatGPT Plus cùng nội dung **PROMPT_CHATGPT_VIDEO.txt**.
   Điền chủ đề ở đầu prompt. ChatGPT viết một mạch lời thoại cho toàn video và
   tự cân đối độ dài theo thời lượng, không chia theo frame. Nếu phiên ChatGPT
   thực tế không đọc được video/thời lượng, prompt yêu cầu nói rõ và hỏi thông tin
   cần thiết ngay trong chat, thay vì giả vờ đã xem video.
2. Tải **script.json** từ ChatGPT. Tạo thư mục `data/video_jobs/demo01/`, đặt hai
   file **input.mp4** và **script.json** cạnh nhau. ID thư mục chỉ gồm chữ/số,
   `_`, `-`, tối đa 36 ký tự. Không cần READY hay chạy bước chuẩn bị trước.
3. Mở workflow AI Shorts chung trong n8n và chạy trigger **Chay MP4 Voiceover**.
   Request trả về ngay; tạo giọng chạy nền. Node **Xem trang thai va duong dan ket qua**
   hiển thị tiến độ. Có thể Publish để tự quét/cập nhật mỗi phút.
4. Renderer tự đọc thời lượng thật bằng ffprobe bên trong, không tạo hoặc yêu cầu
   `contact-sheet.jpg`, `frames/`, `video-info.json` hay `PROMPT_FOR_THIS_VIDEO.txt`.
5. Theo dõi trạng thái đến **completed**. Kết quả nằm trong cùng thư mục:
   **output.mp4** đã có phụ đề động, `narration.wav`, `captions.srt` và `captions.ass`. Video hoàn tất được tự xếp
   vào hàng đợi duyệt Telegram. Node **Gui MP4 sang duyet Telegram** cũng kiểm tra
   các video hoàn tất ở những lượt trước để phục hồi việc chuyển sang duyệt.
6. Nhận video trên Telegram, bấm **Approve & upload** hoặc **Reject**. Sau khi duyệt,
   nhánh upload YouTube dùng chung sẽ nhận video này. Publish workflow để lịch
   một phút tự chạy; nếu chưa Publish, chạy trigger MP4 hoặc nhánh tìm video đã
   duyệt thêm một lần sau khi bấm duyệt.

Phụ đề được đốt trực tiếp vào MP4, đồng thời lưu SRT/ASS riêng. Kiểu giống nhánh ảnh:
chữ trắng in đậm, cỡ lớn, viền đen dày giống mẫu; đặt giữa ngang tại 60% chiều cao màn hình.
Màu vàng chạy dần từ trái sang phải trong từng từ theo nhịp đọc; phần đã đọc giữ vàng, phần chưa đọc màu trắng. Cỡ chữ giữ cố định.
Chữ và lề tự căn theo video dọc/ngang, kể cả video có metadata xoay. Nhánh ZIP/ảnh và MP4 dùng chung bot,
người được duyệt, node đọc MP4, credential và thiết lập quyền riêng tư YouTube.
MP4 được chuyển sang `data/jobs/mp4-<id>/output.mp4` để nhánh upload hiện có đọc.
Video chỉ được upload khi trạng thái chung là **approved**; từ chối hoặc chưa
duyệt thì không upload. Nếu file lớn, bot nhận bản xem trước nén dưới 49 MB;
YouTube vẫn nhận file lồng giọng gốc.

## Script và căn thời gian

`script.json` là UTF-8 JSON, schema mới:

```json
{
  "language": "en-US",
  "mode": "continuous",
  "title": "A title matching your actual video",
  "description": "An accurate YouTube description",
  "narration": "The complete connected voiceover, from the opening to the closing."
}
```

`narration` là một chuỗi 3–20000 ký tự, gồm toàn bộ câu chuyện. Không cần `segments`,
`start` hoặc `end`. Kokoro nhận toàn bộ lời thoại trong một lượt; sau đó nhịp đọc
được căn chung cho toàn video bằng FFmpeg atempo (0,8–1,25 lần, giữ cao độ).
Không chèn khoảng im lặng theo frame/cảnh; chỉ còn các nhịp ngắt câu tự nhiên.
Nếu lời thoại quá ngắn hoặc quá dài để đọc tự nhiên, job yêu cầu bổ sung/rút gọn
script, không chèn khoảng im lặng dài hoặc cắt mất lời. Thời gian tô từng từ được
ước lượng theo lời thoại, chưa phải forced alignment. JSON `segments` cũ vẫn đọc được để tương thích, nhưng
prompt mới chỉ xuất lời thoại liên tục.

Đầu vào: MP4 có luồng hình đọc được, 1–600 giây, tối đa 1 GB. Nội dung script do
bạn kiểm tra trước khi nộp; workflow chỉ tạo giọng và ghép, không tự kiểm chứng
các khẳng định do ChatGPT viết.

## Trạng thái và chạy lại

Khi đã có đủ hai file: `queued` → `preparing` (kiểm tra thời lượng) → `narrating`
→ `muxing` → `completed`. Nếu chưa có script: `awaiting_script`; chỉ cần thêm
`script.json` rồi quét lại, không có bước trích frame.
Trường **review_status** tiếp tục theo hàng đợi chung:
`ready_review` → `review_sending` → `pending_review` → `approved` → `uploading` → `published`,
hoặc `rejected` nếu bị từ chối. Khi `published`, API cũng hiển thị YouTube Video ID.

Khi **failed**, đọc `error` trong node trạng thái hoặc `.voiceover-status.json`.
Sửa `input.mp4` hoặc `script.json` rồi chạy lại workflow. File đã thay đổi mới
được tự đưa vào hàng đợi, tránh lặp lại cùng lỗi. Job đã hoàn tất không tự dựng
lại; dùng ID thư mục mới nếu muốn tạo phiên bản khác. Đừng thay đầu vào khi đang chạy.
Nếu chỉ bước Telegram bị lỗi, file lồng giọng vẫn được giữ; xem **review_error**.
Hệ thống không tự gửi lại liên tục hoặc tự bỏ qua bước duyệt. Không tự sửa trạng
thái thành approved/uploading, và không nộp lại nội dung đã đăng dưới ID khác.

Các job MP4 và ảnh dùng chung một khóa dựng để tránh tranh CPU/RAM. MP4 chạy nền,
vì vậy timeout HTTP 15 giây không phải giới hạn thời gian xử lý video.

## API nội bộ

- `POST /video-jobs/scan` không có body: xếp hàng chuẩn bị/ghép các thư mục sẵn sàng,
  trả ngay `{ "jobs": [...] }`.
- `GET /video-jobs`: đọc trạng thái, tiến độ, lỗi và đường dẫn kết quả.
- `POST /video-jobs/queue-reviews`: xếp các MP4 hoàn tất sang duyệt Telegram, trả ngay;
  không tự upload và không gửi lại các job đã có trạng thái duyệt/đăng.
- `GET /video-jobs/<job_id>`: đọc trạng thái một job.

Các endpoint chỉ dùng trong mạng Docker hiện có. Không có credential/API key mới.
