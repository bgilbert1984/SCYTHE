### Budget 20 (search winner: analytic+swap, 1147 MC layout evaluations, 616 s)

| layout | scenario | top-1 | top-3 | cov90 | 90% set | p(truth) | floor bias |
|---|---|---|---|---|---|---|---|
| mc_optimised | none | 0.72 | 1.00 | 0.98 | 2.1 | 0.60 | -0.00 |
| mc_optimised | moderate* | 0.60 | 0.97 | 0.96 | 2.4 | 0.51 | +0.00 |
| mc_optimised | refl_mild* | 0.67 | 0.97 | 0.96 | 2.5 | 0.55 | -0.16 |
| mc_optimised | severe | 0.48 | 0.88 | 0.91 | 3.5 | 0.38 | +0.04 |
| mc_optimised | refl_strong | 0.59 | 0.90 | 0.92 | 3.8 | 0.46 | -1.03 |
| mc_optimised | combined | 0.55 | 0.92 | 0.92 | 3.1 | 0.45 | -0.57 |
| analytic_optimised | none | 0.73 | 1.00 | 0.97 | 2.0 | 0.62 | +0.03 |
| analytic_optimised | moderate* | 0.67 | 0.98 | 0.96 | 2.3 | 0.57 | +0.02 |
| analytic_optimised | refl_mild* | 0.68 | 0.97 | 0.96 | 2.5 | 0.54 | -0.21 |
| analytic_optimised | severe | 0.56 | 0.87 | 0.91 | 3.2 | 0.44 | +0.01 |
| analytic_optimised | refl_strong | 0.51 | 0.85 | 0.89 | 4.1 | 0.43 | -1.44 |
| analytic_optimised | combined | 0.59 | 0.93 | 0.92 | 2.8 | 0.49 | -0.45 |
| random_median | none | 0.61 | 0.97 | 0.98 | 3.1 | 0.44 | +0.02 |
| random_median | moderate* | 0.55 | 0.93 | 0.94 | 3.6 | 0.40 | +0.09 |
| random_median | refl_mild* | 0.53 | 0.90 | 0.93 | 3.8 | 0.37 | -0.50 |
| random_median | severe | 0.37 | 0.74 | 0.86 | 4.9 | 0.28 | +0.13 |
| random_median | refl_strong | 0.33 | 0.72 | 0.82 | 5.5 | 0.27 | -2.37 |
| random_median | combined | 0.42 | 0.80 | 0.87 | 4.3 | 0.32 | -1.15 |

- mc_optimised (cost 20, design score 0.590): Sx20:array, RC++:tdoa, Sy-60:rss_az, RB++:array, Sy-20:array, RC--:tdoa
- analytic_optimised (cost 20, design score 0.566): Sx20:array, RC+-:tdoa, RB-+:array, RC++:tdoa, Sx-20:array, Sy-60:rss_az
- random_median (cost 20, design score 0.365): Sy100:tdoa, FCyL:array, FAxH:tdoa, RC-+:tdoa, Sy-80:tdoa, RD-+:rss_az, Sx-120:rss_az