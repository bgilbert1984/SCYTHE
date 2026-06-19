"""
MapTileCache — Server-side persistent tile proxy with 24-hour retention.

Caches map tiles from external providers (OSM, Stadia) to improve robustness
and enable offline operation. Prevents provider rate-limiting.
"""

import os
import time
import logging
import requests
from threading import Lock

logger = logging.getLogger(__name__)

class MapTileCache:
    """Hardened tile proxy with local filesystem persistence."""

    def __init__(self, cache_dir: str = 'data/map_tiles'):
        self._dir = cache_dir
        self._lock = Lock()
        self._expiry = 86400  # 24 hours
        os.makedirs(self._dir, exist_ok=True)

        self._providers = {
            'osm': 'https://a.tile.openstreetmap.org/{z}/{x}/{y}.png',
            'stadia_dark': 'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png',
            'stadia_bright': 'https://tiles.stadiamaps.com/tiles/osm_bright/{z}/{x}/{y}{r}.png',
        }

    def get_tile(self, provider: str, z: int, x: int, y: int, api_key: str = '') -> tuple[bytes, str] | None:
        """Fetch tile from cache or provider. Returns (data, content_type) or None."""
        if provider not in self._providers:
            return None

        tile_path = os.path.join(self._dir, provider, str(z), str(x), f"{y}.png")

        # Check cache
        if os.path.exists(tile_path):
            if time.time() - os.path.getmtime(tile_path) < self._expiry:
                with open(tile_path, 'rb') as f:
                    return f.read(), 'image/png'
            else:
                logger.debug(f'[TileCache] Tile expired: {provider}/{z}/{x}/{y}')

        # Fetch from provider
        url_template = self._providers[provider]
        url = url_template.format(z=z, x=x, y=y, r='')
        if api_key:
            url += f"?api_key={api_key}"

        try:
            r = requests.get(url, timeout=10, headers={'User-Agent': 'ScytheMapCache/1.0'})
            if r.status_code == 200:
                data = r.content
                self._save_to_cache(tile_path, data)
                return data, r.headers.get('Content-Type', 'image/png')
            else:
                logger.warning(f'[TileCache] Provider error {r.status_code}: {url}')
        except Exception as e:
            logger.error(f'[TileCache] Fetch failed: {e}')

        # Fallback to expired cache if available
        if os.path.exists(tile_path):
            with open(tile_path, 'rb') as f:
                return f.read(), 'image/png'

        return None

    def _save_to_cache(self, path: str, data: bytes):
        """Atomic write to cache directory."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + '.tmp'
        try:
            with open(tmp_path, 'wb') as f:
                f.write(data)
            os.replace(tmp_path, path)
        except Exception as e:
            logger.error(f'[TileCache] Write failed: {e}')
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def vacuum(self):
        """Remove tiles older than 7 days to manage disk space."""
        MAX_DISK_AGE = 86400 * 7
        now = time.time()
        removed = 0
        for root, dirs, files in os.walk(self._dir):
            for f in files:
                p = os.path.join(root, f)
                if now - os.path.getmtime(p) > MAX_DISK_AGE:
                    os.remove(p)
                    removed += 1
        if removed:
            logger.info(f'[TileCache] vacuumed {removed} old tiles')
