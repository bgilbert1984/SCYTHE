### Budget 20 (search winner: greedy+swap, 1166 MC layout evaluations, 543 s)

| layout | scenario | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias |
|---|---|---|---|---|---|---|---|
| mc_optimised | none | 0.75 | 1.00 | 0.97 | 2.0 | 0.64 | +0.01 |
| mc_optimised | moderate* | 0.69 | 0.96 | 0.95 | 2.4 | 0.56 | -0.04 |
| mc_optimised | refl_mild* | 0.68 | 0.96 | 0.96 | 2.6 | 0.58 | -0.29 |
| mc_optimised | severe | 0.57 | 0.89 | 0.91 | 3.2 | 0.46 | -0.11 |
| mc_optimised | refl_strong | 0.51 | 0.84 | 0.89 | 4.2 | 0.43 | -1.72 |
| mc_optimised | combined | 0.56 | 0.92 | 0.92 | 3.3 | 0.47 | -0.59 |
| analytic_optimised | none | 0.73 | 1.00 | 0.97 | 2.0 | 0.62 | +0.03 |
| analytic_optimised | moderate* | 0.67 | 0.98 | 0.96 | 2.3 | 0.57 | +0.02 |
| analytic_optimised | refl_mild* | 0.68 | 0.97 | 0.95 | 2.4 | 0.55 | -0.17 |
| analytic_optimised | severe | 0.56 | 0.87 | 0.91 | 3.2 | 0.44 | +0.01 |
| analytic_optimised | refl_strong | 0.54 | 0.85 | 0.89 | 3.8 | 0.45 | -1.40 |
| analytic_optimised | combined | 0.58 | 0.93 | 0.89 | 3.0 | 0.47 | -0.52 |
| random_median | none | 0.61 | 0.97 | 0.98 | 3.1 | 0.44 | +0.02 |
| random_median | moderate* | 0.55 | 0.93 | 0.94 | 3.6 | 0.40 | +0.09 |
| random_median | refl_mild* | 0.50 | 0.87 | 0.93 | 4.0 | 0.37 | -0.64 |
| random_median | severe | 0.37 | 0.74 | 0.86 | 4.9 | 0.28 | +0.13 |
| random_median | refl_strong | 0.34 | 0.65 | 0.78 | 5.8 | 0.26 | -2.11 |
| random_median | combined | 0.38 | 0.79 | 0.90 | 5.3 | 0.30 | -1.14 |

- mc_optimised (cost 20, design score 0.621): RC+-:tdoa, RB-+:array, Sy40:array, Sy-40:array, Sx20:array
- analytic_optimised (cost 20, design score 0.582): Sx20:array, RC+-:tdoa, RB-+:array, RC++:tdoa, Sx-20:array, Sy-60:rss_az
- random_median (cost 20, design score 0.372): Sy100:tdoa, FCyL:array, FAxH:tdoa, RC-+:tdoa, Sy-80:tdoa, RD-+:rss_az, Sx-120:rss_az