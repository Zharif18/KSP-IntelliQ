import { useState, useEffect, useRef } from "react";
import {
  Shield, FileText, Users, Map, BarChart3, MessageSquare,
  Search, Bell, Send, Radio, ChevronLeft, Sun, Moon, LogOut, Share2, ShieldAlert, AlertTriangle, Scale
} from "lucide-react";
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid
} from "recharts";
import CrimeMap from "./CrimeMap";
import FIRManagement from "./FIRManagement";
import NetworkGraph from "./NetworkGraph";
import Officers from "./Officers";
import Reports from "./Reports";
import AuditLog from "./AuditLog";
import BiasAudit from "./BiasAudit";
import catalyst from "../catalystInit.jsx";

/* ---------------------------------------------------------------------
   RBAC tab visibility — every tab still exists for every rank; what
   changes is which ones a given accessRole is offered, mirroring the
   backend's own scoping (main.py RANK_ACCESS) so the UI never dangles
   a tab that the API would just 403 on anyway.

   accessRole values come from get_current_officer's "accessRole" field
   — one of the seven Karnataka Police ranks, in ascending seniority:
     Constable < Head Constable < Asst. Sub-Inspector < Sub-Inspector <
     Inspector < Deputy Superintendent < Superintendent of Police
   ("Unassigned" — no recognized RankID on file — gets the same minimal
   set a Constable gets: fail-safe, not fail-open.)
------------------------------------------------------------------------ */
const RANK_LEVEL = {
  "Constable": 1,
  "Head Constable": 2,
  "Asst. Sub-Inspector": 3,
  "Sub-Inspector": 4,
  "Inspector": 5,
  "Deputy Superintendent": 6,
  "Superintendent of Police": 7,
};

const ALL_TABS = [
  { icon: BarChart3, label: "Dashboard" },
  { icon: Map, label: "Crime Map" },
  { icon: Share2, label: "Network" },
  { icon: FileText, label: "Cases" },
  { icon: Users, label: "Officers" },
  { icon: Search, label: "Reports" },
  { icon: ShieldAlert, label: "Audit Log" },
  { icon: Scale, label: "Bias Audit" },
];

function tabsForRole(accessRole) {
  const hidden = new Set();
  const level = RANK_LEVEL[accessRole] || 0; // "Unassigned" / unknown -> 0, most restrictive

  // Audit Log: Superintendent of Police only (level 7), mirroring
  // get_audit_log's OVERSIGHT_MIN_LEVEL check server-side. Reading
  // who-looked-at-what is itself privileged, so even a DySP doesn't see it.
  if (level < 7) hidden.add("Audit Log");

  // Bias Audit is gated the exact same way server-side
  // (get_bias_audit also requires OVERSIGHT_MIN_LEVEL) — Superintendent
  // of Police only, so the tab never dangles for a rank the API would
  // just 403 on.
  if (level < 7) hidden.add("Bias Audit");

  return ALL_TABS.filter((t) => !hidden.has(t.label));
}

/* ---------------------------------------------------------------------
   CATALYST INTEGRATION LAYER
   Calls your deployed Catalyst Functions (e.g. /server/ksp_backend/...).
   Falls back to demo data so the UI always renders. Flip USE_MOCK to
   false once your functions are live.
------------------------------------------------------------------------ */
const USE_MOCK = false;

async function callCatalystFunction(endpoint, fallback) {
  if (USE_MOCK) return fallback;
  try {
    const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, { credentials: "include" });
    if (!res.ok) throw new Error("Function call failed");
    return await res.json();
  } catch (err) {
    console.warn(`Catalyst call to ${endpoint} failed, using fallback`, err);
    return fallback;
  }
}

const MOCK_STATS = { totalCrimes: 1284, openFirs: 96, solved: 812, activeInvestigations: 41 };

