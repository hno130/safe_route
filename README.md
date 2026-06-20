# SafeWalk Seoul

서울 보행자를 위한 **안전 도보 경로 추천 시스템**입니다.

출발지와 도착지를 선택하면 일반 최단 경로와 안전 가중치가 반영된 경로를 비교하고, 사고다발지역을 피하면서 가로등, CCTV, 횡단보도, 경찰서, 비상시설 같은 안전 요소를 더 많이 지나는 도보 경로를 추천합니다.

## 주요 기능

- Leaflet.js 기반 지도 UI
- 서울 보행자 사고다발지역 heatmap 시각화
- 지도 클릭으로 출발지와 도착지 선택
- 일반 경로와 안전 경로 비교
- 안전 경로 후보 최대 3개 제공
- 후보 경로 선택 UI 제공
- 선택한 경로를 추천한 이유 설명
- 사고 위험도, 안전점수, 우회 거리, 예상 시간 표시
- OSM 보행 도로망 기반 A* 경로 탐색
- API 실패 시 샘플 데이터 fallback 지원

## 기술 스택

### 백엔드

- FastAPI
- Python
- OSMnx
- NetworkX
- KOROAD 보행자 교통사고 다발지역 API
- OSRM fallback

### 프론트엔드

- HTML
- CSS
- JavaScript
- Leaflet.js
- Leaflet heatmap

## 실행 방법

```powershell
cd 
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip install -r requirements-osmnx.txt
.\.venv\Scripts\python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

브라우저에서 아래 주소로 접속합니다.

```text
http://127.0.0.1:8000
```

## 사용 방법

1. 지도를 클릭해서 출발지를 선택합니다.
2. 두 번째 클릭으로 도착지를 선택합니다.
3. 서버가 일반 경로와 안전 경로 후보를 계산합니다.
4. 지도에는 일반 경로와 안전 경로가 표시됩니다.
5. 우측 패널에서 안전 경로 후보 3개 중 하나를 선택할 수 있습니다.
6. 선택한 경로의 거리, 위험도, 안전점수, 추천 이유를 확인합니다.

## 경로 계산 방식

기본 모드는 `ROUTER_MODE=auto`입니다.

```powershell
$env:ROUTER_MODE="auto"
$env:SAFE_MAX_DETOUR_RATIO="1.25"
$env:OVERPASS_TIMEOUT="180"
$env:OSRM_BASE_URL="https://router.project-osrm.org"
$env:OSRM_PROFILES="driving,foot,walking"
```

`auto` 모드는 다음 순서로 경로 계산을 시도합니다.

1. OSMnx 보행 도로망 기반 A* 탐색
2. OSRM 경로 후보 기반 fallback
3. 로컬 A* grid fallback

OSMnx를 사용할 수 있으면 실제 보행자 도로망을 기준으로 경로를 계산합니다. 따라서 보행자 전용 도로, 골목, 계단, 횡단보도, 생활도로, 작은 도로 등을 따라가는 경로를 만들 수 있습니다.

## 안전 점수 계산

안전 경로는 단순히 가장 짧은 길을 고르지 않습니다. 각 도로 구간에 안전 가중치를 부여한 뒤 A* 알고리즘으로 경로를 탐색합니다.

### 위험 요소

- KOROAD 보행자 사고다발지역
- 사고건수
- 사상자수
- 사고지점 반경
- 위험 구역 중심부 통과 여부
- 어두운 길, 터널, 보행 인프라가 부족한 도로 태그

### 안전 요소

- 가로등
- CCTV
- 경찰서
- 비상시설
- 횡단보도
- 신호등
- 보도 및 보행자 전용 도로 태그
- 조명 정보가 있는 OSM 도로 태그

### 점수 구조

```text
최종 안전 점수 = 안전시설 보너스 - 사고위험 패널티
```

위험한 구간은 도로 비용이 증가하고, 안전시설이 많은 구간은 도로 비용이 감소합니다. 단, 사고다발지역 중심부에서는 CCTV나 가로등이 있어도 위험 점수를 완전히 상쇄하지 못하도록 보정했습니다.

## 안전 경로 후보 3개

서비스는 안전 경로를 하나만 보여주지 않고, 상위 후보를 최대 3개까지 제공합니다.

후보 경로는 다음 기준으로 정렬됩니다.

- 사고 위험도가 낮은가
- 사고지점 근처를 덜 지나가는가
- 안전시설을 더 많이 지나는가
- 우회 거리가 지나치게 길지 않은가
- 기존 후보와 충분히 다른 경로인가

비슷한 경로가 여러 번 나오지 않도록 후보 간 유사도도 검사합니다. 예를 들어 바로 길 건너편으로만 이동한 경로처럼 거의 같은 경로는 다른 후보로 인정하지 않습니다.

기본 기준은 다음과 같습니다.

```powershell
$env:SAFE_ROUTE_OPTION_COUNT="3"
$env:SAFE_ROUTE_CANDIDATE_POOL="14"
$env:SAFE_ROUTE_SIMILAR_DISTANCE_M="70"
$env:SAFE_ROUTE_SIMILAR_RATIO="0.72"
```

`SAFE_ROUTE_SIMILAR_DISTANCE_M`는 두 경로가 얼마나 가까우면 비슷하다고 볼지 정하는 값입니다. 기본값은 70m입니다.

`SAFE_ROUTE_SIMILAR_RATIO`는 샘플링한 경로 지점 중 몇 퍼센트 이상이 가까우면 같은 경로로 볼지 정하는 값입니다. 기본값은 0.72입니다.

## 주요 환경 변수

```powershell
$env:SAFEWALK_CACHE_DIR="C:\path\to\cache"
$env:SEOUL_STREETLIGHT_CSV="C:\path\to\서울시 가로등 위치 정보.csv"
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

