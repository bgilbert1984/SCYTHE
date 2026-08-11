import unittest

from rf_scythe_api_server import (_parse_ping_measurement, _parse_tracepath_output,
                                  _ping_permission_failure)


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

    def test_ping_parser_supports_linux_and_windows_measurements(self):
        linux = _parse_ping_measurement(
            '64 bytes time=11.4 ms\n3 received\nrtt min/avg/max/mdev = 10.1/11.4/12.7/1.3 ms')
        self.assertEqual(linux, {'samples': [11.4], 'received': 3, 'min': 10.1,
                                 'avg': 11.4, 'max': 12.7, 'mdev': 1.3})
        windows = _parse_ping_measurement(
            'Reply from 8.8.8.8: time=69ms\nReply from 8.8.8.8: time<1ms\n'
            'Packets: Sent = 2, Received = 2, Lost = 0\n'
            'Minimum = 0ms, Maximum = 69ms, Average = 34ms')
        self.assertEqual(windows, {'samples': [69.0, 0.5], 'received': 2,
                                   'min': 0.0, 'avg': 34.0, 'max': 69.0, 'mdev': None})


if __name__ == '__main__':
    unittest.main()
