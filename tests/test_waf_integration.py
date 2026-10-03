"""Exercise real Caddy/Coraza, independently of the app/database test fixtures.

Run: CADDY_WAF_BIN=/path/to/caddy python3 -m unittest discover -s tests -p 'test_waf*.py'
The harness binds only loopback and does not call the production application.
"""
import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]


class _Backend(http.server.BaseHTTPRequestHandler):
    calls = []

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self._reply()

    def do_PUT(self):
        self._reply()

    def _reply(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.calls.append((self.command, self.path, len(body)))
        data = b'a' * (1024 * 1024) if self.path == '/large-response' else b'{"ok":true}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class TestWafIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidate = os.environ.get('CADDY_WAF_BIN')
        if not candidate:
            raise unittest.SkipTest('Set CADDY_WAF_BIN to run real WAF integration tests')
        cls.binary = str(Path(candidate).resolve())
        cls.tmp = tempfile.TemporaryDirectory(prefix='neyro-waf-test-')
        cls.backend = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Backend)
        cls.backend_thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.backend_thread.start()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        env = {
            **os.environ,
            'WAF_DIR': str(ROOT / 'waf'),
            'WAF_TEST_PORT': str(cls.port),
            'WAF_BACKEND_PORT': str(cls.backend.server_port),
            'XDG_DATA_HOME': cls.tmp.name,
            'XDG_CONFIG_HOME': cls.tmp.name,
        }
        cls.log_path = Path(cls.tmp.name) / 'caddy.log'
        cls.log_handle = cls.log_path.open('wb')
        cls.process = subprocess.Popen(
            [cls.binary, 'run', '--config', str(ROOT / 'waf/Caddyfile.test'), '--adapter', 'caddyfile'],
            env=env, stdout=cls.log_handle, stderr=subprocess.STDOUT,
        )
        for _ in range(150):
            if cls.process.poll() is not None:
                cls.log_handle.flush()
                raise RuntimeError(cls.log_path.read_text())
            try:
                with socket.create_connection(('127.0.0.1', cls.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError('Caddy did not listen within 15 seconds')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait(timeout=5)
        cls.backend.shutdown()
        cls.backend.server_close()
        cls.log_handle.close()
        cls.tmp.cleanup()

    def request(self, method='GET', path='/healthz', body=None, content_type=None, headers=None):
        sent_headers = {'Host': 'waf.test', 'User-Agent': 'Neyro-WAF-integration/1.0', **(headers or {})}
        if content_type:
            sent_headers['Content-Type'] = content_type
        if isinstance(body, str):
            body = body.encode()
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            connection.request(method, path, body, sent_headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_benign_telegram_json_and_markup_pass(self):
        data = json.dumps({'text': '<b>Новость</b> — цена 100 ₽. https://example.org/?a=1&b=2'}, ensure_ascii=False)
        status, _ = self.request('POST', '/api/posts/1', data, 'application/json', {
            'X-Init-Data': 'user=%7B%22id%22%3A123%2C%22first_name%22%3A%22Test%22%7D&auth_date=1000&hash=abcdef',
        })
        self.assertEqual(status, 200)

    def test_sql_and_xss_are_blocked_before_backend(self):
        before = len(_Backend.calls)
        query = quote('<script>alert(document.cookie)</script>')
        self.assertEqual(self.request(path='/api/search?q=' + query)[0], 403)
        self.assertEqual(self.request('POST', '/api/channels', json.dumps({'id': "1' OR '1'='1"}), 'application/json')[0], 403)
        self.assertEqual(len(_Backend.calls), before)

    def test_normal_json_body_limit(self):
        self.assertEqual(self.request('POST', '/api/posts/1', json.dumps({'text': 'a' * 64000}), 'application/json')[0], 200)
        before = len(_Backend.calls)
        self.assertEqual(self.request('POST', '/api/posts/1', json.dumps({'text': 'a' * 65536}), 'application/json')[0], 413)
        self.assertEqual(len(_Backend.calls), before)

    def test_png_exception_is_exact_and_bounded(self):
        # This tests transport limits only; the application must decode/validate PNG.
        png = b'\x89PNG\r\n\x1a\n' + b'a' * (100 * 1024)
        self.assertEqual(self.request('POST', '/api/channels/12/logo', png, 'image/png')[0], 200)
        self.assertEqual(self.request('POST', '/api/channels/12/logo', b'a' * (4194304 + 1), 'image/png')[0], 413)
        before = len(_Backend.calls)
        for path in ['/api/channels/12/logo/extra', '/api/channels/nope/logo']:
            self.assertNotEqual(self.request('POST', path, png, 'image/png')[0], 200)
        self.assertNotEqual(self.request('PUT', '/api/channels/12/logo', png, 'image/png')[0], 200)
        self.assertNotEqual(self.request('POST', '/api/channels/12/logo', json.dumps({'text': 'a' * 70000}), 'application/json')[0], 200)
        self.assertEqual(len(_Backend.calls), before)

    def test_malformed_json_is_rejected(self):
        before = len(_Backend.calls)
        self.assertEqual(self.request('POST', '/api/channels', '{"a":', 'application/json')[0], 400)
        self.assertEqual(len(_Backend.calls), before)

    def test_deep_json_is_rejected(self):
        before = len(_Backend.calls)
        data = '{"a":' * 40 + '1' + '}' * 40
        self.assertEqual(self.request('POST', '/api/channels', data, 'application/json')[0], 400)
        self.assertEqual(len(_Backend.calls), before)

    def test_response_body_is_not_bounded_or_buffered_by_waf(self):
        status, data = self.request(path='/large-response')
        self.assertEqual(status, 200)
        self.assertEqual(len(data), 1024 * 1024)

    def test_private_data_is_absent_from_runtime_and_access_logs(self):
        markers = ['private-query-132647', 'private-header-739141', 'private-body-522578']
        attack = {'text': '<script>alert("' + markers[2] + '")</script>'}
        status, _ = self.request('POST', '/api/posts/1?secret=' + markers[0], json.dumps(attack), 'application/json', {'X-Init-Data': markers[1]})
        self.assertEqual(status, 403)
        time.sleep(0.1)
        logs = self.log_path.read_text()
        self.assertIn('Security event; request details redacted', logs)
        self.assertIn('"status":403', logs)
        for marker in markers:
            self.assertNotIn(marker, logs)


if __name__ == '__main__':
    unittest.main()
