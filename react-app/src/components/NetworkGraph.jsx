import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from "d3-force";
import { Search, SlidersHorizontal, X, MapPin, AlertTriangle, History, Clock, Gavel } from "lucide-react";

/* ---------------------------------------------------------------------
   Calls the real backend route:
     GET /server/ksp_intelli_q_function/get_network_graph?district_id=&min_cases=&limit=
     GET /server/ksp_intelli_q_function/get_lookups   (for the district filter)
   Falls back to a small demo graph so the module always renders, same
   pattern as the rest of the app (see Dashboard.jsx / CrimeMap.jsx).
------------------------------------------------------------------------ */
const USE_MOCK = false;

async function fetchJSON(endpoint, opts) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, {
    credentials: "include",
    ...opts,
  });
  if (!res.ok) throw new Error(`${endpoint} failed (${res.status})`);
  return res.json();
}

const MOCK_GRAPH = {
  nodes: [
    { id: "PER0001", type: "person", label: "Hemang Sur", caseCount: 4, repeatOffender: true, stations: ["Whitefield PS", "Indiranagar PS"], crimeTypes: ["Chain Snatching", "Vehicle Theft"] },
    { id: "PER0002", type: "person", label: "Abdul Arya", caseCount: 3, repeatOffender: true, stations: ["Whitefield PS"], crimeTypes: ["Chain Snatching"] },
    { id: "PER0003", type: "person", label: "George Dhar", caseCount: 1, repeatOffender: false, stations: ["Indiranagar PS"], crimeTypes: ["Vehicle Theft"] },
    { id: "PER0004", type: "person", label: "Mugdha Jaggi", caseCount: 2, repeatOffender: true, stations: ["Whitefield PS", "Koramangala PS"], crimeTypes: ["Chain Snatching", "Burglary"] },
    { id: "PER0005", type: "person", label: "Ravi Shastry", caseCount: 1, repeatOffender: false, stations: ["Koramangala PS"], crimeTypes: ["Burglary"] },
    { id: "loc::UNIT01", type: "location", label: "Whitefield PS" },
    { id: "loc::UNIT02", type: "location", label: "Indiranagar PS" },
    { id: "loc::UNIT03", type: "location", label: "Koramangala PS" },
  ],
  edges: [
    { source: "PER0001", target: "PER0002", type: "co-accused", weight: 2 },
    { source: "PER0001", target: "PER0003", type: "co-accused", weight: 1 },
    { source: "PER0004", target: "PER0005", type: "co-accused", weight: 1 },
    { source: "PER0001", target: "loc::UNIT01", type: "location", weight: 3 },
    { source: "PER0001", target: "loc::UNIT02", type: "location", weight: 1 },
    { source: "PER0002", target: "loc::UNIT01", type: "location", weight: 3 },
    { source: "PER0003", target: "loc::UNIT02", type: "location", weight: 1 },
    { source: "PER0004", target: "loc::UNIT01", type: "location", weight: 1 },
    { source: "PER0004", target: "loc::UNIT03", type: "location", weight: 1 },
    { source: "PER0005", target: "loc::UNIT03", type: "location", weight: 1 },
  ],
};

const W = 900, H = 560;

