import json
import unittest
from unittest.mock import patch

from scythe_orchestrator import app


class GraphOpsWorkbenchTests(unittest.TestCase):
    @patch('scythe_orchestrator._graphops_directive_authorized', return_value=True)
    @patch('scythe_orchestrator._get_primary_instance_port', return_value=40837)
    @patch('scythe_orchestrator._proxy_post')
    def test_semantic_panel_is_selection_bounded(self, proxy_post, _port, _authorized):
        proxy_post.side_effect = [
            {'jsonrpc': '2.0', 'id': 'x', 'result': {'content': [{
                'type': 'text', 'text': json.dumps({'clusters': [], 'total_vectors': 3})}]}},
            {'jsonrpc': '2.0', 'id': 'y', 'result': {'content': [{
                'type': 'text', 'text': json.dumps({'results': []})}]}},
        ]
        response = app.test_client().post('/api/graphops/workbench', json={
            'panel': 'semantic',
            'selection': {'kind': 'graph-node', 'entityId': 'host:203.0.113.8',
                          'graphRevision': 'graph-rev-1'},
        })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['bounded'])
        self.assertTrue(body['readOnly'])
        self.assertEqual(body['selection']['graphRevision'], 'graph-rev-1')
        self.assertEqual([item['tool'] for item in body['records']],
                         ['get_semantic_clusters', 'search_similar_entities'])
        second_rpc = proxy_post.call_args_list[1].args[2]
        self.assertEqual(second_rpc['params']['arguments']['query'], 'host:203.0.113.8')

    @patch('scythe_orchestrator._graphops_directive_authorized', return_value=True)
    def test_arbitrary_tool_names_are_not_accepted(self, _authorized):
        response = app.test_client().post('/api/graphops/workbench', json={
            'panel': 'spectrum', 'tool': 'rf_tune',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('unknown workbench fields', response.get_json()['error'])

    @patch('scythe_orchestrator._graphops_directive_authorized', return_value=True)
    @patch('scythe_orchestrator._get_primary_instance_port', return_value=40837)
    @patch('scythe_orchestrator._proxy_post')
    def test_spectrum_mutations_are_described_but_never_called(self, proxy_post, _port, _authorized):
        proxy_post.return_value = {'jsonrpc': '2.0', 'id': 'x', 'result': {}}
        response = app.test_client().post('/api/graphops/workbench', json={'panel': 'spectrum'})
        self.assertEqual(response.status_code, 200)
        called = [call.args[2]['params']['name'] for call in proxy_post.call_args_list]
        self.assertEqual(called, [
            'rf_bridge_status', 'rf_spectrum_snapshot', 'rf_observations_query',
            'rf_sparse_status', 'rf_sparse_supports_query',
        ])
        proposed = [item['tool'] for item in response.get_json()['proposals']]
        self.assertEqual(proposed, ['rf_tune', 'rf_capture_control'])


if __name__ == '__main__':
    unittest.main()
