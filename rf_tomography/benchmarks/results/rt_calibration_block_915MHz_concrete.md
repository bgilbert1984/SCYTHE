### Ray-traced observables vs the assumed error model (915 MHz, concrete facades, scattering 0.0, 400 emitters x 57 sites, 22688 links, first-arrival threshold 20 dB)

| quantity | solver-LOS links | solver-NLOS links | assumed in the Gaussian/impairment experiments |
|---|---|---|---|
| links | 19923 | 2765 | |
| strongest path is direct-like | 0.98 | 0.24 | LOS: ~1 minus outliers (10-25%); NLOS: AoA/TOA not trusted |
| first arrival is direct-like | 1.00 | 0.73 | |
| elevation error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 3.94 | 2.3 / 9.1 / 15.2 | Gaussian 2 deg floor: 1.35 / 3.3 / 5.2 |
| azimuth error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 27.38 | 37.5 / 74.0 / 96.2 | same |
| P(elevation error > 5 deg) | 0.01 | 0.25 | Gaussian 0.01; with outliers 0.10-0.25 |
| P(azimuth error > 5 deg) | 0.02 | 0.65 | same |
| TOA excess q50 / q90 / q99 (m) | -0.0 / 0.0 / 0.0 | -0.0 / 12.0 / 47.2 | 3 m Gaussian floor (+3 m sync); outliers exp. mean 15-25 m |
| P(TOA excess > 10 m) | 0.00 | 0.20 | Gaussian ~0.02; with outliers 0.10-0.25 |
| path-loss exponent n | 4.60 | 6.26 | 2.7 |
| RSS residual sigma (dB) | 8.74 | 7.41 | 6 (LOS) / 8 (NLOS) |
| strongest path: direct / one bounce / diffraction / multi | 0.98 / 0.02 / 0.00 / 0.00 | 0.24 / 0.56 / 0.00 / 0.21 | |

Mean power offset between solver-LOS and solver-NLOS fits: -32.9 dB.

By receiver site kind (solver-LOS links):

| site kind | links | strongest direct | P(el err>5 deg) | P(az err>5 deg) | P(TOA excess>10 m) |
|---|---|---|---|---|---|
| street | 7310 | 0.98 | 0.00 | 0.02 | 0.00 |
| roof | 6247 | 0.95 | 0.03 | 0.04 | 0.01 |
| facade | 6366 | 1.00 | 0.00 | 0.00 | 0.00 |

Angle of arrival as a finite aperture would report it (power-weighted beam peak; no path phases, so a non-coherent approximation):

| aperture | links | core sigma (errors < 5 deg) | P(error > 5 deg) | abs error q50 / q90 / q99 (deg) | assumed |
|---|---|---|---|---|---|
| 3 m vertical (elevation), solver-LOS | 19923 | 0.38 | 0.019 | 0.00 / 0.50 / 14.57 | 2 deg floor |
| 3 m vertical (elevation), solver-NLOS | 2765 | 2.28 | 0.270 | 2.62 / 9.83 / 16.22 | not trusted |
| 3 m horizontal (azimuth), solver-LOS | 19923 | 0.21 | 0.020 | 0.00 / 0.25 / 27.38 | 2 deg floor |
| 3 m horizontal (azimuth), solver-NLOS | 2765 | 2.02 | 0.652 | 37.44 / 74.12 / 96.21 | not trusted |
| 1 m horizontal (azimuth), solver-LOS | 19923 | 0.54 | 0.019 | 0.00 / 0.55 / 27.32 | 2 deg floor |
| 1 m horizontal (azimuth), solver-NLOS | 2765 | 2.01 | 0.648 | 37.40 / 73.86 / 96.50 | not trusted |

Sensitivity of the solver-LOS delay outlier rate to the first-arrival threshold:

| threshold (dB below strongest) | P(TOA excess > 10 m) | strongest-or-first direct |
|---|---|---|
| 10 dB | 0.01 | 0.99 |
| 20 dB | 0.00 | 1.00 |
| 30 dB | 0.00 | 1.00 |