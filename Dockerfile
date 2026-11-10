FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir 'yt-dlp[default]>=2025.1.0,<2027'
WORKDIR /app
COPY app.py auth.py ./
COPY static ./static
RUN chmod -R a+rX /app && useradd --create-home --uid 10001 app && mkdir /data /auth && chown app:app /data /auth
USER app
ENV DATA_DIR=/data AUTH_DIR=/auth PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "app.py"]
