### Ray-traced observables vs the assumed error model (915 MHz, glass facades, scattering 0.0, 400 emitters x 57 sites, 22694 links, first-arrival threshold 20 dB)

| quantity | solver-LOS links | solver-NLOS links | assumed in the Gaussian/impairment experiments |
|---|---|---|---|
| links | 19929 | 2765 | |
| strongest path is direct-like | 0.97 | 0.29 | LOS: ~1 minus outliers (10-25%); NLOS: AoA/TOA not trusted |
| first arrival is direct-like | 1.00 | 1.00 | |
| elevation error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 13.37 | 1.9 / 7.8 / 14.8 | Gaussian 2 deg floor: 1.35 / 3.3 / 5.2 |
| azimuth error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 43.56 | 19.6 / 73.2 / 94.4 | same |
| P(elevation error > 5 deg) | 0.02 | 0.21 | Gaussian 0.01; with outliers 0.10-0.25 |
| P(azimuth error > 5 deg) | 0.03 | 0.59 | same |
| TOA excess q50 / q90 / q99 (m) | -0.0 / 0.0 / 0.0 | -0.0 / 0.0 / 0.0 | 3 m Gaussian floor (+3 m sync); outliers exp. mean 15-25 m |
| P(TOA excess > 10 m) | 0.00 | 0.00 | Gaussian ~0.02; with outliers 0.10-0.25 |
| path-loss exponent n | 2.87 | 3.96 | 2.7 |
| RSS residual sigma (dB) | 4.06 | 4.75 | 6 (LOS) / 8 (NLOS) |
| strongest path: direct / one bounce / diffraction / multi | 0.97 / 0.03 / 0.00 / 0.01 | 0.29 / 0.53 / 0.00 / 0.18 | |

Mean power offset between solver-LOS and solver-NLOS fits: -20.3 dB.

By receiver site kind (solver-LOS links):

| site kind | links | strongest direct | P(el err>5 deg) | P(az err>5 deg) | P(TOA excess>10 m) |
|---|---|---|---|---|---|
| street | 7310 | 0.92 | 0.05 | 0.08 | 0.00 |
| roof | 6253 | 0.99 | 0.00 | 0.01 | 0.00 |
| facade | 6366 | 1.00 | 0.00 | 0.00 | 0.00 |

Angle of arrival as a finite aperture would report it (power-weighted beam peak; no path phases, so a non-coherent approximation):

| aperture | links | core sigma (errors < 5 deg) | P(error > 5 deg) | abs error q50 / q90 / q99 (deg) | assumed |
|---|---|---|---|---|---|
| 3 m vertical (elevation), solver-LOS | 19929 | 0.69 | 0.035 | 0.25 / 1.25 / 19.50 | 2 deg floor |
| 3 m vertical (elevation), solver-NLOS | 2765 | 2.32 | 0.238 | 2.53 / 9.69 / 17.20 | not trusted |
| 3 m horizontal (azimuth), solver-LOS | 19929 | 0.26 | 0.032 | 0.00 / 0.50 / 43.70 | 2 deg floor |
| 3 m horizontal (azimuth), solver-NLOS | 2765 | 1.88 | 0.587 | 19.66 / 73.20 / 94.63 | not trusted |
| 1 m horizontal (azimuth), solver-LOS | 19929 | 0.78 | 0.033 | 0.25 / 1.50 / 42.39 | 2 deg floor |
| 1 m horizontal (azimuth), solver-NLOS | 2765 | 1.99 | 0.569 | 19.70 / 73.00 / 95.17 | not trusted |

Sensitivity of the solver-LOS delay outlier rate to the first-arrival threshold:

| threshold (dB below strongest) | P(TOA excess > 10 m) | strongest-or-first direct |
|---|---|---|
| 10 dB | 0.00 | 1.00 |
| 20 dB | 0.00 | 1.00 |
| 30 dB | 0.00 | 1.00 |