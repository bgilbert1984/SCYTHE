"""new_host_pcapng_logger.py — Track new hosts and capture pcapng files.

This module monitors network flow events from eve-streamer and automatically
captures pcapng files when new hosts are detected on the network.

Features:
  - Maintains a persistent SQLite database of observed hosts
  - Monitors eve-streamer flow events for new src/dst IPs
  - Automatically captures pcapng files for new hosts using tcpdump
  - Stores minimal pcapng captures in /ftp_share/pcapng/
  - Provides host inventory and discovery statistics

Architecture:
  - Reads from eve-streamer WebSocket stream (/ws)
  - Tracks host IPs and first-seen timestamps in SQLite
  - Triggers tcpdump capture when new host detected
  - Stores captures with metadata in FTP share for easy retrieval

Database Schema:
  - hosts: ip, first_seen, last_seen, packet_count, pcapng_file, notes
"""

import asyncio
import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Set
import uuid

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_EVE_WS_URL = "ws://localhost:8081/ws"
DEFAULT_PCAPNG_DIR = "/home/spectrcyde/NerfEngine/ftp_share/pcapng"
DEFAULT_DB_PATH = "/home/spectrcyde/NerfEngine/new_hosts.db"
DEFAULT_CAPTURE_DURATION = 10  # seconds per host capture
DEFAULT_CAPTURE_PACKET_LIMIT = 1000  # packets per capture

# Global state
_host_db: Optional[sqlite3.Connection] = None
_seen_hosts: Set[str] = set()
_capture_lock = threading.Lock()


def _get_db() -> sqlite3.Connection:
    """Get or create the host tracking database."""
    global _host_db
    if _host_db is None:
        db_path = os.environ.get("NEW_HOSTS_DB", DEFAULT_DB_PATH)
        _host_db = sqlite3.connect(db_path, check_same_thread=False)
        _host_db.execute("PRAGMA journal_mode=WAL")
        _init_db_schema()
    return _host_db


def _init_db_schema() -> None:
    """Initialize the database schema if not present."""
    db = _get_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS hosts (
            ip TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            packet_count INTEGER DEFAULT 0,
            pcapng_file TEXT,
            notes TEXT,
            capture_status TEXT DEFAULT 'pending'
        )
        """
    )
    db.commit()
    logger.info("[NewHostLogger] Database schema initialized")


def _is_new_host(ip: str) -> bool:
    """Check if an IP is new to the database."""
    if ip in _seen_hosts:
        return False

    db = _get_db()
    cursor = db.execute("SELECT ip FROM hosts WHERE ip = ?", (ip,))
    exists = cursor.fetchone() is not None

    if not exists:
        _seen_hosts.add(ip)
        return True

    _seen_hosts.add(ip)
    return False


def _add_host(ip: str, notes: str = "") -> None:
    """Add a new host to the database."""
    if not _is_valid_ip(ip):
        return

    now = datetime.utcnow().isoformat()
    db = _get_db()

    try:
        db.execute(
            """
            INSERT OR REPLACE INTO hosts
            (ip, first_seen, last_seen, notes, capture_status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ip, now, now, notes, "pending")
        )
        db.commit()
        logger.info(f"[NewHostLogger] New host registered: {ip}")
    except Exception as e:
        logger.error(f"[NewHostLogger] Failed to add host {ip}: {e}")


def _update_host_seen(ip: str) -> None:
    """Update the last_seen timestamp for a host."""
    if not _is_valid_ip(ip):
        return

    now = datetime.utcnow().isoformat()
    db = _get_db()

    try:
        db.execute(
            "UPDATE hosts SET last_seen = ?, packet_count = packet_count + 1 WHERE ip = ?",
            (now, ip)
        )
        db.commit()
    except Exception as e:
        logger.error(f"[NewHostLogger] Failed to update host {ip}: {e}")


def _is_valid_ip(ip: str) -> bool:
    """Check if IP is valid and not localhost/private."""
    if not ip:
        return False

    # Skip localhost and reserved ranges
    if ip.startswith(("127.", "0.", "255.")):
        return False

    # Skip link-local (169.254.x.x)
    if ip.startswith("169.254"):
        return False

    parts = ip.split(".")
    if len(parts) != 4:
        return False

    try:
        for part in parts:
            num = int(part)
            if num < 0 or num > 255:
                return False
        return True
    except ValueError:
        return False


def _trigger_pcapng_capture(ip: str) -> Optional[str]:
    """Trigger a pcapng capture for the new host.

    Returns the path to the generated pcapng file if successful.
    """
    with _capture_lock:
        pcapng_dir = os.environ.get("PCAPNG_DIR", DEFAULT_PCAPNG_DIR)
        os.makedirs(pcapng_dir, exist_ok=True)

        # Generate filename with timestamp and unique ID
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        filename = f"new_host_{ip.replace('.', '_')}_{timestamp}_{unique_id}.pcapng"
        filepath = os.path.join(pcapng_dir, filename)

        try:
            duration = int(os.environ.get("PCAPNG_CAPTURE_DURATION", DEFAULT_CAPTURE_DURATION))
            packet_limit = int(os.environ.get("PCAPNG_PACKET_LIMIT", DEFAULT_CAPTURE_PACKET_LIMIT))

            # Build tcpdump command to capture packets to/from the new host
            cmd = [
                "tcpdump",
                "-i", "any",
                "-c", str(packet_limit),
                "-G", str(duration),
                "-w", filepath,
                "-n",
                f"host {ip}"
            ]

            logger.info(f"[NewHostLogger] Starting pcapng capture for {ip}: {filepath}")

            # Run tcpdump with timeout
            try:
                subprocess.run(
                    cmd,
                    timeout=duration + 5,
                    capture_output=True,
                    check=False
                )

                # Verify file was created and has content
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    logger.info(f"[NewHostLogger] Captured {os.path.getsize(filepath)} bytes for {ip}")
                    return filepath
                else:
                    logger.warning(f"[NewHostLogger] Capture file is empty or missing: {filepath}")
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    return None

            except subprocess.TimeoutExpired:
                logger.warning(f"[NewHostLogger] Capture timeout for {ip}")
                return filepath if os.path.exists(filepath) else None
            except FileNotFoundError:
                logger.error("[NewHostLogger] tcpdump not found. Install with: apt-get install tcpdump")
                return None

        except Exception as e:
            logger.error(f"[NewHostLogger] Failed to capture pcapng for {ip}: {e}")
            return None


