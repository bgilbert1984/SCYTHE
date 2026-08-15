import json
import time
import unittest
from unittest.mock import patch

from graphops_full_fidelity import (build_full_fidelity_capsule, disclosure_receipt,
                                    evaluate_evidence_compatibility, validate_cloud_report)
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

    def test_question_evidence_compatibility_refuses_missing_temporal_and_sample_claims(self):
        trace, resolved = evidence_fixture()
        question = ('Identify conclusions using stale evidence; expose every inference made from absence; '
                    'test whether quantization or interpolation explains this anomaly')
        capsule = build_full_fidelity_capsule(question, {
            'kind': 'graph-node', 'entityId': 'host:20.189.172.33',
            'graphRevision': 'graph-exact'}, resolved, trace)
        self.assertFalse(capsule['evidenceCompatibility']['compatible'])
        classes = {item['class'] for item in capsule['evidenceCompatibility']['missing']}
        self.assertEqual(classes, {'TEMPORAL_FRESHNESS', 'SENSOR_NEGATIVE_EVIDENCE',
                                   'QUANTIZATION_PROVENANCE', 'INTERPOLATION_PROVENANCE'})
        report = validate_cloud_report({
            'situation': 'A claim', 'anomalies': 'An anomaly', 'measuredVsInferred': 'Mixed',
            'assessment': 'Certain', 'falsifier': 'Repeat', 'direction': 'Measure', 'confidence': .9,
        }, capsule)
        self.assertIn('QUESTION-EVIDENCE MISMATCH', report['situation'])
        self.assertIn('INSUFFICIENT COMPATIBLE EVIDENCE', report['assessment'])
        self.assertEqual(report['confidence'], .1)

    def test_infrastructure_evidence_is_disclosed_with_explicit_classes(self):
        trace, resolved = evidence_fixture()
        infrastructure = {'schemaVersion': 'graphops.infrastructure.v1', 'domains': [{'id': 'asn:8075'}],
                          'observedFlows': [{'id': 'flow:1', 'evidenceClass': 'OBSERVED'}],
                          'modeledPathCandidates': [{'id': 'path:1', 'evidenceClass': 'MODELED_CANDIDATE'}]}
        capsule = build_full_fidelity_capsule('Explain infrastructure', {
            'kind': 'graph-node', 'entityId': 'host:20.189.172.33',
            'graphRevision': 'graph-exact'}, resolved, trace, infrastructure)
        self.assertTrue(capsule['evidenceCompatibility']['compatible'])
        receipt = disclosure_receipt(capsule, 'gpt-oss:20b')
        self.assertEqual(receipt['disclosed']['infrastructureDomains'], 1)
        self.assertEqual(receipt['disclosed']['observedInfrastructureFlows'], 1)

    def test_cloud_infrastructure_is_selection_focused_exact_and_hash_bound(self):
        trace, resolved = evidence_fixture()
        paths = [{'id': f'ris-{index}', 'prefix': f'20.{index}.0.0/16', 'originAsn': 8075,
                  'collectorId': 'rrc20', 'evidenceClass': 'CONTROL_PLANE_OBSERVATION'}
                 for index in range(40)]
        infrastructure = {'schemaVersion': 'graphops.infrastructure.v1', 'graphRevision': 'graph-exact',
            'domains': [
                {'id': 'asn:8075', 'asn': 8075, 'observedHostIds': ['host:20.189.172.33'],
                 'prefixes': ['20.0.0.0/8']},
                {'id': 'asn:54113', 'asn': 54113, 'observedHostIds': ['host:151.101.1.91'],
                 'prefixes': ['151.101.0.0/16']}],
            'observedFlows': [{'id': 'flow-1', 'sourceDomain': 'asn:8075', 'targetDomain': 'asn:54113'}],
            'peeringdbEvidence': {'datasetRevision': 'pdb-1', 'networks': [
                {'asn': 8075}, {'asn': 54113}]},
            'controlPlaneEvidence': {'snapshotRevision': 'ris-1', 'controlPlanePaths': paths},
            'infrastructureContradictions': {'schemaVersion': 'graphops.infrastructure-contradictions.v1',
                                              'findings': [], 'changes': [], 'withheld': []}}
        capsule = build_full_fidelity_capsule('Explain infrastructure', {
            'kind': 'graph-node', 'entityId': 'host:20.189.172.33',
            'graphRevision': 'graph-exact'}, resolved, trace, infrastructure)
        frame = capsule['infrastructureEvidence']; projection = frame['capsuleProjection']
        self.assertEqual(projection['mode'], 'SELECTION_FOCUSED_EXACT_RECORDS')
        self.assertEqual(len(frame['controlPlaneEvidence']['controlPlanePaths']), 32)
        self.assertEqual(projection['omittedCounts']['controlPlanePaths'], 8)
        self.assertEqual(len(projection['sourceSnapshotSha256']), 64)
        self.assertEqual(frame['controlPlaneEvidence']['controlPlanePaths'][-1]['id'], 'ris-39')
        receipt = disclosure_receipt(capsule, 'gpt-oss:20b')
        self.assertEqual(receipt['capsuleProjection']['omittedCounts']['controlPlanePaths'], 8)

    def test_control_plane_and_declared_questions_require_their_exact_layers(self):
        trace, resolved = evidence_fixture()
        infrastructure = {'schemaVersion': 'graphops.infrastructure.v1', 'domains': [
                          {'id': 'asn:8075', 'asn': 8075, 'prefixes': ['20.0.0.0/8'],
                           'observedHostIds': ['host:20.189.172.33']}], 'observedFlows': [],
                          'peeringdbEvidence': {'networks': [{'asn': 8075}]},
                          'controlPlaneEvidence': {'controlPlanePaths': [{
                              'prefix': '20.0.0.0/8', 'collectorId': 'rrc20',
                              'originAsn': 8075,
                              'evidenceClass': 'CONTROL_PLANE_OBSERVATION'}]}}
        capsule = build_full_fidelity_capsule('Compare BGP RIS evidence with PeeringDB facility presence', {
            'kind': 'graph-node', 'entityId': 'host:20.189.172.33',
            'graphRevision': 'graph-exact'}, resolved, trace, infrastructure)
        self.assertTrue(capsule['evidenceCompatibility']['compatible'])
        self.assertIn('CONTROL_PLANE', capsule['evidenceCompatibility']['available'])
        self.assertIn('PEERINGDB_DECLARED', capsule['evidenceCompatibility']['available'])

    def test_contradiction_evidence_is_disclosed_without_promoting_a_verdict(self):
        trace, resolved = evidence_fixture()
        infrastructure = {'schemaVersion': 'graphops.infrastructure.v1', 'domains': [],
                          'observedFlows': [], 'infrastructureContradictions': {
                              'schemaVersion': 'graphops.infrastructure-contradictions.v1',
                              'findings': [{'kind': 'ORIGIN_DISAGREEMENT', 'status': 'UNRESOLVED',
                                            'boundary': 'NOT A HIJACK DETERMINATION'}],
                              'changes': [{'kind': 'ORIGIN_CHANGE_OBSERVED'}],
                              'withheld': [{'kind': 'ABSENCE_INFERENCE_WITHHELD'}]}}
        capsule = build_full_fidelity_capsule('Explain this source disagreement', {
            'kind': 'graph-node', 'entityId': 'host:20.189.172.33',
            'graphRevision': 'graph-exact'}, resolved, trace, infrastructure)
        self.assertTrue(capsule['evidenceCompatibility']['compatible'])
        full_receipt = disclosure_receipt(capsule, 'gpt-oss:20b')
        receipt = full_receipt['disclosed']
        self.assertEqual(receipt['infrastructureContradictions'], 1)
        self.assertEqual(receipt['controlPlaneChanges'], 0)
        self.assertEqual(full_receipt['capsuleProjection']['omittedCounts']['controlPlaneChanges'], 1)
        self.assertEqual(receipt['withheldInfrastructureTests'], 1)

    def test_validator_removes_rtt_distance_path_end_and_topology_absence_promotions(self):
        trace, resolved = evidence_fixture()
        capsule = build_full_fidelity_capsule('Explain route anomalies', {
            'kind': 'graph-node', 'entityId': 'host:20.189.172.33',
            'graphRevision': 'graph-exact'}, resolved, trace)
        report = validate_cloud_report({
            'situation': 'The route reaches an Amazon edge node.',
            'anomalies': 'The last responding hop was not the target.',
            'measuredVsInferred': 'RTTs under 40 ms indicate a short-haul path.',
            'assessment': 'There is no evidence of a VPN or long-haul relay.',
            'falsifier': 'A trace would falsify the claim that the path ends at the Comcast edge.',
            'direction': 'Run repeated traceroutes.', 'confidence': .8,
        }, capsule)
        self.assertIn('TRACEROUTE-TERMINATION CLAIM REMOVED', report['situation'])
        self.assertIn('RTT-TO-DISTANCE PROMOTION REMOVED', report['measuredVsInferred'])
        self.assertIn('TOPOLOGY-ABSENCE CLAIM WITHHELD', report['assessment'])
        self.assertIn('TRACEROUTE-TERMINATION CLAIM REMOVED', report['falsifier'])
        self.assertEqual(report['confidence'], .2)
        self.assertTrue(any(item.startswith('RTT_DISTANCE_PROMOTION_REMOVED')
                            for item in report['validationConstraints']))

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
