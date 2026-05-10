"""
Meeting Point Finder v3 — 주소 입력 지원
==========================================

v2 대비 변경점:
- Origin이 "역 이름"이 아닌 "좌표 + 가까운 역들" 묶음이 됨
- 출발 도보시간이 결과에 반영됨
- 한 사람당 여러 출발역 후보 중 골라서 계산할 수 있음
  (자동: 가장 가까운 역, 수동: 사용자가 선택)

사용 흐름:
    # 주소를 사용할 때
    origin1 = build_origin_from_address("강남구 역삼동", api_key=...)

    # 역 이름을 직접 쓸 때 (V0 폴백)
    origin2 = build_origin_from_station("잠실")

    results = find_meeting_points([origin1, origin2])
"""

from __future__ import annotations
import heapq
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from seoul_subway_data import (
    LINES, COORDS, LINE_SEGMENTS, SEGMENT_LINE, HOMONYM_DISPLAY,
    KTX_TRANSFERS, KTX_SEGMENT_TIMES, KTX_EXCLUDE_FROM_CANDIDATES,
    MUGUNGHWA_SEGMENTS, MUGUNGHWA_SPEED_KMH, MUGUNGHWA_MIN_INTER_TIME,
    MUGUNGHWA_BOARD_PENALTY,
    SAEMAEUL_SEGMENTS, SAEMAEUL_SPEED_KMH, SAEMAEUL_MIN_INTER_TIME,
    SAEMAEUL_BOARD_PENALTY,
    REGIONAL_SEGMENTS, REGIONAL_LINE_CODES, REGIONAL_LINE_SPEED, REGIONAL_COORDS,
    REGIONAL_TRANSFERS,
    INTERCITY_BUS_TERMINALS, INTERCITY_BUS_TIMES, INTERCITY_BUS_BOARD_PENALTY,
    INTERCITY_BUS_STATION_LINKS,
    BRT_HUBS, BRT_HUB_LINKS,
    BRT_ROUTE_TIMES, BRT_BOARD_PENALTY,
    GTX_A_SEGMENT_TIMES, GTX_A_BOARD_PENALTY, GTX_A_TRANSFERS,
    ktx_fare_krw, saemaeul_fare_krw, mugunghwa_fare_krw, subway_fare_krw,
    bus_fare_krw, display_name,
    HEADWAY_MINUTES, TRANSFER_PENALTY as TRANSFER_PENALTY_TD_TABLE,
    get_time_period,
    DELAY_SIGMA, CO2_G_PER_PKM, emission_g,
    get_transfer_minutes,
)
from geocoder import (
    StationCandidate, OriginResult, address_to_origin,
    find_nearest_stations, walking_time_min, haversine_km,
)

# GTX-A 전용 신규 노드 좌표
_GTX_A_EXTRA_COORDS: dict[str, tuple[float, float]] = {
    "운정중앙_GTX": (37.7154, 126.7539),
    "킨텍스_GTX":   (37.6713, 126.7734),
    "성남_GTX":     (37.4477, 127.1271),
    "용인_GTX":     (37.2810, 127.1950),
}

# 전체 좌표 = 수도권 + 지역 지하철 + 고속버스 터미널 + BRT 거점 + GTX-A
ALL_COORDS: dict[str, tuple[float, float]] = {
    **COORDS, **REGIONAL_COORDS, **INTERCITY_BUS_TERMINALS,
    **BRT_HUBS, **_GTX_A_EXTRA_COORDS,
}


# =============================================================================
# 설정 (v2와 동일)
# =============================================================================
LINE_SPEED: dict[str, float] = {
    # 서울 시내 지하철 (2.2~2.7분/역)
    "1": 2.7,
    "2": 2.3,
    "2_성수지선": 2.2,
    "2_신정지선": 2.2,
    "3": 2.4,
    "4": 2.6,
    "5": 2.3,
    "6": 2.3,
    "7": 2.4,
    "8": 2.3,
    "9": 2.7,
    # 광역철도
    "수인분당": 3.0,
    "신분당": 4.5,
    "공항": 4.0,
    "경의중앙": 3.0,
    "경춘": 3.5,
    # 경전철
    "우이신설": 1.8,
    "김포골드": 2.0,
    "신림": 1.8,
    # KTX/무궁화/새마을은 별도 처리 (거리 기반)
    "KTX": 0.0,
    "무궁화": 0.0,
    "새마을": 0.0,
}
DEFAULT_INTER_TIME = 2.5
TRANSFER_PENALTY = 10.0

# KTX 전용
KTX_AVG_SPEED_KMH = 145.0      # 정차 포함 평균 (서울~부산 320km 직선 / 약 130~140분)
KTX_MIN_INTER_TIME = 8.0
KTX_TRANSFER_PENALTY = 20.0


# =============================================================================
# Origin 표현
# =============================================================================
@dataclass
class Origin:
    """한 사람의 출발 정보.

    - label: UI 표시용 (예: "강남구 역삼동" 또는 "잠실역")
    - coord: 출발 위경도 (역인 경우 그 역 좌표)
    - selected_station: 실제 사용할 출발역 (자동 선택 또는 사용자 지정)
    - alt_stations: 대안 역들 (사용자가 다른 역을 고르고 싶을 때)
    - walk_time_min: 출발지 → selected_station까지 접근 시간 (도보 또는 버스+도보 추정)
    - access_mode: 'walk' | 'bus'
    """
    label: str
    coord: tuple[float, float]
    selected_station: str
    alt_stations: list[StationCandidate]
    walk_time_min: float
    access_mode: str = "walk"


def build_origin_from_address(query: str,
                              api_key: str | None = None) -> Origin:
    """주소 문자열로부터 Origin을 만든다.

    카카오 API로 좌표를 얻고, 가까운 역 3개를 후보로 잡고,
    가장 가까운 역을 자동 선택한다.
    """
    result: OriginResult = address_to_origin(query, api_key=api_key)
    selected = result.candidates[0]
    return Origin(
        label=query,
        coord=result.coord,
        selected_station=selected.station,
        alt_stations=result.candidates,
        walk_time_min=selected.walk_time_min,
        access_mode=selected.access_mode,
    )


def build_origin_from_station(station: str) -> Origin:
    """역 이름으로부터 Origin을 만든다."""
    # 정확한 노드 ID — 수도권 또는 지역 지하철
    coord = ALL_COORDS.get(station)
    if coord:
        candidate = StationCandidate(
            station=station, distance_km=0.0,
            walk_time_min=0.0, coords=coord,
        )
        return Origin(
            label=f"{display_name(station)}역",
            coord=coord,
            selected_station=station,
            alt_stations=[candidate],
            walk_time_min=0.0,
        )

    # 동명이역 표시명 (예: '양평')
    matches = [nid for nid, disp in HOMONYM_DISPLAY.items() if disp == station]
    if matches:
        candidates = []
        for nid in matches:
            ncoord = ALL_COORDS.get(nid, (0, 0))
            candidates.append(StationCandidate(
                station=nid, distance_km=0.0,
                walk_time_min=0.0, coords=ncoord,
            ))
        first = candidates[0]
        return Origin(
            label=f"{station}역",
            coord=first.coords,
            selected_station=first.station,
            alt_stations=candidates,
            walk_time_min=0.0,
        )

    raise ValueError(f"역을 찾을 수 없습니다: {station}")


