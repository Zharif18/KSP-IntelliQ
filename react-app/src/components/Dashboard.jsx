import { useState, useEffect, useRef } from "react";
import {
  LayoutDashboard, Map, FileText, Users, BarChart3, MessageSquare,
  ShieldAlert, TrendingUp, Clock, Radio, Search, Bell, Send, X, ChevronRight
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid
} from "recharts";

/* ---------------------------------------------------------------------
   CATALYST INTEGRATION LAYER
   ---------------------------------------------------------------------
   In your actual Catalyst project, these calls hit your deployed
   Functions (e.g. /server/ksp_backend/get_current_officer). Here they
   fall back to demo data so the UI always renders, even outside
   Catalyst — swap USE_MOCK to false once your functions are live.
------------------------------------------------------------------------ */
const USE_MOCK = true;

async function callCatalystFunction(endpoint, fallback) {
  if (USE_MOCK) return fallback;
  try {
    const res = await fetch(`/server/ksp_backend/${endpoint}`, { credentials: "include" });
    if (!res.ok) throw new Error("Function call failed");
    return await res.json();
  } catch (err) {
    console.warn(`Catalyst call to ${endpoint} failed, using fallback`, err);
    return fallback;
  }
}

const MOCK_OFFICER = { name: "Inspector R. Naik", role: "Inspector", station: "Whitefield PS" };
const MOCK_STATS = { totalCrimes: 1284, openFirs: 96, solved: 812, activeInvestigations: 41 };
const MOCK_TREND = [
  { day: "Mon", crimes: 32 }, { day: "Tue", crimes: 41 }, { day: "Wed", crimes: 28 },
  { day: "Thu", crimes: 55 }, { day: "Fri", crimes: 47 }, { day: "Sat", crimes: 63 }, { day: "Sun", crimes: 39 },
];
const MOCK_HOTSPOTS = [
  { area: "Whitefield", risk: 78, type: "Vehicle Theft" },
  { area: "Indiranagar", risk: 64, type: "Chain Snatching" },
  { area: "Electronic City", risk: 52, type: "Burglary" },
  { area: "Yeshwanthpur", risk: 45, type: "Assault" },
];
const MOCK_ALERTS = [
  "FIR #1456 escalated to Priority 1 — Whitefield",
  "Pattern match: 3 burglaries linked, same MO — Indiranagar",
  "Patrol unit 12 dispatched — Electronic City",
  "New evidence uploaded to FIR #1402",
  "Hotspot forecast refreshed — 6 zones updated",
];

const NAV_ITEMS = [
  { icon: LayoutDashboard, label: "Dashboard", active: true },
  { icon: Map, label: "Crime Map" },
  { icon: FileText, label: "Cases" },
  { icon: Users, label: "Officers" },
  { icon: BarChart3, label: "Reports" },
];

function useLiveClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return time;
}

function HudFrame({ children, className = "" }) {
  return (
    <div className={`hud-card ${className}`}>
      <span className="hud-corner tl" /><span className="hud-corner tr" />
      <span className="hud-corner bl" /><span className="hud-corner br" />
      {children}
    </div>
  );
}

function StatCard({ label, value, accent, icon: Icon }) {
  return (
    <HudFrame className="stat-card">
      <div className="stat-icon" style={{ color: accent }}><Icon size={18} /></div>
      <div className="stat-value" style={{ color: accent }}>{value}</div>
      <div className="stat-label">{label}</div>
    </HudFrame>
  );
}

