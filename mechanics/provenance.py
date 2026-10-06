"""Provenance helpers for mechanics/ records.

Every mechanical-state record carries the full chain: which deck, which
mesh, which material cards, which boundary conditions, which solver
build at which commit, and which export file it was derived from.
No unnamed simulation magically becomes truth.
"""

import hashlib
import json
from datetime import datetime, timezone


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


def build_provenance(deck_path, mesh_path=None, export_path=None,
                     solver_build="", solver_commit="",
                     material_block=None, bc_block=None):
    """Assemble the provenance block for one solver run.

    material_block / bc_block are the material-card and boundary-condition
    text blocks (or paths); when omitted their digests are left blank and
    the record MUST NOT be treated as complete.
    """
    def _digest(value):
        if value is None:
            return ""
        try:
            return sha256_file(value)
        except (OSError, TypeError):
            return sha256_text(str(value))

    return {
        "deck_sha256": sha256_file(deck_path),
        "mesh_sha256": _digest(mesh_path),
        "material_model_digest": _digest(material_block),
        "boundary_condition_digest": _digest(bc_block),
        "solver_build": solver_build,
        "solver_commit": solver_commit,
        "export_digest": _digest(export_path),
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


def provenance_complete(prov) -> bool:
    """A record is complete only if every digest is present."""
    required = ("deck_sha256", "mesh_sha256", "solver_commit", "export_digest")
    return all(prov.get(k) for k in required)
