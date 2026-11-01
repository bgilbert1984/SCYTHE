### Link statistics (solver-LOS links; mean [95% paired-bootstrap CI over emitters])

| facade | usable links / all | strongest direct | P(el err > 5 deg) | P(az err > 5 deg) | P(beam el err > 5 deg) |
|---|---|---|---|---|---|
| solid concrete | 0.73 | 0.924 [0.913, 0.935] | 0.018 [0.014, 0.022] | 0.047 [0.039, 0.055] | 0.037 [0.032, 0.044] |
| solid glass | 0.75 | 0.868 [0.855, 0.882] | 0.030 [0.024, 0.035] | 0.087 [0.078, 0.098] | 0.068 [0.061, 0.075] |
| mixed ribbon | 0.74 | 0.748 [0.730, 0.764] | 0.100 [0.090, 0.110] | 0.132 [0.119, 0.143] | 0.124 [0.115, 0.135] |
| mixed grid | 0.73 | 0.726 [0.707, 0.744] | 0.083 [0.074, 0.093] | 0.154 [0.142, 0.167] | 0.222 [0.206, 0.237] |
| mixed staggered | 0.73 | 0.726 [0.710, 0.743] | 0.089 [0.081, 0.098] | 0.151 [0.140, 0.164] | 0.242 [0.228, 0.257] |
| mixed random | 0.73 | 0.720 [0.702, 0.738] | 0.088 [0.079, 0.097] | 0.165 [0.151, 0.179] | 0.257 [0.241, 0.273] |

Paired difference vs `mixed ribbon` (same emitters; 95% bootstrap CI):

| facade | d strongest direct | d P(el err > 5) | d P(az err > 5) |
|---|---|---|---|
| solid concrete | +0.177 [+0.162, +0.191] | -0.082 [-0.092, -0.072] | -0.084 [-0.094, -0.075] |
| solid glass | +0.121 [+0.104, +0.137] | -0.070 [-0.081, -0.060] | -0.045 [-0.056, -0.032] |
| mixed grid | -0.021 [-0.036, -0.007] | -0.017 [-0.025, -0.008] | +0.022 [+0.010, +0.033] |
| mixed staggered | -0.021 [-0.037, -0.007] | -0.011 [-0.020, -0.001] | +0.020 [+0.009, +0.032] |
| mixed random | -0.028 [-0.042, -0.014] | -0.012 [-0.020, -0.004] | +0.033 [+0.022, +0.045] |

### Strongest-path composition (solver-LOS links, share of links)

| path class | mixed ribbon | mixed grid | mixed staggered | mixed random |
|---|---|---|---|---|
| through glass only | 0.293 | 0.291 | 0.298 | 0.291 |
| through concrete (spandrel/pier/roof) | 0.454 | 0.434 | 0.428 | 0.428 |
| reflection off glass | 0.148 | 0.169 | 0.172 | 0.174 |
| reflection off concrete | 0.064 | 0.079 | 0.071 | 0.079 |
| reflection off ground | 0.040 | 0.026 | 0.031 | 0.027 |

### Mechanism test: does the strongest path avoid the direct one when the direct path crosses concrete?

P(strongest path is NOT the direct-like path | what the strongest direct-like path crosses), solver-LOS links:

| facade | direct crosses glass only | direct crosses concrete | median power deficit of direct vs strongest, when not strongest (dB) |
|---|---|---|---|
| mixed ribbon | 0.038 [0.025, 0.052] (n=1187) | 0.326 [0.303, 0.349] (n=2624) | 3.8 |
| mixed grid | 0.037 [0.025, 0.049] (n=1177) | 0.348 [0.324, 0.371] (n=2594) | 4.0 |
| mixed staggered | 0.033 [0.022, 0.044] (n=1200) | 0.353 [0.330, 0.375] (n=2572) | 4.0 |
| mixed random | 0.036 [0.023, 0.049] (n=1175) | 0.348 [0.325, 0.370] (n=2555) | 3.9 |

### Solver performance (240 emitters, beam-peak AoA, default beliefs, paired noise)

Differences are vs `mixed ribbon`, same emitters (95% bootstrap CI over emitters).

