import { useState, useEffect } from "react";
import { Scale, RefreshCw, AlertTriangle, Info } from "lucide-react";

/* ---------------------------------------------------------------------
   Calls the real backend route:
     GET /server/ksp_intelli_q_function/get_bias_audit

   Bias/Fairness Auditing Dashboard — checks the app's own repeat-
   offender flag (the closest thing here to a predictive-policing risk
   signal, used by Network Graph / hotspots / investigation briefs) for
   geographic and demographic disparity. Access is gated server-side
   (Superintendent of Police sees their own district, every other rank
   gets a 403) — this component just renders whatever the backend is
   willing to return, same pattern as AuditLog.jsx.
------------------------------------------------------------------------ */

async function fetchJSON(endpoint) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, { credentials: "include" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.message || `${endpoint} failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}

function DisparityTable({ title, rows, keyLabel }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="bias-block">
      <div className="bias-block-title">{title}</div>
      <table className="bias-table">
        <thead>
          <tr>
            <th>{keyLabel}</th><th>Total</th><th>Flagged</th>
            <th>Share of Total</th><th>Share of Flags</th><th>Disparity Ratio</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.district || r.group} className={r.elevated ? "elevated" : ""}>
              <td>{r.district || r.group}</td>
              <td>{r.total}</td>
              <td>{r.flagged}</td>
              <td>{r.totalSharePct}%</td>
              <td>{r.flaggedSharePct}%</td>
              <td>
                {r.disparityRatio === null ? "—" : r.disparityRatio}
                {r.elevated && <AlertTriangle size={11} style={{ marginLeft: 5, verticalAlign: -1, color: "var(--wine)" }} />}
                {r.sampleTooSmall && <span className="bias-small-sample"> small sample</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function BiasAudit() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [forbidden, setForbidden] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    setForbidden(false);
    fetchJSON("get_bias_audit")
      .then(setData)
      .catch((err) => {
        if (err.status === 403) setForbidden(true);
        else setError(err.message);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="bias-mgmt">
      <style>{`
        .bias-mgmt { font-family: 'Inter', sans-serif; color: var(--text); }
        .bias-card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
          padding: 20px; box-shadow: 0 1px 2px rgba(0,0,0,0.15), 0 8px 24px rgba(0,0,0,0.12); }
        .bias-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
        .bias-btn { display: flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 8px;
          background: var(--panel-raised); color: var(--text); border: 1px solid var(--border); font-size: 12.5px;
          font-weight: 600; cursor: pointer; }
        .bias-empty { text-align: center; padding: 40px; color: var(--muted); font-size: 13px; }
        .bias-error { text-align: center; padding: 16px; color: var(--wine); font-size: 12.5px; }
        .bias-note { display: flex; gap: 8px; background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 8px; padding: 12px 14px; font-size: 11.5px; color: var(--muted); line-height: 1.6; margin-bottom: 18px; }
        .bias-kpi-row { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
        .bias-kpi { flex: 1; min-width: 140px; background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 10px; padding: 12px 14px; }
        .bias-kpi-label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 5px; }
        .bias-kpi-value { font-size: 17px; font-weight: 700; color: var(--gold-strong); }
        .bias-block { margin-bottom: 22px; }
        .bias-block-title { font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
          color: var(--muted); margin-bottom: 10px; }
        .bias-table { width: 100%; border-collapse: collapse; font-size: 12px; }
        .bias-table th { text-align: left; padding: 9px 10px; color: var(--muted); font-size: 10.5px;
          text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); }
        .bias-table td { padding: 10px; border-bottom: 1px solid var(--border); }
        .bias-table tr:last-child td { border-bottom: none; }
        .bias-table tr.elevated td { background: rgba(193,122,122,0.08); }
        .bias-small-sample { font-size: 10px; color: var(--muted); font-style: italic; }
        .bias-disclaimer { display: flex; gap: 8px; font-size: 11px; color: var(--muted); line-height: 1.6;
          border-top: 1px dashed var(--border); padding-top: 14px; margin-top: 4px; }
      `}</style>

      <div className="bias-card">
        <div className="bias-toolbar">
          <div style={{ fontWeight: 700, fontSize: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <Scale size={15} /> Bias &amp; Fairness Audit
            <span style={{ fontWeight: 400, fontSize: 11, color: "var(--muted)" }}>
              Checks the repeat-offender flag for geographic &amp; demographic disparity
            </span>
          </div>
          <button className="bias-btn" onClick={load}><RefreshCw size={13} /> Refresh</button>
        </div>

        {forbidden && (
          <div className="bias-error">
            Bias audit access requires SP level or above. If you believe this is
            wrong, contact your administrator to review your assigned role.
          </div>
        )}
        {error && <div className="bias-error">{error}</div>}

        {!forbidden && !error && (loading ? (
          <div className="bias-empty">Running fairness audit…</div>
        ) : !data ? (
          <div className="bias-empty">No data available.</div>
        ) : (
          <>
            <div className="bias-note">
              <Info size={13} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>{data.methodologyNote}</span>
            </div>

            <div className="bias-kpi-row">
              <div className="bias-kpi">
                <div className="bias-kpi-label">Cases In Scope</div>
                <div className="bias-kpi-value">{data.totals.cases}</div>
              </div>
              <div className="bias-kpi">
                <div className="bias-kpi-label">Flagged Cases</div>
                <div className="bias-kpi-value">{data.totals.flaggedCases}</div>
              </div>
              <div className="bias-kpi">
                <div className="bias-kpi-label">Persons In Scope</div>
                <div className="bias-kpi-value">{data.totals.persons}</div>
              </div>
              <div className="bias-kpi">
                <div className="bias-kpi-label">Flagged Persons</div>
                <div className="bias-kpi-value">{data.totals.flaggedPersons}</div>
              </div>
            </div>

            <DisparityTable title="Geographic Disparity (District)" rows={data.geographic} keyLabel="District" />
            <DisparityTable title="Gender Representation" rows={data.genderRepresentation} keyLabel="Group" />
            <DisparityTable title="Age-Band Representation (Juvenile / Adult)" rows={data.ageBandRepresentation} keyLabel="Group" />

            <div className="bias-disclaimer">
              <AlertTriangle size={13} style={{ flexShrink: 0, marginTop: 1, color: "var(--wine)" }} />
              <span>{data.disclaimer}</span>
            </div>
          </>
        ))}
      </div>
    </div>
  );
}