범죄 또는 사고 위험 지역을 더 강하게 피하고 싶다면 아래 값을 높이면 됩니다.

- `SAFE_RISK_PENALTY_WEIGHT`
- `SAFE_RISK_CORE_PENALTY`
- `SAFE_RISK_CORE_BLOCK_MULTIPLIER`

안전 경로가 너무 멀리 돌아간다면 아래 값을 조정하면 됩니다.

- `SAFE_HIGH_RISK_DETOUR_PENALTY` 증가
- `SAFE_HIGH_RISK_SOFT_DETOUR_RATIO` 감소
- `SAFE_MAX_DETOUR_RATIO` 감소

## KOROAD 사고 데이터

KOROAD API를 사용하려면 `.env.example`을 참고해서 환경 변수를 설정합니다.

```powershell
$env:KOROAD_API_URL="https://..."
$env:KOROAD_SERVICE_KEY="..."
$env:KOROAD_YEAR="2024"
```

KOROAD API 호출이 실패하거나 좌표 데이터가 부족한 경우에도 데모가 멈추지 않도록 서울 샘플 사고 데이터를 fallback으로 사용합니다.

## 경로 설명 기능

`POST /api/routes` 응답에는 `explanation` 객체가 포함됩니다.

```json
{
  "source": "template",
  "summary": "안전 경로를 선택한 이유",
  "bullets": ["위험 절감", "안전시설", "우회 거리"]
}
```

OpenAI API 키가 없어도 로컬 템플릿으로 설명이 생성됩니다. GPT 기반 설명을 사용하려면 아래 환경 변수를 설정합니다.

```powershell
$env:ROUTE_EXPLANATION_PROVIDER="auto"
$env:OPENAI_API_KEY="sk-..."
$env:OPENAI_EXPLANATION_MODEL="gpt-5.5"
```

외부 모델 호출 없이 시연하려면 다음처럼 설정합니다.

```powershell
$env:ROUTE_EXPLANATION_PROVIDER="template"
```

## API

### 사고 데이터

```http
GET /api/accidents
```

지도 heatmap과 사고지점 레이어에 사용할 사고다발지역 데이터를 반환합니다.

### 안전시설 데이터

```http
GET /api/safety?west=...&south=...&east=...&north=...
```

현재 지도 영역 안의 가로등, CCTV, 경찰서, 비상시설, 횡단보도, 신호등 데이터를 반환합니다.

### 경로 계산

```http
POST /api/routes
```

요청 예시:

```json
{
  "start": { "lat": 37.4922, "lng": 127.0152 },
  "end": { "lat": 37.5056, "lng": 127.0476 }
}
```

응답에는 다음 정보가 포함됩니다.

- `router`: 사용된 경로 계산 방식
- `algorithm`: A* 가중치와 그래프 정보
- `normal`: 일반 경로 정보
- `safe`: 기본 추천 안전 경로 정보
- `safe_options`: 선택 가능한 안전 경로 후보 목록
- `comparison`: 일반 경로와 안전 경로의 비교 결과
- `explanation`: 추천 이유 설명

## 프로젝트 구조

```text
safe-walk-route/
├─ app.py
├─ requirements.txt
├─ requirements-osmnx.txt
├─ .env.example
├─ README.md
└─ static/
   ├─ index.html
   ├─ app.js
   └─ styles.css
```

## 발표용 요약

이 프로젝트는 실제 사고 데이터를 지도 위에 시각화하고, 보행자가 출발지와 도착지를 선택하면 일반 최단 경로와 안전 가중치 기반 경로를 비교해 보여주는 프로토타입입니다.

단순 최단 경로가 아니라 사고다발지역은 피하고, 가로등과 CCTV 같은 안전시설은 선호하도록 A* 알고리즘의 도로 비용을 조정했습니다. 또한 안전 경로 후보를 최대 3개까지 제공하고, 각 후보를 선택한 이유를 UI에서 확인할 수 있도록 구성했습니다.