def _update_host_capture(ip: str, filepath: Optional[str], status: str) -> None:
    """Update host record with capture file info."""
    db = _get_db()

    try:
        filename = os.path.basename(filepath) if filepath else None
        db.execute(
            "UPDATE hosts SET pcapng_file = ?, capture_status = ? WHERE ip = ?",
            (filename, status, ip)
        )
        db.commit()
    except Exception as e:
        logger.error(f"[NewHostLogger] Failed to update capture status for {ip}: {e}")


def process_flow_event(event: Dict) -> None:
    """Process a flow event and check for new hosts.

    Args:
        event: FlowEvent dict with src_ip, dst_ip, etc.
    """
    src_ip = event.get("src_ip", "")
    dst_ip = event.get("dst_ip", "")

    for ip in [src_ip, dst_ip]:
        if not ip:
            continue

        # Check if host is new
        if _is_new_host(ip):
            logger.info(f"[NewHostLogger] ✓ NEW HOST DETECTED: {ip}")

            # Add to database
            _add_host(ip, notes=f"First seen at {datetime.utcnow().isoformat()}")

            # Schedule capture (non-blocking)
            threading.Thread(
                target=_async_capture,
                args=(ip,),
                daemon=True
            ).start()
        else:
            # Update last seen
            _update_host_seen(ip)


def _async_capture(ip: str) -> None:
    """Asynchronously capture pcapng for a new host."""
    try:
        filepath = _trigger_pcapng_capture(ip)
        if filepath:
            _update_host_capture(ip, filepath, "completed")
        else:
            _update_host_capture(ip, None, "failed")
    except Exception as e:
        logger.error(f"[NewHostLogger] Capture failed for {ip}: {e}")
        _update_host_capture(ip, None, "error")


def get_host_inventory() -> list:
    """Return the list of all tracked hosts."""
    db = _get_db()
    cursor = db.execute(
        """
        SELECT ip, first_seen, last_seen, packet_count, pcapng_file, capture_status
        FROM hosts
        ORDER BY first_seen DESC
        """
    )

    hosts = []
    for row in cursor.fetchall():
        hosts.append({
            "ip": row[0],
            "first_seen": row[1],
            "last_seen": row[2],
            "packet_count": row[3],
            "pcapng_file": row[4],
            "capture_status": row[5]
        })

    return hosts


def get_discovery_stats() -> Dict:
    """Return discovery statistics."""
    db = _get_db()
    cursor = db.execute("SELECT COUNT(*), MIN(first_seen), MAX(first_seen) FROM hosts")
    row = cursor.fetchone()

    total = row[0] if row else 0
    first = row[1] if row and row[1] else None
    last = row[2] if row and row[2] else None

    # Count captures
    cursor = db.execute("SELECT COUNT(*) FROM hosts WHERE pcapng_file IS NOT NULL")
    captured_row = cursor.fetchone()
    captured = captured_row[0] if captured_row else 0

    return {
        "total_hosts": total,
        "hosts_captured": captured,
        "first_discovery": first,
        "last_discovery": last,
        "pcapng_directory": os.environ.get("PCAPNG_DIR", DEFAULT_PCAPNG_DIR)
    }


def cleanup_old_hosts(days: int = 30) -> int:
    """Remove hosts not seen in N days."""
    db = _get_db()
    cutoff = datetime.utcnow() - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

    cursor = db.execute(
        "DELETE FROM hosts WHERE last_seen < ?",
        (cutoff_iso,)
    )
    db.commit()

    deleted = cursor.rowcount
    if deleted > 0:
        logger.info(f"[NewHostLogger] Cleaned up {deleted} hosts not seen in {days} days")

    return deleted


def initialize():
    """Initialize the new host logger module."""
    logger.info("[NewHostLogger] Initializing...")

    # Create database
    _get_db()

    # Load existing hosts into memory cache
    db = _get_db()
    cursor = db.execute("SELECT ip FROM hosts")
    for row in cursor.fetchall():
        _seen_hosts.add(row[0])

    logger.info(f"[NewHostLogger] Loaded {len(_seen_hosts)} existing hosts from database")
    logger.info(f"[NewHostLogger] pcapng directory: {os.environ.get('PCAPNG_DIR', DEFAULT_PCAPNG_DIR)}")


if __name__ == "__main__":
    # Test the module
    logging.basicConfig(level=logging.INFO)
    initialize()

    # Simulate some events
    test_events = [
        {"src_ip": "8.8.8.8", "dst_ip": "10.0.0.1"},
        {"src_ip": "8.8.4.4", "dst_ip": "10.0.0.1"},
        {"src_ip": "1.1.1.1", "dst_ip": "10.0.0.1"},
    ]

    for event in test_events:
        print(f"Processing: {event}")
        process_flow_event(event)

    print("\nHost Inventory:")
    for host in get_host_inventory():
        print(f"  {host['ip']} - {host['first_seen']} - {host['capture_status']}")

    print("\nStats:")
    print(json.dumps(get_discovery_stats(), indent=2))
