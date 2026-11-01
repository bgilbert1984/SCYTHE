### Ray-traced observables vs the assumed error model (915 MHz, concrete facades, scattering 0.3, 400 emitters x 57 sites, 22760 links, first-arrival threshold 20 dB)

| quantity | solver-LOS links | solver-NLOS links | assumed in the Gaussian/impairment experiments |
|---|---|---|---|
| links | 19931 | 2829 | |
| strongest path is direct-like | 0.98 | 0.23 | LOS: ~1 minus outliers (10-25%); NLOS: AoA/TOA not trusted |
| first arrival is direct-like | 1.00 | 0.73 | |
| elevation error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 2.97 | 2.4 / 10.0 / 37.4 | Gaussian 2 deg floor: 1.35 / 3.3 / 5.2 |
| azimuth error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 26.93 | 38.0 / 73.9 / 96.4 | same |
| P(elevation error > 5 deg) | 0.01 | 0.26 | Gaussian 0.01; with outliers 0.10-0.25 |
| P(azimuth error > 5 deg) | 0.02 | 0.66 | same |
| TOA excess q50 / q90 / q99 (m) | -0.0 / 0.0 / 0.0 | -0.0 / 11.9 / 50.3 | 3 m Gaussian floor (+3 m sync); outliers exp. mean 15-25 m |
| P(TOA excess > 10 m) | 0.00 | 0.18 | Gaussian ~0.02; with outliers 0.10-0.25 |
| path-loss exponent n | 4.58 | 5.48 | 2.7 |
| RSS residual sigma (dB) | 8.64 | 7.67 | 6 (LOS) / 8 (NLOS) |
| strongest path: direct / one bounce / diffraction / multi | 0.98 / 0.02 / 0.00 / 0.00 | 0.24 / 0.54 / 0.00 / 0.22 | |

Mean power offset between solver-LOS and solver-NLOS fits: -15.9 dB.

By receiver site kind (solver-LOS links):

| site kind | links | strongest direct | P(el err>5 deg) | P(az err>5 deg) | P(TOA excess>10 m) |
|---|---|---|---|---|---|
| street | 7310 | 0.98 | 0.00 | 0.02 | 0.00 |
| roof | 6255 | 0.95 | 0.03 | 0.03 | 0.00 |
| facade | 6366 | 1.00 | 0.00 | 0.00 | 0.00 |

Angle of arrival as a finite aperture would report it (power-weighted beam peak; no path phases, so a non-coherent approximation):

| aperture | links | core sigma (errors < 5 deg) | P(error > 5 deg) | abs error q50 / q90 / q99 (deg) | assumed |
|---|---|---|---|---|---|
| 3 m vertical (elevation), solver-LOS | 19931 | 0.36 | 0.019 | 0.00 / 0.50 / 14.39 | 2 deg floor |
| 3 m vertical (elevation), solver-NLOS | 2829 | 2.27 | 0.286 | 2.68 / 10.80 / 36.64 | not trusted |
| 3 m horizontal (azimuth), solver-LOS | 19931 | 0.20 | 0.019 | 0.00 / 0.25 / 26.68 | 2 deg floor |
| 3 m horizontal (azimuth), solver-NLOS | 2829 | 1.99 | 0.663 | 37.81 / 73.94 / 96.36 | not trusted |
| 1 m horizontal (azimuth), solver-LOS | 19931 | 0.50 | 0.019 | 0.00 / 0.50 / 26.78 | 2 deg floor |
| 1 m horizontal (azimuth), solver-NLOS | 2829 | 2.05 | 0.667 | 37.39 / 73.73 / 96.36 | not trusted |

Sensitivity of the solver-LOS delay outlier rate to the first-arrival threshold:

| threshold (dB below strongest) | P(TOA excess > 10 m) | strongest-or-first direct |
|---|---|---|
| 10 dB | 0.01 | 0.99 |
| 20 dB | 0.00 | 1.00 |
| 30 dB | 0.00 | 1.00 |