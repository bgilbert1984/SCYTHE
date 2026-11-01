### Ray-traced observables vs the assumed error model (915 MHz, concrete facades, scattering 0.0, 480 emitters x 54 sites, 18444 links, first-arrival threshold 20 dB)

| quantity | solver-LOS links | solver-NLOS links | assumed in the Gaussian/impairment experiments |
|---|---|---|---|
| links | 7412 | 11032 | |
| strongest path is direct-like | 0.92 | 0.28 | LOS: ~1 minus outliers (10-25%); NLOS: AoA/TOA not trusted |
| first arrival is direct-like | 0.97 | 0.59 | |
| elevation error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 7.96 | 1.6 / 12.7 / 49.1 | Gaussian 2 deg floor: 1.35 / 3.3 / 5.2 |
| azimuth error q50 / q90 / q99 (deg) | 0.00 / 0.00 / 67.73 | 10.2 / 108.9 / 168.1 | same |
| P(elevation error > 5 deg) | 0.02 | 0.28 | Gaussian 0.01; with outliers 0.10-0.25 |
| P(azimuth error > 5 deg) | 0.05 | 0.59 | same |
| TOA excess q50 / q90 / q99 (m) | -0.0 / 0.0 / 43.7 | 0.0 / 108.7 / 348.9 | 3 m Gaussian floor (+3 m sync); outliers exp. mean 15-25 m |
| P(TOA excess > 10 m) | 0.02 | 0.34 | Gaussian ~0.02; with outliers 0.10-0.25 |
| path-loss exponent n | 2.98 | 3.80 | 2.7 |
| RSS residual sigma (dB) | 11.72 | 19.31 | 6 (LOS) / 8 (NLOS) |
| strongest path: direct / one bounce / diffraction / multi | 0.92 / 0.03 / 0.00 / 0.05 | 0.28 / 0.15 / 0.01 / 0.57 | |

Mean power offset between solver-LOS and solver-NLOS fits: -3.0 dB.

By receiver site kind (solver-LOS links):

| site kind | links | strongest direct | P(el err>5 deg) | P(az err>5 deg) | P(TOA excess>10 m) |
|---|---|---|---|---|---|
| street | 3080 | 0.96 | 0.01 | 0.03 | 0.00 |
| roof | 4332 | 0.90 | 0.02 | 0.06 | 0.03 |

Angle of arrival as a finite aperture would report it (power-weighted beam peak; no path phases, so a non-coherent approximation):

| aperture | links | core sigma (errors < 5 deg) | P(error > 5 deg) | abs error q50 / q90 / q99 (deg) | assumed |
|---|---|---|---|---|---|
| 3 m vertical (elevation), solver-LOS | 7412 | 0.70 | 0.037 | 0.25 / 1.25 / 20.75 | 2 deg floor |
| 3 m vertical (elevation), solver-NLOS | 11032 | 1.91 | 0.285 | 1.73 / 13.10 / 49.06 | not trusted |
| 3 m horizontal (azimuth), solver-LOS | 7412 | 0.49 | 0.063 | 0.00 / 0.75 / 67.69 | 2 deg floor |
| 3 m horizontal (azimuth), solver-NLOS | 11032 | 1.49 | 0.592 | 10.46 / 108.87 / 167.89 | not trusted |
| 1 m horizontal (azimuth), solver-LOS | 7412 | 0.81 | 0.065 | 0.25 / 2.25 / 67.81 | 2 deg floor |
| 1 m horizontal (azimuth), solver-NLOS | 11032 | 1.67 | 0.587 | 10.11 / 108.92 / 168.64 | not trusted |

Sensitivity of the solver-LOS delay outlier rate to the first-arrival threshold:

| threshold (dB below strongest) | P(TOA excess > 10 m) | strongest-or-first direct |
|---|---|---|
| 10 dB | 0.03 | 0.96 |
| 20 dB | 0.02 | 0.97 |
| 30 dB | 0.02 | 0.98 |