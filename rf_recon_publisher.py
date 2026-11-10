"""Publish measured RF detections as Recon Entities.

A single-channel receiver measures a signal's frequency, level and SNR. It does not
measure where the transmitter is. Each entity is therefore placed at the RECEIVER's
configured location and says so (``position_kind: RECEIVER_LOCATION``); it never
claims an emitter position. With no receiver location configured, the entity carries
no position at all and only appears in lists.

One entity per signal: a detection is matched to an existing entity whose frequency is
within ``channel_hz``, else a new entity is created. The id is fixed at first sight
(``RF-<kHz>``), so a peak that wanders by a few bins updates one entity instead of
splitting into several. Every field is measured, nothing is inferred.
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Matching radius around an entity's first-seen frequency (window = 2x). 100 kHz keeps a
# broadcast-FM signal (peak wanders +-60 kHz with programme content) as one entity and
# keeps stations on the 200 kHz raster apart. It would merge genuinely distinct
# narrowband signals closer than 100 kHz; lower it via SDRPP_RECON_CHANNEL_HZ for those.
DEFAULT_CHANNEL_HZ = 100_000.0
DEFAULT_MIN_UPDATE_S = 5.0
DEFAULT_POLL_S = 2.0
SOURCE = "rf_observation_publisher"


_DOTENV_KEYS = ("SDRPP_SENSOR_LAT", "SDRPP_SENSOR_LON", "SDRPP_RECON_PUBLISH", "SDRPP_RECON_CHANNEL_HZ")


def _dotenv_keys(path: str) -> Dict[str, str]:
    """Read ONLY this module's own keys from a .env file. The orchestrator's systemd
    environment does not load the repo .env, and that file also holds secrets, so every
    other line is skipped unread-by-intent (parsed past, never kept or logged)."""
    found: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                key, sep, value = line.strip().partition("=")
                if sep and key.strip() in _DOTENV_KEYS:
                    found[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return found


def sensor_location_from_env(environ=None) -> Optional[Tuple[float, float]]:
    """Return (lat, lon) from SDRPP_SENSOR_LAT/LON, None when both are blank.

    One without the other, a non-number, or an out-of-range value raises ValueError
    naming the variable: a half-set position would silently place entities wrongly.
    """
    env = os.environ if environ is None else environ
    raw = {k: (env.get(k) or "").strip() for k in ("SDRPP_SENSOR_LAT", "SDRPP_SENSOR_LON")}
    if not raw["SDRPP_SENSOR_LAT"] and not raw["SDRPP_SENSOR_LON"]:
        return None
    for name, value in raw.items():
        if not value:
            raise ValueError(f"{name} is blank but the other coordinate is set; set both or neither")
    values = {}
    for name, value in raw.items():
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"{name}={value!r} is not a number") from None
        if not math.isfinite(number):
            raise ValueError(f"{name}={value!r} is not finite")
        values[name] = number
    lat, lon = values["SDRPP_SENSOR_LAT"], values["SDRPP_SENSOR_LON"]
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"SDRPP_SENSOR_LAT={lat} is outside -90..90")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"SDRPP_SENSOR_LON={lon} is outside -180..180")
    return lat, lon


class RFReconPublisher:
    def __init__(self, store, post: Callable[[str, dict], Optional[dict]],
                 *, location: Optional[Tuple[float, float]] = None,
                 channel_hz: float = DEFAULT_CHANNEL_HZ,
                 min_update_s: float = DEFAULT_MIN_UPDATE_S,
                 clock: Callable[[], float] = time.time):
        if not channel_hz > 0:
            raise ValueError("channel_hz must be positive")
        self._store = store
        self._post = post
        self._location = location
        self._channel_hz = float(channel_hz)
        self._min_update_s = float(min_update_s)
        self._clock = clock
        self._cursor: Optional[float] = None
        self._seen_at_cursor: set = set()
        self._entities: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self.published = 0
        self.failed = 0

    # -- helpers ---------------------------------------------------------
    def _match(self, freq_hz: float) -> Optional[Dict[str, Any]]:
        best = None
        for ent in self._entities:
            if ent.get("merged_into"):
                continue
            gap = abs(self._centre(ent) - freq_hz)
            if gap <= self._channel_hz and (best is None or gap < abs(self._centre(best) - freq_hz)):
                best = ent
        return best

    @staticmethod
    def _centre(ent: Dict[str, Any]) -> float:
        # Mean of every hit's peak frequency. Matching against the FIRST hit instead left two
        # entities 110 kHz apart unable to absorb one wandering station (live, 100.2-100.35 MHz).
        return ent["freq_sum"] / ent["hits"]

    def _absorb_overlaps(self) -> None:
        """Merge entities whose centres have come within the channel radius.

        The smaller one is folded into the larger and left published once more with
        meta.merged_into (a graph entity cannot be deleted from here: that needs an
        operator session), then it is never updated again.
        """
        while True:
            live = [e for e in self._entities if not e.get("merged_into")]
            pair = None
            for i, a in enumerate(live):
                for b in live[i + 1:]:
                    if abs(self._centre(a) - self._centre(b)) <= self._channel_hz:
                        pair = (a, b) if a["hits"] >= b["hits"] else (b, a)
                        break
                if pair:
                    break
            if pair is None:
                return
            keep, gone = pair
            keep["hits"] += gone["hits"]
            keep["freq_sum"] += gone["freq_sum"]
            keep["best_snr_db"] = max(keep["best_snr_db"], gone["best_snr_db"])
            keep["first_seen"] = min(keep["first_seen"], gone["first_seen"])
            if gone["last_seen"] >= keep["last_seen"]:
                for k in ("last_seen", "last_snr_db", "noise_floor_dbfs", "sensor_id"):
                    keep[k] = gone[k]
            if gone["peak_dbfs"] > keep["peak_dbfs"]:
                keep["peak_dbfs"], keep["frequency_hz"] = gone["peak_dbfs"], gone["frequency_hz"]
            keep["dirty"] = True
            gone["merged_into"] = keep["entity_id"]
            gone["dirty"] = True

    def _payload(self, ent: Dict[str, Any]) -> Dict[str, Any]:
        mhz = ent["frequency_hz"] / 1e6
        meta = {
            "evidence_class": "OBSERVED",
            "frequency_hz": ent["frequency_hz"],
            "peak_dbfs": ent["peak_dbfs"],
            "best_snr_db": ent["best_snr_db"],
            "last_snr_db": ent["last_snr_db"],
            "noise_floor_dbfs": ent["noise_floor_dbfs"],
            "hit_count": ent["hits"],
            "first_seen": ent["first_seen"],
            "last_seen": ent["last_seen"],
            "sensor_id": ent["sensor_id"],
            "position_kind": "RECEIVER_LOCATION" if self._location else "NONE",
            "position_note": ("Position is the receiver's; the emitter location is unknown."
                              if self._location else "No receiver location configured."),
            "signal_family": ent["signal_family"],
            "modulation": ent["modulation"],
        }
        payload = {
            "entity_id": ent["entity_id"],
            "name": f"RF {mhz:.3f} MHz",
            "type": "RECON_ENTITY",
            "disposition": "NEUTRAL",
            "ontology": "rf_signal",
            "source": SOURCE,
            "meta": meta,
        }
        if ent.get("merged_into"):
            meta["merged_into"] = ent["merged_into"]
            payload["name"] += " (merged into " + ent["merged_into"] + ")"
        if ent.get("mission_id"):
            payload["mission_id"] = ent["mission_id"]
        if self._location:
            payload["location"] = {"lat": self._location[0], "lon": self._location[1], "altitude_m": 0}
        return payload

    # -- one polling step --------------------------------------------------
    def step(self) -> int:
        """Fold new detections into entities and publish the due ones. Returns posts made."""
        with self._lock:
            return self._step()

    def _step(self) -> int:
        rows = self._store.query(since=self._cursor, limit=500)
        for row in reversed(rows):  # query() is newest-first; fold oldest-first
            key = row["evidence_id"]
            if self._cursor is not None and row["observed_at"] == self._cursor and key in self._seen_at_cursor:
                continue
            if self._cursor is None or row["observed_at"] > self._cursor:
                self._cursor = row["observed_at"]
                self._seen_at_cursor = set()
            self._seen_at_cursor.add(key)
            freq = float(row["peak_frequency_hz"])
            ent = self._match(freq)
            if ent is None:
                ent = {"entity_id": f"RF-{int(round(freq / 1000.0)):06d}kHz", "frequency_hz": freq, "freq_sum": 0.0,
                       "peak_dbfs": row["peak_dbfs"], "hits": 0, "best_snr_db": row["snr_db"], "first_seen": row["observed_at"],
                       "last_posted": None, "dirty": True}
                self._entities.append(ent)
            ent["hits"] += 1
            ent["freq_sum"] += freq
            ent["dirty"] = True
            ent["best_snr_db"] = max(ent["best_snr_db"], row["snr_db"])
            ent["last_snr_db"] = row["snr_db"]
            if row["peak_dbfs"] >= ent["peak_dbfs"]:  # report the strongest hit's frequency and level
                ent["peak_dbfs"] = row["peak_dbfs"]
                ent["frequency_hz"] = freq
            ent["noise_floor_dbfs"] = row["noise_floor_dbfs"]
            ent["last_seen"] = row["observed_at"]
            ent["sensor_id"] = row["sensor_id"]
            ent["signal_family"] = row.get("signal_family", "UNCLASSIFIED")
            ent["modulation"] = row.get("modulation", "UNRESOLVED")
            ent["mission_id"] = row.get("mission_id")
        self._absorb_overlaps()
        posts = 0
        now = self._clock()
        for ent in self._entities:
            if not ent["dirty"]:
                continue
            if ent["last_posted"] is not None and now - ent["last_posted"] < self._min_update_s:
                continue
            if self._post("/api/recon/entity", self._payload(ent)) is None:
                self.failed += 1  # stays dirty; retried on the next step
                continue
            ent["dirty"] = False
            ent["last_posted"] = now
            self.published += 1
            posts += 1
        return posts

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"entities": len(self._entities), "published": self.published,
                    "failed": self.failed, "positioned": self._location is not None,
                    "position_kind": "RECEIVER_LOCATION" if self._location else "NONE"}


def start_publisher(store, port_fn: Callable[[], Optional[int]],
                    proxy_post: Callable[..., Optional[dict]], log=None,
                    environ=None) -> Optional[RFReconPublisher]:
    """Start the daemon polling thread; None when disabled via SDRPP_RECON_PUBLISH=0."""
    env = dict(os.environ if environ is None else environ)
    if environ is None:
        for key, value in _dotenv_keys(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")).items():
            env.setdefault(key, value)
    if (env.get("SDRPP_RECON_PUBLISH", "1").strip().lower() in ("0", "false", "off", "no")):
        return None
    location = sensor_location_from_env(env)
    channel = float(env.get("SDRPP_RECON_CHANNEL_HZ", DEFAULT_CHANNEL_HZ) or DEFAULT_CHANNEL_HZ)

    def post(path: str, body: dict) -> Optional[dict]:
        port = port_fn()
        return None if port is None else proxy_post(port, path, body, timeout=5)

    pub = RFReconPublisher(store, post, location=location, channel_hz=channel)

    def loop():
        while True:
            try:
                pub.step()
            except Exception:  # never let the poller die
                if log:
                    log.exception("RF recon publisher step failed")
            time.sleep(DEFAULT_POLL_S)

    threading.Thread(target=loop, name="rf-recon-publisher", daemon=True).start()
    if log:
        log.info("RF recon publisher started (position: %s)",
                 "receiver location" if location else "none configured")
    return pub
