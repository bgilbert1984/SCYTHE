import unittest

from graphops.evidence_fabric import (GraphFusionEvidenceFabric, RetrievalPolicy,
                                      SemanticSeed)
from graphops_copilot import InvestigativeDSLExecutor
from graphops_graph_resolver import GraphSelectionResolver


class _Graph:
    def __init__(self):
        self.nodes = {
            'host:a': {'id': 'host:a', 'kind': 'network_host',
                       'labels': {'ip': '10.0.0.1'}, 'timestamp': 100},
            'host:b': {'id': 'host:b', 'kind': 'network_host',
                       'labels': {'ip': '10.0.0.2'}, 'timestamp': 101},
            'host:c': {'id': 'host:c', 'kind': 'network_host',
                       'labels': {'ip': '10.0.0.3'}, 'timestamp': 102},
            'host:d': {'id': 'host:d', 'kind': 'network_host',
                       'labels': {'ip': '10.0.0.4'}, 'timestamp': 103},
        }
        self.edges = {
            'hyperedge:observed': {
                'id': 'hyperedge:observed', 'kind': 'coordination',
                'nodes': ['host:a', 'host:b', 'host:c'], 'timestamp': 104,
                'evidenceClass': 'OBSERVED',
            },
            'edge:contradiction': {
                'id': 'edge:contradiction', 'kind': 'contradicts',
                'nodes': ['host:c', 'host:d'], 'timestamp': 105,
                'evidenceClass': 'CONTRADICTED',
            },
        }


def _pin(engine, entity_id='host:a'):
    resolver = GraphSelectionResolver(engine)
    return resolver.pin_selection({
        'kind': 'graph-node', 'entityId': entity_id,
        'graphRevision': resolver.revision(),
    })


class GraphFusionTests(unittest.TestCase):
    def test_pin_is_selection_aware_and_dsl_adapter_does_not_read_later_state(self):
        engine = _Graph()
        for index in range(600):
            engine.nodes[f'noise:{index}'] = {'id': f'noise:{index}', 'kind': 'noise'}
        view = _pin(engine, 'host:d')
        self.assertEqual(view.nodes[0]['id'], 'host:d')
        adapter = view.engine_adapter()
        engine.nodes['host:later'] = {'id': 'host:later', 'kind': 'network_host'}
        self.assertIsNone(adapter.get_node('host:later'))
        self.assertIsNotNone(adapter.get_node('host:d'))
        self.assertTrue(view.projection_truncated)

    def test_projection_hash_identifies_exact_selection_aware_projection(self):
        engine = _Graph()
        first = _pin(engine, 'host:a')
        repeated = _pin(engine, 'host:a')
        other = _pin(engine, 'host:d')
        self.assertEqual(first.graph_revision, repeated.graph_revision)
        self.assertEqual(first.projection_hash, repeated.projection_hash)
        self.assertNotEqual(first.projection_hash, other.projection_hash)

    def test_traversal_is_deterministic_and_preserves_hyperedge_steps(self):
        view = _pin(_Graph())
        fabric = GraphFusionEvidenceFabric()
        first = fabric.build(question='What contradicts this host?', view=view,
                             mode='pinned_graph')
        second = fabric.build(question='What contradicts this host?', view=view,
                              mode='pinned_graph')
        self.assertEqual(first['traversal']['hash'], second['traversal']['hash'])
        contradiction = next(path for path in first['paths']
                             if path['role'] == 'EXPLICIT_CONTRADICTION')
        self.assertEqual(contradiction['steps'][1],
                         {'type': 'edge', 'id': 'hyperedge:observed'})
        self.assertIn({'type': 'edge', 'id': 'edge:contradiction'},
                      contradiction['steps'])
        self.assertEqual(first['graph']['revision'], view.graph_revision)
        self.assertEqual(first['projection']['hash'], view.projection_hash)

    def test_semantic_seed_is_a_lead_and_unresolved_seed_does_not_traverse(self):
        view = _pin(_Graph())
        seeds = [
            SemanticSeed('host:b', .9, 'fixture', resolution='RESOLVED_IN_PROJECTION'),
            SemanticSeed('host:historical', .99, 'fixture',
                         resolution='OUTSIDE_RETAINED_PROJECTION'),
        ]
        result = GraphFusionEvidenceFabric().build(
            question='Find similar hosts', view=view, mode='pinned_fused',
            semantic_seeds=seeds)
        self.assertEqual(len(result['semanticSeeds']), 2)
        self.assertEqual(result['traversal']['seeds'], 2)
        self.assertFalse(any(path['seedId'] == 'host:historical' for path in result['paths']))

    def test_synthetic_edges_are_excluded_by_default(self):
        engine = _Graph()
        engine.edges = {'synthetic': {'id': 'synthetic', 'kind': 'flow',
                                      'nodes': ['host:a', 'host:b'],
                                      'metadata': {'source': 'test_generator'}}}
        view = _pin(engine)
        result = GraphFusionEvidenceFabric(RetrievalPolicy()).build(
            question='What is connected?', view=view, mode='pinned_graph')
        self.assertEqual(result['traversal']['admittedPaths'], 0)
        self.assertEqual(result['paths'], [])

    def test_deterministic_modes_can_refuse_model_directed_semantic_widening(self):
        executor = InvestigativeDSLExecutor(_pin(_Graph()).engine_adapter())
        with executor.block_verbs({'VECTOR_SEARCH', 'CLUSTER_SIMILAR'}):
            result = executor.run(['VECTOR_SEARCH "similar host" k=5'])
        self.assertIn('disabled by the active retrieval policy',
                      result['steps'][0]['result']['refused'])


if __name__ == '__main__':
    unittest.main()
