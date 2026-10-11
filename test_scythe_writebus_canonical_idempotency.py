#!/usr/bin/env python3
"""WriteBus canonical JSON and idempotency keys: frozen, independent, repeatable.

The write bus decides whether a retried write is the same write by hashing it.
That decision is only as good as two properties of the hash:

  * canonical JSON is a function of the *logical* value: dict insertion order,
    process, and run cannot change a digest;
  * the idempotency key moves with every field that makes a write materially
    different, and with nothing that varies between retries of the same write.

`test_writebus.py` exercises replay through the commit path with caller-supplied
keys, so a canonicalization or key-derivation regression passes it (removing
sort_keys, or putting a timestamp in the key, both survived it). These tests
pin the derivation itself.

Reference values here were NOT produced by this implementation. Each canonical
text was written by hand from the rules (keys sorted, separators `,` and `:`
with no spaces, ASCII-only output with \\uXXXX escapes, list order kept), and
each digest was computed over that text with `sha256sum`. The idempotency
vectors were built the same way from the documented structure of the key: a
SHA-256 of the sorted-key JSON of
  {correlation_id, entity_id, entity_type, graph_ops_hash, payload_hash,
   request_id, room_id, room_name, source}, truncated to 24 hex characters.
`VectorDerivationTests` re-checks that each text hashes to its recorded digest,
so a transcription slip cannot silently weaken a vector.

This module is named test_scythe_* so the existing python-rf glob runs it; the
older test_writebus.py sits in a separate job.
"""

import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from writebus import GraphOp, Provenance, WriteBus, _safe_json, _stable_hash

BS = chr(92)    # a backslash, built so no layer can reinterpret an escape

# (label, input object, canonical text, SHA-256 of that text)
CANONICAL_VECTORS = [
    ("empty object", {}, "{}",
     "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"),
    ("keys are sorted", {"b": 2, "a": 1}, '{"a":1,"b":2}',
     "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"),
    ("nesting, booleans, null; list order kept",
     {"z": [3, 1, 2], "a": {"y": None, "x": True}},
     '{"a":{"x":true,"y":null},"z":[3,1,2]}',
     "9c78dbebd1ed8bbd05dd99ba7a18d83d20a4fddce49ad323258c650e9e74ed89"),
    ("dicts inside lists are sorted, lists are not",
     [{"b": 1, "a": 2}, [3, 2, 1]],
     '[{"a":2,"b":1},[3,2,1]]',
     "d6a1fe3d5078f02cf9d6e977ace9ad7bebcc86ba1cdd6523dd1f3fba1c006188"),
    ("integer", {"n": 1}, '{"n":1}',
     "2bfd14f43d17fc7cea24e0917a8879b4b2f880b8baeec1b9d90fbaad655e71bd"),
    ("float, space, empty list",
     {"t": [], "s": "a b", "n": 1.5}, '{"n":1.5,"s":"a b","t":[]}',
     "61e78dd26236232b74bad295dc37570e53389b720be5df44efffd46bbb7056bc"),
    ("non-ASCII is escaped, not emitted",
     {"k": "\N{LATIN SMALL LETTER E WITH ACUTE}"},
     '{"k":"' + BS + 'u00e9"}',
     "9567d6e8f21bbdcbc2e0140bd733df64007ac8ed4af2329f39121e6349c3c86f"),
    ("astral character becomes a surrogate pair",
     {"k": "\N{GRINNING FACE}"},
     '{"k":"' + BS + 'ud83d' + BS + 'ude00"}',
     "ca90fe6f2acf0d4917b1724ebe76ca894184a6d72c4558deac8cef498f289365"),
]

# Frozen idempotency keys. Inputs are described in `_vector_inputs`.
KEY_VECTOR_1 = "03dfa1b8928f60d5528cda97"    # plain payload, one graph op
KEY_VECTOR_2 = "d91364b9320949b3f2a47dce"    # metadata: bookkeeping stripped
KEY_VECTOR_3 = "8768776df8bd5356c24abb0b"    # no graph ops; explicit room


