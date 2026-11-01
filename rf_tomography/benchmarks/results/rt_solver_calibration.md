### Calibrated beliefs (default -> fitted), safety factor 2.0

| parameter | default | concrete (even emitters) | glass (even emitters) |
|---|---|---|---|
| angle floor (deg) | 2.0 | 0.403 | 0.626 |
| delay floor (m) | 3.0 | 0.500 | 0.500 |
| angle outlier rate | 0.10-0.25 | 0.020 | 0.036 |
| delay outlier rate | 0.10-0.25 | 0.002 | 0.000 |
| non-direct rate (solver-LOS) | 0.10-0.25 | 0.023 | 0.036 |
| path-loss exponent | 2.7 | 4.525 | 2.850 |
| RSS sigma LOS (dB) | 6 | 8.769 | 4.059 |
| RSS sigma NLOS incl. offset (dB) | 8 | 8.209 | 5.698 |
| robust eps / pi_nlos | 0.15 / 0.15 | 0.04 / 0.05 | 0.07 / 0.07 |

### concrete facades: test on odd emitters (n=200), beam-peak AoA

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | bias | MAE | d p(truth) vs robust_default (SE) |
|---|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian_default | 0.85 | 0.92 | 0.91 | 2.2 | 0.62 | +0.17 | 0.40 | -0.021 (0.009) |
| analytic@14 | robust_default | 0.90 | 0.99 | 1.00 | 2.4 | 0.64 | -0.03 | 0.23 | +0.000 (0.000) |
| analytic@14 | gaussian_cal_own | 0.89 | 0.90 | 0.89 | 1.0 | 0.88 | +0.29 | 0.36 | +0.244 (0.019) |
| analytic@14 | robust_cal_own | 0.98 | 1.00 | 1.00 | 1.1 | 0.96 | +0.01 | 0.03 | +0.324 (0.013) |
| analytic@14 | gaussian_cal_other | 0.89 | 0.91 | 0.89 | 1.0 | 0.88 | +0.28 | 0.34 | +0.240 (0.019) |
| analytic@14 | robust_cal_other | 0.97 | 1.00 | 1.00 | 1.1 | 0.96 | -0.01 | 0.03 | +0.319 (0.011) |
| analytic@20 | gaussian_default | 0.87 | 0.95 | 0.92 | 1.8 | 0.72 | +0.10 | 0.30 | -0.040 (0.012) |
| analytic@20 | robust_default | 0.94 | 1.00 | 1.00 | 1.9 | 0.76 | -0.05 | 0.17 | +0.000 (0.000) |
| analytic@20 | gaussian_cal_own | 0.89 | 0.91 | 0.89 | 1.0 | 0.89 | +0.25 | 0.29 | +0.126 (0.020) |
| analytic@20 | robust_cal_own | 0.99 | 1.00 | 0.99 | 1.0 | 0.99 | -0.01 | 0.01 | +0.222 (0.012) |
| analytic@20 | gaussian_cal_other | 0.89 | 0.91 | 0.90 | 1.0 | 0.89 | +0.24 | 0.28 | +0.124 (0.020) |
| analytic@20 | robust_cal_other | 0.98 | 1.00 | 1.00 | 1.0 | 0.98 | -0.01 | 0.02 | +0.218 (0.011) |
| random_median@14 | gaussian_default | 0.79 | 0.96 | 0.99 | 3.6 | 0.45 | +0.05 | 0.40 | +0.023 (0.003) |
| random_median@14 | robust_default | 0.80 | 0.97 | 1.00 | 4.5 | 0.43 | +0.06 | 0.51 | +0.000 (0.000) |
| random_median@14 | gaussian_cal_own | 0.84 | 0.99 | 0.96 | 1.4 | 0.84 | +0.01 | 0.17 | +0.417 (0.020) |
| random_median@14 | robust_cal_own | 0.89 | 1.00 | 0.97 | 1.8 | 0.83 | -0.02 | 0.24 | +0.405 (0.018) |
| random_median@14 | gaussian_cal_other | 0.87 | 0.99 | 0.96 | 1.6 | 0.79 | +0.05 | 0.23 | +0.360 (0.016) |
| random_median@14 | robust_cal_other | 0.89 | 0.99 | 0.97 | 2.0 | 0.77 | +0.03 | 0.38 | +0.342 (0.014) |
| street_arrays(5) | gaussian_default | 0.94 | 1.00 | 1.00 | 2.9 | 0.61 | -0.05 | 0.15 | +0.006 (0.002) |
| street_arrays(5) | robust_default | 0.94 | 1.00 | 1.00 | 3.0 | 0.60 | -0.06 | 0.16 | +0.000 (0.000) |
| street_arrays(5) | gaussian_cal_own | 0.94 | 1.00 | 0.95 | 1.1 | 0.93 | -0.06 | 0.07 | +0.325 (0.020) |
| street_arrays(5) | robust_cal_own | 0.94 | 1.00 | 0.96 | 1.1 | 0.93 | -0.06 | 0.07 | +0.328 (0.019) |
| street_arrays(5) | gaussian_cal_other | 0.94 | 1.00 | 0.98 | 1.2 | 0.90 | -0.05 | 0.08 | +0.297 (0.017) |
| street_arrays(5) | robust_cal_other | 0.94 | 1.00 | 0.98 | 1.2 | 0.90 | -0.06 | 0.09 | +0.295 (0.016) |

