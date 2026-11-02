FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# FFmpeg is required for recording, conversion, compression and ffprobe.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install -r /app/requirements.txt

COPY . /app

# Keep persistent bot data and downloads outside the image.
RUN mkdir -p /app/bot/data /app/bot/downloads
VOLUME ["/app/bot/data", "/app/bot/downloads"]

CMD ["python", "bot.py"]
