### Scenarios

| layout | scenario | solver | top-1 | top-3 | cov90 | 90% set | overconf | floor bias | reflected/trial | refl flagged |
|---|---|---|---|---|---|---|---|---|---|---|
| hand:street_array+roofs | none | gaussian | 0.78 | 1.00 | 0.98 | 1.6 | +0.03 | -0.01 | 0.00 | - |
| hand:street_array+roofs | none | robust | 0.78 | 1.00 | 0.99 | 1.8 | -0.00 | -0.00 | 0.00 | - |
| hand:street_array+roofs | none | robust_paranoid | 0.77 | 1.00 | 0.99 | 2.0 | -0.05 | -0.00 | 0.00 | - |
| hand:street_array+roofs | moderate(indep. ref) | gaussian | 0.54 | 0.94 | 0.81 | 1.8 | +0.23 | +0.04 | 0.00 | - |
| hand:street_array+roofs | moderate(indep. ref) | robust | 0.72 | 0.99 | 0.97 | 2.0 | +0.02 | +0.02 | 0.00 | - |
| hand:street_array+roofs | moderate(indep. ref) | robust_paranoid | 0.70 | 0.99 | 0.97 | 2.4 | -0.03 | +0.01 | 0.00 | - |
| hand:street_array+roofs | refl_mild | gaussian | 0.43 | 0.53 | 0.49 | 1.6 | +0.41 | -4.49 | 0.88 | 0.00 |
| hand:street_array+roofs | refl_mild | robust | 0.84 | 0.99 | 0.99 | 2.0 | -0.09 | -0.15 | 0.88 | 0.75 |
| hand:street_array+roofs | refl_mild | robust_paranoid | 0.81 | 0.98 | 0.99 | 2.5 | -0.13 | -0.34 | 0.88 | 0.76 |
| hand:street_array+roofs | refl_strong | gaussian | 0.13 | 0.21 | 0.14 | 1.5 | +0.73 | -9.97 | 1.89 | 0.00 |
| hand:street_array+roofs | refl_strong | robust | 0.66 | 0.91 | 0.94 | 3.4 | -0.01 | -1.01 | 1.89 | 0.78 |
| hand:street_array+roofs | refl_strong | robust_paranoid | 0.62 | 0.86 | 0.93 | 4.5 | -0.06 | -2.00 | 1.89 | 0.74 |
| hand:street_array+roofs | calib_bias | gaussian | 0.76 | 0.99 | 0.91 | 1.7 | +0.05 | +0.04 | 0.00 | - |
| hand:street_array+roofs | calib_bias | robust | 0.73 | 0.98 | 0.93 | 1.8 | +0.04 | +0.05 | 0.00 | - |
| hand:street_array+roofs | calib_bias | robust_paranoid | 0.72 | 0.97 | 0.94 | 2.1 | -0.02 | +0.05 | 0.00 | - |
| hand:street_array+roofs | combined | gaussian | 0.18 | 0.34 | 0.25 | 1.5 | +0.66 | -7.47 | 1.49 | 0.00 |
| hand:street_array+roofs | combined | robust | 0.56 | 0.93 | 0.87 | 3.0 | +0.11 | -0.92 | 1.49 | 0.76 |
| hand:street_array+roofs | combined | robust_paranoid | 0.53 | 0.91 | 0.93 | 3.6 | +0.06 | -1.28 | 1.49 | 0.76 |
| optimised@20 | none | gaussian | 0.75 | 0.99 | 0.97 | 1.9 | -0.01 | -0.01 | 0.00 | - |
| optimised@20 | none | robust | 0.72 | 0.99 | 0.99 | 2.0 | -0.02 | -0.01 | 0.00 | - |
| optimised@20 | none | robust_paranoid | 0.71 | 0.98 | 0.99 | 2.3 | -0.07 | -0.01 | 0.00 | - |
| optimised@20 | moderate(indep. ref) | gaussian | 0.60 | 0.91 | 0.84 | 2.0 | +0.12 | +0.02 | 0.00 | - |
| optimised@20 | moderate(indep. ref) | robust | 0.71 | 0.97 | 0.98 | 2.2 | -0.04 | +0.06 | 0.00 | - |
| optimised@20 | moderate(indep. ref) | robust_paranoid | 0.71 | 0.97 | 0.99 | 2.7 | -0.10 | +0.05 | 0.00 | - |
| optimised@20 | refl_mild | gaussian | 0.39 | 0.58 | 0.49 | 1.9 | +0.38 | -3.66 | 0.86 | 0.00 |
| optimised@20 | refl_mild | robust | 0.69 | 0.99 | 0.97 | 2.3 | -0.03 | -0.13 | 0.86 | 0.70 |
| optimised@20 | refl_mild | robust_paranoid | 0.69 | 0.98 | 0.99 | 3.1 | -0.11 | -0.25 | 0.86 | 0.72 |
| optimised@20 | refl_strong | gaussian | 0.14 | 0.25 | 0.18 | 1.7 | +0.68 | -8.49 | 2.12 | 0.00 |
| optimised@20 | refl_strong | robust | 0.53 | 0.83 | 0.88 | 4.2 | +0.06 | -1.72 | 2.12 | 0.60 |
| optimised@20 | refl_strong | robust_paranoid | 0.54 | 0.81 | 0.88 | 5.7 | -0.06 | -2.15 | 2.12 | 0.62 |
| optimised@20 | calib_bias | gaussian | 0.68 | 0.99 | 0.94 | 1.9 | +0.08 | -0.00 | 0.00 | - |
| optimised@20 | calib_bias | robust | 0.61 | 0.99 | 0.96 | 2.0 | +0.10 | +0.00 | 0.00 | - |
| optimised@20 | calib_bias | robust_paranoid | 0.65 | 0.99 | 0.97 | 2.4 | -0.01 | -0.00 | 0.00 | - |
| optimised@20 | combined | gaussian | 0.24 | 0.38 | 0.31 | 1.7 | +0.56 | -5.95 | 1.46 | 0.00 |
| optimised@20 | combined | robust | 0.59 | 0.92 | 0.93 | 3.4 | +0.01 | -1.09 | 1.46 | 0.65 |
| optimised@20 | combined | robust_paranoid | 0.60 | 0.89 | 0.93 | 4.5 | -0.08 | -1.44 | 1.46 | 0.65 |
| optimised@14 | none | gaussian | 0.64 | 0.99 | 0.96 | 2.1 | +0.01 | -0.05 | 0.00 | - |
| optimised@14 | none | robust | 0.61 | 0.98 | 0.97 | 2.5 | -0.00 | -0.05 | 0.00 | - |
| optimised@14 | none | robust_paranoid | 0.61 | 0.98 | 0.99 | 3.3 | -0.09 | -0.04 | 0.00 | - |
| optimised@14 | moderate(indep. ref) | gaussian | 0.50 | 0.91 | 0.87 | 2.6 | +0.11 | -0.08 | 0.00 | - |
| optimised@14 | moderate(indep. ref) | robust | 0.54 | 0.94 | 0.96 | 3.4 | +0.00 | -0.09 | 0.00 | - |
| optimised@14 | moderate(indep. ref) | robust_paranoid | 0.53 | 0.93 | 0.99 | 4.4 | -0.07 | -0.10 | 0.00 | - |
| optimised@14 | refl_mild | gaussian | 0.39 | 0.64 | 0.59 | 2.0 | +0.31 | -3.38 | 0.71 | 0.00 |
| optimised@14 | refl_mild | robust | 0.57 | 0.89 | 0.91 | 3.7 | -0.02 | -0.42 | 0.71 | 0.66 |
| optimised@14 | refl_mild | robust_paranoid | 0.54 | 0.85 | 0.94 | 5.2 | -0.08 | -0.65 | 0.71 | 0.67 |
| optimised@14 | refl_strong | gaussian | 0.23 | 0.40 | 0.34 | 1.9 | +0.54 | -6.41 | 1.49 | 0.00 |
| optimised@14 | refl_strong | robust | 0.53 | 0.82 | 0.87 | 5.2 | -0.05 | -1.73 | 1.49 | 0.57 |
| optimised@14 | refl_strong | robust_paranoid | 0.54 | 0.78 | 0.88 | 7.4 | -0.15 | -2.24 | 1.49 | 0.55 |
| optimised@14 | calib_bias | gaussian | 0.59 | 0.99 | 0.92 | 2.1 | +0.07 | -0.06 | 0.00 | - |
| optimised@14 | calib_bias | robust | 0.58 | 0.99 | 0.95 | 2.4 | +0.05 | -0.08 | 0.00 | - |
| optimised@14 | calib_bias | robust_paranoid | 0.59 | 0.99 | 0.99 | 3.1 | -0.04 | -0.10 | 0.00 | - |
| optimised@14 | combined | gaussian | 0.25 | 0.49 | 0.44 | 2.0 | +0.47 | -6.21 | 1.11 | 0.00 |
| optimised@14 | combined | robust | 0.46 | 0.79 | 0.88 | 4.8 | +0.02 | -1.55 | 1.11 | 0.59 |
| optimised@14 | combined | robust_paranoid | 0.46 | 0.76 | 0.89 | 6.7 | -0.07 | -1.86 | 1.11 | 0.60 |
| random_median@14 | none | gaussian | 0.51 | 0.86 | 0.94 | 3.7 | -0.06 | -0.04 | 0.00 | - |
| random_median@14 | none | robust | 0.49 | 0.86 | 0.96 | 4.7 | -0.08 | -0.06 | 0.00 | - |
| random_median@14 | none | robust_paranoid | 0.49 | 0.83 | 0.99 | 7.3 | -0.15 | -0.11 | 0.00 | - |
| random_median@14 | moderate(indep. ref) | gaussian | 0.32 | 0.75 | 0.84 | 4.1 | +0.12 | -0.09 | 0.00 | - |
| random_median@14 | moderate(indep. ref) | robust | 0.36 | 0.77 | 0.91 | 5.8 | +0.00 | -0.03 | 0.00 | - |
| random_median@14 | moderate(indep. ref) | robust_paranoid | 0.34 | 0.76 | 0.98 | 8.3 | -0.05 | -0.09 | 0.00 | - |
| random_median@14 | refl_mild | gaussian | 0.36 | 0.72 | 0.77 | 3.4 | +0.12 | -1.61 | 0.53 | 0.00 |
| random_median@14 | refl_mild | robust | 0.42 | 0.83 | 0.99 | 6.3 | -0.04 | -0.39 | 0.53 | 0.34 |
| random_median@14 | refl_mild | robust_paranoid | 0.41 | 0.82 | 0.99 | 8.8 | -0.11 | -0.67 | 0.53 | 0.34 |
| random_median@14 | refl_strong | gaussian | 0.24 | 0.47 | 0.52 | 3.3 | +0.30 | -5.24 | 1.52 | 0.00 |
| random_median@14 | refl_strong | robust | 0.33 | 0.62 | 0.85 | 8.5 | -0.01 | -2.23 | 1.52 | 0.24 |
| random_median@14 | refl_strong | robust_paranoid | 0.35 | 0.62 | 0.86 | 11.3 | -0.11 | -2.44 | 1.52 | 0.27 |
| random_median@14 | calib_bias | gaussian | 0.44 | 0.86 | 0.94 | 3.6 | +0.02 | -0.04 | 0.00 | - |
| random_median@14 | calib_bias | robust | 0.44 | 0.85 | 0.98 | 4.9 | -0.03 | -0.01 | 0.00 | - |
| random_median@14 | calib_bias | robust_paranoid | 0.47 | 0.86 | 1.00 | 7.6 | -0.14 | -0.01 | 0.00 | - |
| random_median@14 | combined | gaussian | 0.27 | 0.46 | 0.53 | 3.6 | +0.23 | -4.00 | 1.10 | 0.00 |
| random_median@14 | combined | robust | 0.36 | 0.66 | 0.88 | 7.9 | -0.02 | -1.33 | 1.10 | 0.28 |
| random_median@14 | combined | robust_paranoid | 0.34 | 0.65 | 0.91 | 10.6 | -0.08 | -1.53 | 1.10 | 0.32 |