export default function NetworkGraph() {
  const [lookups, setLookups] = useState(null);
  const [rawGraph, setRawGraph] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [districtFilter, setDistrictFilter] = useState("");
  const [minCases, setMinCases] = useState(1);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(null);
  const [filtersOpen, setFiltersOpen] = useState(false);

  const [positions, setPositions] = useState({}); // id -> {x, y}
  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState(null);
  const simRef = useRef(null);
  const svgRef = useRef(null);
  const dragNode = useRef(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 1 });
  const panState = useRef(null);

  useEffect(() => {
    fetchJSON("get_lookups").then(setLookups).catch(() => setLookups(null));
  }, []);

  useEffect(() => {
    setLoading(true);
    setError(null);
    if (USE_MOCK) {
      setRawGraph(MOCK_GRAPH);
      setLoading(false);
      return;
    }
    const qs = new URLSearchParams();
    if (districtFilter) qs.set("district_id", districtFilter);
    if (minCases > 1) qs.set("min_cases", String(minCases));
    fetchJSON(`get_network_graph?${qs.toString()}`)
      .then(setRawGraph)
      .catch((err) => {
        console.warn("get_network_graph failed, using demo graph", err);
        setRawGraph(MOCK_GRAPH);
        setError(null);
      })
      .finally(() => setLoading(false));
  }, [districtFilter, minCases]);

  // ---- Force simulation: recompute whenever the graph data changes ----
  useEffect(() => {
    if (!rawGraph || !rawGraph.nodes.length) return;

    const nodes = rawGraph.nodes.map((n) => ({ ...n }));
    const links = rawGraph.edges.map((e) => ({ ...e }));

    const sim = forceSimulation(nodes)
      .force("link", forceLink(links).id((d) => d.id).distance((l) => (l.type === "co-accused" ? 70 : 110)).strength(0.5))
      .force("charge", forceManyBody().strength(-220))
      .force("center", forceCenter(W / 2, H / 2))
      .force("collide", forceCollide().radius((d) => (d.type === "location" ? 26 : 14 + Math.min(d.caseCount || 1, 6) * 2)))
      .stop();

    for (let i = 0; i < 250; i++) sim.tick();

    const pos = {};
    nodes.forEach((n) => { pos[n.id] = { x: n.x, y: n.y }; });
    setPositions(pos);
    simRef.current = { sim, nodes, links };
  }, [rawGraph]);

  const districtOptions = lookups?.districts || [];

  const filteredNodeIds = useMemo(() => {
    if (!rawGraph) return new Set();
    if (!query.trim()) return null; // null = no filter, show all
    const q = query.trim().toLowerCase();
    const matches = new Set(
      rawGraph.nodes.filter((n) => n.label.toLowerCase().includes(q)).map((n) => n.id)
    );
    // also include direct neighbors of matches so context stays visible
    rawGraph.edges.forEach((e) => {
      if (matches.has(e.source) || matches.has(e.source?.id)) matches.add(e.target?.id || e.target);
      if (matches.has(e.target) || matches.has(e.target?.id)) matches.add(e.source?.id || e.source);
    });
    return matches;
  }, [rawGraph, query]);

  const visibleNodes = useMemo(() => {
    if (!rawGraph) return [];
    if (!filteredNodeIds) return rawGraph.nodes;
    return rawGraph.nodes.filter((n) => filteredNodeIds.has(n.id));
  }, [rawGraph, filteredNodeIds]);

  const visibleEdgeSet = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);
  const visibleEdges = useMemo(() => {
    if (!rawGraph) return [];
    return rawGraph.edges.filter((e) => {
      const s = e.source?.id || e.source, t = e.target?.id || e.target;
      return visibleEdgeSet.has(s) && visibleEdgeSet.has(t);
    });
  }, [rawGraph, visibleEdgeSet]);

  // ---- Pan / zoom ----
  const onWheel = useCallback((ev) => {
    ev.preventDefault();
    setTransform((t) => {
      const k = Math.min(2.5, Math.max(0.4, t.k * (ev.deltaY < 0 ? 1.08 : 0.92)));
      return { ...t, k };
    });
  }, []);

  const onBgPointerDown = (ev) => {
    panState.current = { startX: ev.clientX, startY: ev.clientY, origin: { ...transform } };
  };
  const onPointerMove = (ev) => {
    // The SVG is rendered at width="100%" (often not literally 900px wide),
    // while all our math (node positions, transform.x/y) lives in the
    // viewBox's 900x560 unit space. Screen pixels have to be rescaled by
    // (viewBox size / actual rendered size) before they mean anything in
    // that space — skipping this is what made nodes drift instead of
    // tracking the cursor.
    const rect = svgRef.current.getBoundingClientRect();
    const scaleX = W / rect.width;
    const scaleY = H / rect.height;

    if (dragNode.current) {
      const { id } = dragNode.current;
      const x = ((ev.clientX - rect.left) * scaleX - transform.x) / transform.k;
      const y = ((ev.clientY - rect.top) * scaleY - transform.y) / transform.k;
      setPositions((p) => ({ ...p, [id]: { x, y } }));
      if (simRef.current) {
        const n = simRef.current.nodes.find((n) => n.id === id);
        if (n) { n.fx = x; n.fy = y; }
      }
      return;
    }
    if (panState.current) {
      const { startX, startY, origin } = panState.current;
      setTransform({
        ...origin,
        x: origin.x + (ev.clientX - startX) * scaleX,
        y: origin.y + (ev.clientY - startY) * scaleY,
      });
    }
  };
  const openProfile = (personKey) => {
    setProfile(null);
    setProfileError(null);
    setProfileLoading(true);
    fetchJSON(`get_person_profile?person_key=${encodeURIComponent(personKey)}`)
      .then(setProfile)
      .catch((err) => setProfileError(err.message))
      .finally(() => setProfileLoading(false));
  };

  const onPointerUp = () => {
    if (dragNode.current && simRef.current) {
      const n = simRef.current.nodes.find((n) => n.id === dragNode.current.id);
      if (n) { n.fx = null; n.fy = null; }
    }
    dragNode.current = null;
    panState.current = null;
  };

  if (loading) {
    return <div style={{ padding: 40, textAlign: "center", color: "var(--muted)" }}>Loading network…</div>;
  }
  if (error) {
    return (
      <div style={{ padding: 24, color: "#c17a7a" }}>
        Couldn't load the network graph: {error}
      </div>
    );
  }

  return (
    <div className="network-wrap">
      <style>{`
        .network-wrap { display: flex; flex-direction: column; gap: 12px; padding: 20px 28px; }
        .net-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .net-search { display: flex; align-items: center; gap: 8px; background: var(--panel-raised);
          border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; flex: 1; min-width: 200px; max-width: 320px; }
        .net-search input { background: none; border: none; outline: none; color: var(--text); font-size: 12.5px; width: 100%; }
        .net-btn { display: flex; align-items: center; gap: 6px; background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 8px; padding: 8px 12px; font-size: 12px; color: var(--text); cursor: pointer; }
        .net-btn.active { border-color: var(--gold); color: var(--gold-strong); }
        .net-legend { display: flex; gap: 16px; font-size: 11px; color: var(--muted); margin-left: auto; align-items: center; }
        .net-legend-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 5px; vertical-align: -1px; }
        .net-filters { display: flex; gap: 14px; align-items: center; background: var(--panel); border: 1px solid var(--border);
          border-radius: 10px; padding: 12px 16px; font-size: 12px; }
        .net-filters label { color: var(--muted); margin-right: 6px; }
        .net-filters select, .net-filters input[type=range] { background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 6px; color: var(--text); padding: 5px 8px; font-size: 12px; }
        .net-canvas-wrap { position: relative; background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
          overflow: hidden; }
        .net-side { position: absolute; top: 0; right: 0; bottom: 0; width: 260px; background: var(--panel-raised);
          border-left: 1px solid var(--border); padding: 18px; overflow-y: auto; }
        .net-side-close { position: absolute; top: 12px; right: 12px; cursor: pointer; color: var(--muted); }
        .net-side-title { font-size: 15px; font-weight: 700; color: var(--text); margin-bottom: 2px; }
        .net-side-sub { font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 16px; }
        .net-side-row { margin-bottom: 14px; }
        .net-side-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }
        .net-chip { display: inline-block; background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
          padding: 3px 8px; font-size: 11px; color: var(--text); margin: 2px 4px 2px 0; }
        .net-empty { padding: 60px 20px; text-align: center; color: var(--muted); font-size: 13px; }
        .net-profile-btn { display: flex; align-items: center; gap: 7px; margin-top: 4px; padding: 9px 12px;
          border-radius: 8px; background: var(--gold); color: var(--ink); font-size: 12px; font-weight: 700;
          cursor: pointer; }
        .net-profile-btn:hover { background: var(--gold-strong); }
        .profile-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.55); z-index: 2000;
          display: flex; align-items: center; justify-content: center; padding: 24px; }
        .profile-card { background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
          width: 720px; max-width: 100%; max-height: 85vh; overflow-y: auto; padding: 26px 28px; position: relative; }
        .profile-close { position: absolute; top: 16px; right: 16px; cursor: pointer; color: var(--muted); }
        .profile-title { font-size: 17px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
        .profile-sub { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin: 4px 0 18px; }
        .profile-stat-row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 18px; }
        .profile-stat { flex: 1; min-width: 130px; background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 10px; padding: 12px 14px; }
        .profile-stat-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 5px; }
        .profile-stat-value { font-size: 15px; font-weight: 700; color: var(--gold-strong); }
        .profile-section-title { font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
          color: var(--muted); margin: 18px 0 10px; }
        .profile-table { width: 100%; border-collapse: collapse; font-size: 12px; }
        .profile-table th { text-align: left; padding: 8px 10px; color: var(--muted); font-size: 10px;
          text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); }
        .profile-table td { padding: 9px 10px; border-bottom: 1px solid var(--border); }
        .profile-table tr:last-child td { border-bottom: none; }
        .profile-loading, .profile-error, .profile-suppressed { padding: 30px; text-align: center; color: var(--muted); font-size: 13px; }
        .profile-error { color: var(--wine); }
      `}</style>

      {/* Toolbar */}
      <div className="net-toolbar">
        <div className="net-search">
          <Search size={13} color="var(--muted)" />
          <input placeholder="Search a name…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <div className={`net-btn ${filtersOpen ? "active" : ""}`} onClick={() => setFiltersOpen((v) => !v)}>
          <SlidersHorizontal size={13} /> Filters
        </div>
        <div className="net-legend">
          <span><span className="net-legend-dot" style={{ background: "var(--wine)" }} />Repeat offender</span>
          <span><span className="net-legend-dot" style={{ background: "var(--gold)" }} />Single case</span>
          <span><span className="net-legend-dot" style={{ background: "var(--sage)" }} />Station</span>
        </div>
      </div>

      {filtersOpen && (
        <div className="net-filters">
          <div>
            <label>District</label>
            <select value={districtFilter} onChange={(e) => setDistrictFilter(e.target.value)}>
              <option value="">All districts</option>
              {districtOptions.map((d) => (
                <option key={d.DistrictID} value={d.DistrictID}>{d.DistrictName}</option>
              ))}
            </select>
          </div>
          <div>
            <label>Min. cases per person: {minCases}</label>
            <input type="range" min={1} max={4} value={minCases} onChange={(e) => setMinCases(Number(e.target.value))} />
          </div>
        </div>
      )}

      {/* Canvas */}
      <div className="net-canvas-wrap" style={{ height: H }}>
        {visibleNodes.length === 0 ? (
          <div className="net-empty">No matching persons or locations in this network.</div>
        ) : (
          <svg
            ref={svgRef}
            width="100%"
            height={H}
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            onWheel={onWheel}
            onPointerDown={onBgPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerLeave={onPointerUp}
            style={{ cursor: panState.current ? "grabbing" : "grab", touchAction: "none" }}
          >
            <g transform={`translate(${transform.x},${transform.y}) scale(${transform.k})`}>
              {visibleEdges.map((e, i) => {
                const s = positions[e.source?.id || e.source];
                const t = positions[e.target?.id || e.target];
                if (!s || !t) return null;
                const isCo = e.type === "co-accused";
                return (
                  <line
                    key={i}
                    x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                    stroke={isCo ? "var(--wine)" : "var(--border)"}
                    strokeWidth={isCo ? Math.min(1 + e.weight, 5) : 1.2}
                    strokeOpacity={isCo ? 0.55 : 0.4}
                    strokeDasharray={isCo ? "0" : "3,3"}
                  />
                );
              })}
              {visibleNodes.map((n) => {
                const p = positions[n.id];
                if (!p) return null;
                const isLoc = n.type === "location";
                const r = isLoc ? 12 : 8 + Math.min(n.caseCount || 1, 6) * 1.6;
                const fill = isLoc ? "var(--sage)" : n.repeatOffender ? "var(--wine)" : "var(--gold)";
                return (
                  <g
                    key={n.id}
                    transform={`translate(${p.x},${p.y})`}
                    onPointerDown={(ev) => { ev.stopPropagation(); dragNode.current = { id: n.id }; }}
                    onClick={() => setSelected(n)}
                    style={{ cursor: "pointer" }}
                  >
                    <circle r={r} fill={fill} fillOpacity={selected?.id === n.id ? 1 : 0.85}
                      stroke={selected?.id === n.id ? "var(--gold-strong)" : "var(--ink)"}
                      strokeWidth={selected?.id === n.id ? 2.5 : 1} />
                    {isLoc ? <MapPin size={11} x={-5.5} y={-5.5} color="var(--ink)" /> : null}
                    <text y={r + 13} textAnchor="middle" fontSize="9.5" fill="var(--muted)">
                      {n.label.length > 16 ? n.label.slice(0, 15) + "…" : n.label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        )}

        {selected && (
          <div className="net-side">
            <X className="net-side-close" size={16} onClick={() => setSelected(null)} />
            <div className="net-side-title">{selected.label}</div>
            <div className="net-side-sub">{selected.type === "location" ? "Police Station" : selected.repeatOffender ? "Repeat Offender" : "Accused"}</div>

            {selected.type === "person" && (
              <>
                <div className="net-side-row">
                  <div className="net-side-label">Cases linked</div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: "var(--gold-strong)" }}>{selected.caseCount}</div>
                </div>
                <div className="net-side-row">
                  <div className="net-side-label">Stations</div>
                  {(selected.stations || []).map((s) => <span key={s} className="net-chip">{s}</span>)}
                  {!selected.stations?.length && <span style={{ color: "var(--muted)", fontSize: 11.5 }}>None on file</span>}
                </div>
                <div className="net-side-row">
                  <div className="net-side-label">Modus Operandi</div>
                  {(selected.crimeTypes || []).map((c) => <span key={c} className="net-chip">{c}</span>)}
                  {!selected.crimeTypes?.length && <span style={{ color: "var(--muted)", fontSize: 11.5 }}>None on file</span>}
                </div>
                {selected.repeatOffender && (
                  <div className="net-side-row" style={{ display: "flex", gap: 6, alignItems: "center", color: "var(--wine)", fontSize: 11.5 }}>
                    <AlertTriangle size={13} /> Appears across {selected.stations?.length || 1} jurisdiction(s)
                  </div>
                )}
                <div
                  className="net-profile-btn"
                  onClick={() => openProfile(selected.id)}
                >
                  <History size={13} /> View full profile &amp; MO pattern
                </div>
              </>
            )}
            {selected.type === "location" && (
              <div className="net-side-row" style={{ color: "var(--muted)", fontSize: 12 }}>
                Click connected nodes to see who's tied to this station.
              </div>
            )}
          </div>
        )}
      </div>

      {/* REPEAT OFFENDER / MO PROFILE — opened from the side panel's
          "View full profile" button. Pulls every incident tied to this
          PersonKey across jurisdictions from /get_person_profile. */}
      {(profileLoading || profile || profileError) && (
        <div className="profile-overlay" onClick={() => { setProfile(null); setProfileError(null); }}>
          <div className="profile-card" onClick={(e) => e.stopPropagation()}>
            <X className="profile-close" size={18} onClick={() => { setProfile(null); setProfileError(null); }} />
            {profileLoading ? (
              <div className="profile-loading">Building offender profile…</div>
            ) : profileError ? (
              <div className="profile-error">Couldn't load profile: {profileError}</div>
            ) : (
              <>
                <div className="profile-title"><History size={16} /> {profile.label}</div>
                <div className="profile-sub">
                  {profile.repeatOffender ? "Repeat Offender Profile" : "Offender Profile"}
                  {profile.districts.length > 0 && ` · ${profile.districts.join(", ")}`}
                </div>

                <div className="profile-stat-row">
                  <div className="profile-stat">
                    <div className="profile-stat-label">Linked Cases</div>
                    <div className="profile-stat-value">{profile.caseCount}</div>
                  </div>
                  <div className="profile-stat">
                    <div className="profile-stat-label">Jurisdictions</div>
                    <div className="profile-stat-value">{profile.jurisdictions.length}</div>
                  </div>
                  <div className="profile-stat">
                    <div className="profile-stat-label">Dominant MO</div>
                    <div className="profile-stat-value" style={{ fontSize: 13 }}>{profile.modusOperandi.dominantCrimeType}</div>
                  </div>
                  <div className="profile-stat">
                    <div className="profile-stat-label"><Clock size={10} style={{ verticalAlign: -1 }} /> Typical Time</div>
                    <div className="profile-stat-value" style={{ fontSize: 13 }}>{profile.modusOperandi.dominantTimeBand}</div>
                  </div>
                </div>

                <div className="profile-section-title">Modus Operandi Breakdown</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 4 }}>
                  {profile.modusOperandi.crimeTypeBreakdown.map((c) => (
                    <span key={c.label} className="net-chip">{c.label} × {c.count}</span>
                  ))}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  {profile.modusOperandi.timeBandBreakdown.map((b) => (
                    <span key={b.label} className="net-chip"><Clock size={10} style={{ verticalAlign: -1, marginRight: 3 }} />{b.label} × {b.count}</span>
                  ))}
                </div>
                <div style={{ marginTop: 8 }}>
                  <span className="net-chip"><Gavel size={10} style={{ verticalAlign: -1, marginRight: 3 }} />Typical gravity: {profile.modusOperandi.dominantGravity}</span>
                </div>

                <div className="profile-section-title">Incident Trail</div>
                {profile.detailSuppressed ? (
                  <div className="profile-suppressed">
                    Incident-level detail is outside this role's data scope — aggregate MO pattern only.
                  </div>
                ) : (
                  <table className="profile-table">
                    <thead>
                      <tr><th>Case No</th><th>Date</th><th>Station</th><th>District</th><th>Crime Type</th><th>Time Band</th><th>Status</th></tr>
                    </thead>
                    <tbody>
                      {profile.incidents.map((inc) => (
                        <tr key={inc.caseId}>
                          <td className="mono">{inc.caseNo || inc.caseId}</td>
                          <td>{inc.date}</td>
                          <td>{inc.station}</td>
                          <td>{inc.district}</td>
                          <td>{inc.crimeType}</td>
                          <td>{inc.timeBand}</td>
                          <td>{inc.status}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}