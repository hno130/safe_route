const seoulCenter = [37.5665, 126.978];
const safeRouteSimilarDistanceM = 70;
const safeRouteSimilarRatio = 0.72;

const state = {
  start: null,
  end: null,
  accidents: [],
  safety: [],
  heatLayer: null,
  pointLayer: null,
  safetyLayer: null,
  routeResult: null,
  safeOptions: [],
  selectedOptionIndex: 0,
  normalRoute: null,
  safeRoute: null,
  safeOptionRoutes: [],
  startMarker: null,
  endMarker: null,
  loadingRoute: false,
};

const map = L.map("map", {
  zoomControl: false,
  preferCanvas: true,
}).setView(seoulCenter, 12);
window.safeWalkMap = map;

const mapElement = document.querySelector("#map");
if (window.ResizeObserver && mapElement) {
  const mapResizeObserver = new ResizeObserver(() => {
    requestAnimationFrame(() => map.invalidateSize({ animate: false }));
  });
  mapResizeObserver.observe(mapElement);
}

L.control.zoom({ position: "bottomleft" }).addTo(map);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
}).addTo(map);

const els = {
  startText: document.querySelector("#startText"),
  endText: document.querySelector("#endText"),
  message: document.querySelector("#message"),
  dataSource: document.querySelector("#dataSource"),
  routerMode: document.querySelector("#routerMode"),
  riskReduction: document.querySelector("#riskReduction"),
  safetyDelta: document.querySelector("#safetyDelta"),
  detourDelta: document.querySelector("#detourDelta"),
  normalDistance: document.querySelector("#normalDistance"),
  normalRisk: document.querySelector("#normalRisk"),
  normalHotspots: document.querySelector("#normalHotspots"),
  normalEta: document.querySelector("#normalEta"),
  safeDistance: document.querySelector("#safeDistance"),
  safeRisk: document.querySelector("#safeRisk"),
  safeHotspots: document.querySelector("#safeHotspots"),
  safeEta: document.querySelector("#safeEta"),
  normalAssurance: document.querySelector("#normalAssurance"),
  safeAssurance: document.querySelector("#safeAssurance"),
  normalGrade: document.querySelector("#normalGrade"),
  safeGrade: document.querySelector("#safeGrade"),
  safeOptionList: document.querySelector("#safeOptionList"),
  explanationSource: document.querySelector("#explanationSource"),
  routeExplanation: document.querySelector("#routeExplanation"),
  explanationBullets: document.querySelector("#explanationBullets"),
  safetyLights: document.querySelector("#safetyLights"),
  safetyCctv: document.querySelector("#safetyCctv"),
  safetyHelp: document.querySelector("#safetyHelp"),
  safetyCrossing: document.querySelector("#safetyCrossing"),
  stepStart: document.querySelector("#stepStart"),
  stepEnd: document.querySelector("#stepEnd"),
  stepRoute: document.querySelector("#stepRoute"),
  heatToggle: document.querySelector("#heatToggle"),
  pointToggle: document.querySelector("#pointToggle"),
  safetyToggle: document.querySelector("#safetyToggle"),
  resetBtn: document.querySelector("#resetBtn"),
  locateBtn: document.querySelector("#locateBtn"),
  demoRouteBtn: document.querySelector("#demoRouteBtn"),
  routeLoading: document.querySelector("#routeLoading"),
};

const markerIcons = {
  start: L.divIcon({
    className: "",
    iconAnchor: [17, 34],
    html: '<div class="marker-pin start"><span>출</span></div>',
  }),
  end: L.divIcon({
    className: "",
    iconAnchor: [17, 34],
    html: '<div class="marker-pin end"><span>도</span></div>',
  }),
};

initialize();

function initialize() {
  if (window.lucide) {
    window.lucide.createIcons();
  }

  window.addEventListener("load", () => {
    refreshMapSize();
    setTimeout(refreshMapSize, 150);
    setTimeout(refreshMapSize, 500);
  });
  window.addEventListener("resize", refreshMapSize);
  requestAnimationFrame(() => requestAnimationFrame(refreshMapSize));
  setTimeout(refreshMapSize, 250);

  map.on("click", handleMapClick);
  els.resetBtn.addEventListener("click", resetAll);
  els.demoRouteBtn.addEventListener("click", applyDemoRoute);
  els.locateBtn.addEventListener("click", locateUser);
  els.heatToggle.addEventListener("change", updateLayerVisibility);
  els.pointToggle.addEventListener("change", updateLayerVisibility);
  els.safetyToggle.addEventListener("change", updateLayerVisibility);

  loadAccidents();
  updateSteps();
}

async function loadAccidents() {
  setMessage("사고 데이터를 불러오는 중");
  try {
    const response = await fetch("/api/accidents");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    state.accidents = payload.items || [];
    els.dataSource.textContent = payload.source === "koroad" ? "KOROAD" : "샘플";
    drawAccidents();
    setMessage(payload.fallback ? "샘플 사고 데이터 사용 중" : "KOROAD 데이터 사용 중");
  } catch (error) {
    console.error(error);
    els.dataSource.textContent = "오류";
    setMessage("사고 데이터를 불러오지 못했습니다");
  }
}

