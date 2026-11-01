### midtown_915MHz_mixed: mixed facades, scattering 0.0, 480 ray-traced emitters, AoA = beamformed

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.33 | 0.55 | 0.76 | 9.6 | 0.21 | +0.72 | 4.11 |
| analytic@14 | robust | 0.41 | 0.67 | 0.93 | 11.9 | 0.23 | +1.10 | 3.40 |
| analytic@20 | gaussian | 0.36 | 0.65 | 0.77 | 6.7 | 0.24 | +0.32 | 3.10 |
| analytic@20 | robust | 0.43 | 0.77 | 0.96 | 8.7 | 0.27 | +0.40 | 2.26 |
| random_median@14 | gaussian | 0.07 | 0.24 | 0.90 | 24.6 | 0.07 | -1.41 | 7.06 |
| random_median@14 | robust | 0.06 | 0.23 | 0.96 | 26.6 | 0.07 | -0.60 | 6.39 |
| street_arrays(5) | gaussian | 0.15 | 0.41 | 0.90 | 19.7 | 0.10 | -0.13 | 5.29 |
| street_arrays(5) | robust | 0.15 | 0.42 | 0.94 | 21.4 | 0.09 | +0.06 | 5.08 |

### midtown_915MHz_concrete: concrete facades, scattering 0.0, 480 ray-traced emitters, AoA = beamformed

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.41 | 0.63 | 0.87 | 9.9 | 0.25 | +0.76 | 3.26 |
| analytic@14 | robust | 0.46 | 0.68 | 0.95 | 11.4 | 0.26 | +1.17 | 3.19 |
| analytic@20 | gaussian | 0.47 | 0.74 | 0.87 | 6.9 | 0.30 | +0.90 | 2.28 |
| analytic@20 | robust | 0.54 | 0.83 | 0.95 | 8.3 | 0.31 | +0.96 | 2.45 |
| random_median@14 | gaussian | 0.14 | 0.38 | 0.96 | 24.6 | 0.09 | -0.13 | 5.82 |
| random_median@14 | robust | 0.16 | 0.38 | 0.97 | 25.6 | 0.09 | -0.22 | 5.94 |
| street_arrays(5) | gaussian | 0.29 | 0.53 | 0.97 | 19.9 | 0.12 | +0.69 | 4.42 |
| street_arrays(5) | robust | 0.29 | 0.53 | 0.97 | 21.2 | 0.11 | +0.50 | 4.62 |

### midtown_915MHz_glass_solid: glass_solid facades, scattering 0.0, 480 ray-traced emitters, AoA = beamformed

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias | floor MAE |
|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian | 0.35 | 0.63 | 0.89 | 9.7 | 0.25 | +0.96 | 2.86 |
| analytic@14 | robust | 0.39 | 0.69 | 0.95 | 10.9 | 0.25 | +1.05 | 3.00 |
| analytic@20 | gaussian | 0.46 | 0.73 | 0.90 | 6.9 | 0.29 | +0.94 | 2.05 |
| analytic@20 | robust | 0.51 | 0.82 | 0.97 | 8.2 | 0.29 | +0.69 | 1.91 |
| random_median@14 | gaussian | 0.08 | 0.24 | 0.95 | 24.7 | 0.07 | -0.68 | 6.16 |
| random_median@14 | robust | 0.07 | 0.21 | 0.97 | 25.7 | 0.08 | -0.86 | 6.30 |
| street_arrays(5) | gaussian | 0.09 | 0.40 | 0.97 | 20.1 | 0.10 | +0.30 | 4.68 |
| street_arrays(5) | robust | 0.09 | 0.40 | 0.98 | 21.2 | 0.09 | +0.21 | 4.78 |
