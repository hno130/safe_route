from __future__ import annotations

import json
import math
import os
import time
import xml.etree.ElementTree as ET
import csv
import hashlib
import sqlite3
from heapq import heappop, heappush
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATABASE_PATH = Path(os.getenv("SAFEWALK_DB_PATH", BASE_DIR / "safewalk.db"))

ACCIDENT_CACHE_TTL_SECONDS = 60 * 30
GRID_SIZE = 35
WALKING_SPEED_M_PER_MIN = 78
DEFAULT_OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_TIMEOUT_SECONDS = 7
DEFAULT_ROUTER_MODE = "auto"
SAFE_ROUTE_OPTION_COUNT = int(os.getenv("SAFE_ROUTE_OPTION_COUNT", "3"))
SAFE_ROUTE_CANDIDATE_POOL = int(os.getenv("SAFE_ROUTE_CANDIDATE_POOL", "14"))
SAFE_ROUTE_SIMILAR_DISTANCE_M = float(
    os.getenv("SAFE_ROUTE_SIMILAR_DISTANCE_M", "70")
)
SAFE_ROUTE_SIMILAR_RATIO = float(os.getenv("SAFE_ROUTE_SIMILAR_RATIO", "0.72"))
SAFE_MAX_DETOUR_RATIO = float(os.getenv("SAFE_MAX_DETOUR_RATIO", "1.25"))
SAFE_ASTAR_MIN_WEIGHT_MULTIPLIER = 0.58
SAFE_ASTAR_MAX_WEIGHT_MULTIPLIER = 8.0
SAFE_RISK_PENALTY_WEIGHT = float(os.getenv("SAFE_RISK_PENALTY_WEIGHT", "2.8"))
SAFE_RISK_CORE_THRESHOLD = float(os.getenv("SAFE_RISK_CORE_THRESHOLD", "0.7"))
SAFE_RISK_CORE_PENALTY = float(os.getenv("SAFE_RISK_CORE_PENALTY", "90"))
SAFE_RISK_NEGATIVE_MULTIPLIER_SCALE = float(
    os.getenv("SAFE_RISK_NEGATIVE_MULTIPLIER_SCALE", "0.022")
)
SAFE_FACILITY_POSITIVE_MULTIPLIER_SCALE = float(
    os.getenv("SAFE_FACILITY_POSITIVE_MULTIPLIER_SCALE", "0.006")
)
SAFE_RISK_CORE_BLOCK_THRESHOLD = float(
    os.getenv("SAFE_RISK_CORE_BLOCK_THRESHOLD", "0.85")
)
SAFE_RISK_CORE_BLOCK_MULTIPLIER = float(
    os.getenv("SAFE_RISK_CORE_BLOCK_MULTIPLIER", "35")
)
SAFE_HIGH_RISK_THRESHOLD = float(os.getenv("SAFE_HIGH_RISK_THRESHOLD", "25"))
SAFE_HIGH_RISK_TARGET_RATIO = float(os.getenv("SAFE_HIGH_RISK_TARGET_RATIO", "0.45"))
SAFE_HIGH_RISK_MAX_DETOUR_RATIO = float(
    os.getenv("SAFE_HIGH_RISK_MAX_DETOUR_RATIO", "5.0")
)
SAFE_HIGH_RISK_SOFT_DETOUR_RATIO = float(
    os.getenv("SAFE_HIGH_RISK_SOFT_DETOUR_RATIO", "2.2")
)
SAFE_HIGH_RISK_HARD_DETOUR_RATIO = float(
    os.getenv("SAFE_HIGH_RISK_HARD_DETOUR_RATIO", "3.0")
)
SAFE_HIGH_RISK_DETOUR_PENALTY = float(
    os.getenv("SAFE_HIGH_RISK_DETOUR_PENALTY", "170")
)
SAFE_HIGH_RISK_DISTANCE_PENALTY = float(
    os.getenv("SAFE_HIGH_RISK_DISTANCE_PENALTY", "0.07")
)
ROUTE_EXPLANATION_PROVIDER = os.getenv("ROUTE_EXPLANATION_PROVIDER", "auto").lower()
OPENAI_EXPLANATION_MODEL = os.getenv("OPENAI_EXPLANATION_MODEL", "gpt-5.5")
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "8"))

SAFETY_FEATURE_META = {
    "street_lamp": {
        "label": "Street lights",
        "weight": 1.0,
        "radius_m": 55,
        "color": "#f59e0b",
    },
    "cctv": {
        "label": "CCTV",
        "weight": 0.9,
        "radius_m": 90,
        "color": "#8b5cf6",
    },
    "police": {
        "label": "Police",
        "weight": 1.35,
        "radius_m": 220,
        "color": "#2563eb",
    },
    "emergency": {
        "label": "Emergency",
        "weight": 1.15,
        "radius_m": 180,
        "color": "#dc2626",
    },
    "crossing": {
        "label": "Crossings",
        "weight": 0.42,
        "radius_m": 65,
        "color": "#10b981",
    },
    "traffic_signal": {
        "label": "Signals",
        "weight": 0.36,
        "radius_m": 70,
        "color": "#14b8a6",
    },
}


