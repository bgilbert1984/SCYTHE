
### Solver performance (240 emitters, AoA = coherent x0 snapshots, default beliefs, paired noise)

Differences are vs `mixed ribbon`, same emitters (95% bootstrap CI over emitters).

| layout | solver | facade | top-1 | top-3 | cov90 | set | d top-1 vs ribbon | d p(truth) vs ribbon |
|---|---|---|---|---|---|---|---|---|
| analytic@20 | robust | solid concrete | 0.53 | 0.83 | 0.97 | 8.4 | +0.071 [+0.017, +0.125] | +0.029 [+0.015, +0.043] |
| analytic@20 | robust | solid glass | 0.47 | 0.82 | 0.96 | 8.1 | +0.008 [-0.050, +0.054] | +0.018 [+0.007, +0.032] |
| analytic@20 | robust | mixed ribbon | 0.46 | 0.78 | 0.95 | 8.9 | - | - |
| analytic@20 | robust | mixed grid | 0.35 | 0.69 | 0.91 | 9.2 | -0.109 [-0.171, -0.054] | -0.052 [-0.072, -0.036] |
| analytic@20 | robust | mixed staggered | 0.37 | 0.69 | 0.89 | 9.5 | -0.088 [-0.154, -0.033] | -0.065 [-0.087, -0.045] |
| analytic@20 | robust | mixed random | 0.32 | 0.65 | 0.87 | 9.9 | -0.138 [-0.208, -0.079] | -0.077 [-0.100, -0.056] |
| analytic@20 | gaussian | solid concrete | 0.49 | 0.73 | 0.87 | 6.9 | +0.092 [+0.033, +0.150] | +0.044 [+0.020, +0.068] |
| analytic@20 | gaussian | solid glass | 0.43 | 0.72 | 0.87 | 6.9 | +0.038 [-0.017, +0.096] | +0.033 [+0.012, +0.055] |
| analytic@20 | gaussian | mixed ribbon | 0.39 | 0.65 | 0.76 | 6.6 | - | - |
| analytic@20 | gaussian | mixed grid | 0.29 | 0.59 | 0.72 | 6.4 | -0.100 [-0.158, -0.042] | -0.044 [-0.069, -0.022] |
| analytic@20 | gaussian | mixed staggered | 0.31 | 0.60 | 0.69 | 6.2 | -0.088 [-0.146, -0.025] | -0.053 [-0.080, -0.027] |
| analytic@20 | gaussian | mixed random | 0.22 | 0.54 | 0.64 | 6.5 | -0.176 [-0.233, -0.121] | -0.082 [-0.110, -0.057] |
| analytic@14 | robust | solid concrete | 0.43 | 0.70 | 0.95 | 11.5 | +0.042 [-0.008, +0.092] | +0.029 [+0.016, +0.042] |
| analytic@14 | robust | solid glass | 0.42 | 0.67 | 0.95 | 11.0 | +0.038 [-0.017, +0.083] | +0.022 [+0.010, +0.035] |
| analytic@14 | robust | mixed ribbon | 0.38 | 0.64 | 0.93 | 12.2 | - | - |
| analytic@14 | robust | mixed grid | 0.28 | 0.54 | 0.89 | 12.1 | -0.109 [-0.171, -0.054] | -0.044 [-0.062, -0.028] |
| analytic@14 | robust | mixed staggered | 0.25 | 0.56 | 0.86 | 12.7 | -0.134 [-0.204, -0.079] | -0.058 [-0.080, -0.040] |
| analytic@14 | robust | mixed random | 0.24 | 0.51 | 0.86 | 12.7 | -0.146 [-0.212, -0.092] | -0.064 [-0.086, -0.045] |
| analytic@14 | gaussian | solid concrete | 0.39 | 0.62 | 0.86 | 9.9 | +0.079 [+0.017, +0.142] | +0.042 [+0.019, +0.065] |
| analytic@14 | gaussian | solid glass | 0.38 | 0.60 | 0.87 | 9.6 | +0.067 [+0.017, +0.117] | +0.039 [+0.019, +0.060] |
| analytic@14 | gaussian | mixed ribbon | 0.31 | 0.53 | 0.75 | 9.5 | - | - |
| analytic@14 | gaussian | mixed grid | 0.25 | 0.49 | 0.71 | 9.4 | -0.063 [-0.117, -0.004] | -0.031 [-0.055, -0.008] |
| analytic@14 | gaussian | mixed staggered | 0.23 | 0.47 | 0.67 | 9.4 | -0.084 [-0.142, -0.025] | -0.047 [-0.073, -0.022] |
| analytic@14 | gaussian | mixed random | 0.15 | 0.40 | 0.64 | 9.2 | -0.159 [-0.212, -0.100] | -0.071 [-0.096, -0.046] |
| street_arrays(5) | robust | solid concrete | 0.28 | 0.53 | 0.97 | 21.2 | +0.113 [+0.071, +0.154] | +0.016 [+0.011, +0.021] |
| street_arrays(5) | robust | solid glass | 0.10 | 0.40 | 0.97 | 21.2 | -0.058 [-0.096, -0.021] | -0.001 [-0.006, +0.003] |
| street_arrays(5) | robust | mixed ribbon | 0.16 | 0.44 | 0.95 | 21.5 | - | - |
| street_arrays(5) | robust | mixed grid | 0.08 | 0.23 | 0.81 | 21.9 | -0.083 [-0.125, -0.042] | -0.034 [-0.042, -0.026] |
| street_arrays(5) | robust | mixed staggered | 0.05 | 0.22 | 0.79 | 21.8 | -0.113 [-0.158, -0.067] | -0.038 [-0.046, -0.029] |
| street_arrays(5) | robust | mixed random | 0.03 | 0.18 | 0.71 | 21.9 | -0.133 [-0.175, -0.092] | -0.046 [-0.055, -0.038] |
| street_arrays(5) | gaussian | solid concrete | 0.28 | 0.53 | 0.97 | 19.9 | +0.125 [+0.083, +0.167] | +0.020 [+0.014, +0.027] |
| street_arrays(5) | gaussian | solid glass | 0.10 | 0.39 | 0.96 | 20.1 | -0.054 [-0.092, -0.013] | +0.001 [-0.004, +0.007] |
| street_arrays(5) | gaussian | mixed ribbon | 0.16 | 0.42 | 0.91 | 19.8 | - | - |
| street_arrays(5) | gaussian | mixed grid | 0.07 | 0.22 | 0.74 | 19.6 | -0.087 [-0.125, -0.046] | -0.036 [-0.045, -0.028] |
| street_arrays(5) | gaussian | mixed staggered | 0.05 | 0.20 | 0.73 | 19.7 | -0.108 [-0.154, -0.067] | -0.040 [-0.049, -0.030] |
| street_arrays(5) | gaussian | mixed random | 0.03 | 0.16 | 0.65 | 19.6 | -0.133 [-0.179, -0.092] | -0.049 [-0.059, -0.040] |