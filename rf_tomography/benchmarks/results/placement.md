| kind | budget | n rx | analytic | MC top-1 | MC top-3 | MC top-1 (mismatch) | MC top-3 (mismatch) | cov90 (mismatch) |
|---|---|---|---|---|---|---|---|---|
| opt_nominal | 6 | 3 | 0.34 | 0.28 | 0.69 | 0.18 | 0.52 | 0.72 |
| opt_robust | 6 | 3 | 0.34 | 0.39 | 0.72 | 0.22 | 0.50 | 0.68 |
| random_median | 6 | 1 | 0.04 | 0.22 | 0.66 | 0.25 | 0.56 | 0.87 |
| random_best_of_200 | 6 | 2 | 0.31 | 0.38 | 0.81 | 0.22 | 0.53 | 0.76 |
| opt_nominal | 10 | 4 | 0.52 | 0.60 | 0.98 | 0.39 | 0.85 | 0.81 |
| opt_robust | 10 | 4 | 0.51 | 0.54 | 0.97 | 0.38 | 0.82 | 0.82 |
| random_median | 10 | 4 | 0.28 | 0.32 | 0.70 | 0.22 | 0.50 | 0.82 |
| random_best_of_200 | 10 | 3 | 0.53 | 0.46 | 0.94 | 0.39 | 0.78 | 0.77 |
| opt_nominal | 14 | 5 | 0.64 | 0.66 | 0.97 | 0.48 | 0.90 | 0.78 |
| opt_robust | 14 | 5 | 0.64 | 0.66 | 0.97 | 0.48 | 0.90 | 0.78 |
| random_median | 14 | 5 | 0.41 | 0.51 | 0.86 | 0.32 | 0.71 | 0.78 |
| random_best_of_200 | 14 | 3 | 0.63 | 0.57 | 1.00 | 0.44 | 0.91 | 0.78 |
| opt_nominal | 20 | 6 | 0.74 | 0.78 | 1.00 | 0.52 | 0.95 | 0.80 |
| opt_robust | 20 | 6 | 0.74 | 0.75 | 1.00 | 0.51 | 0.94 | 0.78 |
| random_median | 20 | 7 | 0.52 | 0.53 | 0.94 | 0.41 | 0.84 | 0.80 |
| random_best_of_200 | 20 | 5 | 0.70 | 0.78 | 0.99 | 0.53 | 0.96 | 0.88 |
| hand:street_tdoa | 10 | 5 | 0.05 | 0.08 | 0.23 | 0.03 | 0.17 | 0.88 |
| hand:street_array | 20 | 5 | 0.57 | 0.54 | 0.92 | 0.32 | 0.73 | 0.69 |
| hand:street_array+roofs | 30 | 7 | 0.81 | 0.79 | 1.00 | 0.51 | 0.97 | 0.83 |

Spearman(analytic, MC top-1): nominal 0.96; robust-vs-mismatch 0.94

Chosen layouts:

- opt_nominal @6: Sx60:rss_az, Sx-60:rss_az, Sx0:array
- opt_robust @6: Sx60:rss_az, Sx0:array, Sx-60:rss_az
- opt_nominal @10: Sx20:array, RC+-:tdoa, Sy-20:tdoa, Sy-60:rss_az
- opt_robust @10: Sx20:array, RC+-:tdoa, Sy-20:tdoa, Sx-80:rss_az
- opt_nominal @14: Sx20:array, RC+-:tdoa, RB-+:array, Sy-60:rss_az, Sy60:rss_az
- opt_robust @14: Sx20:array, RC+-:tdoa, RB-+:array, Sy-60:rss_az, Sy60:rss_az
- opt_nominal @20: Sx20:array, RC+-:tdoa, RB-+:array, RC++:tdoa, Sx-20:array, Sy-60:rss_az
- opt_robust @20: Sx20:array, RC+-:tdoa, RB-+:array, Sy-20:array, RC++:tdoa, Sy-60:rss_az

Per-region analytic score, optimum at budget 14: A:low=0.58, A:mid=0.65, A:high=0.59, B:low=0.74, B:mid=0.62, B:high=0.79, C:low=0.59, C:mid=0.62, C:high=0.51, D:low=0.73, D:mid=0.64, D:high=0.66