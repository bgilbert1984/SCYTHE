### Scenarios

| layout | scenario | solver | top-1 | top-3 | cov90 | 90% set | overconf | floor bias | reflected/trial | refl flagged |
|---|---|---|---|---|---|---|---|---|---|---|
| hand:street_array+roofs | none | gaussian | 0.78 | 1.00 | 0.98 | 1.6 | +0.03 | -0.01 | 0.00 | - |
| hand:street_array+roofs | none | robust | 0.78 | 1.00 | 0.99 | 1.8 | -0.00 | -0.00 | 0.00 | - |
| hand:street_array+roofs | none | robust_paranoid | 0.77 | 1.00 | 0.99 | 2.0 | -0.05 | -0.00 | 0.00 | - |
| hand:street_array+roofs | moderate(indep. ref) | gaussian | 0.54 | 0.94 | 0.81 | 1.8 | +0.23 | +0.04 | 0.00 | - |
| hand:street_array+roofs | moderate(indep. ref) | robust | 0.72 | 0.99 | 0.97 | 2.0 | +0.02 | +0.02 | 0.00 | - |
| hand:street_array+roofs | moderate(indep. ref) | robust_paranoid | 0.70 | 0.99 | 0.97 | 2.4 | -0.03 | +0.01 | 0.00 | - |
| hand:street_array+roofs | refl_mild | gaussian | 0.44 | 0.64 | 0.57 | 1.6 | +0.38 | -2.87 | 0.73 | 0.00 |
| hand:street_array+roofs | refl_mild | robust | 0.74 | 0.98 | 0.96 | 2.0 | -0.00 | -0.21 | 0.73 | 0.82 |
| hand:street_array+roofs | refl_mild | robust_paranoid | 0.73 | 0.97 | 0.98 | 2.4 | -0.06 | -0.24 | 0.73 | 0.81 |
| hand:street_array+roofs | refl_strong | gaussian | 0.21 | 0.31 | 0.25 | 1.5 | +0.64 | -6.71 | 1.98 | 0.00 |
| hand:street_array+roofs | refl_strong | robust | 0.59 | 0.88 | 0.91 | 2.9 | +0.05 | -1.06 | 1.98 | 0.72 |
| hand:street_array+roofs | refl_strong | robust_paranoid | 0.57 | 0.85 | 0.92 | 3.7 | -0.00 | -1.25 | 1.98 | 0.72 |
| hand:street_array+roofs | calib_bias | gaussian | 0.76 | 0.99 | 0.91 | 1.7 | +0.05 | +0.04 | 0.00 | - |
| hand:street_array+roofs | calib_bias | robust | 0.73 | 0.98 | 0.93 | 1.8 | +0.04 | +0.05 | 0.00 | - |
| hand:street_array+roofs | calib_bias | robust_paranoid | 0.72 | 0.97 | 0.94 | 2.1 | -0.02 | +0.05 | 0.00 | - |
| hand:street_array+roofs | combined | gaussian | 0.18 | 0.38 | 0.26 | 1.5 | +0.66 | -6.40 | 1.56 | 0.00 |
| hand:street_array+roofs | combined | robust | 0.53 | 0.92 | 0.88 | 2.7 | +0.13 | -1.07 | 1.56 | 0.81 |
| hand:street_array+roofs | combined | robust_paranoid | 0.51 | 0.89 | 0.93 | 3.5 | +0.07 | -1.33 | 1.56 | 0.80 |
| optimised@20 | none | gaussian | 0.75 | 0.99 | 0.97 | 1.9 | -0.01 | -0.01 | 0.00 | - |
| optimised@20 | none | robust | 0.72 | 0.99 | 0.99 | 2.0 | -0.02 | -0.01 | 0.00 | - |
| optimised@20 | none | robust_paranoid | 0.71 | 0.98 | 0.99 | 2.3 | -0.07 | -0.01 | 0.00 | - |
| optimised@20 | moderate(indep. ref) | gaussian | 0.60 | 0.91 | 0.84 | 2.0 | +0.12 | +0.02 | 0.00 | - |
| optimised@20 | moderate(indep. ref) | robust | 0.71 | 0.97 | 0.98 | 2.2 | -0.04 | +0.06 | 0.00 | - |
| optimised@20 | moderate(indep. ref) | robust_paranoid | 0.71 | 0.97 | 0.99 | 2.7 | -0.10 | +0.05 | 0.00 | - |
| optimised@20 | refl_mild | gaussian | 0.36 | 0.60 | 0.47 | 1.9 | +0.40 | -3.18 | 0.84 | 0.00 |
| optimised@20 | refl_mild | robust | 0.67 | 0.99 | 0.98 | 2.4 | -0.00 | -0.16 | 0.84 | 0.73 |
| optimised@20 | refl_mild | robust_paranoid | 0.68 | 0.99 | 0.99 | 3.2 | -0.09 | -0.27 | 0.84 | 0.73 |
| optimised@20 | refl_strong | gaussian | 0.17 | 0.26 | 0.23 | 1.6 | +0.66 | -8.50 | 2.04 | 0.00 |
| optimised@20 | refl_strong | robust | 0.49 | 0.87 | 0.90 | 4.4 | +0.06 | -1.44 | 2.04 | 0.65 |
| optimised@20 | refl_strong | robust_paranoid | 0.48 | 0.81 | 0.91 | 5.8 | -0.03 | -2.07 | 2.04 | 0.65 |
| optimised@20 | calib_bias | gaussian | 0.68 | 0.99 | 0.94 | 1.9 | +0.08 | -0.00 | 0.00 | - |
| optimised@20 | calib_bias | robust | 0.61 | 0.99 | 0.96 | 2.0 | +0.10 | +0.00 | 0.00 | - |
| optimised@20 | calib_bias | robust_paranoid | 0.65 | 0.99 | 0.97 | 2.4 | -0.01 | -0.00 | 0.00 | - |
| optimised@20 | combined | gaussian | 0.23 | 0.38 | 0.33 | 1.8 | +0.55 | -6.54 | 1.53 | 0.00 |
| optimised@20 | combined | robust | 0.52 | 0.90 | 0.91 | 3.3 | +0.08 | -1.21 | 1.53 | 0.70 |
| optimised@20 | combined | robust_paranoid | 0.54 | 0.88 | 0.91 | 4.4 | -0.02 | -1.48 | 1.53 | 0.71 |
| optimised@14 | none | gaussian | 0.64 | 0.99 | 0.96 | 2.1 | +0.01 | -0.05 | 0.00 | - |
| optimised@14 | none | robust | 0.61 | 0.98 | 0.97 | 2.5 | -0.00 | -0.05 | 0.00 | - |
| optimised@14 | none | robust_paranoid | 0.61 | 0.98 | 0.99 | 3.3 | -0.09 | -0.04 | 0.00 | - |
| optimised@14 | moderate(indep. ref) | gaussian | 0.50 | 0.91 | 0.87 | 2.6 | +0.11 | -0.08 | 0.00 | - |
| optimised@14 | moderate(indep. ref) | robust | 0.54 | 0.94 | 0.96 | 3.4 | +0.00 | -0.09 | 0.00 | - |
| optimised@14 | moderate(indep. ref) | robust_paranoid | 0.53 | 0.93 | 0.99 | 4.4 | -0.07 | -0.10 | 0.00 | - |
| optimised@14 | refl_mild | gaussian | 0.39 | 0.66 | 0.60 | 2.0 | +0.29 | -3.28 | 0.71 | 0.00 |
| optimised@14 | refl_mild | robust | 0.57 | 0.93 | 0.97 | 4.0 | -0.04 | -0.47 | 0.71 | 0.69 |
| optimised@14 | refl_mild | robust_paranoid | 0.54 | 0.88 | 0.97 | 5.5 | -0.10 | -0.68 | 0.71 | 0.68 |
| optimised@14 | refl_strong | gaussian | 0.16 | 0.34 | 0.29 | 1.8 | +0.60 | -7.26 | 1.66 | 0.00 |
| optimised@14 | refl_strong | robust | 0.34 | 0.69 | 0.83 | 6.5 | +0.10 | -2.49 | 1.66 | 0.54 |
| optimised@14 | refl_strong | robust_paranoid | 0.33 | 0.66 | 0.84 | 8.1 | +0.02 | -2.88 | 1.66 | 0.54 |
| optimised@14 | calib_bias | gaussian | 0.59 | 0.99 | 0.92 | 2.1 | +0.07 | -0.06 | 0.00 | - |
| optimised@14 | calib_bias | robust | 0.58 | 0.99 | 0.95 | 2.4 | +0.05 | -0.08 | 0.00 | - |
| optimised@14 | calib_bias | robust_paranoid | 0.59 | 0.99 | 0.99 | 3.1 | -0.04 | -0.10 | 0.00 | - |
| optimised@14 | combined | gaussian | 0.26 | 0.47 | 0.43 | 2.1 | +0.44 | -6.02 | 1.05 | 0.00 |
| optimised@14 | combined | robust | 0.45 | 0.81 | 0.87 | 4.8 | +0.05 | -1.56 | 1.05 | 0.63 |
| optimised@14 | combined | robust_paranoid | 0.43 | 0.76 | 0.89 | 6.7 | -0.03 | -1.93 | 1.05 | 0.64 |
| random_median@14 | none | gaussian | 0.51 | 0.86 | 0.94 | 3.7 | -0.06 | -0.04 | 0.00 | - |
| random_median@14 | none | robust | 0.49 | 0.86 | 0.96 | 4.7 | -0.08 | -0.06 | 0.00 | - |
| random_median@14 | none | robust_paranoid | 0.49 | 0.83 | 0.99 | 7.3 | -0.15 | -0.11 | 0.00 | - |
| random_median@14 | moderate(indep. ref) | gaussian | 0.32 | 0.75 | 0.84 | 4.1 | +0.12 | -0.09 | 0.00 | - |
| random_median@14 | moderate(indep. ref) | robust | 0.36 | 0.77 | 0.91 | 5.8 | +0.00 | -0.03 | 0.00 | - |
| random_median@14 | moderate(indep. ref) | robust_paranoid | 0.34 | 0.76 | 0.98 | 8.3 | -0.05 | -0.09 | 0.00 | - |
| random_median@14 | refl_mild | gaussian | 0.31 | 0.66 | 0.73 | 3.4 | +0.17 | -1.84 | 0.61 | 0.00 |
| random_median@14 | refl_mild | robust | 0.37 | 0.74 | 0.93 | 6.5 | +0.01 | -0.82 | 0.61 | 0.33 |
| random_median@14 | refl_mild | robust_paranoid | 0.37 | 0.72 | 0.96 | 9.3 | -0.07 | -0.90 | 0.61 | 0.36 |
| random_median@14 | refl_strong | gaussian | 0.17 | 0.42 | 0.46 | 3.3 | +0.36 | -4.02 | 1.52 | 0.00 |
| random_median@14 | refl_strong | robust | 0.33 | 0.60 | 0.89 | 9.7 | -0.03 | -2.07 | 1.52 | 0.31 |
| random_median@14 | refl_strong | robust_paranoid | 0.32 | 0.59 | 0.89 | 12.2 | -0.10 | -2.05 | 1.52 | 0.35 |
| random_median@14 | calib_bias | gaussian | 0.44 | 0.86 | 0.94 | 3.6 | +0.02 | -0.04 | 0.00 | - |
| random_median@14 | calib_bias | robust | 0.44 | 0.85 | 0.98 | 4.9 | -0.03 | -0.01 | 0.00 | - |
| random_median@14 | calib_bias | robust_paranoid | 0.47 | 0.86 | 1.00 | 7.6 | -0.14 | -0.01 | 0.00 | - |
| random_median@14 | combined | gaussian | 0.26 | 0.50 | 0.56 | 3.4 | +0.26 | -3.27 | 1.00 | 0.00 |
| random_median@14 | combined | robust | 0.38 | 0.64 | 0.84 | 7.9 | -0.05 | -1.76 | 1.00 | 0.34 |
| random_median@14 | combined | robust_paranoid | 0.34 | 0.62 | 0.90 | 10.5 | -0.09 | -1.76 | 1.00 | 0.37 |