| layout | solver | facade | top-1 | top-3 | cov90 | set | d top-1 vs ribbon | d p(truth) vs ribbon |
|---|---|---|---|---|---|---|---|---|
| analytic@20 | robust | solid concrete | 0.53 | 0.83 | 0.97 | 8.4 | +0.071 [+0.021, +0.121] | +0.030 [+0.016, +0.044] |
| analytic@20 | robust | solid glass | 0.47 | 0.82 | 0.96 | 8.1 | +0.021 [-0.033, +0.067] | +0.018 [+0.007, +0.032] |
| analytic@20 | robust | mixed ribbon | 0.46 | 0.79 | 0.94 | 8.9 | - | - |
| analytic@20 | robust | mixed grid | 0.35 | 0.69 | 0.91 | 9.1 | -0.105 [-0.167, -0.050] | -0.053 [-0.074, -0.036] |
| analytic@20 | robust | mixed staggered | 0.35 | 0.69 | 0.89 | 9.6 | -0.105 [-0.167, -0.054] | -0.067 [-0.090, -0.048] |
| analytic@20 | robust | mixed random | 0.30 | 0.64 | 0.88 | 9.8 | -0.159 [-0.221, -0.108] | -0.077 [-0.100, -0.057] |
| analytic@20 | gaussian | solid concrete | 0.50 | 0.74 | 0.88 | 6.9 | +0.117 [+0.054, +0.175] | +0.046 [+0.022, +0.070] |
| analytic@20 | gaussian | solid glass | 0.43 | 0.73 | 0.88 | 6.9 | +0.050 [-0.004, +0.108] | +0.035 [+0.013, +0.058] |
| analytic@20 | gaussian | mixed ribbon | 0.38 | 0.66 | 0.77 | 6.6 | - | - |
| analytic@20 | gaussian | mixed grid | 0.29 | 0.59 | 0.72 | 6.4 | -0.088 [-0.142, -0.025] | -0.045 [-0.070, -0.021] |
| analytic@20 | gaussian | mixed staggered | 0.28 | 0.60 | 0.68 | 6.2 | -0.096 [-0.158, -0.037] | -0.061 [-0.088, -0.035] |
| analytic@20 | gaussian | mixed random | 0.21 | 0.54 | 0.65 | 6.5 | -0.167 [-0.225, -0.112] | -0.082 [-0.110, -0.058] |
| analytic@14 | robust | solid concrete | 0.42 | 0.69 | 0.95 | 11.5 | +0.029 [-0.025, +0.079] | +0.029 [+0.015, +0.042] |
| analytic@14 | robust | solid glass | 0.42 | 0.66 | 0.95 | 10.9 | +0.025 [-0.029, +0.067] | +0.022 [+0.010, +0.034] |
| analytic@14 | robust | mixed ribbon | 0.39 | 0.63 | 0.93 | 12.2 | - | - |
| analytic@14 | robust | mixed grid | 0.28 | 0.54 | 0.88 | 12.2 | -0.113 [-0.171, -0.062] | -0.046 [-0.065, -0.031] |
| analytic@14 | robust | mixed staggered | 0.25 | 0.55 | 0.87 | 12.8 | -0.146 [-0.217, -0.087] | -0.061 [-0.083, -0.042] |
| analytic@14 | robust | mixed random | 0.23 | 0.51 | 0.86 | 12.7 | -0.163 [-0.229, -0.108] | -0.066 [-0.087, -0.047] |
| analytic@14 | gaussian | solid concrete | 0.39 | 0.63 | 0.87 | 9.9 | +0.075 [+0.013, +0.138] | +0.044 [+0.020, +0.068] |
| analytic@14 | gaussian | solid glass | 0.38 | 0.61 | 0.89 | 9.6 | +0.063 [+0.008, +0.117] | +0.042 [+0.021, +0.063] |
| analytic@14 | gaussian | mixed ribbon | 0.31 | 0.52 | 0.75 | 9.5 | - | - |
| analytic@14 | gaussian | mixed grid | 0.28 | 0.49 | 0.70 | 9.5 | -0.038 [-0.096, +0.017] | -0.031 [-0.055, -0.007] |
| analytic@14 | gaussian | mixed staggered | 0.21 | 0.46 | 0.66 | 9.4 | -0.105 [-0.163, -0.050] | -0.054 [-0.081, -0.029] |
| analytic@14 | gaussian | mixed random | 0.15 | 0.41 | 0.64 | 9.2 | -0.167 [-0.221, -0.108] | -0.070 [-0.095, -0.046] |
| street_arrays(5) | robust | solid concrete | 0.26 | 0.53 | 0.97 | 21.2 | +0.117 [+0.075, +0.158] | +0.017 [+0.012, +0.023] |
| street_arrays(5) | robust | solid glass | 0.10 | 0.38 | 0.97 | 21.2 | -0.050 [-0.087, -0.013] | -0.001 [-0.006, +0.004] |
| street_arrays(5) | robust | mixed ribbon | 0.15 | 0.42 | 0.95 | 21.5 | - | - |
| street_arrays(5) | robust | mixed grid | 0.07 | 0.22 | 0.81 | 21.9 | -0.075 [-0.113, -0.037] | -0.033 [-0.040, -0.025] |
| street_arrays(5) | robust | mixed staggered | 0.04 | 0.21 | 0.79 | 21.7 | -0.104 [-0.146, -0.067] | -0.036 [-0.045, -0.028] |
| street_arrays(5) | robust | mixed random | 0.03 | 0.17 | 0.72 | 21.9 | -0.117 [-0.158, -0.079] | -0.045 [-0.054, -0.037] |
| street_arrays(5) | gaussian | solid concrete | 0.26 | 0.53 | 0.97 | 19.9 | +0.121 [+0.079, +0.167] | +0.022 [+0.015, +0.029] |
| street_arrays(5) | gaussian | solid glass | 0.10 | 0.37 | 0.96 | 20.1 | -0.046 [-0.083, -0.004] | +0.002 [-0.005, +0.008] |
| street_arrays(5) | gaussian | mixed ribbon | 0.14 | 0.41 | 0.91 | 19.8 | - | - |
| street_arrays(5) | gaussian | mixed grid | 0.06 | 0.21 | 0.74 | 19.7 | -0.079 [-0.117, -0.046] | -0.035 [-0.043, -0.027] |
| street_arrays(5) | gaussian | mixed staggered | 0.05 | 0.20 | 0.73 | 19.7 | -0.092 [-0.133, -0.054] | -0.038 [-0.047, -0.030] |
| street_arrays(5) | gaussian | mixed random | 0.03 | 0.15 | 0.64 | 19.6 | -0.113 [-0.154, -0.075] | -0.048 [-0.057, -0.039] |