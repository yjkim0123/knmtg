"""
Midpoint Server — 만남의 광장 찾기
=====================================
uvicorn main:app --reload
"""

import os
import json
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
KAKAO_KEY = os.getenv("KAKAO_REST_KEY", "3d50b0e921dfff46986f96ee9d354828")
KAKAO_BASE = "https://dapi.kakao.com/v2/local/search"
BASE_DIR = Path(__file__).parent

# ---------------------------------------------------------------------------
# Build HTML once at startup (inject subway data + patch JS for server proxy)
# ---------------------------------------------------------------------------
_template = (BASE_DIR / "midpoint_demo_v3_template.html").read_text(encoding="utf-8")
_subway_json = (BASE_DIR / "subway_data.json").read_text(encoding="utf-8")

_PATCHED_KAKAO_REQUEST = """\
async function kakaoRequest(path, query) {
  const resp = await fetch(`/api/geocode?path=${encodeURIComponent(path)}&q=${encodeURIComponent(query)}`);
  if (resp.status === 401) throw new Error("서버 API 키 오류");
  if (!resp.ok) throw new Error(`API 오류: ${resp.status}`);
  const data = await resp.json();
  if (!data.documents || !data.documents.length) return null;
  const doc = data.documents[0];
  return [parseFloat(doc.y), parseFloat(doc.x)];
}"""

_PATCHED_CATEGORY = """\
async function kakaoSearchCategory(coord, category, radius = 500, size = 8) {
  const code = CATEGORY_CODES[category];
  if (!code) throw new Error(`알 수 없는 카테고리: ${category}`);
  const [lat, lng] = coord;
  const params = new URLSearchParams({
    lat: String(lat), lng: String(lng),
    category_code: code,
    radius: String(Math.min(Math.max(radius, 1), 20000)),
    size: String(Math.min(size, 15)),
  });
  const resp = await fetch(`/api/category?${params}`);
  if (!resp.ok) throw new Error(`API 오류: ${resp.status}`);
  const data = await resp.json();
  return (data.documents || []).map(d => ({
    name: d.place_name || "",
    category: d.category_name || "",
    address: d.road_address_name || d.address_name || "",
    coord: [parseFloat(d.y), parseFloat(d.x)],
    distance: parseInt(d.distance || "0", 10),
    phone: d.phone || "",
    url: d.place_url || "",
  }));
}"""

_ORIGINAL_KAKAO_REQUEST = """\
async function kakaoRequest(path, query) {
  const url = `https://dapi.kakao.com/v2/local/search${path}?query=${encodeURIComponent(query)}`;
  const resp = await fetch(url, {
    headers: { "Authorization": `KakaoAK ${kakaoKey}` }
  });
  if (resp.status === 401) {
    throw new Error("API 키가 잘못됐습니다");
  }
  if (!resp.ok) {
    throw new Error(`카카오 API 오류: ${resp.status}`);
  }
  const data = await resp.json();
  if (!data.documents || !data.documents.length) return null;
  const doc = data.documents[0];
  return [parseFloat(doc.y), parseFloat(doc.x)];
}"""

_ORIGINAL_CATEGORY = """\
async function kakaoSearchCategory(coord, category, radius = 500, size = 8) {
  if (!kakaoKey) throw new Error("API 키가 없습니다");
  const code = CATEGORY_CODES[category];
  if (!code) throw new Error(`알 수 없는 카테고리: ${category}`);
  const [lat, lng] = coord;
  const params = new URLSearchParams({
    category_group_code: code,
    x: String(lng),
    y: String(lat),
    radius: String(Math.min(Math.max(radius, 1), 20000)),
    sort: "distance",
    size: String(Math.min(size, 15)),
  });
  const url = `https://dapi.kakao.com/v2/local/search/category.json?${params}`;
  const resp = await fetch(url, {
    headers: { "Authorization": `KakaoAK ${kakaoKey}` }
  });
  if (resp.status === 401) throw new Error("API 키가 잘못됐습니다");
  if (!resp.ok) throw new Error(`카카오 API 오류: ${resp.status}`);
  const data = await resp.json();
  return (data.documents || []).map(d => ({
    name: d.place_name || "",
    category: d.category_name || "",
    address: d.road_address_name || d.address_name || "",
    coord: [parseFloat(d.y), parseFloat(d.x)],
    distance: parseInt(d.distance || "0", 10),
    phone: d.phone || "",
    url: d.place_url || "",
  }));
}"""

HTML_PAGE = (
    _template
    .replace("__SUBWAY_DATA__", _subway_json)
    # Kakao key는 서버가 관리 — 브라우저에 노출 안 함
    .replace(
        'let kakaoKey = "3d50b0e921dfff46986f96ee9d354828";',
        'let kakaoKey = "server";  // 서버 프록시 모드'
    )
    # JS 함수들을 서버 프록시 버전으로 교체
    .replace(_ORIGINAL_KAKAO_REQUEST, _PATCHED_KAKAO_REQUEST)
    .replace(_ORIGINAL_CATEGORY, _PATCHED_CATEGORY)
)

# ---------------------------------------------------------------------------
# 중심성 사전 계산 (백그라운드, 캐시 파일 없을 때만 실행)
# ---------------------------------------------------------------------------
def _precompute_centrality():
    try:
        from meeting_finder_v3 import build_graph, get_combined_centrality
        g = build_graph()
        get_combined_centrality(g)
        print("[startup] 중심성 계산 완료")
    except Exception as e:
        print(f"[startup] 중심성 계산 실패: {e}")

threading.Thread(target=_precompute_centrality, daemon=True).start()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Midpoint — 만남의 광장 찾기")


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE

@app.get("/en", response_class=RedirectResponse)
def lang_en():
    return RedirectResponse("/?lang=en", status_code=302)

@app.get("/zh", response_class=RedirectResponse)
def lang_zh():
    return RedirectResponse("/?lang=zh", status_code=302)


@app.get("/api/geocode")
async def geocode(
    path: str = Query("/address.json", description="카카오 엔드포인트 path"),
    q: str = Query(..., description="검색 쿼리"),
):
    """카카오 로컬 API 프록시 (주소/키워드 검색)"""
    allowed = {"/address.json", "/keyword.json"}
    if path not in allowed:
        raise HTTPException(400, f"허용된 path: {allowed}")
    url = f"{KAKAO_BASE}{path}?query={q}"
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(url, headers={"Authorization": f"KakaoAK {KAKAO_KEY}"})
    if resp.status_code == 401:
        raise HTTPException(401, "카카오 API 키 오류")
    resp.raise_for_status()
    return resp.json()


@app.get("/api/category")
async def category(
    lat: float = Query(...),
    lng: float = Query(...),
    category_code: str = Query(..., description="카카오 카테고리 코드 (CE7, FD6 등)"),
    radius: int = Query(500, ge=1, le=20000),
    size: int = Query(8, ge=1, le=15),
):
    """카카오 카테고리 검색 프록시 (카페/맛집)"""
    allowed_codes = {"CE7", "FD6", "CS2", "MT1", "SW8"}
    if category_code not in allowed_codes:
        raise HTTPException(400, f"허용된 코드: {allowed_codes}")
    params = {
        "category_group_code": category_code,
        "x": str(lng),
        "y": str(lat),
        "radius": str(radius),
        "sort": "distance",
        "size": str(size),
    }
    url = f"{KAKAO_BASE}/category.json?" + "&".join(f"{k}={v}" for k, v in params.items())
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(url, headers={"Authorization": f"KakaoAK {KAKAO_KEY}"})
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8765, reload=False)