### glass facades: test on odd emitters (n=200), beam-peak AoA

| layout | solver | top-1 | top-3 | cov90 | 90% set | p(truth) | bias | MAE | d p(truth) vs robust_default (SE) |
|---|---|---|---|---|---|---|---|---|---|
| analytic@14 | gaussian_default | 0.88 | 0.97 | 0.96 | 2.2 | 0.64 | -0.13 | 0.29 | -0.022 (0.010) |
| analytic@14 | robust_default | 0.92 | 1.00 | 1.00 | 2.4 | 0.66 | -0.04 | 0.22 | +0.000 (0.000) |
| analytic@14 | gaussian_cal_own | 0.90 | 0.96 | 0.92 | 1.1 | 0.88 | -0.15 | 0.18 | +0.226 (0.022) |
| analytic@14 | robust_cal_own | 0.97 | 1.00 | 0.99 | 1.1 | 0.95 | -0.02 | 0.05 | +0.290 (0.012) |
| analytic@14 | gaussian_cal_other | 0.89 | 0.95 | 0.90 | 1.0 | 0.89 | -0.16 | 0.19 | +0.229 (0.023) |
| analytic@14 | robust_cal_other | 0.94 | 1.00 | 0.98 | 1.1 | 0.93 | -0.00 | 0.07 | +0.269 (0.014) |
| analytic@20 | gaussian_default | 0.87 | 0.98 | 0.97 | 1.9 | 0.73 | -0.14 | 0.24 | -0.037 (0.012) |
| analytic@20 | robust_default | 0.94 | 1.00 | 1.00 | 1.9 | 0.77 | -0.05 | 0.16 | +0.000 (0.000) |
| analytic@20 | gaussian_cal_own | 0.88 | 0.96 | 0.89 | 1.1 | 0.87 | -0.16 | 0.18 | +0.105 (0.022) |
| analytic@20 | robust_cal_own | 0.97 | 1.00 | 0.99 | 1.0 | 0.97 | -0.02 | 0.03 | +0.199 (0.012) |
| analytic@20 | gaussian_cal_other | 0.88 | 0.95 | 0.88 | 1.0 | 0.87 | -0.18 | 0.20 | +0.102 (0.022) |
| analytic@20 | robust_cal_other | 0.96 | 1.00 | 0.97 | 1.1 | 0.96 | -0.03 | 0.04 | +0.189 (0.014) |
| random_median@14 | gaussian_default | 0.70 | 0.91 | 0.94 | 3.6 | 0.41 | +0.02 | 0.59 | +0.001 (0.009) |
| random_median@14 | robust_default | 0.73 | 0.95 | 1.00 | 4.7 | 0.41 | +0.18 | 0.57 | +0.000 (0.000) |
| random_median@14 | gaussian_cal_own | 0.73 | 0.91 | 0.86 | 1.7 | 0.65 | +0.00 | 0.57 | +0.243 (0.022) |
| random_median@14 | robust_cal_own | 0.80 | 0.98 | 0.96 | 2.2 | 0.68 | +0.17 | 0.44 | +0.276 (0.016) |
| random_median@14 | gaussian_cal_other | 0.71 | 0.91 | 0.81 | 1.4 | 0.69 | -0.05 | 0.61 | +0.280 (0.027) |
| random_median@14 | robust_cal_other | 0.77 | 0.97 | 0.91 | 2.1 | 0.71 | +0.09 | 0.42 | +0.303 (0.023) |
| street_arrays(5) | gaussian_default | 0.69 | 0.84 | 0.80 | 2.8 | 0.47 | -1.31 | 1.43 | -0.061 (0.011) |
| street_arrays(5) | robust_default | 0.77 | 0.96 | 0.98 | 3.5 | 0.53 | -0.44 | 0.57 | +0.000 (0.000) |
| street_arrays(5) | gaussian_cal_own | 0.69 | 0.83 | 0.73 | 1.3 | 0.66 | -1.33 | 1.40 | +0.136 (0.024) |
| street_arrays(5) | robust_cal_own | 0.81 | 0.98 | 0.93 | 1.6 | 0.78 | -0.37 | 0.45 | +0.251 (0.018) |
| street_arrays(5) | gaussian_cal_other | 0.69 | 0.83 | 0.71 | 1.1 | 0.69 | -1.34 | 1.39 | +0.157 (0.027) |
| street_arrays(5) | robust_cal_other | 0.83 | 0.97 | 0.90 | 1.4 | 0.82 | -0.32 | 0.38 | +0.287 (0.020) |


