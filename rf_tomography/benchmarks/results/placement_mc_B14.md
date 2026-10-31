### Budget 14 (search winner: greedy+swap, 838 MC layout evaluations, 388 s)

| layout | scenario | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias |
|---|---|---|---|---|---|---|---|
| mc_optimised | none | 0.67 | 0.98 | 0.97 | 2.5 | 0.52 | +0.01 |
| mc_optimised | moderate* | 0.57 | 0.92 | 0.94 | 3.1 | 0.44 | +0.00 |
| mc_optimised | refl_mild* | 0.53 | 0.91 | 0.94 | 3.7 | 0.42 | -0.74 |
| mc_optimised | severe | 0.46 | 0.82 | 0.92 | 4.8 | 0.34 | +0.05 |
| mc_optimised | refl_strong | 0.44 | 0.75 | 0.84 | 5.7 | 0.33 | -3.08 |
| mc_optimised | combined | 0.48 | 0.86 | 0.90 | 4.4 | 0.38 | -1.16 |
| analytic_optimised | none | 0.69 | 0.99 | 0.97 | 2.4 | 0.53 | +0.02 |
| analytic_optimised | moderate* | 0.56 | 0.94 | 0.97 | 3.1 | 0.44 | -0.11 |
| analytic_optimised | refl_mild* | 0.55 | 0.93 | 0.94 | 3.6 | 0.44 | -0.64 |
| analytic_optimised | severe | 0.44 | 0.82 | 0.90 | 4.8 | 0.33 | +0.14 |
| analytic_optimised | refl_strong | 0.44 | 0.75 | 0.86 | 5.9 | 0.34 | -2.43 |
| analytic_optimised | combined | 0.50 | 0.86 | 0.92 | 4.9 | 0.38 | -0.98 |
| random_median | none | 0.38 | 0.84 | 0.98 | 5.1 | 0.30 | +0.00 |
| random_median | moderate* | 0.38 | 0.77 | 0.94 | 5.9 | 0.28 | +0.18 |
| random_median | refl_mild* | 0.41 | 0.77 | 0.93 | 6.9 | 0.27 | -1.18 |
| random_median | severe | 0.28 | 0.64 | 0.89 | 7.9 | 0.21 | +0.69 |
| random_median | refl_strong | 0.28 | 0.64 | 0.86 | 8.7 | 0.23 | -2.08 |
| random_median | combined | 0.35 | 0.68 | 0.88 | 7.7 | 0.25 | -1.51 |

- mc_optimised (cost 14, design score 0.471): FBxH:array, Sx0:array, RC--:tdoa, Sx-20:rss_az
- analytic_optimised (cost 14, design score 0.455): Sx20:array, RC+-:tdoa, RB-+:array, Sy-60:rss_az, Sy60:rss_az
- random_median (cost 14, design score 0.268): RB+-:rss_az, RA+-:tdoa, Sx-120:rss_az, Sx-80:tdoa, FCyH:array