def build_origin_from_coord(label: str,
                            coord: tuple[float, float]) -> Origin:
    """좌표를 직접 넘겨 Origin을 만든다 (이미 지오코딩된 경우용)."""
    candidates = find_nearest_stations(coord, top_k=3)
    if not candidates:
        raise ValueError(f"근처 5km 이내에 지하철역이 없습니다: {coord}")
    selected = candidates[0]
    return Origin(
        label=label,
        coord=coord,
        selected_station=selected.station,
        alt_stations=candidates,
        walk_time_min=selected.walk_time_min,
        access_mode=selected.access_mode,
    )


def switch_origin_station(origin: Origin, new_station: str) -> Origin:
    """사용자가 다른 출발역을 골랐을 때 Origin을 갱신."""
    for c in origin.alt_stations:
        if c.station == new_station:
            return Origin(
                label=origin.label,
                coord=origin.coord,
                selected_station=new_station,
                alt_stations=origin.alt_stations,
                walk_time_min=c.walk_time_min,
                access_mode=c.access_mode,
            )
    raise ValueError(
        f"{new_station}는 후보에 없습니다. "
        f"가능한 후보: {[c.station for c in origin.alt_stations]}"
    )


# =============================================================================
# 그래프 + 다익스트라 (v2와 동일)
# =============================================================================
@dataclass
class Edge:
    to: str
    time: float
    line: str


_graph_cache: dict[str, list[Edge]] | None = None


def _transfer_penalty(prev_line: str | None, new_line: str, station: str | None = None) -> float:
    """노선 간 환승 페널티 계산. station이 주어지면 역별 실측치를 사용."""
    if prev_line is None:
        return 0.0
    if prev_line == new_line:
        return 0.0
    if prev_line in ("KTX_TRANSFER", "버스환승", "도보") or new_line in ("KTX_TRANSFER", "버스환승", "도보"):
        return 0.0
    if prev_line == "광역버스" or new_line == "광역버스":
        return 0.0
    if prev_line == "KTX" and new_line == "KTX":
        return 0.0
    if new_line in ("무궁화", "새마을") and prev_line not in ("무궁화", "새마을", "KTX", "KTX_TRANSFER"):
        return MUGUNGHWA_BOARD_PENALTY
    if prev_line in ("무궁화", "새마을") and new_line not in ("무궁화", "새마을", "KTX", "KTX_TRANSFER"):
        return MUGUNGHWA_BOARD_PENALTY
    if station:
        return get_transfer_minutes(station, prev_line, new_line, default=TRANSFER_PENALTY)
    return TRANSFER_PENALTY


def build_graph() -> dict[str, list[Edge]]:
    global _graph_cache
    if _graph_cache is not None:
        return _graph_cache

    # 모든 노드 수집
    all_stations = set()
    for stations in LINE_SEGMENTS.values():
        all_stations.update(stations)
    graph: dict[str, list[Edge]] = {name: [] for name in all_stations}

    # 각 세그먼트 내 인접관계 생성
    for seg_name, stations in LINE_SEGMENTS.items():
        line_code = SEGMENT_LINE[seg_name]
        is_ktx = line_code == "KTX"
        time_per_default = LINE_SPEED.get(line_code, DEFAULT_INTER_TIME)
        for i in range(len(stations) - 1):
            a, b = stations[i], stations[i + 1]
            if a == b:
                continue
            if is_ktx:
                # KTX: 실측 시간표 기반 (없으면 직선거리 fallback)
                t = KTX_SEGMENT_TIMES.get((a, b)) or KTX_SEGMENT_TIMES.get((b, a))
                if t is None:
                    if a in COORDS and b in COORDS:
                        dist_km = haversine_km(COORDS[a], COORDS[b])
                        t = max((dist_km / KTX_AVG_SPEED_KMH) * 60, KTX_MIN_INTER_TIME)
                    else:
                        t = KTX_MIN_INTER_TIME
            else:
                t = time_per_default
            graph[a].append(Edge(b, t, line_code))
            graph[b].append(Edge(a, t, line_code))

    # KTX 직통 에지: KTX_SEGMENT_TIMES 중 인접역이 아닌 쌍 (장거리 직통 열차)
    adjacent_ktx: set[tuple[str, str]] = set()
    for seg_name, stations in LINE_SEGMENTS.items():
        if SEGMENT_LINE[seg_name] == "KTX":
            for i in range(len(stations) - 1):
                a, b = stations[i], stations[i + 1]
                adjacent_ktx.add((a, b))
                adjacent_ktx.add((b, a))
    for (a, b), t in KTX_SEGMENT_TIMES.items():
        if (a, b) not in adjacent_ktx and a in graph and b in graph:
            graph[a].append(Edge(b, t, "KTX"))
            graph[b].append(Edge(a, t, "KTX"))

    # 무궁화/ITX 엣지 (거리 기반 시간 추정)
    for seg_name, stations in MUGUNGHWA_SEGMENTS.items():
        for i in range(len(stations) - 1):
            a, b = stations[i], stations[i + 1]
            if a == b:
                continue
            # 노드가 없으면 추가
            if a not in graph:
                graph[a] = []
            if b not in graph:
                graph[b] = []
            if a in COORDS and b in COORDS:
                dist_km = haversine_km(COORDS[a], COORDS[b])
                t = max((dist_km / MUGUNGHWA_SPEED_KMH) * 60, MUGUNGHWA_MIN_INTER_TIME)
            else:
                t = 15.0  # fallback
            graph[a].append(Edge(b, t, "무궁화"))
            graph[b].append(Edge(a, t, "무궁화"))

    # 지역 지하철 엣지 (부산/대구/광주/대전)
    # REGIONAL_COORDS를 메인 COORDS에 병합
    all_coords = {**COORDS, **REGIONAL_COORDS}
    for seg_name, stations in REGIONAL_SEGMENTS.items():
        line_code = REGIONAL_LINE_CODES.get(seg_name, seg_name)
        time_per = REGIONAL_LINE_SPEED.get(line_code, 2.3)
        for i in range(len(stations) - 1):
            a, b = stations[i], stations[i + 1]
            if a == b:
                continue
            if a not in graph:
                graph[a] = []
            if b not in graph:
                graph[b] = []
            graph[a].append(Edge(b, time_per, line_code))
            graph[b].append(Edge(a, time_per, line_code))

    # 새마을/ITX-새마을 엣지
    for seg_name, stations in SAEMAEUL_SEGMENTS.items():
        for i in range(len(stations) - 1):
            a, b = stations[i], stations[i + 1]
            if a == b:
                continue
            if a not in graph:
                graph[a] = []
            if b not in graph:
                graph[b] = []
            if a in COORDS and b in COORDS:
                dist_km = haversine_km(COORDS[a], COORDS[b])
                t = max((dist_km / SAEMAEUL_SPEED_KMH) * 60, SAEMAEUL_MIN_INTER_TIME)
            else:
                t = 10.0
            graph[a].append(Edge(b, t, "새마을"))
            graph[b].append(Edge(a, t, "새마을"))

    # KTX/SRT 환승 엣지 (모든 노드 추가 완료 후 처리)
    for ktx_node, sub_node, transfer_min in KTX_TRANSFERS:
        if ktx_node not in graph:
            graph[ktx_node] = []
        if sub_node not in graph:
            graph[sub_node] = []
        graph[ktx_node].append(Edge(sub_node, transfer_min, "KTX_TRANSFER"))
        graph[sub_node].append(Edge(ktx_node, transfer_min, "KTX_TRANSFER"))

    # 지역 지하철 내부 환승 (같은 역사 다른 호선, 예: 부산 서면 1↔2호선)
    for a, b, t in REGIONAL_TRANSFERS:
        if a not in graph:
            graph[a] = []
        if b not in graph:
            graph[b] = []
        graph[a].append(Edge(b, t, "환승"))
        graph[b].append(Edge(a, t, "환승"))

    # 고속버스 터미널 노드 추가
    for terminal in INTERCITY_BUS_TERMINALS:
        if terminal not in graph:
            graph[terminal] = []

    # 고속버스 O-D 엣지 (탑승 페널티 포함)
    for (a, b), t in INTERCITY_BUS_TIMES.items():
        if a not in graph:
            graph[a] = []
        if b not in graph:
            graph[b] = []
        ride = t + INTERCITY_BUS_BOARD_PENALTY
        graph[a].append(Edge(b, ride, "고속버스"))
        graph[b].append(Edge(a, ride, "고속버스"))

    # 고속버스 터미널 ↔ 지하철역 환승 링크
    for a, b, t in INTERCITY_BUS_STATION_LINKS:
        if a == b:
            continue
        if a not in graph:
            graph[a] = []
        if b not in graph:
            graph[b] = []
        graph[a].append(Edge(b, t, "버스환승"))
        graph[b].append(Edge(a, t, "버스환승"))

    # BRT/버스환승거점 노드 추가 (도보 링크)
    for hub in BRT_HUBS:
        if hub not in graph:
            graph[hub] = []
    for a, b, t in BRT_HUB_LINKS:
        if a not in graph:
            graph[a] = []
        if b not in graph:
            graph[b] = []
        graph[a].append(Edge(b, t, "도보"))
        graph[b].append(Edge(a, t, "도보"))

    # BRT / 광역버스 라우팅 엣지 (탑승 패널티 포함)
    for (a, b), t in BRT_ROUTE_TIMES.items():
        if a not in graph:
            graph[a] = []
        if b not in graph:
            graph[b] = []
        ride = t + BRT_BOARD_PENALTY
        graph[a].append(Edge(b, ride, "광역버스"))
        graph[b].append(Edge(a, ride, "광역버스"))

    # GTX-A 엣지 (KTX 수준 탑승 패널티)
    for (a, b), t in GTX_A_SEGMENT_TIMES.items():
        if a not in graph:
            graph[a] = []
        if b not in graph:
            graph[b] = []
        ride = t + GTX_A_BOARD_PENALTY
        graph[a].append(Edge(b, ride, "GTX-A"))
        graph[b].append(Edge(a, ride, "GTX-A"))

    # GTX-A ↔ 기존 지하철 환승 링크
    for a, b, t in GTX_A_TRANSFERS:
        if a == b:
            continue
        if a not in graph:
            graph[a] = []
        if b not in graph:
            graph[b] = []
        graph[a].append(Edge(b, t, "GTX_TRANSFER"))
        graph[b].append(Edge(a, t, "GTX_TRANSFER"))

    _graph_cache = graph
    return graph


