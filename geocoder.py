"""
Geocoder & Station Matcher
============================

주소를 지하철역으로 변환하는 모듈.

흐름:
  주소 (예: "강남구 역삼동")
    └─→ [카카오 로컬 API] → 위경도 좌표
                           └─→ 가까운 역 N개 + 도보시간

도보시간 계산:
  - 직선거리 × 1.3 (실제 도로 보정 계수)
  - 평균 보행 속도 4 km/h (= 약 67m/분)
  - 최소 2분 (역 안에서 플랫폼까지 이동 등 고려)

카카오 API 키 발급:
  https://developers.kakao.com → 내 애플리케이션 → REST API 키
  무료 티어: 일일 30만 요청 (개인 서비스로는 충분)
"""

from __future__ import annotations
import urllib.request
import urllib.parse
import json
import os
from dataclasses import dataclass
from math import radians, sin, cos, asin, sqrt

from seoul_subway_data import COORDS, REGIONAL_COORDS
_ALL_COORDS = {**COORDS, **REGIONAL_COORDS}


# =============================================================================
# 도보 시간 계산
# =============================================================================
WALKING_SPEED_KMH = 4.5           # 평균 보행 속도 (도시 평지 기준)
WALKING_DETOUR_FACTOR = 1.2       # 직선거리 → 실제 도로거리 보정
MIN_WALK_TIME_MIN = 2.0           # 최소 도보시간 (역사 진입 시간 등)

# 버스 추정 (B 단계: 정류장 데이터 없이 추정식만)
BUS_SPEED_KMH = 15.0              # 시내버스 평균 속도 (정차 포함)
BUS_DETOUR_FACTOR = 1.3           # 버스 노선의 우회 정도
BUS_OVERHEAD_MIN = 5.0            # 정류장 도보(2.5분) + 대기(2.5분) 합산
BUS_DROP_TO_STATION_MIN = 2.0     # 하차 정류장 → 지하철역 도보 추정
BUS_MIN_USEFUL_WALK_MIN = 15.0    # 도보가 이 시간 이상이어야 버스 옵션 검토


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """위경도 두 점 사이 거리(km)"""
    lat1, lng1 = a
    lat2, lng2 = b
    R = 6371
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    h = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlng/2)**2
    return 2 * R * asin(sqrt(h))


def walking_time_min(from_coord: tuple[float, float],
                     to_coord: tuple[float, float]) -> float:
    """두 좌표 사이 도보 시간(분)"""
    distance_km = haversine_km(from_coord, to_coord) * WALKING_DETOUR_FACTOR
    time_min = (distance_km / WALKING_SPEED_KMH) * 60
    return max(time_min, MIN_WALK_TIME_MIN)


def bus_time_estimate_min(from_coord: tuple[float, float],
                          to_coord: tuple[float, float]) -> float:
    """두 좌표 사이 버스+도보 추정 시간 (분).

    추정 모델:
      - 직선거리 × 1.3(노선 우회) ÷ 15km/h (버스 평균속도) × 60 = 버스 이동시간
      - + 5분 (정류장 도보 + 승차 대기)
      - + 2분 (하차 후 지하철역까지 도보)

    ※ 데이터 없는 추정이라 실제와 ±10분 정도 오차 가능.
    """
    distance_km = haversine_km(from_coord, to_coord) * BUS_DETOUR_FACTOR
    bus_ride_min = (distance_km / BUS_SPEED_KMH) * 60
    return bus_ride_min + BUS_OVERHEAD_MIN + BUS_DROP_TO_STATION_MIN


def access_time_min(from_coord: tuple[float, float],
                    to_coord: tuple[float, float]) -> tuple[float, str]:
    """출발지 → 지하철역 접근 최적 모드.

    Returns:
        (시간, 모드)  — mode는 'walk' 또는 'bus'

    규칙:
      - 도보가 BUS_MIN_USEFUL_WALK_MIN 미만이면 무조건 도보
      - 그 이상이면 버스 추정값과 비교, 빠른 쪽 채택
    """
    walk = walking_time_min(from_coord, to_coord)
    if walk < BUS_MIN_USEFUL_WALK_MIN:
        return (walk, "walk")
    bus = bus_time_estimate_min(from_coord, to_coord)
    if bus < walk:
        return (bus, "bus")
    return (walk, "walk")


# =============================================================================
# 가까운 역 찾기
# =============================================================================
@dataclass
class StationCandidate:
    """주소에서 가까운 역 후보"""
    station: str
    distance_km: float
    walk_time_min: float       # 접근 시간(버스든 도보든)
    coords: tuple[float, float]
    access_mode: str = "walk"  # 'walk' 또는 'bus'


def find_nearest_stations(
    coord: tuple[float, float],
    top_k: int = 3,
    max_distance_km: float = 25.0,  # 지방은 역이 드물어 넉넉히
) -> list[StationCandidate]:
    """주어진 좌표에서 가까운 지하철역 N개를 반환.

    각 후보의 접근 시간은 도보/버스 중 더 빠른 쪽으로 계산.
    """
    candidates = []
    for station, station_coord in _ALL_COORDS.items():
        dist = haversine_km(coord, station_coord)
        if dist > max_distance_km:
            continue
        access_t, mode = access_time_min(coord, station_coord)
        candidates.append(StationCandidate(
            station=station,
            distance_km=dist,
            walk_time_min=access_t,
            coords=station_coord,
            access_mode=mode,
        ))
    # 거리가 아니라 접근 시간 기준 정렬 (버스 빠른 곳이 우선)
    candidates.sort(key=lambda c: c.walk_time_min)
    return candidates[:top_k]


