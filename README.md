# SafeWalk Seoul

FastAPI, Leaflet.js, Leaflet heatmap, OSMnx, NetworkX, and safety-weighted pedestrian routing for a safe walking route prototype.

Routes use the OSM walking network first, so they can follow footways, alleys, crossings, stairs, pedestrian streets, and small local roads. If OSMnx or Overpass is not reachable, the API falls back to OSRM route candidates and then to a local A* grid fallback as a last resort.

## Run

```powershell
cd C:\Users\hno13\Documents\Codex\2026-06-11\html-css-javascript-leaflet-js-10\outputs\safe-walk-route
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip install -r requirements-osmnx.txt
.\.venv\Scripts\python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## Routing modes

Default mode:

```powershell
$env:ROUTER_MODE="auto"
$env:SAFE_MAX_DETOUR_RATIO="1.25"
$env:OVERPASS_TIMEOUT="180"
$env:OSRM_BASE_URL="https://router.project-osrm.org"
$env:OSRM_PROFILES="driving,foot,walking"
```

`auto` tries OSMnx walking-network routing first, then OSRM route candidates, and finally the A* grid fallback. In OSMnx mode, both the normal and safe routes are searched with A*. The safe A* edge cost combines negative accident scores and positive safety-facility scores, then still filters candidates with `SAFE_MAX_DETOUR_RATIO` so the route does not detour too much.

For a production pedestrian service, prefer one of these:

- Run your own OSRM/Valhalla/GraphHopper server with a walking profile and set `OSRM_BASE_URL` plus `OSRM_PROFILES=foot`.
- Keep `ROUTER_MODE=auto` with OSMnx installed so the backend computes on the local OSM walking network.
- Tune `SAFE_MAX_DETOUR_RATIO`; lower values reduce detours, higher values avoid more risk.

## Optional OSMnx mode

Install the heavier geospatial stack:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-osmnx.txt
$env:ROUTER_MODE="auto"
.\.venv\Scripts\python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

`ROUTER_MODE=auto` tries OSMnx first, then OSRM, and finally falls back to the built-in A* risk grid if OSM data download or package loading fails.

## Safety data

The route weight combines:

- KOROAD pedestrian accident hot spots
- OSM/Overpass safety features: CCTV/surveillance, police, emergency facilities, crossings, traffic signals
- Seoul Open Data streetlight CSV: downloaded automatically to `SAFEWALK_CACHE_DIR` unless `SEOUL_STREETLIGHT_CSV` points to a local file
- OSM road tags: `lit`, `sidewalk`, `footway`, `crossing`, `tunnel`, major-road class, and access tags

Safe A* scoring:

- Accident hot spot influence becomes an accident penalty score.
- Street lights, CCTV, police, emergency facilities, crossings, traffic signals, and pedestrian-friendly OSM tags become facility bonus scores.
- The edge net score is `facility_score - accident_score`.
- Higher net scores lower the A* edge cost, and lower net scores raise it.
- High-risk core areas get an additional penalty, and safety-facility bonuses are discounted inside that core so a CCTV or street light cannot fully cancel a crime/accident risk zone.
- The backend also creates stronger A* candidates that nearly block high-risk core edges. If an alternate pedestrian path exists, this makes the safe route move around the risky center instead of crossing it.
- If the normal route risk is high, the selector first looks for candidates that reduce risk below a target ratio, even when that means a much larger detour on short trips.
- High-risk candidates also receive nonlinear detour penalties, so a route that avoids risk but becomes excessively long can lose to a shorter route with acceptable residual risk.
- The safe route selector keeps the best `SAFE_ROUTE_OPTION_COUNT` candidates. On OSMnx it also samples K-shortest safe-weight pedestrian paths, so the UI can offer up to three real street-following safe alternatives.
- Alternative routes must be meaningfully different. If most sampled points of a candidate stay within `SAFE_ROUTE_SIMILAR_DISTANCE_M` of an already selected option, it is treated as the same route and removed from the option list.
- The route summary returns `score_breakdown`, `net_safety_score`, and `safety_grade` so the recommendation is explainable in the UI and API response.

Useful variables:

```powershell
$env:SAFEWALK_CACHE_DIR="C:\path\to\cache"
$env:SEOUL_STREETLIGHT_CSV="C:\path\to\서울시 가로등 위치 정보.csv"
$env:SAFE_ROUTE_OPTION_COUNT="3"
$env:SAFE_ROUTE_CANDIDATE_POOL="14"
$env:SAFE_ROUTE_SIMILAR_DISTANCE_M="70"
$env:SAFE_ROUTE_SIMILAR_RATIO="0.72"
$env:SAFE_RISK_PENALTY_WEIGHT="2.8"
$env:SAFE_RISK_CORE_THRESHOLD="0.7"
$env:SAFE_RISK_CORE_PENALTY="90"
$env:SAFE_RISK_CORE_BLOCK_THRESHOLD="0.85"
$env:SAFE_RISK_CORE_BLOCK_MULTIPLIER="35"
$env:SAFE_HIGH_RISK_TARGET_RATIO="0.45"
$env:SAFE_HIGH_RISK_MAX_DETOUR_RATIO="5.0"
$env:SAFE_HIGH_RISK_SOFT_DETOUR_RATIO="2.2"
$env:SAFE_HIGH_RISK_HARD_DETOUR_RATIO="3.0"
$env:SAFE_HIGH_RISK_DETOUR_PENALTY="170"
$env:SAFE_HIGH_RISK_DISTANCE_PENALTY="0.07"
```

Increase `SAFE_RISK_PENALTY_WEIGHT`, `SAFE_RISK_CORE_PENALTY`, or `SAFE_RISK_CORE_BLOCK_MULTIPLIER` when crime or accident hot spots should be avoided more aggressively. Lower `SAFE_RISK_CORE_THRESHOLD`, `SAFE_RISK_CORE_BLOCK_THRESHOLD`, or `SAFE_HIGH_RISK_TARGET_RATIO` if the route still passes too close to the center of a risk zone. Increase `SAFE_HIGH_RISK_DETOUR_PENALTY` or lower `SAFE_HIGH_RISK_SOFT_DETOUR_RATIO` when safe routes become too long.

## Route explanation

Every `POST /api/routes` response includes an `explanation` object:

- `summary`: short Korean explanation of why the safe route was selected
- `bullets`: metric-based reasons such as risk reduction, detour, safety score, and nearby facilities
- `source`: `template`, `template-no-openai-key`, `template-openai-fallback`, or `openai`

The app works without an AI key by using a local template. To use GPT for a more natural explanation:

```powershell
$env:ROUTE_EXPLANATION_PROVIDER="auto"
$env:OPENAI_API_KEY="sk-..."
$env:OPENAI_EXPLANATION_MODEL="gpt-5.5"
```

Set `ROUTE_EXPLANATION_PROVIDER=template` to disable external model calls during demos.

## KOROAD data

Create environment variables from `.env.example`:

```powershell
$env:KOROAD_API_URL="https://..."
$env:KOROAD_SERVICE_KEY="..."
$env:KOROAD_YEAR="2024"
```

If the KOROAD request fails or returns no usable WGS84 coordinates, the API returns Seoul fallback hot spots so the frontend and route comparison keep working.

## API

- `GET /api/accidents`: hot spot data for heatmap and point layers
- `GET /api/safety?west=...&south=...&east=...&north=...`: safety facility points in a bbox
- `POST /api/routes`: route comparison

```json
{
  "start": { "lat": 37.4922, "lng": 127.0152 },
  "end": { "lat": 37.5056, "lng": 127.0476 }
}
```

The route response includes:

- `router`: selected routing backend, usually `osmnx-walk-a-star`
- `algorithm`: graph, A* weighting, and detour-limit metadata
- `normal` / `safe`: distance, ETA, risk score, safety grade, net safety score, score breakdown, and coordinates
- `safe_options`: up to three ranked safe route candidates, each with its own metrics and local explanation for the selection UI
- `comparison`: distance delta, risk reduction, and net safety improvement
- `explanation`: Korean explanation text and supporting bullets for the selected safe route
