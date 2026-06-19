#!/usr/bin/env python3
"""
Revenge-for-Hire Ecosystem Hypergraph Model

A living hypergraph model for multi-actor, multi-intent, multi-channel
malice-as-a-service ecosystems. Designed for integration with HypergraphEngine.

Node Kinds: Actors, Infrastructure, Artifacts
Hyperedge Kinds: Commission, Task-Decomposition, Data-Fusion, Attack-Surface, etc.
Event Types: CommissionCreated, TaskDecomposed, ArtifactGenerated, etc.

Visual Organs:
- Harassment Organ (magenta, staccato jitter)
- Doxxing Organ (teal, radial reveal)
- Reputation Organ (amber, creeping diffusion)
- Obfuscation Organ (indigo, counter-rotating spirals)
- Escalation Organ (crimson, heartbeat surge)
"""

import time
import threading
import json
import math
import random
from enum import Enum, IntFlag
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional, Set, Callable, Tuple
from collections import defaultdict


# ============================================================================
# NODE KINDS
# ============================================================================

class ActorKind(Enum):
    """Actor node kinds in the ecosystem"""
    CLIENT = "client"
    OPERATOR = "operator"
    SUBCONTRACTOR = "subcontractor"
    BOT = "bot"
    VICTIM = "victim"


class InfrastructureKind(Enum):
    """Infrastructure node kinds"""
    PLATFORM = "platform"
    DATA_SOURCE = "data_source"
    PAYMENT_CHANNEL = "payment_channel"
    HOSTING_PROXY = "hosting_proxy"


class ArtifactKind(Enum):
    """Artifact node kinds"""
    HARASSMENT_PACKAGE = "harassment_package"
    DOXX_PACKET = "doxx_packet"
    REPUTATION_PAYLOAD = "reputation_payload"
    ACCESS_TOKEN = "access_token"


# ============================================================================
# HYPEREDGE KINDS
# ============================================================================

class HyperedgeKind(Enum):
    """Hyperedge kinds representing multi-party relationships"""
    COMMISSION = "commission"                       # Client → Operator → Payment
    TASK_DECOMPOSITION = "task_decomposition"       # Operator → Subcontractors → Bots → Artifacts
    DATA_FUSION = "data_fusion"                     # Data Sources → Operator → Victim
    ATTACK_SURFACE = "attack_surface"               # Victim → Platforms → Artifacts
    DISTRIBUTION = "distribution"                   # Bots → Platforms → Payloads
    ATTRIBUTION_OBFUSCATION = "attribution_obfuscation"  # Operator → Proxies → Platforms
    ESCALATION = "escalation"                       # Client → Operator → New Artifacts


# ============================================================================
# ORGAN FLAGS (BITMASK)
# ============================================================================

class OrganMask(IntFlag):
    """Bitmask for organ membership"""
    NONE = 0
    HARASSMENT = 1
    DOXXING = 2
    REPUTATION = 4
    OBFUSCATION = 8
    ESCALATION = 16
    ALL = HARASSMENT | DOXXING | REPUTATION | OBFUSCATION | ESCALATION


# ============================================================================
# EVENT TYPES
# ============================================================================

class EcosystemEventType(Enum):
    """Event types for diff-driven pipeline"""
    COMMISSION_CREATED = "CommissionCreated"
    TASK_DECOMPOSED = "TaskDecomposed"
    ARTIFACT_GENERATED = "ArtifactGenerated"
    DATA_SOURCE_LINKED = "DataSourceLinked"
    ATTACK_EXECUTED = "AttackExecuted"
    ESCALATION_TRIGGERED = "EscalationTriggered"
    OBFUSCATION_LAYER_ADDED = "ObfuscationLayerAdded"
    PAYMENT_COMPLETED = "PaymentCompleted"
    PLATFORM_RESPONSE = "PlatformResponse"
    ATTRIBUTION_LEAK_DETECTED = "AttributionLeakDetected"


# ============================================================================
# VISUAL ENCODING - ORGAN STYLES
# ============================================================================

@dataclass
class OrganVisualStyle:
    """Visual encoding for an organ"""
    name: str
    glyph: str              # Glyph description
    color_start: str        # Hex color gradient start
    color_end: str          # Hex color gradient end
    hue: float              # Hue on color wheel (degrees)
    motion_type: str        # Motion behavior name
    motion_params: Dict[str, float] = field(default_factory=dict)


