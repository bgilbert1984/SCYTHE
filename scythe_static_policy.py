"""Deny-by-default policy for the catch-all static-file routes.

Both Flask apps serve files from their own source directory, which also holds
databases, logs, scripts and local configuration. Flask's path containment
(`send_from_directory`) only stops traversal out of that directory; it does not
decide whether a file inside it is meant to be public. This module does.

A path is public only if every component is non-hidden, no component is a known
private directory, and the extension is on the allowlist. Anything else is a 404,
including files that do not exist, so the response does not reveal which private
files are present.
"""
import posixpath

PUBLIC_EXTENSIONS = frozenset({
    '.html', '.htm', '.js', '.mjs', '.css',
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp',
    '.woff', '.woff2', '.ttf', '.otf',
    '.glb', '.gltf', '.wasm', '.mp4', '.webm', '.wav', '.mp3',
    '.md', '.json', '.geojson', '.txt',
    # Dataset payloads named by the manifests under datasets/ (the regional RF demo fetches the .u16le).
    '.u16le', '.float64le',
})

PRIVATE_DIRECTORIES = frozenset({
    '__pycache__', 'venv', '.venv', 'env', 'tests', 'secrets', 'private',
    'instance', 'instances', 'metrics_logs', 'logs', 'deploy', 'tools',
})

# Allowed extension, private by name: a .json/.txt next to the code is often
# configuration, credentials or an export.
PRIVATE_NAME_MARKERS = (
    'secret', 'token', 'credential', 'passw', 'private', 'apikey', 'api_key',
    'api-key', 'auth', 'session', 'requirements', 'id_rsa', 'id_ed25519',
)


def is_public_static(filename):
    """True if `filename` (a URL path relative to the served root) may be served."""
    if '\x00' in filename or '\\' in filename:
        return False
    normal = posixpath.normpath(filename)
    if normal != filename.rstrip('/'):
        return False
    parts = normal.split('/')
    for part in parts:
        if not part or part.startswith('.') or part.lower() in PRIVATE_DIRECTORIES:
            return False
    ext = posixpath.splitext(parts[-1])[1].lower()
    if ext not in PUBLIC_EXTENSIONS:
        return False
    if ext in ('.json', '.txt', '.geojson'):
        lowered = parts[-1].lower()
        if any(marker in lowered for marker in PRIVATE_NAME_MARKERS):
            return False
    return True
