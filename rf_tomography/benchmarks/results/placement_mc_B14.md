### Budget 14 (search winner: greedy+swap, 732 MC layout evaluations, 263 s)

| layout | scenario | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias |
|---|---|---|---|---|---|---|---|
| mc_optimised | none | 0.70 | 0.99 | 0.98 | 2.4 | 0.54 | +0.06 |
| mc_optimised | moderate* | 0.55 | 0.96 | 0.96 | 2.7 | 0.46 | +0.02 |
| mc_optimised | refl_mild* | 0.57 | 0.90 | 0.92 | 3.4 | 0.45 | -0.49 |
| mc_optimised | severe | 0.42 | 0.78 | 0.87 | 4.2 | 0.34 | +0.04 |
| mc_optimised | refl_strong | 0.45 | 0.77 | 0.85 | 5.3 | 0.35 | -2.42 |
| mc_optimised | combined | 0.44 | 0.82 | 0.85 | 4.2 | 0.37 | -1.42 |
| analytic_optimised | none | 0.69 | 0.99 | 0.97 | 2.4 | 0.53 | +0.02 |
| analytic_optimised | moderate* | 0.56 | 0.94 | 0.97 | 3.1 | 0.44 | -0.11 |
| analytic_optimised | refl_mild* | 0.59 | 0.91 | 0.94 | 3.9 | 0.45 | -0.71 |
| analytic_optimised | severe | 0.44 | 0.82 | 0.90 | 4.8 | 0.33 | +0.14 |
| analytic_optimised | refl_strong | 0.42 | 0.77 | 0.89 | 6.6 | 0.32 | -2.07 |
| analytic_optimised | combined | 0.47 | 0.86 | 0.92 | 4.8 | 0.38 | -1.20 |
| random_median | none | 0.38 | 0.84 | 0.98 | 5.1 | 0.30 | +0.00 |
| random_median | moderate* | 0.38 | 0.77 | 0.94 | 5.9 | 0.28 | +0.18 |
| random_median | refl_mild* | 0.44 | 0.78 | 0.93 | 6.6 | 0.27 | -0.93 |
| random_median | severe | 0.28 | 0.64 | 0.89 | 7.9 | 0.21 | +0.69 |
| random_median | refl_strong | 0.35 | 0.65 | 0.85 | 8.8 | 0.22 | -1.93 |
| random_median | combined | 0.32 | 0.69 | 0.88 | 7.9 | 0.24 | -1.31 |

- mc_optimised (cost 14, design score 0.495): RC+-:tdoa, RB-+:array, Sx-20:tdoa, Sx20:array
- analytic_optimised (cost 14, design score 0.464): Sx20:array, RC+-:tdoa, RB-+:array, Sy-60:rss_az, Sy60:rss_az
- random_median (cost 14, design score 0.279): RB+-:rss_az, RA+-:tdoa, Sx-120:rss_az, Sx-80:tdoa, FCyH:array