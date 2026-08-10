import unittest

from graphops_graph_resolver import GraphResolutionError, GraphSelectionResolver


class _ExplorerEngine:
    def __init__(self):
        self.nodes = {
            'host:a': {'id': 'host:a', 'kind': 'network_host', 'labels': {'ip': '10.0.0.1'}, 'timestamp': 100},
            'host:b': {'id': 'host:b', 'kind': 'network_host', 'labels': {'ip': '10.0.0.2'}, 'timestamp': 101},
            'host:c': {'id': 'host:c', 'kind': 'network_host', 'labels': {'ip': '10.0.0.3'}, 'timestamp': 102},
            'host:d': {'id': 'host:d', 'kind': 'network_host', 'labels': {'ip': '10.0.0.4'}},
        }
        self.edges = {
            'tcp:a-b': {'id': 'tcp:a-b', 'kind': 'network_flow', 'nodes': ['host:a', 'host:b'],
                        'labels': {'proto': 'tcp', 'dest_port': '443'}, 'timestamp': 103},
            'udp:b-c': {'id': 'udp:b-c', 'kind': 'network_flow', 'nodes': ['host:b', 'host:c'],
                        'labels': {'proto': 'udp', 'dest_port': '53'}, 'timestamp': 104},
            'tcp:c-d': {'id': 'tcp:c-d', 'kind': 'network_flow', 'nodes': ['host:c', 'host:d'],
                        'labels': {'proto': 'tcp', 'dest_port': '22'}},
        }


class GraphOpsExplorerTests(unittest.TestCase):
    def test_reports_available_matched_and_returned_counts(self):
        result = GraphSelectionResolver(_ExplorerEngine()).explore(node_limit=2, edge_limit=1)
        self.assertEqual(result['counts']['availableNodes'], 4)
        self.assertEqual(result['counts']['availableEdges'], 3)
        self.assertEqual(result['counts']['matchedNodes'], 4)
        self.assertEqual(result['counts']['matchedEdges'], 3)
        self.assertEqual(result['counts']['returnedNodes'], 2)
        self.assertEqual(result['counts']['returnedEdges'], 1)
        self.assertTrue(result['bounded'])

    def test_protocol_search_and_time_filters_are_entity_explicit(self):
        udp = GraphSelectionResolver(_ExplorerEngine()).explore(protocol='udp')
        self.assertEqual({item['id'] for item in udp['nodes']}, {'host:b', 'host:c'})
        self.assertEqual([item['id'] for item in udp['edges']], ['udp:b-c'])
        searched = GraphSelectionResolver(_ExplorerEngine()).explore(query='443')
        self.assertEqual({item['id'] for item in searched['nodes']}, {'host:a', 'host:b'})
        self.assertEqual([item['id'] for item in searched['edges']], ['tcp:a-b'])
        timed = GraphSelectionResolver(_ExplorerEngine()).explore(start=101, end=104)
        self.assertEqual({item['id'] for item in timed['nodes']}, {'host:b', 'host:c'})
        self.assertEqual({item['id'] for item in timed['edges']}, {'tcp:a-b', 'udp:b-c'})
        self.assertEqual(timed['counts']['unknownTimeExcluded'], 2)

    def test_focus_expansion_is_bounded_by_depth(self):
        one = GraphSelectionResolver(_ExplorerEngine()).explore(focus_id='host:a', depth=1)
        self.assertEqual({item['id'] for item in one['nodes']}, {'host:a', 'host:b'})
        self.assertEqual({item['id'] for item in one['edges']}, {'tcp:a-b'})
        two = GraphSelectionResolver(_ExplorerEngine()).explore(focus_id='host:a', depth=2)
        self.assertEqual({item['id'] for item in two['nodes']}, {'host:a', 'host:b', 'host:c'})
        self.assertEqual({item['id'] for item in two['edges']}, {'tcp:a-b', 'udp:b-c'})

    def test_invalid_unbounded_inputs_fail_closed(self):
        with self.assertRaisesRegex(GraphResolutionError, 'query exceeds'):
            GraphSelectionResolver(_ExplorerEngine()).explore(query='x' * 129)
        with self.assertRaisesRegex(GraphResolutionError, 'protocol'):
            GraphSelectionResolver(_ExplorerEngine()).explore(protocol='tcp; rm')


if __name__ == '__main__':
    unittest.main()
