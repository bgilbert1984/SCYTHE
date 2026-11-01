
### Solver performance (240 emitters, AoA = coherent x4 snapshots, default beliefs, paired noise)

Differences are vs `mixed ribbon`, same emitters (95% bootstrap CI over emitters).

| layout | solver | facade | top-1 | top-3 | cov90 | set | d top-1 vs ribbon | d p(truth) vs ribbon |
|---|---|---|---|---|---|---|---|---|
| analytic@20 | robust | solid concrete | 0.51 | 0.83 | 0.97 | 8.5 | +0.033 [-0.021, +0.087] | +0.029 [+0.016, +0.045] |
| analytic@20 | robust | solid glass | 0.46 | 0.81 | 0.95 | 8.3 | -0.013 [-0.067, +0.042] | +0.012 [-0.002, +0.029] |
| analytic@20 | robust | mixed ribbon | 0.47 | 0.78 | 0.95 | 8.9 | - | - |
| analytic@20 | robust | mixed grid | 0.35 | 0.69 | 0.89 | 9.3 | -0.121 [-0.183, -0.062] | -0.049 [-0.069, -0.030] |
| analytic@20 | robust | mixed staggered | 0.35 | 0.69 | 0.88 | 9.5 | -0.121 [-0.192, -0.058] | -0.062 [-0.086, -0.042] |
| analytic@20 | robust | mixed random | 0.32 | 0.62 | 0.89 | 9.8 | -0.155 [-0.225, -0.092] | -0.073 [-0.094, -0.054] |
| analytic@20 | gaussian | solid concrete | 0.49 | 0.74 | 0.87 | 6.9 | +0.117 [+0.046, +0.183] | +0.052 [+0.026, +0.079] |
| analytic@20 | gaussian | solid glass | 0.40 | 0.71 | 0.87 | 7.0 | +0.033 [-0.021, +0.096] | +0.029 [+0.006, +0.053] |
| analytic@20 | gaussian | mixed ribbon | 0.37 | 0.67 | 0.77 | 6.6 | - | - |
| analytic@20 | gaussian | mixed grid | 0.26 | 0.60 | 0.72 | 6.5 | -0.109 [-0.175, -0.050] | -0.042 [-0.068, -0.017] |
| analytic@20 | gaussian | mixed staggered | 0.28 | 0.61 | 0.68 | 6.3 | -0.092 [-0.163, -0.025] | -0.046 [-0.073, -0.020] |
| analytic@20 | gaussian | mixed random | 0.23 | 0.51 | 0.64 | 6.5 | -0.138 [-0.200, -0.079] | -0.076 [-0.103, -0.052] |
| analytic@14 | robust | solid concrete | 0.41 | 0.69 | 0.95 | 11.6 | +0.017 [-0.037, +0.071] | +0.028 [+0.013, +0.044] |
| analytic@14 | robust | solid glass | 0.37 | 0.65 | 0.94 | 11.0 | -0.017 [-0.071, +0.033] | +0.011 [-0.004, +0.028] |
| analytic@14 | robust | mixed ribbon | 0.39 | 0.64 | 0.93 | 12.3 | - | - |
| analytic@14 | robust | mixed grid | 0.27 | 0.55 | 0.88 | 12.2 | -0.117 [-0.175, -0.071] | -0.040 [-0.060, -0.023] |
| analytic@14 | robust | mixed staggered | 0.26 | 0.54 | 0.86 | 12.7 | -0.134 [-0.200, -0.079] | -0.054 [-0.077, -0.034] |
| analytic@14 | robust | mixed random | 0.28 | 0.54 | 0.87 | 12.7 | -0.113 [-0.179, -0.058] | -0.062 [-0.082, -0.043] |
| analytic@14 | gaussian | solid concrete | 0.38 | 0.64 | 0.87 | 9.8 | +0.075 [+0.008, +0.133] | +0.046 [+0.021, +0.072] |
| analytic@14 | gaussian | solid glass | 0.33 | 0.59 | 0.83 | 9.6 | +0.025 [-0.037, +0.083] | +0.026 [+0.004, +0.049] |
| analytic@14 | gaussian | mixed ribbon | 0.31 | 0.54 | 0.75 | 9.5 | - | - |
| analytic@14 | gaussian | mixed grid | 0.24 | 0.51 | 0.72 | 9.6 | -0.067 [-0.129, -0.008] | -0.030 [-0.055, -0.006] |
| analytic@14 | gaussian | mixed staggered | 0.21 | 0.45 | 0.65 | 9.4 | -0.096 [-0.163, -0.042] | -0.044 [-0.070, -0.018] |
| analytic@14 | gaussian | mixed random | 0.16 | 0.40 | 0.64 | 9.3 | -0.146 [-0.212, -0.087] | -0.066 [-0.091, -0.044] |
| street_arrays(5) | robust | solid concrete | 0.26 | 0.52 | 0.97 | 21.3 | +0.096 [+0.042, +0.154] | +0.018 [+0.012, +0.024] |
| street_arrays(5) | robust | solid glass | 0.12 | 0.38 | 0.96 | 21.3 | -0.042 [-0.087, +0.004] | -0.000 [-0.006, +0.006] |
| street_arrays(5) | robust | mixed ribbon | 0.16 | 0.40 | 0.95 | 21.7 | - | - |
| street_arrays(5) | robust | mixed grid | 0.08 | 0.25 | 0.79 | 21.8 | -0.083 [-0.125, -0.042] | -0.031 [-0.038, -0.023] |
| street_arrays(5) | robust | mixed staggered | 0.07 | 0.23 | 0.81 | 21.8 | -0.087 [-0.133, -0.042] | -0.030 [-0.038, -0.022] |
| street_arrays(5) | robust | mixed random | 0.04 | 0.20 | 0.71 | 21.9 | -0.121 [-0.163, -0.079] | -0.040 [-0.048, -0.033] |
| street_arrays(5) | gaussian | solid concrete | 0.26 | 0.52 | 0.97 | 19.9 | +0.113 [+0.058, +0.167] | +0.023 [+0.016, +0.030] |
| street_arrays(5) | gaussian | solid glass | 0.12 | 0.39 | 0.93 | 20.0 | -0.029 [-0.075, +0.017] | +0.003 [-0.004, +0.010] |
| street_arrays(5) | gaussian | mixed ribbon | 0.15 | 0.39 | 0.90 | 19.8 | - | - |
| street_arrays(5) | gaussian | mixed grid | 0.07 | 0.24 | 0.72 | 19.6 | -0.079 [-0.121, -0.042] | -0.032 [-0.040, -0.023] |
| street_arrays(5) | gaussian | mixed staggered | 0.07 | 0.22 | 0.75 | 19.6 | -0.083 [-0.125, -0.042] | -0.032 [-0.041, -0.023] |
| street_arrays(5) | gaussian | mixed random | 0.04 | 0.18 | 0.64 | 19.6 | -0.108 [-0.154, -0.071] | -0.042 [-0.051, -0.033] |