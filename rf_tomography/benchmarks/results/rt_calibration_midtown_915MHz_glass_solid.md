### Ray-traced observables vs the assumed error model (915 MHz, glass_solid facades, scattering 0.0, 480 emitters x 54 sites, 18931 links, first-arrival threshold 20 dB)

| quantity | solver-LOS links | solver-NLOS links | assumed in the Gaussian/impairment experiments |
|---|---|---|---|
| links | 7421 | 11510 | |
| strongest path is direct-like | 0.87 | 0.33 | LOS: ~1 minus outliers (10-25%); NLOS: AoA/TOA not trusted |
| first arrival is direct-like | 0.99 | 0.70 | |
| elevation error q50 / q90 / q99 (deg) | 0.00 / 0.71 / 15.76 | 1.1 / 10.9 / 48.9 | Gaussian 2 deg floor: 1.35 / 3.3 / 5.2 |
| azimuth error q50 / q90 / q99 (deg) | 0.00 / 3.38 / 133.85 | 6.5 / 93.6 / 166.1 | same |
| P(elevation error > 5 deg) | 0.03 | 0.24 | Gaussian 0.01; with outliers 0.10-0.25 |
| P(azimuth error > 5 deg) | 0.09 | 0.53 | same |
| TOA excess q50 / q90 / q99 (m) | -0.0 / 0.0 / 4.1 | -0.0 / 83.7 / 308.2 | 3 m Gaussian floor (+3 m sync); outliers exp. mean 15-25 m |
| P(TOA excess > 10 m) | 0.01 | 0.25 | Gaussian ~0.02; with outliers 0.10-0.25 |
| path-loss exponent n | 2.39 | 3.50 | 2.7 |
| RSS residual sigma (dB) | 8.33 | 18.05 | 6 (LOS) / 8 (NLOS) |
| strongest path: direct / one bounce / diffraction / multi | 0.87 / 0.04 / 0.00 / 0.09 | 0.33 / 0.11 / 0.01 / 0.56 | |

Mean power offset between solver-LOS and solver-NLOS fits: -19.6 dB.

By receiver site kind (solver-LOS links):

| site kind | links | strongest direct | P(el err>5 deg) | P(az err>5 deg) | P(TOA excess>10 m) |
|---|---|---|---|---|---|
| street | 3080 | 0.91 | 0.03 | 0.06 | 0.00 |
| roof | 4341 | 0.84 | 0.03 | 0.11 | 0.02 |

Angle of arrival as a finite aperture would report it (power-weighted beam peak; no path phases, so a non-coherent approximation):

| aperture | links | core sigma (errors < 5 deg) | P(error > 5 deg) | abs error q50 / q90 / q99 (deg) | assumed |
|---|---|---|---|---|---|
| 3 m vertical (elevation), solver-LOS | 7421 | 1.21 | 0.068 | 0.75 / 3.02 / 21.75 | 2 deg floor |
| 3 m vertical (elevation), solver-NLOS | 11510 | 1.88 | 0.264 | 1.58 / 12.03 / 48.94 | not trusted |
| 3 m horizontal (azimuth), solver-LOS | 7421 | 0.73 | 0.123 | 0.25 / 10.50 / 133.90 | 2 deg floor |
| 3 m horizontal (azimuth), solver-NLOS | 11510 | 1.42 | 0.539 | 7.36 / 93.51 / 166.01 | not trusted |
| 1 m horizontal (azimuth), solver-LOS | 7421 | 1.26 | 0.123 | 0.75 / 9.50 / 134.10 | 2 deg floor |
| 1 m horizontal (azimuth), solver-NLOS | 11510 | 1.76 | 0.528 | 6.29 / 93.22 / 167.08 | not trusted |

Sensitivity of the solver-LOS delay outlier rate to the first-arrival threshold:

| threshold (dB below strongest) | P(TOA excess > 10 m) | strongest-or-first direct |
|---|---|---|
| 10 dB | 0.03 | 0.96 |
| 20 dB | 0.01 | 0.99 |
| 30 dB | 0.01 | 0.99 |