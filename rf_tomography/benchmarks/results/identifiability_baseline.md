### Nominal (solver model == world)

| layout | emitter | top-1 | top-3 | band | p(truth) | 90% set | cov90 | CRLB σz m | verdict |
|---|---|---|---|---|---|---|---|---|---|
| street_rss_az | narrow_433M_25k | 0.04 | 0.10 | 0.46 | 0.03 | 28.6 | 0.95 | 99.1 | unidentifiable |
| street_rss_az | lora_915M_200k | 0.06 | 0.14 | 0.44 | 0.03 | 28.6 | 0.93 | 108.9 | unidentifiable |
| street_rss_az | l_1260M_1M | 0.04 | 0.12 | 0.42 | 0.03 | 28.4 | 0.89 | 104.1 | unidentifiable |
| street_rss_az | wifi_2402M_20M | 0.07 | 0.16 | 0.48 | 0.03 | 28.4 | 0.94 | 105.6 | unidentifiable |
| street_tdoa | narrow_433M_25k | 0.07 | 0.26 | 0.64 | 0.05 | 21.3 | 0.91 | 44.8 | unidentifiable |
| street_tdoa | lora_915M_200k | 0.12 | 0.29 | 0.66 | 0.06 | 19.1 | 0.91 | 39.5 | unidentifiable |
| street_tdoa | l_1260M_1M | 0.09 | 0.21 | 0.69 | 0.06 | 18.0 | 0.91 | 31.2 | unidentifiable |
| street_tdoa | wifi_2402M_20M | 0.08 | 0.21 | 0.62 | 0.06 | 18.5 | 0.89 | 30.7 | unidentifiable |
| street_vertical_array | narrow_433M_25k | 0.57 | 0.93 | 0.99 | 0.49 | 2.8 | 0.94 | 2.3 | floor_ranked_top3 |
| street_vertical_array | lora_915M_200k | 0.64 | 0.96 | 0.97 | 0.51 | 2.8 | 0.96 | 2.4 | floor_ranked_top3 |
| street_vertical_array | l_1260M_1M | 0.55 | 0.90 | 0.96 | 0.45 | 3.1 | 0.94 | 2.7 | floor_ranked_top3 |
| street_vertical_array | wifi_2402M_20M | 0.58 | 0.90 | 0.97 | 0.48 | 2.9 | 0.98 | 2.7 | floor_ranked_top3 |
| street_plus_rooftops | narrow_433M_25k | 0.81 | 1.00 | 1.00 | 0.73 | 1.7 | 0.99 | 1.4 | floor_resolved |
| street_plus_rooftops | lora_915M_200k | 0.79 | 1.00 | 0.99 | 0.73 | 1.6 | 0.99 | 1.4 | floor_resolved |
| street_plus_rooftops | l_1260M_1M | 0.82 | 1.00 | 1.00 | 0.74 | 1.6 | 0.96 | 1.4 | floor_resolved |
| street_plus_rooftops | wifi_2402M_20M | 0.74 | 0.99 | 0.99 | 0.70 | 1.6 | 0.95 | 1.4 | floor_resolved |

### Mismatch (unmodelled 3.0 m range bias, 3.0° angle bias)

| layout | emitter | top-1 | top-3 | band | p(truth) | 90% set | cov90 | CRLB σz m | verdict |
|---|---|---|---|---|---|---|---|---|---|
| street_rss_az | narrow_433M_25k | 0.06 | 0.10 | 0.41 | 0.03 | 28.9 | 0.91 | 103.3 | unidentifiable |
| street_rss_az | lora_915M_200k | 0.07 | 0.13 | 0.53 | 0.03 | 28.6 | 0.93 | 93.7 | unidentifiable |
| street_rss_az | l_1260M_1M | 0.04 | 0.12 | 0.48 | 0.03 | 28.4 | 0.87 | 103.2 | unidentifiable |
| street_rss_az | wifi_2402M_20M | 0.04 | 0.10 | 0.47 | 0.03 | 28.6 | 0.91 | 107.0 | unidentifiable |
| street_tdoa | narrow_433M_25k | 0.06 | 0.21 | 0.62 | 0.05 | 21.2 | 0.86 | 53.9 | unidentifiable |
| street_tdoa | lora_915M_200k | 0.04 | 0.21 | 0.57 | 0.06 | 19.3 | 0.84 | 36.5 | unidentifiable |
| street_tdoa | l_1260M_1M | 0.07 | 0.19 | 0.59 | 0.06 | 18.2 | 0.88 | 38.5 | unidentifiable |
| street_tdoa | wifi_2402M_20M | 0.06 | 0.17 | 0.61 | 0.05 | 17.9 | 0.82 | 34.0 | unidentifiable |
| street_vertical_array | narrow_433M_25k | 0.42 | 0.81 | 0.94 | 0.36 | 3.0 | 0.76 | 2.6 | band_only |
| street_vertical_array | lora_915M_200k | 0.36 | 0.77 | 0.95 | 0.33 | 3.1 | 0.78 | 2.6 | band_only |
| street_vertical_array | l_1260M_1M | 0.36 | 0.76 | 0.94 | 0.36 | 2.9 | 0.77 | 2.5 | band_only |
| street_vertical_array | wifi_2402M_20M | 0.41 | 0.76 | 0.94 | 0.37 | 2.9 | 0.73 | 2.5 | band_only |
| street_plus_rooftops | narrow_433M_25k | 0.61 | 0.98 | 0.97 | 0.59 | 1.7 | 0.86 | 1.4 | floor_ranked_top3 |
| street_plus_rooftops | lora_915M_200k | 0.52 | 0.97 | 0.98 | 0.53 | 1.7 | 0.82 | 1.4 | floor_ranked_top3 |
| street_plus_rooftops | l_1260M_1M | 0.57 | 0.99 | 0.97 | 0.56 | 1.7 | 0.82 | 1.4 | floor_ranked_top3 |
| street_plus_rooftops | wifi_2402M_20M | 0.64 | 0.96 | 0.96 | 0.58 | 1.7 | 0.84 | 1.4 | floor_ranked_top3 |