class _Operators:
    """The key derivation never touches the operator manager."""


def _bus(tmpdir, default_room="Global"):
    return WriteBus(_Operators(), object(), default_room=default_room,
                    writebus_db_path=os.path.join(tmpdir, "writebus.sqlite3"))


def _op(event_type="NODE_UPDATE", entity_id="node-1", **data):
    return GraphOp(event_type, entity_id, data or {"id": "node-1",
                                                   "kind": "test"})


def _vector_inputs(n):
    """The three frozen cases. Argument dicts are built in a deliberately
    non-sorted insertion order."""
    if n == 1:
        return dict(
            entity_id="entity-1", entity_type="TEST_ENTITY",
            payload={"type": "TEST_ENTITY", "id": "entity-1"},
            prov=Provenance(source="manual_ui", request_id="req-1",
                            correlation_id="corr-1"),
            graph_ops=[GraphOp("NODE_UPDATE", "node-1",
                               {"kind": "test", "id": "node-1"})],
            room_name=None, room_id=None)
    if n == 2:
        return dict(
            entity_id="entity-1", entity_type="TEST_ENTITY",
            payload={"type": "TEST_ENTITY", "id": "entity-1",
                     "metadata": {"write_fingerprint": "x",
                                  "provenance_write": {"a": 1},
                                  "provenance": {"b": 2}, "trust": "t",
                                  "temporal_context": {}, "causality": [],
                                  "tag": "alpha"}},
            prov=Provenance(source="manual_ui", request_id="req-1",
                            correlation_id="corr-1"),
            graph_ops=[GraphOp("NODE_UPDATE", "node-1",
                               {"kind": "test", "id": "node-1"})],
            room_name=None, room_id=None)
    return dict(
        entity_id="entity-9", entity_type="OTHER",
        payload={"type": "TEST_ENTITY", "id": "entity-1"},
        prov=Provenance(source="pcap_ingest"),
        graph_ops=[], room_name="Ops", room_id="room-7")


def _key(bus, **overrides):
    args = _vector_inputs(1)
    args.update(overrides)
    return bus._idempotency_key(**args)


class VectorDerivationTests(unittest.TestCase):
    """The reference values are internally consistent and pure ASCII."""

    def test_each_text_hashes_to_its_recorded_digest(self):
        for label, _obj, text, digest in CANONICAL_VECTORS:
            with self.subTest(label):
                self.assertEqual(text.encode("ascii").decode(), text)
                self.assertEqual(
                    hashlib.sha256(text.encode("ascii")).hexdigest(), digest)

    def test_escaped_vectors_contain_a_real_backslash(self):
        by_label = {label: text for label, _o, text, _d in CANONICAL_VECTORS}
        self.assertEqual(
            by_label["non-ASCII is escaped, not emitted"].count(BS), 1)
        self.assertEqual(
            by_label["astral character becomes a surrogate pair"].count(BS), 2)

    def test_idempotency_vector_texts_hash_as_documented(self):
        """Rebuild the three keys from hand-written canonical text."""
        def sha(text):
            return hashlib.sha256(text.encode("ascii")).hexdigest()

        payload_1 = sha('{"id":"entity-1","type":"TEST_ENTITY"}')
        ops_1 = sha('[{"entity_data":{"id":"node-1","kind":"test"},'
                    '"entity_id":"node-1","event_type":"NODE_UPDATE"}]')

        def core(entity_id, entity_type, ops, payload, request_id,
                 correlation_id, room_id, room_name, source):
            def q(value):
                return "null" if value is None else '"%s"' % value
            return ('{"correlation_id":%s,"entity_id":"%s","entity_type":"%s",'
                    '"graph_ops_hash":"%s","payload_hash":"%s",'
                    '"request_id":%s,"room_id":%s,"room_name":"%s",'
                    '"source":"%s"}' % (q(correlation_id), entity_id,
                                        entity_type, ops, payload,
                                        q(request_id), q(room_id), room_name,
                                        source))

        key_1 = sha(core("entity-1", "TEST_ENTITY", ops_1, payload_1,
                         "req-1", "corr-1", None, "Global", "manual_ui"))[:24]
        payload_2 = sha('{"id":"entity-1","meta":{"tag":"alpha"},'
                        '"metadata":{"tag":"alpha"},"type":"TEST_ENTITY"}')
        key_2 = sha(core("entity-1", "TEST_ENTITY", ops_1, payload_2,
                         "req-1", "corr-1", None, "Global", "manual_ui"))[:24]
        key_3 = sha(core("entity-9", "OTHER", sha("[]"), payload_1,
                         None, None, "room-7", "Ops", "pcap_ingest"))[:24]
        self.assertEqual((key_1, key_2, key_3),
                         (KEY_VECTOR_1, KEY_VECTOR_2, KEY_VECTOR_3))