// Hotspot Forecast, Incident Log, and 7-Day Trend are now live —
// fetched from /get_hotspots, /get_incident_log, /get_crime_trend.
// Same fetchJSON pattern CrimeMap.jsx already uses: no silent
// fallback to fake data, real loading/error states instead, so the
// panel never quietly shows placeholder numbers as if they were live.
async function fetchJSON(endpoint, opts) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, {
    credentials: "include",
    ...opts,
  });
  if (!res.ok) throw new Error(`${endpoint} failed (${res.status})`);
  return res.json();
}

function useLiveClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return time;
}

function CaseCard({ children, className = "", stamp }) {
  return (
    <div className={`case-card ${className}`}>
      {stamp && <span className="stamp">{stamp}</span>}
      {children}
    </div>
  );
}

function StatCard({ label, value, tone, icon: Icon }) {
  return (
    <CaseCard className={`stat-card tone-${tone}`}>
      <div className="stat-top">
        <div className="stat-icon"><Icon size={15} /></div>
      </div>
      <div className="stat-value display">{value}</div>
      <div className="stat-label">{label}</div>
    </CaseCard>
  );
}

export default function KSPIntelliQDashboard() {
  const [theme, setTheme] = useState("dark");
  const [activeTab, setActiveTab] = useState("Dashboard");
  const [officer, setOfficer] = useState(null);
  const [accessDenied, setAccessDenied] = useState(null); // holds the message, or null
  const [stats, setStats] = useState(null);
  const [hotspots, setHotspots] = useState([]);
  const [hotspotsLoading, setHotspotsLoading] = useState(true);
  const [hotspotsError, setHotspotsError] = useState(null);
  const [incidentLog, setIncidentLog] = useState([]);
  const [logLoading, setLogLoading] = useState(true);
  const [logError, setLogError] = useState(null);
  const [trend, setTrend] = useState([]);
  const [trendLoading, setTrendLoading] = useState(true);
  const [trendError, setTrendError] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertsError, setAlertsError] = useState(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [messages, setMessages] = useState([
    { from: "ai", text: "IntelliQ Assistant ready. Ask about a FIR, area, or suspect." },
  ]);
  const [input, setInput] = useState("");
  const [assistantThinking, setAssistantThinking] = useState(false);
  const clock = useLiveClock();
  const scrollRef = useRef(null);

  const fetchOfficer = () => {
    fetch("/server/ksp_intelli_q_function/get_current_officer", { credentials: "include" })
      .then(async (res) => {
        const d = await res.json();
        if (res.status === 403 && d.error === "not_provisioned") {
          const debugLine = `\n\n[debug] zuid used: ${JSON.stringify(d.debug_zuid_used)} | user_id field: ${JSON.stringify(d.debug_user_id_field)} | zuid field: ${JSON.stringify(d.debug_zuid_field)} | user keys: ${JSON.stringify(d.debug_user_keys)}`;
          setAccessDenied((d.message || "This login isn't linked to an officer profile yet. Contact your administrator.") + debugLine);
        } else if (res.ok) {
          setOfficer(d.officer);
          setAccessDenied(null);
        } else {
          setAccessDenied(d.detail || d.error || `Unexpected error (${res.status})`);
        }
      })
      .catch((err) => setAccessDenied(err.message));
  };

  useEffect(() => {
    fetchOfficer();
    callCatalystFunction("get_dashboard_stats", MOCK_STATS).then(setStats);

    fetchJSON("get_hotspots?limit=6")
      .then((d) => setHotspots(d.hotspots || []))
      .catch((err) => setHotspotsError(err.message))
      .finally(() => setHotspotsLoading(false));

    fetchJSON("get_incident_log?limit=8")
      .then((d) => setIncidentLog(d.entries || []))
      .catch((err) => setLogError(err.message))
      .finally(() => setLogLoading(false));

    fetchJSON("get_crime_trend?days=7")
      .then((d) => setTrend(d.trend || []))
      .catch((err) => setTrendError(err.message))
      .finally(() => setTrendLoading(false));

    fetchJSON("get_trend_alerts?window_weeks=4&limit=8")
      .then((d) => setAlerts(d.alerts || []))
      .catch((err) => setAlertsError(err.message))
      .finally(() => setAlertsLoading(false));
  }, []);

  const handleLogout = () => {
    if (catalyst && catalyst.auth) {
      catalyst.auth.signOut(`${window.location.origin}/app/index.html`);
    }
  };

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const sendMessage = () => {
    if (!input.trim() || assistantThinking) return;
    const q = input.trim();
    const historyForApi = messages
      .filter((m) => m.from === "user" || m.from === "ai")
      .slice(-6)
      .map((m) => ({ role: m.from === "user" ? "user" : "model", text: m.text }));

    setMessages((m) => [...m, { from: "user", text: q }]);
    setInput("");
    setAssistantThinking(true);

    fetch("/server/ksp_intelli_q_function/ai_assistant", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: q, history: historyForApi }),
    })
      .then(async (res) => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
        setMessages((m) => [...m, { from: "ai", text: data.reply || "No response from the assistant." }]);
        if (data.action?.tab) setActiveTab(data.action.tab);
      })
      .catch((err) => {
        setMessages((m) => [...m, { from: "ai", text: `Couldn't reach the assistant: ${err.message}` }]);
      })
      .finally(() => setAssistantThinking(false));
  };

  if (accessDenied) {
    return (
      <div className={`access-denied-wrap theme-${theme}`}>
        <style>{`
          @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=Inter:wght@400;500;600;700&display=swap');

          .access-denied-wrap {
            min-height: 100vh; display: flex; flex-direction: column; align-items: center;
            justify-content: center; font-family: 'Inter', sans-serif; position: relative; padding: 24px;
          }
          .access-denied-wrap.theme-dark {
            --ink: #0e1116; --panel: #171b23; --gold: #d4b073; --gold-strong: #e8c98d;
            --text: #f3f1ea; --muted: #a8adba; --border: rgba(255,255,255,0.1);
            background: var(--ink); color: var(--text);
            background-image: radial-gradient(circle at 85% 0%, rgba(212,176,115,0.07), transparent 50%);
          }
          .access-denied-wrap.theme-light {
            --ink: #f3efe6; --panel: #ffffff; --gold: #93692e; --gold-strong: #7a5624;
            --text: #201d17; --muted: #5c5749; --border: rgba(32,29,23,0.12);
            background: var(--ink); color: var(--text);
            background-image: radial-gradient(circle at 85% 0%, rgba(147,105,46,0.05), transparent 50%);
          }
          .access-denied-brand { display: flex; flex-direction: column; align-items: center; gap: 10px; margin-bottom: 28px; }
          .access-denied-title { font-size: 22px; font-weight: 700; font-family: 'Playfair Display', serif; }
          .access-denied-sub { color: var(--muted); font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; }
          .access-denied-card {
            background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
            padding: 32px 28px; width: 360px; max-width: 90vw; box-sizing: border-box;
            box-shadow: 0 12px 40px rgba(0,0,0,0.2); display: flex; flex-direction: column;
            align-items: center; text-align: center; gap: 14px;
          }
          .access-denied-heading { font-size: 15px; font-weight: 700; color: var(--text); }
          .access-denied-message { font-size: 12.5px; color: var(--muted); line-height: 1.6; white-space: pre-line; word-break: break-word; }
          .access-denied-signout {
            margin-top: 6px; padding: 10px 22px; border-radius: 8px; border: 1px solid var(--border);
            background: var(--gold); color: var(--ink); font-weight: 600; font-size: 12.5px;
            cursor: pointer; transition: background 0.15s;
          }
          .access-denied-signout:hover { background: var(--gold-strong); }
        `}</style>

        <div className="access-denied-brand">
          <Shield size={32} color="var(--gold-strong)" />
          <div className="access-denied-title">KSP IntelliQ</div>
          <div className="access-denied-sub">Crime Intelligence &amp; Decision Support</div>
        </div>

        <div className="access-denied-card">
          <div className="access-denied-heading">Access not set up yet</div>
          <div className="access-denied-message">{accessDenied}</div>
          <button className="access-denied-signout" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={`ksp-dash theme-${theme}`}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

        .ksp-dash {
          font-family: 'Inter', sans-serif;
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          -webkit-font-smoothing: antialiased;
          transition: background 0.2s, color 0.2s;
        }
        .theme-dark {
          --ink: #0e1116;
          --panel: #171b23;
          --panel-raised: #212630;
          --gold: #d4b073;
          --gold-strong: #e8c98d;
          --wine: #c17a7a;
          --sage: #7fb39c;
          --text: #f3f1ea;
          --muted: #a8adba;
          --border: rgba(255,255,255,0.1);
          background: var(--ink);
          background-image: radial-gradient(circle at 85% 0%, rgba(212,176,115,0.07), transparent 50%);
          color: var(--text);
        }
        .theme-light {
          --ink: #f3efe6;
          --panel: #ffffff;
          --panel-raised: #f4f0e6;
          --gold: #93692e;
          --gold-strong: #7a5624;
          --wine: #973f3f;
          --sage: #2f6d52;
          --text: #201d17;
          --muted: #5c5749;
          --border: rgba(32,29,23,0.12);
          background: var(--ink);
          background-image: radial-gradient(circle at 85% 0%, rgba(147,105,46,0.05), transparent 50%);
          color: var(--text);
        }

        .mono { font-family: 'JetBrains Mono', monospace; }
        .display { font-family: 'Playfair Display', serif; }

        /* Header */
        .top-bar { display: flex; align-items: center; justify-content: space-between;
          padding: 18px 28px; background: linear-gradient(180deg, var(--panel), var(--ink));
          border-bottom: 1px solid var(--border); }
        .brand { display: flex; align-items: center; gap: 13px; }
        .seal { width: 36px; height: 36px; flex-shrink: 0; }
        .brand-title { font-size: 19px; font-weight: 700; letter-spacing: 0.01em; color: var(--text); }
        .brand-sub { color: var(--muted); font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; margin-top: 2px; font-weight: 500; }
        .top-right { display: flex; align-items: center; gap: 16px; }
        .clock { font-size: 12px; color: var(--gold-strong); font-weight: 500; }
        .icon-btn { width: 34px; height: 34px; border-radius: 8px; background: var(--panel-raised);
          border: 1px solid var(--border); display: flex; align-items: center; justify-content: center;
          color: var(--text); cursor: pointer; }
        .id-badge { display: flex; align-items: center; gap: 10px; padding: 6px 14px 6px 6px;
          background: var(--panel-raised); border: 1px solid var(--border); border-radius: 8px; }
        .id-photo { width: 26px; height: 26px; border-radius: 6px; background: var(--gold);
          display: flex; align-items: center; justify-content: center; color: var(--ink); }
        .id-name { font-size: 12px; font-weight: 600; color: var(--text); }
        .id-role { font-size: 9.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 500; }
        .id-access { display: inline-block; margin-top: 3px; font-size: 8.5px; color: var(--gold-strong);
          text-transform: uppercase; letter-spacing: 0.05em; font-weight: 700; padding: 1px 7px;
          border: 1px solid rgba(212,176,115,0.4); border-radius: 20px; }
        .id-badge-no { font-size: 9.5px; color: var(--gold-strong); font-weight: 500; }

        /* Folder tabs */
        .tab-row { display: flex; gap: 2px; padding: 0 28px; background: var(--ink); }
        .tab { display: flex; align-items: center; gap: 7px; padding: 12px 16px; font-size: 12.5px;
          color: var(--muted); cursor: pointer; border-bottom: 2px solid transparent; transition: color 0.15s; font-weight: 500; }
        .tab.active { color: var(--gold-strong); border-bottom-color: var(--gold-strong); }
        .tab:hover:not(.active) { color: var(--text); }

        .main { flex: 1; padding: 28px; }
        .section-title { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.12em;
          color: var(--muted); margin-bottom: 14px; font-weight: 700; }

        .case-card { position: relative; background: var(--panel); border: 1px solid var(--border);
          border-radius: 12px; padding: 20px; box-shadow: 0 1px 2px rgba(0,0,0,0.15), 0 8px 24px rgba(0,0,0,0.12); }
        .stamp { position: absolute; top: 16px; right: 18px;
          background: rgba(212,176,115,0.12); border: 1px solid var(--gold); color: var(--gold-strong);
          font-size: 9px; letter-spacing: 0.1em; padding: 3px 10px; text-transform: uppercase; font-weight: 700;
          border-radius: 20px; }

        .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-bottom: 20px; }
        .stat-card { padding: 18px 20px; }
        .stat-top { margin-bottom: 12px; }
        .stat-icon { width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center;
          justify-content: center; background: rgba(212,176,115,0.14); color: var(--gold-strong); }
        .tone-wine .stat-icon { background: rgba(193,122,122,0.14); color: var(--wine); }
        .tone-sage .stat-icon { background: rgba(127,179,156,0.14); color: var(--sage); }
        .stat-value { font-size: 29px; font-weight: 700; line-height: 1; margin-bottom: 6px; color: var(--text); }
        .stat-label { color: var(--muted); font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }

        .grid-2 { display: grid; grid-template-columns: 1.3fr 1fr; gap: 16px; margin-bottom: 20px; }
        .grid-1 { display: grid; grid-template-columns: 1fr; gap: 16px; margin-bottom: 20px; }

        .tone-alert { border-color: rgba(193,122,122,0.4); }
        .alert-row { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
        .alert-row:last-child { border-bottom: none; }
        .alert-pulse { position: relative; width: 9px; height: 9px; border-radius: 50%; background: var(--wine);
          flex-shrink: 0; box-shadow: 0 0 0 0 rgba(193,122,122,0.6); animation: alert-pulse-anim 1.8s infinite; }
        .alert-pulse.critical { background: #e34d4d; box-shadow: 0 0 0 0 rgba(227,77,77,0.7); animation-duration: 1.1s; }
        @keyframes alert-pulse-anim {
          0% { box-shadow: 0 0 0 0 rgba(193,122,122,0.55); }
          70% { box-shadow: 0 0 0 9px rgba(193,122,122,0); }
          100% { box-shadow: 0 0 0 0 rgba(193,122,122,0); }
        }
        .alert-badge { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
          padding: 4px 10px; border-radius: 20px; background: rgba(212,176,115,0.14); color: var(--gold-strong); flex-shrink: 0; }
        .alert-badge.critical { background: rgba(227,77,77,0.16); color: #e88a8a; }

        .forecast-body { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
        .hotspot-row { display: flex; align-items: center; justify-content: space-between; padding: 10px 0;
          border-bottom: 1px solid var(--border); font-size: 13px; color: var(--text); font-weight: 500; }
        .hotspot-row:last-child { border-bottom: none; }
        .risk-track { width: 90px; height: 5px; background: var(--panel-raised); border-radius: 4px; overflow: hidden; }
        .risk-fill { height: 100%; background: linear-gradient(90deg, var(--gold), var(--wine)); border-radius: 4px; }

        .log-list { display: flex; flex-direction: column; margin-top: 6px; }
        .log-row { display: flex; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--border); font-size: 12px; }
        .log-row:last-child { border-bottom: none; }
        .log-time { color: var(--gold-strong); font-weight: 600; flex-shrink: 0; }
        .log-text { color: var(--text); line-height: 1.4; }

        /* Assistant — side pull tab */
        .assist-tab { position: fixed; top: 50%; right: 0; transform: translateY(-50%) rotate(180deg);
          writing-mode: vertical-rl; background: var(--gold); color: var(--ink); font-weight: 700;
          font-size: 11.5px; letter-spacing: 0.08em; padding: 18px 9px; cursor: pointer;
          border-radius: 10px 0 0 10px; display: flex; align-items: center; gap: 6px; z-index: 50;
          text-transform: uppercase; box-shadow: -4px 0 16px rgba(0,0,0,0.25); }
        .assist-panel { position: fixed; top: 0; right: 0; height: 100%; width: 340px; background: var(--panel);
          border-left: 1px solid var(--border); display: flex; flex-direction: column; z-index: 60;
          box-shadow: -12px 0 32px rgba(0,0,0,0.3); }
        .assist-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 18px;
          border-bottom: 1px solid var(--border); }
        .assist-title { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--gold-strong); font-weight: 700; }
        .assist-close { color: var(--muted); cursor: pointer; display: flex; align-items: center; gap: 4px; font-size: 11px; font-weight: 500; }
        .assist-messages { flex: 1; overflow-y: auto; padding: 16px 18px; display: flex; flex-direction: column; gap: 10px;
          overscroll-behavior: contain; }
        .msg { font-size: 12.5px; max-width: 88%; padding: 10px 12px; border-radius: 10px; line-height: 1.45; color: var(--text); }
        .msg.ai { background: var(--panel-raised); align-self: flex-start; }
        .msg.user { background: rgba(212,176,115,0.16); align-self: flex-end; }
        .msg-thinking { color: var(--muted); font-style: italic; animation: pulse-thinking 1.4s ease-in-out infinite; }
        @keyframes pulse-thinking { 0%, 100% { opacity: 0.5; } 50% { opacity: 1; } }
        .assist-input:disabled { opacity: 0.6; cursor: not-allowed; }
        .assist-send:disabled { opacity: 0.5; cursor: not-allowed; }
        .assist-input-row { display: flex; gap: 8px; padding: 14px; border-top: 1px solid var(--border); }
        .assist-input { flex: 1; background: var(--panel-raised); border: 1px solid var(--border); border-radius: 8px;
          padding: 10px 12px; font-size: 12.5px; color: var(--text); outline: none; }
        .assist-send { background: var(--gold); border: none; border-radius: 8px; width: 38px; display: flex;
          align-items: center; justify-content: center; color: var(--ink); cursor: pointer; }
      `}</style>

      {/* TOP BAR */}
      <div className="top-bar">
        <div className="brand">
          <svg className="seal" viewBox="0 0 48 48" fill="none">
            <circle cx="24" cy="24" r="21" stroke="var(--gold-strong)" strokeWidth="1.4" />
            <circle cx="24" cy="24" r="16" stroke="var(--gold-strong)" strokeWidth="0.9" opacity="0.6" />
            <path d="M24 11 L26.7 20 L36 20 L28.6 25.7 L31.3 34.7 L24 29 L16.7 34.7 L19.4 25.7 L12 20 L21.3 20 Z"
              fill="none" stroke="var(--gold-strong)" strokeWidth="1.2" />
          </svg>
          <div>
            <div className="brand-title display">KSP IntelliQ</div>
            <div className="brand-sub">Crime Intelligence &amp; Decision Support</div>
          </div>
        </div>
        <div className="top-right">
          <div className="clock mono">{clock.toLocaleTimeString()} · {clock.toLocaleDateString()}</div>
          <div className="icon-btn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title="Toggle theme">
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </div>
          <div className="icon-btn"><Bell size={15} /></div>
          <div className="icon-btn" onClick={handleLogout} title="Sign out"><LogOut size={15} /></div>
          {officer && (
            <div className="id-badge">
              <div className="id-photo"><Shield size={13} /></div>
              <div>
                <div className="id-name">{officer.name}</div>
                <div className="id-role">
                  {officer.role} · {officer.station}
                </div>
                {officer.accessRole && officer.accessRole !== "Unassigned" && officer.accessRole !== officer.role && (
                  <div className="id-access" title="RBAC data-access tier — not a rank or job title">
                    Access tier: {officer.accessRole}
                  </div>
                )}
                <div className="id-badge-no mono">{officer.badge}</div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* FOLDER TABS — filtered by the signed-in officer's RBAC accessRole.
          Before the officer profile loads, show the full-privilege set
          briefly rather than flashing a restricted set then expanding it. */}
      <div className="tab-row">
        {(officer ? tabsForRole(officer.accessRole) : ALL_TABS).map(({ icon: Icon, label }) => (
          <div key={label} className={`tab ${activeTab === label ? "active" : ""}`} onClick={() => setActiveTab(label)}>
            <Icon size={13} /> {label}
          </div>
        ))}
      </div>

      {/* MAIN */}
      <div className="main">
        {activeTab === "Crime Map" ? (
          <CrimeMap theme={theme} />
        ) : activeTab === "Network" ? (
          <NetworkGraph />
        ) : activeTab === "Cases" ? (
          <FIRManagement />
        ) : activeTab === "Officers" ? (
          <Officers />
        ) : activeTab === "Reports" ? (
          <Reports officer={officer} />
        ) : activeTab === "Audit Log" ? (
          <AuditLog />
        ) : activeTab === "Bias Audit" ? (
          <BiasAudit />
        ) : activeTab !== "Dashboard" ? (
          <div style={{ padding: 40, textAlign: "center", color: "var(--muted)" }}>
            {activeTab} module — build this next.
          </div>
        ) : (
        <>
        <div className="section-title">Live Overview</div>
        <div className="kpi-row">
          <StatCard label="Total Crimes" value={stats?.totalCrimes ?? "—"} tone="gold" icon={Shield} />
          <StatCard label="Open FIRs" value={stats?.openFirs ?? "—"} tone="wine" icon={FileText} />
          <StatCard label="Solved Cases" value={stats?.solved ?? "—"} tone="sage" icon={Search} />
          <StatCard label="Active Investigations" value={stats?.activeInvestigations ?? "—"} tone="wine" icon={Users} />
        </div>

        <div className="grid-2">
          <CaseCard stamp="Predictive">
            <div className="section-title">Hotspot Forecast — District Grid</div>
            <div className="forecast-body">
              {hotspotsLoading ? (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>Loading…</div>
              ) : hotspotsError ? (
                <div style={{ color: "#c17a7a", fontSize: 12, padding: "8px 0" }}>
                  Couldn't load hotspot forecast: {hotspotsError}
                </div>
              ) : hotspots.length === 0 ? (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>
                  No case activity in your scope in the last 30 days.
                </div>
              ) : (
                hotspots.map((h) => (
                  <div className="hotspot-row" key={h.area}>
                    <div>
                      <div>{h.area}</div>
                      <div style={{ color: "var(--muted)", fontSize: 11, fontWeight: 400 }}>{h.type}</div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <div className="risk-track"><div className="risk-fill" style={{ width: `${h.risk}%` }} /></div>
                      <span className="mono" style={{ fontSize: 11, color: "var(--gold-strong)", fontWeight: 600 }}>{h.risk}%</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </CaseCard>

          <CaseCard>
            <div className="section-title"><Radio size={11} style={{ verticalAlign: -1, marginRight: 5 }} />Incident Log</div>
            <div className="log-list">
              {logLoading ? (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>Loading…</div>
              ) : logError ? (
                <div style={{ color: "#c17a7a", fontSize: 12, padding: "8px 0" }}>
                  Couldn't load incident log: {logError}
                </div>
              ) : incidentLog.length === 0 ? (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>
                  No recent case activity in your scope.
                </div>
              ) : (
                incidentLog.map((l, i) => (
                  <div className="log-row" key={l.caseNo || i}>
                    <span className="log-time mono">{l.time}</span>
                    <span className="log-text">{l.text}</span>
                  </div>
                ))
              )}
            </div>
          </CaseCard>
        </div>

        <div className="grid-2">
          <CaseCard>
            <div className="section-title">7-Day Crime Trend</div>
            {trendLoading ? (
              <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>Loading…</div>
            ) : trendError ? (
              <div style={{ color: "#c17a7a", fontSize: 12, padding: "8px 0" }}>
                Couldn't load crime trend: {trendError}
              </div>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={trend}>
                  <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                  <XAxis dataKey="day" stroke="var(--muted)" fontSize={11} tickLine={false} axisLine={false} />
                  <YAxis stroke="var(--muted)" fontSize={11} tickLine={false} axisLine={false} />
                  <Tooltip contentStyle={{ background: "var(--panel-raised)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, color: "var(--text)" }} />
                  <Line type="monotone" dataKey="crimes" stroke="var(--gold-strong)" strokeWidth={2.5} dot={{ r: 3, fill: "var(--wine)" }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </CaseCard>

          <CaseCard stamp="Verified">
            <div className="section-title">Case Status Breakdown</div>
            <div className="forecast-body">
              <div className="hotspot-row">
                <div>Solved</div>
                <div className="mono" style={{ color: "var(--sage)", fontWeight: 700 }}>{stats?.solved ?? "—"}</div>
              </div>
              <div className="hotspot-row">
                <div>Open / Under Investigation</div>
                <div className="mono" style={{ color: "var(--wine)", fontWeight: 700 }}>{stats?.openFirs ?? "—"}</div>
              </div>
              <div className="hotspot-row">
                <div>Active Field Cases</div>
                <div className="mono" style={{ color: "var(--gold-strong)", fontWeight: 700 }}>{stats?.activeInvestigations ?? "—"}</div>
              </div>
            </div>
          </CaseCard>
        </div>

        <div className="grid-1">
          <CaseCard className={alerts.length ? "tone-alert" : ""}>
            <div className="section-title">
              <AlertTriangle size={13} style={{ verticalAlign: -2, marginRight: 6, color: "var(--wine)" }} />
              Emerging Trend Alerts
              <span style={{ marginLeft: 8, fontSize: 10.5, color: "var(--muted)", fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>
                this week vs. 4-week rolling average
              </span>
            </div>
            <div className="forecast-body">
              {alertsLoading ? (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>Scanning for anomalies…</div>
              ) : alertsError ? (
                <div style={{ color: "#c17a7a", fontSize: 12, padding: "8px 0" }}>
                  Couldn't load trend alerts: {alertsError}
                </div>
              ) : alerts.length === 0 ? (
                <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>
                  No category in your scope is currently spiking above its rolling baseline.
                </div>
              ) : (
                alerts.map((a, i) => (
                  <div className="alert-row" key={`${a.area}-${a.crimeType}-${i}`}>
                    <span className={`alert-pulse ${a.severity === "critical" ? "critical" : ""}`} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600 }}>{a.crimeType} — {a.area}</div>
                      <div style={{ color: "var(--muted)", fontSize: 11 }}>
                        {a.currentCount} this week vs. avg {a.baselineAvg} · z = {a.zScore}
                      </div>
                    </div>
                    <span className={`alert-badge ${a.severity === "critical" ? "critical" : ""}`}>
                      {a.severity === "critical" ? "Critical" : "Elevated"}
                    </span>
                  </div>
                ))
              )}
            </div>
          </CaseCard>
        </div>
        </>
        )}
      </div>

      {/* ASSISTANT — SIDE PULL TAB */}
      {!assistantOpen && (
        <div className="assist-tab" onClick={() => setAssistantOpen(true)}>
          <MessageSquare size={13} /> Assistant
        </div>
      )}
      {assistantOpen && (
        <div className="assist-panel">
          <div className="assist-header">
            <div className="assist-title"><MessageSquare size={14} /> IntelliQ Assistant</div>
            <div className="assist-close" onClick={() => setAssistantOpen(false)}>
              <ChevronLeft size={14} /> Close
            </div>
          </div>
          <div className="assist-messages" ref={scrollRef}>
            {messages.map((m, i) => <div key={i} className={`msg ${m.from}`}>{m.text}</div>)}
            {assistantThinking && <div className="msg ai msg-thinking">IntelliQ is thinking…</div>}
          </div>
          <div className="assist-input-row">
            <input
              className="assist-input"
              placeholder="Ask about a FIR, area, or suspect…"
              value={input}
              disabled={assistantThinking}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendMessage()}
            />
            <button className="assist-send" onClick={sendMessage} disabled={assistantThinking}><Send size={14} /></button>
          </div>
        </div>
      )}
    </div>
  );
}