def shortest_time(graph, start) -> tuple[dict[str, float], dict[str, int]]:
    """최단 시간 + 환승 횟수 계산. KTX는 1회 탑승만 허용.

    반환: (시간 dict, 환승 횟수 dict)
    """
    if start not in graph:
        return {}, {}
    # state: (time, node, prev_line, ktx_boarded, transfers)
    pq = [(0.0, start, None, False, 0)]
    best: dict[tuple, float] = {(start, None, False): 0.0}
    result: dict[str, float] = {start: 0.0}
    transfers_map: dict[str, int] = {start: 0}
    while pq:
        t, node, prev_line, ktx_boarded, n_transfers = heapq.heappop(pq)
        state = (node, prev_line, ktx_boarded)
        if best.get(state, float("inf")) < t:
            continue
        for edge in graph[node]:
            if ktx_boarded and prev_line == "KTX_TRANSFER" and edge.line == "KTX":
                continue
            transfer = _transfer_penalty(prev_line, edge.line, node)
            is_transfer = transfer > 0 and prev_line not in (None, edge.line)
            new_t = t + edge.time + transfer
            new_transfers = n_transfers + (1 if is_transfer else 0)
            new_ktx_boarded = ktx_boarded or (edge.line == "KTX")
            new_state = (edge.to, edge.line, new_ktx_boarded)
            if new_t < best.get(new_state, float("inf")):
                best[new_state] = new_t
                if new_t < result.get(edge.to, float("inf")):
                    result[edge.to] = new_t
                    transfers_map[edge.to] = new_transfers
                heapq.heappush(pq, (new_t, edge.to, edge.line, new_ktx_boarded, new_transfers))

    # KTX ↔ 지하철 쌍: 더 짧은 시간으로 통일
    for ktx, sub, _ in KTX_TRANSFERS:
        ktx_t = result.get(ktx, float("inf"))
        sub_t = result.get(sub, float("inf"))
        best_t = min(ktx_t, sub_t)
        if best_t < float("inf"):
            best_n = min(transfers_map.get(ktx, 99), transfers_map.get(sub, 99))
            result[ktx] = best_t
            result[sub] = best_t
            transfers_map[ktx] = best_n
            transfers_map[sub] = best_n

    return result, transfers_map


def get_lines_for_station(station: str) -> list[str]:
    return [line for line, sts in LINES.items() if station in sts]


# =============================================================================
# PageRank 기반 역 중심성
# =============================================================================
_pagerank_cache: dict[str, float] | None = None

def compute_pagerank(graph: dict, damping: float = 0.85, iterations: int = 60) -> dict[str, float]:
    """전철 그래프 PageRank. 높을수록 교통 요지.

    그래프를 무방향으로 처리 (대중교통은 양방향). O(edges * iterations).
    """
    global _pagerank_cache
    if _pagerank_cache is not None:
        return _pagerank_cache

    nodes = list(graph.keys())
    n = len(nodes)
    if n == 0:
        return {}

    # 무방향 이웃 (중복 제거, 자기 자신 제외)
    out_neighbors: dict[str, list[str]] = {node: [] for node in nodes}
    in_neighbors: dict[str, list[str]] = {node: [] for node in nodes}
    seen: set[tuple[str, str]] = set()
    for node, edges in graph.items():
        for e in edges:
            pair = (min(node, e.to), max(node, e.to))
            if pair not in seen:
                seen.add(pair)
                out_neighbors[node].append(e.to)
                out_neighbors[e.to].append(node)
                in_neighbors[node].append(e.to)
                in_neighbors[e.to].append(node)

    rank: dict[str, float] = {node: 1.0 / n for node in nodes}
    teleport = (1.0 - damping) / n
    for _ in range(iterations):
        new_rank: dict[str, float] = {}
        for node in nodes:
            s = sum(rank[nb] / max(len(out_neighbors[nb]), 1) for nb in in_neighbors[node])
            new_rank[node] = teleport + damping * s
        rank = new_rank

    # 0~1 정규화
    max_r = max(rank.values())
    min_r = min(rank.values())
    span = (max_r - min_r) or 1.0
    normalized = {node: (rank[node] - min_r) / span for node in nodes}

    _pagerank_cache = normalized
    return normalized


