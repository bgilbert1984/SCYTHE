"""A recon write carries only what changed.

Runs the real registry against a real WriteBus and OperatorSessionManager (temp
database), so the room cache, the SQLite row and the graph events are what a
deployment produces, not fakes that agree with the code.
"""
import json
import os
import sqlite3
import tempfile
import unittest

from operator_session_manager import OperatorSessionManager
from registries.recon_registry import merge_recon_update, update_disposition, upsert_recon_entity
from writebus import WriteBus, WriteContext


class RecordingHypergraph:
    def __init__(self):
        self.events = []

    def apply_graph_event(self, event):
        self.events.append(event)
        return True


class ReconPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.opman = OperatorSessionManager(db_path=os.path.join(self.tmp.name, 'ops.db'))
        self.graph = RecordingHypergraph()
        self.writer = WriteBus(self.opman, self.graph,
                               writebus_db_path=os.path.join(self.tmp.name, 'wb.db'))
        self.ctx_n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self):
        self.ctx_n += 1
        return WriteContext(room_name='Global', request_id='req-%d' % self.ctx_n, source='test')

    def write(self, payload):
        return upsert_recon_entity(payload, self.ctx(), writer=self.writer)

    def stored(self, entity_id):
        """What the room cache and the SQLite row hold."""
        room = self.opman.get_room_by_name('Global')
        cached = self.opman.room_entities[room.room_id][entity_id]['data']
        conn = sqlite3.connect(self.opman.db_path)
        try:
            row = conn.execute('SELECT entity_data FROM room_entities WHERE room_id=? AND entity_id=?',
                               (room.room_id, entity_id)).fetchone()
        finally:
            conn.close()
        return cached, json.loads(row[0])

    def last_node(self):
        return self.graph.events[-1]['entity_data']

    def seed(self):
        return self.write({
            'entity_id': 'E1', 'name': 'Alpha', 'disposition': 'HOSTILE', 'threat_level': 'HIGH',
            'lat': 38.5, 'lon': -77.25, 'alt': 120, 'ontology': 'vessel',
        })

    # --- the acceptance test from the review ---------------------------------

    def test_label_only_update_preserves_protected_fields_everywhere(self):
        created = self.seed()['entity']
        updated = self.write({'entity_id': 'E1', 'name': 'Alpha-renamed'})

        for where, entity in (('response', updated['entity']),
                              ('cache', self.stored('E1')[0]),
                              ('sqlite', self.stored('E1')[1])):
            self.assertEqual(entity['name'], 'Alpha-renamed', where)
            self.assertEqual(entity['disposition'], 'HOSTILE', where)
            self.assertEqual(entity['threat_level'], 'HIGH', where)
            self.assertEqual(entity['location'], {'lat': 38.5, 'lon': -77.25, 'altitude_m': 120.0}, where)
            self.assertEqual(entity['created'], created['created'], where)

        node = self.last_node()
        self.assertEqual(node['position'], [38.5, -77.25, 120.0])
        self.assertEqual(node['metadata']['disposition'], 'HOSTILE')
        self.assertEqual(node['metadata']['threat_level'], 'HIGH')
        self.assertEqual(node['metadata']['name'], 'Alpha-renamed')

    def test_disposition_endpoint_helper_keeps_the_rest_of_the_entity(self):
        self.seed()
        # update_disposition has no writer parameter; route it through the same bus
        import registries.recon_registry as reg
        original = reg.bus
        reg.bus = lambda: self.writer
        try:
            update_disposition('E1', 'FRIENDLY', self.ctx())
        finally:
            reg.bus = original
        cached, row = self.stored('E1')
        for entity in (cached, row):
            self.assertEqual(entity['disposition'], 'FRIENDLY')
            self.assertEqual(entity['name'], 'Alpha')
            self.assertEqual(entity['threat_level'], 'HIGH')
            self.assertEqual(entity['location']['lat'], 38.5)
        self.assertEqual(self.last_node()['position'], [38.5, -77.25, 120.0])

    # --- omitted vs falsy vs null --------------------------------------------

    def test_falsy_values_are_values_not_omissions(self):
        self.seed()
        out = self.write({'entity_id': 'E1', 'active': False, 'count': 0, 'tags': []})['entity']
        self.assertIs(out['active'], False)
        self.assertEqual(out['count'], 0)
        self.assertEqual(out['tags'], [])
        self.assertEqual(out['disposition'], 'HOSTILE')

    def test_null_or_empty_protected_fields_mean_not_provided(self):
        self.seed()
        for blank in (None, ''):
            out = self.write({'entity_id': 'E1', 'disposition': blank, 'threat_level': blank})['entity']
            self.assertEqual((out['disposition'], out['threat_level']), ('HOSTILE', 'HIGH'), repr(blank))

    def test_explicit_change_of_protected_fields_applies(self):
        self.seed()
        out = self.write({'entity_id': 'E1', 'disposition': 'NEUTRAL', 'threat_level': 'LOW'})['entity']
        self.assertEqual((out['disposition'], out['threat_level']), ('NEUTRAL', 'LOW'))

    def test_null_on_an_ordinary_field_is_stored_as_given(self):
        self.seed()
        out = self.write({'entity_id': 'E1', 'ontology': None})['entity']
        self.assertIn('ontology', out)
        self.assertIsNone(out['ontology'])

    # --- position -------------------------------------------------------------

    def test_complete_position_replaces_the_old_one(self):
        self.seed()
        out = self.write({'entity_id': 'E1', 'lat': 10.0, 'lon': 20.0})['entity']
        self.assertEqual(out['location'], {'lat': 10.0, 'lon': 20.0, 'altitude_m': 0.0})
        self.assertEqual(self.last_node()['position'], [10.0, 20.0, 0.0])
        self.assertEqual(self.stored('E1')[1]['location']['lat'], 10.0)

    def test_location_object_is_not_shadowed_by_the_old_top_level_pair(self):
        self.seed()
        out = self.write({'entity_id': 'E1', 'location': {'lat': 1.5, 'lon': 2.5, 'altitude_m': 7}})['entity']
        self.assertEqual(out['location'], {'lat': 1.5, 'lon': 2.5, 'altitude_m': 7.0})
        self.assertNotIn('lat', out)

    def test_lone_coordinate_is_rejected_and_nothing_is_written(self):
        self.seed()
        before = self.stored('E1')[1]
        events = len(self.graph.events)
        for patch in ({'lat': 10.0}, {'lon': 20.0}, {'location': {'lat': 5.0}}):
            with self.assertRaises(ValueError):
                self.write(dict(patch, entity_id='E1'))
        self.assertEqual(self.stored('E1')[1], before)
        self.assertEqual(len(self.graph.events), events)

    def test_position_keys_that_are_blank_do_not_count_as_a_position(self):
        self.seed()
        out = self.write({'entity_id': 'E1', 'lat': None, 'lon': '', 'name': 'x'})['entity']
        self.assertEqual(out['location']['lat'], 38.5)
        self.assertEqual((out['lat'], out['lon']), (38.5, -77.25))

    def test_zero_coordinates_are_a_position(self):
        self.seed()
        out = self.write({'entity_id': 'E1', 'lat': 0.0, 'lon': 0})['entity']
        self.assertEqual(out['location'], {'lat': 0.0, 'lon': 0.0, 'altitude_m': 0.0})

    def test_lng_spelling_counts_as_a_coordinate(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.write({'entity_id': 'E1', 'lng': 2.0})
        out = self.write({'entity_id': 'E1', 'lat': 1.0, 'lng': 2.0})['entity']
        self.assertEqual(out['location']['lon'], 2.0)

    def test_dict_shaped_rooms_are_supported(self):
        class DictRoomManager:
            rooms = {'Global': {'room_id': 'r1'}}
            room_entities = {'r1': {'E9': {'id': 'E9', 'type': 'RECON_ENTITY',
                                           'data': {'entity_id': 'E9', 'disposition': 'HOSTILE'}}}}

            def get_room_by_name(self, name):
                return self.rooms.get(name)

            def publish_to_room(self, room_id, **kwargs):
                return True

        writer = WriteBus(DictRoomManager(), RecordingHypergraph(),
                          writebus_db_path=os.path.join(self.tmp.name, 'wb2.db'))
        out = upsert_recon_entity({'entity_id': 'E9', 'name': 'n'}, self.ctx(), writer=writer)['entity']
        self.assertEqual(out['disposition'], 'HOSTILE')

    # --- creation is unchanged -------------------------------------------------

    def test_creation_keeps_the_payload_as_given(self):
        out = self.write({'entity_id': 'NEW', 'name': 'n'})['entity']
        self.assertNotIn('disposition', out)
        self.assertNotIn('location', out)
        self.assertEqual(out['type'], 'RECON_ENTITY')
        self.assertIsNone(self.last_node()['position'])

    def test_creation_with_a_lone_coordinate_is_not_newly_rejected(self):
        out = self.write({'entity_id': 'NEW2', 'lat': 1.0})['entity']
        self.assertEqual(out['lat'], 1.0)

    def test_other_entities_are_not_touched(self):
        self.seed()
        self.write({'entity_id': 'E2', 'name': 'Bravo', 'disposition': 'NEUTRAL'})
        self.write({'entity_id': 'E2', 'name': 'Bravo2'})
        self.assertEqual(self.stored('E1')[1]['disposition'], 'HOSTILE')
        self.assertEqual(self.stored('E2')[1]['disposition'], 'NEUTRAL')


class MergeFunctionTests(unittest.TestCase):
    def test_no_existing_returns_the_patch(self):
        self.assertEqual(merge_recon_update(None, {'a': 1}), {'a': 1})
        self.assertEqual(merge_recon_update({}, {'a': 1}), {'a': 1})

    def test_inputs_are_not_mutated(self):
        existing = {'entity_id': 'e', 'lat': 1.0, 'lon': 2.0, 'disposition': 'X'}
        patch = {'entity_id': 'e', 'disposition': None, 'lat': 3.0, 'lon': 4.0}
        merge_recon_update(existing, patch)
        self.assertEqual(existing, {'entity_id': 'e', 'lat': 1.0, 'lon': 2.0, 'disposition': 'X'})
        self.assertEqual(patch, {'entity_id': 'e', 'disposition': None, 'lat': 3.0, 'lon': 4.0})


if __name__ == '__main__':
    unittest.main()
