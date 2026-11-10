import concurrent.futures
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import auth


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = auth.Store(self.temp.name)
        self.setup_token = self.store.setup_file.read_text()
        self.admin_token = self.store.setup(self.setup_token, 'abcdefgh')

    def tearDown(self):
        self.temp.cleanup()

    def test_password_minimum_and_hash(self):
        for password in ('1234567', None, 'x' * 129):
            with self.assertRaises(auth.AuthError):
                auth.password_hash(password)
        encoded = auth.password_hash('12345678')
        self.assertNotIn('12345678', encoded)
        self.assertTrue(auth.password_matches('12345678', encoded))
        self.assertFalse(auth.password_matches('wrongpass', encoded))

    def test_setup_is_single_use_and_secret_removed(self):
        self.assertFalse(self.store.setup_file.exists())
        with self.assertRaises(auth.AuthError):
            self.store.setup(self.setup_token, 'newpassword')
        with self.assertRaises(auth.AuthError):
            self.store.setup('wrong-token', 'newpassword')
        self.assertTrue(self.store.session(self.admin_token)['admin'])

    def test_setup_expired_and_hash_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            pending = auth.Store(directory)
            token = pending.setup_file.read_text()
            with patch('auth.time.time', return_value=time.time() + 3601):
                with self.assertRaises(auth.AuthError):
                    pending.setup(token, 'abcdefgh')
        self.assertTrue(auth.HASH_SLOTS.acquire(False))
        self.assertTrue(auth.HASH_SLOTS.acquire(False))
        try:
            with self.assertRaises(auth.AuthError) as error:
                auth.password_hash('abcdefgh')
            self.assertEqual(error.exception.status, 429)
        finally:
            auth.HASH_SLOTS.release()
            auth.HASH_SLOTS.release()

    def test_login_persistence_expiry_and_invalid_cookie(self):
        token = self.store.login('TLACASSI', 'abcdefgh')
        self.assertEqual(self.store.session(token)['username'], 'tlacassi')
        self.assertIsNone(self.store.session('made-up-token'))
        with self.assertRaises(auth.AuthError):
            self.store.login('tlacassi', 'wrongpass')
        with patch('auth.time.time', return_value=time.time() + auth.SESSION_TTL + 1):
            self.assertIsNone(self.store.session(token))
        reopened = auth.Store(self.temp.name)
        self.assertIsNotNone(reopened.session(token))
        self.assertFalse(reopened.setup_file.exists())

    def test_invitation_expired_revoked_used_and_duplicate_username(self):
        invitation = self.store.invite('Alex')
        with self.assertRaises(auth.AuthError):
            self.store.register(invitation['token'], 'tlacassi', 'abcdefgh')
        token = self.store.register(invitation['token'], 'alex', 'abcdefgh')
        self.assertFalse(self.store.session(token)['admin'])
        with self.assertRaises(auth.AuthError):
            self.store.register(invitation['token'], 'another', 'abcdefgh')
        for state in ('expired', 'revoked'):
            invite = self.store.invite(state)
            with self.store.connect() as db:
                db.execute('UPDATE invites SET expires=0' if state == 'expired' else 'UPDATE invites SET revoked=1')
            with self.assertRaises(auth.AuthError):
                self.store.register(invite['token'], state, 'abcdefgh')

    def test_invitation_is_atomic_under_concurrent_claims(self):
        invite = self.store.invite('Race')
        def claim(username):
            try:
                self.store.register(invite['token'], username, 'abcdefgh')
                return True
            except auth.AuthError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(claim, ('first', 'second')))
        self.assertEqual(outcomes.count(True), 1)

    def test_revocation_invalidates_every_session_and_login(self):
        invite = self.store.invite('Alex')
        first = self.store.register(invite['token'], 'alex', 'abcdefgh')
        second = self.store.login('alex', 'abcdefgh')
        user_id = self.store.session(first)['id']
        self.store.revoke_user(user_id, self.store.session(self.admin_token)['id'])
        self.assertIsNone(self.store.session(first))
        self.assertIsNone(self.store.session(second))
        with self.assertRaises(auth.AuthError):
            self.store.login('alex', 'abcdefgh')
        with self.assertRaises(auth.AuthError):
            self.store.revoke_user(1, 1)

    def test_rate_limit(self):
        for _ in range(10):
            self.store.limit('same-ip')
        with self.assertRaises(auth.AuthError) as error:
            self.store.limit('same-ip')
        self.assertEqual(error.exception.status, 429)


class HttpAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        auth.store = auth.Store(Path(self.temp.name) / 'auth')
        self.admin_token = auth.store.setup(auth.store.setup_file.read_text(), 'abcdefgh')
        self.admin = auth.store.session(self.admin_token)
        invite = auth.store.invite('Guest')
        self.guest_token = auth.store.register(invite['token'], 'guest', 'abcdefgh')
        self.guest = auth.store.session(self.guest_token)
        self.old_data = app.DATA
        app.DATA = Path(self.temp.name) / 'data'
        app.DATA.mkdir()
        app.jobs.clear()

    def tearDown(self):
        app.DATA = self.old_data
        app.jobs.clear()
        self.temp.cleanup()

    def request(self, path, body=None, token=None, csrf=None, origin='https://downloader.test'):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        headers = {'Host': 'downloader.test', 'Origin': origin}
        if token:
            headers['Cookie'] = auth.COOKIE + '=' + token
        if csrf:
            headers['X-CSRF-Token'] = csrf
        if body is not None:
            headers['Content-Type'] = 'application/json'
        conn.request('GET' if body is None else 'POST', path, body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def test_unauthenticated_routes_and_security_headers(self):
        for path in ('/api/jobs/' + 'a' * 32, '/auth/me', '/auth/admin/users'):
            self.assertEqual(self.request(path)[0], 401)
        for path in ('/', '/app.js', '/style.css', '/admin'):
            status, headers, _ = self.request(path)
            self.assertEqual(status, 303)
            self.assertEqual(headers['Location'], '/login')
        for path in ('/login', '/invite', '/setup', '/auth.js', '/auth.css'):
            status, headers, _ = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_cookie_origin_csrf_and_admin_authorization(self):
        credentials = {'username': 'tlacassi', 'password': 'abcdefgh'}
        status, headers, _ = self.request('/auth/login', credentials)
        self.assertEqual(status, 200)
        for flag in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.assertEqual(self.request('/auth/login', credentials, origin='https://evil.test')[0], 403)
        self.assertEqual(self.request('/auth/login', credentials, origin='https://[invalid')[0], 403)
        self.assertEqual(self.request('/auth/admin/invites', {'label': 'test'}, self.admin_token)[0], 403)
        self.assertEqual(self.request('/auth/admin/invites', {'label': 'test'}, self.guest_token, self.guest['csrf'])[0], 403)
        self.assertEqual(self.request('/auth/admin/invites', {'label': 'test'}, self.admin_token, self.admin['csrf'])[0], 201)
        self.assertEqual(self.request('/api/jobs', {'url':'https://example.com', 'format':'mp3'}, self.admin_token)[0], 403)

    def test_job_download_ownership_and_revocation(self):
        key = 'b' * 32
        folder = app.DATA / key
        folder.mkdir()
        (folder / 'video.mp3').write_bytes(b'test media')
        app.jobs[key] = {'status':'ready', 'owner':self.guest['id'], 'format':'mp3', 'title':'test', 'expires':time.time()+3600}
        for suffix in ('', '/download'):
            path = '/api/jobs/' + key + suffix
            self.assertEqual(self.request(path, token=self.admin_token)[0], 404)
            self.assertEqual(self.request(path, token=self.guest_token)[0], 200)
        status, _, data = self.request('/api/jobs/' + key, token=self.guest_token)
        self.assertNotIn('owner', json.loads(data))
        self.assertEqual(self.request('/auth/admin/revoke-user', {'id':self.guest['id']}, self.admin_token, self.admin['csrf'])[0], 200)
        self.assertEqual(self.request('/api/jobs/' + key + '/download', token=self.guest_token)[0], 401)

    def test_logout_and_revoked_invitation(self):
        invite = auth.store.invite('Revoke')
        self.assertEqual(self.request('/auth/admin/revoke-invite', {'id':invite['id']}, self.admin_token, self.admin['csrf'])[0], 200)
        self.assertEqual(self.request('/auth/register', {'token':invite['token'],'username':'newuser','password':'abcdefgh'})[0], 403)
        self.assertEqual(self.request('/auth/logout', {}, self.guest_token, self.guest['csrf'])[0], 200)
        self.assertEqual(self.request('/auth/me', token=self.guest_token)[0], 401)


if __name__ == '__main__':
    unittest.main()
