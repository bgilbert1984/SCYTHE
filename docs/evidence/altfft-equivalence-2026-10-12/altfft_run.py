#!/usr/bin/env python3
"""Run the frozen Dedirock equivalence runner with np.fft.fft substituted, experiment-only.

Usage: altfft_run.py BACKEND SCYTHE HOLDOUT INDEX RUNNER OUT
BACKEND: numpy (control, through the same wrapper) | scipy | pyfftw
Production code is imported unchanged; only the attribute numpy.fft.fft is rebound in
this process.  Every call is counted by calling module, and the backend's identity is
written to OUT.backend.json so the substitution is evidenced, not assumed.
"""
import json, runpy, sys, collections
import numpy as np

backend, scythe, holdout, index, runner, out = sys.argv[1:7]
orig = np.fft.fft
calls = collections.Counter()
ident = {}

if backend == "numpy":
    impl = orig
    ident = {"module": orig.__module__, "name": orig.__name__}
elif backend == "scipy":
    import scipy, scipy.fft
    impl = scipy.fft.fft
    ident = {"module": impl.__module__, "name": impl.__name__, "scipy": scipy.__version__,
             "backends_active": str(scipy.fft.get_workers())}
elif backend == "pyfftw":
    import pyfftw, pyfftw.interfaces.numpy_fft as nf
    impl = nf.fft
    ident = {"module": impl.__module__, "name": impl.__name__, "pyfftw": pyfftw.__version__,
             "fftw_version": getattr(pyfftw, "fftw_version", None)}
else:
    raise SystemExit("unknown backend")

def wrapper(a, n=None, axis=-1, norm=None):
    f = sys._getframe(1)
    calls[f.f_globals.get("__name__", "?") + ":" + str(f.f_code.co_name)] += 1
    return impl(a, n=n, axis=axis, norm=norm)

np.fft.fft = wrapper
sys.argv = ["dedirock_equivalence.py", "--scythe", scythe, "--holdout", holdout,
            "--index", index, "--out", out]
try:
    runpy.run_path(runner, run_name="__main__")
except SystemExit as e:
    if e.code not in (None, 0):
        raise
finally:
    np.fft.fft = orig
json.dump({"backend": backend, "identity": ident, "numpy": np.__version__,
           "calls_by_site": dict(calls), "total_calls": sum(calls.values())},
          open(out + ".backend.json", "w"), indent=1)
print(backend, "calls:", dict(calls))
