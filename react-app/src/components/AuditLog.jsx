import { useState, useEffect } from "react";
import { ShieldAlert, RefreshCw } from "lucide-react";

/* ---------------------------------------------------------------------
   Calls the real backend route:
     GET /server/ksp_intelli_q_function/get_audit_log?limit=

   Access itself is gated server-side (Superintendent of Police sees
   their own district's entries, every other rank gets a 403) — this
   component doesn't duplicate that logic, it just renders whatever the
   backend is willing to return, and shows the 403 message plainly if
   the signed-in officer shouldn't be here (e.g. they reached this tab
   via a stale bookmark rather than the nav, since the nav itself
   already hides this tab for every rank below Superintendent of Police).
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

const RESULT_TONE = { SUCCESS: "sage", DENIED: "wine", ERROR: "wine" };

export default function AuditLog() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [forbidden, setForbidden] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    setForbidden(false);
    fetchJSON("get_audit_log?limit=200")
      .then((data) => setEntries(data.entries || []))
      .catch((err) => {
        if (err.status === 403) setForbidden(true);
        else setError(err.message);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="off-mgmt">
      {/* Reuses the .off-* classes already defined by Officers.jsx's own
          <style> block when Officers has rendered in this session; kept
          self-contained here too so this tab looks right even when it's
          opened first. */}
      <style>{`
        .off-mgmt {
          font-family: 'Inter', sans-serif; color: var(--text);
        }
        .off-card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
          padding: 20px; box-shadow: 0 1px 2px rgba(0,0,0,0.15), 0 8px 24px rgba(0,0,0,0.12); }
        .off-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
        .off-btn { display: flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 8px;
          border: none; font-size: 12.5px; font-weight: 600; cursor: pointer; background: var(--panel-raised);
          color: var(--text); border: 1px solid var(--border); }
        .off-table { width: 100%; border-collapse: collapse; font-size: 12px; }
        .off-table th { text-align: left; padding: 10px 12px; color: var(--muted); font-size: 10.5px;
          text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 1px solid var(--border); }
        .off-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); }
        .off-table tr:last-child td { border-bottom: none; }
        .load-pill { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 10.5px; font-weight: 600; background: rgba(255,255,255,0.06); }
        .load-pill.wine { color: var(--wine); }
        .load-pill.sage { color: var(--sage); }
        .off-empty { text-align: center; padding: 40px; color: var(--muted); font-size: 13px; }
        .off-error { text-align: center; padding: 16px; color: var(--wine); font-size: 12.5px; }
      `}</style>

      <div className="off-card">
        <div className="off-toolbar">
          <div style={{ fontWeight: 700, fontSize: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <ShieldAlert size={15} /> Audit Log
            <span style={{ fontWeight: 400, fontSize: 11, color: "var(--muted)" }}>
              Every query and view against this system, logged automatically
            </span>
          </div>
          <button className="off-btn" onClick={load}><RefreshCw size={13} /> Refresh</button>
        </div>

        {forbidden && (
          <div className="off-error">
            Audit trail access requires SP level or above. If you believe this is
            wrong, contact your administrator to review your assigned role.
          </div>
        )}
        {error && <div className="off-error">{error}</div>}

        {!forbidden && (loading ? (
          <div className="off-empty">Loading audit trail…</div>
        ) : entries.length === 0 ? (
          <div className="off-empty">No audit entries yet.</div>
        ) : (
          <table className="off-table">
            <thead>
              <tr>
                <th>Time (UTC)</th><th>Officer</th><th>Role</th><th>Action</th>
                <th>Endpoint</th><th>Resource</th><th>Records</th><th>Result</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => (
                <tr key={i}>
                  <td className="mono" style={{ fontFamily: "monospace" }}>{(e.Time_stamp || "").replace("T", " ").replace("Z", "")}</td>
                  <td>{e.EmployeeName || "—"}</td>
                  <td>{e.RoleName}</td>
                  <td>{e.Action}</td>
                  <td className="mono" style={{ fontFamily: "monospace" }}>{e.Endpoint}</td>
                  <td>{e.ResourceType}{e.ResourceID ? ` · ${e.ResourceID}` : ""}</td>
                  <td>{e.RecordCount >= 0 ? e.RecordCount : "—"}</td>
                  <td><span className={`load-pill ${RESULT_TONE[e._Result] || "sage"}`}>{e._Result}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
      </div>
    </div>
  );
}