class Point(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class RouteRequest(BaseModel):
    start: Point
    end: Point


app = FastAPI(
    title="Safe Walk Route API",
    description="Pedestrian route comparison using accident hot spots and risk-aware weights.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_database()


@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


FALLBACK_ACCIDENTS: list[dict[str, Any]] = [
    {
        "id": "demo-001",
        "name": "Gangnam Station Crossing",
        "district": "Gangnam-gu",
        "lat": 37.4981,
        "lng": 127.0276,
        "accidents": 18,
        "casualties": 22,
        "radius_m": 390,
        "year": 2024,
    },
    {
        "id": "demo-002",
        "name": "Seoul Station Plaza",
        "district": "Jung-gu",
        "lat": 37.5547,
        "lng": 126.9707,
        "accidents": 15,
        "casualties": 18,
        "radius_m": 360,
        "year": 2024,
    },
    {
        "id": "demo-003",
        "name": "Jongno 3-ga",
        "district": "Jongno-gu",
        "lat": 37.5707,
        "lng": 126.9910,
        "accidents": 16,
        "casualties": 19,
        "radius_m": 370,
        "year": 2024,
    },
    {
        "id": "demo-004",
        "name": "Hongik Univ. Entrance",
        "district": "Mapo-gu",
        "lat": 37.5563,
        "lng": 126.9236,
        "accidents": 14,
        "casualties": 17,
        "radius_m": 350,
        "year": 2024,
    },
    {
        "id": "demo-005",
        "name": "Sillim Station",
        "district": "Gwanak-gu",
        "lat": 37.4842,
        "lng": 126.9297,
        "accidents": 13,
        "casualties": 16,
        "radius_m": 340,
        "year": 2024,
    },
    {
        "id": "demo-006",
        "name": "Jamsil Saenae",
        "district": "Songpa-gu",
        "lat": 37.5110,
        "lng": 127.0862,
        "accidents": 12,
        "casualties": 15,
        "radius_m": 330,
        "year": 2024,
    },
    {
        "id": "demo-007",
        "name": "Yeongdeungpo Station",
        "district": "Yeongdeungpo-gu",
        "lat": 37.5156,
        "lng": 126.9070,
        "accidents": 12,
        "casualties": 14,
        "radius_m": 330,
        "year": 2024,
    },
    {
        "id": "demo-008",
        "name": "Sinchon Rotary",
        "district": "Seodaemun-gu",
        "lat": 37.5552,
        "lng": 126.9368,
        "accidents": 10,
        "casualties": 12,
        "radius_m": 300,
        "year": 2024,
    },
    {
        "id": "demo-009",
        "name": "Cheongnyangni Station",
        "district": "Dongdaemun-gu",
        "lat": 37.5804,
        "lng": 127.0469,
        "accidents": 11,
        "casualties": 13,
        "radius_m": 310,
        "year": 2024,
    },
    {
        "id": "demo-010",
        "name": "Konkuk Univ. Entrance",
        "district": "Gwangjin-gu",
        "lat": 37.5404,
        "lng": 127.0692,
        "accidents": 9,
        "casualties": 11,
        "radius_m": 290,
        "year": 2024,
    },
]

_accident_cache: dict[str, Any] = {
    "expires_at": 0.0,
    "payload": None,
}
_safety_cache: dict[str, dict[str, Any]] = {}
_walk_graph_cache: dict[str, Any] = {}


def db_connection() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database() -> None:
    with db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS route_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                start_lat REAL NOT NULL,
                start_lng REAL NOT NULL,
                end_lat REAL NOT NULL,
                end_lng REAL NOT NULL,
                router TEXT NOT NULL,
                accident_source TEXT,
                safety_source TEXT,
                normal_distance_m REAL,
                safe_distance_m REAL,
                normal_risk_score REAL,
                safe_risk_score REAL,
                risk_reduction_percent REAL,
                safe_option_count INTEGER,
                explanation_summary TEXT,
                request_json TEXT NOT NULL,
                response_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                summary_json TEXT,
                payload_hash TEXT NOT NULL,
                bbox TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_route_requests_created_at
            ON route_requests(created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_data_snapshots_type_created_at
            ON data_snapshots(source_type, created_at DESC)
            """
        )


def fetch_rows(
    conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()
) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def payload_hash(value: Any) -> str:
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


def store_route_request(
    start: dict[str, float], end: dict[str, float], result: dict[str, Any]
) -> None:
    try:
        init_database()
        normal = result.get("normal") or {}
        safe = result.get("safe") or {}
        comparison = result.get("comparison") or {}
        explanation = result.get("explanation") or {}
        request_payload = {"start": start, "end": end}
        with db_connection() as conn:
            conn.execute(
                """
                INSERT INTO route_requests (
                    created_at, start_lat, start_lng, end_lat, end_lng, router,
                    accident_source, safety_source, normal_distance_m,
                    safe_distance_m, normal_risk_score, safe_risk_score,
                    risk_reduction_percent, safe_option_count,
                    explanation_summary, request_json, response_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    float(start["lat"]),
                    float(start["lng"]),
                    float(end["lat"]),
                    float(end["lng"]),
                    str(result.get("router") or "unknown"),
                    result.get("accident_source"),
                    result.get("safety_source"),
                    normal.get("distance_m"),
                    safe.get("distance_m"),
                    normal.get("risk_score"),
                    safe.get("risk_score"),
                    comparison.get("risk_reduction_percent"),
                    len(result.get("safe_options") or []),
                    explanation.get("summary"),
                    json_dumps(request_payload),
                    json_dumps(result),
                ),
            )
    except Exception as exc:
        print(f"DB route history save failed: {exc}")


def store_data_snapshot(
    source_type: str,
    payload: dict[str, Any],
    bbox: tuple[float, float, float, float] | None = None,
) -> None:
    try:
        init_database()
        items = payload.get("items") or []
        summary = payload.get("summary") or {}
        bbox_text = ",".join(f"{value:.7f}" for value in bbox) if bbox else None
        with db_connection() as conn:
            conn.execute(
                """
                INSERT INTO data_snapshots (
                    created_at, source_type, source, item_count,
                    summary_json, payload_hash, bbox
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    source_type,
                    str(payload.get("source") or "unknown"),
                    len(items),
                    json_dumps(summary),
                    payload_hash({"source_type": source_type, "bbox": bbox_text, "items": items}),
                    bbox_text,
                ),
            )
    except Exception as exc:
        print(f"DB data snapshot save failed: {exc}")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/db/stats")
def database_stats() -> dict[str, Any]:
    init_database()
    with db_connection() as conn:
        route_count = conn.execute("SELECT COUNT(*) FROM route_requests").fetchone()[0]
        snapshot_count = conn.execute("SELECT COUNT(*) FROM data_snapshots").fetchone()[0]
        latest_routes = fetch_rows(
            conn,
            """
            SELECT id, created_at, router, start_lat, start_lng, end_lat, end_lng,
                   normal_distance_m, safe_distance_m, normal_risk_score,
                   safe_risk_score, risk_reduction_percent, safe_option_count
            FROM route_requests
            ORDER BY id DESC
            LIMIT 5
            """,
        )
        latest_snapshots = fetch_rows(
            conn,
            """
            SELECT id, created_at, source_type, source, item_count, bbox
            FROM data_snapshots
            ORDER BY id DESC
            LIMIT 5
            """,
        )
    return {
        "database": str(DATABASE_PATH),
        "route_request_count": route_count,
        "data_snapshot_count": snapshot_count,
        "latest_route_requests": latest_routes,
        "latest_data_snapshots": latest_snapshots,
    }


@app.get("/api/routes/history")
def route_history(limit: int = Query(default=10, ge=1, le=50)) -> dict[str, Any]:
    init_database()
    with db_connection() as conn:
        rows = fetch_rows(
            conn,
            """
            SELECT id, created_at, router, start_lat, start_lng, end_lat, end_lng,
                   normal_distance_m, safe_distance_m, normal_risk_score,
                   safe_risk_score, risk_reduction_percent, safe_option_count,
                   explanation_summary
            FROM route_requests
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
    return {"items": rows}


@app.get("/api/accidents")
def accidents() -> dict[str, Any]:
    return load_accidents()


@app.get("/api/safety")
def safety(
    west: float | None = Query(default=None, ge=-180, le=180),
    south: float | None = Query(default=None, ge=-90, le=90),
    east: float | None = Query(default=None, ge=-180, le=180),
    north: float | None = Query(default=None, ge=-90, le=90),
) -> dict[str, Any]:
    if None in (west, south, east, north):
        bbox = bbox_for_points(
            [
                {"lat": seoul_center()["lat"], "lng": seoul_center()["lng"]},
            ],
            margin_m=1400,
        )
    else:
        bbox = sanitize_bbox((west, south, east, north))
    return load_safety_features(bbox)


@app.post("/api/routes")
def routes(payload: RouteRequest) -> dict[str, Any]:
    accident_payload = load_accidents()
    accident_points = accident_payload["items"]

    start = payload.start.model_dump()
    end = payload.end.model_dump()
    safety_payload = load_safety_features(
        bbox_for_points([start, end], route_margin_m(start, end))
    )
    safety_features = safety_payload["items"]
    straight_distance = haversine_m(start, end)
    if straight_distance < 20:
        empty = build_route_summary([start, end], accident_points, safety_features)
        result = {
            "router": "none",
            "algorithm": {
                "name": "No route needed",
                "graph": "none",
                "normal_weight": "none",
                "safe_weight": "none",
                "detour_limit_ratio": None,
            },
            "accident_source": accident_payload["source"],
            "safety_source": safety_payload["source"],
            "safety_summary": safety_payload["summary"],
            "normal": empty,
            "safe": empty,
            "explanation": {
                "source": "template",
                "title": "경로 비교 불가",
                "summary": "출발지와 도착지가 너무 가까워 안전 경로와 일반 경로를 의미 있게 비교하지 않았습니다.",
                "bullets": ["20m 미만의 매우 짧은 이동은 우회 경로 산정 효과가 작습니다."],
            },
            "message": "Start and destination are too close to compare routes.",
        }
        store_route_request(start, end, result)
        return result

    route_result = try_osmnx_routes(start, end, accident_points, safety_features)
    if not route_result:
        route_result = try_osrm_routes(start, end, accident_points, safety_features)

    if route_result:
        router = route_result["router"]
        normal_coords = route_result["normal"]
        safe_coords = route_result["safe"]
        safe_coord_options = route_result.get("safe_options") or [safe_coords]
    else:
        router = "risk-grid-a-star-fallback"
        normal_coords = calculate_grid_route(
            start, end, accident_points, safety_features, safe=False
        )
        safe_coords = calculate_grid_route(
            start, end, accident_points, safety_features, safe=True
        )
        safe_coord_options = [safe_coords]
        route_result = {"algorithm": algorithm_meta(router)}

    normal = build_route_summary(normal_coords, accident_points, safety_features)
    safe_options = build_safe_route_options(
        safe_coord_options,
        normal,
        accident_points,
        safety_features,
        safety_payload["summary"],
        route_result.get("algorithm", algorithm_meta(router)),
        accident_payload["source"],
        safety_payload["source"],
    )
    if not safe_options:
        safe_options = build_safe_route_options(
            [safe_coords],
            normal,
            accident_points,
            safety_features,
            safety_payload["summary"],
            route_result.get("algorithm", algorithm_meta(router)),
            accident_payload["source"],
            safety_payload["source"],
        )
    safe = safe_options[0]
    comparison = safe["comparison"]

    result = {
        "router": router,
        "algorithm": route_result.get("algorithm", algorithm_meta(router)),
        "accident_source": accident_payload["source"],
        "safety_source": safety_payload["source"],
        "safety_summary": safety_payload["summary"],
        "normal": normal,
        "safe": safe,
        "safe_options": safe_options,
        "comparison": comparison,
    }
    result["explanation"] = build_route_explanation(
        normal,
        safe,
        comparison,
        safety_payload["summary"],
        result["algorithm"],
        accident_payload["source"],
        safety_payload["source"],
    )
    result["safe"]["explanation"] = result["explanation"]
    result["safe_options"][0]["explanation"] = result["explanation"]
    store_route_request(start, end, result)
    return result


def build_safe_route_options(
    coord_options: list[list[dict[str, float]]],
    normal: dict[str, Any],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
    safety_summary: dict[str, int],
    algorithm: dict[str, Any],
    accident_source: str,
    safety_source: str,
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: list[dict[str, Any]] = []
    for coords in coord_options:
        if len(coords) < 2 or route_already_seen(coords, seen):
            continue
        summary = build_route_summary(coords, accident_points, safety_features)
        comparison = build_route_comparison(normal, summary)
        option = {
            **summary,
            "comparison": comparison,
            "option_score": round(osmnx_candidate_cost(summary, normal), 3),
        }
        options.append(option)
        seen.append({"coords": coords})

    options.sort(key=lambda option: float(option["option_score"]))
    ranked = options[: max(1, SAFE_ROUTE_OPTION_COUNT)]
    for index, option in enumerate(ranked, start=1):
        option["id"] = f"safe-option-{index}"
        option["rank"] = index
        option["label"] = safe_option_label(index, option, normal)
        option["explanation"] = build_route_explanation(
            normal,
            option,
            option["comparison"],
            safety_summary,
            algorithm,
            accident_source,
            safety_source,
            allow_openai=False,
        )
    return ranked


def build_route_comparison(
    normal: dict[str, Any], safe: dict[str, Any]
) -> dict[str, float]:
    distance_delta_m = round(float(safe["distance_m"]) - float(normal["distance_m"]), 1)
    distance_delta_percent = round(
        distance_delta_m / max(float(normal["distance_m"]), 1) * 100, 1
    )
    return {
        "distance_delta_m": distance_delta_m,
        "distance_delta_percent": distance_delta_percent,
        "risk_delta": round(float(safe["risk_score"]) - float(normal["risk_score"]), 1),
        "net_safety_delta": round(
            float(safe["net_safety_score"]) - float(normal["net_safety_score"]), 1
        ),
        "risk_reduction_percent": calculate_reduction(
            float(normal["risk_score"]), float(safe["risk_score"])
        ),
    }


def safe_option_label(
    index: int, option: dict[str, Any], normal: dict[str, Any]
) -> str:
    if index == 1:
        return "추천"
    distance = float(option["distance_m"])
    normal_distance = float(normal["distance_m"])
    risk = float(option["risk_score"])
    normal_risk = float(normal["risk_score"])
    if index == 2:
        if distance <= normal_distance * 1.45:
            return "짧은 우회"
        return "위험 최소"
    if index == 3:
        if distance <= normal_distance * 1.6 and risk <= normal_risk * 0.45:
            return "균형 대안"
        if risk <= normal_risk * 0.35:
            return "저위험 대안"
        return "대안 3"
    return f"대안 {index}"


def algorithm_meta(router: str) -> dict[str, Any]:
    if router == "osmnx-walk-a-star":
        return {
            "name": "A* on OSM walking network",
            "graph": "OSMnx walk graph",
            "normal_weight": "length",
            "safe_weight": "length adjusted by facility_score - accident_score",
            "detour_limit_ratio": SAFE_MAX_DETOUR_RATIO,
        }
    if router.startswith("road-osrm"):
        return {
            "name": "OSRM road fallback with safe candidate scoring",
            "graph": "OSRM route candidates",
            "normal_weight": "OSRM shortest route",
            "safe_weight": "candidate score from accidents and safety facilities",
            "detour_limit_ratio": None,
        }
    return {
        "name": "A* grid fallback",
        "graph": "local risk grid",
        "normal_weight": "distance",
        "safe_weight": "distance adjusted by facility_score - accident_score",
        "detour_limit_ratio": None,
    }


def build_route_explanation(
    normal: dict[str, Any],
    safe: dict[str, Any],
    comparison: dict[str, Any],
    safety_summary: dict[str, int],
    algorithm: dict[str, Any],
    accident_source: str,
    safety_source: str,
    allow_openai: bool = True,
) -> dict[str, Any]:
    explanation = template_route_explanation(
        normal, safe, comparison, safety_summary, algorithm, accident_source, safety_source
    )
    if not allow_openai or ROUTE_EXPLANATION_PROVIDER in {"template", "local", "off"}:
        return explanation

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        explanation["source"] = "template-no-openai-key"
        return explanation

    try:
        gpt_summary = call_openai_route_explanation(
            normal, safe, comparison, safety_summary, algorithm, accident_source, safety_source
        )
    except Exception as exc:
        print(f"OpenAI route explanation failed: {exc}")
        explanation["source"] = "template-openai-fallback"
        return explanation

    if gpt_summary:
        explanation["source"] = "openai"
        explanation["model"] = OPENAI_EXPLANATION_MODEL
        explanation["summary"] = gpt_summary
    return explanation


def template_route_explanation(
    normal: dict[str, Any],
    safe: dict[str, Any],
    comparison: dict[str, Any],
    safety_summary: dict[str, int],
    algorithm: dict[str, Any],
    accident_source: str,
    safety_source: str,
) -> dict[str, Any]:
    distance_delta = float(comparison.get("distance_delta_m", 0))
    distance_text = format_distance_delta_for_text(distance_delta)
    risk_reduction = comparison.get("risk_reduction_percent", 0)
    normal_risk = normal.get("risk_score", 0)
    safe_risk = safe.get("risk_score", 0)
    normal_grade = normal.get("safety_grade", "-")
    safe_grade = safe.get("safety_grade", "-")
    normal_score = normal.get("net_safety_score", 0)
    safe_score = safe.get("net_safety_score", 0)
    safe_counts = safe.get("safety_counts") or {}
    lamps = safe_counts.get("street_lamp", 0)
    cctv = safe_counts.get("cctv", 0)
    help_points = safe_counts.get("police", 0) + safe_counts.get("emergency", 0)
    crossings = safe_counts.get("crossing", 0) + safe_counts.get("traffic_signal", 0)

    if distance_delta > 0:
        summary = (
            f"안전 경로는 일반 경로보다 {distance_text} 더 이동하지만, "
            f"위험도는 {normal_risk}에서 {safe_risk}로 낮아져 위험을 {risk_reduction}% 줄였습니다. "
            f"그래서 최종 등급은 {normal_grade}에서 {safe_grade}로 개선된 경로를 선택했습니다."
        )
    else:
        summary = (
            f"안전 경로는 추가 우회 없이 일반 경로와 비슷한 거리에서 "
            f"위험도 {safe_risk}, 안전등급 {safe_grade}를 유지하는 경로로 선택됐습니다."
        )

    bullets = [
        (
            f"A*가 {algorithm.get('graph', '보행 네트워크')}에서 사고/위험 패널티와 "
            "안전시설 보너스를 함께 반영했습니다."
        ),
        (
            f"안전점수는 {normal_score}에서 {safe_score}로 "
            f"{format_signed_number(comparison.get('net_safety_delta', 0))} 변했습니다."
        ),
        (
            f"선택된 경로 주변 안전시설은 가로등 {lamps}개, CCTV {cctv}개, "
            f"안심거점 {help_points}개, 횡단/신호 시설 {crossings}개입니다."
        ),
    ]

    if safe.get("near_hotspots", 0) < normal.get("near_hotspots", 0):
        bullets.append("사고다발 권역 통과 수를 줄이는 후보가 최종 경로로 선택됐습니다.")
    elif safe_risk < normal_risk:
        bullets.append("위험권역을 완전히 벗어나지는 못했지만 중심부 통과 비용을 낮춘 후보가 선택됐습니다.")
    if comparison.get("distance_delta_percent", 0) > 120:
        bullets.append("우회율이 큰 편이라 실제 서비스에서는 사용자 설정으로 거리 우선/안전 우선을 조절할 수 있습니다.")

    return {
        "source": "template",
        "title": "안전 경로 선택 이유",
        "summary": summary,
        "bullets": bullets,
        "data_sources": {
            "accident": accident_source,
            "safety": safety_source,
        },
    }


def call_openai_route_explanation(
    normal: dict[str, Any],
    safe: dict[str, Any],
    comparison: dict[str, Any],
    safety_summary: dict[str, int],
    algorithm: dict[str, Any],
    accident_source: str,
    safety_source: str,
) -> str | None:
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    context = {
        "normal_route": explanation_route_context(normal),
        "safe_route": explanation_route_context(safe),
        "comparison": comparison,
        "algorithm": algorithm,
        "safety_summary": safety_summary,
        "data_sources": {"accident": accident_source, "safety": safety_source},
        "instruction": (
            "한국어로 2~3문장 설명을 작성한다. 실제 데이터에 없는 범죄 사실은 단정하지 말고 "
            "'위험권역' 또는 '사고다발지역'이라고 표현한다. 숫자 지표를 포함하고, 과장하지 않는다."
        ),
    }
    body = {
        "model": OPENAI_EXPLANATION_MODEL,
        "input": [
            {
                "role": "system",
                "content": (
                    "너는 지도 기반 보행 경로 추천 서비스의 결과 설명문을 작성한다. "
                    "사용자가 왜 이 안전 경로가 선택됐는지 짧고 명확하게 이해하도록 쓴다."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False),
            },
        ],
        "max_output_tokens": 260,
    }
    request = Request(
        f"{base_url}/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "safe-walk-route-demo/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=OPENAI_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    text = extract_openai_output_text(payload)
    return text.strip() if text else None


def explanation_route_context(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "distance_km": route.get("distance_km"),
        "eta_min": route.get("eta_min"),
        "risk_score": route.get("risk_score"),
        "near_hotspots": route.get("near_hotspots"),
        "safety_grade": route.get("safety_grade"),
        "net_safety_score": route.get("net_safety_score"),
        "score_breakdown": route.get("score_breakdown"),
        "safety_counts": route.get("safety_counts"),
    }


def extract_openai_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output", []) or []:
        if item.get("type") == "message":
            for content in item.get("content", []) or []:
                text = content.get("text") or content.get("output_text")
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks)


def format_distance_delta_for_text(distance_m: float) -> str:
    if abs(distance_m) >= 1000:
        return f"{round(distance_m / 1000, 2)}km"
    return f"{round(distance_m, 1)}m"


def format_signed_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    prefix = "+" if number > 0 else ""
    return f"{prefix}{round(number, 1)}"


def load_accidents() -> dict[str, Any]:
    now = time.time()
    cached = _accident_cache.get("payload")
    if cached and now < _accident_cache["expires_at"]:
        return cached

    try:
        items = fetch_koroad_accidents()
        if not items:
            raise RuntimeError("KOROAD returned no usable accident points.")
        payload = {
            "source": "koroad",
            "items": items,
            "count": len(items),
            "loaded_at": int(now),
            "fallback": False,
        }
    except Exception as exc:
        payload = {
            "source": "fallback",
            "items": FALLBACK_ACCIDENTS,
            "count": len(FALLBACK_ACCIDENTS),
            "loaded_at": int(now),
            "fallback": True,
            "notice": str(exc),
        }

    _accident_cache["payload"] = payload
    _accident_cache["expires_at"] = now + ACCIDENT_CACHE_TTL_SECONDS
    store_data_snapshot("accidents", payload)
    return payload


def load_safety_features(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    bbox = sanitize_bbox(bbox)
    cache_key = bbox_cache_key(bbox)
    cached = _safety_cache.get(cache_key)
    if cached and time.time() < cached["expires_at"]:
        return cached["payload"]

    notices = []
    items: list[dict[str, Any]] = []
    source_parts = []

    try:
        configure_osmnx()
        items.extend(fetch_osm_safety_features(bbox))
        source_parts.append("osm-overpass")
    except Exception as exc:
        notices.append(f"OSM safety features failed: {exc}")

    try:
        seoul_lights = fetch_seoul_streetlights(bbox)
        if seoul_lights:
            items.extend(seoul_lights)
            source_parts.append("seoul-open-data-streetlights")
    except Exception as exc:
        notices.append(f"Seoul streetlights failed: {exc}")

    items = dedupe_safety_features(items)
    if items:
        source = "+".join(source_parts) if source_parts else "mixed"
        fallback = False
    else:
        items = fallback_safety_features(bbox)
        source = "fallback"
        fallback = True

    payload = {
        "source": source,
        "fallback": fallback,
        "notice": "; ".join(notices) if notices else None,
        "bbox": {
            "west": bbox[0],
            "south": bbox[1],
            "east": bbox[2],
            "north": bbox[3],
        },
        "items": items,
        "summary": summarize_safety_features(items),
        "meta": SAFETY_FEATURE_META,
        "loaded_at": int(time.time()),
    }
    _safety_cache[cache_key] = {
        "expires_at": time.time() + ACCIDENT_CACHE_TTL_SECONDS,
        "payload": payload,
    }
    store_data_snapshot("safety", payload, bbox)
    return payload


def fetch_osm_safety_features(
    bbox: tuple[float, float, float, float]
) -> list[dict[str, Any]]:
    import osmnx as ox  # type: ignore

    tags = {
        "highway": ["street_lamp", "crossing", "traffic_signals"],
        "man_made": "surveillance",
        "surveillance": True,
        "amenity": ["police", "fire_station", "hospital"],
        "emergency": True,
    }
    features = ox.features_from_bbox(bbox, tags)
    if features.empty:
        return []

    items: list[dict[str, Any]] = []
    for index, row in features.reset_index().iterrows():
        category = safety_category(row)
        if not category:
            continue
        point = geometry_representative_point(row.get("geometry"))
        if not point:
            continue
        meta = SAFETY_FEATURE_META[category]
        items.append(
            {
                "id": f"osm-{category}-{index}",
                "category": category,
                "label": meta["label"],
                "lat": round(point["lat"], 7),
                "lng": round(point["lng"], 7),
                "name": first_row_text(row, "name", "operator", "description")
                or meta["label"],
                "weight": meta["weight"],
                "radius_m": meta["radius_m"],
                "color": meta["color"],
                "source": "OSM",
            }
        )
    return dedupe_safety_features(items)


def fetch_seoul_streetlights(
    bbox: tuple[float, float, float, float]
) -> list[dict[str, Any]]:
    csv_path = seoul_streetlight_csv_path()
    if not csv_path.exists():
        download_seoul_streetlight_csv(csv_path)

    raw = csv_path.read_bytes()
    text = decode_csv_bytes(raw)
    rows = csv.DictReader(text.splitlines())
    west, south, east, north = bbox
    meta = SAFETY_FEATURE_META["street_lamp"]
    items = []
    for index, row in enumerate(rows, start=1):
        lat = first_float(row, "위도", "lat", "LAT", "latitude", "Latitude")
        lng = first_float(row, "경도", "lon", "lng", "LON", "LONGITUDE", "longitude")
        if lat is None or lng is None:
            values = list(row.values())
            if len(values) >= 3:
                try:
                    lat = float(str(values[1]).strip())
                    lng = float(str(values[2]).strip())
                except ValueError:
                    continue
        if lat is None or lng is None or not (south <= lat <= north and west <= lng <= east):
            continue
        items.append(
            {
                "id": f"seoul-streetlight-{index}",
                "category": "street_lamp",
                "label": meta["label"],
                "lat": round(lat, 7),
                "lng": round(lng, 7),
                "name": first_text(row, "관리번호", "번호", "name") or "서울시 가로등",
                "weight": meta["weight"],
                "radius_m": meta["radius_m"],
                "color": meta["color"],
                "source": "Seoul Open Data",
            }
        )
    return items[:700]


def seoul_streetlight_csv_path() -> Path:
    configured = os.getenv("SEOUL_STREETLIGHT_CSV")
    if configured:
        return Path(configured)
    cache_dir = Path(os.getenv("SAFEWALK_CACHE_DIR", BASE_DIR.parent.parent / "work"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "seoul_streetlights.csv"


def download_seoul_streetlight_csv(csv_path: Path) -> None:
    url = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"
    body = urlencode(
        {"infId": "OA-22205", "seqNo": "", "seq": "1", "infSeq": "1"}
    ).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "User-Agent": "safe-walk-route-demo/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urlopen(request, timeout=30) as response:
        csv_path.write_bytes(response.read())


def decode_csv_bytes(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def safety_category(row: Any) -> str | None:
    highway = str(row.get("highway") or "").lower()
    amenity = str(row.get("amenity") or "").lower()
    man_made = str(row.get("man_made") or "").lower()
    surveillance = row.get("surveillance")
    emergency = row.get("emergency")

    if highway == "street_lamp":
        return "street_lamp"
    if man_made == "surveillance" or truthy_tag(surveillance):
        return "cctv"
    if amenity == "police":
        return "police"
    if amenity in {"fire_station", "hospital"} or truthy_tag(emergency):
        return "emergency"
    if highway == "crossing":
        return "crossing"
    if highway == "traffic_signals":
        return "traffic_signal"
    return None


def truthy_tag(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text not in {"", "nan", "no", "false", "0"}


def geometry_representative_point(geometry: Any) -> dict[str, float] | None:
    if geometry is None:
        return None
    try:
        point = geometry if geometry.geom_type == "Point" else geometry.representative_point()
        return {"lat": float(point.y), "lng": float(point.x)}
    except Exception:
        return None


def first_row_text(row: Any, *keys: str) -> str | None:
    for key in keys:
        try:
            value = row.get(key)
        except Exception:
            value = None
        if value is not None and str(value).strip() and str(value).lower() != "nan":
            return str(value).strip()
    return None


def dedupe_safety_features(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    for item in items:
        point = {"lat": item["lat"], "lng": item["lng"]}
        if all(
            item["category"] != existing["category"]
            or haversine_m(point, {"lat": existing["lat"], "lng": existing["lng"]}) > 8
            for existing in deduped
        ):
            deduped.append(item)
    return deduped[:900]


def summarize_safety_features(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = {category: 0 for category in SAFETY_FEATURE_META}
    for item in items:
        category = item.get("category")
        if category in summary:
            summary[category] += 1
    summary["total"] = len(items)
    return summary


def fallback_safety_features(
    bbox: tuple[float, float, float, float]
) -> list[dict[str, Any]]:
    west, south, east, north = bbox
    seeds = [
        ("street_lamp", 0.2, 0.18),
        ("street_lamp", 0.42, 0.72),
        ("street_lamp", 0.74, 0.33),
        ("cctv", 0.3, 0.54),
        ("cctv", 0.62, 0.28),
        ("police", 0.52, 0.5),
        ("crossing", 0.18, 0.62),
        ("crossing", 0.8, 0.44),
        ("traffic_signal", 0.66, 0.68),
        ("emergency", 0.45, 0.25),
    ]
    items = []
    for index, (category, x_ratio, y_ratio) in enumerate(seeds, start=1):
        meta = SAFETY_FEATURE_META[category]
        items.append(
            {
                "id": f"fallback-safety-{index}",
                "category": category,
                "label": meta["label"],
                "lat": round(south + (north - south) * y_ratio, 7),
                "lng": round(west + (east - west) * x_ratio, 7),
                "name": meta["label"],
                "weight": meta["weight"],
                "radius_m": meta["radius_m"],
                "color": meta["color"],
                "source": "fallback",
            }
        )
    return items


def configure_osmnx() -> None:
    import osmnx as ox  # type: ignore

    cache_dir = os.getenv("SAFEWALK_CACHE_DIR")
    if not cache_dir:
        cache_dir = str(BASE_DIR.parent.parent / "work" / "osmnx-cache")
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = cache_dir
    ox.settings.timeout = int(os.getenv("OVERPASS_TIMEOUT", "180"))
    ox.settings.overpass_rate_limit = True
    useful_node_tags = set(ox.settings.useful_tags_node)
    useful_way_tags = set(ox.settings.useful_tags_way)
    useful_node_tags.update(
        {"highway", "amenity", "man_made", "surveillance", "emergency", "lit"}
    )
    useful_way_tags.update(
        {
            "highway",
            "footway",
            "sidewalk",
            "crossing",
            "lit",
            "tunnel",
            "bridge",
            "surface",
            "access",
            "indoor",
            "name",
        }
    )
    ox.settings.useful_tags_node = list(useful_node_tags)
    ox.settings.useful_tags_way = list(useful_way_tags)


def seoul_center() -> dict[str, float]:
    return {"lat": 37.5665, "lng": 126.978}


def route_margin_m(start: dict[str, float], end: dict[str, float]) -> float:
    straight = haversine_m(start, end)
    return max(650, min(1500, straight * 0.28))


def bbox_for_points(
    points: list[dict[str, float]], margin_m: float
) -> tuple[float, float, float, float]:
    latitudes = [point["lat"] for point in points]
    longitudes = [point["lng"] for point in points]
    center_lat = sum(latitudes) / len(latitudes)
    lat_margin = margin_m / 111_320
    lng_margin = margin_m / (111_320 * max(0.2, math.cos(math.radians(center_lat))))
    return sanitize_bbox(
        (
            min(longitudes) - lng_margin,
            min(latitudes) - lat_margin,
            max(longitudes) + lng_margin,
            max(latitudes) + lat_margin,
        )
    )


def sanitize_bbox(
    bbox: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    west, south, east, north = bbox
    west, east = sorted((max(-180, west), min(180, east)))
    south, north = sorted((max(-90, south), min(90, north)))

    max_span = float(os.getenv("SAFETY_MAX_BBOX_DEG", "0.08"))
    if east - west > max_span:
        center = (east + west) / 2
        west, east = center - max_span / 2, center + max_span / 2
    if north - south > max_span:
        center = (north + south) / 2
        south, north = center - max_span / 2, center + max_span / 2
    return (round(west, 7), round(south, 7), round(east, 7), round(north, 7))


def bbox_cache_key(bbox: tuple[float, float, float, float]) -> str:
    return ",".join(f"{value:.4f}" for value in bbox)


def fetch_koroad_accidents() -> list[dict[str, Any]]:
    api_url = os.getenv("KOROAD_API_URL", "").strip()
    service_key = (
        os.getenv("KOROAD_SERVICE_KEY", "").strip()
        or os.getenv("KOROAD_API_KEY", "").strip()
    )
    if not api_url or not service_key:
        raise RuntimeError("KOROAD_API_URL and KOROAD_SERVICE_KEY are not configured.")

    params = {
        "serviceKey": service_key,
        "searchYearCd": os.getenv("KOROAD_YEAR", "2024"),
        "siDo": os.getenv("KOROAD_SIDO", "11"),
        "numOfRows": os.getenv("KOROAD_NUM_ROWS", "100"),
        "pageNo": "1",
    }
    gu_gun = os.getenv("KOROAD_GUGUN", "").strip()
    if gu_gun:
        params["guGun"] = gu_gun

    url = append_query(api_url, params)
    request = Request(url, headers={"User-Agent": "safe-walk-route-demo/1.0"})
    try:
        with urlopen(request, timeout=8) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except URLError as exc:
        raise RuntimeError(f"KOROAD request failed: {exc}") from exc

    rows = parse_remote_payload(raw)
    items = []
    for index, row in enumerate(rows, start=1):
        normalized = normalize_accident_row(row, index)
        if normalized:
            items.append(normalized)
    return items


def append_query(url: str, params: dict[str, str]) -> str:
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}{urlencode(params)}"


def parse_remote_payload(raw: str) -> list[dict[str, Any]]:
    stripped = raw.lstrip("\ufeff \n\r\t")
    if stripped.startswith("{") or stripped.startswith("["):
        data = json.loads(stripped)
        return extract_json_rows(data)

    root = ET.fromstring(stripped)
    rows = []
    for item in root.findall(".//item"):
        rows.append({child.tag: child.text for child in item})
    return rows


def extract_json_rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if not isinstance(data, dict):
        return []

    candidate_keys = ("items", "item", "body", "data", "response", "result")
    for key in candidate_keys:
        value = data.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            rows = extract_json_rows(value)
            if rows:
                return rows
    return []


def normalize_accident_row(row: dict[str, Any], index: int) -> dict[str, Any] | None:
    lat = first_float(row, "lat", "latitude", "la_crd", "laCrd", "y", "y_crd")
    lng = first_float(row, "lng", "lon", "longitude", "lo_crd", "loCrd", "x", "x_crd")
    if lat is None or lng is None or not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return None

    accidents = first_float(row, "accidents", "occrrnc_cnt", "occrrncCnt", "acc_cnt")
    casualties = first_float(row, "casualties", "caslt_cnt", "casltCnt", "injury_cnt")
    if accidents is None:
        accidents = 1
    if casualties is None:
        casualties = accidents

    name = first_text(row, "name", "spot_nm", "spotNm", "addr", "address")
    district = first_text(row, "district", "sgg_nm", "sido_sgg_nm", "guGunNm")
    radius = max(240, min(520, 230 + accidents * 8 + casualties * 3))

    return {
        "id": f"koroad-{index:03d}",
        "name": name or f"KOROAD Hotspot {index}",
        "district": district or "Seoul",
        "lat": round(lat, 7),
        "lng": round(lng, 7),
        "accidents": int(round(accidents)),
        "casualties": int(round(casualties)),
        "radius_m": int(round(radius)),
        "year": int(os.getenv("KOROAD_YEAR", "2024")),
    }


def first_float(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(str(value).replace(",", "").strip())
        except ValueError:
            continue
    return None


def first_text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def try_osmnx_routes(
    start: dict[str, float],
    end: dict[str, float],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
) -> dict[str, Any] | None:
    router_mode = os.getenv("ROUTER_MODE", DEFAULT_ROUTER_MODE).lower()
    if router_mode not in {"auto", "osmnx"}:
        return None

    try:
        import networkx as nx  # type: ignore
        import osmnx as ox  # type: ignore
    except Exception:
        return None

    try:
        configure_osmnx()
        graph = get_walk_graph(bbox_for_points([start, end], route_margin_m(start, end)))

        origin = ox.distance.nearest_nodes(graph, X=start["lng"], Y=start["lat"])
        destination = ox.distance.nearest_nodes(graph, X=end["lng"], Y=end["lat"])

        normal_nodes = nx.astar_path(
            graph,
            origin,
            destination,
            heuristic=osmnx_astar_heuristic(graph, 1.0),
            weight="length",
        )
        normal_coords = nodes_to_route_coords(graph, normal_nodes, "length", start, end)
        normal_summary = build_route_summary(
            normal_coords, accident_points, safety_features
        )

        candidates = [{"nodes": normal_nodes, "coords": normal_coords, "summary": normal_summary}]
        for accident_factor, safety_factor, avoid_core in (
            (1.0, 1.0, False),
            (1.35, 0.9, False),
            (1.75, 0.75, False),
            (2.25, 0.62, False),
            (2.6, 0.5, True),
            (3.2, 0.35, True),
        ):
            assign_osmnx_safe_weights(
                graph,
                accident_points,
                safety_features,
                accident_factor,
                safety_factor,
                avoid_core,
            )
            safe_nodes = nx.astar_path(
                graph,
                origin,
                destination,
                heuristic=osmnx_astar_heuristic(
                    graph, SAFE_ASTAR_MIN_WEIGHT_MULTIPLIER
                ),
                weight="safe_weight",
            )
            safe_coords = nodes_to_route_coords(
                graph, safe_nodes, "safe_weight", start, end
            )
            if not route_already_seen(safe_coords, candidates):
                candidates.append(
                    {
                        "nodes": safe_nodes,
                        "coords": safe_coords,
                        "summary": build_route_summary(
                            safe_coords, accident_points, safety_features
                        ),
                    }
                )

        assign_osmnx_safe_weights(
            graph,
            accident_points,
            safety_features,
            3.2,
            0.35,
            True,
        )
        add_osmnx_k_shortest_candidates(
            nx,
            graph,
            origin,
            destination,
            start,
            end,
            accident_points,
            safety_features,
            candidates,
            "safe_weight",
            SAFE_ROUTE_CANDIDATE_POOL,
        )
        for waypoint in safety_waypoint_candidates(
            start, end, normal_coords, accident_points
        ):
            waypoint_node = ox.distance.nearest_nodes(
                graph, X=waypoint["lng"], Y=waypoint["lat"]
            )
            if waypoint_node in {origin, destination}:
                continue
            try:
                first_leg = nx.astar_path(
                    graph,
                    origin,
                    waypoint_node,
                    heuristic=osmnx_astar_heuristic(
                        graph, SAFE_ASTAR_MIN_WEIGHT_MULTIPLIER
                    ),
                    weight="safe_weight",
                )
                second_leg = nx.astar_path(
                    graph,
                    waypoint_node,
                    destination,
                    heuristic=osmnx_astar_heuristic(
                        graph, SAFE_ASTAR_MIN_WEIGHT_MULTIPLIER
                    ),
                    weight="safe_weight",
                )
            except Exception:
                continue

            waypoint_nodes = first_leg + second_leg[1:]
            waypoint_coords = nodes_to_route_coords(
                graph, waypoint_nodes, "safe_weight", start, end
            )
            if not route_already_seen(waypoint_coords, candidates):
                candidates.append(
                    {
                        "nodes": waypoint_nodes,
                        "coords": waypoint_coords,
                        "summary": build_route_summary(
                            waypoint_coords, accident_points, safety_features
                        ),
                    }
                )

        safe_candidates = rank_osmnx_safe_candidates(
            candidates, normal_summary, SAFE_ROUTE_OPTION_COUNT
        )
        safe_candidate = safe_candidates[0]
        return {
            "router": "osmnx-walk-a-star",
            "algorithm": algorithm_meta("osmnx-walk-a-star"),
            "normal": normal_coords,
            "safe": safe_candidate["coords"],
            "safe_options": [candidate["coords"] for candidate in safe_candidates],
        }
    except Exception as exc:
        if router_mode == "osmnx":
            raise
        print(f"OSMnx routing failed: {exc}")
        return None


def try_osrm_routes(
    start: dict[str, float],
    end: dict[str, float],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
) -> dict[str, Any] | None:
    router_mode = os.getenv("ROUTER_MODE", DEFAULT_ROUTER_MODE).lower()
    if router_mode not in {"auto", "road", "osrm"}:
        return None

    for profile in osrm_profiles():
        normal_candidates = request_osrm_routes([start, end], profile, alternatives=True)
        if not normal_candidates:
            continue

        normal = normal_candidates[0]
        safe_candidates = normal_candidates[:]
        for waypoint in safety_waypoint_candidates(start, end, normal["coords"], accident_points):
            detours = request_osrm_routes(
                [start, waypoint, end], profile, alternatives=False
            )
            safe_candidates.extend(detours)

        ranked_safe = rank_safest_road_routes(
            normal["coords"], safe_candidates, accident_points, safety_features
        )
        safe = ranked_safe[0]
        router = f"road-osrm-{profile}"
        return {
            "router": router,
            "algorithm": algorithm_meta(router),
            "normal": normal["coords"],
            "safe": safe["coords"],
            "safe_options": [candidate["coords"] for candidate in ranked_safe],
        }

    return None


def add_osmnx_k_shortest_candidates(
    nx_module: Any,
    graph: Any,
    origin: Any,
    destination: Any,
    start: dict[str, float],
    end: dict[str, float],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    weight: str,
    limit: int,
) -> None:
    if limit <= 0:
        return

    try:
        simple_graph = nx_module.DiGraph()
        simple_graph.add_nodes_from(graph.nodes)
        for u, v, data in graph.edges(data=True):
            route_weight = float(
                data.get(weight) or data.get("length") or edge_distance(graph, u, v)
            )
            existing = simple_graph.get_edge_data(u, v)
            if existing is None or route_weight < float(existing.get("_route_weight", math.inf)):
                simple_graph.add_edge(u, v, _route_weight=route_weight)

        for index, path_nodes in enumerate(
            nx_module.shortest_simple_paths(
                simple_graph, origin, destination, weight="_route_weight"
            )
        ):
            if index >= limit:
                break
            path_nodes = list(path_nodes)
            if len(path_nodes) < 2:
                continue
            coords = nodes_to_route_coords(graph, path_nodes, weight, start, end)
            if route_already_seen(coords, candidates):
                continue
            candidates.append(
                {
                    "nodes": path_nodes,
                    "coords": coords,
                    "summary": build_route_summary(
                        coords, accident_points, safety_features
                    ),
                }
            )
    except Exception:
        return


def osrm_profiles() -> list[str]:
    configured = os.getenv("OSRM_PROFILES") or os.getenv("OSRM_PROFILE")
    if configured:
        profiles = [item.strip() for item in configured.split(",") if item.strip()]
    else:
        profiles = ["driving", "foot", "walking"]
    return list(dict.fromkeys(profiles))


def request_osrm_routes(
    points: list[dict[str, float]], profile: str, alternatives: bool
) -> list[dict[str, Any]]:
    base_url = os.getenv("OSRM_BASE_URL", DEFAULT_OSRM_BASE_URL).rstrip("/")
    coordinates = ";".join(f"{point['lng']},{point['lat']}" for point in points)
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
        "alternatives": "true" if alternatives else "false",
    }
    url = f"{base_url}/route/v1/{profile}/{coordinates}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "safe-walk-route-demo/1.0"})

    try:
        with urlopen(request, timeout=OSRM_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return []

    if payload.get("code") != "Ok":
        return []

    routes = []
    for route in payload.get("routes", []):
        geometry = route.get("geometry") or {}
        raw_coords = geometry.get("coordinates") or []
        coords = [
            {"lat": float(lat), "lng": float(lng)}
            for lng, lat in raw_coords
            if isinstance(lat, (int, float)) and isinstance(lng, (int, float))
        ]
        coords = dedupe_coords(coords)
        if len(coords) >= 2:
            routes.append(
                {
                    "coords": coords,
                    "distance_m": float(route.get("distance") or route_distance(coords)),
                    "duration_s": float(route.get("duration") or 0),
                }
            )
    return routes


def safety_waypoint_candidates(
    start: dict[str, float],
    end: dict[str, float],
    normal_coords: list[dict[str, float]],
    accident_points: list[dict[str, Any]],
) -> list[dict[str, float]]:
    origin = midpoint(start, end)
    start_xy = to_local_xy(start, origin)
    end_xy = to_local_xy(end, origin)
    vector_x = end_xy[0] - start_xy[0]
    vector_y = end_xy[1] - start_xy[1]
    vector_length = math.hypot(vector_x, vector_y)
    if vector_length == 0:
        return []

    perpendicular = (-vector_y / vector_length, vector_x / vector_length)
    relevant_spots = relevant_accident_spots(start, end, normal_coords, accident_points)
    anchors = waypoint_anchors(start, end, normal_coords, relevant_spots)
    base_detour_m = max(220, min(1400, route_distance(normal_coords) * 0.24))
    distances = [base_detour_m, base_detour_m * 1.45, base_detour_m * 2.1]

    candidates: list[dict[str, float]] = []
    for anchor in anchors:
        anchor_xy = to_local_xy(anchor, origin)
        for distance in distances:
            for direction in (-1, 1):
                candidate_xy = (
                    anchor_xy[0] + perpendicular[0] * distance * direction,
                    anchor_xy[1] + perpendicular[1] * distance * direction,
                )
                candidate = from_local_xy(candidate_xy, origin)
                if candidate_is_useful(candidate, start, end, accident_points):
                    candidates.append(candidate)

    return unique_points(candidates, min_distance_m=180)[:6]


def relevant_accident_spots(
    start: dict[str, float],
    end: dict[str, float],
    normal_coords: list[dict[str, float]],
    accident_points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    route_length = route_distance(normal_coords)
    max_distance = max(450, min(1200, route_length * 0.22))
    spots = []
    for spot in accident_points:
        distance_to_route = min_distance_to_route(spot, normal_coords)
        severity = float(spot.get("accidents", 1)) + float(spot.get("casualties", 1)) * 0.55
        if distance_to_route <= max(max_distance, float(spot.get("radius_m", 320)) * 1.8):
            spots.append(
                {
                    **spot,
                    "_distance_to_route": distance_to_route,
                    "_severity": severity,
                    "_start_distance": haversine_m(
                        start, {"lat": float(spot["lat"]), "lng": float(spot["lng"])}
                    ),
                    "_end_distance": haversine_m(
                        end, {"lat": float(spot["lat"]), "lng": float(spot["lng"])}
                    ),
                }
            )

    spots.sort(
        key=lambda item: (
            item["_distance_to_route"] - item["_severity"] * 18,
            min(item["_start_distance"], item["_end_distance"]),
        )
    )
    return spots[:4]


def waypoint_anchors(
    start: dict[str, float],
    end: dict[str, float],
    normal_coords: list[dict[str, float]],
    relevant_spots: list[dict[str, Any]],
) -> list[dict[str, float]]:
    anchors = [
        route_point_at_ratio(normal_coords, 0.35),
        route_point_at_ratio(normal_coords, 0.5),
        route_point_at_ratio(normal_coords, 0.65),
    ]
    for spot in relevant_spots[:3]:
        anchors.insert(0, {"lat": float(spot["lat"]), "lng": float(spot["lng"])})
    return unique_points(anchors, min_distance_m=220)


def candidate_is_useful(
    candidate: dict[str, float],
    start: dict[str, float],
    end: dict[str, float],
    accident_points: list[dict[str, Any]],
) -> bool:
    straight = haversine_m(start, end)
    candidate_trip = haversine_m(start, candidate) + haversine_m(candidate, end)
    if candidate_trip > max(straight * 2.6, straight + 3200):
        return False
    return risk_influence(candidate["lat"], candidate["lng"], accident_points) < 1.8


def select_safest_road_route(
    normal_coords: list[dict[str, float]],
    candidates: list[dict[str, Any]],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
) -> dict[str, Any]:
    return rank_safest_road_routes(
        normal_coords, candidates, accident_points, safety_features
    )[0]


def rank_safest_road_routes(
    normal_coords: list[dict[str, float]],
    candidates: list[dict[str, Any]],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normal_distance = max(route_distance(normal_coords), 1)
    max_reasonable_distance = max(normal_distance * 1.9, normal_distance + 1800)
    eligible = [
        candidate
        for candidate in candidates
        if route_distance(candidate["coords"]) <= max_reasonable_distance
    ]
    if not eligible:
        eligible = candidates

    ranked: list[dict[str, Any]] = []
    for candidate in sorted(
        eligible,
        key=lambda item: road_route_cost(
            item["coords"], normal_distance, accident_points, safety_features
        ),
    ):
        if not route_already_seen(candidate["coords"], ranked):
            ranked.append(candidate)
        if len(ranked) >= SAFE_ROUTE_OPTION_COUNT:
            break
    return ranked or eligible[:1]


def road_route_cost(
    coords: list[dict[str, float]],
    normal_distance: float,
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
) -> float:
    distance = route_distance(coords)
    risk = route_risk_score(coords, accident_points)
    assurance = route_assurance_score(coords, safety_features)
    near_hotspots = nearby_hotspot_count(coords, accident_points)
    net_safety = route_net_safety_score(risk, assurance, near_hotspots)
    distance_penalty = max(0, distance - normal_distance * 1.15) * 0.7
    return (
        risk * 560
        + near_hotspots * 260
        - net_safety * 42
        + distance * 0.18
        + distance_penalty
    )


def route_point_at_ratio(
    coords: list[dict[str, float]], ratio: float
) -> dict[str, float]:
    if not coords:
        return {"lat": 0, "lng": 0}
    target = route_distance(coords) * ratio
    covered = 0.0
    for a, b in zip(coords, coords[1:]):
        segment = haversine_m(a, b)
        if covered + segment >= target and segment > 0:
            local_ratio = (target - covered) / segment
            return {
                "lat": a["lat"] + (b["lat"] - a["lat"]) * local_ratio,
                "lng": a["lng"] + (b["lng"] - a["lng"]) * local_ratio,
            }
        covered += segment
    return coords[-1]


def midpoint(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    return {"lat": (a["lat"] + b["lat"]) / 2, "lng": (a["lng"] + b["lng"]) / 2}


def to_local_xy(
    point: dict[str, float], origin: dict[str, float]
) -> tuple[float, float]:
    meters_per_lat = 111_320
    meters_per_lng = meters_per_lat * math.cos(math.radians(origin["lat"]))
    return (
        (point["lng"] - origin["lng"]) * meters_per_lng,
        (point["lat"] - origin["lat"]) * meters_per_lat,
    )


def from_local_xy(
    xy: tuple[float, float], origin: dict[str, float]
) -> dict[str, float]:
    meters_per_lat = 111_320
    meters_per_lng = meters_per_lat * math.cos(math.radians(origin["lat"]))
    return {
        "lat": origin["lat"] + xy[1] / meters_per_lat,
        "lng": origin["lng"] + xy[0] / meters_per_lng,
    }


def unique_points(
    points: list[dict[str, float]], min_distance_m: float
) -> list[dict[str, float]]:
    unique = []
    for point in points:
        if all(haversine_m(point, existing) >= min_distance_m for existing in unique):
            unique.append(point)
    return unique


def get_walk_graph(bbox: tuple[float, float, float, float]) -> Any:
    import osmnx as ox  # type: ignore

    bbox = sanitize_bbox(bbox)
    cache_key = bbox_cache_key(bbox)
    cached = _walk_graph_cache.get(cache_key)
    if cached is not None:
        return cached

    graph = ox.graph_from_bbox(
        bbox, network_type="walk", simplify=True, retain_all=False
    )
    _walk_graph_cache[cache_key] = graph
    return graph


def assign_osmnx_safe_weights(
    graph: Any,
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
    accident_factor: float,
    safety_factor: float,
    avoid_core: bool = False,
) -> None:
    for u, v, key, data in graph.edges(keys=True, data=True):
        length = float(data.get("length") or edge_distance(graph, u, v))
        midpoint = edge_midpoint(graph, u, v, data)
        score = edge_safety_score(
            midpoint,
            data,
            accident_points,
            safety_features,
            accident_factor,
            safety_factor,
            avoid_core,
        )
        data["accident_score"] = score["accident"]
        data["facility_score"] = score["facility"]
        data["net_safety_score"] = score["net"]
        data["safe_weight"] = length * score["multiplier"]


def osmnx_astar_heuristic(graph: Any, min_weight_multiplier: float):
    def heuristic(node: Any, target: Any) -> float:
        return (
            haversine_m(
                {"lat": float(graph.nodes[node]["y"]), "lng": float(graph.nodes[node]["x"])},
                {"lat": float(graph.nodes[target]["y"]), "lng": float(graph.nodes[target]["x"])},
            )
            * min_weight_multiplier
        )

    return heuristic


def edge_safety_score(
    midpoint: dict[str, float],
    data: dict[str, Any],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
    accident_factor: float,
    safety_factor: float,
    avoid_core: bool = False,
) -> dict[str, float]:
    accident_influence = risk_influence(midpoint["lat"], midpoint["lng"], accident_points)
    facility_influence = safety_influence(midpoint["lat"], midpoint["lng"], safety_features)
    infra_bonus = edge_infra_bonus(data)
    infra_penalty = edge_infra_penalty(data)
    core_penalty = core_risk_penalty(accident_influence)

    accident_score = min(
        100.0,
        accident_influence * 22.0 * accident_factor * SAFE_RISK_PENALTY_WEIGHT
        + core_penalty
        + infra_penalty * 62.0,
    )
    facility_score = min(
        100.0,
        facility_influence * 16.0 * safety_factor + infra_bonus * 100.0,
    )
    if core_penalty:
        facility_score *= facility_credit_ratio_in_risk_core(accident_influence)
    net_score = max(-100.0, min(100.0, facility_score - accident_score))
    if net_score < 0:
        multiplier = 1 + abs(net_score) * SAFE_RISK_NEGATIVE_MULTIPLIER_SCALE
    else:
        multiplier = 1 - net_score * SAFE_FACILITY_POSITIVE_MULTIPLIER_SCALE
    multiplier = max(
        SAFE_ASTAR_MIN_WEIGHT_MULTIPLIER,
        min(SAFE_ASTAR_MAX_WEIGHT_MULTIPLIER, multiplier),
    )
    if avoid_core and accident_influence >= SAFE_RISK_CORE_BLOCK_THRESHOLD:
        core_strength = min(1.0, accident_influence / 3.0)
        multiplier = max(
            multiplier,
            SAFE_RISK_CORE_BLOCK_MULTIPLIER * core_strength,
        )

    return {
        "accident": round(accident_score, 3),
        "facility": round(facility_score, 3),
        "net": round(net_score, 3),
        "multiplier": multiplier,
    }


def core_risk_penalty(accident_influence: float) -> float:
    if accident_influence <= SAFE_RISK_CORE_THRESHOLD:
        return 0.0
    excess = accident_influence - SAFE_RISK_CORE_THRESHOLD
    return min(75.0, excess * SAFE_RISK_CORE_PENALTY)


def facility_credit_ratio_in_risk_core(accident_influence: float) -> float:
    excess = max(0.0, accident_influence - SAFE_RISK_CORE_THRESHOLD)
    return max(0.28, 1 / (1 + excess * 1.35))


def edge_infra_bonus(data: dict[str, Any]) -> float:
    bonus = 0.0
    highway = tag_values(data.get("highway"))
    footway = tag_values(data.get("footway"))
    sidewalk = tag_values(data.get("sidewalk"))
    lit = tag_values(data.get("lit"))
    crossing = tag_values(data.get("crossing"))

    if highway & {"footway", "pedestrian", "steps", "living_street"}:
        bonus += 0.08
    if highway & {"path", "service", "residential"}:
        bonus += 0.035
    if footway & {"sidewalk", "crossing", "access_aisle"}:
        bonus += 0.055
    if sidewalk and not (sidewalk & {"no", "none", "separate"}):
        bonus += 0.045
    if lit & {"yes", "automatic", "24/7"}:
        bonus += 0.075
    if crossing and not (crossing & {"no", "unmarked"}):
        bonus += 0.035
    return min(0.20, bonus)


def edge_infra_penalty(data: dict[str, Any]) -> float:
    penalty = 0.0
    highway = tag_values(data.get("highway"))
    lit = tag_values(data.get("lit"))
    tunnel = tag_values(data.get("tunnel"))
    access = tag_values(data.get("access"))
    indoor = tag_values(data.get("indoor"))

    if highway & {"primary", "secondary", "tertiary", "trunk"}:
        if not tag_values(data.get("sidewalk")) and not tag_values(data.get("footway")):
            penalty += 0.16
    if lit & {"no", "false"}:
        penalty += 0.18
    if tunnel & {"yes", "building_passage"}:
        penalty += 0.12
    if access & {"private", "customers", "permissive"}:
        penalty += 0.12
    if indoor & {"yes"}:
        penalty += 0.06
    return min(0.45, penalty)


def tag_values(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, list | tuple | set):
        raw_values = value
    else:
        raw_values = str(value).replace(";", ",").split(",")
    return {
        str(item).strip().lower()
        for item in raw_values
        if str(item).strip() and str(item).strip().lower() != "nan"
    }


def nodes_to_route_coords(
    graph: Any,
    nodes: list[Any],
    weight: str,
    start: dict[str, float],
    end: dict[str, float],
) -> list[dict[str, float]]:
    coords = [{"lat": start["lat"], "lng": start["lng"]}]
    for u, v in zip(nodes, nodes[1:]):
        data = best_edge_data(graph, u, v, weight)
        segment = edge_geometry_coords(graph, u, v, data)
        coords.extend(segment)
    coords.append({"lat": end["lat"], "lng": end["lng"]})
    return dedupe_coords(coords)


def best_edge_data(graph: Any, u: Any, v: Any, weight: str) -> dict[str, Any]:
    edge_options = graph.get_edge_data(u, v) or {}
    if not edge_options:
        return {}
    return min(
        edge_options.values(),
        key=lambda data: float(data.get(weight) or data.get("length") or edge_distance(graph, u, v)),
    )


def edge_geometry_coords(
    graph: Any, u: Any, v: Any, data: dict[str, Any]
) -> list[dict[str, float]]:
    geometry = data.get("geometry")
    if geometry is not None:
        try:
            coords = [{"lat": float(y), "lng": float(x)} for x, y in geometry.coords]
            u_point = {"lat": float(graph.nodes[u]["y"]), "lng": float(graph.nodes[u]["x"])}
            if coords and haversine_m(coords[-1], u_point) < haversine_m(coords[0], u_point):
                coords.reverse()
            return coords
        except Exception:
            pass
    return [
        {"lat": float(graph.nodes[u]["y"]), "lng": float(graph.nodes[u]["x"])},
        {"lat": float(graph.nodes[v]["y"]), "lng": float(graph.nodes[v]["x"])},
    ]


def route_already_seen(
    coords: list[dict[str, float]], candidates: list[dict[str, Any]]
) -> bool:
    for candidate in candidates:
        if route_too_similar(coords, candidate["coords"]):
            return True
    return False


def route_too_similar(
    coords: list[dict[str, float]], other: list[dict[str, float]]
) -> bool:
    if len(coords) < 2 or len(other) < 2:
        return False

    distance = route_distance(coords)
    other_distance = route_distance(other)
    distance_delta_ratio = abs(distance - other_distance) / max(distance, other_distance, 1)
    sample_points = sample_route_points(coords, 18)
    if not sample_points:
        return False

    nearest_distances = [min_distance_to_route(point, other) for point in sample_points]
    close_count = sum(
        1 for value in nearest_distances if value <= SAFE_ROUTE_SIMILAR_DISTANCE_M
    )
    close_ratio = close_count / len(nearest_distances)
    average_distance = sum(nearest_distances) / len(nearest_distances)

    if close_ratio >= SAFE_ROUTE_SIMILAR_RATIO:
        return True
    return (
        close_ratio >= 0.58
        and average_distance <= SAFE_ROUTE_SIMILAR_DISTANCE_M * 0.85
        and distance_delta_ratio <= 0.18
    )


def sample_route_points(
    coords: list[dict[str, float]], sample_count: int
) -> list[dict[str, float]]:
    if not coords:
        return []
    if len(coords) == 1 or sample_count <= 1:
        return [coords[0]]
    return [
        coords[min(len(coords) - 1, round((len(coords) - 1) * index / (sample_count - 1)))]
        for index in range(sample_count)
    ]


def select_osmnx_safe_candidate(
    candidates: list[dict[str, Any]], normal_summary: dict[str, Any]
) -> dict[str, Any]:
    return rank_osmnx_safe_candidates(candidates, normal_summary, 1)[0]


def rank_osmnx_safe_candidates(
    candidates: list[dict[str, Any]], normal_summary: dict[str, Any], limit: int
) -> list[dict[str, Any]]:
    normal_distance = max(float(normal_summary["distance_m"]), 1)
    normal_risk = float(normal_summary["risk_score"])
    normal_hotspots = float(normal_summary.get("near_hotspots", 0))
    short_high_risk_bonus = 0.0
    if normal_distance < 1200 and normal_risk >= SAFE_HIGH_RISK_THRESHOLD:
        short_high_risk_bonus = min(
            3.8,
            1.0 + max(0.0, normal_risk - SAFE_HIGH_RISK_THRESHOLD) / 20 * 1.8,
        )
    max_ratio = (
        SAFE_MAX_DETOUR_RATIO
        + min(0.35, normal_risk / 100 * 0.85)
        + min(0.12, normal_hotspots * 0.06)
        + short_high_risk_bonus
    )
    eligible = [
        candidate
        for candidate in candidates
        if candidate["summary"]["distance_m"] <= normal_distance * max_ratio
    ]
    pool = eligible or candidates
    if normal_risk >= SAFE_HIGH_RISK_THRESHOLD:
        target_risk = max(8.0, normal_risk * SAFE_HIGH_RISK_TARGET_RATIO)
        aggressive_pool = [
            candidate
            for candidate in candidates
            if candidate["summary"]["risk_score"] <= target_risk
            and candidate["summary"]["distance_m"]
            <= normal_distance * SAFE_HIGH_RISK_MAX_DETOUR_RATIO
        ]
        if aggressive_pool:
            pool = aggressive_pool

    ranked_by_score = sorted(
        pool,
        key=lambda candidate: osmnx_candidate_cost(
            candidate["summary"], normal_summary
        ),
    )
    ranked: list[dict[str, Any]] = []
    for candidate in ranked_by_score:
        if not route_already_seen(candidate["coords"], ranked):
            ranked.append(candidate)
        if len(ranked) >= max(1, limit):
            break
    return ranked or ranked_by_score[:1]


def osmnx_candidate_cost(
    summary: dict[str, Any], normal_summary: dict[str, Any]
) -> float:
    normal_distance = max(float(normal_summary["distance_m"]), 1)
    normal_risk = float(normal_summary["risk_score"])
    distance = float(summary["distance_m"])
    risk = float(summary["risk_score"])
    assurance = float(summary.get("assurance_score", 0))
    net_safety = float(summary.get("net_safety_score", assurance - risk))
    near_hotspots = float(summary.get("near_hotspots", 0))
    if normal_risk >= SAFE_HIGH_RISK_THRESHOLD:
        detour_ratio = distance / normal_distance
        distance_penalty = max(
            0, distance - normal_distance * SAFE_HIGH_RISK_SOFT_DETOUR_RATIO
        ) * SAFE_HIGH_RISK_DISTANCE_PENALTY
        soft_detour_penalty = (
            max(0, detour_ratio - SAFE_HIGH_RISK_SOFT_DETOUR_RATIO) ** 2
        ) * SAFE_HIGH_RISK_DETOUR_PENALTY
        hard_detour_penalty = (
            max(0, detour_ratio - SAFE_HIGH_RISK_HARD_DETOUR_RATIO) ** 2
        ) * SAFE_HIGH_RISK_DETOUR_PENALTY * 2.2
        over_detour_penalty = max(
            0, detour_ratio - SAFE_HIGH_RISK_MAX_DETOUR_RATIO
        ) * 220
        return (
            risk * 13.0
            + near_hotspots * 10.0
            - net_safety * 0.25
            + distance_penalty
            + soft_detour_penalty
            + hard_detour_penalty
            + over_detour_penalty
        )
    distance_penalty = max(0, distance - normal_distance * 1.12) * 0.025
    over_detour_penalty = max(0, distance / normal_distance - 1.35) * 95
    return (
        risk * 6.4
        + near_hotspots * 8.0
        - net_safety * 0.55
        + distance_penalty
        + over_detour_penalty
    )


def edge_distance(graph: Any, u: Any, v: Any) -> float:
    return haversine_m(
        {"lat": graph.nodes[u]["y"], "lng": graph.nodes[u]["x"]},
        {"lat": graph.nodes[v]["y"], "lng": graph.nodes[v]["x"]},
    )


def edge_midpoint(graph: Any, u: Any, v: Any, data: dict[str, Any]) -> dict[str, float]:
    geometry = data.get("geometry")
    if geometry is not None:
        try:
            point = geometry.interpolate(0.5, normalized=True)
            return {"lat": float(point.y), "lng": float(point.x)}
        except Exception:
            pass
    return {
        "lat": (float(graph.nodes[u]["y"]) + float(graph.nodes[v]["y"])) / 2,
        "lng": (float(graph.nodes[u]["x"]) + float(graph.nodes[v]["x"])) / 2,
    }


def nodes_to_coords(
    graph: Any,
    nodes: list[Any],
    start: dict[str, float],
    end: dict[str, float],
) -> list[dict[str, float]]:
    coords = [{"lat": start["lat"], "lng": start["lng"]}]
    coords.extend({"lat": float(graph.nodes[node]["y"]), "lng": float(graph.nodes[node]["x"])} for node in nodes)
    coords.append({"lat": end["lat"], "lng": end["lng"]})
    return dedupe_coords(coords)


def calculate_grid_route(
    start: dict[str, float],
    end: dict[str, float],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
    safe: bool,
) -> list[dict[str, float]]:
    grid = build_grid(start, end)
    start_index = nearest_grid_index(start, grid)
    end_index = nearest_grid_index(end, grid)
    previous = astar_grid(
        grid, start_index, end_index, accident_points, safety_features, safe
    )

    path_indices = []
    cursor = end_index
    while cursor is not None:
        path_indices.append(cursor)
        cursor = previous.get(cursor)
    path_indices.reverse()

    coords = [{"lat": start["lat"], "lng": start["lng"]}]
    coords.extend(grid["points"][index] for index in path_indices)
    coords.append({"lat": end["lat"], "lng": end["lng"]})
    return smooth_grid_route(dedupe_coords(coords))


def build_grid(start: dict[str, float], end: dict[str, float]) -> dict[str, Any]:
    lat_min = min(start["lat"], end["lat"])
    lat_max = max(start["lat"], end["lat"])
    lng_min = min(start["lng"], end["lng"])
    lng_max = max(start["lng"], end["lng"])

    diagonal_m = max(haversine_m(start, end), 600)
    margin_deg = max(0.0045, min(0.025, diagonal_m / 111_000 * 0.35))
    lat_min -= margin_deg
    lat_max += margin_deg
    lng_min -= margin_deg
    lng_max += margin_deg

    points = []
    for row in range(GRID_SIZE):
        lat = lat_min + (lat_max - lat_min) * row / (GRID_SIZE - 1)
        for col in range(GRID_SIZE):
            lng = lng_min + (lng_max - lng_min) * col / (GRID_SIZE - 1)
            points.append({"lat": lat, "lng": lng})
    return {
        "points": points,
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lng_min": lng_min,
        "lng_max": lng_max,
    }


def nearest_grid_index(point: dict[str, float], grid: dict[str, Any]) -> int:
    return min(
        range(len(grid["points"])),
        key=lambda index: haversine_m(point, grid["points"][index]),
    )


def astar_grid(
    grid: dict[str, Any],
    start_index: int,
    end_index: int,
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
    safe: bool,
) -> dict[int, int | None]:
    distances: dict[int, float] = {start_index: 0.0}
    previous: dict[int, int | None] = {start_index: None}
    queue: list[tuple[float, int]] = [
        (
            grid_heuristic(
                grid["points"][start_index], grid["points"][end_index], safe
            ),
            start_index,
        )
    ]

    while queue:
        _, current = heappop(queue)
        if current == end_index:
            break
        current_distance = distances.get(current, math.inf)

        for neighbor in grid_neighbors(current):
            current_point = grid["points"][current]
            neighbor_point = grid["points"][neighbor]
            edge_length = haversine_m(current_point, neighbor_point)
            midpoint = {
                "lat": (current_point["lat"] + neighbor_point["lat"]) / 2,
                "lng": (current_point["lng"] + neighbor_point["lng"]) / 2,
            }
            weight = edge_length
            if safe:
                weight = grid_safe_edge_weight(
                    edge_length, midpoint, accident_points, safety_features
                )

            candidate = current_distance + weight
            if candidate < distances.get(neighbor, math.inf):
                distances[neighbor] = candidate
                previous[neighbor] = current
                priority = candidate + grid_heuristic(
                    neighbor_point, grid["points"][end_index], safe
                )
                heappush(queue, (priority, neighbor))

    return previous


def grid_heuristic(
    point: dict[str, float], end: dict[str, float], safe: bool
) -> float:
    multiplier = SAFE_ASTAR_MIN_WEIGHT_MULTIPLIER if safe else 1.0
    return haversine_m(point, end) * multiplier


def grid_safe_edge_weight(
    edge_length: float,
    midpoint: dict[str, float],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]],
) -> float:
    accident_influence = risk_influence(midpoint["lat"], midpoint["lng"], accident_points)
    accident_score = (
        accident_influence * 22 * SAFE_RISK_PENALTY_WEIGHT
        + core_risk_penalty(accident_influence)
    )
    facility_score = safety_influence(midpoint["lat"], midpoint["lng"], safety_features) * 16
    if accident_influence > SAFE_RISK_CORE_THRESHOLD:
        facility_score *= facility_credit_ratio_in_risk_core(accident_influence)
    net_score = max(-100.0, min(100.0, facility_score - accident_score))
    if net_score < 0:
        multiplier = 1 + abs(net_score) * SAFE_RISK_NEGATIVE_MULTIPLIER_SCALE
    else:
        multiplier = 1 - net_score * SAFE_FACILITY_POSITIVE_MULTIPLIER_SCALE
    multiplier = max(
        SAFE_ASTAR_MIN_WEIGHT_MULTIPLIER,
        min(SAFE_ASTAR_MAX_WEIGHT_MULTIPLIER, multiplier),
    )
    return edge_length * multiplier


def grid_neighbors(index: int) -> list[int]:
    row, col = divmod(index, GRID_SIZE)
    neighbors = []
    for row_delta in (-1, 0, 1):
        for col_delta in (-1, 0, 1):
            if row_delta == 0 and col_delta == 0:
                continue
            next_row = row + row_delta
            next_col = col + col_delta
            if 0 <= next_row < GRID_SIZE and 0 <= next_col < GRID_SIZE:
                neighbors.append(next_row * GRID_SIZE + next_col)
    return neighbors


def smooth_grid_route(coords: list[dict[str, float]]) -> list[dict[str, float]]:
    if len(coords) <= 4:
        return coords

    smoothed = [coords[0]]
    for index in range(1, len(coords) - 1):
        previous = smoothed[-1]
        current = coords[index]
        following = coords[index + 1]
        if is_collinear(previous, current, following):
            continue
        smoothed.append(current)
    smoothed.append(coords[-1])
    return smoothed


def is_collinear(
    a: dict[str, float], b: dict[str, float], c: dict[str, float], tolerance: float = 1e-8
) -> bool:
    return abs((b["lat"] - a["lat"]) * (c["lng"] - b["lng"]) - (b["lng"] - a["lng"]) * (c["lat"] - b["lat"])) < tolerance


def build_route_summary(
    coords: list[dict[str, float]],
    accident_points: list[dict[str, Any]],
    safety_features: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    safety_features = safety_features or []
    distance = route_distance(coords)
    risk_score = route_risk_score(coords, accident_points)
    near_hotspots = nearby_hotspot_count(coords, accident_points)
    safety_counts = nearby_safety_counts(coords, safety_features)
    assurance_score = route_assurance_score(coords, safety_features)
    net_safety_score = route_net_safety_score(
        risk_score, assurance_score, near_hotspots
    )
    accident_penalty_score = route_accident_penalty_score(risk_score, near_hotspots)
    return {
        "coordinates": [[round(point["lat"], 7), round(point["lng"], 7)] for point in coords],
        "distance_m": round(distance, 1),
        "distance_km": round(distance / 1000, 2),
        "eta_min": max(1, round(distance / WALKING_SPEED_M_PER_MIN)),
        "risk_score": risk_score,
        "near_hotspots": near_hotspots,
        "assurance_score": assurance_score,
        "net_safety_score": net_safety_score,
        "safety_grade": route_safety_grade(net_safety_score),
        "score_breakdown": {
            "facility_bonus": assurance_score,
            "accident_penalty": accident_penalty_score,
            "net_safety": net_safety_score,
        },
        "safety_counts": safety_counts,
    }


def route_distance(coords: list[dict[str, float]]) -> float:
    return sum(haversine_m(a, b) for a, b in zip(coords, coords[1:]))


def route_risk_score(
    coords: list[dict[str, float]], accident_points: list[dict[str, Any]]
) -> float:
    if len(coords) < 2:
        return 0.0
    weighted_risk = 0.0
    total_distance = 0.0
    for a, b in zip(coords, coords[1:]):
        segment_distance = haversine_m(a, b)
        samples = max(2, min(12, math.ceil(segment_distance / 90)))
        for step in range(samples):
            ratio = step / (samples - 1)
            lat = a["lat"] + (b["lat"] - a["lat"]) * ratio
            lng = a["lng"] + (b["lng"] - a["lng"]) * ratio
            weighted_risk += risk_influence(lat, lng, accident_points) * segment_distance / samples
        total_distance += segment_distance

    if total_distance == 0:
        return 0.0
    return round(min(100, (weighted_risk / total_distance) * 26), 1)


def nearby_hotspot_count(
    coords: list[dict[str, float]], accident_points: list[dict[str, Any]]
) -> int:
    count = 0
    for spot in accident_points:
        threshold = max(260, float(spot.get("radius_m", 300)) * 1.15)
        if min_distance_to_route(spot, coords) <= threshold:
            count += 1
    return count


def route_assurance_score(
    coords: list[dict[str, float]], safety_features: list[dict[str, Any]]
) -> float:
    if len(coords) < 2 or not safety_features:
        return 0.0
    weighted_safety = 0.0
    total_distance = 0.0
    for a, b in zip(coords, coords[1:]):
        segment_distance = haversine_m(a, b)
        samples = max(2, min(12, math.ceil(segment_distance / 90)))
        for step in range(samples):
            ratio = step / (samples - 1)
            lat = a["lat"] + (b["lat"] - a["lat"]) * ratio
            lng = a["lng"] + (b["lng"] - a["lng"]) * ratio
            weighted_safety += (
                safety_influence(lat, lng, safety_features) * segment_distance / samples
            )
        total_distance += segment_distance
    if total_distance == 0:
        return 0.0
    return round(min(100, (weighted_safety / total_distance) * 30), 1)


def route_net_safety_score(
    risk_score: float, assurance_score: float, near_hotspots: int
) -> float:
    score = assurance_score - route_accident_penalty_score(risk_score, near_hotspots)
    return round(max(-100, min(100, score)), 1)


def route_accident_penalty_score(risk_score: float, near_hotspots: int) -> float:
    return round(min(100, risk_score + near_hotspots * 2.5), 1)


def route_safety_grade(net_safety_score: float) -> str:
    if net_safety_score >= 35:
        return "A"
    if net_safety_score >= 20:
        return "B"
    if net_safety_score >= 5:
        return "C"
    if net_safety_score >= -10:
        return "D"
    return "E"


def nearby_safety_counts(
    coords: list[dict[str, float]], safety_features: list[dict[str, Any]]
) -> dict[str, int]:
    counts = {category: 0 for category in SAFETY_FEATURE_META}
    for feature in safety_features:
        category = feature.get("category")
        if category not in counts:
            continue
        threshold = max(35, float(feature.get("radius_m", 70)) * 1.25)
        if min_distance_to_route(feature, coords) <= threshold:
            counts[category] += 1
    counts["total"] = sum(counts.values())
    return counts


def min_distance_to_route(
    spot: dict[str, Any], coords: list[dict[str, float]]
) -> float:
    if not coords:
        return math.inf
    return min(
        haversine_m({"lat": float(spot["lat"]), "lng": float(spot["lng"])}, point)
        for point in coords
    )


def risk_influence(lat: float, lng: float, accident_points: list[dict[str, Any]]) -> float:
    influence = 0.0
    for spot in accident_points:
        distance = haversine_m(
            {"lat": lat, "lng": lng},
            {"lat": float(spot["lat"]), "lng": float(spot["lng"])},
        )
        radius = float(spot.get("radius_m", 320))
        accidents = float(spot.get("accidents", 1))
        casualties = float(spot.get("casualties", accidents))
        severity = min(3.0, 0.18 * accidents + 0.10 * casualties)
        influence += severity * math.exp(-((distance / radius) ** 2))
    return influence


def safety_influence(
    lat: float, lng: float, safety_features: list[dict[str, Any]]
) -> float:
    influence = 0.0
    point = {"lat": lat, "lng": lng}
    for feature in safety_features:
        distance = haversine_m(
            point, {"lat": float(feature["lat"]), "lng": float(feature["lng"])}
        )
        radius = float(feature.get("radius_m", 70))
        weight = float(feature.get("weight", 0.5))
        influence += weight * math.exp(-((distance / radius) ** 2))
    return influence


def calculate_reduction(normal: float, safe: float) -> float:
    if normal <= 0:
        return 0.0
    return round(max(0, (normal - safe) / normal * 100), 1)


def dedupe_coords(coords: list[dict[str, float]]) -> list[dict[str, float]]:
    deduped = []
    for point in coords:
        if not deduped:
            deduped.append(point)
            continue
        previous = deduped[-1]
        if haversine_m(previous, point) > 1:
            deduped.append(point)
    return deduped


def haversine_m(a: dict[str, float], b: dict[str, float]) -> float:
    earth_radius_m = 6_371_000
    lat1 = math.radians(a["lat"])
    lat2 = math.radians(b["lat"])
    delta_lat = math.radians(b["lat"] - a["lat"])
    delta_lng = math.radians(b["lng"] - a["lng"])
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    return earth_radius_m * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
