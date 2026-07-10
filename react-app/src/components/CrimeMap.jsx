import { useState, useEffect } from "react";
import { MapContainer, TileLayer, CircleMarker, Popup } from "react-leaflet";
import { Filter, MapPin } from "lucide-react";
import "leaflet/dist/leaflet.css";

/* ---------------------------------------------------------------------
   Calls /server/ksp_intelli_q_function/search_fir on your Catalyst
   backend. Falls back to demo pins so the map always renders.
------------------------------------------------------------------------ */
const USE_MOCK = true;

async function fetchFirs(filters) {
  if (USE_MOCK) return MOCK_FIRS;
  try {
    const params = new URLSearchParams(filters).toString();
    const res = await fetch(`/server/ksp_intelli_q_function/search_fir?${params}`, { credentials: "include" });
    const data = await res.json();
    return data.results || [];
  } catch (err) {
    console.warn("search_fir failed, using fallback", err);
    return MOCK_FIRS;
  }
}

// Karnataka / Bengaluru area coordinates for demo pins
const MOCK_FIRS = [
  { fir_id: "FIR001", crime_type: "Theft", location: "Whitefield", lat: 12.9698, lng: 77.7500, status: "Open" },
  { fir_id: "FIR002", crime_type: "Chain Snatching", location: "Indiranagar", lat: 12.9719, lng: 77.6412, status: "Under Investigation" },
  { fir_id: "FIR003", crime_type: "Burglary", location: "Electronic City", lat: 12.8452, lng: 77.6602, status: "Closed" },
  { fir_id: "FIR004", crime_type: "Assault", location: "Yeshwanthpur", lat: 13.0284, lng: 77.5546, status: "Open" },
  { fir_id: "FIR005", crime_type: "Vehicle Theft", location: "Whitefield", lat: 12.9750, lng: 77.7420, status: "Open" },
  { fir_id: "FIR006", crime_type: "Robbery", location: "Koramangala", lat: 12.9352, lng: 77.6245, status: "Under Investigation" },
];

const STATUS_COLOR = { Open: "#c17a7a", "Under Investigation": "#d4b073", Closed: "#7fb39c" };

export default function CrimeMap() {
  const [firs, setFirs] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchFirs(statusFilter ? { status: statusFilter } : {}).then((data) => {
      setFirs(data);
      setLoading(false);
    });
  }, [statusFilter]);

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
          padding: 16px 20px; background: var(--panel); border-bottom: 1px solid var(--border); }
        .map-title { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 14px; }
        .filter-row { display: flex; gap: 8px; }
        .filter-chip { padding: 6px 14px; border-radius: 20px; font-size: 11.5px; cursor: pointer;
          border: 1px solid var(--border); background: var(--panel-raised); color: var(--muted); font-weight: 600; }
        .filter-chip.active { background: var(--gold); color: var(--ink); border-color: var(--gold); }
        .map-body { height: 420px; position: relative; }
        .legend { position: absolute; bottom: 16px; left: 16px; z-index: 1000; background: var(--panel);
          border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; font-size: 11px; }
        .legend-row { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
        .legend-dot { width: 8px; height: 8px; border-radius: 50%; }
      `}</style>

      <div className="map-header">
        <div className="map-title"><MapPin size={15} /> Crime Map — Live FIR Locations</div>
        <div className="filter-row">
          <Filter size={13} color="var(--muted)" style={{ alignSelf: "center", marginRight: 2 }} />
          {["", "Open", "Under Investigation", "Closed"].map((s) => (
            <div
              key={s || "all"}
              className={`filter-chip ${statusFilter === s ? "active" : ""}`}
              onClick={() => setStatusFilter(s)}
            >
              {s || "All"}
            </div>
          ))}
        </div>
      </div>

      <div className="map-body">
        <MapContainer center={[12.9716, 77.5946]} zoom={11} style={{ height: "100%", width: "100%" }}>
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            attribution='&copy; OpenStreetMap &copy; CARTO'
          />
          {firs.map((fir) => (
            <CircleMarker
              key={fir.fir_id}
              center={[fir.lat, fir.lng]}
              radius={9}
              pathOptions={{
                color: STATUS_COLOR[fir.status] || "#d4b073",
                fillColor: STATUS_COLOR[fir.status] || "#d4b073",
                fillOpacity: 0.6,
                weight: 2,
              }}
            >
              <Popup>
                <strong>{fir.fir_id}</strong> — {fir.crime_type}
                <br />
                {fir.location}
                <br />
                Status: {fir.status}
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
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