# =============================================================================
# 카카오 지오코딩
# =============================================================================
KAKAO_API_BASE = "https://dapi.kakao.com/v2/local/search"


class GeocodeError(Exception):
    pass


def geocode_kakao(query: str, api_key: str | None = None) -> tuple[float, float]:
    """카카오 로컬 API로 주소를 위경도로 변환.

    주소, 키워드, 동/도로명 모두 지원.

    Args:
        query: 검색할 주소나 장소 이름
        api_key: 카카오 REST API 키. None이면 환경변수 KAKAO_REST_KEY에서 읽음.

    Returns:
        (lat, lng) 튜플

    Raises:
        GeocodeError: API 키 누락, 결과 없음, 네트워크 오류 등
    """
    api_key = api_key or os.environ.get("KAKAO_REST_KEY")
    if not api_key:
        raise GeocodeError(
            "카카오 API 키가 없습니다. "
            "환경변수 KAKAO_REST_KEY를 설정하거나 api_key 인자로 전달하세요."
        )

    # 1단계: 주소 검색 (정확한 도로명/지번)
    coord = _kakao_request("/address.json", {"query": query}, api_key)
    if coord:
        return coord

    # 2단계: 키워드 검색 (장소명, 동 이름, 건물명 등)
    coord = _kakao_request("/keyword.json", {"query": query}, api_key)
    if coord:
        return coord

    raise GeocodeError(f"주소를 찾을 수 없습니다: '{query}'")


def _kakao_request(path: str, params: dict, api_key: str) -> tuple[float, float] | None:
    """카카오 API 호출 후 첫 번째 결과의 좌표 반환. 결과 없으면 None."""
    url = KAKAO_API_BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"KakaoAK {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise GeocodeError("API 키가 잘못됐습니다. 카카오 개발자 콘솔에서 확인하세요.")
        raise GeocodeError(f"카카오 API 오류: {e.code}")
    except urllib.error.URLError as e:
        raise GeocodeError(f"네트워크 오류: {e.reason}")

    documents = data.get("documents", [])
    if not documents:
        return None

    doc = documents[0]
    # 카카오 좌표는 'x'(경도), 'y'(위도) 형식
    lng = float(doc["x"])
    lat = float(doc["y"])
    return (lat, lng)


# =============================================================================
# 통합: 주소 → 출발역 후보
# =============================================================================
@dataclass
class OriginResult:
    query: str
    coord: tuple[float, float]
    candidates: list[StationCandidate]  # 기본 추천 = candidates[0]


def address_to_origin(query: str, api_key: str | None = None) -> OriginResult:
    """주소를 받아 출발역 후보 3개를 반환.

    candidates[0]이 가장 가까운 역(자동 선택용),
    [1], [2]는 사용자가 다른 역을 고르고 싶을 때 표시.
    """
    coord = geocode_kakao(query, api_key=api_key)
    candidates = find_nearest_stations(coord, top_k=3)
    if not candidates:
        raise GeocodeError(
            f"'{query}' 근처 3km 이내에 지하철역이 없습니다. "
            "수도권 내 주소만 지원됩니다."
        )
    return OriginResult(query=query, coord=coord, candidates=candidates)


# =============================================================================
# 데모 (오프라인 테스트용)
# =============================================================================
def demo_offline():
    """API 호출 없이 좌표→역 매칭만 테스트"""
    print("=" * 60)
    print("Geocoder — 오프라인 데모 (좌표→역 매칭)")
    print("=" * 60)

    test_coords = [
        ("강남역 인근",       (37.4979, 127.0276)),
        ("판교 테크노밸리",   (37.4019, 127.1086)),
        ("일산 호수공원",     (37.6708, 126.7610)),
        ("강남구 역삼동 중심", (37.5006, 127.0364)),
        ("성동구 옥수동",     (37.5403, 127.0177)),
    ]

    for desc, coord in test_coords:
        print(f"\n📍 {desc} ({coord[0]}, {coord[1]})")
        candidates = find_nearest_stations(coord, top_k=3)
        for i, c in enumerate(candidates, 1):
            print(f"  {i}. {c.station:12s} "
                  f"{c.distance_km:.2f}km ({c.walk_time_min:.0f}분 도보)")


def demo_online(api_key: str):
    """카카오 API를 실제로 호출하는 데모"""
    print("=" * 60)
    print("Geocoder — 온라인 데모 (카카오 API)")
    print("=" * 60)

    queries = [
        "강남구 역삼동",
        "성남시 분당구 정자동",
        "서울특별시 종로구 청운동",
        "이태원 해밀턴호텔",
    ]
    for q in queries:
        print(f"\n📍 '{q}'")
        try:
            result = address_to_origin(q, api_key=api_key)
            print(f"   좌표: {result.coord[0]:.4f}, {result.coord[1]:.4f}")
            for i, c in enumerate(result.candidates, 1):
                print(f"   {i}. {c.station:12s} "
                      f"{c.distance_km:.2f}km ({c.walk_time_min:.0f}분)")
        except GeocodeError as e:
            print(f"   ❌ {e}")


if __name__ == "__main__":
    import sys
    api_key = os.environ.get("KAKAO_REST_KEY")
    if api_key:
        demo_online(api_key)
    else:
        print("(KAKAO_REST_KEY 환경변수가 없어 오프라인 데모만 실행)\n")
        demo_offline()
