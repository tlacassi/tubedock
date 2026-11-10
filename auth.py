"""Local accounts, single-use invitations, and revocable browser sessions."""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import threading
from contextlib import contextmanager
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import urlparse

SESSION_TTL = 12 * 3600
INVITE_TTL = 7 * 86400
COOKIE = '__Host-tubedock'
ROOT = Path(__file__).resolve().parent
HASH_SLOTS = threading.BoundedSemaphore(2)


def derive(password, salt):
    if not HASH_SLOTS.acquire(blocking=False):
        raise AuthError('Serveur occupé. Réessaie dans quelques secondes.', 429)
    try:
        return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    finally:
        HASH_SLOTS.release()


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise AuthError('Le mot de passe doit contenir entre 8 et 128 caractères.')
    salt = secrets.token_hex(16)
    key = derive(password, bytes.fromhex(salt))
    return salt + ':' + key.hex()


def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > 128 or not encoded:
        return False
    salt, expected = encoded.split(':')
    actual = derive(password, bytes.fromhex(salt))
    return hmac.compare_digest(actual.hex(), expected)


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.directory / 'accounts.sqlite3'
        self.setup_file = self.directory / 'setup-token'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password TEXT, admin INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                    csrf TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS invites (
                    id INTEGER PRIMARY KEY, token TEXT UNIQUE NOT NULL,
                    label TEXT NOT NULL, expires REAL NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0, used_by INTEGER);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (key TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_key ON attempts(key, created);
            ''')
            if not db.execute('SELECT 1 FROM users WHERE admin=1').fetchone():
                token = secrets.token_urlsafe(32)
                db.execute('INSERT INTO users(username, admin, created) VALUES (?,1,?)', ('tlacassi', time.time()))
                db.execute('INSERT INTO settings VALUES (?,?)', ('setup_hash', digest(token)))
                db.execute('INSERT INTO settings VALUES (?,?)', ('setup_expires', str(time.time() + 3600)))
                self.setup_file.write_text(token, encoding='utf-8')
                self.setup_file.chmod(0o600)
        self.database.chmod(0o600)
        self.dummy_password = password_hash(secrets.token_urlsafe(24))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def limit(self, key):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM attempts WHERE created < ?', (now - 900,))
            if db.execute('SELECT count(*) FROM attempts WHERE key=?', (key,)).fetchone()[0] >= 10:
                raise AuthError('Trop de tentatives. Réessaie dans 15 minutes.', 429)
            db.execute('INSERT INTO attempts VALUES (?,?)', (key, now))

    def session(self, token):
        if not isinstance(token, str) or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute('''SELECT u.id,u.username,u.admin,s.csrf FROM sessions s
                JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.active=1''',
                (digest(token), time.time())).fetchone()
            return dict(row) if row else None

    def new_session(self, db, user_id):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest(token), user_id, csrf, time.time() + SESSION_TTL))
        return token

    def login(self, username, password):
        if not isinstance(username, str) or len(username) > 32:
            raise AuthError('Identifiants incorrects.', 401)
        with self.connect() as db:
            user = db.execute('SELECT * FROM users WHERE username=?', (username.lower(),)).fetchone()
            valid = password_matches(password, user['password'] if user and user['password'] else self.dummy_password)
            if not valid or not user or not user['active'] or not user['password']:
                raise AuthError('Identifiants incorrects.', 401)
            return self.new_session(db, user['id'])

    def setup(self, token, password):
        encoded = password_hash(password)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            settings = dict(db.execute('SELECT key,value FROM settings').fetchall())
            if not isinstance(token, str) or not hmac.compare_digest(digest(token), settings.get('setup_hash', '')) or float(settings.get('setup_expires', '0')) <= time.time():
                raise AuthError('Lien de configuration invalide ou expiré.', 403)
            admin = db.execute('SELECT id FROM users WHERE username=? AND password IS NULL AND admin=1', ('tlacassi',)).fetchone()
            if not admin:
                raise AuthError('Le compte administrateur est déjà configuré.', 403)
            db.execute('UPDATE users SET password=? WHERE id=?', (encoded, admin['id']))
            db.execute('DELETE FROM settings WHERE key IN (?,?)', ('setup_hash', 'setup_expires'))
            session = self.new_session(db, admin['id'])
        self.setup_file.unlink(missing_ok=True)
        return session

    def invite(self, label):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
            raise AuthError('Donne un nom à cette invitation (80 caractères maximum).')
        token, expires = secrets.token_urlsafe(32), time.time() + INVITE_TTL
        with self.connect() as db:
            row = db.execute('INSERT INTO invites(token,label,expires) VALUES (?,?,?)', (digest(token), label.strip(), expires))
            return {'id': row.lastrowid, 'token': token, 'expires': expires}

    def register(self, token, username, password):
        if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{3,32}', username):
            raise AuthError('Identifiant : 3 à 32 lettres, chiffres, tirets ou underscores.')
        if not isinstance(token, str) or len(token) > 128:
            raise AuthError('Invitation invalide.', 403)
        encoded = password_hash(password)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            invite = db.execute('SELECT id FROM invites WHERE token=? AND revoked=0 AND used_by IS NULL AND expires>?', (digest(token), time.time())).fetchone()
            if not invite:
                raise AuthError('Invitation invalide, utilisée ou expirée.', 403)
            try:
                user_id = db.execute('INSERT INTO users(username,password,created) VALUES (?,?,?)', (username.lower(), encoded, time.time())).lastrowid
            except sqlite3.IntegrityError:
                raise AuthError('Cet identifiant est déjà utilisé.')
            db.execute('UPDATE invites SET used_by=? WHERE id=?', (user_id, invite['id']))
            return self.new_session(db, user_id)

    def revoke_user(self, user_id, actor_id):
        if type(user_id) is not int:
            raise AuthError('Compte invalide.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user = db.execute('SELECT admin FROM users WHERE id=?', (user_id,)).fetchone()
            if not user:
                raise AuthError('Compte introuvable.', 404)
            if user['admin'] or user_id == actor_id:
                raise AuthError('Le compte administrateur ne peut pas être révoqué.', 403)
            db.execute('UPDATE users SET active=0 WHERE id=?', (user_id,))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))


store = None


def initialize():
    global store
    store = Store(os.environ.get('AUTH_DIR', '/auth'))


def cookie_token(handler):
    try:
        cookies = SimpleCookie(handler.headers.get('Cookie', ''))
        return cookies[COOKIE].value if COOKIE in cookies else ''
    except CookieError:
        return ''


def require(handler, admin=False, csrf=False):
    user = store.session(cookie_token(handler))
    if not user:
        raise AuthError('Connexion requise.', 401)
    if admin and not user['admin']:
        raise AuthError('Accès administrateur requis.', 403)
    if csrf and not hmac.compare_digest(handler.headers.get('X-CSRF-Token', '').encode(), user['csrf'].encode()):
        raise AuthError('Requête non autorisée. Recharge la page.', 403)
    return user


def reply(handler, status, payload, session=None, logout=False):
    data = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Cache-Control', 'no-store')
    if session is not None:
        handler.send_header('Set-Cookie', f'{COOKIE}={session}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}')
    if logout:
        handler.send_header('Set-Cookie', f'{COOKIE}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0')
    handler.end_headers()
    handler.wfile.write(data)


def read_body(handler):
    try:
        size = int(handler.headers.get('Content-Length', '0'))
        if not 0 < size <= 4096:
            raise ValueError()
        value = json.loads(handler.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise AuthError('Requête invalide.')


def get(handler, path):
    if path in ('/login', '/setup', '/invite', '/auth.js', '/auth.css', '/admin'):
        if path == '/admin':
            require(handler, admin=True)
        name = {'/auth.js': 'auth.js', '/auth.css': 'auth.css'}.get(path, 'auth.html')
        data = (ROOT / 'static' / name).read_bytes()
        handler.send_response(200)
        handler.send_header('Content-Type', {'js': 'text/javascript', 'css': 'text/css', 'html': 'text/html'}[name.rsplit('.', 1)[1]] + '; charset=utf-8')
        handler.send_header('Content-Length', str(len(data)))
        handler.send_header('Cache-Control', 'no-store')
        handler.end_headers()
        handler.wfile.write(data)
        return True
    if path == '/auth/me':
        reply(handler, 200, require(handler))
        return True
    if path in ('/auth/admin/users', '/auth/admin/invites'):
        require(handler, admin=True)
        with store.connect() as db:
            query = 'SELECT id,username,admin,active,created FROM users ORDER BY id' if path.endswith('/users') else '''SELECT i.id,i.label,i.expires,i.revoked,u.username AS used_by
                FROM invites i LEFT JOIN users u ON u.id=i.used_by ORDER BY i.id DESC LIMIT 200'''
            reply(handler, 200, [dict(row) for row in db.execute(query)])
        return True
    return False


def post(handler, path):
    if not path.startswith('/auth/'):
        return False
    # Host is preserved by the tunnel; reject cross-origin credential submissions.
    try:
        origin = urlparse(handler.headers.get('Origin', ''))
    except ValueError:
        raise AuthError('Origine non autorisée.', 403)
    if origin.scheme != 'https' or origin.netloc.lower() != handler.headers.get('Host', '').lower():
        raise AuthError('Origine non autorisée.', 403)
    body = read_body(handler)
    if path in ('/auth/login', '/auth/setup', '/auth/register'):
        store.limit('login:' + digest(handler.headers.get('CF-Connecting-IP', handler.client_address[0])))
        if path == '/auth/login':
            session = store.login(body.get('username'), body.get('password'))
        elif path == '/auth/setup':
            session = store.setup(body.get('token'), body.get('password'))
        else:
            session = store.register(body.get('token'), body.get('username'), body.get('password'))
        reply(handler, 200, {'ok': True}, session=session)
        return True
    user = require(handler, admin=path.startswith('/auth/admin/'), csrf=True)
    if path == '/auth/logout':
        with store.connect() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (digest(cookie_token(handler)),))
        reply(handler, 200, {'ok': True}, logout=True)
    elif path == '/auth/admin/invites':
        reply(handler, 201, store.invite(body.get('label')))
    elif path == '/auth/admin/revoke-user':
        store.revoke_user(body.get('id'), user['id'])
        reply(handler, 200, {'ok': True})
    elif path == '/auth/admin/revoke-invite':
        if type(body.get('id')) is not int:
            raise AuthError('Invitation invalide.')
        with store.connect() as db:
            db.execute('UPDATE invites SET revoked=1 WHERE id=?', (body['id'],))
        reply(handler, 200, {'ok': True})
    else:
        raise AuthError('Introuvable.', 404)
    return True


if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['renew-setup']:
        raise SystemExit('Usage: python auth.py renew-setup')
    initialize()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        admin = db.execute('SELECT password FROM users WHERE username=? AND admin=1', ('tlacassi',)).fetchone()
        if not admin or admin['password'] is not None:
            raise SystemExit('Admin already configured; setup renewal refused.')
        token = secrets.token_urlsafe(32)
        db.execute('UPDATE settings SET value=? WHERE key=?', (digest(token), 'setup_hash'))
        db.execute('UPDATE settings SET value=? WHERE key=?', (str(time.time() + 3600), 'setup_expires'))
        store.setup_file.write_text(token, encoding='utf-8')
        store.setup_file.chmod(0o600)
    print(token)
