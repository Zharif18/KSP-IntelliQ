import { useState, useEffect, useRef, useMemo, Fragment } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup, useMap } from "react-leaflet";
import L from "leaflet";
import { Filter, MapPin, Flame, Clock, AlertTriangle } from "lucide-react";
import "leaflet/dist/leaflet.css";

/* ---------------------------------------------------------------------
   Calls the real backend routes:
     GET /server/ksp_intelli_q_function/get_lookups   (once, for names)
     GET /server/ksp_intelli_q_function/search_case?status_id=&limit=
     GET /server/ksp_intelli_q_function/get_trend_alerts
   CaseMaster rows come back with IDs (CrimeMinorHeadID, CaseStatusID,
   PoliceStationID) plus IncidentFromDate (a full timestamp) — we
   resolve IDs to display names using the lookup tables, and derive the
   incident's hour-of-day straight from IncidentFromDate for the
   time-of-day slider below.
------------------------------------------------------------------------ */

async function fetchJSON(endpoint, opts) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, {
    credentials: "include",
    ...opts,
  });
  if (!res.ok) throw new Error(`${endpoint} failed (${res.status})`);
  return res.json();
}

function incidentHour(caseRow) {
  const ts = caseRow.IncidentFromDate;
  if (!ts || typeof ts !== "string" || !ts.includes("T")) return null;
  const h = parseInt(ts.split("T")[1].slice(0, 2), 10);
  return Number.isNaN(h) ? null : h;
}

function formatHour(h) {
  if (h === 0) return "12am";
  if (h === 12) return "12pm";
  return h < 12 ? `${h}am` : `${h - 12}pm`;
}