### One mechanism at a time

| layout | scenario | solver | top-1 | top-3 | cov90 | 90% set | overconf | floor bias | reflected/trial | refl flagged |
|---|---|---|---|---|---|---|---|---|---|---|
| hand:street_array+roofs | refl_ground_only(.35) | gaussian | 0.09 | 0.14 | 0.09 | 1.4 | +0.80 | -11.34 | 1.98 | 0.00 |
| hand:street_array+roofs | refl_ground_only(.35) | robust | 0.51 | 0.89 | 0.89 | 3.3 | +0.11 | -1.28 | 1.98 | 0.77 |
| hand:street_array+roofs | refl_ground_only(.35) | robust_paranoid | 0.49 | 0.84 | 0.89 | 5.0 | +0.04 | -2.08 | 1.98 | 0.75 |
| hand:street_array+roofs | refl_facade_only(.35) | gaussian | 0.66 | 0.88 | 0.77 | 1.7 | +0.15 | +0.08 | 0.60 | 0.00 |
| hand:street_array+roofs | refl_facade_only(.35) | robust | 0.76 | 1.00 | 0.98 | 2.0 | -0.03 | +0.05 | 0.60 | 0.81 |
| hand:street_array+roofs | refl_facade_only(.35) | robust_paranoid | 0.76 | 1.00 | 0.99 | 2.3 | -0.10 | +0.04 | 0.60 | 0.80 |
| hand:street_array+roofs | common_delay_only(40m) | gaussian | 0.72 | 1.00 | 0.98 | 1.7 | +0.09 | +0.01 | 0.00 | - |
| hand:street_array+roofs | common_delay_only(40m) | robust | 0.71 | 1.00 | 0.98 | 1.8 | +0.06 | +0.01 | 0.00 | - |
| hand:street_array+roofs | common_delay_only(40m) | robust_paranoid | 0.69 | 1.00 | 0.99 | 2.1 | +0.03 | +0.00 | 0.00 | - |
| hand:street_array+roofs | el_bias_only(0.5deg) | gaussian | 0.78 | 0.99 | 0.95 | 1.6 | +0.04 | +0.05 | 0.00 | - |
| hand:street_array+roofs | el_bias_only(0.5deg) | robust | 0.79 | 0.99 | 0.94 | 1.8 | -0.01 | +0.06 | 0.00 | - |
| hand:street_array+roofs | el_bias_only(0.5deg) | robust_paranoid | 0.80 | 0.99 | 0.96 | 2.0 | -0.08 | +0.06 | 0.00 | - |
| hand:street_array+roofs | el_bias_only(2deg) | gaussian | 0.59 | 0.94 | 0.78 | 1.7 | +0.20 | +0.02 | 0.00 | - |
| hand:street_array+roofs | el_bias_only(2deg) | robust | 0.57 | 0.94 | 0.81 | 1.9 | +0.18 | +0.02 | 0.00 | - |
| hand:street_array+roofs | el_bias_only(2deg) | robust_paranoid | 0.54 | 0.94 | 0.84 | 2.2 | +0.16 | +0.02 | 0.00 | - |
| optimised@20 | refl_ground_only(.35) | gaussian | 0.14 | 0.23 | 0.16 | 1.5 | +0.71 | -10.52 | 2.10 | 0.00 |
| optimised@20 | refl_ground_only(.35) | robust | 0.52 | 0.85 | 0.88 | 3.9 | +0.06 | -2.63 | 2.10 | 0.53 |
| optimised@20 | refl_ground_only(.35) | robust_paranoid | 0.49 | 0.80 | 0.87 | 5.4 | -0.01 | -3.47 | 2.10 | 0.54 |
| optimised@20 | refl_facade_only(.35) | gaussian | 0.52 | 0.81 | 0.68 | 1.8 | +0.27 | -0.20 | 0.81 | 0.00 |
| optimised@20 | refl_facade_only(.35) | robust | 0.73 | 0.97 | 0.95 | 2.4 | -0.06 | -0.02 | 0.81 | 0.98 |
| optimised@20 | refl_facade_only(.35) | robust_paranoid | 0.74 | 0.98 | 0.97 | 3.0 | -0.14 | -0.02 | 0.81 | 0.98 |
| optimised@20 | common_delay_only(40m) | gaussian | 0.76 | 1.00 | 0.98 | 1.9 | +0.00 | +0.01 | 0.00 | - |
| optimised@20 | common_delay_only(40m) | robust | 0.67 | 1.00 | 0.99 | 2.0 | +0.06 | +0.00 | 0.00 | - |
| optimised@20 | common_delay_only(40m) | robust_paranoid | 0.68 | 1.00 | 0.99 | 2.3 | -0.02 | -0.00 | 0.00 | - |
| optimised@20 | el_bias_only(0.5deg) | gaussian | 0.72 | 0.99 | 0.95 | 1.9 | +0.03 | -0.01 | 0.00 | - |
| optimised@20 | el_bias_only(0.5deg) | robust | 0.66 | 0.99 | 0.96 | 2.0 | +0.05 | -0.00 | 0.00 | - |
| optimised@20 | el_bias_only(0.5deg) | robust_paranoid | 0.68 | 0.99 | 0.99 | 2.3 | -0.03 | -0.00 | 0.00 | - |
| optimised@20 | el_bias_only(2deg) | gaussian | 0.51 | 0.97 | 0.83 | 1.9 | +0.24 | -0.01 | 0.00 | - |
| optimised@20 | el_bias_only(2deg) | robust | 0.50 | 0.98 | 0.88 | 2.0 | +0.20 | +0.01 | 0.00 | - |
| optimised@20 | el_bias_only(2deg) | robust_paranoid | 0.48 | 0.98 | 0.93 | 2.4 | +0.15 | +0.00 | 0.00 | - |