# =============================================================================
# 매개 중심성 (Betweenness Centrality) — Brandes 알고리즘 (Dijkstra 기반)
# =============================================================================
_betweenness_cache: dict[str, float] | None = None

def compute_betweenness(graph: dict) -> dict[str, float]:
    """각 역이 다른 역들의 최단 경로에 얼마나 자주 등장하는지 측정.
    높을수록 네트워크 병목 역 (신도림, 홍대입구 등).
    """
    global _betweenness_cache
    if _betweenness_cache is not None:
        return _betweenness_cache

    nodes = list(graph.keys())
    btw: dict[str, float] = {n: 0.0 for n in nodes}

    for src in nodes:
        # Dijkstra from src
        dist: dict[str, float] = {src: 0.0}
        sigma: dict[str, float] = {src: 1.0}   # 최단 경로 수
        pred: dict[str, list[str]] = {n: [] for n in nodes}
        pq = [(0.0, src)]
        stack: list[str] = []
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            stack.append(u)
            for e in graph[u]:
                v, w = e.to, e.time
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    sigma[v] = sigma[u]
                    pred[v] = [u]
                    heapq.heappush(pq, (nd, v))
                elif abs(nd - dist.get(v, float("inf"))) < 1e-9:
                    sigma[v] = sigma.get(v, 0.0) + sigma[u]
                    pred[v].append(u)

        # 역방향 누적
        delta: dict[str, float] = {n: 0.0 for n in nodes}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += (sigma.get(v, 0.0) / max(sigma.get(w, 1.0), 1e-9)) * (1.0 + delta[w])
            if w != src:
                btw[w] += delta[w]

    # 0~1 정규화
    max_b = max(btw.values()) or 1.0
    normalized = {n: btw[n] / max_b for n in nodes}
    _betweenness_cache = normalized
    return normalized


# =============================================================================
# 근접 중심성 (Closeness Centrality)
# =============================================================================
_closeness_cache: dict[str, float] | None = None

def _fast_dijkstra(graph: dict, start: str) -> dict[str, float]:
    """중심성 계산용 빠른 단방향 Dijkstra (환승 패널티 없음)."""
    dist: dict[str, float] = {start: 0.0}
    pq = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, float("inf")):
            continue
        for e in graph[u]:
            nd = d + e.time
            if nd < dist.get(e.to, float("inf")):
                dist[e.to] = nd
                heapq.heappush(pq, (nd, e.to))
    return dist

def compute_closeness(graph: dict) -> dict[str, float]:
    """전체 네트워크에서 평균 이동 거리가 짧을수록 높은 점수.
    전국 어디서든 빠르게 닿는 역 선호.
    """
    global _closeness_cache
    if _closeness_cache is not None:
        return _closeness_cache

    nodes = list(graph.keys())
    n = len(nodes)
    closeness: dict[str, float] = {}
    for src in nodes:
        dists = _fast_dijkstra(graph, src)
        reachable = [v for v in dists.values() if v < float("inf") and v > 0]
        if reachable:
            # Wasserman-Faust 공식: 도달 가능 비율 × 역수 평균
            closeness[src] = (len(reachable) / (n - 1)) * (len(reachable) / sum(reachable))
        else:
            closeness[src] = 0.0

    max_c = max(closeness.values()) or 1.0
    min_c = min(closeness.values())
    span = (max_c - min_c) or 1.0
    normalized = {n: (closeness[n] - min_c) / span for n in nodes}
    _closeness_cache = normalized
    return normalized


# =============================================================================
# Node2Vec 임베딩 (numpy 기반 간소화 구현)
# =============================================================================
_node2vec_cache: dict[str, "np.ndarray"] | None = None

def compute_node2vec(
    graph: dict,
    dim: int = 16,
    walk_length: int = 12,
    num_walks: int = 6,
    p: float = 1.0,
    q: float = 0.5,
    window: int = 4,
    epochs: int = 3,
    lr: float = 0.025,
) -> dict[str, "np.ndarray"]:
    """Node2Vec: 랜덤워크 + Skip-Gram으로 역 임베딩 학습.
    q < 1 → DFS 편향 (커뮤니티 구조 포착).
    """
    import numpy as np
    global _node2vec_cache
    if _node2vec_cache is not None:
        return _node2vec_cache

    nodes = list(graph.keys())
    node_idx = {n: i for i, n in enumerate(nodes)}
    n = len(nodes)
    rng = np.random.default_rng(42)

    # 이웃 리스트 (중복 제거, 가중치 = 1/time으로 정규화)
    adj: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for node, edges in graph.items():
        ui = node_idx[node]
        seen: set[int] = set()
        for e in edges:
            vi = node_idx.get(e.to)
            if vi is None or vi == ui or vi in seen:
                continue
            seen.add(vi)
            w = 1.0 / max(e.time, 1.0)
            adj[ui].append((vi, w))
            adj[vi].append((ui, w))  # 무방향

    def _sample_next(u: int, prev: int | None) -> int:
        nbrs = adj[u]
        if not nbrs:
            return u
        weights = []
        for v, w in nbrs:
            if prev is None:
                alpha = 1.0
            elif v == prev:
                alpha = 1.0 / p
            elif any(v == nb for nb, _ in adj[prev]):
                alpha = 1.0
            else:
                alpha = 1.0 / q
            weights.append(w * alpha)
        total = sum(weights)
        r = rng.random() * total
        cum = 0.0
        for (v, _), ww in zip(nbrs, weights):
            cum += ww
            if r <= cum:
                return v
        return nbrs[-1][0]

    # 랜덤워크 생성
    walks: list[list[int]] = []
    for _ in range(num_walks):
        order = rng.permutation(n).tolist()
        for start in order:
            walk = [start]
            prev = None
            for _ in range(walk_length - 1):
                cur = walk[-1]
                nxt = _sample_next(cur, prev)
                prev = cur
                walk.append(nxt)
            walks.append(walk)

    # Skip-Gram (Negative Sampling 없이 간소화된 소프트맥스 근사)
    emb = rng.standard_normal((n, dim)).astype(np.float32) * 0.01
    ctx = rng.standard_normal((n, dim)).astype(np.float32) * 0.01

    for _ in range(epochs):
        rng.shuffle(walks)
        for walk in walks:
            for i, u in enumerate(walk):
                lo = max(0, i - window)
                hi = min(len(walk), i + window + 1)
                for j in range(lo, hi):
                    if j == i:
                        continue
                    v = walk[j]
                    score = np.dot(emb[u], ctx[v])
                    grad = lr * (1.0 - 1.0 / (1.0 + np.exp(-score)))
                    emb[u] += grad * ctx[v]
                    ctx[v] += grad * emb[u]

    # L2 정규화
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms = np.where(norms < 1e-9, 1.0, norms)
    emb = emb / norms

    result = {node: emb[node_idx[node]] for node in nodes}
    _node2vec_cache = result
    return result


