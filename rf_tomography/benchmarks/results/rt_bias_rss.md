### Which measurement drives the floor bias? (analytic@20, robust, beam-peak or exact bearings)

Bias = mean(posterior-mean floor - true floor) in floors; position groups are thirds of the building.

| table | bearings+range | RSS | all | low third | mid third | high third | MAE | top-1 | cov90 | set |
|---|---|---|---|---|---|---|---|---|---|---|
| mixed | traced (beam peak, first arrival) | traced power | +0.41 | +3.70 | -1.11 | -0.48 | 2.28 | 0.47 | 0.95 | 8.7 |
| mixed | traced (beam peak, first arrival) | none (dropped) | +0.31 | +2.39 | -0.29 | -0.38 | 1.57 | 0.50 | 0.97 | 7.8 |
| mixed | traced (beam peak, first arrival) | solver's own model, exact range | +0.81 | +3.80 | -0.11 | -0.32 | 1.86 | 0.50 | 0.97 | 8.4 |
| mixed | exact | traced power | +0.81 | +3.89 | -0.42 | -0.16 | 1.66 | 0.57 | 0.98 | 7.7 |
| mixed | exact | none (dropped) | +0.41 | +1.98 | -0.07 | -0.07 | 1.01 | 0.60 | 1.00 | 6.6 |
| mixed | exact | solver's own model, exact range | +0.86 | +3.51 | -0.05 | -0.07 | 1.43 | 0.61 | 1.00 | 7.4 |
| concrete | traced (beam peak, first arrival) | traced power | +0.92 | +5.14 | -0.64 | -0.49 | 2.45 | 0.53 | 0.97 | 8.4 |
| concrete | traced (beam peak, first arrival) | none (dropped) | +0.29 | +2.05 | +0.02 | -0.46 | 1.35 | 0.54 | 1.00 | 7.2 |
| concrete | traced (beam peak, first arrival) | solver's own model, exact range | +0.72 | +3.50 | +0.03 | -0.45 | 1.73 | 0.55 | 1.00 | 8.0 |
| concrete | exact | traced power | +1.09 | +5.20 | -0.68 | -0.11 | 2.15 | 0.56 | 0.97 | 7.9 |
| concrete | exact | none (dropped) | +0.42 | +1.98 | -0.06 | -0.08 | 1.02 | 0.60 | 1.00 | 6.6 |
| concrete | exact | solver's own model, exact range | +0.87 | +3.52 | -0.04 | -0.08 | 1.44 | 0.60 | 1.00 | 7.4 |
| glass_solid | traced (beam peak, first arrival) | traced power | +0.68 | +3.75 | -0.34 | -0.46 | 1.86 | 0.47 | 0.96 | 8.1 |
| glass_solid | traced (beam peak, first arrival) | none (dropped) | +0.42 | +2.24 | +0.06 | -0.28 | 1.30 | 0.50 | 0.98 | 6.9 |
| glass_solid | traced (beam peak, first arrival) | solver's own model, exact range | +0.95 | +3.98 | +0.08 | -0.28 | 1.77 | 0.50 | 0.98 | 7.9 |
| glass_solid | exact | traced power | +0.73 | +3.61 | -0.42 | -0.20 | 1.60 | 0.57 | 0.98 | 7.8 |
| glass_solid | exact | none (dropped) | +0.41 | +1.98 | -0.07 | -0.07 | 1.01 | 0.60 | 1.00 | 6.6 |
| glass_solid | exact | solver's own model, exact range | +0.96 | +3.83 | -0.05 | -0.07 | 1.52 | 0.60 | 1.00 | 7.6 |