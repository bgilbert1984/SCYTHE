### Presets

| layout | preset | top-1 | top-3 | band | cov90 | 90% set | overconf | NLOS trusted | outlier cols |
|---|---|---|---|---|---|---|---|---|---|
| hand:street_array | none | 0.56 | 0.93 | 0.97 | 0.96 | 3.0 | +0.00 | 0.00 | 0.00 |
| hand:street_array | moderate | 0.43 | 0.78 | 0.89 | 0.74 | 2.9 | +0.15 | 0.10 | 1.01 |
| hand:street_array | severe | 0.17 | 0.47 | 0.78 | 0.39 | 2.9 | +0.43 | 0.34 | 2.41 |
| hand:street_array+roofs | none | 0.78 | 1.00 | 0.99 | 0.98 | 1.6 | +0.03 | 0.00 | 0.00 |
| hand:street_array+roofs | moderate | 0.54 | 0.94 | 0.97 | 0.81 | 1.8 | +0.23 | 0.12 | 1.63 |
| hand:street_array+roofs | severe | 0.30 | 0.62 | 0.88 | 0.44 | 1.7 | +0.50 | 0.34 | 3.99 |
| optimised@14 | none | 0.64 | 0.99 | 0.95 | 0.96 | 2.1 | +0.01 | 0.00 | 0.00 |
| optimised@14 | moderate | 0.50 | 0.91 | 0.94 | 0.87 | 2.6 | +0.11 | 0.06 | 0.70 |
| optimised@14 | severe | 0.37 | 0.71 | 0.88 | 0.66 | 2.9 | +0.26 | 0.11 | 1.94 |
| optimised@20 | none | 0.75 | 0.99 | 0.99 | 0.97 | 1.9 | -0.01 | 0.00 | 0.00 |
| optimised@20 | moderate | 0.60 | 0.91 | 0.96 | 0.84 | 2.0 | +0.12 | 0.05 | 1.34 |
| optimised@20 | severe | 0.28 | 0.66 | 0.89 | 0.48 | 2.0 | +0.43 | 0.13 | 3.17 |
| random_median@14 | none | 0.51 | 0.86 | 0.97 | 0.94 | 3.7 | -0.06 | 0.00 | 0.00 |
| random_median@14 | moderate | 0.32 | 0.75 | 0.89 | 0.84 | 4.1 | +0.12 | 0.07 | 0.74 |
| random_median@14 | severe | 0.24 | 0.59 | 0.80 | 0.62 | 4.1 | +0.25 | 0.19 | 1.87 |

### One mechanism at a time (moderate severity)

| layout | mechanism | top-1 | top-3 | band | cov90 | 90% set | overconf | NLOS trusted | outlier cols |
|---|---|---|---|---|---|---|---|---|---|
| hand:street_array+roofs | outliers_only | 0.66 | 0.94 | 0.99 | 0.84 | 1.6 | +0.17 | 0.00 | 1.68 |
| hand:street_array+roofs | false_los_only | 0.74 | 0.99 | 0.97 | 0.90 | 1.7 | +0.08 | 0.12 | 0.00 |
| hand:street_array+roofs | false_nlos_only | 0.82 | 0.99 | 0.99 | 0.96 | 1.7 | -0.03 | 0.00 | 0.00 |
| hand:street_array+roofs | geometry_only | 0.79 | 1.00 | 0.99 | 0.98 | 1.7 | +0.02 | 0.00 | 0.00 |
| optimised@20 | outliers_only | 0.57 | 0.94 | 0.96 | 0.81 | 1.9 | +0.18 | 0.00 | 1.28 |
| optimised@20 | false_los_only | 0.70 | 1.00 | 0.99 | 0.97 | 1.9 | +0.04 | 0.03 | 0.00 |
| optimised@20 | false_nlos_only | 0.71 | 1.00 | 0.95 | 0.96 | 2.0 | +0.02 | 0.00 | 0.00 |
| optimised@20 | geometry_only | 0.80 | 1.00 | 0.99 | 0.97 | 1.9 | -0.05 | 0.00 | 0.00 |