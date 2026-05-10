"""
generate_dataset.py — Korean Nationwide Multimodal Transit Graph (KNMTG)
=========================================================================
Generates subway_data.json from seoul_subway_data.py.
Run from the project root:

    python3 generate_dataset.py

Output: subway_data.json (used by the FastAPI server at startup)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from seoul_subway_data import (
    COORDS, REGIONAL_COORDS, INTERCITY_BUS_TERMINALS, BRT_HUBS,
    LINES, LINE_SEGMENTS, SEGMENT_LINE,
    KTX_TRANSFERS, KTX_SEGMENT_TIMES, KTX_EXCLUDE_FROM_CANDIDATES,
    MUGUNGHWA_SEGMENTS, MUGUNGHWA_DIRECT_TIMES,
    SAEMAEUL_SEGMENTS, SAEMAEUL_DIRECT_TIMES,
    ITX_MAEUM_DIRECT_TIMES,
    REGIONAL_SEGMENTS, REGIONAL_LINE_CODES, REGIONAL_LINE_SPEED,
    REGIONAL_TRANSFERS,
    INTERCITY_BUS_TIMES, INTERCITY_BUS_STATION_LINKS,
    BRT_HUB_LINKS, BRT_ROUTE_TIMES, BRT_ROUTE_NAMES, BRT_BOARD_PENALTY,
    GTX_A_SEGMENT_TIMES, GTX_A_TRANSFERS,
    HOMONYM_DISPLAY,
    NODE_TYPES, HEADWAY_MINUTES, TRANSFER_PENALTY, SERVICE_HOURS,
    DELAY_SIGMA, CO2_G_PER_PKM,
    dataset_statistics,
)


def tkey(d):
    """dict[(a,b)->v]  →  {"a|b": v}"""
    return {f"{k[0]}|{k[1]}": v for k, v in d.items()}


def tlist(d):
    """dict[(a,b)->v]  →  [[a, b, v], ...]"""
    return [[k[0], k[1], v] for k, v in d.items()]


def build_brt_names_json():
    out = {}
    for (a, b), name in BRT_ROUTE_NAMES.items():
        out[f"{a}|{b}"] = name
        out[f"{b}|{a}"] = name
    return out


def main():
    # GTX-A 노드를 COORDS에 포함 (수도권 신규 역)
    from seoul_subway_data import COORDS as _COORDS
    GTX_EXTRA = {
        "운정중앙_GTX": (37.7154, 126.7539),
        "킨텍스_GTX":   (37.6713, 126.7734),
        "성남_GTX":     (37.4477, 127.1271),
        "용인_GTX":     (37.2810, 127.1950),
    }
    ALL_COORDS = {**COORDS, **REGIONAL_COORDS, **INTERCITY_BUS_TERMINALS,
                  **BRT_HUBS, **GTX_EXTRA}

    node_types_json = {k: NODE_TYPES.get(k, "subway") for k in ALL_COORDS}
    # GTX-A 노드는 ktx 타입으로 분류
    for k in GTX_EXTRA:
        node_types_json[k] = "ktx"

    data = {
        # ── Core graph ─────────────────────────────────────────────────────
        "COORDS":                 {k: list(v) for k, v in ALL_COORDS.items()},
        "LINES":                  LINES,
        "NODE_TYPES":             node_types_json,
        "LINE_SEGMENTS":          LINE_SEGMENTS,
        "SEGMENT_LINE":           SEGMENT_LINE,
        "KTX_TRANSFERS":          [[a, b, t] for a, b, t in KTX_TRANSFERS],
        "KTX_EXCLUDE":            list(KTX_EXCLUDE_FROM_CANDIDATES),
        "KTX_SEGMENT_TIMES":      tkey(KTX_SEGMENT_TIMES),
        "MUGUNGHWA_SEGMENTS":     MUGUNGHWA_SEGMENTS,
        "MUGUNGHWA_DIRECT_TIMES": tkey(MUGUNGHWA_DIRECT_TIMES),
        "SAEMAEUL_SEGMENTS":      SAEMAEUL_SEGMENTS,
        "SAEMAEUL_DIRECT_TIMES":  tkey(SAEMAEUL_DIRECT_TIMES),
        "ITX_MAEUM_DIRECT_TIMES": tkey(ITX_MAEUM_DIRECT_TIMES),
        "REGIONAL_SEGMENTS":      REGIONAL_SEGMENTS,
        "REGIONAL_LINE_CODES":    REGIONAL_LINE_CODES,
        "REGIONAL_LINE_SPEED":    REGIONAL_LINE_SPEED,
        "REGIONAL_TRANSFERS":     [[a, b, t] for a, b, t in REGIONAL_TRANSFERS],
        # ── GTX-A ──────────────────────────────────────────────────────────
        "GTX_A_SEGMENT_TIMES":    tkey(GTX_A_SEGMENT_TIMES),
        "GTX_A_TRANSFERS":        [[a, b, t] for a, b, t in GTX_A_TRANSFERS],
        # ── Bus ────────────────────────────────────────────────────────────
        "INTERCITY_BUS_TIMES":    tlist(INTERCITY_BUS_TIMES),
        "INTERCITY_BUS_STATION_LINKS": [
            [a, b, t] for a, b, t in INTERCITY_BUS_STATION_LINKS
        ],
        "BRT_HUB_LINKS":          [[a, b, t] for a, b, t in BRT_HUB_LINKS],
        "BRT_BOARD_PENALTY":      BRT_BOARD_PENALTY,
        "BRT_ROUTE_TIMES":        tlist(BRT_ROUTE_TIMES),
        "BRT_ROUTE_NAMES":        build_brt_names_json(),
        # ── Display helpers ────────────────────────────────────────────────
        "HOMONYM_DISPLAY":        HOMONYM_DISPLAY,
        # ── Time-dependent parameters (Option B) ───────────────────────────
        "HEADWAY_MINUTES":        HEADWAY_MINUTES,
        "TRANSFER_PENALTY":       TRANSFER_PENALTY,
        "SERVICE_HOURS":          {k: list(v) for k, v in SERVICE_HOURS.items()},
        # ── Uncertainty model (σ per mode) ─────────────────────────────────
        "DELAY_SIGMA":            DELAY_SIGMA,
        # ── CO₂ emission factors ────────────────────────────────────────────
        "CO2_G_PER_PKM":          CO2_G_PER_PKM,
        # ── Dataset metadata ───────────────────────────────────────────────
        "DATASET_VERSION":        "1.1",
        "DATASET_STATS":          dataset_statistics(),
    }

    out_path = Path(__file__).parent / "subway_data.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    stats = data["DATASET_STATS"]
    print(f"subway_data.json generated  ({out_path.stat().st_size // 1024} KB)")
    print(f"  Nodes : {stats['nodes']['total']}  "
          f"(subway={stats['nodes'].get('subway',0)}, "
          f"ktx={stats['nodes'].get('ktx',0)}, "
          f"regional_rail={stats['nodes'].get('regional_rail',0)}, "
          f"bus_terminal={stats['nodes'].get('bus_terminal',0)}, "
          f"brt_hub={stats['nodes'].get('brt_hub',0)})")
    print(f"  Edges : {stats['edges']['total']}  (undirected unique pairs)")


if __name__ == "__main__":
    main()