ORGAN_STYLES = {
    OrganMask.HARASSMENT: OrganVisualStyle(
        name="Harassment",
        glyph="tri-fork-sigil",
        color_start="#FF00FF",  # Magenta
        color_end="#8B00FF",    # Ultraviolet
        hue=300.0,
        motion_type="staccato_jitter",
        motion_params={"jitter_amp": 0.02, "jitter_freq": 15.0}
    ),
    OrganMask.DOXXING: OrganVisualStyle(
        name="Doxxing",
        glyph="broken-circle-eye",
        color_start="#008B8B",  # Teal
        color_end="#FFFFFF",    # Surgical white
        hue=180.0,
        motion_type="radial_reveal",
        motion_params={"reveal_speed": 0.1, "scan_speed": 0.5}
    ),
    OrganMask.REPUTATION: OrganVisualStyle(
        name="Reputation",
        glyph="notched-hexagon",
        color_start="#FFBF00",  # Amber
        color_end="#8B4513",    # Rust
        hue=45.0,
        motion_type="creeping_diffusion",
        motion_params={"diffusion_rate": 0.05, "ripple_freq": 5.0}
    ),
    OrganMask.OBFUSCATION: OrganVisualStyle(
        name="Obfuscation",
        glyph="triple-spiral",
        color_start="#4B0082",  # Deep indigo
        color_end="#000000",    # Void black
        hue=260.0,
        motion_type="counter_rotating_spirals",
        motion_params={"layer1_speed": 0.5, "layer2_speed": -0.3, "layer3_osc": 0.7}
    ),
    OrganMask.ESCALATION: OrganVisualStyle(
        name="Escalation",
        glyph="double-barbed-crescent",
        color_start="#DC143C",  # Crimson
        color_end="#FF4500",    # Incandescent orange
        hue=10.0,
        motion_type="heartbeat_surge",
        motion_params={"base_rate": 1.0, "spike_strength": 0.8, "decay_rate": 10.0}
    )
}


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class EcosystemNode:
    """Node in the revenge ecosystem hypergraph"""
    id: str
    kind: str                           # ActorKind, InfrastructureKind, or ArtifactKind value
    label: str

    # Core attributes
    risk_score: float = 0.0
    jurisdiction: str = "unknown"
    anonymity_level: float = 0.5
    activity_level: float = 0.0
    last_seen: float = 0.0

    # Operational attributes
    capabilities: int = 0               # Bitmask
    vulnerabilities: int = 0            # Bitmask
    channels: List[str] = field(default_factory=list)
    reputation: float = 0.5
    cost: float = 0.0

    # Semantic attributes (role-specific)
    motive: Optional[str] = None        # For clients
    emotional_state: Optional[str] = None
    skill_level: Optional[float] = None # For operators
    domain: Optional[str] = None        # OSINT, harassment, SEO, malware
    target_vector: Optional[str] = None # For artifacts
    platform_affinity: Optional[str] = None
    sensitivity: Optional[float] = None

    # Organ membership
    organ_mask: int = OrganMask.NONE

    # Position for visualization
    position: Optional[List[float]] = None

    # Timestamps
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_event_time: float = 0.0

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_hgnode(self) -> Dict[str, Any]:
        """Convert to HypergraphEngine node format"""
        return {
            'id': self.id,
            'kind': self.kind,
            'position': self.position,
            'frequency': self.activity_level * 1000,  # Map to pseudo-frequency
            'labels': {
                'organ_mask': self.organ_mask,
                'risk_score': self.risk_score,
                'jurisdiction': self.jurisdiction,
            },
            'metadata': {
                'label': self.label,
                'anonymity_level': self.anonymity_level,
                'reputation': self.reputation,
                'motive': self.motive,
                'emotional_state': self.emotional_state,
                **self.metadata
            }
        }


@dataclass
class EcosystemHyperedge:
    """Hyperedge in the revenge ecosystem"""
    id: str
    kind: str                           # HyperedgeKind value
    nodes: List[str]                    # Node IDs

    # Core attributes
    weight: float = 1.0
    intent: str = ""
    confidence: float = 0.5
    timeline_start: float = 0.0
    timeline_end: float = 0.0
    automation_ratio: float = 0.0
    jurisdiction_spread: int = 1
    obfuscation_depth: int = 0

    # Visual attributes
    harm_glow: float = 0.0
    last_event_time: float = 0.0

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_hgedge(self) -> Dict[str, Any]:
        """Convert to HypergraphEngine edge format"""
        return {
            'id': self.id,
            'kind': self.kind,
            'nodes': self.nodes,
            'weight': self.weight,
            'labels': {
                'intent': self.intent,
                'confidence': self.confidence,
                'automation_ratio': self.automation_ratio,
                'obfuscation_depth': self.obfuscation_depth,
            },
            'metadata': {
                'harm_glow': self.harm_glow,
                'jurisdiction_spread': self.jurisdiction_spread,
                **self.metadata
            },
            'timestamp': self.timestamp
        }


