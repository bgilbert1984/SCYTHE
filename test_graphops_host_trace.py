import unittest
from unittest.mock import patch

from scythe_orchestrator import (_HOST_LIVENESS_CACHE, _HOST_TRACE_CACHE,
                                 _host_trace_target, _ping_rtt_ms, app)


def snapshot():
    return {
        'graphRevision': 'graph-1',
        'nodes': [
            {'id': 'host:8.8.8.8', 'kind': 'network_host', 'labels': {'ip': '8.8.8.8'}},
            {'id': 'event:1', 'kind': 'network_event', 'labels': {'ip': '1.1.1.1'}},
        ],
    }


class GraphOpsHostTraceTests(unittest.TestCase):
    def setUp(self):
        _HOST_TRACE_CACHE.clear()
        _HOST_LIVENESS_CACHE.clear()

    def test_target_is_resolved_from_current_host_node(self):
        target, max_hops = _host_trace_target(snapshot(), {
            'entityId': 'host:8.8.8.8', 'graphRevision': 'graph-1', 'maxHops': 12,
        })
        self.assertEqual(target, '8.8.8.8')
        self.assertEqual(max_hops, 12)

    def test_ping_rtt_parser_supports_linux_and_windows_forms(self):
        self.assertEqual(_ping_rtt_ms('64 bytes time=11.4 ms'), 11.4)
        self.assertEqual(_ping_rtt_ms('Reply time<1ms TTL=128'), 0.5)

    def test_stale_or_non_host_selections_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'stale'):
            _host_trace_target(snapshot(), {'entityId': 'host:8.8.8.8', 'graphRevision': 'old'})
        with self.assertRaisesRegex(ValueError, 'network_host'):
            _host_trace_target(snapshot(), {'entityId': 'event:1', 'graphRevision': 'graph-1'})

    def test_arbitrary_target_and_multicast_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'unknown host trace fields'):
            _host_trace_target(snapshot(), {
                'entityId': 'host:8.8.8.8', 'graphRevision': 'graph-1', 'target': '127.0.0.1',
            })
        multicast = {'graphRevision': 'graph-1', 'nodes': [
            {'id': 'host:224.0.0.1', 'kind': 'network_host', 'labels': {'ip': '224.0.0.1'}},
        ]}
        with self.assertRaisesRegex(ValueError, 'multicast'):
            _host_trace_target(multicast, {'entityId': 'host:224.0.0.1', 'graphRevision': 'graph-1'})

    @patch('scythe_orchestrator._graphops_directive_authorized', return_value=True)
    @patch('scythe_orchestrator._get_primary_instance_port', return_value=36501)
    @patch('scythe_orchestrator._proxy_post')
    def test_endpoint_runs_bounded_measurements_and_labels_geo_estimates(
            self, proxy_post, _port, _authorized):
        def response(port, path, body, timeout):
            if path.endswith('/selection/resolve'):
                return {'graphRevision': 'graph-1', 'node': snapshot()['nodes'][0]}
            if path.endswith('/probe'):
                return {'status': 'ok', 'rtt_avg_ms': 8.5}
            return {'status': 'ok', 'hops': [
                {'hop': 1, 'ip': '1.1.1.1', 'rtt_ms': 8.5,
                 'geo': {'lat': 37.4, 'lon': -122.1, 'city': 'San Jose'}},
            ], 'tool_used': 'nmap'}
        proxy_post.side_effect = response
        response = app.test_client().post('/api/graphops/host-trace', json={
            'entityId': 'host:8.8.8.8', 'graphRevision': 'graph-1', 'maxHops': 20,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['evidenceClasses']['rtt'], 'MEASURED')
        self.assertEqual(payload['evidenceClasses']['route'], 'MEASURED')
        self.assertEqual(payload['evidenceClasses']['geography'], 'INFERRED')
        self.assertFalse(payload['rawPacketsExposed'])
        self.assertEqual(len(payload['geoPath']), 1)
        self.assertEqual(payload['status'], 'completed')
        self.assertTrue(payload['measurementSummary']['routeMeasured'])

    @patch('scythe_orchestrator._graphops_directive_authorized', return_value=True)
    @patch('scythe_orchestrator._get_primary_instance_port', return_value=36501)
    @patch('scythe_orchestrator._proxy_post')
    def test_synthetic_fallback_is_never_described_as_active_measurement(
            self, proxy_post, _port, _authorized):
        def response(port, path, body, timeout):
            if path.endswith('/selection/resolve'):
                return {'graphRevision': 'graph-1', 'node': snapshot()['nodes'][0]}
            if path.endswith('/probe'):
                return {'status': 'unavailable', 'reason': 'INSUFFICIENT_PRIVILEGE'}
            return {'status': 'simulated', 'simulated': True, 'hops': [
                {'hop': 1, 'ip': '10.0.0.1', 'rtt_ms': 8.5},
            ]}
        proxy_post.side_effect = response
        response = app.test_client().post('/api/graphops/host-trace', json={
            'entityId': 'host:8.8.8.8', 'graphRevision': 'graph-1', 'maxHops': 20,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['evidenceClasses']['route'], 'SYNTHETIC')
        self.assertFalse(payload['measurementSummary']['routeMeasured'])
        self.assertEqual(payload['geoPath'], [])
        self.assertNotIn('ACTIVE MEASUREMENT', payload['boundary'])

    @patch('scythe_orchestrator._measure_host_liveness')
    @patch('scythe_orchestrator._graphops_directive_authorized', return_value=True)
    @patch('scythe_orchestrator._get_primary_instance_port', return_value=36501)
    @patch('scythe_orchestrator._proxy_post')
    def test_liveness_endpoint_probes_only_a_revision_pinned_host(
            self, proxy_post, _port, _authorized, measure):
        proxy_post.return_value = {'graphRevision': 'graph-1', 'node': snapshot()['nodes'][0]}
        measure.return_value = {'state': 'active', 'alive': True, 'rttMs': 7.2,
                                'tool': 'ping', 'evidenceClass': 'MEASURED'}
        response = app.test_client().post('/api/graphops/host-liveness', json={
            'entityId': 'host:8.8.8.8', 'graphRevision': 'graph-1',
        })
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['state'], 'active')
        self.assertTrue(payload['bounded'])
        self.assertFalse(payload['rawPacketsExposed'])
        measure.assert_called_once_with('8.8.8.8')


if __name__ == '__main__':
    unittest.main()
