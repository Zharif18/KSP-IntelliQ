import { useState, useEffect } from "react";
import { Search, TrendingUp } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid
} from "recharts";

/* ---------------------------------------------------------------------
   Calls the real backend route:
     GET /server/ksp_intelli_q_function/get_reports
   One combined payload — total_cases, solve_rate, and three ranked
   breakdowns (by_crime_type, by_district, by_status) — all aggregated
   server-side from live CaseMaster rows in main.py's _reports().
------------------------------------------------------------------------ */

async function fetchJSON(endpoint) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, { credentials: "include" });
  if (!res.ok) throw new Error(`${endpoint} failed (${res.status})`);
  return res.json();
}

function StatCard({ label, value, tone }) {
  return (
    <div className="rpt-stat">
      <div className="rpt-stat-label">{label}</div>
      <div className={`rpt-stat-value ${tone || ""}`}>{value}</div>
    </div>
  );
}

function RankedList({ title, rows }) {
  const max = rows.length ? Math.max(...rows.map((r) => r.count)) : 1;
  return (
    <div className="rpt-list">
      <div className="rpt-list-title">{title}</div>
      {rows.length === 0 ? (
        <div className="rpt-empty" style={{ padding: 16 }}>No data.</div>
      ) : (
        rows.map((r) => (
          <div key={r.label} className="rpt-row">
            <div className="rpt-row-label">{r.label}</div>
            <div className="rpt-row-track">
              <div className="rpt-row-fill" style={{ width: `${(r.count / max) * 100}%` }} />
            </div>
            <div className="rpt-row-count">{r.count}</div>
          </div>
        ))
      )}
    </div>
  );
}

export default function Reports() {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = () => {
    setLoading(true);
    setError(null);
    fetchJSON("get_reports")
      .then(setReport)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const chartData = (report?.by_crime_type || []).slice(0, 8).map((r) => ({ name: r.label, count: r.count }));

  return (
    <div className="rpt-mgmt">
      <style>{`
        .rpt-mgmt {
          --ink: #0e1116; --panel: #171b23; --panel-raised: #212630;
          --gold: #d4b073; --gold-strong: #e8c98d; --wine: #c17a7a; --sage: #7fb39c;
          --text: #f3f1ea; --muted: #a8adba; --border: rgba(255,255,255,0.1);
          font-family: 'Inter', sans-serif; color: var(--text);
          display: flex; flex-direction: column; gap: 16px;
        }
        .rpt-card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
          padding: 20px; box-shadow: 0 1px 2px rgba(0,0,0,0.15), 0 8px 24px rgba(0,0,0,0.12); }
        .rpt-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
        .rpt-btn { display: flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 8px;
          font-size: 12.5px; font-weight: 600; cursor: pointer; background: var(--panel-raised);
          color: var(--text); border: 1px solid var(--border); }
        .rpt-stats-row { display: flex; gap: 14px; flex-wrap: wrap; }
        .rpt-stat { background: var(--panel-raised); border: 1px solid var(--border); border-radius: 10px;
          padding: 14px 18px; min-width: 140px; flex: 1; }
        .rpt-stat-label { font-size: 10.5px; color: var(--muted); text-transform: uppercase;
          letter-spacing: 0.06em; margin-bottom: 6px; }
        .rpt-stat-value { font-size: 22px; font-weight: 700; }
        .rpt-stat-value.gold { color: var(--gold-strong); }
        .rpt-stat-value.sage { color: var(--sage); }
        .rpt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .rpt-list-title { font-weight: 700; font-size: 13px; margin-bottom: 12px; }
        .rpt-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; font-size: 12px; }
        .rpt-row-label { width: 120px; flex-shrink: 0; color: var(--muted);
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .rpt-row-track { flex: 1; height: 8px; background: var(--panel-raised); border-radius: 4px; overflow: hidden; }
        .rpt-row-fill { height: 100%; background: var(--gold); border-radius: 4px; }
        .rpt-row-count { width: 30px; text-align: right; font-weight: 600; }
        .rpt-empty { text-align: center; padding: 40px; color: var(--muted); font-size: 13px; }
        .rpt-error { text-align: center; padding: 16px; color: var(--wine); font-size: 12.5px; }
        @media (max-width: 900px) { .rpt-grid { grid-template-columns: 1fr; } }
      `}</style>

      <div className="rpt-card">
        <div className="rpt-toolbar">
          <div style={{ fontWeight: 700, fontSize: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <TrendingUp size={15} /> Reports
          </div>
          <button className="rpt-btn" onClick={load}><Search size={13} /> Refresh</button>
        </div>

        {error && <div className="rpt-error">{error}</div>}

        {loading || !report ? (
          <div className="rpt-empty">Crunching case records…</div>
        ) : (
          <div className="rpt-stats-row">
            <StatCard label="Total Cases" value={report.total_cases} tone="gold" />
            <StatCard label="Solve Rate" value={`${report.solve_rate}%`} tone="sage" />
            <StatCard label="Crime Categories" value={report.by_crime_type.length} />
            <StatCard label="Districts Reporting" value={report.by_district.length} />
          </div>
        )}
      </div>

      {report && !loading && (
        <>
          <div className="rpt-card">
            <div className="rpt-list-title">Cases by Crime Type</div>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={chartData} layout="vertical" margin={{ left: 10, right: 20 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" stroke="var(--muted)" fontSize={11} tickLine={false} axisLine={false} />
                <YAxis type="category" dataKey="name" stroke="var(--muted)" fontSize={11}
                  tickLine={false} axisLine={false} width={140} />
                <Tooltip contentStyle={{ background: "var(--panel-raised)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, color: "var(--text)" }} />
                <Bar dataKey="count" fill="var(--gold)" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="rpt-grid">
            <div className="rpt-card">
              <RankedList title="Cases by District" rows={report.by_district} />
            </div>
            <div className="rpt-card">
              <RankedList title="Cases by Status" rows={report.by_status} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}