@dataclass
class EcosystemEvent:
    """Event in the ecosystem (for diff-driven pipeline)"""
    id: str
    event_type: str                     # EcosystemEventType value
    node_ids: List[str] = field(default_factory=list)
    edge_ids: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    # Payload
    intensity: float = 0.0
    budget: float = 0.0
    organ_mask: int = OrganMask.NONE

    # Diff structure
    added_nodes: List[str] = field(default_factory=list)
    updated_nodes: List[str] = field(default_factory=list)
    removed_nodes: List[str] = field(default_factory=list)
    added_edges: List[str] = field(default_factory=list)
    updated_edges: List[str] = field(default_factory=list)
    removed_edges: List[str] = field(default_factory=list)

    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OrganState:
    """Aggregate state for all organs"""
    harassment_intensity: float = 0.0
    doxxing_intensity: float = 0.0
    reputation_intensity: float = 0.0
    obfuscation_depth: float = 0.0
    emotional_heat: float = 0.0

    # Decay rates
    harassment_decay: float = 0.2
    doxxing_decay: float = 0.15
    reputation_decay: float = 0.05
    obfuscation_decay: float = 0.1
    heat_decay: float = 0.3

    def update(self, delta_time: float):
        """Apply decay to all intensities"""
        self.harassment_intensity *= math.exp(-delta_time * self.harassment_decay)
        self.doxxing_intensity *= math.exp(-delta_time * self.doxxing_decay)
        self.reputation_intensity *= math.exp(-delta_time * self.reputation_decay)
        self.obfuscation_depth *= math.exp(-delta_time * self.obfuscation_decay)
        self.emotional_heat *= math.exp(-delta_time * self.heat_decay)

    def to_dict(self) -> Dict[str, float]:
        return {
            'harassment_intensity': self.harassment_intensity,
            'doxxing_intensity': self.doxxing_intensity,
            'reputation_intensity': self.reputation_intensity,
            'obfuscation_depth': self.obfuscation_depth,
            'emotional_heat': self.emotional_heat,
        }


# ============================================================================
# REVENGE ECOSYSTEM ENGINE
# ============================================================================

