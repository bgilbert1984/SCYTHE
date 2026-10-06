"""Provenance helpers for mechanics/ records.

Every mechanical-state record carries the full chain: which deck, which
mesh, which material cards, which boundary conditions, which solver
build at which commit, and which export file it was derived from.
No unnamed simulation magically becomes truth.

Completeness is load-bearing: a record is complete only when every
digest is present. There is no partial provenance.
"""

import hashlib
import json
from datetime import datetime, timezone

# Digests every complete record must carry. material_model_digest and
# boundary_condition_digest are required because the README promises the
# full materials/BC chain — a promise the schema now enforces.
REQUIRED_DIGESTS = (
    "deck_sha256",
    "mesh_sha256",
    "material_model_digest",
    "boundary_condition_digest",
    "solver_commit",
    "export_digest",
)


def sha256_file(path, chunk=1 << 20):
    """SHA-256 of a file, streamed."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def build_provenance(deck_path, export_path, mesh_path=None,
                     solver_build="", solver_commit="",
                     material_block=None, bc_block=None):
    """Assemble the provenance block for one solver run.

    A provenance object is either complete or it does not exist: every
    required input is mandatory, and absence is a refusal, not an empty
    digest. Build this AFTER the solver export exists (the importer
    consumes it), not before the run.

    deck_path: the solver input deck (.rad / .k). Required.
    export_path: the solver export file (CSV/VTK) this record derives
        from. Required — the export is what makes the chain checkable.
    mesh_path: external mesh file. When the mesh is embedded in the deck
        (the normal case), pass None and mesh_sha256 binds to deck_sha256:
        one artifact, one digest, no contradiction.
    material_block / bc_block: the material-card / boundary-condition text
        (or a path to the file holding them, e.g. decks/<name>/NOTES.md).
        REQUIRED — absent material/BC evidence is a refusal, matching the
        schema. There is no silent empty digest.
    """
    deck = sha256_file(deck_path)
    mesh = sha256_file(mesh_path) if mesh_path else deck
    if material_block is None:
        raise ValueError(
            "material_model_digest is required: pass the material-card "
            "block or its file (e.g. decks/<name>/NOTES.md)"
        )
    if bc_block is None:
        raise ValueError(
            "boundary_condition_digest is required: pass the "
            "boundary-condition block or its file"
        )

    def _digest(value):
        try:
            return sha256_file(value)
        except (OSError, TypeError):
            return sha256_text(str(value))

    return {
        "deck_sha256": deck,
        "mesh_sha256": mesh,
        "material_model_digest": _digest(material_block),
        "boundary_condition_digest": _digest(bc_block),
        "solver_build": solver_build,
        "solver_commit": solver_commit,
        "export_digest": sha256_file(export_path),
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


def provenance_complete(prov) -> bool:
    """A record is complete only when every required digest is present."""
    return all(prov.get(k) for k in REQUIRED_DIGESTS)