def node2vec_centrality(embeddings: dict) -> dict[str, float]:
    """임베딩 공간에서 전체 평균과 가까울수록 '네트워크 중심' 역."""
    import numpy as np
    nodes = list(embeddings.keys())
    mat = np.stack([embeddings[n] for n in nodes])
    center = mat.mean(axis=0)
    dists = np.linalg.norm(mat - center, axis=1)
    max_d = dists.max() or 1.0
    scores = 1.0 - (dists / max_d)   # 가까울수록 높은 점수
    return {node: float(scores[i]) for i, node in enumerate(nodes)}


# =============================================================================
# 통합 중심성 점수 캐시
# =============================================================================
_combined_centrality_cache: dict[str, float] | None = None

_CENTRALITY_CACHE_FILE = Path(__file__).parent / "centrality_cache.json"

def get_combined_centrality(graph: dict) -> dict[str, float]:
    """PageRank + Betweenness + Closeness + Node2Vec 가중 평균.
    결과를 JSON으로 캐시 — 서버 재시작 시 재계산 생략.
    """
    global _combined_centrality_cache
    if _combined_centrality_cache is not None:
        return _combined_centrality_cache

    # 디스크 캐시 로드 시도
    if _CENTRALITY_CACHE_FILE.exists():
        try:
            cached = json.loads(_CENTRALITY_CACHE_FILE.read_text(encoding="utf-8"))
            # 노드 수가 동일하면 유효
            if len(cached) == len(graph):
                _combined_centrality_cache = cached
                return cached
        except Exception:
            pass

    import time as _t
    t0 = _t.time()
    pr   = compute_pagerank(graph)
    btw  = compute_betweenness(graph)
    cls  = compute_closeness(graph)
    n2v_emb = compute_node2vec(graph)
    n2v  = node2vec_centrality(n2v_emb)
    print(f"[centrality] 계산 완료 {_t.time()-t0:.1f}s")

    nodes = list(graph.keys())
    combined = {}
    for node in nodes:
        combined[node] = (
            0.25 * pr.get(node, 0.0) +
            0.35 * btw.get(node, 0.0) +
            0.25 * cls.get(node, 0.0) +
            0.15 * n2v.get(node, 0.0)
        )
    max_c = max(combined.values()) or 1.0
    min_c = min(combined.values())
    span  = (max_c - min_c) or 1.0
    normalized = {n: (combined[n] - min_c) / span for n in nodes}

    # 디스크에 저장
    try:
        _CENTRALITY_CACHE_FILE.write_text(
            json.dumps(normalized, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass

    _combined_centrality_cache = normalized
    return normalized


# =============================================================================
# 후보 필터링
# =============================================================================


def filter_candidates(origins: list[Origin],
                      all_stations: list[str],
                      factor: float = 2.5) -> list[str]:
    """출발지들의 중심에서 합리적 거리 안의 역만 후보로.

    KTX/버스를 타면 이동 반경이 넓어지므로 최소 30km, factor=2.5로 여유롭게 설정.
    지하철과 쌍을 이루는 KTX 역(광명_KTX, 서울_KTX 등)은 제외.
    해당 지하철역이 대신 만남 장소로 추천됨.
    """
    coords = [o.coord for o in origins]
    if not coords:
        return all_stations
    cx = sum(c[0] for c in coords) / len(coords)
    cy = sum(c[1] for c in coords) / len(coords)
    centroid = (cx, cy)
    max_orig = max(haversine_km(centroid, c) for c in coords)
    threshold = max(max_orig * factor, 30.0)
    return [s for s in all_stations
            if s in ALL_COORDS
            and s not in KTX_EXCLUDE_FROM_CANDIDATES
            and haversine_km(centroid, ALL_COORDS[s]) <= threshold]


# =============================================================================
# 만남 장소 추천
# =============================================================================
@dataclass
class MeetingResult:
    station: str
    times: list[float]          # 각 사람의 총 시간 (도보 + 대중교통)
    walk_times: list[float]     # 각 사람의 출발지 접근 시간
    transit_times: list[float]  # 각 사람의 대중교통 시간
    max_time: float
    sum_time: float
    std_time: float
    score: float
    coords: tuple[float, float] = (0, 0)
    lines: list[str] = field(default_factory=list)
    fares: list[int] = field(default_factory=list)            # 각 사람 예상 요금(원)
    fares_all: list[tuple | None] = field(default_factory=list)  # (KTX, 새마을, 무궁화) or None


def find_meeting_points(
    origins: list[Origin],
    strategy: str = "balanced",
    top_k: int = 5,
    use_geographic_filter: bool = True,
) -> list[MeetingResult]:
    """주소 기반 출발지에 대해 만남 장소 후보를 점수순으로 반환.

    각 사람의 총 시간 = 출발지→출발역 도보시간 + 지하철시간.
    """
    graph = build_graph()
    pagerank = get_combined_centrality(graph)

    distance_maps: list[dict[str, float]] = []
    transfer_maps: list[dict[str, int]] = []
    walk_times: list[float] = []
    for o in origins:
        if o.selected_station not in graph:
            raise ValueError(f"역을 찾을 수 없습니다: {o.selected_station}")
        dm, tm = shortest_time(graph, o.selected_station)
        distance_maps.append(dm)
        transfer_maps.append(tm)
        walk_times.append(o.walk_time_min)

    all_stations = list(graph.keys())
    candidates = filter_candidates(origins, all_stations) \
        if use_geographic_filter else all_stations

    results = []
    for station in candidates:
        transit_times_for_station = []
        ok = True
        for dm in distance_maps:
            if station not in dm:
                ok = False
                break
            transit_times_for_station.append(dm[station])
        if not ok:
            continue

        # 총 시간 = 도보 + 대중교통
        total_times = [w + t for w, t in zip(walk_times, transit_times_for_station)]

        max_t = max(total_times)
        sum_t = sum(total_times)
        mean = sum_t / len(total_times)
        var = sum((t - mean) ** 2 for t in total_times) / len(total_times)
        std = var ** 0.5

        # 환승 횟수 패널티: 가장 많이 환승하는 사람 기준 5분/회
        max_transfers = max(
            (tm.get(station, 0) for tm in transfer_maps), default=0
        )
        transfer_score_penalty = max_transfers * 5.0

        # PageRank 보너스: 교통 요지일수록 최대 5분 감점 (낮을수록 유리)
        pr_bonus = pagerank.get(station, 0.0) * 5.0

        mean_t = sum_t / len(total_times)
        if strategy == "minimax":
            score = max_t + 2.0 * std + transfer_score_penalty - pr_bonus
        elif strategy == "sum":
            score = sum_t + transfer_score_penalty - pr_bonus
        elif strategy == "combined":
            # 종합: max(40%) + 평균(30%) + std(30%) — 빠르고 공평하며 총 이동 최소
            score = 0.4 * max_t + 0.3 * mean_t + 0.3 * std + transfer_score_penalty - pr_bonus
        else:
            # balanced: max + 공평도 페널티
            score = max_t + 1.0 * std + transfer_score_penalty - pr_bonus

        # 예상 요금: 출발지 → 만남 장소 직선거리 기반 추정
        # 장거리는 KTX / 새마을 / 무궁화 3가지 모두 표시
        fares = []        # 최저 요금 (실제 탈 교통수단)
        fares_all = []    # [KTX, 새마을, 무궁화] 전체 비교용 (장거리만)
        dest_coord = ALL_COORDS.get(station, COORDS.get(station, (0, 0)))
        is_ktx_dest = station.endswith("_KTX") or station.endswith("_SRT")
        is_bus_dest = station in INTERCITY_BUS_TERMINALS
        for o in origins:
            dist = haversine_km(o.coord, dest_coord)
            if dist < 3:
                fares.append(1400)
                fares_all.append(None)
            elif dist < 80:
                fares.append(subway_fare_krw(dist))
                fares_all.append(None)
            elif is_ktx_dest:
                fares.append(ktx_fare_krw(dist))
                fares_all.append((ktx_fare_krw(dist), saemaeul_fare_krw(dist), mugunghwa_fare_krw(dist)))
            elif is_bus_dest:
                fares.append(bus_fare_krw(dist))
                fares_all.append(None)
            else:
                # 새마을이 기본 (무궁화보다 빠르고 KTX보다 저렴)
                fares.append(saemaeul_fare_krw(dist))
                fares_all.append((ktx_fare_krw(dist), saemaeul_fare_krw(dist), mugunghwa_fare_krw(dist)))

        results.append(MeetingResult(
            station=station,
            times=total_times,
            walk_times=walk_times,
            transit_times=transit_times_for_station,
            max_time=max_t,
            sum_time=sum_t,
            std_time=std,
            score=score,
            coords=ALL_COORDS.get(station, COORDS.get(station, (0, 0))),
            lines=get_lines_for_station(station),
            fares=fares,
            fares_all=fares_all,
        ))

    results.sort(key=lambda c: c.score)
    return results[:top_k]


# =============================================================================
# 데모
# =============================================================================
def demo_offline():
    """API 없이 좌표 기반으로 시연"""
    print("=" * 70)
    print("Meeting Point Finder v3 — 좌표 기반 데모 (오프라인)")
    print("=" * 70)

    # 주소 대신 좌표를 직접 사용
    cases = [
        ("4명 시나리오: 강남/홍대/잠실/혜화 거주",
         [("강남구 역삼동", (37.5006, 127.0364)),
          ("마포구 합정동", (37.5494, 126.9135)),
          ("송파구 잠실동", (37.5145, 127.1058)),
          ("종로구 혜화동", (37.5826, 127.0019))]),
        ("3명 시나리오: 분당/일산/강남",
         [("성남시 분당구 정자동", (37.3672, 127.1086)),
          ("고양시 일산서구 호수공원", (37.6708, 126.7610)),
          ("서울 강남구 역삼동",       (37.5006, 127.0364))]),
    ]

    for desc, addresses in cases:
        print(f"\n📍 {desc}")
        origins = []
        for addr, coord in addresses:
            o = build_origin_from_coord(addr, coord)
            origins.append(o)
            print(f"   - {addr} → 가까운 역: {o.selected_station} "
                  f"(도보 {o.walk_time_min:.0f}분)")

        print("\n  추천 (Balanced):")
        results = find_meeting_points(origins, strategy="balanced", top_k=3)
        for i, r in enumerate(results, 1):
            print(f"    {i}. {r.station:12s} max={r.max_time:.0f}분, "
                  f"std={r.std_time:.1f}")
            for o, walk, transit in zip(origins, r.walk_times, r.transit_times):
                total = walk + transit
                print(f"        · {o.label:25s} 도보 {walk:.0f}분 + "
                      f"지하철 {transit:.0f}분 = {total:.0f}분")


if __name__ == "__main__":
    demo_offline()


# =============================================================================
# Time-Dependent Dijkstra  (TSAS 논문 Option B)
# =============================================================================
# 기존 shortest_time()은 고정 TRANSFER_PENALTY=10분 사용.
# 아래 shortest_time_td()는 시간대별 배차간격(HEADWAY_MINUTES)으로
# 환승 대기시간을 동적으로 계산: penalty = walk(3분) + headway/2
# =============================================================================

# 노선 코드 → HEADWAY_MINUTES 키 매핑
_LINE_TO_HEADWAY_KEY: dict[str, str] = {
    "1": "1호선",   "2": "2호선",   "3": "3호선",   "4": "4호선",
    "5": "5호선",   "6": "6호선",   "7": "7호선",   "8": "8호선",
    "9": "9호선",   "수인분당": "수인분당",  "신분당": "신분당",
    "공항": "공항철도",  "경의중앙": "경의중앙",  "경춘": "경춘",
    "KTX": "KTX",  "무궁화": "무궁화",  "새마을": "새마을",
    "ITX마음": "ITX마음",  "고속버스": "고속버스",  "광역버스": "광역버스",
    "대구": "대구",  "부산": "부산",  "광주": "광주",  "대전": "대전",
}
_PLATFORM_WALK = 3.0  # 승강장 이동 기본 도보 (분)


def _boarding_wait(line: str, period: str) -> float:
    """Expected boarding wait = headway / 2 for the given line and period."""
    key = _LINE_TO_HEADWAY_KEY.get(line, line)
    hw_entry = HEADWAY_MINUTES.get(key, {})
    hw = hw_entry.get(period, hw_entry.get("off_peak", 10.0))
    return hw / 2.0


def _transfer_penalty_td(prev_line: str | None, new_line: str, period: str,
                          station: str | None = None) -> float:
    """Time-dependent transfer penalty = station-specific walk + boarding wait."""
    if prev_line is None or prev_line == new_line:
        return 0.0
    if prev_line in ("KTX_TRANSFER", "버스환승", "도보") or \
       new_line in ("KTX_TRANSFER", "버스환승", "도보"):
        return 0.0
    if prev_line == "광역버스" or new_line == "광역버스":
        return 0.0
    if prev_line == "KTX" and new_line == "KTX":
        return 0.0
    if new_line in ("무궁화", "새마을") and \
       prev_line not in ("무궁화", "새마을", "KTX", "KTX_TRANSFER"):
        return MUGUNGHWA_BOARD_PENALTY
    if prev_line in ("무궁화", "새마을") and \
       new_line not in ("무궁화", "새마을", "KTX", "KTX_TRANSFER"):
        return MUGUNGHWA_BOARD_PENALTY
    walk = get_transfer_minutes(station, prev_line, new_line, default=_PLATFORM_WALK) \
        if station else _PLATFORM_WALK
    return walk + _boarding_wait(new_line, period)


def shortest_time_td(
    graph: dict[str, list],
    start: str,
    period: str = "off_peak",
) -> tuple[dict[str, float], dict[str, int]]:
    """Time-dependent shortest path.

    Args:
        graph:  transit graph from build_graph()
        start:  origin station node id
        period: "peak_am" | "peak_pm" | "off_peak"

    Returns:
        (travel_times, transfer_counts) — same format as shortest_time()
    """
    if start not in graph:
        return {}, {}
    pq = [(0.0, start, None, False, 0)]
    best: dict[tuple, float] = {(start, None, False): 0.0}
    result: dict[str, float] = {start: 0.0}
    transfers_map: dict[str, int] = {start: 0}
    while pq:
        t, node, prev_line, ktx_boarded, n_transfers = heapq.heappop(pq)
        state = (node, prev_line, ktx_boarded)
        if best.get(state, float("inf")) < t:
            continue
        for edge in graph[node]:
            if ktx_boarded and prev_line == "KTX_TRANSFER" and edge.line == "KTX":
                continue
            transfer = _transfer_penalty_td(prev_line, edge.line, period, node)
            is_transfer = transfer > 0 and prev_line not in (None, edge.line)
            new_t = t + edge.time + transfer
            new_transfers = n_transfers + (1 if is_transfer else 0)
            new_ktx = ktx_boarded or (edge.line == "KTX")
            new_state = (edge.to, edge.line, new_ktx)
            if new_t < best.get(new_state, float("inf")):
                best[new_state] = new_t
                if new_t < result.get(edge.to, float("inf")):
                    result[edge.to] = new_t
                    transfers_map[edge.to] = new_transfers
                heapq.heappush(pq, (new_t, edge.to, edge.line, new_ktx, new_transfers))

    for ktx, sub, _ in KTX_TRANSFERS:
        kt = result.get(ktx, float("inf"))
        st = result.get(sub, float("inf"))
        bt = min(kt, st)
        if bt < float("inf"):
            bn = min(transfers_map.get(ktx, 99), transfers_map.get(sub, 99))
            result[ktx] = result[sub] = bt
            transfers_map[ktx] = transfers_map[sub] = bn

    return result, transfers_map


def find_meeting_points_td(
    origins: list[Origin],
    hour: int,
    strategy: str = "balanced",
    top_k: int = 5,
    use_geographic_filter: bool = True,
) -> list[MeetingResult]:
    """Time-dependent meeting point finder.

    Args:
        origins: list of Origin objects
        hour:    departure hour (0-23, KST)
        strategy: same as find_meeting_points()
        top_k:  number of results

    Returns:
        list of MeetingResult (same format as find_meeting_points())
    """
    period = get_time_period(hour)
    graph = build_graph()
    pagerank = get_combined_centrality(graph)

    distance_maps: list[dict[str, float]] = []
    transfer_maps: list[dict[str, int]] = []
    walk_times: list[float] = []
    for o in origins:
        if o.selected_station not in graph:
            raise ValueError(f"역을 찾을 수 없습니다: {o.selected_station}")
        dm, tm = shortest_time_td(graph, o.selected_station, period=period)
        distance_maps.append(dm)
        transfer_maps.append(tm)
        walk_times.append(o.walk_time_min)

    all_stations = list(graph.keys())
    candidates = filter_candidates(origins, all_stations) \
        if use_geographic_filter else all_stations

    results = []
    for station in candidates:
        transit_times_for_station = []
        ok = True
        for dm in distance_maps:
            if station not in dm:
                ok = False
                break
            transit_times_for_station.append(dm[station])
        if not ok:
            continue

        total_times = [w + t for w, t in zip(walk_times, transit_times_for_station)]
        max_t = max(total_times)
        sum_t = sum(total_times)
        mean_t = sum_t / len(total_times)
        var = sum((t - mean_t) ** 2 for t in total_times) / len(total_times)
        std = var ** 0.5
        max_transfers = max(
            (tm.get(station, 0) for tm in transfer_maps), default=0
        )
        transfer_score_penalty = max_transfers * 5.0
        pr_bonus = pagerank.get(station, 0.0) * 5.0

        if strategy == "minimax":
            score = max_t + 2.0 * std + transfer_score_penalty - pr_bonus
        elif strategy == "sum":
            score = sum_t + transfer_score_penalty - pr_bonus
        elif strategy == "combined":
            score = 0.4 * max_t + 0.3 * mean_t + 0.3 * std + transfer_score_penalty - pr_bonus
        else:
            score = max_t + 1.0 * std + transfer_score_penalty - pr_bonus

        dest_coord = ALL_COORDS.get(station, COORDS.get(station, (0, 0)))
        is_ktx_dest = station.endswith("_KTX") or station.endswith("_SRT")
        is_bus_dest = station in INTERCITY_BUS_TERMINALS
        fares, fares_all = [], []
        for o in origins:
            dist = haversine_km(o.coord, dest_coord)
            if dist < 3:
                fares.append(1400); fares_all.append(None)
            elif dist < 80:
                fares.append(subway_fare_krw(dist)); fares_all.append(None)
            elif is_ktx_dest:
                fares.append(ktx_fare_krw(dist))
                fares_all.append((ktx_fare_krw(dist), saemaeul_fare_krw(dist), mugunghwa_fare_krw(dist)))
            elif is_bus_dest:
                fares.append(bus_fare_krw(dist)); fares_all.append(None)
            else:
                fares.append(saemaeul_fare_krw(dist))
                fares_all.append((ktx_fare_krw(dist), saemaeul_fare_krw(dist), mugunghwa_fare_krw(dist)))

        results.append(MeetingResult(
            station=station,
            times=total_times,
            walk_times=walk_times,
            transit_times=transit_times_for_station,
            max_time=max_t,
            sum_time=sum_t,
            std_time=std,
            score=score,
            coords=dest_coord,
            lines=get_lines_for_station(station),
            fares=fares,
            fares_all=fares_all,
        ))

    results.sort(key=lambda c: c.score)
    return results[:top_k]


# =============================================================================
# 논문 실험: Static vs Time-Dependent 비교
# =============================================================================

def compare_static_vs_td(
    origins: list[Origin],
    hours: list[int] | None = None,
    strategy: str = "balanced",
    top_k: int = 5,
) -> dict:
    """Run static and time-dependent meeting point finding for multiple hours.

    Returns a dict suitable for JSON serialisation (paper Table 2 / Figure).

    Example output:
    {
      "static": [{"station": "홍대입구", "max": 32.1, "std": 4.2}, ...],
      "peak_am_08": [{"station": "신촌", "max": 38.5, "std": 6.1}, ...],
      "off_peak_14": [...],
    }
    """
    if hours is None:
        hours = [8, 14, 19]   # peak_am, off_peak, peak_pm

    graph = build_graph()

    static_results = find_meeting_points(origins, strategy=strategy, top_k=top_k)
    static_stations = [r.station for r in static_results]

    out: dict = {
        "static": [
            {"rank": i+1, "station": r.station,
             "max_min": round(r.max_time, 1),
             "std_min": round(r.std_time, 1),
             "sum_min": round(r.sum_time, 1)}
            for i, r in enumerate(static_results)
        ],
    }

    for h in hours:
        period = get_time_period(h)
        label = f"{period}_{h:02d}h"
        td_results = find_meeting_points_td(origins, hour=h,
                                            strategy=strategy, top_k=top_k)
        td_stations = [r.station for r in td_results]

        # rank shift: how many top-k stations changed vs static
        overlap = len(set(static_stations) & set(td_stations))
        changed = top_k - overlap

        out[label] = [
            {"rank": i+1, "station": r.station,
             "max_min": round(r.max_time, 1),
             "std_min": round(r.std_time, 1),
             "sum_min": round(r.sum_time, 1)}
            for i, r in enumerate(td_results)
        ]
        out[label + "_meta"] = {
            "period": period,
            "hour": h,
            "top_k_changed_vs_static": changed,
            "avg_max_delta_min": round(
                sum(r.max_time for r in td_results) / top_k -
                sum(r.max_time for r in static_results) / top_k, 2
            ),
        }

    return out


# =============================================================================
# Uncertainty-Aware Scoring  (논문 Option ② — Robust Meeting Point)
# =============================================================================
# LINE_CODE → DELAY_SIGMA 키 매핑
_LINE_TO_SIGMA_KEY: dict[str, str] = {
    "1": "1호선", "2": "2호선", "3": "3호선", "4": "4호선",
    "5": "5호선", "6": "6호선", "7": "7호선", "8": "8호선", "9": "9호선",
    "수인분당": "수인분당", "신분당": "신분당", "공항": "공항철도",
    "경의중앙": "경의중앙", "경춘": "경춘",
    "GTX-A": "GTX-A",
    "KTX": "KTX", "무궁화": "무궁화", "새마을": "새마을",
    "ITX마음": "ITX마음", "고속버스": "고속버스", "광역버스": "광역버스",
    "대구": "대구", "부산": "부산", "광주": "광주", "대전": "대전",
}


def route_sigma(route_lines: set[str]) -> float:
    """Aggregate delay σ for a route given the set of lines used.
    Combined σ = sqrt(Σ σᵢ²) assuming independence.
    """
    variance = sum(
        DELAY_SIGMA.get(_LINE_TO_SIGMA_KEY.get(l, l), 5.0) ** 2
        for l in route_lines
        if l not in ("KTX_TRANSFER", "GTX_TRANSFER", "버스환승", "도보", "환승")
    )
    return variance ** 0.5


def robust_score(
    total_times: list[float],
    route_sigma_vals: list[float],
    lambda_: float = 0.5,
    strategy: str = "balanced",
) -> float:
    """Robust meeting point score: E[max] + λ·σ_combined.

    λ controls risk-aversion (0 = risk-neutral, 1 = risk-averse).
    """
    max_t = max(total_times)
    sum_t = sum(total_times)
    mean_t = sum_t / len(total_times)
    var = sum((t - mean_t) ** 2 for t in total_times) / len(total_times)
    std = var ** 0.5
    # Propagated uncertainty across all travelers
    combined_sigma = (sum(s**2 for s in route_sigma_vals) / len(route_sigma_vals)) ** 0.5

    if strategy == "minimax":
        base = max_t + 2.0 * std
    elif strategy == "sum":
        base = sum_t
    elif strategy == "combined":
        base = 0.4 * max_t + 0.3 * mean_t + 0.3 * std
    else:
        base = max_t + 1.0 * std

    return base + lambda_ * combined_sigma


# =============================================================================
# CO₂ Emission Estimation  (논문 Option ③)
# =============================================================================

# 대략적 line code → CO2 mode 매핑
_LINE_TO_CO2_MODE: dict[str, str] = {
    "GTX-A": "GTX-A", "KTX": "KTX",
    "무궁화": "무궁화", "새마을": "새마을", "ITX마음": "ITX마음",
    "고속버스": "고속버스", "광역버스": "광역버스",
}


def estimate_route_co2(origin_coord: tuple, dest_coord: tuple,
                       route_lines: set[str]) -> float:
    """Estimate CO₂ (grams) for a trip based on dominant mode and haversine distance."""
    from geocoder import haversine_km
    dist = haversine_km(origin_coord, dest_coord)
    # Pick dominant mode (highest CO2 factor if multiple)
    mode = "subway"
    for line in route_lines:
        candidate = _LINE_TO_CO2_MODE.get(line, "subway")
        if CO2_G_PER_PKM.get(candidate, 0) > CO2_G_PER_PKM.get(mode, 0):
            mode = candidate
    return emission_g(mode, dist)


# =============================================================================
# Multi-Objective Pareto Front  (논문 Option ⑤)
# =============================================================================

@dataclass
class ParetoResult:
    """A meeting point on the Pareto front (time × cost × CO₂)."""
    station: str
    max_time: float       # minutes — minimize
    total_cost: int       # KRW — minimize
    total_co2: float      # grams CO₂ — minimize
    std_time: float
    sum_time: float
    times: list[float]
    coords: tuple[float, float]
    lines: list[str]


def _dominates(a: ParetoResult, b: ParetoResult) -> bool:
    """True if a dominates b (better or equal in all objectives, strictly better in one)."""
    return (
        a.max_time <= b.max_time and
        a.total_cost <= b.total_cost and
        a.total_co2 <= b.total_co2 and
        (a.max_time < b.max_time or a.total_cost < b.total_cost or a.total_co2 < b.total_co2)
    )


def find_pareto_meeting_points(
    origins: list[Origin],
    strategy: str = "balanced",
    max_candidates: int = 50,
) -> list[ParetoResult]:
    """Find Pareto-optimal meeting points across (time, cost, CO₂).

    Returns the Pareto front — all non-dominated solutions.
    Useful for letting users pick based on their own preference.
    """
    graph = build_graph()
    pagerank = get_combined_centrality(graph)

    distance_maps, transfer_maps, walk_times = [], [], []
    for o in origins:
        dm, tm = shortest_time(graph, o.selected_station)
        distance_maps.append(dm)
        transfer_maps.append(tm)
        walk_times.append(o.walk_time_min)

    all_stations = list(graph.keys())
    candidates = filter_candidates(origins, all_stations)

    # Score all candidates with balanced strategy first, take top-max_candidates
    scored = []
    for station in candidates:
        transit_times = []
        ok = True
        for dm in distance_maps:
            if station not in dm:
                ok = False; break
            transit_times.append(dm[station])
        if not ok:
            continue
        total_times = [w + t for w, t in zip(walk_times, transit_times)]
        max_t = max(total_times)
        sum_t = sum(total_times)
        mean_t = sum_t / len(total_times)
        std = (sum((t - mean_t)**2 for t in total_times) / len(total_times)) ** 0.5
        pr_bonus = pagerank.get(station, 0.0) * 5.0
        score = max_t + std - pr_bonus
        scored.append((score, station, total_times, transit_times))

    scored.sort()
    top_candidates = scored[:max_candidates]

    # Build full ParetoResult for each
    results: list[ParetoResult] = []
    for _, station, total_times, transit_times in top_candidates:
        dest_coord = ALL_COORDS.get(station, (0, 0))
        is_ktx = station.endswith("_KTX") or station.endswith("_SRT")
        is_bus = station in INTERCITY_BUS_TERMINALS

        total_cost = 0
        total_co2 = 0.0
        for o in origins:
            dist = haversine_km(o.coord, dest_coord)
            if dist < 3:
                cost = 1400
            elif dist < 80:
                cost = subway_fare_krw(dist)
            elif is_ktx:
                cost = ktx_fare_krw(dist)
            elif is_bus:
                cost = bus_fare_krw(dist)
            else:
                cost = saemaeul_fare_krw(dist)
            total_cost += cost
            total_co2 += estimate_route_co2(o.coord, dest_coord, set())

        max_t = max(total_times)
        sum_t = sum(total_times)
        mean_t = sum_t / len(total_times)
        std = (sum((t - mean_t)**2 for t in total_times) / len(total_times)) ** 0.5

        results.append(ParetoResult(
            station=station,
            max_time=max_t,
            total_cost=total_cost,
            total_co2=total_co2,
            std_time=std,
            sum_time=sum_t,
            times=total_times,
            coords=dest_coord,
            lines=get_lines_for_station(station),
        ))

    # Compute Pareto front
    pareto_front = []
    for r in results:
        if not any(_dominates(other, r) for other in results if other is not r):
            pareto_front.append(r)

    pareto_front.sort(key=lambda r: r.max_time)
    return pareto_front
