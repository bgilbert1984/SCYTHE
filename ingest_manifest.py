"""Per-ingest evidence manifest.

Every PCAP ingest can emit one JSON document that pins down what went in, what
code and config processed it, and what came out, so later DR drills, perf
baselines and reproducibility checks have something to compare against.

Two runs over the same PCAP with the same code and config must produce the same
``session_hash``; timings and runtime versions are recorded but deliberately
excluded from every hash.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

MANIFEST_SCHEMA = "scythe.ingest_manifest/1"
PIPELINE_VERSION = "pcap_ingest/1"

_REPO_DIR = Path(__file__).resolve().parent


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def canonical_hash(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def config_hash(config: Any) -> str:
    """Hash of the config fields that can change ingest output.

    Output-location and operational fields are excluded so moving the staging
    directory does not change the hash.
    """
    d = dataclasses.asdict(config) if dataclasses.is_dataclass(config) else dict(config)
    for k in ("ftp_url", "staging_dir", "manifest_dir", "parallel_decode", "skip_existing"):
        d.pop(k, None)
    return canonical_hash(d)


def git_commit() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(_REPO_DIR), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def runtime_versions() -> Dict[str, Optional[str]]:
    versions: Dict[str, Optional[str]] = {"python": platform.python_version()}
    for mod in ("numpy", "duckdb", "scapy", "dpkt"):
        try:
            versions[mod] = getattr(__import__(mod), "__version__", None)
        except Exception:
            versions[mod] = None
    return versions


def session_hash(session_ids: Iterable[str]) -> str:
    """Order-independent hash of the sessions an ingest produced.

    Session ids are themselves a hash of the 5-tuple and time bucket, so this
    is a cheap proxy for graph equivalence at the session layer.
    """
    return canonical_hash(sorted(session_ids))


def build_manifest(
    filepath: Path,
    result: Any,
    config: Any,
    stage_timings: Optional[Dict[str, float]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the manifest dict for one ingested PCAP.

    ``extra`` carries hashes owned by later stages (gravity, Cesium bundle,
    mission id) once those stages report them; absent keys are simply omitted.
    """
    manifest: Dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "pcap_file": result.pcap_file,
        "pcap_sha256": sha256_file(filepath),
        "pcap_size": filepath.stat().st_size,
        "pcap_artifact_id": result.pcap_artifact_id,
        "pipeline_version": PIPELINE_VERSION,
        "git_commit": git_commit(),
        "config_hash": config_hash(config),
        "session_count": result.sessions_created,
        "session_hash": session_hash(result.session_ids),
        "packets_decoded": result.packets_decoded,
        "entity_count": result.nodes_emitted,
        "edge_count": result.edges_emitted,
        "ok": result.ok,
        "errors": list(result.errors),
        "runtime": runtime_versions(),
        "stage_timings_sec": {k: round(v, 6) for k, v in (stage_timings or {}).items()},
        "duration_sec": round(result.duration_sec, 6),
        "platform": sys.platform,
    }
    if extra:
        manifest.update(extra)
    return manifest


def write_manifest(manifest: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{manifest['pcap_sha256'][:16]}.manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return path
