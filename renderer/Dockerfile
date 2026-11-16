FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-dejavu-core espeak-ng libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY renderer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY renderer/app.py renderer/zip_jobs.py renderer/video_jobs.py renderer/review_bridge.py renderer/subtitles.py ./
ENV PYTHONUNBUFFERED=1 JOBS_DIR=/files/jobs
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
