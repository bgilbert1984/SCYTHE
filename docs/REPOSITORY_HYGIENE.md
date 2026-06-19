# Repository Hygiene

SCYTHE should be pushed from a dedicated clean clone, not directly from a busy
NerfEngine runtime tree. On this workstation the clean public clone is:

```bash
/home/spectrcyde/SCYTHE
```

Use the manifest-based sync helper when promoting files from NerfEngine:

```bash
cd /home/spectrcyde/SCYTHE
SCYTHE_SOURCE_DIR=/home/spectrcyde/NerfEngine tools/sync_from_nerfengine.sh
tools/preflight_secret_scan.sh
git status --short
```

The sync helper copies only the explicit public export manifest. It does not
copy runtime databases, instance state, PCAP captures, logs, or other bulky
local telemetry.

Live-data credentials belong in environment variables or browser runtime
configuration:

```bash
AISSTREAM_API_KEY=
N2YO_API_KEY=
CESIUM_ION_TOKEN=
STADIA_API_KEY=
```

Enable the checked-in pre-push hook in a clone with:

```bash
git config core.hooksPath .githooks
```
