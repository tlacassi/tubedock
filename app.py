import json
import ipaddress
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import unicodedata
import sqlite3
import auth
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', '/tmp/converter-data'))
TTL = 3600
MAX_JOBS = 4
TIMEOUT = 1800
jobs = {}
lock = threading.Lock()
worker = threading.Semaphore(1)


def video_url(raw):
    """Accept HTTP(S) links for yt-dlp; reject obvious local addresses."""
    if not isinstance(raw, str) or len(raw) > 2048 or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return None
    try:
        url = urlparse(raw)
        host = (url.hostname or '').lower().rstrip('.')
        if url.scheme not in ('http', 'https') or not host or url.username or url.password:
            return None
        if url.port not in (None, 80, 443) or host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if '.' not in host or not re.fullmatch(r'[a-z0-9.-]+', host):
                return None
        return raw
    except ValueError:
        return None


def safe_title(title):
    if not isinstance(title, str):
        return 'youtube-conversion'
    title = unicodedata.normalize('NFC', title)
    title = ''.join(ch for ch in title if unicodedata.category(ch)[0] in ('L', 'N') or ch in ' -_.()[]')
    title = re.sub(r'\s+', ' ', title).strip(' .')
    while len(title.encode('utf-8')) > 180:
        title = title[:-1]
    title = title.rstrip(' .')
    if not title:
        return 'youtube-conversion'
    if re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', title.split('.')[0].rstrip(' ')):
        title = '_' + title
    return title


def clean():
    now = time.time()
    with lock:
        expired = [key for key, job in jobs.items() if job.get('expires', float('inf')) <= now]
        for key in expired:
            shutil.rmtree(DATA / key, ignore_errors=True)
            del jobs[key]


def cleanup_loop():
    while True:
        clean()
        time.sleep(30)


def convert(key, url, fmt):
    with worker:
        folder = DATA / key
        try:
            with lock:
                jobs[key]['status'] = 'processing'
            folder.mkdir(parents=True, exist_ok=True)
            command = ['yt-dlp', '--ignore-config', '--no-playlist', '--no-progress', '--no-part',
                       '--max-filesize', '2G', '--socket-timeout', '20', '--retries', '2',
                       '--print', 'after_move:%(title)j', '-o', str(folder / 'video.%(ext)s')]
            if fmt == 'mp3':
                command += ['-f', 'bestaudio/best', '-x', '--audio-format', 'mp3', '--audio-quality', '0']
            else:
                command += ['-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', '--merge-output-format', 'mp4', '--recode-video', 'mp4']
            with (folder / 'title.txt').open('wb') as title_file:
                subprocess.run(command + ['--', url], stdout=title_file, stderr=subprocess.DEVNULL,
                               timeout=TIMEOUT, check=True)
            result = folder / ('video.' + fmt)
            if not result.is_file():
                raise RuntimeError('Missing output')
            try:
                with (folder / 'title.txt').open('rb') as title_file:
                    title = json.loads(title_file.read(65536).decode('utf-8'))
            except (OSError, ValueError, UnicodeError):
                title = None
            with lock:
                jobs[key].update(status='ready', expires=time.time() + TTL, title=safe_title(title))
        except (subprocess.SubprocessError, OSError, RuntimeError):
            shutil.rmtree(folder, ignore_errors=True)
            with lock:
                jobs[key].update(status='error', expires=time.time() + TTL)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def end_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")
        super().end_headers()

    def log_message(self, format, *args):
        pass

    def send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        try:
            self.post()
        except auth.AuthError as error:
            auth.reply(self, error.status, {'error': str(error)})
        except sqlite3.Error:
            auth.reply(self, 503, {'error': 'Authentification temporairement indisponible.'})

    def post(self):
        if auth.post(self, self.path):
            return
        user = auth.require(self, csrf=True)
        if self.path != '/api/jobs':
            return self.send_json(404, {'error': 'Introuvable.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 4096:
                raise ValueError()
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError()
            url, fmt = video_url(body.get('url')), body.get('format')
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return self.send_json(400, {'error': 'Requête invalide.'})
        if not url or fmt not in ('mp3', 'mp4'):
            return self.send_json(400, {'error': 'Utilise un lien HTTP(S) valide et choisis MP3 ou MP4.'})
        clean()
        with lock:
            if sum(j['status'] in ('queued', 'processing') for j in jobs.values()) >= MAX_JOBS:
                return self.send_json(429, {'error': 'La file est pleine. Réessaie dans quelques instants.'})
            key = uuid.uuid4().hex
            jobs[key] = {'status': 'queued', 'format': fmt, 'owner': user['id']}
        threading.Thread(target=convert, args=(key, url, fmt), daemon=True).start()
        self.send_json(202, {'id': key})

    def do_GET(self):
        try:
            self.get()
        except auth.AuthError as error:
            if error.status == 401 and not self.path.startswith(('/api/', '/auth/')):
                self.send_response(303)
                self.send_header('Location', '/login')
                self.send_header('Content-Length', '0')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
            else:
                auth.reply(self, error.status, {'error': str(error)})
        except sqlite3.Error:
            auth.reply(self, 503, {'error': 'Authentification temporairement indisponible.'})

    def get(self):
        path = urlparse(self.path).path
        if auth.get(self, path):
            return
        user = auth.require(self)
        if path in ('/', '/style.css', '/app.js'):
            name = {'/': 'index.html', '/style.css': 'style.css', '/app.js': 'app.js'}[path]
            data = (ROOT / 'static' / name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', {'html': 'text/html', 'css': 'text/css', 'js': 'text/javascript'}[name.rsplit('.', 1)[1]] + '; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        match = re.fullmatch(r'/api/jobs/([0-9a-f]{32})(/download)?', path)
        if not match:
            return self.send_json(404, {'error': 'Introuvable.'})
        clean()
        key = match.group(1)
        with lock:
            job = jobs.get(key, {}).copy()
            if not job or job.get('owner') != user['id']:
                return self.send_json(404, {'error': 'Ce lien a expiré ou est introuvable.'})
            if match.group(2):
                if job['status'] != 'ready':
                    return self.send_json(409, {'error': 'Le fichier n’est pas prêt.'})
                try:
                    file = (DATA / key / ('video.' + job['format'])).open('rb')
                except OSError:
                    return self.send_json(404, {'error': 'Fichier indisponible.'})
            else:
                return self.send_json(200, {name: value for name, value in job.items() if name != 'owner'})
        with file:
            self.send_response(200)
            self.send_header('Content-Type', 'audio/mpeg' if job['format'] == 'mp3' else 'video/mp4')
            filename = job['title'] + '.' + job['format']
            self.send_header('Content-Disposition', "attachment; filename=\"youtube-conversion." + job['format'] + "\"; filename*=UTF-8''" + quote(filename, safe=''))
            self.send_header('Content-Length', str(os.fstat(file.fileno()).st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                shutil.copyfileobj(file, self.wfile, length=1024 * 1024)
            except (BrokenPipeError, ConnectionResetError):
                pass


def main():
    auth.initialize()
    DATA.mkdir(parents=True, exist_ok=True)
    for item in DATA.iterdir():
        if item.is_dir() and re.fullmatch(r'[0-9a-f]{32}', item.name):
            shutil.rmtree(item, ignore_errors=True)
    threading.Thread(target=cleanup_loop, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()


if __name__ == '__main__':
    main()
