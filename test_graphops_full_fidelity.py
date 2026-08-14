import json
import time
import unittest
from unittest.mock import patch

from graphops_full_fidelity import (build_full_fidelity_capsule, disclosure_receipt,
                                    validate_cloud_report)
from scythe_orchestrator import (_HOST_TRACE_EVIDENCE, app)


def evidence_fixture():
    trace = {
        'evidenceId': 'trace-test', 'target': '20.189.172.33', 'capturedAt': 1770000000.125,
        'selection': {'entityId': 'host:20.189.172.33', 'graphRevision': 'graph-exact'},
        'probe': {'status': 'ok', 'rtt_avg_ms': 26.0, 'authorization': 'must-not-leave'},
        'traceroute': {'status': 'ok', 'tool_used': 'nmap', 'hops': [
            {'hop': 1, 'ip': '172.28.208.1', 'rtt_ms': 1.01,
             'anomaly': 'private_backbone', 'packet_payload': 'must-not-leave'},
            {'hop': 2, 'ip': '104.44.22.146', 'rtt_ms': 34.17,
             'geo': {'city': 'Redmond', 'lat': 47.674, 'lon': -122.121}},
        ]},
        'evidenceClasses': {'target': 'OBSERVED', 'rtt': 'MEASURED',
                            'route': 'MEASURED', 'geography': 'INFERRED'},
        'measurementSummary': {'rttMeasured': True, 'routeMeasured': True},
        'boundary': 'GEOIP IS INFERRED', 'rawPacketsExposed': False,
    }
    resolved = {
        'graphRevision': 'graph-exact',
        'node': {'id': 'host:20.189.172.33', 'kind': 'network_host',
                 'labels': {'ip': '20.189.172.33', 'asn': 8075, 'org': 'Microsoft',
                            'note': 'Bearer must-not-leave'},
                 'metadata': {'api_key': 'must-not-leave', 'raw': 'engine internal'}},
        'incidentEdges': [{'id': 'edge:1', 'kind': 'network_flow',
                           'source': 'host:local', 'target': 'host:20.189.172.33',
                           'labels': {'last_seen': '2026-08-12T06:49:00.097Z',
                                      'cookie': 'must-not-leave'}}],
        'memberNodes': [],
    }
    return trace, resolved


class FullFidelityCapsuleTests(unittest.TestCase):
    def setUp(self):
        _HOST_TRACE_EVIDENCE.clear()

    def test_capsule_preserves_exact_facts_and_evidence_classes_but_not_secrets(self):
        trace, resolved = evidence_fixture()
        capsule = build_full_fidelity_capsule(
            'Explain this path', {'kind': 'graph-node', 'entityId': 'host:20.189.172.33',
                                  'graphRevision': 'graph-exact'}, resolved, trace)
        encoded = json.dumps(capsule, sort_keys=True)
        self.assertIn('20.189.172.33', encoded)
        self.assertIn('104.44.22.146', encoded)
        self.assertIn('47.674', encoded)
        self.assertIn('2026-08-12T06:49:00.097Z', encoded)
        self.assertIn('MEASURED', encoded)
        self.assertIn('INFERRED', encoded)
        self.assertNotIn('must-not-leave', encoded)
        self.assertNotIn('engine internal', encoded)
        self.assertEqual(len(capsule['sha256']), 64)
        receipt = disclosure_receipt(capsule, 'gpt-oss:20b')
        self.assertEqual(receipt['disclosed']['exactIpAddresses'], 3)
        self.assertEqual(receipt['disclosed']['exactLocations'], 1)
        self.assertFalse(receipt['directiveExecution'])

    def test_cloud_guardrails_remove_geoip_itineraries_and_single_trace_causes(self):
        trace, resolved = evidence_fixture()
        trace['traceroute']['hops'][1]['physics_anomaly'] = {
            'type': 'relay_chain', 'evidence_class': 'DERIVED_INFERENCE'}
        capsule = build_full_fidelity_capsule(
            'Explain', {'kind': 'graph-node', 'entityId': 'host:20.189.172.33',
                        'graphRevision': 'graph-exact'}, resolved, trace)
        report = validate_cloud_report({
            'situation': 'The path likely traverses Seattle and Virginia before it returns.',
            'anomalies': 'One non-monotonic response',
            'measuredVsInferred': 'RTT measured and GeoIP inferred',
            'assessment': 'The RTT spike suggests transient congestion and a routing change.',
            'falsifier': 'Repeat the trace', 'direction': 'analysis', 'confidence': .88,
        }, capsule)
        self.assertIn('UNSUPPORTED PHYSICAL-ROUTE CLAIM REMOVED', report['situation'])
        self.assertIn('UNCORROBORATED TIMING-CAUSE CLAIM REMOVED', report['assessment'])
        self.assertIn('Run repeated fixed-flow traceroutes', report['direction'])
        self.assertEqual(report['confidence'], .25)
        self.assertIn('NON_ACTIONABLE_DIRECTION_REPLACED', report['validationConstraints'])
        self.assertIn('DERIVED_PHYSICS_WARNING_CONFIDENCE_CEILING_0.50',
                      report['validationConstraints'])

    @patch('graphops_full_fidelity.ask_ollama_cloud')
    @patch('scythe_orchestrator._graphops_directive_authorized', return_value=True)
    def test_endpoint_requires_acknowledgement_and_matching_server_evidence(self, _authorized, ask_cloud):
        trace, resolved = evidence_fixture()
        _HOST_TRACE_EVIDENCE['trace-test'] = {
            'capturedAt': time.time(), 'result': trace, 'resolved': resolved,
        }
        body = {'mode': 'cloud-full-fidelity', 'question': 'Explain this path',
                'evidenceId': 'trace-test', 'selection': {
                    'kind': 'graph-node', 'entityId': 'host:20.189.172.33',
                    'graphRevision': 'graph-exact'}}
        response = app.test_client().post(
            '/api/graphops/conversation/cloud-full-fidelity', json=body)
        self.assertEqual(response.status_code, 400)
        ask_cloud.assert_not_called()

        ask_cloud.return_value = {'model': 'gpt-oss:20b', 'report': {
            'situation': 'Measured route', 'anomalies': 'None',
            'measuredVsInferred': 'RTT measured; geography inferred',
            'assessment': 'Bounded', 'falsifier': 'Repeat trace',
            'direction': 'Measure again', 'confidence': .7}}
        response = app.test_client().post(
            '/api/graphops/conversation/cloud-full-fidelity',
            json={**body, 'acknowledgeExactDisclosure': True})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['ollamaRoute'], 'OLLAMA_CLOUD_FULL_FIDELITY')
        self.assertEqual(payload['disclosureReceipt']['disclosed']['exactIpAddresses'], 3)
        self.assertFalse(payload['directiveExecution'])
        outbound = ask_cloud.call_args.args[0]
        self.assertEqual(outbound['hostTrace']['target'], '20.189.172.33')

        response = app.test_client().post(
            '/api/graphops/conversation/cloud-full-fidelity', json={**body,
                'acknowledgeExactDisclosure': True,
                'selection': {**body['selection'], 'graphRevision': 'graph-wrong'}})
        self.assertEqual(response.status_code, 409)


if __name__ == '__main__':
    unittest.main()
