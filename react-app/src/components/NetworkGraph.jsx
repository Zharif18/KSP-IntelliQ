import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide, forceX, forceY } from "d3-force";
import { Search, SlidersHorizontal, X, MapPin, AlertTriangle, History, Clock, Gavel, Sparkles, Printer, Users, ZoomIn, ZoomOut, Maximize2, Shield, Maximize, Minimize } from "lucide-react";

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

const DEFAULT_W = 900, DEFAULT_H = 560;

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
  const [hovered, setHovered] = useState(null); // node id currently hovered
  const [hoverPos, setHoverPos] = useState(null); // cursor pos (wrap-relative) for the hover tooltip
  const [profile, setProfile] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState(null);
  const [brief, setBrief] = useState(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [briefError, setBriefError] = useState(null);
  const simRef = useRef(null);
  const svgRef = useRef(null);
  const wrapRef = useRef(null);
  const dragNode = useRef(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, k: 1 });
  const panState = useRef(null);

  // Fullscreen: gives the force layout real extra room to spread nodes
  // into (not just a bigger stretched view of the same 900x560 layout),
  // and is implemented with plain fixed positioning rather than the
  // browser Fullscreen API — requestFullscreen() throws/rejects inside
  // sandboxed iframes and some embedded contexts, which is exactly where
  // this app tends to run, so a CSS-only overlay avoids that failure
  // mode entirely.
  const [fullscreen, setFullscreen] = useState(false);
  const [dims, setDims] = useState({ w: DEFAULT_W, h: DEFAULT_H });
  const W = dims.w, H = dims.h;

  const enterFullscreen = useCallback(() => {
    const margin = 16;
    setDims({ w: Math.max(320, window.innerWidth - margin * 2), h: Math.max(320, window.innerHeight - margin * 2) });
    setFullscreen(true);
  }, []);
  const exitFullscreen = useCallback(() => {
    setDims({ w: DEFAULT_W, h: DEFAULT_H });
    setFullscreen(false);
  }, []);

  useEffect(() => {
    if (!fullscreen) return;
    const margin = 16;
    const onResize = () => setDims({ w: Math.max(320, window.innerWidth - margin * 2), h: Math.max(320, window.innerHeight - margin * 2) });
    const onKey = (ev) => { if (ev.key === "Escape") exitFullscreen(); };
    window.addEventListener("resize", onResize);
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("resize", onResize);
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [fullscreen, exitFullscreen]);

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
  // Repulsion, link distance, and collide radius all scale with node count —
  // the old fixed strengths were tuned for the ~8-node demo graph and turned
  // into a crushed hairball once a real district pulled in 60-120 people.
  useEffect(() => {
    if (!rawGraph || !rawGraph.nodes.length) return;

    const nodes = rawGraph.nodes.map((n) => ({ ...n }));
    const links = rawGraph.edges.map((e) => ({ ...e }));
    const n = nodes.length;
    const density = Math.min(1, n / 60); // 0 (sparse) → 1 (dense/120 cap)
    const chargeStrength = -220 - density * 480; // -220 sparse … -700 dense
    const linkDistance = (l) => {
      const base = l.type === "co-accused" ? 70 : 110;
      return base + density * 90;
    };

    const sim = forceSimulation(nodes)
      .force("link", forceLink(links).id((d) => d.id).distance(linkDistance).strength(0.4))
      .force("charge", forceManyBody().strength(chargeStrength).distanceMax(600))
      .force("center", forceCenter(W / 2, H / 2))
      .force("x", forceX(W / 2).strength(0.03))
      .force("y", forceY(H / 2).strength(0.03))
      .force("collide", forceCollide().radius((d) => (d.type === "location" ? 30 : 20 + Math.min(d.caseCount || 1, 6) * 2.2)).strength(0.9))
      .stop();

    const ticks = 250 + Math.round(density * 150); // denser graphs get more time to settle
    for (let i = 0; i < ticks; i++) sim.tick();

    // Auto-fit: rescale/recenter so the settled layout fills the canvas
    // with breathing room, instead of spilling past the fixed viewBox or
    // sitting tiny in the middle of it.
    const xs = nodes.map((d) => d.x), ys = nodes.map((d) => d.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const pad = 60;
    const spanX = Math.max(maxX - minX, 1), spanY = Math.max(maxY - minY, 1);
    const k = Math.min(2, Math.max(0.4, Math.min((W - pad * 2) / spanX, (H - pad * 2) / spanY)));
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    setTransform({ x: W / 2 - cx * k, y: H / 2 - cy * k, k });

    const pos = {};
    nodes.forEach((d) => { pos[d.id] = { x: d.x, y: d.y }; });
    setPositions(pos);
    simRef.current = { sim, nodes, links };
  }, [rawGraph, W, H]);

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

  const zoomBy = useCallback((factor) => {
    setTransform((t) => {
      const k = Math.min(2.5, Math.max(0.4, t.k * factor));
      // zoom around the canvas center so it doesn't drift off-screen
      const cx = W / 2, cy = H / 2;
      return {
        k,
        x: cx - ((cx - t.x) / t.k) * k,
        y: cy - ((cy - t.y) / t.k) * k,
      };
    });
  }, [W, H]);

  const fitToView = useCallback(() => {
    const ids = Object.keys(positions);
    if (!ids.length) return;
    const xs = ids.map((id) => positions[id].x), ys = ids.map((id) => positions[id].y);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const pad = 60;
    const spanX = Math.max(maxX - minX, 1), spanY = Math.max(maxY - minY, 1);
    const k = Math.min(2, Math.max(0.4, Math.min((W - pad * 2) / spanX, (H - pad * 2) / spanY)));
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    setTransform({ x: W / 2 - cx * k, y: H / 2 - cy * k, k });
  }, [positions, W, H]);

  // Neighbor set for whichever node is hovered (falls back to selected) —
  // drives the "highlight this node's ties, fade the rest" dimming below,
  // which is what actually cuts through clutter on a dense graph.
  const focusId = hovered || selected?.id || null;
  const focusNeighbors = useMemo(() => {
    if (!focusId || !rawGraph) return null;
    const set = new Set([focusId]);
    rawGraph.edges.forEach((e) => {
      const s = e.source?.id || e.source, t = e.target?.id || e.target;
      if (s === focusId) set.add(t);
      if (t === focusId) set.add(s);
    });
    return set;
  }, [focusId, rawGraph]);

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
  // Tooltip follows the cursor, in coordinates relative to the canvas
  // wrapper (so it can be positioned with plain absolute CSS regardless
  // of pan/zoom/viewBox scaling). No debounce — it needs to appear the
  // instant the pointer lands on a node, not after a delay.
  const updateHoverPos = useCallback((ev) => {
    const rect = wrapRef.current.getBoundingClientRect();
    setHoverPos({ x: ev.clientX - rect.left, y: ev.clientY - rect.top });
  }, []);

  const openProfile = (personKey) => {
    setProfile(null);
    setProfileError(null);
    setProfileLoading(true);
    setBrief(null);
    setBriefError(null);
    fetchJSON(`get_person_profile?person_key=${encodeURIComponent(personKey)}`)
      .then(setProfile)
      .catch((err) => setProfileError(err.message))
      .finally(() => setProfileLoading(false));
  };

  const closeProfile = () => {
    setProfile(null);
    setProfileError(null);
    setBrief(null);
    setBriefError(null);
  };

  const generateBrief = () => {
    if (!profile) return;
    setBrief(null);
    setBriefError(null);
    setBriefLoading(true);
    fetchJSON(`get_investigation_brief?person_key=${encodeURIComponent(profile.personKey)}`)
      .then(setBrief)
      .catch((err) => setBriefError(err.message))
      .finally(() => setBriefLoading(false));
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
        .net-zoom-group { display: flex; gap: 4px; }
        .net-zoom-btn { padding: 8px 9px; }
        .net-cap-hint { font-size: 11px; color: var(--muted); background: var(--panel); border: 1px solid var(--border);
          border-radius: 8px; padding: 8px 10px; }
        .net-legend { display: flex; gap: 16px; font-size: 11px; color: var(--muted); margin-left: auto; align-items: center; }
        .net-legend-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; margin-right: 5px; vertical-align: -1px; }
        .net-filters { display: flex; gap: 14px; align-items: center; background: var(--panel); border: 1px solid var(--border);
          border-radius: 10px; padding: 12px 16px; font-size: 12px; }
        .net-filters label { color: var(--muted); margin-right: 6px; }
        .net-filters select, .net-filters input[type=range] { background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 6px; color: var(--text); padding: 5px 8px; font-size: 12px; }
        .net-canvas-wrap { position: relative; background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
          overflow: hidden; }
        .net-fullscreen-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 999; }
        .net-canvas-wrap.net-fullscreen { position: fixed; top: 16px; left: 16px; right: 16px; bottom: 16px;
          z-index: 1000; width: auto; height: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.5); }
        .net-fullscreen-exit { position: absolute; top: 12px; right: 12px; z-index: 1001; background: var(--panel);
          box-shadow: 0 2px 10px rgba(0,0,0,0.25); }
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
        .net-tooltip { position: absolute; z-index: 500; pointer-events: none; background: var(--panel-raised);
          border: 1px solid var(--border); border-radius: 8px; padding: 9px 11px; box-shadow: 0 6px 20px rgba(0,0,0,0.3);
          font-size: 11.5px; line-height: 1.5; }
        .net-tooltip-title { font-weight: 700; color: var(--text); margin-bottom: 3px; font-size: 12.5px; }
        .net-tooltip-row { color: var(--text); }
        .net-tooltip-muted { color: var(--muted); font-size: 11px; margin-top: 2px; }
        .net-tooltip-flag { color: var(--wine); font-weight: 600; }
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
        .profile-title-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
        .brief-btn { display: flex; align-items: center; gap: 6px; background: var(--gold); color: var(--ink);
          border-radius: 8px; padding: 8px 12px; font-size: 11.5px; font-weight: 700; cursor: pointer; white-space: nowrap; }
        .brief-btn:hover { background: var(--gold-strong); }
        .brief-block { margin-top: 22px; border-top: 1px dashed var(--border); padding-top: 16px; }
        .brief-section-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
        .brief-ai-badge { font-size: 9.5px; font-weight: 600; text-transform: none; letter-spacing: 0; color: var(--muted);
          background: var(--panel-raised); border: 1px solid var(--border); border-radius: 6px; padding: 3px 8px; }
        .brief-summary { font-size: 12.5px; line-height: 1.65; color: var(--text); margin: 0 0 14px; }
        .brief-subhead { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
          color: var(--muted); margin: 0 0 8px; }
        .brief-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap;
          margin-top: 6px; font-size: 10.5px; color: var(--muted); }
        .brief-print-btn { display: flex; align-items: center; gap: 5px; background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 6px; padding: 5px 10px; font-size: 11px; color: var(--text); cursor: pointer; }
        .brief-print-btn:hover { border-color: var(--gold); color: var(--gold-strong); }
        @media print {
          body * { visibility: hidden; }
          #investigation-brief-print, #investigation-brief-print * { visibility: visible; }
          #investigation-brief-print { position: absolute; top: 0; left: 0; width: 100%; }
          .brief-print-btn { display: none; }
        }
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
        <div className="net-zoom-group">
          <div className="net-btn net-zoom-btn" title="Zoom out" onClick={() => zoomBy(0.85)}><ZoomOut size={13} /></div>
          <div className="net-btn net-zoom-btn" title="Zoom in" onClick={() => zoomBy(1.15)}><ZoomIn size={13} /></div>
          <div className="net-btn net-zoom-btn" title="Fit to view" onClick={fitToView}><Maximize2 size={13} /></div>
          <div className="net-btn net-zoom-btn" title={fullscreen ? "Exit fullscreen (Esc)" : "Fullscreen"} onClick={fullscreen ? exitFullscreen : enterFullscreen}>
            {fullscreen ? <Minimize size={13} /> : <Maximize size={13} />}
          </div>
        </div>
        {rawGraph?.nodes?.length >= 120 && (
          <div className="net-cap-hint" title="Showing the first 120 people for this filter. Narrow with District or Min. cases to see the rest.">
            Showing top 120 — narrow filters to see more
          </div>
        )}
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
      {fullscreen && <div className="net-fullscreen-backdrop" onClick={exitFullscreen} />}
      <div
        ref={wrapRef}
        className={`net-canvas-wrap ${fullscreen ? "net-fullscreen" : ""}`}
        style={fullscreen ? undefined : { height: H }}
      >
        {fullscreen && (
          <div className="net-btn net-fullscreen-exit" title="Exit fullscreen (Esc)" onClick={exitFullscreen}>
            <Minimize size={13} /> Exit fullscreen
          </div>
        )}
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
                const sId = e.source?.id || e.source, tId = e.target?.id || e.target;
                const s = positions[sId];
                const t = positions[tId];
                if (!s || !t) return null;
                const isCo = e.type === "co-accused";
                // When a node is focused (hover or select), fade every edge
                // not touching it — this is what actually declutters a
                // dense graph instead of just letting everything sit at
                // equal, competing opacity.
                const hasFocus = !!focusNeighbors;
                const inFocus = !hasFocus || (focusNeighbors.has(sId) && focusNeighbors.has(tId));
                // Three tiers instead of two: a muted resting opacity so the
                // whole graph isn't shouting at once, a bright tier for
                // edges touching the focused node, and a near-invisible
                // tier for everything else once focus narrows the view.
                const strokeOpacity = hasFocus
                  ? (inFocus ? (isCo ? 0.65 : 0.45) : 0.05)
                  : (isCo ? 0.32 : 0.2);
                return (
                  <line
                    key={i}
                    x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                    stroke={isCo ? "var(--wine)" : "var(--border)"}
                    strokeWidth={isCo ? Math.min(1 + e.weight, 5) : 1.2}
                    strokeOpacity={strokeOpacity}
                    strokeDasharray={isCo ? "0" : "3,3"}
                    style={{ transition: "stroke-opacity 120ms ease" }}
                  />
                );
              })}
              {visibleNodes.map((n) => {
                const p = positions[n.id];
                if (!p) return null;
                const isLoc = n.type === "location";
                const r = isLoc ? 12 : 8 + Math.min(n.caseCount || 1, 6) * 1.6;
                const fill = isLoc ? "var(--sage)" : n.repeatOffender ? "var(--wine)" : "var(--gold)";
                const isFocused = focusId === n.id;
                const inFocus = !focusNeighbors || focusNeighbors.has(n.id);
                // Labels always-on gets unreadable past ~40 nodes, so past
                // that point only the focused/selected node and its direct
                // neighbors get a label — everyone else surfaces on hover.
                const showLabel = visibleNodes.length <= 40 || isFocused || (focusNeighbors && focusNeighbors.has(n.id));
                return (
                  <g
                    key={n.id}
                    transform={`translate(${p.x},${p.y})`}
                    onPointerDown={(ev) => { ev.stopPropagation(); dragNode.current = { id: n.id }; }}
                    onPointerEnter={(ev) => { setHovered(n.id); updateHoverPos(ev); }}
                    onPointerMove={(ev) => { if (!dragNode.current) updateHoverPos(ev); }}
                    onPointerLeave={() => { setHovered((h) => (h === n.id ? null : h)); setHoverPos(null); }}
                    onClick={() => setSelected(n)}
                    style={{ cursor: "pointer", opacity: inFocus ? 1 : 0.25, transition: "opacity 120ms ease" }}
                  >
                    <circle r={r} fill={fill} fillOpacity={selected?.id === n.id ? 1 : 0.85}
                      stroke={selected?.id === n.id || isFocused ? "var(--gold-strong)" : "var(--ink)"}
                      strokeWidth={selected?.id === n.id || isFocused ? 2.5 : 1} />
                    {isLoc ? <MapPin size={11} x={-5.5} y={-5.5} color="var(--ink)" /> : null}
                    {showLabel && (
                      <text y={r + 13} textAnchor="middle" fontSize="9.5" fill="var(--muted)">
                        {n.label.length > 16 ? n.label.slice(0, 15) + "…" : n.label}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>
        )}

        {hovered && hoverPos && (() => {
          const n = rawGraph?.nodes?.find((x) => x.id === hovered);
          if (!n) return null;
          const wrapEl = wrapRef.current;
          const wrapW = wrapEl ? wrapEl.clientWidth : 900;
          const wrapH = wrapEl ? wrapEl.clientHeight : H;
          const tipW = 220;
          // Flip to the left/above the cursor if it would run off the edge,
          // so the tooltip never gets clipped by the canvas border.
          const left = hoverPos.x + 18 + tipW > wrapW ? hoverPos.x - tipW - 14 : hoverPos.x + 18;
          const top = hoverPos.y + 130 > wrapH ? hoverPos.y - 110 : hoverPos.y + 16;
          return (
            <div className="net-tooltip" style={{ left, top, width: tipW }}>
              <div className="net-tooltip-title">{n.label}</div>
              {n.type === "location" ? (
                <div className="net-tooltip-row">Police Station</div>
              ) : (
                <>
                  <div className="net-tooltip-row">
                    {n.caseCount} case{n.caseCount === 1 ? "" : "s"}
                    {n.repeatOffender && <span className="net-tooltip-flag"> · Repeat offender</span>}
                  </div>
                  {n.stations?.length > 0 && (
                    <div className="net-tooltip-row net-tooltip-muted">{n.stations.join(", ")}</div>
                  )}
                  {n.crimeTypes?.length > 0 && (
                    <div className="net-tooltip-row net-tooltip-muted">{n.crimeTypes.join(", ")}</div>
                  )}
                </>
              )}
            </div>
          );
        })()}

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
                {selected.investigatingOfficers && (
                  <div className="net-side-row">
                    <div className="net-side-label"><Shield size={10} style={{ verticalAlign: -1, marginRight: 3 }} />Investigating Officer(s)</div>
                    {selected.investigatingOfficers.length > 0
                      ? selected.investigatingOfficers.map((o) => <span key={o} className="net-chip">{o}</span>)
                      : <span style={{ color: "var(--muted)", fontSize: 11.5 }}>None on file</span>}
                  </div>
                )}
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
        <div className="profile-overlay" onClick={closeProfile}>
          <div className="profile-card" onClick={(e) => e.stopPropagation()}>
            <X className="profile-close" size={18} onClick={closeProfile} />
            {profileLoading ? (
              <div className="profile-loading">Building offender profile…</div>
            ) : profileError ? (
              <div className="profile-error">Couldn't load profile: {profileError}</div>
            ) : (
              <>
                <div className="profile-title-row">
                  <div>
                    <div className="profile-title"><History size={16} /> {profile.label}</div>
                    <div className="profile-sub">
                      {profile.repeatOffender ? "Repeat Offender Profile" : "Offender Profile"}
                      {profile.districts.length > 0 && ` · ${profile.districts.join(", ")}`}
                    </div>
                  </div>
                  {!brief && !briefLoading && (
                    <div className="brief-btn" onClick={generateBrief}>
                      <Sparkles size={13} /> Generate Investigation Brief
                    </div>
                  )}
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

                {/* AUTO-GENERATED INVESTIGATION BRIEF
                    Composed server-side from the same scoped case data
                    above (MO pattern, associates, recurring narrative
                    terms) — never independent of it, so nothing here can
                    say something the rest of the profile doesn't back up. */}
                {(briefLoading || brief || briefError) && (
                  <div className="brief-block" id="investigation-brief-print">
                    <div className="profile-section-title brief-section-title">
                      <span><Sparkles size={12} style={{ verticalAlign: -2, marginRight: 5 }} />Investigation Brief</span>
                      {brief && (
                        <span className="brief-ai-badge">AI-summarized · verify against source records</span>
                      )}
                    </div>

                    {briefLoading ? (
                      <div className="profile-loading">Composing brief from linked case data…</div>
                    ) : briefError ? (
                      <div className="profile-error">Couldn't generate brief: {briefError}</div>
                    ) : (
                      <>
                        <p className="brief-summary">{brief.summary}</p>

                        {brief.associates.length > 0 && (
                          <>
                            <div className="brief-subhead"><Users size={11} style={{ verticalAlign: -1, marginRight: 4 }} />Known Associates</div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                              {brief.associates.map((a) => (
                                <span key={a.personKey} className="net-chip">{a.label} × {a.sharedCases}</span>
                              ))}
                            </div>
                          </>
                        )}

                        {brief.narrativeKeywords.length > 0 && (
                          <>
                            <div className="brief-subhead">Recurring Case-Narrative Terms</div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                              {brief.narrativeKeywords.map((k) => (
                                <span key={k} className="net-chip">{k}</span>
                              ))}
                            </div>
                          </>
                        )}

                        <div className="brief-footer">
                          <span>Generated {new Date(brief.generatedAt).toLocaleString()} · for briefing use only, not a substitute for the case file</span>
                          <div className="brief-print-btn" onClick={() => window.print()}>
                            <Printer size={12} /> Print
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}