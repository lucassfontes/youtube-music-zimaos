FROM python:3.12-slim-bookworm
# YouTube extraction needs a JS runtime (Deno) plus yt-dlp-ejs.
COPY --from=denoland/deno:bin-2.9.8 /deno /usr/local/bin/deno
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ /app/
RUN mkdir -p /config /music /videos && useradd -u 1000 -m -s /usr/sbin/nologin appuser \
 && chown -R appuser:appuser /app /config /music /videos
USER 1000:1000
EXPOSE 8787
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1"]
