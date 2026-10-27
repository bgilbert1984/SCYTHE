"""Anonymous requests must not retrieve private files from either Flask app.

A synthetic marker file is written into the served directory under names a real
deployment would hold (databases, WAL files, logs, env files, scripts). The
tests assert the marker bytes never appear in a response, so a redirect or an
HTML error page cannot pass for containment.
"""
import os
import unittest
from pathlib import Path

from scythe_static_policy import PUBLIC_EXTENSIONS, is_public_static

MARKER = b'SYNTHETIC-PRIVATE-MARKER-7f3a'
HERE = Path(__file__).resolve().parent
PRIVATE_NAMES = [
    'zz_probe_operator_sessions.db', 'zz_probe_operator_sessions.db-wal',
    'zz_probe_operator_sessions.db-shm', 'zz_probe.sqlite', 'zz_probe.sqlite3',
    'zz_probe.log', 'zz_probe.env', 'zz_probe.pem', 'zz_probe.key', 'zz_probe.py',
    'zz_probe.sh', 'zz_probe.yml', 'zz_probe.bak', 'zz_probe.sql', 'zz_probe.pcap',
    '.zz_probe_dotfile', 'zz_probe_secrets.json', 'zz_probe_session_dump.json',
    'zz_probe_token.txt',
]
PUBLIC_NAME = 'zz_probe_public.js'


def _write_probes():
    for name in PRIVATE_NAMES:
        (HERE / name).write_bytes(MARKER)
    (HERE / PUBLIC_NAME).write_bytes(b'/* public */')


def _remove_probes():
    for name in PRIVATE_NAMES + [PUBLIC_NAME]:
        try:
            (HERE / name).unlink()
        except FileNotFoundError:
            pass


class PolicyTests(unittest.TestCase):
    def test_private_names_rejected(self):
        for name in PRIVATE_NAMES:
            self.assertFalse(is_public_static(name), name)

    def test_traversal_and_odd_paths_rejected(self):
        for name in ['', '/etc/x.js', '../a.html', 'a/../b.html', '/etc/passwd', 'a//b.js',
                     'a\\b.js', 'a\x00.js', 'a/./b.js', 'dir/']:
            self.assertFalse(is_public_static(name), repr(name))

    def test_hidden_and_private_directories_rejected(self):
        for name in ['.git/config.js', 'x/.hidden/a.js', '__pycache__/a.js',
                     'venv/lib/a.js', '.venv/a.js', 'tests/a.js', 'deploy/a.js',
                     'tools/a.js', 'logs/a.txt', 'Venv/a.js', 'TESTS/a.js']:
            self.assertFalse(is_public_static(name), name)

    def test_extension_is_case_insensitive(self):
        self.assertFalse(is_public_static('a.DB'))
        self.assertTrue(is_public_static('a.JS'))

    def test_normal_assets_allowed(self):
        for name in ['login.html', 'scythe-web/lifecycle.js', 'docs/x.md',
                     'assets/a/b.png', 'cesium/Widgets/widgets.css', 'data/x.json']:
            self.assertTrue(is_public_static(name), name)

    def test_allowlist_has_no_database_or_script_extension(self):
        for ext in ('.db', '.sqlite', '.sqlite3', '.log', '.env', '.py', '.sh',
                    '.pem', '.key', '.yml', '.yaml', '.sql', '.bak', '.pcap'):
            self.assertNotIn(ext, PUBLIC_EXTENSIONS)


class _AppCase:
    app = None
    prefixes = ('/',)

    @classmethod
    def setUpClass(cls):
        _write_probes()
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls):
        _remove_probes()

    def test_private_files_not_returned(self):
        for prefix in self.prefixes:
            for name in PRIVATE_NAMES:
                for path in (prefix + name, prefix + name.upper()):
                    resp = self.client.get(path)
                    self.assertNotIn(MARKER, resp.get_data(), path)
                    self.assertEqual(resp.status_code, 404, path)

    def test_traversal_variants_not_returned(self):
        for path in ('/%2e%2e/zz_probe.env', '/./zz_probe.env', '/a/../zz_probe.env',
                     '//zz_probe.env', '/zz_probe.env%00.js', '/zz_probe.env/'):
            resp = self.client.get(path)
            self.assertNotIn(MARKER, resp.get_data(), path)

    def test_head_and_post_do_not_leak(self):
        resp = self.client.head('/zz_probe_operator_sessions.db')
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post('/zz_probe_operator_sessions.db')
        self.assertNotIn(MARKER, resp.get_data())

    def test_missing_private_and_existing_private_look_alike(self):
        a = self.client.get('/zz_probe_operator_sessions.db')
        b = self.client.get('/zz_no_such_file.db')
        # the 404 body may echo the requested path; nothing else may differ
        body = lambda r, n: r.get_data().replace(n.encode(), b'<path>')
        self.assertEqual((a.status_code, body(a, 'zz_probe_operator_sessions.db')),
                         (b.status_code, body(b, 'zz_no_such_file.db')))

    def test_public_asset_still_served(self):
        resp = self.client.get('/' + PUBLIC_NAME)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_data(), b'/* public */')
        resp.close()


class OrchestratorExposureTests(_AppCase, unittest.TestCase):
    prefixes = ('/', '/static/')

    @classmethod
    def setUpClass(cls):
        from scythe_orchestrator import app
        cls.app = app
        super().setUpClass()

    def test_static_prefix_public_asset_still_served(self):
        resp = self.client.get('/static/' + PUBLIC_NAME)
        self.assertEqual(resp.status_code, 200)
        resp.close()


class InstanceServerExposureTests(unittest.TestCase):
    """Run in a child process: importing rf_scythe_api_server applies the eventlet
    monkey patch, which would change threading for every test sharing this process."""

    def test_instance_server_child_process(self):
        import subprocess
        import sys
        child = subprocess.run(
            [sys.executable, '-m', 'unittest', 'test_scythe_static_exposure._InstanceServerChild'],
            cwd=str(HERE), capture_output=True, text=True, timeout=240,
            env=dict(os.environ, SCYTHE_EXPOSURE_CHILD='1'))
        if 'SKIPPED-IMPORT' in child.stderr:
            self.skipTest('instance server not importable here')
        self.assertEqual(child.returncode, 0, child.stderr[-3000:])
        self.assertIn('Ran 5 tests', child.stderr)


@unittest.skipUnless(os.environ.get('SCYTHE_EXPOSURE_CHILD') == '1', 'child process only')
class _InstanceServerChild(_AppCase, unittest.TestCase):
    prefixes = ('/', '/static/')

    @classmethod
    def setUpClass(cls):
        try:
            from rf_scythe_api_server import app
        except ImportError as exc:  # optional runtime deps missing in this env
            import sys
            sys.stderr.write('SKIPPED-IMPORT %s\n' % exc)
            raise unittest.SkipTest(str(exc))
        cls.app = app
        super().setUpClass()


if __name__ == '__main__':
    unittest.main()
