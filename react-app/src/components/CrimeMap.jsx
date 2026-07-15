import { useState, useEffect } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup } from "react-leaflet";
import { Filter, MapPin } from "lucide-react";
import "leaflet/dist/leaflet.css";

/* ---------------------------------------------------------------------
   Calls the real backend routes:
     GET /server/ksp_intelli_q_function/get_lookups   (once, for names)
     GET /server/ksp_intelli_q_function/search_case?status_id=...
   CaseMaster rows come back with IDs (CrimeMinorHeadID, CaseStatusID,
   PoliceStationID) — we resolve those to display names using the
   lookup tables, same way the rest of the app does.
------------------------------------------------------------------------ */

async function fetchJSON(endpoint, opts) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, {
    credentials: "include",
    ...opts,
  });
  if (!res.ok) throw new Error(`${endpoint} failed (${res.status})`);
  return res.json();
}

export default function CrimeMap() {
  const [lookups, setLookups] = useState(null);
  const [cases, setCases] = useState([]);
  const [statusFilter, setStatusFilter] = useState(""); // holds a CaseStatusID, "" = all
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Load lookups once
  useEffect(() => {
    fetchJSON("get_lookups")
      .then(setLookups)
      .catch((err) => setError(err.message));
  }, []);

  // Load cases whenever the status filter changes (needs lookups first,
  // just so the filter chips below have names to show)
  useEffect(() => {
    if (!lookups) return;
    setLoading(true);
    setError(null);
    const qs = statusFilter ? `?status_id=${encodeURIComponent(statusFilter)}` : "";
    fetchJSON(`search_case${qs}`)
      .then((data) => setCases(data.results || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [lookups, statusFilter]);

  if (error) {
    return (
      <div className="crime-map-wrap" style={{ padding: 24, color: "#c17a7a" }}>
        Couldn't load the crime map: {error}. Check that the backend function is deployed
        and reachable at /server/ksp_intelli_q_function/.
      </div>
    );
  }

  const subheadName = (id) =>
    lookups?.crime_subheads.find((s) => s.CrimeSubHeadID === id)?.CrimeHeadName || "Unknown";
  const unitName = (id) => lookups?.units.find((u) => u.UnitID === id)?.UnitName || "Unassigned";
  const statusName = (id) =>
    lookups?.statuses.find((s) => s.CaseStatusID === id)?.CaseStatusName || "Unknown";

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
        .filter-row { display: flex; gap: 8px; flex-wrap: wrap; }
        .filter-chip { padding: 6px 14px; border-radius: 20px; font-size: 11.5px; cursor: pointer;
          border: 1px solid var(--border); background: var(--panel-raised); color: var(--muted); font-weight: 600; }
        .filter-chip.active { background: var(--gold); color: var(--ink); border-color: var(--gold); }
        .map-body { height: 420px; position: relative; }
        .legend { position: absolute; bottom: 16px; left: 16px; z-index: 1000; background: var(--panel);
          border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; font-size: 11px; }
        .legend-row { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
        .legend-dot { width: 8px; height: 8px; border-radius: 50%; }
        .map-loading { display: flex; align-items: center; justify-content: center; height: 100%; color: var(--muted); font-size: 13px; }
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
        </div>
      </div>

      <div className="map-body">
        {loading || !lookups ? (
          <div className="map-loading">Loading case locations…</div>
        ) : (
          <MapContainer center={[12.9716, 77.5946]} zoom={11} style={{ height: "100%", width: "100%" }}>
            <TileLayer
              url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
              attribution='&copy; OpenStreetMap &copy; CARTO'
            />
            {cases
              .filter((c) => c.latitude && c.longitude)
              .map((c) => {
                const status = statusName(c.CaseStatusID);
                const color = STATUS_COLOR[status] || "#d4b073";
                return (
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
                    </Popup>
                  </CircleMarker>
                );
              })}
          </MapContainer>
        )}
        <div className="legend">
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Status</div>
          {Object.entries(STATUS_COLOR).map(([label, color]) => (
            <div className="legend-row" key={label}>
              <span className="legend-dot" style={{ background: color }} /> {label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}