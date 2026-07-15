import { useState, useEffect } from "react";
import { UserCheck } from "lucide-react";

async function fetchJSON(endpoint, opts) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, {
    credentials: "include",
    ...opts,
  });
  if (!res.ok) throw new Error(`${endpoint} failed (${res.status})`);
  return res.json();
}

export default function OnboardingLink({ onLinked }) {
  const [employees, setEmployees] = useState([]);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(true);
  const [linking, setLinking] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchJSON("get_unlinked_employees")
      .then((d) => setEmployees(d.employees || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const confirmLink = async () => {
    if (!selected) return;
    setLinking(true);
    setError(null);
    try {
      await fetchJSON("link_officer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ employee_id: selected }),
      });
      onLinked();
    } catch (err) {
      setError(err.message);
    } finally {
      setLinking(false);
    }
  };

  return (
    <div className="onboard-wrap">
      <style>{`
        .onboard-wrap {
          --ink: #0e1116; --panel: #171b23; --panel-raised: #212630;
          --gold: #d4b073; --gold-strong: #e8c98d; --text: #f3f1ea; --muted: #a8adba;
          --border: rgba(255,255,255,0.1);
          min-height: 100vh; display: flex; align-items: center; justify-content: center;
          background: var(--ink); color: var(--text); font-family: 'Inter', sans-serif;
        }
        .onboard-card { background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
          padding: 28px; width: 420px; max-width: 90vw; }
        .onboard-title { display: flex; align-items: center; gap: 8px; font-size: 16px; font-weight: 700; margin-bottom: 6px; }
        .onboard-sub { color: var(--muted); font-size: 12.5px; margin-bottom: 20px; line-height: 1.5; }
        .onboard-select { width: 100%; background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 8px; padding: 10px 12px; font-size: 13px; color: var(--text); outline: none; margin-bottom: 14px; }
        .onboard-btn { width: 100%; padding: 10px; border-radius: 8px; border: none; background: var(--gold);
          color: var(--ink); font-weight: 700; font-size: 13px; cursor: pointer; }
        .onboard-btn:disabled { opacity: 0.5; cursor: default; }
        .onboard-error { color: #c17a7a; font-size: 12px; margin-top: 10px; }
      `}</style>
      <div className="onboard-card">
        <div className="onboard-title"><UserCheck size={16} color="var(--gold-strong)" /> Link Your Officer Profile</div>
        <div className="onboard-sub">
          First time signing in. Select your name below to connect this login to your
          officer record — this only happens once.
        </div>
        {loading ? (
          <div style={{ color: "var(--muted)", fontSize: 12.5 }}>Loading officer list…</div>
        ) : (
          <>
            <select className="onboard-select" value={selected} onChange={(e) => setSelected(e.target.value)}>
              <option value="">Select your name…</option>
              {employees.map((e) => (
                <option key={e.EmployeeID} value={e.EmployeeID}>
                  {e.FirstName} — {e.KGID}
                </option>
              ))}
            </select>
            <button className="onboard-btn" onClick={confirmLink} disabled={!selected || linking}>
              {linking ? "Linking…" : "Confirm & Continue"}
            </button>
          </>
        )}
        {error && <div className="onboard-error">{error}</div>}
      </div>
    </div>
  );
}