class CanonicalJsonTests(unittest.TestCase):

    def test_canonical_text_matches_the_frozen_vectors(self):
        for label, obj, text, _digest in CANONICAL_VECTORS:
            with self.subTest(label):
                self.assertEqual(_safe_json(obj), text)

    def test_digest_matches_the_frozen_vectors(self):
        for label, obj, _text, digest in CANONICAL_VECTORS:
            with self.subTest(label):
                self.assertEqual(_stable_hash(obj), digest)

    def test_dict_insertion_order_cannot_change_the_digest(self):
        """Every ordering of the top-level and nested keys, 144 in all, must
        land on the digest of the one sorted form."""
        sorted_form = {"a": {"x": 1, "y": [{"q": 1, "p": 2}], "z": None},
                       "b": "two", "c": [3, 2, 1], "d": True}
        expected_text = ('{"a":{"x":1,"y":[{"p":2,"q":1}],"z":null},'
                         '"b":"two","c":[3,2,1],"d":true}')
        expected_digest = hashlib.sha256(
            expected_text.encode("ascii")).hexdigest()
        inner_items = list(sorted_form["a"].items())
        outer = {k: v for k, v in sorted_form.items() if k != "a"}
        seen = set()
        for inner_order in itertools.permutations(inner_items):
            for outer_order in itertools.permutations(
                    list(outer.items()) + [("a", None)]):
                built = {}
                for key, value in outer_order:
                    built[key] = dict(inner_order) if key == "a" else value
                self.assertEqual(_safe_json(built), expected_text)
                seen.add(_stable_hash(built))
        self.assertEqual(seen, {expected_digest})

    def test_list_order_is_significant(self):
        self.assertNotEqual(_stable_hash({"v": [1, 2]}),
                            _stable_hash({"v": [2, 1]}))

    def test_distinct_values_have_distinct_digests(self):
        digests = [_stable_hash(obj) for _l, obj, _t, _d in CANONICAL_VECTORS]
        self.assertEqual(len(set(digests)), len(digests))

    def test_values_json_cannot_encode_fall_back_to_str(self):
        class Token:
            def __str__(self):
                return "token-7"
        self.assertEqual(_safe_json({"v": Token()}), '{"v":"token-7"}')


class IdempotencyKeyTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bus = _bus(self._tmp.name)

    def test_frozen_vectors(self):
        for n, expected in ((1, KEY_VECTOR_1), (2, KEY_VECTOR_2),
                            (3, KEY_VECTOR_3)):
            with self.subTest(vector=n):
                self.assertEqual(
                    self.bus._idempotency_key(**_vector_inputs(n)), expected)

    def test_key_is_24_lowercase_hex_characters(self):
        self.assertRegex(_key(self.bus), r"^[0-9a-f]{24}$")

    def test_identical_inputs_give_identical_keys_across_executions(self):
        """Fresh Provenance objects carry different timestamps each time; the
        key must not."""
        keys = {self.bus._idempotency_key(**_vector_inputs(1))
                for _ in range(200)}
        self.assertEqual(keys, {KEY_VECTOR_1})

    def test_identical_inputs_give_identical_keys_across_bus_instances(self):
        other = _bus(tempfile.mkdtemp(dir=self._tmp.name))
        self.assertEqual(_key(self.bus), _key(other))

    def test_retry_varying_fields_do_not_change_the_key(self):
        """operator, session and timestamp differ between retries of one write."""
        baseline = _key(self.bus)
        for field, value in (("operator_id", "operator-2"),
                             ("session_id", "session-9"),
                             ("timestamp", "2031-01-01T00:00:00Z")):
            with self.subTest(field):
                prov = Provenance(source="manual_ui", request_id="req-1",
                                  correlation_id="corr-1", **{field: value})
                self.assertEqual(_key(self.bus, prov=prov), baseline)

    def test_every_material_field_changes_the_key(self):
        baseline = _key(self.bus)

        def prov(**kw):
            base = dict(source="manual_ui", request_id="req-1",
                        correlation_id="corr-1")
            base.update(kw)
            return Provenance(**base)

        variants = {
            "entity_id": dict(entity_id="entity-2"),
            "entity_type": dict(entity_type="OTHER_ENTITY"),
            "payload value": dict(payload={"id": "entity-1",
                                           "type": "CHANGED"}),
            "payload extra key": dict(payload={"id": "entity-1",
                                               "type": "TEST_ENTITY",
                                               "x": 1}),
            "request_id": dict(prov=prov(request_id="req-2")),
            "correlation_id": dict(prov=prov(correlation_id="corr-2")),
            "source": dict(prov=prov(source="pcap_ingest")),
            "room_name": dict(room_name="Ops"),
            "room_id": dict(room_id="room-7"),
            "op event_type": dict(graph_ops=[_op(event_type="NODE_CREATE")]),
            "op entity_id": dict(graph_ops=[_op(entity_id="node-2")]),
            "op entity_data": dict(graph_ops=[_op(id="node-1", kind="other")]),
            "extra op": dict(graph_ops=[_op(), _op(entity_id="node-3")]),
            "no ops": dict(graph_ops=[]),
        }
        keys = {name: _key(self.bus, **kw) for name, kw in variants.items()}
        for name, key in keys.items():
            with self.subTest(name):
                self.assertNotEqual(key, baseline)
        self.assertEqual(len(set(keys.values()) | {baseline}),
                         len(variants) + 1, "two variants collided")

    def test_operation_order_is_part_of_the_write(self):
        first, second = _op(entity_id="node-1"), _op(entity_id="node-2")
        self.assertNotEqual(_key(self.bus, graph_ops=[first, second]),
                            _key(self.bus, graph_ops=[second, first]))

    def test_dict_insertion_order_cannot_change_the_key(self):
        a = _key(self.bus,
                 payload={"id": "entity-1", "type": "TEST_ENTITY",
                          "attrs": {"p": 1, "q": 2, "r": 3}},
                 graph_ops=[GraphOp("NODE_UPDATE", "node-1",
                                    {"id": "node-1", "k": {"m": 1, "n": 2}})])
        b = _key(self.bus,
                 payload={"attrs": {"r": 3, "q": 2, "p": 1},
                          "type": "TEST_ENTITY", "id": "entity-1"},
                 graph_ops=[GraphOp("NODE_UPDATE", "node-1",
                                    {"k": {"n": 2, "m": 1}, "id": "node-1"})])
        self.assertEqual(a, b)

    def test_bookkeeping_metadata_is_not_part_of_the_key(self):
        bare = _key(self.bus)
        for name in ("write_fingerprint", "provenance_write", "provenance",
                     "trust", "temporal_context", "causality"):
            with self.subTest(name):
                payload = {"id": "entity-1", "type": "TEST_ENTITY",
                           "metadata": {name: "anything"}}
                self.assertEqual(_key(self.bus, payload=payload), bare)

    def test_empty_metadata_is_the_same_as_none(self):
        bare = _key(self.bus)
        for payload in ({"id": "entity-1", "type": "TEST_ENTITY",
                         "metadata": {}},
                        {"id": "entity-1", "type": "TEST_ENTITY", "meta": {}}):
            self.assertEqual(_key(self.bus, payload=payload), bare)

    def test_meta_and_metadata_are_aliases(self):
        a = _key(self.bus, payload={"id": "entity-1", "type": "TEST_ENTITY",
                                    "metadata": {"tag": "alpha"}})
        b = _key(self.bus, payload={"id": "entity-1", "type": "TEST_ENTITY",
                                    "meta": {"tag": "alpha"}})
        self.assertEqual(a, b)

    def test_other_metadata_is_part_of_the_key(self):
        bare = _key(self.bus)
        tagged = _key(self.bus, payload={"id": "entity-1",
                                         "type": "TEST_ENTITY",
                                         "metadata": {"tag": "alpha"}})
        other = _key(self.bus, payload={"id": "entity-1",
                                        "type": "TEST_ENTITY",
                                        "metadata": {"tag": "beta"}})
        self.assertEqual(len({bare, tagged, other}), 3)

    def test_default_room_is_part_of_the_key(self):
        elsewhere = _bus(tempfile.mkdtemp(dir=self._tmp.name),
                         default_room="Ops")
        self.assertNotEqual(_key(self.bus), _key(elsewhere))
        # an unset room_name means the bus default, and an explicit one wins
        self.assertEqual(_key(self.bus, room_name="Global"), _key(self.bus))
        self.assertEqual(_key(elsewhere, room_name="Ops"), _key(elsewhere))
        self.assertEqual(_key(elsewhere, room_name="Global"), _key(self.bus))