function drawAccidents() {
  if (state.heatLayer) {
    map.removeLayer(state.heatLayer);
  }
  if (state.pointLayer) {
    map.removeLayer(state.pointLayer);
  }

  const maxSeverity = Math.max(
    1,
    ...state.accidents.map((item) => item.accidents + item.casualties * 0.55)
  );
  const heatPoints = state.accidents.map((item) => [
    item.lat,
    item.lng,
    Math.min(1, (item.accidents + item.casualties * 0.55) / maxSeverity),
  ]);

  state.heatLayer = L.heatLayer(heatPoints, {
    radius: 42,
    blur: 34,
    minOpacity: 0.36,
    maxZoom: 17,
    gradient: {
      0.15: "#2f80ed",
      0.38: "#52b788",
      0.6: "#ffd166",
      0.78: "#f9844a",
      1: "#d74040",
    },
  });

  state.pointLayer = L.layerGroup(
    state.accidents.map((item) =>
      L.circleMarker([item.lat, item.lng], {
        radius: Math.max(5, Math.min(12, item.accidents / 1.7)),
        color: "#b91c1c",
        weight: 1,
        fillColor: "#ef4444",
        fillOpacity: 0.68,
      }).bindPopup(accidentPopup(item))
    )
  );

  updateLayerVisibility();
}

function accidentPopup(item) {
  return `
    <strong class="popup-title">${escapeHtml(item.name)}</strong>
    <div class="popup-grid">
      <span>사고</span><strong>${item.accidents}건</strong>
      <span>사상자</span><strong>${item.casualties}명</strong>
      <span>반경</span><strong>${item.radius_m}m</strong>
      <span>지역</span><strong>${escapeHtml(item.district || "Seoul")}</strong>
    </div>
  `;
}

function updateLayerVisibility() {
  toggleLayer(state.heatLayer, els.heatToggle.checked);
  toggleLayer(state.pointLayer, els.pointToggle.checked);
  toggleLayer(state.safetyLayer, els.safetyToggle.checked);
}

function toggleLayer(layer, shouldShow) {
  if (!layer) return;
  if (shouldShow && !map.hasLayer(layer)) {
    layer.addTo(map);
  }
  if (!shouldShow && map.hasLayer(layer)) {
    map.removeLayer(layer);
  }
}

function handleMapClick(event) {
  if (state.loadingRoute) return;

  if (state.start && state.end) {
    resetRouteOnly();
    setStart(event.latlng);
    updateSteps();
    setMessage("도착지를 기다리는 중");
    return;
  }

  if (!state.start) {
    setStart(event.latlng);
    updateSteps();
    setMessage("도착지를 기다리는 중");
    return;
  }

  setEnd(event.latlng);
  updateSteps();
  requestRoutes();
}

function setStart(latlng) {
  state.start = toPoint(latlng);
  if (state.startMarker) {
    state.startMarker.setLatLng(latlng);
  } else {
    state.startMarker = L.marker(latlng, { icon: markerIcons.start }).addTo(map);
  }
  els.startText.textContent = formatPoint(state.start);
}

function setEnd(latlng) {
  state.end = toPoint(latlng);
  if (state.endMarker) {
    state.endMarker.setLatLng(latlng);
  } else {
    state.endMarker = L.marker(latlng, { icon: markerIcons.end }).addTo(map);
  }
  els.endText.textContent = formatPoint(state.end);
}

async function requestRoutes() {
  if (!state.start || !state.end) return;

  state.loadingRoute = true;
  setRouteLoading(true);
  clearRoutes();
  updateSteps(true);
  setMessage("경로 분석 중");

  try {
    const response = await fetch("/api/routes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start: state.start,
        end: state.end,
      }),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const result = await response.json();
    const displayResult = await maybeUpgradeFallbackRoute(result);
    prepareRouteOptions(displayResult);
    drawRoutes(displayResult);
    renderResult(displayResult);
    loadSafetyForRoute(displayResult);
    setMessage("경로 분석 완료");
  } catch (error) {
    console.error(error);
    setMessage("경로를 계산하지 못했습니다");
  } finally {
    state.loadingRoute = false;
    setRouteLoading(false);
    updateSteps();
  }
}

function prepareRouteOptions(result) {
  const options = Array.isArray(result.safe_options) && result.safe_options.length
    ? result.safe_options
    : [result.safe];
  state.routeResult = result;
  state.safeOptions = options;
  state.selectedOptionIndex = 0;
  result.safe_options = options;
  result.safe = options[0] || result.safe;
  result.comparison = result.safe?.comparison || result.comparison;
  result.explanation = result.safe?.explanation || result.explanation;
}