### One mechanism at a time

| layout | scenario | solver | top-1 | top-3 | cov90 | 90% set | overconf | floor bias | reflected/trial | refl flagged |
|---|---|---|---|---|---|---|---|---|---|---|
| hand:street_array+roofs | refl_ground_only(.35) | gaussian | 0.09 | 0.14 | 0.09 | 1.4 | +0.80 | -11.34 | 1.98 | 0.00 |
| hand:street_array+roofs | refl_ground_only(.35) | robust | 0.51 | 0.89 | 0.89 | 3.3 | +0.11 | -1.28 | 1.98 | 0.77 |
| hand:street_array+roofs | refl_ground_only(.35) | robust_paranoid | 0.49 | 0.84 | 0.89 | 5.0 | +0.04 | -2.08 | 1.98 | 0.75 |
| hand:street_array+roofs | refl_facade_only(.35) | gaussian | 0.42 | 0.81 | 0.60 | 1.7 | +0.39 | -0.31 | 1.40 | 0.00 |
| hand:street_array+roofs | refl_facade_only(.35) | robust | 0.65 | 0.97 | 0.96 | 2.7 | +0.03 | -0.07 | 1.40 | 0.92 |
| hand:street_array+roofs | refl_facade_only(.35) | robust_paranoid | 0.64 | 0.97 | 0.99 | 3.3 | -0.03 | -0.05 | 1.40 | 0.92 |
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
| optimised@20 | refl_facade_only(.35) | gaussian | 0.38 | 0.71 | 0.57 | 1.9 | +0.39 | -0.18 | 0.96 | 0.00 |
| optimised@20 | refl_facade_only(.35) | robust | 0.72 | 0.97 | 0.97 | 2.4 | -0.05 | -0.11 | 0.96 | 0.97 |
| optimised@20 | refl_facade_only(.35) | robust_paranoid | 0.69 | 0.97 | 0.99 | 3.1 | -0.11 | -0.12 | 0.96 | 0.98 |
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
| hand:street_array+roofs | refl_strong | robust | 0.59 | 0.88 | 0.91 | 2.9 | +0.05 | -1.06 | 1.98 | 0.72 |
| hand:street_array+roofs | refl_strong | robust+el_offset_mean(2deg) | 0.23 | 0.36 | 0.39 | 3.5 | +0.25 | -6.16 | 1.98 | 0.62 |
| hand:street_array+roofs | refl_strong | robust+el_offset_median(2deg) | 0.47 | 0.74 | 0.77 | 3.0 | +0.10 | -2.07 | 1.98 | 0.68 |
| hand:street_array+roofs | combined+el_bias(2deg) | robust | 0.41 | 0.85 | 0.74 | 2.8 | +0.26 | -1.16 | 1.56 | 0.81 |
| hand:street_array+roofs | combined+el_bias(2deg) | robust+el_offset_mean(2deg) | 0.17 | 0.41 | 0.44 | 3.6 | +0.30 | -6.26 | 1.56 | 0.75 |
| hand:street_array+roofs | combined+el_bias(2deg) | robust+el_offset_median(2deg) | 0.42 | 0.83 | 0.84 | 3.0 | +0.12 | -1.84 | 1.56 | 0.80 |
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
| optimised@20 | refl_strong | robust | 0.49 | 0.87 | 0.90 | 4.4 | +0.06 | -1.44 | 2.04 | 0.65 |
| optimised@20 | refl_strong | robust+el_offset_mean(2deg) | 0.26 | 0.39 | 0.40 | 3.1 | +0.29 | -8.50 | 2.04 | 0.48 |
| optimised@20 | refl_strong | robust+el_offset_median(2deg) | 0.40 | 0.68 | 0.68 | 3.3 | +0.16 | -4.82 | 2.04 | 0.56 |
| optimised@20 | combined+el_bias(2deg) | robust | 0.42 | 0.88 | 0.83 | 3.4 | +0.18 | -1.25 | 1.53 | 0.71 |
| optimised@20 | combined+el_bias(2deg) | robust+el_offset_mean(2deg) | 0.28 | 0.52 | 0.51 | 3.1 | +0.27 | -6.37 | 1.53 | 0.61 |
| optimised@20 | combined+el_bias(2deg) | robust+el_offset_median(2deg) | 0.42 | 0.80 | 0.79 | 2.9 | +0.17 | -3.08 | 1.53 | 0.64 |
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
| optimised@14 | refl_strong | robust | 0.34 | 0.69 | 0.83 | 6.5 | +0.10 | -2.49 | 1.66 | 0.54 |
| optimised@14 | refl_strong | robust+el_offset_mean(2deg) | 0.25 | 0.44 | 0.46 | 3.2 | +0.29 | -7.03 | 1.66 | 0.48 |
| optimised@14 | refl_strong | robust+el_offset_median(2deg) | 0.25 | 0.44 | 0.46 | 3.2 | +0.29 | -7.03 | 1.66 | 0.48 |
| optimised@14 | combined+el_bias(2deg) | robust | 0.37 | 0.80 | 0.86 | 4.8 | +0.13 | -1.54 | 1.05 | 0.63 |
| optimised@14 | combined+el_bias(2deg) | robust+el_offset_mean(2deg) | 0.29 | 0.56 | 0.56 | 3.5 | +0.20 | -6.59 | 1.05 | 0.56 |
| optimised@14 | combined+el_bias(2deg) | robust+el_offset_median(2deg) | 0.29 | 0.56 | 0.56 | 3.5 | +0.20 | -6.59 | 1.05 | 0.56 |