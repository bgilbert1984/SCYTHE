### block_915MHz_concrete: concrete facades, scattering 0.0, 400 ray-traced emitters, AoA = strongest

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.84 | 0.93 | 0.91 | 2.3 | 0.62 | +0.16 | 0.40 |
| analytic@14 | robust | 0.89 | 1.00 | 1.00 | 2.5 | 0.64 | -0.05 | 0.23 |
| analytic@14 | robust_paranoid | 0.89 | 1.00 | 1.00 | 3.0 | 0.58 | -0.11 | 0.27 |
| analytic@20 | gaussian | 0.86 | 0.95 | 0.92 | 1.8 | 0.72 | +0.10 | 0.31 |
| analytic@20 | robust | 0.92 | 1.00 | 0.99 | 1.9 | 0.75 | -0.04 | 0.18 |
| analytic@20 | robust_paranoid | 0.92 | 1.00 | 1.00 | 2.1 | 0.72 | -0.06 | 0.19 |
| random_median@14 | gaussian | 0.83 | 0.98 | 0.99 | 3.6 | 0.45 | +0.03 | 0.34 |
| random_median@14 | robust | 0.83 | 0.99 | 1.00 | 4.4 | 0.43 | -0.01 | 0.47 |
| random_median@14 | robust_paranoid | 0.82 | 0.98 | 1.00 | 7.1 | 0.36 | -0.07 | 0.79 |
| street_arrays(5) | gaussian | 0.99 | 1.00 | 1.00 | 3.0 | 0.61 | +0.00 | 0.12 |
| street_arrays(5) | robust | 0.97 | 1.00 | 1.00 | 3.0 | 0.60 | -0.01 | 0.14 |
| street_arrays(5) | robust_paranoid | 0.97 | 1.00 | 1.00 | 3.2 | 0.58 | +0.00 | 0.15 |

### block_915MHz_concrete: concrete facades, scattering 0.0, 400 ray-traced emitters, AoA = beamformed

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.83 | 0.92 | 0.90 | 2.2 | 0.61 | +0.17 | 0.43 |
| analytic@14 | robust | 0.89 | 1.00 | 1.00 | 2.4 | 0.64 | -0.06 | 0.23 |
| analytic@14 | robust_paranoid | 0.89 | 1.00 | 1.00 | 3.0 | 0.58 | -0.12 | 0.27 |
| analytic@20 | gaussian | 0.84 | 0.95 | 0.92 | 1.8 | 0.71 | +0.11 | 0.33 |
| analytic@20 | robust | 0.93 | 1.00 | 1.00 | 1.9 | 0.75 | -0.05 | 0.19 |
| analytic@20 | robust_paranoid | 0.93 | 1.00 | 1.00 | 2.1 | 0.71 | -0.07 | 0.20 |
| random_median@14 | gaussian | 0.81 | 0.97 | 0.99 | 3.6 | 0.44 | +0.05 | 0.36 |
| random_median@14 | robust | 0.81 | 0.98 | 1.00 | 4.4 | 0.42 | +0.03 | 0.46 |
| random_median@14 | robust_paranoid | 0.78 | 0.98 | 1.00 | 7.1 | 0.36 | -0.03 | 0.78 |
| street_arrays(5) | gaussian | 0.93 | 1.00 | 1.00 | 3.0 | 0.60 | -0.06 | 0.17 |
| street_arrays(5) | robust | 0.92 | 0.99 | 1.00 | 3.0 | 0.60 | -0.08 | 0.19 |
| street_arrays(5) | robust_paranoid | 0.92 | 0.99 | 1.00 | 3.3 | 0.57 | -0.07 | 0.19 |

### block_915MHz_concrete_diffuse30: concrete facades, scattering 0.3, 400 ray-traced emitters, AoA = strongest

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.86 | 0.94 | 0.92 | 2.2 | 0.63 | +0.15 | 0.37 |
| analytic@14 | robust | 0.89 | 1.00 | 1.00 | 2.4 | 0.64 | -0.04 | 0.23 |
| analytic@14 | robust_paranoid | 0.90 | 1.00 | 1.00 | 2.9 | 0.59 | -0.09 | 0.26 |
| analytic@20 | gaussian | 0.88 | 0.95 | 0.93 | 1.8 | 0.73 | +0.11 | 0.28 |
| analytic@20 | robust | 0.94 | 1.00 | 1.00 | 1.9 | 0.77 | -0.03 | 0.16 |
| analytic@20 | robust_paranoid | 0.94 | 1.00 | 1.00 | 2.1 | 0.73 | -0.05 | 0.17 |
| random_median@14 | gaussian | 0.83 | 0.99 | 0.99 | 3.6 | 0.44 | +0.02 | 0.35 |
| random_median@14 | robust | 0.80 | 0.99 | 1.00 | 4.5 | 0.42 | -0.01 | 0.48 |
| random_median@14 | robust_paranoid | 0.81 | 0.99 | 1.00 | 7.0 | 0.36 | -0.08 | 0.78 |
| street_arrays(5) | gaussian | 0.99 | 1.00 | 1.00 | 3.0 | 0.61 | +0.01 | 0.12 |
| street_arrays(5) | robust | 0.97 | 1.00 | 1.00 | 3.0 | 0.60 | -0.00 | 0.13 |
| street_arrays(5) | robust_paranoid | 0.98 | 1.00 | 1.00 | 3.2 | 0.58 | +0.01 | 0.14 |