### Stress test: calibrated beliefs vs a dirtier world (test = odd emitters)

| table | stress | layout | solver | top-1 | top-3 | cov90 | 90% set | bias |
|---|---|---|---|---|---|---|---|---|
| concrete | none | analytic@14 | robust_default | 0.92 | 0.99 | 0.99 | 2.5 | -0.05 |
| concrete | none | analytic@14 | robust_cal_own | 0.98 | 1.00 | 1.00 | 1.1 | -0.00 |
| concrete | none | analytic@14 | robust_cal_own_floors_x3 | 0.94 | 0.99 | 0.99 | 1.4 | -0.02 |
| concrete | none | analytic@14 | gaussian_cal_own | 0.89 | 0.90 | 0.89 | 1.0 | +0.29 |
| concrete | none | analytic@20 | robust_default | 0.93 | 1.00 | 1.00 | 1.9 | -0.05 |
| concrete | none | analytic@20 | robust_cal_own | 0.98 | 1.00 | 0.99 | 1.0 | -0.02 |
| concrete | none | analytic@20 | robust_cal_own_floors_x3 | 0.94 | 1.00 | 1.00 | 1.2 | -0.03 |
| concrete | none | analytic@20 | gaussian_cal_own | 0.89 | 0.91 | 0.89 | 1.0 | +0.26 |
| concrete | none | street_arrays(5) | robust_default | 0.94 | 1.00 | 1.00 | 3.0 | -0.06 |
| concrete | none | street_arrays(5) | robust_cal_own | 0.94 | 1.00 | 0.96 | 1.1 | -0.06 |
| concrete | none | street_arrays(5) | robust_cal_own_floors_x3 | 0.95 | 1.00 | 1.00 | 1.9 | -0.06 |
| concrete | none | street_arrays(5) | gaussian_cal_own | 0.94 | 1.00 | 0.96 | 1.1 | -0.06 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | analytic@14 | robust_default | 0.69 | 0.94 | 0.98 | 2.7 | -0.14 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | analytic@14 | robust_cal_own | 0.68 | 0.94 | 0.84 | 1.8 | -0.07 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | analytic@14 | robust_cal_own_floors_x3 | 0.73 | 0.96 | 0.94 | 1.8 | -0.07 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | analytic@14 | gaussian_cal_own | 0.68 | 0.84 | 0.69 | 1.0 | +0.20 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | analytic@20 | robust_default | 0.81 | 0.99 | 0.99 | 2.0 | -0.06 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | analytic@20 | robust_cal_own | 0.82 | 0.97 | 0.89 | 1.3 | -0.06 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | analytic@20 | robust_cal_own_floors_x3 | 0.83 | 0.99 | 0.96 | 1.4 | -0.02 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | analytic@20 | gaussian_cal_own | 0.72 | 0.85 | 0.74 | 1.0 | +0.25 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | street_arrays(5) | robust_default | 0.81 | 0.97 | 1.00 | 3.2 | -0.03 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | street_arrays(5) | robust_cal_own | 0.69 | 0.95 | 0.83 | 1.5 | +0.00 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | street_arrays(5) | robust_cal_own_floors_x3 | 0.79 | 0.98 | 0.97 | 2.1 | -0.05 |
| concrete | mild (+1 deg, +2 m, 5% outliers) | street_arrays(5) | gaussian_cal_own | 0.66 | 0.86 | 0.70 | 1.2 | -0.12 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | analytic@14 | robust_default | 0.57 | 0.90 | 0.93 | 3.4 | -0.26 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | analytic@14 | robust_cal_own | 0.47 | 0.80 | 0.74 | 2.7 | -0.24 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | analytic@14 | robust_cal_own_floors_x3 | 0.56 | 0.89 | 0.83 | 2.4 | -0.21 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | analytic@14 | gaussian_cal_own | 0.45 | 0.76 | 0.47 | 1.1 | +0.31 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | analytic@20 | robust_default | 0.55 | 0.97 | 0.95 | 2.5 | -0.06 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | analytic@20 | robust_cal_own | 0.41 | 0.90 | 0.66 | 1.9 | +0.02 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | analytic@20 | robust_cal_own_floors_x3 | 0.55 | 0.95 | 0.84 | 1.9 | +0.01 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | analytic@20 | gaussian_cal_own | 0.45 | 0.74 | 0.47 | 1.1 | +0.47 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | street_arrays(5) | robust_default | 0.51 | 0.82 | 0.94 | 4.1 | -0.03 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | street_arrays(5) | robust_cal_own | 0.36 | 0.67 | 0.61 | 2.7 | +0.05 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | street_arrays(5) | robust_cal_own_floors_x3 | 0.45 | 0.79 | 0.81 | 2.8 | +0.02 |
| concrete | strong (+2 deg, +4 m, 15% outliers) | street_arrays(5) | gaussian_cal_own | 0.41 | 0.73 | 0.47 | 1.2 | -0.04 |
| glass | none | analytic@14 | robust_default | 0.92 | 1.00 | 1.00 | 2.3 | -0.06 |
| glass | none | analytic@14 | robust_cal_own | 0.96 | 1.00 | 0.99 | 1.1 | -0.02 |
| glass | none | analytic@14 | robust_cal_own_floors_x3 | 0.86 | 1.00 | 1.00 | 2.0 | -0.07 |
| glass | none | analytic@14 | gaussian_cal_own | 0.89 | 0.96 | 0.91 | 1.1 | -0.16 |
| glass | none | analytic@20 | robust_default | 0.94 | 1.00 | 1.00 | 1.9 | -0.03 |
| glass | none | analytic@20 | robust_cal_own | 0.98 | 1.00 | 0.98 | 1.0 | -0.02 |
| glass | none | analytic@20 | robust_cal_own_floors_x3 | 0.93 | 1.00 | 1.00 | 1.6 | -0.03 |
| glass | none | analytic@20 | gaussian_cal_own | 0.88 | 0.95 | 0.91 | 1.1 | -0.17 |
| glass | none | street_arrays(5) | robust_default | 0.78 | 0.96 | 0.98 | 3.5 | -0.41 |
| glass | none | street_arrays(5) | robust_cal_own | 0.81 | 0.98 | 0.93 | 1.6 | -0.34 |
| glass | none | street_arrays(5) | robust_cal_own_floors_x3 | 0.78 | 0.96 | 0.95 | 3.1 | -0.44 |
| glass | none | street_arrays(5) | gaussian_cal_own | 0.69 | 0.83 | 0.73 | 1.3 | -1.33 |
| glass | mild (+1 deg, +2 m, 5% outliers) | analytic@14 | robust_default | 0.69 | 0.97 | 0.98 | 2.5 | -0.10 |
| glass | mild (+1 deg, +2 m, 5% outliers) | analytic@14 | robust_cal_own | 0.71 | 0.97 | 0.85 | 1.6 | -0.06 |
| glass | mild (+1 deg, +2 m, 5% outliers) | analytic@14 | robust_cal_own_floors_x3 | 0.66 | 0.97 | 0.95 | 2.2 | -0.12 |
| glass | mild (+1 deg, +2 m, 5% outliers) | analytic@14 | gaussian_cal_own | 0.64 | 0.89 | 0.70 | 1.2 | -0.25 |
| glass | mild (+1 deg, +2 m, 5% outliers) | analytic@20 | robust_default | 0.83 | 0.99 | 0.99 | 2.0 | -0.02 |
| glass | mild (+1 deg, +2 m, 5% outliers) | analytic@20 | robust_cal_own | 0.82 | 0.99 | 0.94 | 1.3 | -0.03 |
| glass | mild (+1 deg, +2 m, 5% outliers) | analytic@20 | robust_cal_own_floors_x3 | 0.80 | 0.99 | 0.98 | 1.7 | -0.03 |
| glass | mild (+1 deg, +2 m, 5% outliers) | analytic@20 | gaussian_cal_own | 0.71 | 0.90 | 0.75 | 1.1 | -0.19 |
| glass | mild (+1 deg, +2 m, 5% outliers) | street_arrays(5) | robust_default | 0.63 | 0.93 | 0.97 | 3.7 | -0.46 |
| glass | mild (+1 deg, +2 m, 5% outliers) | street_arrays(5) | robust_cal_own | 0.60 | 0.92 | 0.82 | 1.9 | -0.50 |
| glass | mild (+1 deg, +2 m, 5% outliers) | street_arrays(5) | robust_cal_own_floors_x3 | 0.62 | 0.91 | 0.93 | 3.2 | -0.58 |
| glass | mild (+1 deg, +2 m, 5% outliers) | street_arrays(5) | gaussian_cal_own | 0.48 | 0.71 | 0.58 | 1.3 | -1.38 |
| glass | strong (+2 deg, +4 m, 15% outliers) | analytic@14 | robust_default | 0.60 | 0.92 | 0.94 | 3.3 | -0.18 |
| glass | strong (+2 deg, +4 m, 15% outliers) | analytic@14 | robust_cal_own | 0.51 | 0.81 | 0.78 | 2.5 | -0.18 |
| glass | strong (+2 deg, +4 m, 15% outliers) | analytic@14 | robust_cal_own_floors_x3 | 0.58 | 0.91 | 0.89 | 2.8 | -0.23 |
| glass | strong (+2 deg, +4 m, 15% outliers) | analytic@14 | gaussian_cal_own | 0.49 | 0.81 | 0.53 | 1.1 | -0.15 |
| glass | strong (+2 deg, +4 m, 15% outliers) | analytic@20 | robust_default | 0.60 | 0.96 | 0.94 | 2.4 | -0.04 |
| glass | strong (+2 deg, +4 m, 15% outliers) | analytic@20 | robust_cal_own | 0.48 | 0.93 | 0.68 | 1.8 | -0.06 |
| glass | strong (+2 deg, +4 m, 15% outliers) | analytic@20 | robust_cal_own_floors_x3 | 0.59 | 0.97 | 0.89 | 2.0 | -0.05 |
| glass | strong (+2 deg, +4 m, 15% outliers) | analytic@20 | gaussian_cal_own | 0.47 | 0.77 | 0.50 | 1.1 | +0.03 |
| glass | strong (+2 deg, +4 m, 15% outliers) | street_arrays(5) | robust_default | 0.42 | 0.74 | 0.92 | 4.7 | -0.35 |
| glass | strong (+2 deg, +4 m, 15% outliers) | street_arrays(5) | robust_cal_own | 0.36 | 0.65 | 0.58 | 3.2 | -0.25 |
| glass | strong (+2 deg, +4 m, 15% outliers) | street_arrays(5) | robust_cal_own_floors_x3 | 0.42 | 0.74 | 0.86 | 4.1 | -0.35 |
| glass | strong (+2 deg, +4 m, 15% outliers) | street_arrays(5) | gaussian_cal_own | 0.34 | 0.60 | 0.40 | 1.3 | -1.29 |