class CrossProcessTests(unittest.TestCase):
    """The same logical write gets the same key in separate interpreters with
    different string-hash seeds. A dict ordering or set ordering that leaked
    into the key would show up here."""

    PROBE = textwrap.dedent("""
        import json, sys, tempfile, os
        sys.path.insert(0, sys.argv[1])
        from writebus import GraphOp, Provenance, WriteBus
        class Ops: pass
        with tempfile.TemporaryDirectory() as d:
            bus = WriteBus(Ops(), object(),
                           writebus_db_path=os.path.join(d, "w.sqlite3"))
            reverse = sys.argv[2] == "reverse"
            def order(d):
                items = list(d.items())
                return dict(reversed(items) if reverse else items)
            k1 = bus._idempotency_key(
                entity_id="entity-1", entity_type="TEST_ENTITY",
                payload=order({"id": "entity-1", "type": "TEST_ENTITY"}),
                prov=Provenance(source="manual_ui", request_id="req-1",
                                correlation_id="corr-1"),
                graph_ops=[GraphOp("NODE_UPDATE", "node-1",
                                   order({"id": "node-1", "kind": "test"}))])
            k2 = bus._idempotency_key(
                entity_id="entity-1", entity_type="TEST_ENTITY",
                payload=order({"id": "entity-1", "type": "TEST_ENTITY",
                               "metadata": order({
                                   "write_fingerprint": "x",
                                   "provenance_write": {"a": 1},
                                   "provenance": {"b": 2}, "trust": "t",
                                   "temporal_context": {}, "causality": [],
                                   "tag": "alpha"})}),
                prov=Provenance(source="manual_ui", request_id="req-1",
                                correlation_id="corr-1"),
                graph_ops=[GraphOp("NODE_UPDATE", "node-1",
                                   order({"id": "node-1", "kind": "test"}))])
            print(json.dumps([k1, k2]))
    """)

    def test_frozen_keys_in_fresh_interpreters(self):
        for seed in ("0", "1", "4242", "random"):
            for order in ("forward", "reverse"):
                with self.subTest(hashseed=seed, order=order):
                    with tempfile.TemporaryDirectory() as cwd:
                        proc = subprocess.run(
                            [sys.executable, "-I", "-c", self.PROBE, REPO,
                             order],
                            cwd=cwd, capture_output=True, text=True,
                            timeout=120,
                            env=dict(os.environ, PYTHONHASHSEED=seed,
                                     PYTHONDONTWRITEBYTECODE="1"))
                    self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
                    self.assertEqual(
                        json.loads(proc.stdout.strip().splitlines()[-1]),
                        [KEY_VECTOR_1, KEY_VECTOR_2])


if __name__ == "__main__":
    unittest.main()
