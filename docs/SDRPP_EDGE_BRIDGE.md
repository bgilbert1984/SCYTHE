# SDR++ Edge Bridge

RF SCYTHE keeps SDR++ as a separate native process. The browser and Flask
server never load or link SDR++ libraries; the bridge connects only to the two
local interfaces supplied by SDR++ modules.

## AlmaLinux 10 / WSL NESDR setup

The repository vendors SDR++ source but does not install a system-wide build.
For the minimal SCYTHE edge build, install the required packages from AlmaLinux
and EPEL, then use the checked-in builder:

```bash
sudo dnf --disablerepo=tailscale-stable,unityhub install -y \
  cmake gcc-c++ fftw-devel glfw-devel volk-devel libzstd-devel \
  libusb1-devel rtl-sdr-devel
scripts/build_sdrpp_edge_alma10.sh
scripts/run_sdrpp_edge.sh
```

The builder installs under `~/.local/share/scythe/sdrpp-edge` and enables only
the RTL-SDR source, a GUI VFO path, IQ Exporter, and Rigctl Server plus their
small supporting modules. It does not modify `/usr`.

The vendored SDR++ tree carries a bounded first-frame guard: waterfall FFT
producers skip the frame until `waterfallHeight > 0`, and `--autostart` waits
for that layout. That avoids the upstream `WaterFall::getFFTBuffer()`
modulo-by-zero on the first FFT frame. `getFFTBuffer()` / `pushFFT()` now use
an acquire flag (`fftBufferHeld`) so unlock does not depend on a second layout
check. `scripts/run_sdrpp_edge.sh` passes `--autostart` by default. Set
`SCYTHE_SDRPP_AUTOSTART=0` to keep a manual Play click.

For the SCYTHE NESDR SMArt v5 sensor used by this deployment, configure SDR++
for serial `14530058`, 2.048 MS/s, localhost Int16 IQ Exporter on port 1234,
and localhost Rigctl on port 4532. A 4096-point FFT then has 500 Hz native bin
spacing before the bridge's bounded display downsampling.

The running SCYTHE instance is a child of the user-level orchestrator service,
so set the `SDRPP_*` variables on `scythe-orchestrator.service`, not on a
nonexistent per-instance service. The child inherits that environment when the
orchestrator starts it. SDR++ itself owns the USB device and must run from a
login session whose active groups include `rtlsdr`; the web process does not
need direct USB access.

## SDR++ configuration

Create and enable these module instances in SDR++:

1. **IQ Exporter**
   - Mode: `Baseband` or `VFO`
   - Protocol: `TCP (Server)`
   - Host: `127.0.0.1`
   - Port: `1234`
   - Sample type: `Int16`
   - Sample rate: `1.0 MS/s`
   - Start the exporter after choosing the SDR source.
2. **Rigctl Server**
   - Host: `127.0.0.1`
   - Port: `4532`
   - Select the desired radio VFO.
   - Enable tuning and start listening.

The bridge sample type and rate must exactly match IQ Exporter. Copy the SDR++
variables from `.env.example` into the instance environment and adjust them if
the exporter uses different settings.

Set `SDRPP_AUTO_START=true` to make RF SCYTHE begin reconnecting at server
startup. Otherwise the first **Scan Spectrum** action or `POST /api/sdr/start`
starts it. A failed connection backs off automatically and does not substitute
mock RF samples.

`SCYTHE_RF_CAPTURE_OWNER=orchestrator` makes the orchestrator the only process
that opens the IQ exporter. Child instances are spawned with
`SDRPP_AUTO_START=false` and `SCYTHE_PROCESS_ROLE=child`. Their Spectrum
workbench MCP tools proxy bounded reads to the orchestrator broker.

## Browser/API boundary

All routes require the normal operator session in production. Internal callers
may use the configured `X-Internal-Token`.

- `GET /api/sdr/status?control=true`
- `POST /api/sdr/start`
- `POST /api/sdr/stop`
- `POST /api/sdr/tune` with `frequency_hz`, optional `mode`, and `bandwidth_hz`
- `POST /api/sdr/config` for FFT/sample interpretation settings
- `GET /api/sdr/spectrum/latest`
- `GET /api/sdr/spectrum/stream` for authenticated NDJSON frames

