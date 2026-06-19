# principals.py — System Principal Registry
# =========================================================================
#
# Principal Taxonomy:
#   Operator   — human, authenticated via OperatorSession
#   System     — non-human, bounded analytical instrument
#   External   — ingest pipelines, sensors, scanners
#
# System principals may:
#   - read evidence
#   - summarize
#   - propose inference
#   - write inferred edges
#
# System principals may NOT:
#   - delete evidence
#   - modify observed facts
#   - export TAK / CoT
#   - authorize actions
#
# This is NOT an operator.  It is a formally recognized bounded
# analytical instrument with explicit provenance and trust metadata.
# =========================================================================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Optional, Set


@dataclass(frozen=True)
class SystemPrincipal:
    """An immutable non-human principal with bounded capabilities."""
    principal_id: str                       # e.g. "SYSTEM:GRAPHOPS"
    display_name: str                       # human-readable label
    author_class: str = "system"            # trust.author_class
    auth_level: str = "bounded"             # trust.auth_level
    source: str = "graphops"                # provenance.source
    model_version: Optional[str] = None     # provenance.model_version
    capabilities: FrozenSet[str] = field(default_factory=lambda: frozenset({
        "read_evidence",
        "summarize",
        "propose_inference",
        "write_inferred_edges",
    }))

    # ---------- capability checks ----------

    def can(self, action: str) -> bool:
        return action in self.capabilities

    def is_system(self) -> bool:
        return self.principal_id.startswith("SYSTEM:")

    # ---------- WriteContext factory ----------

    def write_context(self, *, room_name: str = "Global",
                      request_id: Optional[str] = None,
                      evidence_refs: Optional[list] = None) -> "WriteContext":
        """Produce a WriteContext with correct system provenance."""
        from writebus import WriteContext
        return WriteContext(
            room_name=room_name,
            operator_id=self.principal_id,
            source=self.source,
            model_version=self.model_version,
            request_id=request_id,
            evidence_refs=evidence_refs or [],
        )


# =========================================================================
# Registry — singleton set of known system principals
# =========================================================================

# The canonical GraphOps system principal
GRAPHOPS = SystemPrincipal(
    principal_id="SYSTEM:GRAPHOPS",
    display_name="GraphOps Bot",
    source="graphops",
    model_version="gemma-3-1b",
)

# Future principals can be added here:
# PCAP_INGEST = SystemPrincipal(
#     principal_id="SYSTEM:PCAP_INGEST",
#     display_name="PCAP Ingest Pipeline",
#     source="pcap_ingest",
#     capabilities=frozenset({"read_evidence", "write_inferred_edges"}),
# )

_REGISTRY: Dict[str, SystemPrincipal] = {
    GRAPHOPS.principal_id: GRAPHOPS,
}


def get_principal(principal_id: str) -> Optional[SystemPrincipal]:
    """Look up a registered system principal by ID."""
    return _REGISTRY.get(principal_id)


def is_system_principal(operator_id: str) -> bool:
    """Return True if operator_id refers to a registered system principal."""
    return operator_id in _REGISTRY


def register(principal: SystemPrincipal) -> None:
    """Register a new system principal at runtime."""
    _REGISTRY[principal.principal_id] = principal


def all_principals() -> Dict[str, SystemPrincipal]:
    """Return a snapshot of all registered system principals."""
    return dict(_REGISTRY)