export default function KSPIntelliQDashboard() {
  const [officer, setOfficer] = useState(null);
  const [stats, setStats] = useState(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [messages, setMessages] = useState([
    { from: "ai", text: "IntelliQ Assistant online. Ask about FIRs, hotspots, or officer records." },
  ]);
  const [input, setInput] = useState("");
  const clock = useLiveClock();
  const scrollRef = useRef(null);

  useEffect(() => {
    callCatalystFunction("get_current_officer", { officer: MOCK_OFFICER }).then((d) => setOfficer(d.officer));
    callCatalystFunction("get_dashboard_stats", MOCK_STATS).then(setStats);
  }, []);

  const sendMessage = () => {
    if (!input.trim()) return;
    const q = input.trim();
    setMessages((m) => [...m, { from: "user", text: q }]);
    setInput("");
    setTimeout(() => {
      setMessages((m) => [...m, { from: "ai", text: `Searching records for: "${q}"… (connect Gemini via Catalyst Function to answer live)` }]);
    }, 500);
  };

  return (
    <div className="ksp-dash">
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

        .ksp-dash {
          --bg-void: #090d13;
          --bg-panel: #10161f;
          --bg-raised: #161e2b;
          --accent-amber: #f2a93b;
          --accent-cyan: #2dd4c8;
          --accent-red: #e4483d;
          --text-primary: #e8ecf1;
          --text-muted: #6b7686;
          --border-line: #232c3a;
          font-family: 'Inter', sans-serif;
          background: var(--bg-void);
          background-image:
            radial-gradient(circle at 15% 10%, rgba(45,212,200,0.05), transparent 40%),
            radial-gradient(circle at 85% 90%, rgba(242,169,59,0.05), transparent 40%);
          color: var(--text-primary);
          min-height: 100vh;
          display: flex;
          flex-direction: column;
        }
        .mono { font-family: 'JetBrains Mono', monospace; }
        .display { font-family: 'Rajdhani', sans-serif; font-weight: 700; letter-spacing: 0.03em; }

        /* Status bar */
        .status-bar {
          display: flex; align-items: center; justify-content: space-between;
          padding: 10px 20px; background: var(--bg-panel);
          border-bottom: 1px solid var(--border-line);
        }
        .brand { display: flex; align-items: center; gap: 10px; }
        .brand-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent-cyan);
          box-shadow: 0 0 8px var(--accent-cyan); animation: pulse 2s infinite; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
        .brand-title { font-size: 18px; }
        .brand-sub { color: var(--text-muted); font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase; }
        .status-right { display: flex; align-items: center; gap: 20px; }
        .clock { font-size: 13px; color: var(--accent-cyan); }
        .officer-chip { display: flex; align-items: center; gap: 8px; padding: 6px 12px;
          background: var(--bg-raised); border: 1px solid var(--border-line); border-radius: 4px; font-size: 12px; }
        .officer-role { color: var(--accent-amber); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }

        /* Body layout */
        .body-wrap { display: flex; flex: 1; }
        .nav-rail { width: 68px; background: var(--bg-panel); border-right: 1px solid var(--border-line);
          display: flex; flex-direction: column; align-items: center; padding: 20px 0; gap: 6px; }
        .nav-item { width: 44px; height: 44px; display: flex; align-items: center; justify-content: center;
          border-radius: 6px; color: var(--text-muted); cursor: pointer; transition: all 0.15s; }
        .nav-item:hover { color: var(--accent-cyan); background: var(--bg-raised); }
        .nav-item.active { color: var(--accent-cyan); background: var(--bg-raised);
          box-shadow: inset 2px 0 0 var(--accent-cyan); }

        .main { flex: 1; padding: 24px; overflow-y: auto; }
        .section-title { font-size: 12px; text-transform: uppercase; letter-spacing: 0.12em;
          color: var(--text-muted); margin-bottom: 12px; }

        /* HUD card frame — signature element */
        .hud-card { position: relative; background: var(--bg-panel); border: 1px solid var(--border-line);
          border-radius: 4px; padding: 18px; }
        .hud-corner { position: absolute; width: 10px; height: 10px; opacity: 0.7; }
        .hud-corner.tl { top: -1px; left: -1px; border-top: 2px solid var(--accent-cyan); border-left: 2px solid var(--accent-cyan); }
        .hud-corner.tr { top: -1px; right: -1px; border-top: 2px solid var(--accent-cyan); border-right: 2px solid var(--accent-cyan); }
        .hud-corner.bl { bottom: -1px; left: -1px; border-bottom: 2px solid var(--accent-cyan); border-left: 2px solid var(--accent-cyan); }
        .hud-corner.br { bottom: -1px; right: -1px; border-bottom: 2px solid var(--accent-cyan); border-right: 2px solid var(--accent-cyan); }

        .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }
        .stat-card { display: flex; flex-direction: column; gap: 6px; }
        .stat-icon { margin-bottom: 4px; }
        .stat-value { font-family: 'Rajdhani', sans-serif; font-weight: 700; font-size: 30px; line-height: 1; }
        .stat-label { color: var(--text-muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; }

        .grid-2 { display: grid; grid-template-columns: 1.4fr 1fr; gap: 16px; margin-bottom: 20px; }

        /* Radar sweep — signature motion element */
        .radar-box { position: relative; height: 220px; border-radius: 4px; overflow: hidden;
          background: radial-gradient(circle, rgba(45,212,200,0.08) 0%, transparent 70%), var(--bg-raised);
          border: 1px solid var(--border-line); display: flex; align-items: center; justify-content: center; }
        .radar-ring { position: absolute; border: 1px solid rgba(45,212,200,0.25); border-radius: 50%; }
        .radar-sweep { position: absolute; width: 50%; height: 2px; left: 50%; top: 50%; transform-origin: left center;
          background: linear-gradient(90deg, var(--accent-cyan), transparent); animation: sweep 4s linear infinite; }
        @keyframes sweep { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .radar-dot { position: absolute; width: 6px; height: 6px; border-radius: 50%; background: var(--accent-amber);
          box-shadow: 0 0 6px var(--accent-amber); }
        .radar-label { position: absolute; bottom: 10px; left: 12px; font-size: 11px; color: var(--text-muted); }

        .ticker-panel { display: flex; flex-direction: column; }
        .ticker-header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; color: var(--accent-amber); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }
        .ticker-list { display: flex; flex-direction: column; gap: 10px; overflow: hidden; max-height: 180px; }
        .ticker-item { font-size: 12.5px; color: var(--text-primary); border-left: 2px solid var(--accent-cyan); padding-left: 10px; opacity: 0.9; }

        .hotspot-row { display: flex; align-items: center; justify-content: space-between; padding: 8px 0;
          border-bottom: 1px solid var(--border-line); font-size: 13px; }
        .hotspot-row:last-child { border-bottom: none; }
        .risk-bar-track { width: 80px; height: 5px; background: var(--bg-raised); border-radius: 3px; overflow: hidden; }
        .risk-bar-fill { height: 100%; background: linear-gradient(90deg, var(--accent-cyan), var(--accent-amber)); }

        /* AI assistant dock */
        .ai-fab { position: fixed; bottom: 24px; right: 24px; width: 56px; height: 56px; border-radius: 50%;
          background: var(--accent-amber); color: #10161f; display: flex; align-items: center; justify-content: center;
          cursor: pointer; box-shadow: 0 4px 20px rgba(242,169,59,0.4); border: none; z-index: 50; }
        .ai-panel { position: fixed; bottom: 24px; right: 24px; width: 340px; height: 440px; background: var(--bg-panel);
          border: 1px solid var(--border-line); border-radius: 8px; display: flex; flex-direction: column; z-index: 50;
          box-shadow: 0 10px 40px rgba(0,0,0,0.5); }
        .ai-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px;
          border-bottom: 1px solid var(--border-line); }
        .ai-title { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--accent-cyan); }
        .ai-close { color: var(--text-muted); cursor: pointer; }
        .ai-messages { flex: 1; overflow-y: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
        .msg { font-size: 12.5px; max-width: 85%; padding: 8px 10px; border-radius: 6px; line-height: 1.4; }
        .msg.ai { background: var(--bg-raised); align-self: flex-start; color: var(--text-primary); }
        .msg.user { background: rgba(45,212,200,0.15); align-self: flex-end; color: var(--text-primary); }
        .ai-input-row { display: flex; gap: 6px; padding: 10px; border-top: 1px solid var(--border-line); }
        .ai-input { flex: 1; background: var(--bg-raised); border: 1px solid var(--border-line); border-radius: 4px;
          padding: 8px 10px; font-size: 12.5px; color: var(--text-primary); outline: none; }
        .ai-send { background: var(--accent-amber); border: none; border-radius: 4px; width: 34px; display: flex;
          align-items: center; justify-content: center; color: #10161f; cursor: pointer; }
      `}</style>

      {/* STATUS BAR */}
      <div className="status-bar">
        <div className="brand">
          <span className="brand-dot" />
          <div>
            <div className="brand-title display">KSP INTELLIQ</div>
            <div className="brand-sub">Crime Intelligence &amp; Decision Support</div>
          </div>
        </div>
        <div className="status-right">
          <div className="clock mono">{clock.toLocaleTimeString()} · {clock.toLocaleDateString()}</div>
          <Bell size={16} color="var(--text-muted)" />
          {officer && (
            <div className="officer-chip">
              <div>
                <div>{officer.name}</div>
                <div className="officer-role">{officer.role} · {officer.station}</div>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="body-wrap">
        {/* NAV RAIL */}
        <div className="nav-rail">
          {NAV_ITEMS.map(({ icon: Icon, label, active }) => (
            <div key={label} className={`nav-item ${active ? "active" : ""}`} title={label}>
              <Icon size={19} />
            </div>
          ))}
        </div>

        {/* MAIN */}
        <div className="main">
          <div className="section-title">Live Overview</div>
          <div className="kpi-row">
            <StatCard label="Total Crimes" value={stats?.totalCrimes ?? "—"} accent="var(--accent-cyan)" icon={ShieldAlert} />
            <StatCard label="Open FIRs" value={stats?.openFirs ?? "—"} accent="var(--accent-amber)" icon={FileText} />
            <StatCard label="Solved Cases" value={stats?.solved ?? "—"} accent="var(--accent-cyan)" icon={TrendingUp} />
            <StatCard label="Active Investigations" value={stats?.activeInvestigations ?? "—"} accent="var(--accent-red)" icon={Search} />
          </div>

          <div className="grid-2">
            <HudFrame>
              <div className="section-title" style={{ marginBottom: 8 }}>Hotspot Scan — District Grid</div>
              <div className="radar-box">
                <div className="radar-ring" style={{ width: 60, height: 60 }} />
                <div className="radar-ring" style={{ width: 120, height: 120 }} />
                <div className="radar-ring" style={{ width: 180, height: 180 }} />
                <div className="radar-sweep" />
                <div className="radar-dot" style={{ top: "30%", left: "62%" }} />
                <div className="radar-dot" style={{ top: "68%", left: "38%" }} />
                <div className="radar-dot" style={{ top: "45%", left: "25%" }} />
                <div className="radar-label mono">SCAN ACTIVE · 6 ZONES TRACKED</div>
              </div>
            </HudFrame>

            <HudFrame className="ticker-panel">
              <div className="ticker-header"><Radio size={13} /> Live Alerts</div>
              <div className="ticker-list">
                {MOCK_ALERTS.map((a, i) => <div key={i} className="ticker-item mono">{a}</div>)}
              </div>
            </HudFrame>
          </div>

          <div className="grid-2">
            <HudFrame>
              <div className="section-title">7-Day Crime Trend</div>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={MOCK_TREND}>
                  <CartesianGrid stroke="var(--border-line)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="day" stroke="var(--text-muted)" fontSize={11} tickLine={false} axisLine={false} />
                  <YAxis stroke="var(--text-muted)" fontSize={11} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ background: "var(--bg-raised)", border: "1px solid var(--border-line)", fontSize: 12 }} />
                  <Line type="monotone" dataKey="crimes" stroke="var(--accent-cyan)" strokeWidth={2} dot={{ r: 3, fill: "var(--accent-amber)" }} />
                </LineChart>
              </ResponsiveContainer>
            </HudFrame>

            <HudFrame>
              <div className="section-title">Predictive Hotspots</div>
              {MOCK_HOTSPOTS.map((h) => (
                <div className="hotspot-row" key={h.area}>
                  <div>
                    <div>{h.area}</div>
                    <div style={{ color: "var(--text-muted)", fontSize: 11 }}>{h.type}</div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div className="risk-bar-track"><div className="risk-bar-fill" style={{ width: `${h.risk}%` }} /></div>
                    <span className="mono" style={{ fontSize: 11, color: "var(--accent-amber)" }}>{h.risk}%</span>
                  </div>
                </div>
              ))}
            </HudFrame>
          </div>
        </div>
      </div>

      {/* AI ASSISTANT DOCK */}
      {!assistantOpen && (
        <button className="ai-fab" onClick={() => setAssistantOpen(true)}>
          <MessageSquare size={22} />
        </button>
      )}
      {assistantOpen && (
        <div className="ai-panel">
          <div className="ai-header">
            <div className="ai-title"><MessageSquare size={14} /> IntelliQ Assistant</div>
            <X size={16} className="ai-close" onClick={() => setAssistantOpen(false)} />
          </div>
          <div className="ai-messages" ref={scrollRef}>
            {messages.map((m, i) => <div key={i} className={`msg ${m.from}`}>{m.text}</div>)}
          </div>
          <div className="ai-input-row">
            <input
              className="ai-input"
              placeholder="Ask about a FIR, area, or suspect…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendMessage()}
            />
            <button className="ai-send" onClick={sendMessage}><Send size={14} /></button>
          </div>
        </div>
      )}
    </div>
  );
}