The stream is bounded to `SDRPP_MAX_BINS` bins and `SDRPP_FPS` frames per
second. Raw IQ remains on the edge and is not forwarded to browsers.

## Receiver display context

The regional page can show the configured NESDR as an `rf_receiver_sensor`
display-context node and as an interactive Cesium `RF RX` vantage marker. The
operator must explicitly press **VANTAGE** and grant browser geolocation; the
coordinate remains session-local and is never written into the canonical graph
or its content-addressed revision. Clearing VANTAGE removes both projections.

The node keeps three claims separate:

- RF bridge configuration/runtime status (`RF_BRIDGE_RUNTIME_STATUS`);
- browser location (`MEASURED_BROWSER_GEOLOCATION`, with browser accuracy);
- device presence (`CONFIGURED_NOT_USB_ATTESTED`).

Consequently, `reconnecting` or `IQ DISCONNECTED` remains visible and is never
presented as a successful USB or RF capture attestation. Raw IQ is not exposed.

## MCP evidence bridge

Significant edge FFT peaks are reduced to a bounded observation record with a
stable `evidence_id`, timestamp, sensor, frequency, power, noise floor, and SNR.
No raw IQ or unbounded waterfall history enters model context. Configure the
threshold, frequency deduplication bucket, cooldown, and store capacity with
the `SDRPP_DETECTION_*` and `SDRPP_OBSERVATION_MAX` variables in `.env.example`.

Authenticated self-hosted AI clients can use these read-only MCP tools:

- `rf_bridge_status`
- `rf_spectrum_snapshot`
- `rf_observations_query`
- `rf_correlate_graph`
- `rf_insight_context`
- `rf_sparse_status`
- `rf_sparse_supports_query`
- `rf_sparse_insight_context`

Peak FFT detections remain `OBSERVED` on `/api/graphops/rf-observations`.
Residual windows and OMP supports are a second family:
M1 uses deterministic
peak-track estimation for stationary or drifting carriers, plus OMP-assisted
periodic-amplitude recovery against a four-second bounded FFT window. Noise
and empty windows emit `NO_SUPPORT`, `INSUFFICIENT_EVIDENCE`, or
`NOISE_COMPATIBLE`. `periodic_sideband` / `spacing_hz` are reserved until a
spectral triplet at `fc ± fm` is actually detected. Supports record both
native FFT bin width and analysis bin width after display downsamplingupports`

Those records are `DERIVED_INFERENCE`. They never claim range, AoA, or blade
length. Raw IQ and full waterfalls stay on the edge. Sparse recovery currently
supports stationary carrier, linear drift, and periodic sideband atoms against
a four-second bounded FFT window.

RF facts are labelled `OBSERVED`; temporal graph correlations are labelled
`INFERRED` and explicitly do not claim causality. `RF_CORRELATE` in the
GraphOps Copilot DSL uses the same evidence store and applies real frequency
and time-window filters. GraphOps Autopilot subscribes to new evidence records
and routes them through its existing confidence tiers.

Local Ollama may continuously interpret these bounded observations and graph
correlations. Cloud analysis remains an explicit disclosure event: send only
selected observation windows, derived peak tracks/noise statistics, pinned
graph evidence, calibration metadata, and evidence hashes. Never include raw
IQ, unbounded waterfall history, receiver credentials, or hardware-control
authority in a Cloud capsule. Measured RF stays `OBSERVED`; correlations and
model explanations stay `INFERRED`.

`rf_tune`, `rf_capture_control`, and sensor ingestion cannot be called through
direct `tools/call`. They require the orchestrator proposal path. The default
Phase 0 blocks mutation execution, Phase 1 simulates it, and remote phase
changes remain disabled unless an operator explicitly sets
`MCP_ALLOW_REMOTE_PHASE_CONTROL=true`.

## Network boundary

Keep ports 1234 and 4532 bound to loopback or a private sensor network. SDR++
does not add TLS or authentication to these module sockets. Only the RF SCYTHE
API should be exposed through the instance proxy.

## Verification

Run the self-contained bridge tests with:

```bash
python -m unittest -v test_rf_bridge.py test_rf_mcp.py test_rf_sparse_analyzer.py test_graphops_rf_ingest.py
```

They use local fake IQ and Rigctl servers; SDR hardware is not required.
