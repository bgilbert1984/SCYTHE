### Ray-traced observables vs the assumed error model (915 MHz, glass facades, scattering 0.3, 400 emitters x 57 sites, 22762 links, first-arrival threshold 20 dB)

| quantity | solver-LOS links | solver-NLOS links | assumed in the Gaussian/impairment experiments |
|---|---|---|---|
| links | 19931 | 2831 | |
| strongest path is direct-like | 0.97 | 0.30 | LOS: ~1 minus outliers (10-25%); NLOS: AoA/TOA not trusted |
| first arrival is direct-like | 1.00 | 0.97 | |
| elevation error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 11.98 | 1.9 / 8.9 / 34.8 | Gaussian 2 deg floor: 1.35 / 3.3 / 5.2 |
| azimuth error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 40.74 | 19.9 / 72.9 / 94.3 | same |
| P(elevation error > 5 deg) | 0.02 | 0.22 | Gaussian 0.01; with outliers 0.10-0.25 |
| P(azimuth error > 5 deg) | 0.03 | 0.59 | same |
| TOA excess q50 / q90 / q99 (m) | -0.0 / 0.0 / 0.0 | -0.0 / 0.0 / 42.9 | 3 m Gaussian floor (+3 m sync); outliers exp. mean 15-25 m |
| P(TOA excess > 10 m) | 0.00 | 0.02 | Gaussian ~0.02; with outliers 0.10-0.25 |
| path-loss exponent n | 2.87 | 3.67 | 2.7 |
| RSS residual sigma (dB) | 3.98 | 6.07 | 6 (LOS) / 8 (NLOS) |
| strongest path: direct / one bounce / diffraction / multi | 0.97 / 0.03 / 0.00 / 0.01 | 0.30 / 0.52 / 0.00 / 0.18 | |

Mean power offset between solver-LOS and solver-NLOS fits: -13.6 dB.

By receiver site kind (solver-LOS links):

| site kind | links | strongest direct | P(el err>5 deg) | P(az err>5 deg) | P(TOA excess>10 m) |
|---|---|---|---|---|---|
| street | 7310 | 0.93 | 0.04 | 0.07 | 0.00 |
| roof | 6255 | 0.99 | 0.00 | 0.01 | 0.00 |
| facade | 6366 | 1.00 | 0.00 | 0.00 | 0.00 |

Angle of arrival as a finite aperture would report it (power-weighted beam peak; no path phases, so a non-coherent approximation):

| aperture | links | core sigma (errors < 5 deg) | P(error > 5 deg) | abs error q50 / q90 / q99 (deg) | assumed |
|---|---|---|---|---|---|
| 3 m vertical (elevation), solver-LOS | 19931 | 0.64 | 0.031 | 0.25 / 1.25 / 18.84 | 2 deg floor |
| 3 m vertical (elevation), solver-NLOS | 2831 | 2.28 | 0.248 | 2.51 / 10.86 / 34.19 | not trusted |
| 3 m horizontal (azimuth), solver-LOS | 19931 | 0.23 | 0.029 | 0.00 / 0.50 / 40.92 | 2 deg floor |
| 3 m horizontal (azimuth), solver-NLOS | 2831 | 1.85 | 0.587 | 19.95 / 73.03 / 94.71 | not trusted |
| 1 m horizontal (azimuth), solver-LOS | 19931 | 0.73 | 0.029 | 0.25 / 1.25 / 39.86 | 2 deg floor |
| 1 m horizontal (azimuth), solver-NLOS | 2831 | 2.06 | 0.558 | 20.15 / 72.43 / 95.08 | not trusted |

Sensitivity of the solver-LOS delay outlier rate to the first-arrival threshold:

| threshold (dB below strongest) | P(TOA excess > 10 m) | strongest-or-first direct |
|---|---|---|
| 10 dB | 0.00 | 1.00 |
| 20 dB | 0.00 | 1.00 |
| 30 dB | 0.00 | 1.00 |