import time
from typing import Dict, Any

class HostConfidenceEngine:
    def __init__(self):
        # HostID -> {score, behaviors, last_updated}
        self.registry = {}

    def get_base_score(self, host_identity: Dict[str, Any]) -> float:
        score = 0.0
        # Signals
        if host_identity.get("is_new_ip"): score += 20
        if host_identity.get("foreign_asn"): score += 15
        if host_identity.get("high_entropy"): score += 25
        if host_identity.get("lateral_movement"): score += 35
        if host_identity.get("unusual_ports"): score += 15
        if host_identity.get("ja3_rarity"): score += 25
        if host_identity.get("container_breakout"): score += 40
        return min(score, 100.0)

    def process_event(self, host_ip: str, signal: str, value: Any):
        if host_ip not in self.registry:
            self.registry[host_ip] = {"score": 0.0, "behaviors": set()}

        # Simple rule-based weight assignment
        weights = {
            "new_ip": 20,
            "foreign_asn": 15,
            "high_entropy": 25,
            "lateral_movement": 35,
            "container_breakout": 40
        }

        self.registry[host_ip]["score"] += weights.get(signal, 5)
        self.registry[host_ip]["behaviors"].add(signal)
        self.registry[host_ip]["last_updated"] = time.time()

        return self.registry[host_ip]

    def get_action(self, host_ip: str) -> str:
        score = self.registry.get(host_ip, {}).get("score", 0.0)
        if score < 20: return "registry_only"
        if score < 40: return "micro_pcap"
        if score < 60: return "zeek_extraction"
        return "escalation_pipeline"
