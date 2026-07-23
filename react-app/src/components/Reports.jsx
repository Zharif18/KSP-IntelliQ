import { useState, useEffect } from "react";
import { Search, TrendingUp, Download } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid
} from "recharts";

/* ---------------------------------------------------------------------
   Calls the real backend routes:
     GET /server/ksp_intelli_q_function/get_reports
     GET /server/ksp_intelli_q_function/get_ncrb_report?from=&to=&format=csv
   get_reports returns one combined payload — total_cases, solve_rate,
   and three ranked breakdowns — aggregated server-side in _reports().
   get_ncrb_report is the NCRB crime-return export, gated server-side
   by officer.canExportNCRB (Inspector rank and above only); this
   component only shows the button when that flag is true, but the
   403 the API would return either way is the real boundary.
------------------------------------------------------------------------ */

async function fetchJSON(endpoint) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, { credentials: "include" });
  if (!res.ok) throw new Error(`${endpoint} failed (${res.status})`);
  return res.json();
}

async function downloadNCRBReport(from, to, setExportError, setExporting) {
  setExporting(true);
  setExportError(null);
  try {
    const params = new URLSearchParams({ format: "csv" });
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    const res = await fetch(`/server/ksp_intelli_q_function/get_ncrb_report?${params}`, {
      credentials: "include",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.message || `NCRB export failed (${res.status})`);
    }
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ncrb_report_${from || "all"}_${to || "all"}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (err) {
    setExportError(err.message);
  } finally {
    setExporting(false);
  }
}

const NCRB_STANDARD_TABLES = [
  { key: "crime_head_district", label: "Table A — Crime Head x District" },
  { key: "women", label: "Table B — Crime Against Women" },
  { key: "children", label: "Table C — Crime Against Children" },
  { key: "persons_apprehended", label: "Table D — Persons Apprehended" },
];

async function downloadBlob(endpoint, params, filename, setExportError, setExporting) {
  setExporting(true);
  setExportError(null);
  try {
    const qs = new URLSearchParams(params);
    const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}?${qs}`, { credentials: "include" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.message || `Export failed (${res.status})`);
    }
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (err) {
    setExportError(err.message);
  } finally {
    setExporting(false);
  }
}

function downloadStandardTable(table, from, to, setExportError, setExporting) {
  return downloadBlob(
    "export_ncrb_return",
    { table, format: "csv", ...(from ? { from } : {}), ...(to ? { to } : {}) },
    `ncrb_${table}_${from || "all"}_${to || "all"}.csv`,
    setExportError, setExporting
  );
}

function downloadStandardBundle(from, to, setExportError, setExporting) {
  return downloadBlob(
    "export_ncrb_return_bundle",
    { ...(from ? { from } : {}), ...(to ? { to } : {}) },
    `ncrb_return_bundle_${from || "all"}_${to || "all"}.zip`,
    setExportError, setExporting
  );
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

export default function Reports({ officer }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(null);
  const [standardTable, setStandardTable] = useState(NCRB_STANDARD_TABLES[0].key);

  const canExportNCRB = !!officer?.canExportNCRB;
  const isSCRBAnalyst = !!officer?.isSCRBAnalyst;

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
        .ncrb-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
          margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border); }
        .ncrb-label { font-size: 11.5px; color: var(--muted); font-weight: 600; margin-right: 4px; }
        .ncrb-date { background: var(--panel-raised); border: 1px solid var(--border); border-radius: 8px;
          padding: 7px 10px; font-size: 12px; color: var(--text); outline: none; }
        .ncrb-btn { display: flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 8px;
          font-size: 12.5px; font-weight: 600; cursor: pointer; background: var(--gold); color: #1a1a1a;
          border: none; }
        .ncrb-btn:disabled { opacity: 0.6; cursor: not-allowed; }
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

        {canExportNCRB && (
          <div className="ncrb-row">
            <span className="ncrb-label">NCRB Export</span>
            <input type="date" className="ncrb-date" value={fromDate}
              onChange={(e) => setFromDate(e.target.value)} />
            <span style={{ color: "var(--muted)", fontSize: 12 }}>to</span>
            <input type="date" className="ncrb-date" value={toDate}
              onChange={(e) => setToDate(e.target.value)} />
            <button
              className="ncrb-btn"
              disabled={exporting}
              onClick={() => downloadNCRBReport(fromDate, toDate, setExportError, setExporting)}
            >
              <Download size={13} /> {exporting ? "Exporting…" : "Export CSV"}
            </button>
            {exportError && <span className="rpt-error" style={{ padding: 0 }}>{exportError}</span>}
          </div>
        )}
      </div>

      {canExportNCRB && (
        <div className="rpt-card">
          <div className="rpt-toolbar" style={{ marginBottom: 4 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>
              NCRB Standard Return {isSCRBAnalyst && (
                <span style={{ fontWeight: 500, fontSize: 11.5, color: "var(--muted)", marginLeft: 6 }}>
                  · Statewide, consolidated — SCRB
                </span>
              )}
            </div>
          </div>
          <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5, marginBottom: 4 }}>
            Auto-generated in the standard NCRB crime-return shapes (district x crime-head
            incidence, Crime Against Women, Crime Against Children, Persons Apprehended) —
            aggregate counts only, so nothing here needs re-keying into a separate return.
            Victim/juvenile figures never include a name, consistent with Sec 72 BNS and
            JJ Act 2015 Sec 74.
          </div>
          <div className="ncrb-row" style={{ borderTop: "none", marginTop: 4, paddingTop: 4 }}>
            <select
              className="ncrb-date"
              value={standardTable}
              onChange={(e) => setStandardTable(e.target.value)}
            >
              {NCRB_STANDARD_TABLES.map((t) => (
                <option key={t.key} value={t.key}>{t.label}</option>
              ))}
            </select>
            <button
              className="rpt-btn"
              disabled={exporting}
              onClick={() => downloadStandardTable(standardTable, fromDate, toDate, setExportError, setExporting)}
            >
              <Download size={13} /> {exporting ? "Exporting…" : "Download table CSV"}
            </button>
            <button
              className="ncrb-btn"
              disabled={exporting}
              onClick={() => downloadStandardBundle(fromDate, toDate, setExportError, setExporting)}
              title="All four standard tables, zipped, for the selected period"
            >
              <Download size={13} /> {exporting ? "Exporting…" : "Download full bundle (.zip)"}
            </button>
          </div>
          {exportError && <span className="rpt-error" style={{ padding: 0 }}>{exportError}</span>}
        </div>
      )}

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