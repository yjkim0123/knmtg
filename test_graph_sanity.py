"""
Subway Graph Sanity Tests
==========================

알려진 실제 소요시간과 우리 모델의 결과를 비교.

목적: **그래프 버그를 잡는다** (예: 동명이역, 잘못된 분기, 누락된 환승)
**파라미터 미세 튜닝은 목적이 아님** — 평균 ±20% 오차는 평균값 모델의 본질적 한계.

따라서 기대 범위는 충분히 넓게 잡되, 명백한 그래프 오류
(예: 인천→문래 19분 같은 비현실적 결과)를 잡아내는 것에 집중.
"""

from __future__ import annotations
from meeting_finder_v3 import build_graph, shortest_time
from seoul_subway_data import COORDS, display_name


# (start, end, expected_min, expected_max, description)
# 기대 범위는 실제 시간의 ±25% 정도로 넉넉하게
EXPECTED_TIMES = [
    # ─── 짧은 거리 (인접/2~3정거장) ───
    ("강남", "역삼", 1, 6, "인접 1정거장"),
    ("종각", "시청", 1, 6, "1호선 인접"),
    ("잠실", "잠실새내", 1, 6, "2호선 인접"),
    ("강남", "교대", 1, 8, "2호선 2정거장"),

    # ─── 서울 시내 단~중거리 ───
    ("강남", "사당", 8, 22, "2호선 7정거장"),
    ("종로3가", "잠실", 18, 40, "2호선+5호선 환승"),
    ("강남", "홍대입구", 25, 45, "2호선 12정거장+ — 길 수 있음"),
    ("강남", "서울역", 25, 45, "2호선+1호선 환승"),
    ("잠실", "강남", 8, 22, "2호선 7정거장"),
    ("강남", "혜화", 28, 50, "환승 1~2회"),
    ("강남", "광화문", 28, 50, "2호선+5호선 환승"),
    ("강남", "공덕", 25, 45, "여러 경로"),

    # ─── 신분당선 ───
    ("강남", "정자", 18, 35, "신분당선 직통 5정거장"),
    ("강남", "판교", 14, 30, "신분당선 직통 4정거장"),

    # ─── 중장거리 ───
    ("서울역", "수원", 20, 80, "1호선/무궁화/새마을"),

    # ─── 인천-서울 (그래프 버그 검증) ───
    ("인천", "서울역", 60, 95, "1호선 끝까지"),
    ("인천", "신도림", 45, 80, "1호선 인천행"),
    ("인천", "문래", 50, 90, "1호선 인천 → 신도림 → 문래"),
    ("인천", "강남", 55, 115, "광역버스 직통 or 1호선+2호선"),

    # ─── 양평 동명이역 (가장 중요한 검증!) ───
    ("용문", "문래", 80, 130, "경기도 양평 → 영등포 — 동명이역 검증"),
    ("용문", "왕십리", 55, 95, "경의중앙선 직통"),
    ("용문", "청량리", 35, 90, "경의중앙선 또는 양평_KTX 경유"),

    # ─── 분기 검증 ───
    ("당고개", "사당", 50, 80, "4호선 끝~끝"),
    ("방화", "하남검단산", 75, 115, "5호선 상일행 끝"),
    ("방화", "마천", 70, 100, "5호선 마천행 끝"),

    # ─── KTX 검증 ───
    ("서울_KTX", "부산_KTX", 140, 200, "KTX 서울~부산 직행 (실제 ~150분)"),
    ("서울_KTX", "동대구_KTX", 95, 140, "KTX 서울~동대구"),
    ("서울_KTX", "광주송정_KTX", 100, 145, "KTX 서울~광주송정 (호남선)"),
    ("서울_KTX", "강릉_KTX", 75, 130, "KTX 서울~강릉 (강릉선)"),
    ("서울_KTX", "대전_KTX", 50, 85, "KTX 서울~대전"),
    ("강남", "부산_KTX", 150, 220, "강남→수서(SRT) or 서울역(KTX)→부산"),
    ("강남", "동대구_KTX", 110, 180, "강남→수서(SRT) or 서울역(KTX)→동대구"),
    # KTX-KTX 환승 (분기점)
    ("부산_KTX", "광주송정_KTX", 150, 280, "부산→오송 환승→광주송정"),
]


def run_tests():
    print("=" * 80)
    print("Subway Graph Sanity Tests")
    print("=" * 80)

    graph = build_graph()
    pass_count = 0
    fail_count = 0
    failures = []

    for start, end, lo, hi, desc in EXPECTED_TIMES:
        actual_start = _resolve_node(start)
        actual_end = _resolve_node(end)

        if not actual_start or not actual_end:
            failures.append(f"  ❌ {start} → {end}: 노드 없음")
            fail_count += 1
            continue

        dm, _ = shortest_time(graph, actual_start)
        if actual_end not in dm:
            failures.append(f"  ❌ {start} → {end}: 도달 불가")
            fail_count += 1
            continue

        t = dm[actual_end]

        if lo <= t <= hi:
            symbol = "✓"
            pass_count += 1
        else:
            symbol = "❌"
            fail_count += 1
            direction = "너무 짧음" if t < lo else "너무 긺"
            failures.append(
                f"  ❌ {start:8s} → {end:8s}: {t:5.1f}분 "
                f"(예상: {lo}~{hi}분, {direction}) — {desc}"
            )

        print(f"  {symbol} {start:8s} → {end:8s}: "
              f"{t:5.1f}분  [{lo:3d}~{hi:3d}분]  {desc}")

    print()
    print("=" * 80)
    print(f"통과: {pass_count}/{pass_count + fail_count}")
    if failures:
        print(f"\n실패 케이스:")
        for f in failures:
            print(f)
    print("=" * 80)
    return fail_count == 0


def _resolve_node(name: str) -> str | None:
    if name in COORDS:
        return name
    if name == "양평":
        return "양평_경의중앙"
    return None


if __name__ == "__main__":
    import sys
    ok = run_tests()
    sys.exit(0 if ok else 1)
