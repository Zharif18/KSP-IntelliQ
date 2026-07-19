import { useState, useEffect } from "react";
import { Users, Search } from "lucide-react";

/* ---------------------------------------------------------------------
   Calls the real backend routes:
     GET /server/ksp_intelli_q_function/get_lookups
     GET /server/ksp_intelli_q_function/get_officers?district_id=
   Active-case counts come straight from get_officers (aggregated
   server-side from live CaseMaster rows), not hardcoded.
------------------------------------------------------------------------ */

async function fetchJSON(endpoint, opts) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, {
    credentials: "include",
    ...opts,
  });
  if (!res.ok) throw new Error(`${endpoint} failed (${res.status})`);
  return res.json();
}

export default function Officers() {
  const [lookups, setLookups] = useState(null);
  const [officers, setOfficers] = useState([]);
  const [districtId, setDistrictId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchJSON("get_lookups")
      .then(setLookups)
      .catch((err) => setError(err.message));
  }, []);

  const runSearch = () => {
    setLoading(true);
    setError(null);
    const params = districtId ? `?district_id=${districtId}` : "";
    fetchJSON(`get_officers${params}`)
      .then((data) => setOfficers(data.officers || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    runSearch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookups]);

  const loadTone = (n) => (n >= 8 ? "wine" : n >= 4 ? "gold" : "sage");

  return (
    <div className="off-mgmt">
      <style>{`
        .off-mgmt {
          font-family: 'Inter', sans-serif; color: var(--text);
        }
        .off-card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
          padding: 20px; box-shadow: 0 1px 2px rgba(0,0,0,0.15), 0 8px 24px rgba(0,0,0,0.12); }
        .off-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
        .off-filters { display: flex; gap: 8px; flex-wrap: wrap; }
        .off-select { background: var(--panel-raised); border: 1px solid var(--border); border-radius: 8px;
          padding: 8px 12px; font-size: 12.5px; color: var(--text); outline: none; }
        .off-btn { display: flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 8px;
          border: none; font-size: 12.5px; font-weight: 600; cursor: pointer; background: var(--panel-raised);
          color: var(--text); border: 1px solid var(--border); }
        .off-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
        .off-table th { text-align: left; padding: 10px 12px; color: var(--muted); font-size: 10.5px;
          text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 1px solid var(--border); }
        .off-table td { padding: 12px; border-bottom: 1px solid var(--border); }
        .off-table tr:last-child td { border-bottom: none; }
        .load-pill { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 10.5px;
          font-weight: 600; background: rgba(255,255,255,0.06); }
        .load-pill.wine { color: var(--wine); }
        .load-pill.gold { color: var(--gold-strong); }
        .load-pill.sage { color: var(--sage); }
        .off-empty { text-align: center; padding: 40px; color: var(--muted); font-size: 13px; }
        .off-error { text-align: center; padding: 16px; color: var(--wine); font-size: 12.5px; }
      `}</style>

      <div className="off-card">
        <div className="off-toolbar">
          <div style={{ fontWeight: 700, fontSize: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <Users size={15} /> Officers
          </div>
          <div className="off-filters">
            <select className="off-select" value={districtId}
              onChange={(e) => setDistrictId(e.target.value)}>
              <option value="">All districts</option>
              {lookups?.districts.map((d) => (
                <option key={d.DistrictID} value={d.DistrictID}>{d.DistrictName}</option>
              ))}
            </select>
            <button className="off-btn" onClick={runSearch}><Search size={13} /> Filter</button>
          </div>
        </div>

        {error && <div className="off-error">{error}</div>}

        {loading || !lookups ? (
          <div className="off-empty">Loading officer roster…</div>
        ) : officers.length === 0 ? (
          <div className="off-empty">No officers match this filter.</div>
        ) : (
          <table className="off-table">
            <thead>
              <tr>
                <th>Name</th><th>Rank</th><th>Unit</th><th>District</th><th>Badge</th><th>Active Cases</th>
              </tr>
            </thead>
            <tbody>
              {officers.map((o) => (
                <tr key={o.employee_id}>
                  <td>{o.name}</td>
                  <td>{o.rank}</td>
                  <td>{o.unit}</td>
                  <td>{o.district}</td>
                  <td className="mono" style={{ fontFamily: "monospace" }}>{o.badge}</td>
                  <td><span className={`load-pill ${loadTone(o.active_cases)}`}>{o.active_cases}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}