class RevengeEcosystemEngine:
    """
    Hypergraph engine specialized for revenge-for-hire ecosystem modeling.
    Integrates with HypergraphEngine for unified node/edge management.
    """

    def __init__(self, hypergraph_engine=None):
        self.nodes: Dict[str, EcosystemNode] = {}
        self.edges: Dict[str, EcosystemHyperedge] = {}
        self.events: List[EcosystemEvent] = []
        self.organ_state = OrganState()

        # Indices
        self.nodes_by_kind: Dict[str, Set[str]] = defaultdict(set)
        self.nodes_by_organ: Dict[int, Set[str]] = defaultdict(set)
        self.edges_by_kind: Dict[str, Set[str]] = defaultdict(set)
        self.node_to_edges: Dict[str, Set[str]] = defaultdict(set)

        # External integration
        self.hypergraph_engine = hypergraph_engine
        self.subscribers: List[Callable] = []

        # State
        self._lock = threading.RLock()
        self._sequence = 0
        self._last_update = time.time()

    # ---------- Node Operations ----------

    def add_node(self, node: EcosystemNode) -> str:
        """Add a node to the ecosystem"""
        with self._lock:
            self.nodes[node.id] = node
            self.nodes_by_kind[node.kind].add(node.id)

            # Index by organ
            for organ in OrganMask:
                if organ != OrganMask.NONE and organ != OrganMask.ALL:
                    if node.organ_mask & organ:
                        self.nodes_by_organ[organ].add(node.id)

            # Mirror to HypergraphEngine if available
            if self.hypergraph_engine:
                try:
                    self.hypergraph_engine.add_node(node.to_hgnode())
                except Exception:
                    pass

            self._emit_event('NODE_CREATE', node.id, node.to_dict())
            return node.id

    def update_node(self, node_id: str, **updates) -> Optional[EcosystemNode]:
        """Update node attributes"""
        with self._lock:
            node = self.nodes.get(node_id)
            if not node:
                return None

            for key, value in updates.items():
                if hasattr(node, key):
                    setattr(node, key, value)

            node.updated_at = time.time()

            # Re-index organ membership if changed
            if 'organ_mask' in updates:
                for organ in OrganMask:
                    if organ != OrganMask.NONE and organ != OrganMask.ALL:
                        self.nodes_by_organ[organ].discard(node_id)
                        if node.organ_mask & organ:
                            self.nodes_by_organ[organ].add(node_id)

            # Mirror to HypergraphEngine
            if self.hypergraph_engine:
                try:
                    self.hypergraph_engine.update_node(node_id, **node.to_hgnode())
                except Exception:
                    pass

            self._emit_event('NODE_UPDATE', node_id, node.to_dict())
            return node

    def get_node(self, node_id: str) -> Optional[EcosystemNode]:
        return self.nodes.get(node_id)

    def remove_node(self, node_id: str) -> bool:
        with self._lock:
            if node_id not in self.nodes:
                return False

            node = self.nodes.pop(node_id)
            self.nodes_by_kind[node.kind].discard(node_id)

            for organ in OrganMask:
                if organ != OrganMask.NONE and organ != OrganMask.ALL:
                    self.nodes_by_organ[organ].discard(node_id)

            # Remove from edges
            for edge_id in list(self.node_to_edges.get(node_id, [])):
                self.remove_edge(edge_id)

            self._emit_event('NODE_DELETE', node_id, {})
            return True

    # ---------- Edge Operations ----------

    def add_edge(self, edge: EcosystemHyperedge) -> str:
        """Add a hyperedge to the ecosystem"""
        with self._lock:
            self.edges[edge.id] = edge
            self.edges_by_kind[edge.kind].add(edge.id)

            for node_id in edge.nodes:
                self.node_to_edges[node_id].add(edge.id)

            # Mirror to HypergraphEngine
            if self.hypergraph_engine:
                try:
                    self.hypergraph_engine.add_edge(edge.to_hgedge())
                except Exception:
                    pass

            self._emit_event('EDGE_CREATE', edge.id, edge.to_dict())
            return edge.id

    def update_edge(self, edge_id: str, **updates) -> Optional[EcosystemHyperedge]:
        with self._lock:
            edge = self.edges.get(edge_id)
            if not edge:
                return None

            for key, value in updates.items():
                if hasattr(edge, key):
                    setattr(edge, key, value)

            self._emit_event('EDGE_UPDATE', edge_id, edge.to_dict())
            return edge

    def remove_edge(self, edge_id: str) -> bool:
        with self._lock:
            if edge_id not in self.edges:
                return False

            edge = self.edges.pop(edge_id)
            self.edges_by_kind[edge.kind].discard(edge_id)

            for node_id in edge.nodes:
                self.node_to_edges[node_id].discard(edge_id)

            self._emit_event('EDGE_DELETE', edge_id, {})
            return True

    # ---------- Event Processing ----------

    def process_event(self, event: EcosystemEvent):
        """Process an ecosystem event - updates organ state and node/edge attributes"""
        with self._lock:
            self.events.append(event)
            self._sequence += 1

            event_type = event.event_type

            # Update organ state based on event type
            if event_type == EcosystemEventType.COMMISSION_CREATED.value:
                self.organ_state.emotional_heat += event.intensity * 0.2
                self._process_commission(event)

            elif event_type == EcosystemEventType.TASK_DECOMPOSED.value:
                self.organ_state.harassment_intensity += event.intensity * 0.3
                self._process_task_decomposition(event)

            elif event_type == EcosystemEventType.ARTIFACT_GENERATED.value:
                self._process_artifact_generation(event)

            elif event_type == EcosystemEventType.DATA_SOURCE_LINKED.value:
                self.organ_state.doxxing_intensity += event.intensity * 0.4
                self._process_data_link(event)

            elif event_type == EcosystemEventType.ATTACK_EXECUTED.value:
                self.organ_state.harassment_intensity += event.intensity * 0.5
                self._process_attack(event)

            elif event_type == EcosystemEventType.ESCALATION_TRIGGERED.value:
                self.organ_state.emotional_heat += event.intensity * 0.6
                self._process_escalation(event)

            elif event_type == EcosystemEventType.OBFUSCATION_LAYER_ADDED.value:
                self.organ_state.obfuscation_depth += event.intensity * 0.3
                self._process_obfuscation(event)

            elif event_type == EcosystemEventType.PLATFORM_RESPONSE.value:
                self._process_platform_response(event)

            elif event_type == EcosystemEventType.ATTRIBUTION_LEAK_DETECTED.value:
                self.organ_state.obfuscation_depth -= event.intensity * 0.5
                self._process_attribution_leak(event)

            # Broadcast event
            self._emit_event('ECOSYSTEM_EVENT', event.id, event.to_dict())

    def _process_commission(self, event: EcosystemEvent):
        """Process a commission event"""
        for node_id in event.node_ids:
            node = self.nodes.get(node_id)
            if node:
                node.activity_level += 0.3
                node.last_event_time = event.timestamp
                if node.kind == ActorKind.CLIENT.value:
                    node.organ_mask |= OrganMask.ESCALATION

    def _process_task_decomposition(self, event: EcosystemEvent):
        """Process task decomposition"""
        for node_id in event.node_ids:
            node = self.nodes.get(node_id)
            if node:
                node.activity_level += 0.2
                if node.kind in [ActorKind.BOT.value, ActorKind.SUBCONTRACTOR.value]:
                    node.organ_mask |= OrganMask.HARASSMENT

    def _process_artifact_generation(self, event: EcosystemEvent):
        """Process artifact generation"""
        for edge_id in event.edge_ids:
            edge = self.edges.get(edge_id)
            if edge:
                edge.harm_glow = 1.0
                edge.last_event_time = event.timestamp

        # Determine artifact type and set organ
        organ = event.organ_mask
        if organ == OrganMask.NONE:
            organ = OrganMask.HARASSMENT  # Default

        for node_id in event.added_nodes:
            node = self.nodes.get(node_id)
            if node:
                node.organ_mask |= organ

    def _process_data_link(self, event: EcosystemEvent):
        """Process data source linkage"""
        for node_id in event.node_ids:
            node = self.nodes.get(node_id)
            if node:
                node.risk_score += event.intensity * 0.2
                if node.kind == ArtifactKind.DOXX_PACKET.value:
                    node.organ_mask |= OrganMask.DOXXING

    def _process_attack(self, event: EcosystemEvent):
        """Process attack execution"""
        for node_id in event.node_ids:
            node = self.nodes.get(node_id)
            if node:
                if node.kind == ActorKind.VICTIM.value:
                    node.risk_score += event.intensity * 0.5
                    node.activity_level += 0.1

    def _process_escalation(self, event: EcosystemEvent):
        """Process escalation event"""
        for node_id in event.node_ids:
            node = self.nodes.get(node_id)
            if node:
                node.activity_level *= (1.0 + event.intensity * 0.5)
                node.organ_mask |= OrganMask.ESCALATION

    def _process_obfuscation(self, event: EcosystemEvent):
        """Process obfuscation layer addition"""
        for edge_id in event.edge_ids:
            edge = self.edges.get(edge_id)
            if edge:
                edge.obfuscation_depth += 1

        for node_id in event.node_ids:
            node = self.nodes.get(node_id)
            if node:
                node.anonymity_level = min(1.0, node.anonymity_level + 0.1)
                node.organ_mask |= OrganMask.OBFUSCATION

    def _process_platform_response(self, event: EcosystemEvent):
        """Process platform moderation response"""
        for node_id in event.node_ids:
            node = self.nodes.get(node_id)
            if node:
                # Platform response reduces activity
                node.activity_level *= 0.5
                node.risk_score += 0.1  # Slightly increases risk of detection

    def _process_attribution_leak(self, event: EcosystemEvent):
        """Process attribution leak detection"""
        for node_id in event.node_ids:
            node = self.nodes.get(node_id)
            if node:
                node.anonymity_level = max(0.0, node.anonymity_level - 0.3)
                node.risk_score += event.intensity * 0.4

    # ---------- Tick / Update ----------

    def tick(self, delta_time: float = None):
        """Update the ecosystem state (call per frame/tick)"""
        now = time.time()
        if delta_time is None:
            delta_time = now - self._last_update
        self._last_update = now

        with self._lock:
            # Decay organ state
            self.organ_state.update(delta_time)

            # Update all nodes
            for node in self.nodes.values():
                # Decay activity and risk
                node.activity_level *= math.exp(-delta_time * 0.2)
                node.risk_score *= math.exp(-delta_time * 0.05)

                # Apply organ-specific modulations
                if node.organ_mask & OrganMask.HARASSMENT:
                    node.activity_level += self.organ_state.harassment_intensity * 0.1 * delta_time

                if node.organ_mask & OrganMask.DOXXING:
                    node.risk_score += self.organ_state.doxxing_intensity * 0.15 * delta_time

                if node.organ_mask & OrganMask.REPUTATION:
                    node.reputation += self.organ_state.reputation_intensity * 0.2 * delta_time

                if node.organ_mask & OrganMask.OBFUSCATION:
                    node.risk_score *= 0.99  # Harder to attribute

                if node.organ_mask & OrganMask.ESCALATION:
                    node.activity_level *= (1.0 + self.organ_state.emotional_heat * 0.3 * delta_time)

            # Update all edges
            for edge in self.edges.values():
                # Decay harm glow
                edge.harm_glow *= math.exp(-delta_time * 3.0)

    # ---------- Queries ----------

    def get_organ_nodes(self, organ: OrganMask) -> List[EcosystemNode]:
        """Get all nodes in a specific organ"""
        return [self.nodes[nid] for nid in self.nodes_by_organ.get(organ, [])]

    def get_nodes_by_kind(self, kind: str) -> List[EcosystemNode]:
        """Get all nodes of a specific kind"""
        return [self.nodes[nid] for nid in self.nodes_by_kind.get(kind, [])]

    def get_edges_by_kind(self, kind: str) -> List[EcosystemHyperedge]:
        """Get all edges of a specific kind"""
        return [self.edges[eid] for eid in self.edges_by_kind.get(kind, [])]

    def get_edges_for_node(self, node_id: str) -> List[EcosystemHyperedge]:
        """Get all edges touching a node"""
        return [self.edges[eid] for eid in self.node_to_edges.get(node_id, [])]

    def get_attack_surface(self, victim_id: str) -> Dict[str, Any]:
        """Get the attack surface for a victim"""
        victim = self.nodes.get(victim_id)
        if not victim or victim.kind != ActorKind.VICTIM.value:
            return {}

        edges = self.get_edges_for_node(victim_id)
        attack_edges = [e for e in edges if e.kind == HyperedgeKind.ATTACK_SURFACE.value]

        platforms = set()
        artifacts = set()
        for edge in attack_edges:
            for nid in edge.nodes:
                node = self.nodes.get(nid)
                if node:
                    if node.kind == InfrastructureKind.PLATFORM.value:
                        platforms.add(nid)
                    elif node.kind in [a.value for a in ArtifactKind]:
                        artifacts.add(nid)

        return {
            'victim_id': victim_id,
            'platforms': list(platforms),
            'artifacts': list(artifacts),
            'edges': [e.id for e in attack_edges],
            'risk_score': victim.risk_score
        }

    def trace_obfuscation_layers(self, operator_id: str) -> List[Dict[str, Any]]:
        """Trace obfuscation layers from an operator"""
        layers = []
        visited = set()
        queue = [operator_id]
        depth = 0

        while queue and depth < 10:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            node = self.nodes.get(current_id)
            if node and node.organ_mask & OrganMask.OBFUSCATION:
                layers.append({
                    'node_id': current_id,
                    'kind': node.kind,
                    'depth': depth,
                    'jurisdiction': node.jurisdiction,
                    'anonymity_level': node.anonymity_level
                })

            # Follow obfuscation edges
            for edge in self.get_edges_for_node(current_id):
                if edge.kind == HyperedgeKind.ATTRIBUTION_OBFUSCATION.value:
                    for nid in edge.nodes:
                        if nid not in visited:
                            queue.append(nid)

            depth += 1

        return layers

    # ---------- Metrics ----------

    def get_metrics(self) -> Dict[str, Any]:
        """Get ecosystem metrics"""
        return {
            'total_nodes': len(self.nodes),
            'total_edges': len(self.edges),
            'total_events': len(self.events),
            'organ_state': self.organ_state.to_dict(),
            'nodes_by_kind': {k: len(v) for k, v in self.nodes_by_kind.items()},
            'edges_by_kind': {k: len(v) for k, v in self.edges_by_kind.items()},
            'nodes_by_organ': {
                OrganMask(k).name: len(v)
                for k, v in self.nodes_by_organ.items()
            },
            'sequence': self._sequence,
        }

    # ---------- Event Emission ----------

    def subscribe(self, callback: Callable):
        """Subscribe to ecosystem events"""
        with self._lock:
            self.subscribers.append(callback)

    def _emit_event(self, event_type: str, entity_id: str, data: Dict):
        """Emit event to subscribers"""
        event = {
            'event_type': event_type,
            'entity_id': entity_id,
            'data': data,
            'timestamp': time.time(),
            'sequence': self._sequence
        }

        for sub in self.subscribers:
            try:
                sub(event)
            except Exception:
                pass

    # ---------- Scenario Generation ----------

    def generate_scenario(self, scenario_type: str = "harassment_campaign") -> Dict[str, Any]:
        """Generate a test scenario"""
        if scenario_type == "harassment_campaign":
            return self._generate_harassment_scenario()
        elif scenario_type == "doxxing_operation":
            return self._generate_doxxing_scenario()
        elif scenario_type == "reputation_attack":
            return self._generate_reputation_scenario()
        elif scenario_type == "full_ecosystem":
            return self._generate_full_ecosystem()
        else:
            return self._generate_harassment_scenario()

    def _generate_harassment_scenario(self) -> Dict[str, Any]:
        """Generate a harassment campaign scenario"""
        # Create client
        client = EcosystemNode(
            id=f"client_{int(time.time()*1000)}",
            kind=ActorKind.CLIENT.value,
            label="Anonymous Client",
            motive="personal_grudge",
            emotional_state="angry",
            organ_mask=OrganMask.ESCALATION,
            position=[37.7749 + random.uniform(-0.1, 0.1), -122.4194 + random.uniform(-0.1, 0.1), 0]
        )
        self.add_node(client)

        # Create operator
        operator = EcosystemNode(
            id=f"operator_{int(time.time()*1000)}",
            kind=ActorKind.OPERATOR.value,
            label="Harassment Operator",
            skill_level=0.7,
            domain="harassment",
            reputation=0.8,
            organ_mask=OrganMask.HARASSMENT | OrganMask.OBFUSCATION,
            position=[37.7749 + random.uniform(-0.1, 0.1), -122.4194 + random.uniform(-0.1, 0.1), 0]
        )
        self.add_node(operator)

        # Create victim
        victim = EcosystemNode(
            id=f"victim_{int(time.time()*1000)}",
            kind=ActorKind.VICTIM.value,
            label="Target Individual",
            risk_score=0.3,
            organ_mask=OrganMask.HARASSMENT,
            position=[37.7749 + random.uniform(-0.1, 0.1), -122.4194 + random.uniform(-0.1, 0.1), 0]
        )
        self.add_node(victim)

        # Create bots
        bots = []
        for i in range(5):
            bot = EcosystemNode(
                id=f"bot_{int(time.time()*1000)}_{i}",
                kind=ActorKind.BOT.value,
                label=f"Bot Swarm {i}",
                organ_mask=OrganMask.HARASSMENT,
                position=[37.7749 + random.uniform(-0.2, 0.2), -122.4194 + random.uniform(-0.2, 0.2), 0]
            )
            self.add_node(bot)
            bots.append(bot)

        # Create platform
        platform = EcosystemNode(
            id=f"platform_{int(time.time()*1000)}",
            kind=InfrastructureKind.PLATFORM.value,
            label="Social Media Platform",
            organ_mask=OrganMask.HARASSMENT | OrganMask.REPUTATION,
            position=[37.7749, -122.4194, 0]
        )
        self.add_node(platform)

        # Create payment channel
        payment = EcosystemNode(
            id=f"payment_{int(time.time()*1000)}",
            kind=InfrastructureKind.PAYMENT_CHANNEL.value,
            label="Crypto Wallet",
            organ_mask=OrganMask.OBFUSCATION,
            position=[37.7749 + random.uniform(-0.1, 0.1), -122.4194 + random.uniform(-0.1, 0.1), 0]
        )
        self.add_node(payment)

        # Create commission edge
        commission = EcosystemHyperedge(
            id=f"commission_{int(time.time()*1000)}",
            kind=HyperedgeKind.COMMISSION.value,
            nodes=[client.id, operator.id, payment.id],
            intent="harassment_campaign",
            weight=1.0
        )
        self.add_edge(commission)

        # Create task decomposition edge
        task = EcosystemHyperedge(
            id=f"task_{int(time.time()*1000)}",
            kind=HyperedgeKind.TASK_DECOMPOSITION.value,
            nodes=[operator.id] + [b.id for b in bots],
            intent="swarm_harassment",
            automation_ratio=0.9
        )
        self.add_edge(task)

        # Create attack surface edge
        attack = EcosystemHyperedge(
            id=f"attack_{int(time.time()*1000)}",
            kind=HyperedgeKind.ATTACK_SURFACE.value,
            nodes=[victim.id, platform.id],
            intent="harassment_delivery"
        )
        self.add_edge(attack)

        # Fire commission event
        event = EcosystemEvent(
            id=f"event_{int(time.time()*1000)}",
            event_type=EcosystemEventType.COMMISSION_CREATED.value,
            node_ids=[client.id, operator.id],
            edge_ids=[commission.id],
            intensity=0.8
        )
        self.process_event(event)

        return {
            'scenario': 'harassment_campaign',
            'nodes': [client.id, operator.id, victim.id, platform.id, payment.id] + [b.id for b in bots],
            'edges': [commission.id, task.id, attack.id],
            'events': [event.id]
        }

    def _generate_doxxing_scenario(self) -> Dict[str, Any]:
        """Generate a doxxing operation scenario"""
        # Similar structure but focused on doxxing organ
        client = EcosystemNode(
            id=f"client_{int(time.time()*1000)}",
            kind=ActorKind.CLIENT.value,
            label="Doxxing Client",
            motive="expose_identity",
            organ_mask=OrganMask.ESCALATION | OrganMask.DOXXING,
            position=[37.7749, -122.4194, 0]
        )
        self.add_node(client)

        operator = EcosystemNode(
            id=f"osint_operator_{int(time.time()*1000)}",
            kind=ActorKind.OPERATOR.value,
            label="OSINT Operator",
            skill_level=0.9,
            domain="OSINT",
            organ_mask=OrganMask.DOXXING,
            position=[37.7849, -122.4094, 0]
        )
        self.add_node(operator)

        victim = EcosystemNode(
            id=f"victim_{int(time.time()*1000)}",
            kind=ActorKind.VICTIM.value,
            label="Doxxing Target",
            organ_mask=OrganMask.DOXXING,
            position=[37.7649, -122.4294, 0]
        )
        self.add_node(victim)

        # Data sources
        data_sources = []
        for i, name in enumerate(["Breach Database", "Social Media Scrape", "Public Records"]):
            ds = EcosystemNode(
                id=f"datasource_{int(time.time()*1000)}_{i}",
                kind=InfrastructureKind.DATA_SOURCE.value,
                label=name,
                sensitivity=0.7 + i * 0.1,
                organ_mask=OrganMask.DOXXING,
                position=[37.7749 + (i-1)*0.05, -122.4194 + (i-1)*0.05, 0]
            )
            self.add_node(ds)
            data_sources.append(ds)

        # Doxx packet artifact
        doxx = EcosystemNode(
            id=f"doxx_{int(time.time()*1000)}",
            kind=ArtifactKind.DOXX_PACKET.value,
            label="Compiled Doxx Packet",
            sensitivity=0.95,
            organ_mask=OrganMask.DOXXING,
            position=[37.7699, -122.4244, 0]
        )
        self.add_node(doxx)

        # Data fusion edge
        fusion = EcosystemHyperedge(
            id=f"fusion_{int(time.time()*1000)}",
            kind=HyperedgeKind.DATA_FUSION.value,
            nodes=[ds.id for ds in data_sources] + [operator.id, victim.id],
            intent="identity_compilation",
            confidence=0.85
        )
        self.add_edge(fusion)

        return {
            'scenario': 'doxxing_operation',
            'nodes': [client.id, operator.id, victim.id, doxx.id] + [ds.id for ds in data_sources],
            'edges': [fusion.id]
        }

    def _generate_reputation_scenario(self) -> Dict[str, Any]:
        """Generate a reputation attack scenario"""
        operator = EcosystemNode(
            id=f"seo_operator_{int(time.time()*1000)}",
            kind=ActorKind.OPERATOR.value,
            label="SEO Sabotage Operator",
            domain="SEO",
            organ_mask=OrganMask.REPUTATION,
            position=[37.7749, -122.4194, 0]
        )
        self.add_node(operator)

        victim = EcosystemNode(
            id=f"victim_{int(time.time()*1000)}",
            kind=ActorKind.VICTIM.value,
            label="Business Target",
            organ_mask=OrganMask.REPUTATION,
            position=[37.7849, -122.4094, 0]
        )
        self.add_node(victim)

        # Platforms
        platforms = []
        for name in ["Review Site", "Complaint Board", "Search Engine"]:
            p = EcosystemNode(
                id=f"platform_{name.replace(' ', '_').lower()}_{int(time.time()*1000)}",
                kind=InfrastructureKind.PLATFORM.value,
                label=name,
                organ_mask=OrganMask.REPUTATION,
                position=[37.7749 + random.uniform(-0.1, 0.1), -122.4194 + random.uniform(-0.1, 0.1), 0]
            )
            self.add_node(p)
            platforms.append(p)

        # Reputation payload
        payload = EcosystemNode(
            id=f"rep_payload_{int(time.time()*1000)}",
            kind=ArtifactKind.REPUTATION_PAYLOAD.value,
            label="Fake Reviews & SEO Poison",
            target_vector="search_rankings",
            organ_mask=OrganMask.REPUTATION,
            position=[37.7699, -122.4244, 0]
        )
        self.add_node(payload)

        # Distribution edge
        dist = EcosystemHyperedge(
            id=f"distribution_{int(time.time()*1000)}",
            kind=HyperedgeKind.DISTRIBUTION.value,
            nodes=[payload.id] + [p.id for p in platforms],
            intent="reputation_damage"
        )
        self.add_edge(dist)

        return {
            'scenario': 'reputation_attack',
            'nodes': [operator.id, victim.id, payload.id] + [p.id for p in platforms],
            'edges': [dist.id]
        }

    def _generate_full_ecosystem(self) -> Dict[str, Any]:
        """Generate a full ecosystem with all organs"""
        results = {
            'harassment': self._generate_harassment_scenario(),
            'doxxing': self._generate_doxxing_scenario(),
            'reputation': self._generate_reputation_scenario()
        }

        # Add obfuscation layer connecting everything
        all_operators = [n for n in self.nodes.values() if n.kind == ActorKind.OPERATOR.value]
        if len(all_operators) >= 2:
            proxy = EcosystemNode(
                id=f"proxy_{int(time.time()*1000)}",
                kind=InfrastructureKind.HOSTING_PROXY.value,
                label="Offshore VPN Chain",
                jurisdiction="offshore",
                organ_mask=OrganMask.OBFUSCATION,
                position=[37.7749, -122.5, 0]
            )
            self.add_node(proxy)

            obfuscation = EcosystemHyperedge(
                id=f"obfuscation_{int(time.time()*1000)}",
                kind=HyperedgeKind.ATTRIBUTION_OBFUSCATION.value,
                nodes=[op.id for op in all_operators] + [proxy.id],
                obfuscation_depth=3,
                jurisdiction_spread=4
            )
            self.add_edge(obfuscation)

            results['obfuscation'] = {
                'nodes': [proxy.id],
                'edges': [obfuscation.id]
            }

        return results


