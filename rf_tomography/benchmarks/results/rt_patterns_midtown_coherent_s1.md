
### Solver performance (240 emitters, AoA = coherent x1 snapshots, default beliefs, paired noise)

Differences are vs `mixed ribbon`, same emitters (95% bootstrap CI over emitters).

| layout | solver | facade | top-1 | top-3 | cov90 | set | d top-1 vs ribbon | d p(truth) vs ribbon |
|---|---|---|---|---|---|---|---|---|
| analytic@20 | robust | solid concrete | 0.48 | 0.81 | 0.96 | 8.6 | +0.038 [-0.029, +0.100] | +0.039 [+0.023, +0.056] |
| analytic@20 | robust | solid glass | 0.43 | 0.75 | 0.95 | 8.6 | -0.004 [-0.067, +0.058] | +0.003 [-0.015, +0.019] |
| analytic@20 | robust | mixed ribbon | 0.44 | 0.77 | 0.94 | 9.1 | - | - |
| analytic@20 | robust | mixed grid | 0.33 | 0.65 | 0.89 | 9.3 | -0.105 [-0.171, -0.037] | -0.044 [-0.062, -0.028] |
| analytic@20 | robust | mixed staggered | 0.32 | 0.70 | 0.87 | 9.3 | -0.121 [-0.192, -0.054] | -0.048 [-0.070, -0.028] |
| analytic@20 | robust | mixed random | 0.33 | 0.66 | 0.89 | 9.7 | -0.109 [-0.183, -0.037] | -0.051 [-0.069, -0.033] |
| analytic@20 | gaussian | solid concrete | 0.46 | 0.73 | 0.86 | 6.9 | +0.113 [+0.037, +0.188] | +0.068 [+0.042, +0.094] |
| analytic@20 | gaussian | solid glass | 0.32 | 0.65 | 0.81 | 6.9 | -0.029 [-0.104, +0.033] | +0.012 [-0.018, +0.038] |
| analytic@20 | gaussian | mixed ribbon | 0.35 | 0.62 | 0.73 | 6.6 | - | - |
| analytic@20 | gaussian | mixed grid | 0.25 | 0.57 | 0.69 | 6.5 | -0.100 [-0.167, -0.029] | -0.035 [-0.062, -0.006] |
| analytic@20 | gaussian | mixed staggered | 0.28 | 0.59 | 0.68 | 6.3 | -0.075 [-0.150, -0.008] | -0.034 [-0.061, -0.005] |
| analytic@20 | gaussian | mixed random | 0.22 | 0.49 | 0.63 | 6.4 | -0.130 [-0.196, -0.067] | -0.057 [-0.085, -0.034] |
| analytic@14 | robust | solid concrete | 0.39 | 0.66 | 0.93 | 11.5 | +0.054 [-0.004, +0.113] | +0.043 [+0.027, +0.061] |
| analytic@14 | robust | solid glass | 0.36 | 0.60 | 0.91 | 11.3 | +0.025 [-0.042, +0.092] | +0.009 [-0.010, +0.027] |
| analytic@14 | robust | mixed ribbon | 0.33 | 0.61 | 0.91 | 12.4 | - | - |
| analytic@14 | robust | mixed grid | 0.27 | 0.54 | 0.87 | 12.3 | -0.067 [-0.138, -0.004] | -0.026 [-0.043, -0.011] |
| analytic@14 | robust | mixed staggered | 0.27 | 0.54 | 0.87 | 12.7 | -0.067 [-0.142, -0.004] | -0.040 [-0.061, -0.021] |
| analytic@14 | robust | mixed random | 0.29 | 0.55 | 0.89 | 12.8 | -0.046 [-0.113, +0.013] | -0.032 [-0.050, -0.017] |
| analytic@14 | gaussian | solid concrete | 0.36 | 0.59 | 0.85 | 9.8 | +0.109 [+0.050, +0.175] | +0.065 [+0.039, +0.090] |
| analytic@14 | gaussian | solid glass | 0.27 | 0.55 | 0.77 | 9.5 | +0.017 [-0.054, +0.075] | +0.014 [-0.015, +0.039] |
| analytic@14 | gaussian | mixed ribbon | 0.25 | 0.48 | 0.70 | 9.5 | - | - |
| analytic@14 | gaussian | mixed grid | 0.21 | 0.46 | 0.68 | 9.7 | -0.042 [-0.100, +0.021] | -0.024 [-0.048, +0.003] |
| analytic@14 | gaussian | mixed staggered | 0.22 | 0.46 | 0.67 | 9.4 | -0.033 [-0.100, +0.025] | -0.028 [-0.056, -0.001] |
| analytic@14 | gaussian | mixed random | 0.18 | 0.38 | 0.62 | 9.2 | -0.071 [-0.138, -0.013] | -0.041 [-0.067, -0.021] |
| street_arrays(5) | robust | solid concrete | 0.25 | 0.52 | 0.96 | 21.3 | +0.058 [+0.004, +0.121] | +0.017 [+0.012, +0.024] |
| street_arrays(5) | robust | solid glass | 0.11 | 0.32 | 0.93 | 21.7 | -0.079 [-0.133, -0.021] | -0.006 [-0.014, +0.002] |
| street_arrays(5) | robust | mixed ribbon | 0.19 | 0.41 | 0.94 | 22.0 | - | - |
| street_arrays(5) | robust | mixed grid | 0.07 | 0.26 | 0.80 | 22.1 | -0.117 [-0.175, -0.062] | -0.029 [-0.036, -0.019] |
| street_arrays(5) | robust | mixed staggered | 0.10 | 0.27 | 0.83 | 21.9 | -0.092 [-0.150, -0.033] | -0.025 [-0.033, -0.016] |
| street_arrays(5) | robust | mixed random | 0.07 | 0.21 | 0.78 | 22.2 | -0.121 [-0.179, -0.075] | -0.034 [-0.042, -0.025] |
| street_arrays(5) | gaussian | solid concrete | 0.25 | 0.52 | 0.96 | 19.8 | +0.071 [+0.017, +0.129] | +0.023 [+0.015, +0.032] |
| street_arrays(5) | gaussian | solid glass | 0.12 | 0.33 | 0.86 | 19.8 | -0.062 [-0.113, -0.004] | -0.003 [-0.011, +0.006] |
| street_arrays(5) | gaussian | mixed ribbon | 0.18 | 0.39 | 0.87 | 19.7 | - | - |
| street_arrays(5) | gaussian | mixed grid | 0.07 | 0.25 | 0.72 | 19.6 | -0.108 [-0.163, -0.058] | -0.028 [-0.037, -0.017] |
| street_arrays(5) | gaussian | mixed staggered | 0.08 | 0.26 | 0.72 | 19.6 | -0.096 [-0.154, -0.042] | -0.026 [-0.035, -0.016] |
| street_arrays(5) | gaussian | mixed random | 0.06 | 0.20 | 0.67 | 19.6 | -0.121 [-0.175, -0.071] | -0.035 [-0.046, -0.024] |