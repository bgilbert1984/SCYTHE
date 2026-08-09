import unittest

import requests

from scythe_orchestrator import _bootstrap_instance_when_ready


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')

    def json(self):
        return self.payload


class _Session:
    def __init__(self, counts, fail_first_get=False):
        self.counts = iter(counts)
        self.fail_first_get = fail_first_get
        self.get_calls = 0
        self.posts = []

    def get(self, url, timeout):
        self.get_calls += 1
        if self.fail_first_get and self.get_calls == 1:
            raise requests.ConnectionError('listener not ready')
        return _Response({'count': next(self.counts)})

    def post(self, url, json, timeout):
        self.posts.append((url, json))
        return _Response({'instance_id': 'scythe-bootstrap'}, status=201)


class BootstrapInstanceTests(unittest.TestCase):
    def test_existing_registry_is_left_untouched(self):
        session = _Session([1])
        ok = _bootstrap_instance_when_ready('http://127.0.0.1:5001', 'Live GraphOps',
                                            session=session, timeout=1, sleep=lambda _: None)
        self.assertTrue(ok)
        self.assertEqual(session.posts, [])

    def test_waits_for_listener_then_creates_exactly_one_child(self):
        session = _Session([0], fail_first_get=True)
        ticks = iter([0.0, 0.1, 0.2, 0.3])
        ok = _bootstrap_instance_when_ready('http://127.0.0.1:5001/', 'Live GraphOps',
                                            session=session, timeout=1, sleep=lambda _: None,
                                            monotonic=lambda: next(ticks))
        self.assertTrue(ok)
        self.assertEqual(len(session.posts), 1)
        self.assertEqual(session.posts[0][1], {'name': 'Live GraphOps'})


if __name__ == '__main__':
    unittest.main()
