# SCYTHE v0.4.0 laboratory guide

## Runtime controls

| Input | Action |
|---|---|
| `W`, `A`, `S`, `D` | Move the spatial probe |
| `Shift` | Sprint |
| `Space` | Jump |
| Mouse | Look |
| Click or `Tab` | Capture or release the cursor |
| `T` | Select the next transmitter |
| `1`, `2`, `3`, `4` | Set active transmitter to ASK, FSK, BPSK, or QPSK |
| `O` | Toggle a loaded optical fusion layer |
| `[` and `]` | Select optical depth plane |
| `Esc` | Exit |

## Multi-emitter scenario

The scenario manifest is
`Assets/StreamingAssets/Scenarios/rf_milestone_01.json`. It declares three independent links:

- `TX-A` / ALPHA: 2.4 GHz BPSK, static
- `TX-B` / BRAVO: 915 MHz QPSK, static
- `TX-C` / CHARLIE: 5.8 GHz FSK, deterministic ping-pong motion

Every transmitter has its own stable id, carrier, symbol rate, samples per symbol, modulation, power,
position, radiating state, and motion metadata.

The included event sequence changes BRAVO power at 20 seconds, changes CHARLIE modulation at 30
seconds, disables BRAVO at 40 seconds, and restores BRAVO at 50 seconds. Events execute against
`SimulationClock`, in manifest order, once each.

## RF occlusion approximation

Only colliders carrying `RFOccluder` participate. The runtime counts unique blockers on the straight
transmitter-to-probe segment and applies:

```text
loss_dB = blocker_count × loss_dB_per_blocker
amplitude = amplitude × 10^(-loss_dB / 20)
```

The active-link panel reports `CLEAR` or the blocker count and applied loss. The power map intentionally
shows the incoherent, unoccluded sum of free-space power densities and says so on-screen.

This approximation does not model material permittivity, reflection, edge diffraction, scattering,
polarization, antenna patterns, coherent interference, or multipath.

## Optical dataset handoff

No solver-generated optical dataset is bundled in v0.4.0. The HUD therefore reports
`NO SOLVER DATASET BUNDLED`; it does not generate substitute physics.

To ingest a dataset:

1. Create `Assets/OpticalDatasets/<dataset-name>/`.
2. Add `metadata.json`, `phase.exr`, and `intensity.exr`.
3. Add optional `polarization.exr`, `depth_planes/*.exr`, and `lane_masks/*.exr`.
4. Set `opticalDatasetRelativeDirectory` to `<dataset-name>` in the scenario manifest.
5. Set `opticalDatasetRequired` to `true` when production builds must refuse an absent dataset.
6. Run either build script.

The build validates metadata units and provenance, required assets, phase/intensity dimensions, and
the correspondence between depth-plane positions and textures. Any declared incomplete dataset stops
the build.

The initial optical display is labeled `DATASET SPACE // UNREGISTERED`. It does not claim camera,
pose, depth, or RF-to-optical spatial registration.

## Validation and builds

Run the standalone gate:

```bash
/opt/unity/6000.3.15f1/Editor/Unity \
  -batchmode -quit -nographics \
  -projectPath /workspaces/codespaces-blank/UnityProject \
  -executeMethod Scythe.Editor.ValidationCommand.Run \
  -logFile /workspaces/codespaces-blank/UnityProject/validation-v0.4.log
```

Build players from the repository root:

```bash
./build_unity_linux.sh
./build_unity_windows.sh
```

Both build commands run the validation gate before generating a player.