### block_915MHz_concrete_diffuse30: concrete facades, scattering 0.3, 400 ray-traced emitters, AoA = beamformed

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.84 | 0.92 | 0.91 | 2.2 | 0.62 | +0.17 | 0.41 |
| analytic@14 | robust | 0.88 | 1.00 | 1.00 | 2.4 | 0.64 | -0.05 | 0.23 |
| analytic@14 | robust_paranoid | 0.89 | 1.00 | 1.00 | 3.0 | 0.59 | -0.11 | 0.26 |
| analytic@20 | gaussian | 0.86 | 0.95 | 0.92 | 1.8 | 0.72 | +0.12 | 0.30 |
| analytic@20 | robust | 0.94 | 1.00 | 1.00 | 1.9 | 0.77 | -0.04 | 0.16 |
| analytic@20 | robust_paranoid | 0.95 | 1.00 | 1.00 | 2.1 | 0.73 | -0.06 | 0.17 |
| random_median@14 | gaussian | 0.80 | 0.98 | 0.98 | 3.6 | 0.44 | +0.05 | 0.38 |
| random_median@14 | robust | 0.79 | 0.99 | 1.00 | 4.4 | 0.42 | +0.02 | 0.47 |
| random_median@14 | robust_paranoid | 0.79 | 0.99 | 1.00 | 7.0 | 0.36 | -0.04 | 0.78 |
| street_arrays(5) | gaussian | 0.94 | 1.00 | 1.00 | 3.0 | 0.60 | -0.05 | 0.16 |
| street_arrays(5) | robust | 0.93 | 1.00 | 1.00 | 3.0 | 0.60 | -0.07 | 0.16 |
| street_arrays(5) | robust_paranoid | 0.93 | 1.00 | 1.00 | 3.2 | 0.57 | -0.06 | 0.18 |

### block_915MHz_glass: glass facades, scattering 0.0, 400 ray-traced emitters, AoA = strongest

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.89 | 0.98 | 0.97 | 2.3 | 0.66 | -0.08 | 0.24 |
| analytic@14 | robust | 0.94 | 1.00 | 1.00 | 2.4 | 0.67 | -0.03 | 0.19 |
| analytic@14 | robust_paranoid | 0.94 | 1.00 | 1.00 | 2.9 | 0.61 | -0.04 | 0.22 |
| analytic@20 | gaussian | 0.92 | 0.99 | 0.97 | 1.8 | 0.77 | -0.08 | 0.19 |
| analytic@20 | robust | 0.96 | 1.00 | 1.00 | 1.9 | 0.78 | -0.02 | 0.15 |
| analytic@20 | robust_paranoid | 0.96 | 1.00 | 1.00 | 2.1 | 0.75 | -0.02 | 0.16 |
| random_median@14 | gaussian | 0.79 | 0.94 | 0.93 | 3.6 | 0.42 | -0.07 | 0.50 |
| random_median@14 | robust | 0.84 | 0.99 | 1.00 | 4.5 | 0.42 | +0.07 | 0.45 |
| random_median@14 | robust_paranoid | 0.84 | 0.99 | 1.00 | 7.2 | 0.36 | +0.07 | 0.73 |
| street_arrays(5) | gaussian | 0.83 | 0.92 | 0.87 | 2.9 | 0.52 | -0.86 | 0.98 |
| street_arrays(5) | robust | 0.93 | 0.98 | 0.98 | 3.3 | 0.58 | -0.34 | 0.47 |
| street_arrays(5) | robust_paranoid | 0.94 | 0.98 | 0.98 | 3.7 | 0.55 | -0.31 | 0.46 |

