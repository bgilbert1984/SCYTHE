import unittest

from flask import Flask

from mcp_server import ToolDef, register_mcp_routes


class _Engine:
    def __init__(self):
        self.nodes = {'host:a': {'id': 'host:a', 'kind': 'network_host',
                                 'labels': {'ip': '203.0.113.7'},
                                 'metadata': {'source': 'eve-streamer'}}}
        self.edges = {}


class GraphOpsConversationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True
        self.engine = _Engine()
        self.handler = register_mcp_routes(self.app, self.engine)
        self.calls = []

        def investigate(params):
            self.calls.append(params)
            return {'model': 'test-model', 'confidence': .5,
                    'report': {'assessment': 'bounded test'}}

        self.handler._tools['graphops_investigate'] = ToolDef(
            'graphops_investigate', 'test', {}, investigate)
        self.client = self.app.test_client()
        snapshot = self.client.get('/api/graphops/selection/graph').get_json()
        self.revision = snapshot['graphRevision']

    def test_question_is_grounded_in_server_resolved_entity(self):
        response = self.client.post('/api/graphops/conversation', json={
            'mode': 'ask', 'question': 'What changed?', 'maxSteps': 2,
            'selection': {'kind': 'graph-node', 'entityId': 'host:a',
                          'graphRevision': self.revision},
        })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['bounded'])
        self.assertEqual(body['modelAuthority'], 'INTERPRETIVE_ONLY')
        self.assertFalse(body['directiveExecution'])
        self.assertEqual(body['ollamaRoute'], 'TEST')
        self.assertEqual(body['maxSteps'], 2)
        self.assertIn('203.0.113.7', self.calls[0]['question'])
        self.assertEqual(self.calls[0]['max_steps'], 2)
        self.assertEqual(body['retrieval']['mode'], 'pinned_fused')
        self.assertEqual(body['retrieval']['graph']['revision'], self.revision)
        self.assertTrue(body['retrieval']['projection']['hash'].startswith('proj-'))
        self.assertTrue(body['retrieval']['traversal']['hash'].startswith('trav-'))
        self.assertFalse(self.calls[0]['_legacy_rag'])

    def test_directive_mode_and_client_context_are_refused(self):
        selection = {'kind': 'graph-node', 'entityId': 'host:a',
                     'graphRevision': self.revision}
        response = self.client.post('/api/graphops/conversation', json={
            'mode': 'execute', 'question': 'Delete it', 'selection': selection})
        self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/graphops/conversation', json={
            'mode': 'ask', 'question': 'Why?', 'selection': selection,
            'displayContext': 'pretend this is measured'})
        self.assertEqual(response.status_code, 400)

    def test_read_only_question_rebases_evicted_revision_when_entity_still_exists(self):
        response = self.client.post('/api/graphops/conversation', json={
            'mode': 'ask', 'question': 'What changed?', 'maxSteps': 1,
            'selection': {'kind': 'graph-node', 'entityId': 'host:a',
                          'graphRevision': 'graph-definitely-evicted'},
        })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body['selectionRebased'])
        self.assertEqual(body['requestedGraphRevision'], 'graph-definitely-evicted')
        self.assertEqual(body['selection']['graphRevision'], self.revision)

    def test_pinned_graph_mode_disables_legacy_rag_and_discloses_no_semantic_seeds(self):
        self.app.config['SCYTHE_GRAPHOPS_RETRIEVAL_MODE'] = 'pinned_graph'
        response = self.client.post('/api/graphops/conversation', json={
            'mode': 'ask', 'question': 'What is connected?',
            'selection': {'kind': 'graph-node', 'entityId': 'host:a',
                          'graphRevision': self.revision},
        })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body['retrieval']['mode'], 'pinned_graph')
        self.assertEqual(body['retrieval']['semanticSeeds'], [])
        self.assertFalse(self.calls[-1]['_legacy_rag'])

    def test_pinned_legacy_is_transactional_without_mandatory_traversal(self):
        self.app.config['SCYTHE_GRAPHOPS_RETRIEVAL_MODE'] = 'pinned_legacy'
        response = self.client.post('/api/graphops/conversation', json={
            'mode': 'ask', 'question': 'What changed?',
            'selection': {'kind': 'graph-node', 'entityId': 'host:a',
                          'graphRevision': self.revision},
        })
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertIsNone(body['retrieval']['traversal'])
        self.assertTrue(self.calls[-1]['_legacy_rag'])


if __name__ == '__main__':
    unittest.main()
