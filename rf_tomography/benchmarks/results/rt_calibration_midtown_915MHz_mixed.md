### Ray-traced observables vs the assumed error model (915 MHz, mixed facades, scattering 0.0, 480 emitters x 54 sites, 18781 links, first-arrival threshold 20 dB)

| quantity | solver-LOS links | solver-NLOS links | assumed in the Gaussian/impairment experiments |
|---|---|---|---|
| links | 7420 | 11361 | |
| strongest path is direct-like | 0.76 | 0.25 | LOS: ~1 minus outliers (10-25%); NLOS: AoA/TOA not trusted |
| first arrival is direct-like | 0.98 | 0.60 | |
| elevation error q50 / q90 / q99 (deg) | 0.00 / 4.65 / 120.31 | 2.0 / 16.2 / 95.1 | Gaussian 2 deg floor: 1.35 / 3.3 / 5.2 |
| azimuth error q50 / q90 / q99 (deg) | 0.00 / 10.57 / 165.73 | 9.2 / 111.6 / 170.0 | same |
| P(elevation error > 5 deg) | 0.09 | 0.32 | Gaussian 0.01; with outliers 0.10-0.25 |
| P(azimuth error > 5 deg) | 0.13 | 0.58 | same |
| TOA excess q50 / q90 / q99 (m) | -0.0 / 0.0 / 42.2 | 0.0 / 103.6 / 347.2 | 3 m Gaussian floor (+3 m sync); outliers exp. mean 15-25 m |
| P(TOA excess > 10 m) | 0.02 | 0.33 | Gaussian ~0.02; with outliers 0.10-0.25 |
| path-loss exponent n | 2.62 | 3.59 | 2.7 |
| RSS residual sigma (dB) | 10.10 | 19.90 | 6 (LOS) / 8 (NLOS) |
| strongest path: direct / one bounce / diffraction / multi | 0.76 / 0.09 / 0.00 / 0.15 | 0.25 / 0.11 / 0.01 / 0.64 | |

Mean power offset between solver-LOS and solver-NLOS fits: -10.1 dB.

By receiver site kind (solver-LOS links):

| site kind | links | strongest direct | P(el err>5 deg) | P(az err>5 deg) | P(TOA excess>10 m) |
|---|---|---|---|---|---|
| street | 3080 | 0.78 | 0.11 | 0.10 | 0.00 |
| roof | 4340 | 0.74 | 0.08 | 0.15 | 0.03 |

Angle of arrival as a finite aperture would report it (power-weighted beam peak; no path phases, so a non-coherent approximation):

| aperture | links | core sigma (errors < 5 deg) | P(error > 5 deg) | abs error q50 / q90 / q99 (deg) | assumed |
|---|---|---|---|---|---|
| 3 m vertical (elevation), solver-LOS | 7420 | 1.18 | 0.119 | 0.50 / 6.62 / 120.31 | 2 deg floor |
| 3 m vertical (elevation), solver-NLOS | 11361 | 1.94 | 0.317 | 2.06 / 16.61 / 95.03 | not trusted |
| 3 m horizontal (azimuth), solver-LOS | 7420 | 0.79 | 0.146 | 0.25 / 16.02 / 168.84 | 2 deg floor |
| 3 m horizontal (azimuth), solver-NLOS | 11361 | 1.53 | 0.580 | 9.57 / 111.38 / 170.10 | not trusted |
| 1 m horizontal (azimuth), solver-LOS | 7420 | 1.21 | 0.141 | 0.50 / 17.27 / 173.58 | 2 deg floor |
| 1 m horizontal (azimuth), solver-NLOS | 11361 | 1.76 | 0.571 | 9.07 / 111.38 / 170.95 | not trusted |

Sensitivity of the solver-LOS delay outlier rate to the first-arrival threshold:

| threshold (dB below strongest) | P(TOA excess > 10 m) | strongest-or-first direct |
|---|---|---|
| 10 dB | 0.04 | 0.95 |
| 20 dB | 0.02 | 0.98 |
| 30 dB | 0.01 | 0.98 |