function selectSafeOption(index) {
  if (!state.routeResult || !state.safeOptions[index]) return;
  state.selectedOptionIndex = index;
  const selected = state.safeOptions[index];
  state.routeResult.safe = selected;
  state.routeResult.comparison = selected.comparison || state.routeResult.comparison;
  state.routeResult.explanation = selected.explanation || state.routeResult.explanation;
  drawRoutes(state.routeResult);
  renderResult(state.routeResult);
  loadSafetyForRoute(state.routeResult);
}

function drawRoutes(result) {
  clearRoutes();
  state.normalRoute = L.polyline(result.normal.coordinates, {
    color: "#2563eb",
    weight: 5,
    opacity: 0.72,
    dashArray: "9 8",
    lineCap: "round",
    lineJoin: "round",
  }).addTo(map);

  const selected = result.safe;
  const options = result.safe_options || [selected];
  state.safeOptionRoutes = options
    .filter((option) => option && option !== selected)
    .map((option) =>
      L.polyline(option.coordinates, {
        color: "#159947",
        weight: 4,
        opacity: 0.24,
        dashArray: "4 8",
        lineCap: "round",
        lineJoin: "round",
      }).addTo(map)
    );

  state.safeRoute = L.polyline(result.safe.coordinates, {
    color: "#159947",
    weight: 7,
    opacity: 0.88,
    lineCap: "round",
    lineJoin: "round",
  }).addTo(map);

  const bounds = L.latLngBounds([
    ...result.normal.coordinates,
    ...options.flatMap((option) => option.coordinates || []),
  ]);
  if (bounds.isValid()) {
    map.fitBounds(bounds.pad(0.18), { animate: true, maxZoom: 16 });
    setTimeout(refreshMapSize, 180);
  }
}

