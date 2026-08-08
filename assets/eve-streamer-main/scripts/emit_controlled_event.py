#!/usr/bin/env python3
"""Append one explicitly synthetic flow to Eve Streamer's controlled feed."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "runtime" / "eve-live.json"


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else "10.20.30.41"
    destination = sys.argv[2] if len(sys.argv) > 2 else "8.8.4.4"
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event_type": "test_flow",
        "src_ip": source,
        "src_port": 49152,
        "dest_ip": destination,
        "dest_port": 443,
        "proto": "TCP",
        "controlled_event_id": str(uuid.uuid4()),
    }
    FEED.parent.mkdir(parents=True, exist_ok=True)
    with FEED.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, separators=(",", ":")) + "\n")
        stream.flush()
    print(json.dumps({"status": "appended", "feed": str(FEED), "event": event}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