### Common elevation offset as a nuisance parameter

| layout | scenario | solver | top-1 | top-3 | cov90 | 90% set | overconf | floor bias | reflected/trial | refl flagged |
|---|---|---|---|---|---|---|---|---|---|---|
| hand:street_array+roofs | none | robust | 0.78 | 1.00 | 0.99 | 1.8 | -0.00 | -0.00 | 0.00 | - |
| hand:street_array+roofs | none | robust+el_offset_mean(2deg) | 0.68 | 0.99 | 0.99 | 2.4 | -0.05 | +0.03 | 0.00 | - |
| hand:street_array+roofs | none | robust+el_offset_median(2deg) | 0.68 | 0.99 | 0.98 | 2.4 | -0.06 | +0.02 | 0.00 | - |
| hand:street_array+roofs | moderate(indep. ref) | robust | 0.72 | 0.99 | 0.97 | 2.0 | +0.02 | +0.02 | 0.00 | - |
| hand:street_array+roofs | moderate(indep. ref) | robust+el_offset_mean(2deg) | 0.58 | 0.97 | 0.95 | 2.7 | -0.01 | -0.05 | 0.00 | - |
| hand:street_array+roofs | moderate(indep. ref) | robust+el_offset_median(2deg) | 0.62 | 0.99 | 0.97 | 2.7 | -0.04 | -0.02 | 0.00 | - |
| hand:street_array+roofs | el_bias(1deg) | robust | 0.73 | 0.98 | 0.93 | 1.8 | +0.04 | +0.05 | 0.00 | - |
| hand:street_array+roofs | el_bias(1deg) | robust+el_offset_mean(2deg) | 0.68 | 0.99 | 0.97 | 2.4 | -0.05 | +0.04 | 0.00 | - |
| hand:street_array+roofs | el_bias(1deg) | robust+el_offset_median(2deg) | 0.68 | 0.98 | 0.97 | 2.5 | -0.06 | +0.04 | 0.00 | - |
| hand:street_array+roofs | el_bias(2deg) | robust | 0.57 | 0.94 | 0.81 | 1.9 | +0.18 | +0.02 | 0.00 | - |
| hand:street_array+roofs | el_bias(2deg) | robust+el_offset_mean(2deg) | 0.66 | 0.98 | 0.96 | 2.4 | -0.03 | +0.03 | 0.00 | - |
| hand:street_array+roofs | el_bias(2deg) | robust+el_offset_median(2deg) | 0.64 | 0.97 | 0.96 | 2.5 | -0.03 | +0.03 | 0.00 | - |
| hand:street_array+roofs | el_bias(3deg) | robust | 0.45 | 0.84 | 0.66 | 1.9 | +0.29 | -0.01 | 0.00 | - |
| hand:street_array+roofs | el_bias(3deg) | robust+el_offset_mean(2deg) | 0.59 | 0.96 | 0.93 | 2.4 | +0.03 | +0.03 | 0.00 | - |
| hand:street_array+roofs | el_bias(3deg) | robust+el_offset_median(2deg) | 0.59 | 0.96 | 0.93 | 2.4 | +0.02 | +0.03 | 0.00 | - |
| hand:street_array+roofs | refl_strong | robust | 0.66 | 0.91 | 0.94 | 3.4 | -0.01 | -1.01 | 1.89 | 0.78 |
| hand:street_array+roofs | refl_strong | robust+el_offset_mean(2deg) | 0.15 | 0.26 | 0.26 | 3.4 | +0.35 | -9.73 | 1.89 | 0.58 |
| hand:street_array+roofs | refl_strong | robust+el_offset_median(2deg) | 0.54 | 0.77 | 0.79 | 3.3 | +0.02 | -3.29 | 1.89 | 0.71 |
| hand:street_array+roofs | combined+el_bias(2deg) | robust | 0.44 | 0.87 | 0.76 | 3.1 | +0.21 | -0.94 | 1.49 | 0.76 |
| hand:street_array+roofs | combined+el_bias(2deg) | robust+el_offset_mean(2deg) | 0.21 | 0.39 | 0.42 | 3.5 | +0.26 | -7.01 | 1.49 | 0.64 |
| hand:street_array+roofs | combined+el_bias(2deg) | robust+el_offset_median(2deg) | 0.51 | 0.86 | 0.86 | 2.9 | +0.06 | -1.78 | 1.49 | 0.72 |
| optimised@20 | none | robust | 0.72 | 0.99 | 0.99 | 2.0 | -0.02 | -0.01 | 0.00 | - |
| optimised@20 | none | robust+el_offset_mean(2deg) | 0.70 | 0.99 | 0.97 | 2.4 | -0.09 | -0.01 | 0.00 | - |
| optimised@20 | none | robust+el_offset_median(2deg) | 0.69 | 0.99 | 0.97 | 2.5 | -0.08 | -0.02 | 0.00 | - |
| optimised@20 | moderate(indep. ref) | robust | 0.71 | 0.97 | 0.98 | 2.2 | -0.04 | +0.06 | 0.00 | - |
| optimised@20 | moderate(indep. ref) | robust+el_offset_mean(2deg) | 0.61 | 0.96 | 0.97 | 2.7 | -0.02 | +0.02 | 0.00 | - |
| optimised@20 | moderate(indep. ref) | robust+el_offset_median(2deg) | 0.62 | 0.96 | 0.97 | 2.6 | -0.03 | +0.03 | 0.00 | - |
| optimised@20 | el_bias(1deg) | robust | 0.61 | 0.99 | 0.96 | 2.0 | +0.10 | +0.00 | 0.00 | - |
| optimised@20 | el_bias(1deg) | robust+el_offset_mean(2deg) | 0.64 | 0.99 | 0.99 | 2.4 | -0.02 | +0.01 | 0.00 | - |
| optimised@20 | el_bias(1deg) | robust+el_offset_median(2deg) | 0.64 | 0.99 | 0.98 | 2.5 | -0.02 | +0.00 | 0.00 | - |
| optimised@20 | el_bias(2deg) | robust | 0.50 | 0.98 | 0.88 | 2.0 | +0.20 | +0.01 | 0.00 | - |
| optimised@20 | el_bias(2deg) | robust+el_offset_mean(2deg) | 0.61 | 0.98 | 0.94 | 2.4 | +0.00 | +0.01 | 0.00 | - |
| optimised@20 | el_bias(2deg) | robust+el_offset_median(2deg) | 0.60 | 0.99 | 0.95 | 2.4 | +0.02 | +0.01 | 0.00 | - |
| optimised@20 | el_bias(3deg) | robust | 0.37 | 0.93 | 0.79 | 2.1 | +0.33 | +0.01 | 0.00 | - |
| optimised@20 | el_bias(3deg) | robust+el_offset_mean(2deg) | 0.58 | 0.97 | 0.93 | 2.4 | +0.03 | +0.01 | 0.00 | - |
| optimised@20 | el_bias(3deg) | robust+el_offset_median(2deg) | 0.57 | 0.97 | 0.94 | 2.4 | +0.05 | +0.01 | 0.00 | - |
| optimised@20 | refl_strong | robust | 0.53 | 0.83 | 0.88 | 4.2 | +0.06 | -1.72 | 2.12 | 0.60 |
| optimised@20 | refl_strong | robust+el_offset_mean(2deg) | 0.24 | 0.44 | 0.42 | 3.1 | +0.30 | -7.97 | 2.12 | 0.48 |
| optimised@20 | refl_strong | robust+el_offset_median(2deg) | 0.40 | 0.72 | 0.74 | 3.4 | +0.15 | -3.33 | 2.12 | 0.57 |
| optimised@20 | combined+el_bias(2deg) | robust | 0.49 | 0.89 | 0.88 | 3.4 | +0.09 | -1.13 | 1.46 | 0.65 |
| optimised@20 | combined+el_bias(2deg) | robust+el_offset_mean(2deg) | 0.32 | 0.56 | 0.56 | 3.0 | +0.23 | -5.62 | 1.46 | 0.57 |
| optimised@20 | combined+el_bias(2deg) | robust+el_offset_median(2deg) | 0.49 | 0.83 | 0.82 | 3.0 | +0.08 | -2.62 | 1.46 | 0.61 |
| optimised@14 | none | robust | 0.61 | 0.98 | 0.97 | 2.5 | -0.00 | -0.05 | 0.00 | - |
| optimised@14 | none | robust+el_offset_mean(2deg) | 0.57 | 0.96 | 0.96 | 2.8 | -0.03 | -0.03 | 0.00 | - |
| optimised@14 | none | robust+el_offset_median(2deg) | 0.57 | 0.96 | 0.96 | 2.8 | -0.03 | -0.03 | 0.00 | - |
| optimised@14 | moderate(indep. ref) | robust | 0.54 | 0.94 | 0.96 | 3.4 | +0.00 | -0.09 | 0.00 | - |
| optimised@14 | moderate(indep. ref) | robust+el_offset_mean(2deg) | 0.47 | 0.89 | 0.93 | 3.4 | +0.01 | -0.11 | 0.00 | - |
| optimised@14 | moderate(indep. ref) | robust+el_offset_median(2deg) | 0.47 | 0.89 | 0.93 | 3.4 | +0.01 | -0.11 | 0.00 | - |
| optimised@14 | el_bias(1deg) | robust | 0.58 | 0.99 | 0.95 | 2.4 | +0.05 | -0.08 | 0.00 | - |
| optimised@14 | el_bias(1deg) | robust+el_offset_mean(2deg) | 0.60 | 0.97 | 0.97 | 2.8 | -0.06 | -0.11 | 0.00 | - |
| optimised@14 | el_bias(1deg) | robust+el_offset_median(2deg) | 0.60 | 0.97 | 0.97 | 2.8 | -0.06 | -0.11 | 0.00 | - |
| optimised@14 | el_bias(2deg) | robust | 0.46 | 0.97 | 0.91 | 2.5 | +0.16 | -0.08 | 0.00 | - |
| optimised@14 | el_bias(2deg) | robust+el_offset_mean(2deg) | 0.53 | 0.97 | 0.96 | 2.8 | +0.01 | -0.10 | 0.00 | - |
| optimised@14 | el_bias(2deg) | robust+el_offset_median(2deg) | 0.53 | 0.97 | 0.96 | 2.8 | +0.01 | -0.10 | 0.00 | - |
| optimised@14 | el_bias(3deg) | robust | 0.38 | 0.89 | 0.83 | 2.6 | +0.23 | -0.07 | 0.00 | - |
| optimised@14 | el_bias(3deg) | robust+el_offset_mean(2deg) | 0.49 | 0.94 | 0.93 | 2.9 | +0.04 | -0.09 | 0.00 | - |
| optimised@14 | el_bias(3deg) | robust+el_offset_median(2deg) | 0.49 | 0.94 | 0.93 | 2.9 | +0.04 | -0.09 | 0.00 | - |
| optimised@14 | refl_strong | robust | 0.53 | 0.82 | 0.87 | 5.2 | -0.05 | -1.73 | 1.49 | 0.57 |
| optimised@14 | refl_strong | robust+el_offset_mean(2deg) | 0.29 | 0.54 | 0.57 | 3.5 | +0.21 | -5.52 | 1.49 | 0.50 |
| optimised@14 | refl_strong | robust+el_offset_median(2deg) | 0.29 | 0.54 | 0.57 | 3.5 | +0.21 | -5.52 | 1.49 | 0.50 |
| optimised@14 | combined+el_bias(2deg) | robust | 0.38 | 0.79 | 0.86 | 4.9 | +0.10 | -1.54 | 1.11 | 0.60 |
| optimised@14 | combined+el_bias(2deg) | robust+el_offset_mean(2deg) | 0.30 | 0.53 | 0.55 | 3.5 | +0.20 | -6.47 | 1.11 | 0.56 |
| optimised@14 | combined+el_bias(2deg) | robust+el_offset_median(2deg) | 0.30 | 0.53 | 0.55 | 3.5 | +0.20 | -6.47 | 1.11 | 0.56 |