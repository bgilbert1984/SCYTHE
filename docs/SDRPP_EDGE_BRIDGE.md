# SDR++ Edge Bridge

RF SCYTHE keeps SDR++ as a separate native process. The browser and Flask
server never load or link SDR++ libraries; the bridge connects only to the two
local interfaces supplied by SDR++ modules.

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

RF facts are labelled `OBSERVED`; temporal graph correlations are labelled
`INFERRED` and explicitly do not claim causality. `RF_CORRELATE` in the
GraphOps Copilot DSL uses the same evidence store and applies real frequency
and time-window filters. GraphOps Autopilot subscribes to new evidence records
and routes them through its existing confidence tiers.

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
python -m unittest -v test_rf_bridge.py test_rf_mcp.py
```

They use local fake IQ and Rigctl servers; SDR hardware is not required.
