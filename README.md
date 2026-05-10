# KNMTG: Korean Nationwide Multimodal Transit Graph v1.1

A unified, time-dependent, open-source transit graph for South Korea integrating five public transportation modes.

## Overview

| Property | Value |
|---|---|
| Nodes | 932 |
| Directed edges | 2,916 |
| Modes | Subway, KTX/SRT/GTX-A, Regional Rail, Intercity Bus, BRT |
| Coverage | Nationwide (6 metro systems + KTX + intercity) |
| License | CC-BY 4.0 (data) / MIT (code) |
| Paper | *under review — IJGIS 2026* |

## Node composition

| Type | Count | % |
|---|---|---|
| Urban metro (subway) | 586 | 62.9% |
| KTX / SRT / GTX-A terminals | 47 | 5.0% |
| Regional rail (Mugunghwa/Saemaeul/ITX) | 254 | 27.3% |
| Intercity bus terminals | 42 | 4.5% |
| BRT hubs | 3 | 0.3% |
| **Total** | **932** | |

## Dataset files

| File | Format | Description |
|---|---|---|
| `knmtg_v1.1.json` | JSON | Full graph (nodes + edges + metadata) |
| `knmtg_nodes.csv` | CSV | Node list with type, latitude, longitude |
| `knmtg_edges.csv` | CSV | Edge list with mode label and travel time (minutes) |
| `seoul_subway_data.py` | Python | Source data and constants |
| `meeting_finder_v3.py` | Python | Dijkstra router + midpoint finder |

## Quick start

```python
from meeting_finder_v3 import build_graph, shortest_time, find_meeting_points

g = build_graph()  # 932 nodes

# Single-source shortest time from a station
dist, transfers = shortest_time(g, '강남')
print(dist['서울_KTX'])  # ~31 min

# Symmetric midpoint finder
results = find_meeting_points(g, ['강남', '부산역'], strategy='balanced')
print(results[0])  # best meeting point
```

## Validation (MAE = 3.8 min over 12 O-D pairs)

| Route | Mode | KNMTG (min) | Reference (min) | Error |
|---|---|---|---|---|
| Seoul KTX → Busan KTX | KTX | 141 | ~150 | −9 |
| Seoul KTX → Daejeon KTX | KTX | 56 | ~48 | +8 |
| Seoul KTX → Gwangju-Songjeong KTX | KTX | 110 | ~88–110 | ±0 |
| Seoul KTX → Dongdaegu KTX | KTX | 101 | ~95 | +6 |
| Suwon → Seoul Station | Saemaul | 22 | ~22 | 0 |
| Banjuk → Daejeon (metro) | Metro | 42 | ~41 | +1 |
| Gangnam → Hongik Univ. | Metro | 39 | ~30–40 | ±0 |
| Jamsil → Gangnam | Metro | 14 | ~7–14 | ±0 |
| Daejeon KTX → Daejeon (metro) | Transfer | 10 | ~10 | 0 |
| Daejeon Bus Terminal → Daejeon (metro) | Bus link | 15 | ~15 | 0 |
| Osong Bus Hub → Jochiwon | City bus | 5 | ~5 | 0 |
| Dongtan BRT → Suwon | BRT | 25 | ~30 | −5 |

## Web demo

Live midpoint finder: **https://midpoint-kr.fly.dev**

## Citation

```bibtex
@article{kim2026knmtg,
  author  = {Kim, Yongjun},
  title   = {{KNMTG}: A Korean Nationwide Multimodal Transit Graph
             for Time-Dependent Midpoint Search},
  journal = {International Journal of Geographical Information Science},
  year    = {2026},
  note    = {Under review}
}
```

## License

- Dataset files (`knmtg_*.csv`, `knmtg_*.json`): **CC-BY 4.0**
- Code (`*.py`): **MIT**