async function loadSafetyForRoute(result) {
  const coordinates = [
    ...(result.normal?.coordinates || []),
    ...(result.safe?.coordinates || []),
  ];
  if (!coordinates.length) return;

  const bounds = L.latLngBounds(coordinates);
  const padded = bounds.pad(0.16);
  const params = new URLSearchParams({
    west: padded.getWest().toFixed(7),
    south: padded.getSouth().toFixed(7),
    east: padded.getEast().toFixed(7),
    north: padded.getNorth().toFixed(7),
  });

  try {
    const response = await fetch(`/api/safety?${params}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    state.safety = payload.items || [];
    drawSafetyFeatures();
    if (payload.summary) renderSafetySummary(payload.summary);
  } catch (error) {
    console.warn("Safety layer load failed", error);
  }
}

function drawSafetyFeatures() {
  if (state.safetyLayer) {
    map.removeLayer(state.safetyLayer);
  }

  const visibleItems = state.safety.slice(0, 700);
  state.safetyLayer = L.layerGroup(
    visibleItems.map((item) =>
      L.circleMarker([item.lat, item.lng], safetyMarkerStyle(item)).bindPopup(
        safetyPopup(item)
      )
    )
  );
  updateLayerVisibility();
}

function safetyMarkerStyle(item) {
  const color = item.color || safetyColor(item.category);
  const isLamp = item.category === "street_lamp";
  return {
    radius: isLamp ? 3 : 5,
    color: "#ffffff",
    weight: isLamp ? 0.6 : 1,
    fillColor: color,
    fillOpacity: isLamp ? 0.58 : 0.82,
    opacity: 0.88,
  };
}

function safetyColor(category) {
  return {
    street_lamp: "#f59e0b",
    cctv: "#8b5cf6",
    police: "#2563eb",
    emergency: "#dc2626",
    crossing: "#10b981",
    traffic_signal: "#14b8a6",
  }[category] || "#64748b";
}

function safetyPopup(item) {
  return `
    <strong class="popup-title">${escapeHtml(item.name || item.label)}</strong>
    <div class="popup-grid">
      <span>분류</span><strong>${escapeHtml(item.label || item.category)}</strong>
      <span>출처</span><strong>${escapeHtml(item.source || "OSM")}</strong>
      <span>반경</span><strong>${item.radius_m || 0}m</strong>
    </div>
  `;
}

function refreshMapSize() {
  map.invalidateSize({ animate: false });
}

function renderResult(result) {
  els.routerMode.textContent = formatRouterName(result.router);
  const comparison = result.comparison || {};
  els.riskReduction.textContent =
    comparison.risk_reduction_percent === undefined
      ? "-"
      : `${comparison.risk_reduction_percent}%`;
  els.safetyDelta.textContent =
    comparison.net_safety_delta === undefined
      ? "-"
      : formatSignedScore(comparison.net_safety_delta);
  els.detourDelta.textContent =
    comparison.distance_delta_m === undefined
      ? "-"
      : formatDistanceDelta(comparison.distance_delta_m);
  renderSafetySummary(result.safety_summary || result.safe?.safety_counts);
  renderExplanation(result.explanation);
  renderSafeOptions(result);

  renderRoute("normal", result.normal);
  renderRoute("safe", result.safe);
}

function renderSafeOptions(result) {
  if (!els.safeOptionList) return;
  const options = result.safe_options || [];
  if (!options.length) {
    els.safeOptionList.replaceChildren();
    return;
  }

  const buttons = options.map((option, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `safe-option-button${
      index === state.selectedOptionIndex ? " active" : ""
    }`;
    button.setAttribute(
      "aria-pressed",
      index === state.selectedOptionIndex ? "true" : "false"
    );
    button.addEventListener("click", () => selectSafeOption(index));

    const label = document.createElement("strong");
    label.textContent = `${index + 1}. ${option.label || "대안"}`;

    const meta = document.createElement("span");
    const detour = option.comparison?.distance_delta_m;
    meta.textContent = `${formatOptionDistance(option.distance_km)} · 위험 ${
      option.risk_score ?? "-"
    } · ${formatDistanceDelta(detour ?? 0)}`;

    const grade = document.createElement("em");
    grade.className = `grade-badge grade-${String(
      option.safety_grade || "empty"
    ).toLowerCase()}`;
    grade.textContent = option.safety_grade || "-";

    button.append(label, meta, grade);
    return button;
  });

  els.safeOptionList.replaceChildren(...buttons);
}

function renderRoute(type, route) {
  const prefix = type === "normal" ? "normal" : "safe";
  const distanceKm = Number(route.distance_km);
  els[`${prefix}Distance`].textContent = Number.isFinite(distanceKm)
    ? `${distanceKm.toFixed(2)} km`
    : "-";
  els[`${prefix}Risk`].textContent = route.risk_score ?? "-";
  const safetyScore = route.net_safety_score ?? route.assurance_score;
  els[`${prefix}Assurance`].textContent =
    safetyScore === undefined ? "-" : formatSignedScore(safetyScore);
  els[`${prefix}Grade`].textContent = route.safety_grade || "-";
  els[`${prefix}Grade`].className = `grade-badge grade-${String(
    route.safety_grade || "empty"
  ).toLowerCase()}`;
  els[`${prefix}Hotspots`].textContent = `${route.near_hotspots}곳`;
  els[`${prefix}Eta`].textContent = `${route.eta_min}분`;
  if (route.score_breakdown) {
    els[`${prefix}Assurance`].title = `시설 +${route.score_breakdown.facility_bonus} / 사고 -${route.score_breakdown.accident_penalty}`;
  }
}

function renderSafetySummary(summary = {}) {
  els.safetyLights.textContent = summary.street_lamp ?? "-";
  els.safetyCctv.textContent = summary.cctv ?? "-";
  els.safetyHelp.textContent =
    (summary.police ?? 0) + (summary.emergency ?? 0) || "-";
  els.safetyCrossing.textContent =
    (summary.crossing ?? 0) + (summary.traffic_signal ?? 0) || "-";
}

function renderExplanation(explanation) {
  if (!els.explanationSource || !els.routeExplanation || !els.explanationBullets) {
    return;
  }

  if (!explanation) {
    els.explanationSource.textContent = "-";
    els.routeExplanation.textContent = "경로를 선택하면 안전 경로 선택 이유가 표시됩니다.";
    els.explanationBullets.replaceChildren();
    return;
  }

  els.explanationSource.textContent =
    explanation.source === "openai" ? "GPT" : "로컬";
  els.routeExplanation.textContent = explanation.summary || "-";
  const items = (explanation.bullets || []).slice(0, 4).map((text) => {
    const li = document.createElement("li");
    li.textContent = text;
    return li;
  });
  els.explanationBullets.replaceChildren(...items);
}

function formatRouterName(router) {
  return {
    "osmnx-walk-a-star": "A* 보행망",
    "risk-grid-a-star-fallback": "A* fallback",
  }[router] || router || "-";
}

function formatSignedScore(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return `${number > 0 ? "+" : ""}${round1(number)}`;
}

function formatDistanceDelta(value) {
  const meters = Number(value);
  if (!Number.isFinite(meters)) return "-";
  if (Math.abs(meters) >= 1000) {
    return `${meters > 0 ? "+" : ""}${round2(meters / 1000)} km`;
  }
  return `${meters > 0 ? "+" : ""}${round1(meters)} m`;
}

function formatOptionDistance(value) {
  const distance = Number(value);
  return Number.isFinite(distance) ? `${distance.toFixed(2)}km` : "-";
}

async function maybeUpgradeFallbackRoute(result) {
  if (!result.router || !result.router.includes("risk-grid")) {
    return result;
  }

  try {
    setMessage("실제 도로 경로로 보정 중");
    const roadResult = await buildBrowserRoadRoutes();
    return roadResult || result;
  } catch (error) {
    console.warn("Browser road routing fallback failed", error);
    return result;
  }
}

async function buildBrowserRoadRoutes() {
  const profiles = ["driving", "foot", "walking"];
  for (const profile of profiles) {
    const normalCandidates = await requestOsrmRoutes([state.start, state.end], profile, true);
    if (!normalCandidates.length) continue;

    const normal = normalCandidates[0];
    const safeCandidates = [...normalCandidates];
    const waypoints = safetyWaypointCandidates(normal.coordinates);
    for (const waypoint of waypoints) {
      const detours = await requestOsrmRoutes([state.start, waypoint, state.end], profile, false);
      safeCandidates.push(...detours);
    }

    const normalSummary = buildClientRouteSummary(normal.coordinates);
    const safeOptions = rankSafestRoadRoutes(normal.coordinates, safeCandidates)
      .slice(0, 3)
      .map((candidate, index) => {
        const summary = buildClientRouteSummary(candidate.coordinates);
        const comparison = buildClientRouteComparison(normalSummary, summary);
        summary.comparison = comparison;
        summary.rank = index + 1;
        summary.label = safeOptionLabel(index + 1, summary, normalSummary);
        summary.explanation = buildClientRouteExplanation(
          normalSummary,
          summary,
          comparison
        );
        return summary;
      });
    const safeSummary = safeOptions[0];
    return {
      router: `road-osrm-${profile}`,
      accident_source: "browser-osrm",
      normal: normalSummary,
      safe: safeSummary,
      safe_options: safeOptions,
      comparison: safeSummary.comparison,
      explanation: safeSummary.explanation,
    };
  }
  return null;
}

async function requestOsrmRoutes(points, profile, alternatives) {
  const coordinates = points.map((point) => `${point.lng},${point.lat}`).join(";");
  const params = new URLSearchParams({
    overview: "full",
    geometries: "geojson",
    steps: "false",
    alternatives: alternatives ? "true" : "false",
  });
  const url = `https://router.project-osrm.org/route/v1/${profile}/${coordinates}?${params}`;
  const response = await fetch(url);
  if (!response.ok) return [];

  const payload = await response.json();
  if (payload.code !== "Ok") return [];

  return (payload.routes || [])
    .map((route) => ({
      coordinates: dedupeClientCoords(
        (route.geometry?.coordinates || []).map(([lng, lat]) => [lat, lng])
      ),
      distance_m: route.distance || 0,
    }))
    .filter((route) => route.coordinates.length >= 2);
}

function safetyWaypointCandidates(normalCoords) {
  const start = state.start;
  const end = state.end;
  const origin = midpoint(start, end);
  const [startX, startY] = toLocalXY(start, origin);
  const [endX, endY] = toLocalXY(end, origin);
  const vectorX = endX - startX;
  const vectorY = endY - startY;
  const vectorLength = Math.hypot(vectorX, vectorY);
  if (!vectorLength) return [];

  const perpendicular = [-vectorY / vectorLength, vectorX / vectorLength];
  const anchors = waypointAnchors(normalCoords);
  const baseDetour = Math.max(450, Math.min(1400, routeDistance(normalCoords) * 0.24));
  const distances = [baseDetour, baseDetour * 1.45];
  const candidates = [];

  for (const anchor of anchors) {
    const [anchorX, anchorY] = toLocalXY(anchor, origin);
    for (const distance of distances) {
      for (const direction of [-1, 1]) {
        const candidate = fromLocalXY(
          [
            anchorX + perpendicular[0] * distance * direction,
            anchorY + perpendicular[1] * distance * direction,
          ],
          origin
        );
        if (candidateIsUseful(candidate)) candidates.push(candidate);
      }
    }
  }

  return uniqueClientPoints(candidates, 180).slice(0, 6);
}

function waypointAnchors(normalCoords) {
  const anchors = [
    routePointAtRatio(normalCoords, 0.35),
    routePointAtRatio(normalCoords, 0.5),
    routePointAtRatio(normalCoords, 0.65),
  ];
  const relevant = [...state.accidents]
    .map((spot) => ({
      ...spot,
      distanceToRoute: minDistanceToRoute(
        { lat: spot.lat, lng: spot.lng },
        normalCoords
      ),
      severity: spot.accidents + spot.casualties * 0.55,
    }))
    .filter((spot) => spot.distanceToRoute <= Math.max(450, spot.radius_m * 1.8))
    .sort(
      (a, b) =>
        a.distanceToRoute -
        a.severity * 18 -
        (b.distanceToRoute - b.severity * 18)
    )
    .slice(0, 3);

  relevant.forEach((spot) => anchors.unshift({ lat: spot.lat, lng: spot.lng }));
  return uniqueClientPoints(anchors, 220);
}

function candidateIsUseful(candidate) {
  const straight = haversine(state.start, state.end);
  const candidateTrip = haversine(state.start, candidate) + haversine(candidate, state.end);
  if (candidateTrip > Math.max(straight * 2.6, straight + 3200)) return false;
  return riskInfluence(candidate.lat, candidate.lng) < 1.8;
}

function selectSafestRoadRoute(normalCoords, candidates) {
  return rankSafestRoadRoutes(normalCoords, candidates)[0];
}

function rankSafestRoadRoutes(normalCoords, candidates) {
  const normalDistance = Math.max(routeDistance(normalCoords), 1);
  const maxReasonableDistance = Math.max(normalDistance * 1.9, normalDistance + 1800);
  const eligible = candidates.filter(
    (candidate) => routeDistance(candidate.coordinates) <= maxReasonableDistance
  );
  const pool = eligible.length ? eligible : candidates;
  const ranked = [];
  [...pool]
    .sort(
      (a, b) =>
        roadRouteCost(a.coordinates, normalDistance) -
        roadRouteCost(b.coordinates, normalDistance)
    )
    .forEach((candidate) => {
      if (!clientRouteAlreadySeen(candidate.coordinates, ranked)) {
        ranked.push(candidate);
      }
    });
  return ranked.length ? ranked : pool.slice(0, 1);
}

function roadRouteCost(coords, normalDistance) {
  const distance = routeDistance(coords);
  const risk = routeRiskScore(coords);
  const nearHotspots = nearbyHotspotCount(coords);
  const distancePenalty = Math.max(0, distance - normalDistance * 1.15) * 0.7;
  return risk * 680 + nearHotspots * 260 + distance * 0.18 + distancePenalty;
}

function buildClientRouteSummary(coords) {
  const distance = routeDistance(coords);
  const riskScore = routeRiskScore(coords);
  const nearHotspots = nearbyHotspotCount(coords);
  const assuranceScore = 0;
  const accidentPenalty = routeAccidentPenaltyScore(riskScore, nearHotspots);
  const netSafetyScore = routeNetSafetyScore(riskScore, assuranceScore, nearHotspots);
  return {
    coordinates: coords.map(([lat, lng]) => [roundCoord(lat), roundCoord(lng)]),
    distance_m: round1(distance),
    distance_km: round2(distance / 1000),
    eta_min: Math.max(1, Math.round(distance / 78)),
    risk_score: riskScore,
    near_hotspots: nearHotspots,
    assurance_score: assuranceScore,
    net_safety_score: netSafetyScore,
    safety_grade: routeSafetyGrade(netSafetyScore),
    score_breakdown: {
      facility_bonus: assuranceScore,
      accident_penalty: accidentPenalty,
      net_safety: netSafetyScore,
    },
  };
}

function buildClientRouteComparison(normal, safe) {
  const distanceDelta = round1(safe.distance_m - normal.distance_m);
  return {
    distance_delta_m: distanceDelta,
    distance_delta_percent: round1(
      (distanceDelta / Math.max(normal.distance_m, 1)) * 100
    ),
    risk_delta: round1(safe.risk_score - normal.risk_score),
    net_safety_delta: round1(safe.net_safety_score - normal.net_safety_score),
    risk_reduction_percent: calculateReduction(normal.risk_score, safe.risk_score),
  };
}

function buildClientRouteExplanation(normal, safe, comparison) {
  return {
    source: "template",
    title: "브라우저 fallback 선택 이유",
    summary: `안전 경로는 일반 경로보다 ${formatDistanceDelta(
      comparison.distance_delta_m
    )} 더 이동하지만, 위험도는 ${normal.risk_score}에서 ${
      safe.risk_score
    }로 낮아져 위험을 ${comparison.risk_reduction_percent}% 줄였습니다.`,
    bullets: [
      `사고지점 접근 수: 일반 ${normal.near_hotspots}곳, 선택 경로 ${safe.near_hotspots}곳`,
      `안전 등급: ${normal.safety_grade}에서 ${safe.safety_grade}`,
      `예상 이동 시간: ${safe.eta_min}분`,
    ],
  };
}

function safeOptionLabel(index, option, normal) {
  if (index === 1) return "추천";
  if (index === 2) {
    return option.distance_m <= normal.distance_m * 1.45 ? "짧은 우회" : "위험 최소";
  }
  if (index === 3) {
    if (
      option.distance_m <= normal.distance_m * 1.6 &&
      option.risk_score <= normal.risk_score * 0.45
    ) {
      return "균형 대안";
    }
    if (option.risk_score <= normal.risk_score * 0.35) return "저위험 대안";
    return "대안 3";
  }
  return `대안 ${index}`;
}

function clientRouteAlreadySeen(coords, candidates) {
  return candidates.some((candidate) => {
    return clientRouteTooSimilar(coords, candidate.coordinates);
  });
}

function clientRouteTooSimilar(coords, other) {
  if (coords.length < 2 || other.length < 2) return false;
  const distance = routeDistance(coords);
  const otherDistance = routeDistance(other);
  const distanceDeltaRatio =
    Math.abs(distance - otherDistance) / Math.max(distance, otherDistance, 1);
  const samplePoints = sampleClientRoutePoints(coords, 18);
  const nearestDistances = samplePoints.map((point) => minDistanceToRoute(point, other));
  const closeCount = nearestDistances.filter(
    (value) => value <= safeRouteSimilarDistanceM
  ).length;
  const closeRatio = closeCount / Math.max(nearestDistances.length, 1);
  const averageDistance =
    nearestDistances.reduce((total, value) => total + value, 0) /
    Math.max(nearestDistances.length, 1);

  if (closeRatio >= safeRouteSimilarRatio) return true;
  return (
    closeRatio >= 0.58 &&
    averageDistance <= safeRouteSimilarDistanceM * 0.85 &&
    distanceDeltaRatio <= 0.18
  );
}

function sampleClientRoutePoints(coords, sampleCount) {
  if (!coords.length) return [];
  if (coords.length === 1 || sampleCount <= 1) return [coords[0]];
  return Array.from({ length: sampleCount }, (_, index) => {
    const ratio = index / Math.max(1, sampleCount - 1);
    return coords[
      Math.min(coords.length - 1, Math.round((coords.length - 1) * ratio))
    ];
  });
}

function routeDistance(coords) {
  let distance = 0;
  for (let index = 0; index < coords.length - 1; index += 1) {
    distance += haversine(coords[index], coords[index + 1]);
  }
  return distance;
}

function routeRiskScore(coords) {
  if (coords.length < 2) return 0;
  let weightedRisk = 0;
  let totalDistance = 0;
  for (let index = 0; index < coords.length - 1; index += 1) {
    const a = toLatLngObject(coords[index]);
    const b = toLatLngObject(coords[index + 1]);
    const segmentDistance = haversine(a, b);
    const samples = Math.max(2, Math.min(12, Math.ceil(segmentDistance / 90)));
    for (let step = 0; step < samples; step += 1) {
      const ratio = step / (samples - 1);
      const lat = a.lat + (b.lat - a.lat) * ratio;
      const lng = a.lng + (b.lng - a.lng) * ratio;
      weightedRisk += (riskInfluence(lat, lng) * segmentDistance) / samples;
    }
    totalDistance += segmentDistance;
  }
  if (!totalDistance) return 0;
  return round1(Math.min(100, (weightedRisk / totalDistance) * 26));
}

function nearbyHotspotCount(coords) {
  return state.accidents.filter((spot) => {
    const threshold = Math.max(260, spot.radius_m * 1.15);
    return minDistanceToRoute({ lat: spot.lat, lng: spot.lng }, coords) <= threshold;
  }).length;
}

function minDistanceToRoute(point, coords) {
  return Math.min(...coords.map((coord) => haversine(point, coord)));
}

function riskInfluence(lat, lng) {
  return state.accidents.reduce((total, spot) => {
    const distance = haversine({ lat, lng }, { lat: spot.lat, lng: spot.lng });
    const radius = spot.radius_m || 320;
    const severity = Math.min(3, 0.18 * spot.accidents + 0.1 * spot.casualties);
    return total + severity * Math.exp(-((distance / radius) ** 2));
  }, 0);
}

function routeNetSafetyScore(riskScore, assuranceScore, nearHotspots) {
  const score = assuranceScore - routeAccidentPenaltyScore(riskScore, nearHotspots);
  return round1(Math.max(-100, Math.min(100, score)));
}

function routeAccidentPenaltyScore(riskScore, nearHotspots) {
  return round1(Math.min(100, riskScore + nearHotspots * 2.5));
}

function routeSafetyGrade(netSafetyScore) {
  if (netSafetyScore >= 35) return "A";
  if (netSafetyScore >= 20) return "B";
  if (netSafetyScore >= 5) return "C";
  if (netSafetyScore >= -10) return "D";
  return "E";
}

function routePointAtRatio(coords, ratio) {
  if (!coords.length) return { lat: 0, lng: 0 };
  const target = routeDistance(coords) * ratio;
  let covered = 0;
  for (let index = 0; index < coords.length - 1; index += 1) {
    const a = toLatLngObject(coords[index]);
    const b = toLatLngObject(coords[index + 1]);
    const segment = haversine(a, b);
    if (covered + segment >= target && segment > 0) {
      const localRatio = (target - covered) / segment;
      return {
        lat: a.lat + (b.lat - a.lat) * localRatio,
        lng: a.lng + (b.lng - a.lng) * localRatio,
      };
    }
    covered += segment;
  }
  return toLatLngObject(coords[coords.length - 1]);
}

function midpoint(a, b) {
  return { lat: (a.lat + b.lat) / 2, lng: (a.lng + b.lng) / 2 };
}

function toLocalXY(point, origin) {
  const metersPerLat = 111320;
  const metersPerLng = metersPerLat * Math.cos((origin.lat * Math.PI) / 180);
  return [(point.lng - origin.lng) * metersPerLng, (point.lat - origin.lat) * metersPerLat];
}

function fromLocalXY([x, y], origin) {
  const metersPerLat = 111320;
  const metersPerLng = metersPerLat * Math.cos((origin.lat * Math.PI) / 180);
  return { lat: origin.lat + y / metersPerLat, lng: origin.lng + x / metersPerLng };
}

function uniqueClientPoints(points, minDistanceM) {
  return points.filter((point, index) =>
    points.findIndex((candidate) => haversine(point, candidate) < minDistanceM) === index
  );
}

function dedupeClientCoords(coords) {
  return coords.filter((coord, index) => index === 0 || haversine(coord, coords[index - 1]) > 1);
}

function haversine(a, b) {
  const pointA = toLatLngObject(a);
  const pointB = toLatLngObject(b);
  const earthRadiusM = 6371000;
  const lat1 = (pointA.lat * Math.PI) / 180;
  const lat2 = (pointB.lat * Math.PI) / 180;
  const deltaLat = ((pointB.lat - pointA.lat) * Math.PI) / 180;
  const deltaLng = ((pointB.lng - pointA.lng) * Math.PI) / 180;
  const value =
    Math.sin(deltaLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(deltaLng / 2) ** 2;
  return earthRadiusM * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(1 - value));
}

function toLatLngObject(point) {
  return Array.isArray(point) ? { lat: point[0], lng: point[1] } : point;
}

function calculateReduction(normal, safe) {
  if (normal <= 0) return 0;
  return round1(Math.max(0, ((normal - safe) / normal) * 100));
}

function roundCoord(value) {
  return Number(value.toFixed(7));
}

function round1(value) {
  return Math.round(value * 10) / 10;
}

function round2(value) {
  return Math.round(value * 100) / 100;
}

function updateSteps(isLoading = false) {
  els.stepStart.classList.toggle("active", !state.start);
  els.stepEnd.classList.toggle("active", Boolean(state.start && !state.end));
  els.stepRoute.classList.toggle("active", Boolean(state.start && state.end) || isLoading);
}

function clearRoutes() {
  if (state.normalRoute) {
    map.removeLayer(state.normalRoute);
    state.normalRoute = null;
  }
  state.safeOptionRoutes.forEach((route) => map.removeLayer(route));
  state.safeOptionRoutes = [];
  if (state.safeRoute) {
    map.removeLayer(state.safeRoute);
    state.safeRoute = null;
  }
}

function resetRouteOnly() {
  clearRoutes();
  clearSafetyLayer();
  if (state.startMarker) {
    map.removeLayer(state.startMarker);
    state.startMarker = null;
  }
  if (state.endMarker) {
    map.removeLayer(state.endMarker);
    state.endMarker = null;
  }
  state.start = null;
  state.end = null;
  state.safety = [];
  state.routeResult = null;
  state.safeOptions = [];
  state.selectedOptionIndex = 0;
  els.startText.textContent = "대기 중";
  els.endText.textContent = "대기 중";
  clearMetrics();
}

function resetAll() {
  resetRouteOnly();
  map.setView(seoulCenter, 12);
  els.routerMode.textContent = "-";
  updateSteps();
  setMessage("출발지를 기다리는 중");
}

function clearMetrics() {
  els.riskReduction.textContent = "-";
  els.safetyDelta.textContent = "-";
  els.detourDelta.textContent = "-";
  if (els.safeOptionList) {
    els.safeOptionList.replaceChildren();
  }
  renderExplanation();
  renderSafetySummary();
  [
    els.normalDistance,
    els.normalRisk,
    els.normalAssurance,
    els.normalGrade,
    els.normalHotspots,
    els.normalEta,
    els.safeDistance,
    els.safeRisk,
    els.safeAssurance,
    els.safeGrade,
    els.safeHotspots,
    els.safeEta,
  ].forEach((element) => {
    element.textContent = "-";
  });
  els.normalGrade.className = "grade-badge";
  els.safeGrade.className = "grade-badge";
}

function clearSafetyLayer() {
  if (state.safetyLayer) {
    map.removeLayer(state.safetyLayer);
    state.safetyLayer = null;
  }
}

function applyDemoRoute() {
  resetRouteOnly();
  const start = L.latLng(37.4922, 127.0152);
  const end = L.latLng(37.5056, 127.0476);
  setStart(start);
  setEnd(end);
  map.fitBounds(L.latLngBounds([start, end]).pad(0.7), { maxZoom: 14 });
  updateSteps(true);
  requestRoutes();
}

function locateUser() {
  if (!navigator.geolocation) {
    setMessage("현재 위치를 사용할 수 없습니다");
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (position) => {
      const latlng = L.latLng(position.coords.latitude, position.coords.longitude);
      map.setView(latlng, 15);
      if (!state.start || (state.start && state.end)) {
        resetRouteOnly();
        setStart(latlng);
        updateSteps();
      }
      setMessage("현재 위치 확인 완료");
    },
    () => setMessage("현재 위치 권한이 필요합니다"),
    { enableHighAccuracy: true, timeout: 8000 }
  );
}

function toPoint(latlng) {
  return {
    lat: Number(latlng.lat.toFixed(7)),
    lng: Number(latlng.lng.toFixed(7)),
  };
}

function formatPoint(point) {
  return `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`;
}

function setRouteLoading(isLoading) {
  if (!els.routeLoading) return;
  els.routeLoading.classList.toggle("visible", isLoading);
  els.routeLoading.setAttribute("aria-hidden", isLoading ? "false" : "true");
}

function setMessage(message) {
  els.message.textContent = message;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
