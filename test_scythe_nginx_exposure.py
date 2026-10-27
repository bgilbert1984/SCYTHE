"""The repo's nginx config must not hand out private files, with or without the app's own policy.

The server block is rendered from deploy/nginx-spectrcyde-auth.conf with only the
listener, TLS and upstream ports rewritten, then run on a loopback port with a
throwaway prefix. Two upstreams are used:
  - a leaky one that returns a marker for every path (the application layer bypassed),
    which isolates the nginx rule;
  - the real orchestrator app, for the end-to-end path through the /SCYTHE/ alias.
Skipped when nginx is not installed.
"""
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from werkzeug.serving import make_server

MARKER = b'SYNTHETIC-PRIVATE-MARKER-7f3a'
HERE = Path(__file__).resolve().parent
CONF = HERE / 'deploy' / 'nginx-spectrcyde-auth.conf'
NGINX = shutil.which('nginx') or ('/usr/sbin/nginx' if Path('/usr/sbin/nginx').exists() else None)
PRIVATE = ['zz_nx_operator_sessions.db', 'zz_nx_operator_sessions.db-wal', 'zz_nx.sqlite3',
           'zz_nx.log', 'zz_nx.env', 'zz_nx.pem', 'zz_nx.key', 'zz_nx.py', 'zz_nx.sh',
           'zz_nx.bak', 'zz_nx.sql', 'zz_nx.pcap', 'ZZ_NX_OPERATOR_SESSIONS.DB']
PREFIXES = ['/SCYTHE/', '/assets/', '/scythe-web/', '/docs/']


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _serve(app_callable):
    port = _free_port()
    server = make_server('127.0.0.1', port, app_callable, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def _leaky_app(environ, start_response):
    start_response('200 OK', [('Content-Type', 'application/octet-stream')])
    return [MARKER]


def _wordpress_app(environ, start_response):
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b'wordpress']


def _render(listen_port, orchestrator_port, wordpress_port, workdir):
    text = CONF.read_text()
    server = text[text.index('server {'):]
    server = re.sub(r'\n\s*listen[^\n]*', '', server)
    server = re.sub(r'\n\s*(ssl_certificate|ssl_certificate_key|ssl_dhparam|include /etc/letsencrypt)[^\n]*', '', server)
    server = re.sub(r'\n\s*include /etc/nginx/snippets/[^\n]*', '', server)
    server = server.replace('server_name', 'listen 127.0.0.1:%d;\n    server_name' % listen_port, 1)
    server = server.replace('127.0.0.1:5001', '127.0.0.1:%d' % orchestrator_port)
    server = server.replace('127.0.0.1:8080', '127.0.0.1:%d' % wordpress_port)
    return ('worker_processes 1;\ndaemon off;\npid %(w)s/nginx.pid;\nerror_log %(w)s/error.log;\n'
            'events { worker_connections 64; }\nhttp {\n'
            '  access_log off; client_body_temp_path %(w)s/b; proxy_temp_path %(w)s/p;\n'
            '  fastcgi_temp_path %(w)s/f; uwsgi_temp_path %(w)s/u; scgi_temp_path %(w)s/s;\n'
            '  map $http_upgrade $connection_upgrade { default upgrade; "" close; }\n%(s)s\n}\n'
            % {'w': workdir, 's': server})


class _NginxHarness:
    upstream_port = None

    @classmethod
    def start_nginx(cls, upstream_port):
        cls.tmp = tempfile.mkdtemp(prefix='nx_exposure_')
        cls.wp, wp_port = _serve(_wordpress_app)
        cls.port = _free_port()
        conf = Path(cls.tmp) / 'nginx.conf'
        conf.write_text(_render(cls.port, upstream_port, wp_port, cls.tmp))
        check = subprocess.run([NGINX, '-t', '-p', cls.tmp, '-c', str(conf)],
                               capture_output=True, text=True)
        if check.returncode:
            raise AssertionError('nginx -t failed: ' + check.stderr)
        cls.proc = subprocess.Popen([NGINX, '-p', cls.tmp, '-c', str(conf)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(('127.0.0.1', cls.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise AssertionError('nginx did not start')

    @classmethod
    def stop_nginx(cls):
        cls.proc.terminate()
        cls.proc.wait(10)
        cls.wp.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path):
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d%s' % (self.port, path), timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()


@unittest.skipUnless(NGINX, 'nginx not installed')
class NginxRuleTests(_NginxHarness, unittest.TestCase):
    """Upstream returns the marker for everything: only the nginx rule can stop it."""

    @classmethod
    def setUpClass(cls):
        cls.up, up_port = _serve(_leaky_app)
        cls.start_nginx(up_port)

    @classmethod
    def tearDownClass(cls):
        cls.stop_nginx()
        cls.up.shutdown()

    def test_private_extensions_blocked_under_every_proxied_prefix(self):
        for prefix in PREFIXES:
            for name in PRIVATE:
                status, body = self.get(prefix + name)
                self.assertNotIn(MARKER, body, prefix + name)
                self.assertEqual(status, 404, prefix + name)

    def test_nested_subdirectory_blocked(self):
        status, body = self.get('/SCYTHE/a/b/zz_nx.db')
        self.assertEqual((status, MARKER in body), (404, False))

    def test_non_private_extension_still_proxied(self):
        for prefix in PREFIXES:
            status, body = self.get(prefix + 'zz_nx_public.js')
            self.assertEqual((status, body), (200, MARKER), prefix)


@unittest.skipUnless(NGINX, 'nginx not installed')
class NginxEndToEndTests(_NginxHarness, unittest.TestCase):
    """Real orchestrator behind nginx, through the /SCYTHE/ alias."""

    @classmethod
    def setUpClass(cls):
        from scythe_orchestrator import app
        for name in PRIVATE + ['zz_nx_public.js']:
            (HERE / name).write_bytes(MARKER if name != 'zz_nx_public.js' else b'/* public */')
        cls.up, up_port = _serve(app)
        cls.start_nginx(up_port)

    @classmethod
    def tearDownClass(cls):
        cls.stop_nginx()
        cls.up.shutdown()
        for name in PRIVATE + ['zz_nx_public.js']:
            try:
                (HERE / name).unlink()
            except FileNotFoundError:
                pass

    def test_private_files_not_returned_through_alias(self):
        for prefix in PREFIXES + ['/']:
            for name in PRIVATE:
                status, body = self.get(prefix + name)
                self.assertNotIn(MARKER, body, prefix + name)

    def test_public_asset_served_through_alias(self):
        self.assertEqual(self.get('/SCYTHE/zz_nx_public.js'), (200, b'/* public */'))


if __name__ == '__main__':
    unittest.main()
