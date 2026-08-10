import unittest

from rf_scythe_api_server import _parse_tracepath_output, _ping_permission_failure


class NetworkMeasurementParsingTests(unittest.TestCase):
    def test_tracepath_parser_ignores_local_metadata_and_deduplicates_hops(self):
        output = """ 1?: [LOCALHOST] pmtu 1500
 1:  172.28.208.1  0.362ms
 1:  172.28.208.1  0.262ms
 2:  172.16.101.254  7.215ms
 3:  no reply
     Too many hops: pmtu 1500
"""
        self.assertEqual(_parse_tracepath_output(output), [
            {'hop': 1, 'ip': '172.28.208.1', 'rtt_ms': 0.262},
            {'hop': 2, 'ip': '172.16.101.254', 'rtt_ms': 7.215},
        ])

    def test_ping_permission_failure_is_not_mislabeled_as_unreachable(self):
        self.assertTrue(_ping_permission_failure(
            'ping: socktype: SOCK_RAW\nping: missing cap_net_raw+p capability or setuid?'))
        self.assertFalse(_ping_permission_failure('5 packets transmitted, 0 received'))


if __name__ == '__main__':
    unittest.main()