### block_915MHz_glass: glass facades, scattering 0.0, 400 ray-traced emitters, AoA = beamformed

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.85 | 0.96 | 0.95 | 2.3 | 0.63 | -0.13 | 0.31 |
| analytic@14 | robust | 0.91 | 1.00 | 1.00 | 2.4 | 0.65 | -0.04 | 0.22 |
| analytic@14 | robust_paranoid | 0.91 | 1.00 | 1.00 | 2.9 | 0.59 | -0.06 | 0.25 |
| analytic@20 | gaussian | 0.86 | 0.98 | 0.94 | 1.8 | 0.72 | -0.15 | 0.27 |
| analytic@20 | robust | 0.94 | 1.00 | 1.00 | 1.9 | 0.76 | -0.05 | 0.18 |
| analytic@20 | robust_paranoid | 0.94 | 1.00 | 1.00 | 2.1 | 0.72 | -0.06 | 0.19 |
| random_median@14 | gaussian | 0.70 | 0.94 | 0.94 | 3.6 | 0.41 | +0.04 | 0.57 |
| random_median@14 | robust | 0.73 | 0.98 | 0.99 | 4.5 | 0.41 | +0.16 | 0.49 |
| random_median@14 | robust_paranoid | 0.72 | 0.98 | 1.00 | 7.2 | 0.35 | +0.16 | 0.76 |
| street_arrays(5) | gaussian | 0.65 | 0.82 | 0.78 | 2.8 | 0.46 | -1.38 | 1.53 |
| street_arrays(5) | robust | 0.73 | 0.95 | 0.98 | 3.5 | 0.53 | -0.49 | 0.61 |
| street_arrays(5) | robust_paranoid | 0.73 | 0.96 | 0.98 | 3.9 | 0.50 | -0.50 | 0.63 |

### block_915MHz_glass_diffuse30: glass facades, scattering 0.3, 400 ray-traced emitters, AoA = strongest

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.91 | 0.98 | 0.96 | 2.3 | 0.66 | -0.06 | 0.23 |
| analytic@14 | robust | 0.95 | 1.00 | 1.00 | 2.4 | 0.67 | -0.01 | 0.19 |
| analytic@14 | robust_paranoid | 0.94 | 1.00 | 1.00 | 2.9 | 0.62 | -0.02 | 0.22 |
| analytic@20 | gaussian | 0.92 | 0.99 | 0.98 | 1.8 | 0.77 | -0.05 | 0.18 |
| analytic@20 | robust | 0.96 | 1.00 | 1.00 | 1.9 | 0.79 | +0.00 | 0.14 |
| analytic@20 | robust_paranoid | 0.97 | 1.00 | 1.00 | 2.0 | 0.75 | -0.00 | 0.15 |
| random_median@14 | gaussian | 0.76 | 0.95 | 0.94 | 3.6 | 0.41 | -0.07 | 0.50 |
| random_median@14 | robust | 0.81 | 0.98 | 1.00 | 4.5 | 0.42 | +0.06 | 0.46 |
| random_median@14 | robust_paranoid | 0.81 | 0.98 | 1.00 | 7.1 | 0.36 | +0.06 | 0.72 |
| street_arrays(5) | gaussian | 0.84 | 0.93 | 0.88 | 2.9 | 0.53 | -0.74 | 0.88 |
| street_arrays(5) | robust | 0.94 | 0.99 | 0.99 | 3.2 | 0.58 | -0.21 | 0.35 |
| street_arrays(5) | robust_paranoid | 0.94 | 0.98 | 0.99 | 3.6 | 0.55 | -0.21 | 0.38 |

### block_915MHz_glass_diffuse30: glass facades, scattering 0.3, 400 ray-traced emitters, AoA = beamformed

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.88 | 0.96 | 0.94 | 2.2 | 0.64 | -0.11 | 0.30 |
| analytic@14 | robust | 0.94 | 1.00 | 1.00 | 2.4 | 0.66 | -0.03 | 0.21 |
| analytic@14 | robust_paranoid | 0.94 | 1.00 | 1.00 | 2.9 | 0.60 | -0.04 | 0.24 |
| analytic@20 | gaussian | 0.87 | 0.97 | 0.95 | 1.9 | 0.73 | -0.12 | 0.25 |
| analytic@20 | robust | 0.95 | 1.00 | 1.00 | 1.9 | 0.77 | -0.03 | 0.16 |
| analytic@20 | robust_paranoid | 0.96 | 1.00 | 1.00 | 2.1 | 0.74 | -0.03 | 0.17 |
| random_median@14 | gaussian | 0.70 | 0.94 | 0.94 | 3.6 | 0.41 | +0.04 | 0.55 |
| random_median@14 | robust | 0.73 | 0.98 | 1.00 | 4.5 | 0.41 | +0.14 | 0.49 |
| random_median@14 | robust_paranoid | 0.72 | 0.97 | 1.00 | 7.2 | 0.35 | +0.13 | 0.74 |
| street_arrays(5) | gaussian | 0.71 | 0.87 | 0.82 | 2.8 | 0.49 | -1.12 | 1.25 |
| street_arrays(5) | robust | 0.77 | 0.97 | 0.98 | 3.3 | 0.54 | -0.43 | 0.56 |
| street_arrays(5) | robust_paranoid | 0.76 | 0.96 | 0.99 | 3.7 | 0.51 | -0.45 | 0.59 |
