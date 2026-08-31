# NerfEngine SignalIntelligence source

SCYTHE carries only `core.py`, not the full NerfEngine repository. The upstream
file was copied from `bgilbert1984/NerfEngine` at commit
`72a15af9a1bc659778505d3851a467657a611dcf`.

The upstream `core.py` SHA-256 before the SCYTHE spectrum-product adapter was:

`4ab812ccc92f161e54ca14810cdff5edff20ca20cf087533db7cf303b59b979d`

SCYTHE's local change adds `SignalProcessor.process_spectrum_frame`. It consumes
the versioned `scythe.rf.spectrum.v1` product without performing another FFT and
marks every result as experimental inference that is not graph evidence.