/* ---------------------------------------------------------------------
   Lightweight canvas heatmap layer — no extra npm dependency. Draws an
   additive radial-gradient blob per point directly onto a canvas glued
   to Leaflet's overlay pane, redrawing on pan/zoom/data change. This is
   the density view the point markers can't show at a glance: where
   incidents cluster, regardless of individual case status.
------------------------------------------------------------------------ */
function HeatmapLayer({ points }) {
  const map = useMap();
  const layerRef = useRef(null);
  const pointsRef = useRef(points);
  pointsRef.current = points;

  useEffect(() => {
    if (!map) return undefined;

    const HeatLayer = L.Layer.extend({
      onAdd(m) {
        this._map = m;
        this._canvas = L.DomUtil.create("canvas", "heat-canvas-layer");
        this._canvas.style.position = "absolute";
        this._canvas.style.pointerEvents = "none";
        m.getPanes().overlayPane.appendChild(this._canvas);
        m.on("moveend zoomend resize", this._reset, this);
        this._reset();
      },
      onRemove(m) {
        L.DomUtil.remove(this._canvas);
        m.off("moveend zoomend resize", this._reset, this);
      },
      _reset() {
        const size = this._map.getSize();
        const topLeft = this._map.containerPointToLayerPoint([0, 0]);
        L.DomUtil.setPosition(this._canvas, topLeft);
        this._canvas.width = size.x;
        this._canvas.height = size.y;
        this._draw();
      },
      _draw() {
        const ctx = this._canvas.getContext("2d");
        ctx.clearRect(0, 0, this._canvas.width, this._canvas.height);
        ctx.globalCompositeOperation = "lighter";
        const radius = 26;
        (pointsRef.current || []).forEach(([lat, lng]) => {
          const p = this._map.latLngToContainerPoint([lat, lng]);
          const grad = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, radius);
          grad.addColorStop(0, "rgba(227,77,77,0.45)");
          grad.addColorStop(0.5, "rgba(212,176,115,0.22)");
          grad.addColorStop(1, "rgba(212,176,115,0)");
          ctx.fillStyle = grad;
          ctx.beginPath();
          ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
          ctx.fill();
        });
      },
      setPoints() {
        this._draw();
      },
    });

    const layer = new HeatLayer();
    layer.addTo(map);
    layerRef.current = layer;
    return () => {
      map.removeLayer(layer);
      layerRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  useEffect(() => {
    if (layerRef.current) layerRef.current.setPoints(points);
  }, [points]);

  return null;
}

export default function CrimeMap() {
  const [lookups, setLookups] = useState(null);
  const [cases, setCases] = useState([]);
  const [statusFilter, setStatusFilter] = useState(""); // holds a CaseStatusID, "" = all
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [viewMode, setViewMode] = useState("markers"); // "markers" | "heatmap"
  const [hourRange, setHourRange] = useState([0, 24]); // [fromHour, toHour], 24 = midnight end-of-day

  const [alerts, setAlerts] = useState([]);

  // Load lookups once
  useEffect(() => {
    fetchJSON("get_lookups")
      .then(setLookups)
      .catch((err) => setError(err.message));
  }, []);

  // Load cases whenever the status filter changes (needs lookups first,
  // just so the filter chips below have names to show). Bumped the
  // limit well past the old default of 50 — a heatmap/time-slider needs
  // enough points to actually show a density pattern.
  useEffect(() => {
    if (!lookups) return;
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ limit: "600" });
    if (statusFilter) params.set("status_id", statusFilter);
    fetchJSON(`search_case?${params.toString()}`)
      .then((data) => setCases(data.results || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [lookups, statusFilter]);

  // Emerging trend alerts — used to flag markers whose station + crime
  // category combo is currently spiking above its rolling baseline.
  useEffect(() => {
    fetchJSON("get_trend_alerts?window_weeks=4&limit=20")
      .then((d) => setAlerts(d.alerts || []))
      .catch(() => setAlerts([]));
  }, []);

  const subheadName = (id) =>
    lookups?.crime_subheads.find((s) => s.CrimeSubHeadID === id)?.CrimeHeadName || "Unknown";
  const unitName = (id) => lookups?.units.find((u) => u.UnitID === id)?.UnitName || "Unassigned";
  const statusName = (id) =>
    lookups?.statuses.find((s) => s.CaseStatusID === id)?.CaseStatusName || "Unknown";

  const alertKeySet = useMemo(
    () => new Set(alerts.map((a) => `${a.area}::${a.crimeType}`)),
    [alerts]
  );
  const isAlerted = (c) => alertKeySet.has(`${unitName(c.PoliceStationID)}::${subheadName(c.CrimeMinorHeadID)}`);

  const timeFilteredCases = useMemo(() => {
    const [from, to] = hourRange;
    if (from === 0 && to === 24) return cases; // full day, no filtering needed
    return cases.filter((c) => {
      const h = incidentHour(c);
      if (h === null) return false;
      return h >= from && h < to;
    });
  }, [cases, hourRange]);

  const geoCases = timeFilteredCases.filter((c) => c.latitude && c.longitude);
  const heatPoints = useMemo(() => geoCases.map((c) => [c.latitude, c.longitude]), [geoCases]);

  if (error) {
    return (
      <div className="crime-map-wrap" style={{ padding: 24, color: "#c17a7a" }}>
        Couldn't load the crime map: {error}. Check that the backend function is deployed
        and reachable at /server/ksp_intelli_q_function/.
      </div>
    );
  }

  const STATUS_COLOR = {}; // filled in below once lookups are in, keyed by status name
  lookups?.statuses.forEach((s, i) => {
    const palette = ["#c17a7a", "#d4b073", "#7fb39c", "#8a9bd4"];
    STATUS_COLOR[s.CaseStatusName] = palette[i % palette.length];
  });

  return (
    <div className="crime-map-wrap">
      <style>{`
        .crime-map-wrap {
          --ink: #0e1116; --panel: #171b23; --panel-raised: #212630;
          --gold: #d4b073; --gold-strong: #e8c98d; --wine: #c17a7a; --sage: #7fb39c;
          --text: #f3f1ea; --muted: #a8adba; --border: rgba(255,255,255,0.1);
          font-family: 'Inter', sans-serif; background: var(--ink); color: var(--text);
          border-radius: 12px; overflow: hidden; border: 1px solid var(--border);
        }
        .map-header { display: flex; align-items: center; justify-content: space-between;
          padding: 16px 20px; background: var(--panel); border-bottom: 1px solid var(--border); flex-wrap: wrap; gap: 10px; }
        .map-title { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 14px; }
        .filter-row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
        .filter-chip { padding: 6px 14px; border-radius: 20px; font-size: 11.5px; cursor: pointer;
          border: 1px solid var(--border); background: var(--panel-raised); color: var(--muted); font-weight: 600; }
        .filter-chip.active { background: var(--gold); color: var(--ink); border-color: var(--gold); }
        .view-toggle { display: flex; gap: 2px; background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 20px; padding: 2px; }
        .view-toggle-btn { display: flex; align-items: center; gap: 5px; padding: 5px 12px; border-radius: 20px;
          font-size: 11.5px; font-weight: 600; color: var(--muted); cursor: pointer; }
        .view-toggle-btn.active { background: var(--gold); color: var(--ink); }
        .time-bar { display: flex; align-items: center; gap: 14px; padding: 12px 20px; background: var(--panel);
          border-bottom: 1px solid var(--border); flex-wrap: wrap; }
        .time-bar-label { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--muted);
          font-weight: 600; flex-shrink: 0; }
        .time-slider-wrap { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 240px; }
        .time-slider-wrap input[type=range] { flex: 1; accent-color: var(--gold); }
        .time-readout { font-size: 11.5px; color: var(--gold-strong); font-weight: 700; white-space: nowrap; min-width: 108px; text-align: right; }
        .time-reset { font-size: 11px; color: var(--muted); text-decoration: underline; cursor: pointer; flex-shrink: 0; }
        .map-body { height: 420px; position: relative; }
        .legend { position: absolute; bottom: 16px; left: 16px; z-index: 1000; background: var(--panel);
          border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; font-size: 11px; }
        .legend-row { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
        .legend-dot { width: 8px; height: 8px; border-radius: 50%; }
        .map-loading { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--muted); font-size: 13px; }
        .map-count-badge { position: absolute; top: 12px; right: 12px; z-index: 1000; background: var(--panel);
          border: 1px solid var(--border); border-radius: 8px; padding: 6px 12px; font-size: 11px; color: var(--muted); }
        .alert-ring-path { animation: map-alert-pulse 1.6s infinite; }
        @keyframes map-alert-pulse {
          0% { stroke-opacity: 0.85; }
          70% { stroke-opacity: 0.05; }
          100% { stroke-opacity: 0.85; }
        }
      `}</style>

      <div className="map-header">
        <div className="map-title"><MapPin size={15} /> Crime Map — Live FIR Locations</div>
        <div className="filter-row">
          <Filter size={13} color="var(--muted)" style={{ alignSelf: "center", marginRight: 2 }} />
          <div
            className={`filter-chip ${statusFilter === "" ? "active" : ""}`}
            onClick={() => setStatusFilter("")}
          >
            All
          </div>
          {lookups?.statuses.map((s) => (
            <div
              key={s.CaseStatusID}
              className={`filter-chip ${statusFilter === s.CaseStatusID ? "active" : ""}`}
              onClick={() => setStatusFilter(s.CaseStatusID)}
            >
              {s.CaseStatusName}
            </div>
          ))}
          <div className="view-toggle">
            <div className={`view-toggle-btn ${viewMode === "markers" ? "active" : ""}`} onClick={() => setViewMode("markers")}>
              <MapPin size={12} /> Markers
            </div>
            <div className={`view-toggle-btn ${viewMode === "heatmap" ? "active" : ""}`} onClick={() => setViewMode("heatmap")}>
              <Flame size={12} /> Heatmap
            </div>
          </div>
        </div>
      </div>

      {/* Time-of-day slider — filters both marker and heatmap views to
          incidents whose IncidentFromDate hour falls inside the window,
          so you can see how hotspots shift from, say, a daytime pattern
          to a late-night one. */}
      <div className="time-bar">
        <div className="time-bar-label"><Clock size={13} /> Time of day</div>
        <div className="time-slider-wrap">
          <input
            type="range" min={0} max={24} step={1} value={hourRange[0]}
            onChange={(e) => {
              const v = Number(e.target.value);
              setHourRange(([, to]) => [Math.min(v, to - 1 < 0 ? 0 : to - 1), to]);
            }}
          />
          <input
            type="range" min={0} max={24} step={1} value={hourRange[1]}
            onChange={(e) => {
              const v = Number(e.target.value);
              setHourRange(([from]) => [from, Math.max(v, from + 1)]);
            }}
          />
        </div>
        <div className="time-readout">{formatHour(hourRange[0])} – {hourRange[1] === 24 ? "12am" : formatHour(hourRange[1])}</div>
        {(hourRange[0] !== 0 || hourRange[1] !== 24) && (
          <div className="time-reset" onClick={() => setHourRange([0, 24])}>Reset to all day</div>
        )}
      </div>

      <div className="map-body">
        {loading || !lookups ? (
          <div className="map-loading">Loading case locations…</div>
        ) : (
          <>
            <div className="map-count-badge">
              {geoCases.length} incident{geoCases.length === 1 ? "" : "s"} in view
              {alerts.length > 0 && (
                <span style={{ marginLeft: 8, color: "var(--wine)" }}>
                  <AlertTriangle size={11} style={{ verticalAlign: -1, marginRight: 3 }} />{alerts.length} trending
                </span>
              )}
            </div>
            <MapContainer center={[12.9716, 77.5946]} zoom={11} style={{ height: "100%", width: "100%" }}>
              <TileLayer
                url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
                attribution='&copy; OpenStreetMap &copy; CARTO'
              />
              {viewMode === "heatmap" ? (
                <HeatmapLayer points={heatPoints} />
              ) : (
                geoCases.map((c) => {
                  const status = statusName(c.CaseStatusID);
                  const color = STATUS_COLOR[status] || "#d4b073";
                  const flagged = isAlerted(c);
                  return (
                    <Fragment key={c.CaseMasterID}>
                      {flagged && (
                        <CircleMarker
                          key={`${c.CaseMasterID}-ring`}
                          center={[c.latitude, c.longitude]}
                          radius={15}
                          pathOptions={{
                            color: "#e34d4d", weight: 2, fill: false,
                            className: "alert-ring-path",
                          }}
                        />
                      )}
                      <CircleMarker
                        key={c.CaseMasterID}
                        center={[c.latitude, c.longitude]}
                        radius={9}
                        pathOptions={{ color, fillColor: color, fillOpacity: 0.6, weight: 2 }}
                      >
                        <Popup>
                          <strong>{c.CaseMasterID}</strong> — {subheadName(c.CrimeMinorHeadID)}
                          <br />
                          {unitName(c.PoliceStationID)}
                          <br />
                          Status: {status}
                          {incidentHour(c) !== null && (
                            <>
                              <br />
                              Time: {formatHour(incidentHour(c))}
                            </>
                          )}
                          {flagged && (
                            <>
                              <br />
                              <span style={{ color: "#c1372f", fontWeight: 700 }}>⚠ Trending above baseline</span>
                            </>
                          )}
                        </Popup>
                      </CircleMarker>
                    </Fragment>
                  );
                })
              )}
            </MapContainer>
          </>
        )}
        <div className="legend">
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{viewMode === "heatmap" ? "Incident Density" : "Status"}</div>
          {viewMode === "heatmap" ? (
            <div style={{ color: "var(--muted)" }}>Brighter = more incidents clustered here</div>
          ) : (
            Object.entries(STATUS_COLOR).map(([label, color]) => (
              <div className="legend-row" key={label}>
                <span className="legend-dot" style={{ background: color }} /> {label}
              </div>
            ))
          )}
          {alerts.length > 0 && viewMode === "markers" && (
            <div className="legend-row">
              <span className="legend-dot" style={{ background: "transparent", border: "2px solid #e34d4d" }} /> Trending (pulses red)
            </div>
          )}
        </div>
      </div>
    </div>
  );
}