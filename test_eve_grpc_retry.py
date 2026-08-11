import unittest

import requests

from scythe_grpc_server import EveForwardRejected, _forward_eve_chunk_with_retry


class _Context:
    def is_active(self):
        return True


class _Response:
    def __init__(self, status, payload=None, text=''):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class EveGrpcRetryTests(unittest.TestCase):
    def test_transient_boot_failures_retain_batch_until_graph_is_ready(self):
        responses = [requests.ConnectionError('booting'), _Response(503),
                     _Response(200, {'committed': 1})]
        calls = []

        def post(*args, **kwargs):
            calls.append(kwargs['json'])
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        result = _forward_eve_chunk_with_retry(
            'http://127.0.0.1/events', {}, [{'event_id': 'stable'}], _Context(),
            post=post, sleep=lambda _: None)
        self.assertEqual(result['committed'], 1)
        self.assertEqual(calls, [{'events': [{'event_id': 'stable'}]}] * 3)

    def test_validation_failure_is_not_retried(self):
        with self.assertRaises(EveForwardRejected):
            _forward_eve_chunk_with_retry(
                'http://127.0.0.1/events', {}, [{}], _Context(),
                post=lambda *args, **kwargs: _Response(400, text='invalid event'),
                sleep=lambda _: None)


if __name__ == '__main__':
    unittest.main()
