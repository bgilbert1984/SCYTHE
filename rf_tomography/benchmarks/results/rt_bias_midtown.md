### midtown_915MHz_mixed

| AoA | layout | n | mean bias | mode bias | MAE | top-1 | cov90 |
|---|---|---|---|---|---|---|---|
| beamformed | analytic@20 | 239 | +0.41 | +0.52 | 2.28 | 0.47 | 0.95 |
| beamformed | street_arrays(5) | 240 | +0.03 | +1.43 | 5.11 | 0.15 | 0.94 |
| strongest | analytic@20 | 239 | +0.39 | +0.48 | 2.18 | 0.51 | 0.97 |
| strongest | street_arrays(5) | 240 | +0.28 | +1.75 | 4.94 | 0.27 | 0.95 |
| oracle | analytic@20 | 239 | +0.81 | +0.92 | 1.66 | 0.57 | 0.98 |
| oracle | street_arrays(5) | 240 | +0.68 | +2.12 | 4.56 | 0.40 | 0.96 |

Bias by position in building (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| low third | 62 | +3.70 | +3.06 | 5.12 | 0.34 | 0.90 | 15.7 |
| mid third | 74 | -1.11 | -0.53 | 1.98 | 0.43 | 0.97 | 8.4 |
| high third | 103 | -0.48 | -0.26 | 0.78 | 0.58 | 0.97 | 4.7 |

Bias by 90% set size (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| narrow | 111 | -0.15 | -0.05 | 0.50 | 0.62 | 0.99 | 3.3 |
| medium | 50 | -0.16 | -0.02 | 0.89 | 0.48 | 0.94 | 5.5 |
| wide | 78 | +1.57 | +1.68 | 5.69 | 0.26 | 0.91 | 18.5 |

Bias by receivers with line of sight (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| 0-2 | 57 | +4.07 | +3.60 | 5.56 | 0.23 | 0.93 | 18.1 |
| 3-4 | 158 | -0.78 | -0.45 | 1.31 | 0.54 | 0.97 | 6.1 |
| 5+ | 24 | -0.49 | -0.42 | 0.85 | 0.58 | 0.92 | 3.8 |

Bias by mean range to used receivers (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| near | 80 | -0.66 | -0.76 | 1.52 | 0.55 | 0.97 | 5.0 |
| mid | 79 | -0.23 | +0.38 | 1.85 | 0.52 | 0.96 | 8.5 |
| far | 80 | +2.10 | +1.94 | 3.46 | 0.35 | 0.93 | 12.7 |

Bias by building size (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| <= 40 floors | 100 | +0.66 | +0.20 | 1.46 | 0.44 | 1.00 | 7.1 |
| > 40 floors | 139 | +0.22 | +0.75 | 2.86 | 0.50 | 0.92 | 9.9 |

Bias by direct-dominant links (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| frac_direct >= 0.8 | 109 | +0.74 | +1.02 | 1.87 | 0.51 | 0.97 | 8.1 |
| frac_direct < 0.8 | 130 | +0.13 | +0.10 | 2.62 | 0.44 | 0.94 | 9.3 |

Bias by facade style of host (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| curtain | 80 | +0.49 | +0.07 | 1.56 | 0.39 | 0.99 | 8.2 |
| glass_curtain | 79 | +0.30 | +0.71 | 2.29 | 0.56 | 0.95 | 8.9 |
| masonry | 80 | +0.43 | +0.78 | 2.98 | 0.47 | 0.93 | 9.1 |

Bias by mean estimate vs truth (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| all | 239 | +0.41 | +0.52 | 2.28 | 0.47 | 0.95 | 8.7 |

Bias by position in building [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| low third | 62 | +3.89 | +3.61 | 4.53 | 0.39 | 0.94 | 15.4 |
| mid third | 74 | -0.42 | +0.00 | 0.99 | 0.55 | 1.00 | 6.4 |
| high third | 103 | -0.16 | -0.04 | 0.42 | 0.70 | 1.00 | 4.1 |

Bias by 90% set size [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| narrow | 129 | -0.08 | +0.01 | 0.40 | 0.68 | 1.00 | 3.3 |
| medium | 38 | -0.16 | -0.05 | 0.64 | 0.61 | 0.97 | 5.0 |
| wide | 72 | +2.94 | +3.07 | 4.47 | 0.36 | 0.96 | 17.2 |

Bias by receivers with line of sight [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| 0-2 | 57 | +4.25 | +4.05 | 5.08 | 0.26 | 0.95 | 18.1 |
| 3-4 | 158 | -0.28 | -0.06 | 0.62 | 0.67 | 0.99 | 4.6 |
| 5+ | 24 | -0.14 | -0.04 | 0.42 | 0.67 | 1.00 | 3.6 |

Bias by mean range to used receivers [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| near | 80 | -0.03 | +0.09 | 0.61 | 0.65 | 1.00 | 4.0 |
| mid | 79 | +0.12 | +0.52 | 1.26 | 0.62 | 1.00 | 6.9 |
| far | 80 | +2.34 | +2.15 | 3.11 | 0.45 | 0.95 | 12.2 |

Bias by mean estimate vs truth [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| all | 239 | +0.81 | +0.92 | 1.66 | 0.57 | 0.98 | 7.7 |

Signed elevation residual (beam peak - geometric), solver-LOS links, degrees:

| receiver / emitter height | links | mean | median | mean of |err|<5 | P(> +5) | P(< -5) |
|---|---|---|---|---|---|---|
| street / emitter in low third | 651 | -4.05 | -0.50 | -0.60 | 0.000 | 0.086 |
| street / emitter in mid third | 908 | -6.64 | -0.50 | -0.68 | 0.004 | 0.108 |
| street / emitter in high third | 1521 | -9.51 | -0.75 | -0.81 | 0.007 | 0.147 |
| roof / emitter in low third | 844 | +0.53 | +0.50 | +0.73 | 0.105 | 0.086 |
| roof / emitter in mid third | 1432 | +0.49 | +0.26 | +0.66 | 0.080 | 0.055 |
| roof / emitter in high third | 2064 | -0.19 | +0.00 | +0.28 | 0.035 | 0.032 |

### midtown_915MHz_concrete

| AoA | layout | n | mean bias | mode bias | MAE | top-1 | cov90 |
|---|---|---|---|---|---|---|---|
| beamformed | analytic@20 | 239 | +0.92 | +1.51 | 2.45 | 0.53 | 0.97 |
| beamformed | street_arrays(5) | 240 | +0.48 | +0.99 | 4.63 | 0.26 | 0.97 |
| strongest | analytic@20 | 239 | +0.92 | +1.49 | 2.38 | 0.54 | 0.97 |
| strongest | street_arrays(5) | 240 | +0.63 | +1.15 | 4.53 | 0.37 | 0.97 |
| oracle | analytic@20 | 239 | +1.09 | +1.53 | 2.15 | 0.56 | 0.97 |
| oracle | street_arrays(5) | 240 | +0.66 | +1.18 | 4.51 | 0.39 | 0.97 |

Bias by position in building (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| low third | 62 | +5.14 | +6.08 | 6.36 | 0.40 | 0.89 | 15.3 |
| mid third | 74 | -0.64 | -0.03 | 1.48 | 0.53 | 1.00 | 7.6 |
| high third | 103 | -0.49 | -0.14 | 0.78 | 0.60 | 1.00 | 4.8 |

Bias by 90% set size (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| narrow | 123 | +0.44 | +0.55 | 0.93 | 0.67 | 0.99 | 3.3 |
| medium | 45 | -0.35 | -0.18 | 0.73 | 0.51 | 1.00 | 5.3 |
| wide | 71 | +2.56 | +4.24 | 6.16 | 0.30 | 0.92 | 19.3 |

Bias by receivers with line of sight (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| 0-2 | 58 | +5.45 | +6.31 | 6.97 | 0.29 | 0.88 | 17.7 |
| 3-4 | 158 | -0.58 | -0.03 | 1.07 | 0.59 | 1.00 | 5.7 |
| 5+ | 23 | -0.13 | -0.04 | 0.49 | 0.65 | 1.00 | 3.7 |

Bias by mean range to used receivers (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| near | 80 | +0.54 | +0.76 | 1.54 | 0.64 | 0.99 | 4.1 |
| mid | 79 | +0.87 | +2.04 | 2.34 | 0.58 | 0.96 | 8.6 |
| far | 80 | +1.35 | +1.74 | 3.45 | 0.36 | 0.96 | 12.6 |

Bias by building size (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| <= 40 floors | 100 | +0.87 | +0.66 | 1.76 | 0.51 | 0.97 | 7.1 |
| > 40 floors | 139 | +0.96 | +2.12 | 2.94 | 0.54 | 0.97 | 9.4 |

Bias by direct-dominant links (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| frac_direct >= 0.8 | 178 | +0.78 | +1.54 | 2.04 | 0.59 | 0.97 | 7.3 |
| frac_direct < 0.8 | 61 | +1.35 | +1.43 | 3.62 | 0.34 | 0.97 | 11.6 |

Bias by facade style of host (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| concrete | 239 | +0.92 | +1.51 | 2.45 | 0.53 | 0.97 | 8.4 |

Bias by mean estimate vs truth (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| all | 239 | +0.92 | +1.51 | 2.45 | 0.53 | 0.97 | 8.4 |

Bias by position in building [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| low third | 62 | +5.20 | +6.11 | 6.10 | 0.37 | 0.90 | 15.6 |
| mid third | 74 | -0.68 | -0.18 | 1.23 | 0.51 | 1.00 | 6.7 |
| high third | 103 | -0.11 | -0.01 | 0.43 | 0.72 | 1.00 | 4.1 |

Bias by 90% set size [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| narrow | 81 | -0.05 | +0.04 | 0.34 | 0.77 | 1.00 | 2.8 |
| medium | 86 | +0.62 | +0.74 | 1.32 | 0.57 | 0.99 | 4.5 |
| wide | 72 | +2.94 | +4.14 | 5.16 | 0.33 | 0.93 | 17.7 |

Bias by receivers with line of sight [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| 0-2 | 58 | +5.47 | +6.40 | 6.75 | 0.26 | 0.90 | 18.0 |
| 3-4 | 158 | -0.34 | -0.03 | 0.71 | 0.66 | 1.00 | 4.8 |
| 5+ | 23 | -0.08 | -0.04 | 0.42 | 0.70 | 1.00 | 3.5 |

Bias by mean range to used receivers [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| near | 80 | +0.57 | +0.78 | 1.52 | 0.64 | 0.99 | 4.0 |
| mid | 79 | +0.69 | +1.70 | 2.09 | 0.62 | 0.97 | 8.1 |
| far | 80 | +2.02 | +2.11 | 2.82 | 0.44 | 0.96 | 11.6 |

Bias by mean estimate vs truth [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| all | 239 | +1.09 | +1.53 | 2.15 | 0.56 | 0.97 | 7.9 |

Signed elevation residual (beam peak - geometric), solver-LOS links, degrees:

| receiver / emitter height | links | mean | median | mean of |err|<5 | P(> +5) | P(< -5) |
|---|---|---|---|---|---|---|
| street / emitter in low third | 651 | -0.34 | -0.25 | -0.31 | 0.000 | 0.002 |
| street / emitter in mid third | 908 | -0.35 | -0.25 | -0.37 | 0.006 | 0.008 |
| street / emitter in high third | 1521 | -0.54 | -0.25 | -0.42 | 0.016 | 0.020 |
| roof / emitter in low third | 843 | -0.54 | +0.25 | +0.38 | 0.019 | 0.076 |
| roof / emitter in mid third | 1428 | +0.01 | +0.25 | +0.32 | 0.015 | 0.031 |
| roof / emitter in high third | 2061 | -0.19 | +0.00 | +0.20 | 0.012 | 0.017 |

### midtown_915MHz_glass_solid

| AoA | layout | n | mean bias | mode bias | MAE | top-1 | cov90 |
|---|---|---|---|---|---|---|---|
| beamformed | analytic@20 | 240 | +0.68 | +0.48 | 1.86 | 0.47 | 0.96 |
| beamformed | street_arrays(5) | 240 | +0.19 | +1.75 | 4.80 | 0.10 | 0.97 |
| strongest | analytic@20 | 240 | +0.68 | +0.56 | 1.76 | 0.55 | 0.97 |
| strongest | street_arrays(5) | 240 | +0.74 | +2.39 | 4.39 | 0.35 | 0.97 |
| oracle | analytic@20 | 240 | +0.73 | +0.57 | 1.60 | 0.57 | 0.98 |
| oracle | street_arrays(5) | 240 | +0.82 | +2.48 | 4.32 | 0.40 | 0.98 |

Bias by position in building (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| low third | 63 | +3.75 | +2.38 | 4.57 | 0.33 | 0.92 | 15.3 |
| mid third | 74 | -0.34 | -0.03 | 1.22 | 0.49 | 0.96 | 6.9 |
| high third | 103 | -0.46 | -0.31 | 0.67 | 0.55 | 0.98 | 4.5 |

Bias by 90% set size (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| narrow | 112 | -0.17 | -0.11 | 0.45 | 0.64 | 0.98 | 3.2 |
| medium | 59 | -0.16 | -0.10 | 0.96 | 0.36 | 0.93 | 5.3 |
| wide | 69 | +2.79 | +1.94 | 4.92 | 0.30 | 0.94 | 18.4 |

Bias by receivers with line of sight (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| 0-2 | 58 | +3.97 | +2.47 | 5.03 | 0.29 | 0.93 | 17.9 |
| 3-4 | 158 | -0.36 | -0.13 | 0.87 | 0.53 | 0.97 | 5.1 |
| 5+ | 24 | -0.42 | -0.29 | 0.74 | 0.58 | 0.92 | 4.0 |

Bias by mean range to used receivers (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| near | 80 | -0.08 | -0.10 | 0.61 | 0.51 | 0.99 | 4.2 |
| mid | 80 | -0.13 | -0.33 | 1.06 | 0.53 | 0.99 | 6.7 |
| far | 80 | +2.26 | +1.88 | 3.92 | 0.39 | 0.90 | 13.3 |

Bias by building size (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| <= 40 floors | 100 | +0.57 | -0.15 | 1.42 | 0.43 | 0.99 | 6.9 |
| > 40 floors | 140 | +0.76 | +0.94 | 2.18 | 0.51 | 0.94 | 8.9 |

Bias by direct-dominant links (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| frac_direct >= 0.8 | 159 | +0.25 | -0.17 | 1.37 | 0.52 | 0.97 | 7.3 |
| frac_direct < 0.8 | 81 | +1.53 | +1.77 | 2.83 | 0.40 | 0.93 | 9.6 |

Bias by facade style of host (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| glass_solid | 240 | +0.68 | +0.48 | 1.86 | 0.47 | 0.96 | 8.1 |

Bias by mean estimate vs truth (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| all | 240 | +0.68 | +0.48 | 1.86 | 0.47 | 0.96 | 8.1 |

Bias by position in building [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| low third | 63 | +3.61 | +2.46 | 4.32 | 0.43 | 0.92 | 15.4 |
| mid third | 74 | -0.42 | -0.09 | 0.90 | 0.55 | 1.00 | 6.4 |
| high third | 103 | -0.20 | -0.11 | 0.44 | 0.68 | 1.00 | 4.1 |

Bias by 90% set size [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| narrow | 128 | -0.12 | -0.05 | 0.41 | 0.70 | 1.00 | 3.2 |
| medium | 39 | -0.18 | -0.08 | 0.62 | 0.59 | 0.97 | 5.0 |
| wide | 73 | +2.70 | +2.00 | 4.22 | 0.36 | 0.95 | 17.1 |

Bias by receivers with line of sight [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| 0-2 | 58 | +3.88 | +2.66 | 4.81 | 0.28 | 0.93 | 18.1 |
| 3-4 | 158 | -0.29 | -0.09 | 0.60 | 0.66 | 0.99 | 4.6 |
| 5+ | 24 | -0.16 | -0.12 | 0.43 | 0.71 | 1.00 | 3.6 |

Bias by mean range to used receivers [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| near | 80 | -0.17 | -0.07 | 0.51 | 0.64 | 1.00 | 4.0 |
| mid | 80 | -0.12 | -0.36 | 0.91 | 0.64 | 1.00 | 6.4 |
| far | 80 | +2.49 | +2.15 | 3.39 | 0.45 | 0.94 | 12.8 |

Bias by mean estimate vs truth [oracle bearings] (analytic@20, robust):

| group | n | mean bias | mode bias | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|
| all | 240 | +0.73 | +0.57 | 1.60 | 0.57 | 0.98 | 7.8 |

Signed elevation residual (beam peak - geometric), solver-LOS links, degrees:

| receiver / emitter height | links | mean | median | mean of |err|<5 | P(> +5) | P(< -5) |
|---|---|---|---|---|---|---|
| street / emitter in low third | 651 | -1.21 | -0.75 | -0.86 | 0.000 | 0.038 |
| street / emitter in mid third | 908 | -1.80 | -0.75 | -0.91 | 0.004 | 0.073 |
| street / emitter in high third | 1521 | -1.93 | -0.75 | -0.98 | 0.007 | 0.068 |
| roof / emitter in low third | 844 | +0.41 | +0.50 | +0.91 | 0.058 | 0.070 |
| roof / emitter in mid third | 1432 | +0.50 | +0.50 | +0.67 | 0.031 | 0.031 |
| roof / emitter in high third | 2065 | +0.10 | +0.00 | +0.25 | 0.030 | 0.019 |