# ============================================================================
# SHADER PARAMETER EXPORT
# ============================================================================

def export_shader_uniforms(engine: RevengeEcosystemEngine) -> Dict[str, Any]:
    """Export current state as shader uniforms"""
    state = engine.organ_state

    return {
        # Organ intensities
        'u_harassmentIntensity': state.harassment_intensity,
        'u_doxxingIntensity': state.doxxing_intensity,
        'u_reputationIntensity': state.reputation_intensity,
        'u_obfuscationDepth': state.obfuscation_depth,
        'u_emotionalHeat': state.emotional_heat,

        # Time
        'u_time': time.time(),

        # Node/edge counts
        'u_nodeCount': len(engine.nodes),
        'u_edgeCount': len(engine.edges),

        # Organ colors (as vec3)
        'u_harassmentColor': [1.0, 0.0, 1.0],      # Magenta
        'u_doxxingColor': [0.0, 0.545, 0.545],     # Teal
        'u_reputationColor': [1.0, 0.749, 0.0],    # Amber
        'u_obfuscationColor': [0.294, 0.0, 0.51],  # Indigo
        'u_escalationColor': [0.863, 0.078, 0.235] # Crimson
    }


# ============================================================================
# MODULE EXPORTS
# ============================================================================

__all__ = [
    'ActorKind',
    'InfrastructureKind',
    'ArtifactKind',
    'HyperedgeKind',
    'OrganMask',
    'EcosystemEventType',
    'OrganVisualStyle',
    'ORGAN_STYLES',
    'EcosystemNode',
    'EcosystemHyperedge',
    'EcosystemEvent',
    'OrganState',
    'RevengeEcosystemEngine',
    'export_shader_uniforms'
]
