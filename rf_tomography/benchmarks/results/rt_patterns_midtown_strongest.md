
### Solver performance (240 emitters, AoA = strongest, default beliefs, paired noise)

Differences are vs `mixed ribbon`, same emitters (95% bootstrap CI over emitters).

| layout | solver | facade | top-1 | top-3 | cov90 | set | d top-1 vs ribbon | d p(truth) vs ribbon |
|---|---|---|---|---|---|---|---|---|
| analytic@20 | robust | solid concrete | 0.54 | 0.85 | 0.97 | 8.4 | +0.038 [-0.017, +0.087] | +0.020 [+0.009, +0.032] |
| analytic@20 | robust | solid glass | 0.55 | 0.86 | 0.97 | 8.0 | +0.046 [+0.000, +0.092] | +0.024 [+0.015, +0.034] |
| analytic@20 | robust | mixed ribbon | 0.50 | 0.81 | 0.95 | 8.7 | - | - |
| analytic@20 | robust | mixed grid | 0.46 | 0.80 | 0.95 | 8.6 | -0.042 [-0.100, +0.008] | -0.008 [-0.023, +0.003] |
| analytic@20 | robust | mixed staggered | 0.46 | 0.81 | 0.95 | 8.8 | -0.038 [-0.092, +0.013] | -0.016 [-0.030, -0.004] |
| analytic@20 | robust | mixed random | 0.48 | 0.78 | 0.95 | 8.9 | -0.021 [-0.079, +0.029] | -0.015 [-0.029, -0.003] |
| analytic@20 | gaussian | solid concrete | 0.50 | 0.76 | 0.89 | 6.9 | +0.096 [+0.037, +0.158] | +0.036 [+0.015, +0.058] |
| analytic@20 | gaussian | solid glass | 0.50 | 0.79 | 0.91 | 7.0 | +0.092 [+0.033, +0.146] | +0.039 [+0.020, +0.059] |
| analytic@20 | gaussian | mixed ribbon | 0.41 | 0.69 | 0.79 | 6.6 | - | - |
| analytic@20 | gaussian | mixed grid | 0.38 | 0.72 | 0.82 | 6.6 | -0.025 [-0.092, +0.025] | -0.000 [-0.022, +0.019] |
| analytic@20 | gaussian | mixed staggered | 0.40 | 0.70 | 0.80 | 6.4 | -0.008 [-0.067, +0.050] | -0.005 [-0.026, +0.018] |
| analytic@20 | gaussian | mixed random | 0.38 | 0.66 | 0.78 | 6.6 | -0.029 [-0.092, +0.021] | -0.024 [-0.050, -0.004] |
| street_arrays(5) | robust | solid concrete | 0.37 | 0.56 | 0.97 | 21.2 | +0.113 [+0.067, +0.158] | +0.013 [+0.008, +0.018] |
| street_arrays(5) | robust | solid glass | 0.35 | 0.55 | 0.97 | 21.1 | +0.096 [+0.058, +0.138] | +0.012 [+0.007, +0.017] |
| street_arrays(5) | robust | mixed ribbon | 0.25 | 0.48 | 0.96 | 21.5 | - | - |
| street_arrays(5) | robust | mixed grid | 0.25 | 0.49 | 0.97 | 21.8 | -0.004 [-0.050, +0.037] | -0.000 [-0.005, +0.004] |
| street_arrays(5) | robust | mixed staggered | 0.26 | 0.50 | 0.97 | 21.6 | +0.004 [-0.033, +0.042] | -0.000 [-0.004, +0.004] |
| street_arrays(5) | robust | mixed random | 0.26 | 0.51 | 0.96 | 21.9 | +0.004 [-0.037, +0.046] | +0.001 [-0.004, +0.006] |
| street_arrays(5) | gaussian | solid concrete | 0.37 | 0.56 | 0.97 | 19.9 | +0.117 [+0.071, +0.163] | +0.017 [+0.011, +0.024] |
| street_arrays(5) | gaussian | solid glass | 0.35 | 0.55 | 0.97 | 20.1 | +0.092 [+0.050, +0.133] | +0.016 [+0.010, +0.022] |
| street_arrays(5) | gaussian | mixed ribbon | 0.25 | 0.47 | 0.92 | 19.8 | - | - |
| street_arrays(5) | gaussian | mixed grid | 0.25 | 0.47 | 0.93 | 19.7 | -0.004 [-0.050, +0.037] | +0.001 [-0.005, +0.008] |
| street_arrays(5) | gaussian | mixed staggered | 0.25 | 0.48 | 0.94 | 19.8 | -0.004 [-0.042, +0.033] | +0.001 [-0.004, +0.007] |
| street_arrays(5) | gaussian | mixed random | 0.27 | 0.50 | 0.93 | 19.8 | +0.013 [-0.029, +0.050] | +0.003 [-0.002, +0.009] |