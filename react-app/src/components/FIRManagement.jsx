import { useState, useEffect } from "react";
import { Search, Plus, X, FileText } from "lucide-react";

/* ---------------------------------------------------------------------
   Calls your deployed Catalyst Functions. Falls back to demo data.
   Flip USE_MOCK to false once search_fir / add_fir are deployed.
------------------------------------------------------------------------ */
const USE_MOCK = true;

async function searchFirs(filters) {
  if (USE_MOCK) return MOCK_RESULTS.filter((f) =>
    (!filters.crime_type || f.crime_type === filters.crime_type) &&
    (!filters.status || f.status === filters.status)
  );
  try {
    const params = new URLSearchParams(filters).toString();
    const res = await fetch(`/server/ksp_intelli_q_function/search_fir?${params}`, { credentials: "include" });
    const data = await res.json();
    return data.results || [];
  } catch (err) {
    console.warn("search_fir failed", err);
    return [];
  }
}

async function createFir(payload) {
  if (USE_MOCK) return { message: "FIR created (mock)", fir: { fir_id: `FIR${Math.floor(Math.random() * 900) + 100}`, ...payload } };
  try {
    const res = await fetch(`/server/ksp_intelli_q_function/add_fir`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify(payload),
    });
    return await res.json();
  } catch (err) {
    console.warn("add_fir failed", err);
    return { error: "Failed to create FIR" };
  }
}

const MOCK_RESULTS = [
  { fir_id: "FIR001", crime_type: "Theft", location: "Whitefield", district: "Bengaluru East", status: "Open", date_reported: "2026-07-01" },
  { fir_id: "FIR002", crime_type: "Chain Snatching", location: "Indiranagar", district: "Bengaluru East", status: "Under Investigation", date_reported: "2026-07-03" },
  { fir_id: "FIR003", crime_type: "Burglary", location: "Electronic City", district: "Bengaluru South", status: "Closed", date_reported: "2026-06-28" },
  { fir_id: "FIR004", crime_type: "Assault", location: "Yeshwanthpur", district: "Bengaluru North", status: "Open", date_reported: "2026-07-05" },
];

const STATUS_COLOR = { Open: "var(--wine)", "Under Investigation": "var(--gold-strong)", Closed: "var(--sage)" };
const CRIME_TYPES = ["Theft", "Chain Snatching", "Burglary", "Assault", "Vehicle Theft", "Robbery"];

function EmptyForm() {
  return { crime_type: "", location: "", district: "", status: "Open", investigating_officer: "", description: "" };
}

export default function FIRManagement() {
  const [results, setResults] = useState([]);
  const [filters, setFilters] = useState({ crime_type: "", status: "" });
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(EmptyForm());
  const [submitting, setSubmitting] = useState(false);

  const runSearch = () => {
    setLoading(true);
    searchFirs(filters).then((r) => { setResults(r); setLoading(false); });
  };

  useEffect(() => { runSearch(); /* eslint-disable-next-line */ }, []);

  const submitFir = async () => {
    if (!form.crime_type || !form.location || !form.district) return;
    setSubmitting(true);
    const res = await createFir(form);
    setSubmitting(false);
    if (!res.error) {
      setShowForm(false);
      setForm(EmptyForm());
      runSearch();
    }
  };

  return (
    <div className="fir-mgmt">
      <style>{`
        .fir-mgmt {
          --ink: #0e1116; --panel: #171b23; --panel-raised: #212630;
          --gold: #d4b073; --gold-strong: #e8c98d; --wine: #c17a7a; --sage: #7fb39c;
          --text: #f3f1ea; --muted: #a8adba; --border: rgba(255,255,255,0.1);
          font-family: 'Inter', sans-serif; color: var(--text);
        }
        .fir-card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
          padding: 20px; box-shadow: 0 1px 2px rgba(0,0,0,0.15), 0 8px 24px rgba(0,0,0,0.12); }
        .fir-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
        .fir-filters { display: flex; gap: 8px; flex-wrap: wrap; }
        .fir-select { background: var(--panel-raised); border: 1px solid var(--border); border-radius: 8px;
          padding: 8px 12px; font-size: 12.5px; color: var(--text); outline: none; }
        .fir-btn { display: flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 8px;
          border: none; font-size: 12.5px; font-weight: 600; cursor: pointer; }
        .fir-btn.primary { background: var(--gold); color: var(--ink); }
        .fir-btn.ghost { background: var(--panel-raised); color: var(--text); border: 1px solid var(--border); }
        .fir-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
        .fir-table th { text-align: left; padding: 10px 12px; color: var(--muted); font-size: 10.5px;
          text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 1px solid var(--border); }
        .fir-table td { padding: 12px; border-bottom: 1px solid var(--border); }
        .fir-table tr:last-child td { border-bottom: none; }
        .status-pill { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 10.5px;
          font-weight: 600; background: rgba(255,255,255,0.06); }
        .fir-empty { text-align: center; padding: 40px; color: var(--muted); font-size: 13px; }

        .modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.55); display: flex;
          align-items: center; justify-content: center; z-index: 100; }
        .modal { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
          padding: 22px; width: 400px; max-width: 90vw; }
        .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
        .modal-title { font-weight: 700; font-size: 15px; }
        .field-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em;
          margin-bottom: 5px; margin-top: 12px; font-weight: 600; }
        .field-input, .field-textarea { width: 100%; background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 8px; padding: 9px 11px; font-size: 12.5px; color: var(--text); outline: none;
          font-family: inherit; box-sizing: border-box; }
        .field-textarea { resize: vertical; min-height: 60px; }
        .modal-actions { display: flex; gap: 8px; margin-top: 18px; }
      `}</style>

      <div className="fir-card">
        <div className="fir-toolbar">
          <div style={{ fontWeight: 700, fontSize: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <FileText size={15} /> FIR Management
          </div>
          <div className="fir-filters">
            <select className="fir-select" value={filters.crime_type}
              onChange={(e) => setFilters({ ...filters, crime_type: e.target.value })}>
              <option value="">All crime types</option>
              {CRIME_TYPES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className="fir-select" value={filters.status}
              onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
              <option value="">All statuses</option>
              <option>Open</option>
              <option>Under Investigation</option>
              <option>Closed</option>
            </select>
            <button className="fir-btn ghost" onClick={runSearch}><Search size={13} /> Search</button>
            <button className="fir-btn primary" onClick={() => setShowForm(true)}><Plus size={13} /> New FIR</button>
          </div>
        </div>

        {loading ? (
          <div className="fir-empty">Loading case records…</div>
        ) : results.length === 0 ? (
          <div className="fir-empty">No FIRs match these filters.</div>
        ) : (
          <table className="fir-table">
            <thead>
              <tr>
                <th>FIR ID</th><th>Crime Type</th><th>Location</th><th>District</th><th>Date</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {results.map((f) => (
                <tr key={f.fir_id}>
                  <td className="mono" style={{ fontFamily: "monospace" }}>{f.fir_id}</td>
                  <td>{f.crime_type}</td>
                  <td>{f.location}</td>
                  <td>{f.district}</td>
                  <td>{f.date_reported}</td>
                  <td><span className="status-pill" style={{ color: STATUS_COLOR[f.status] }}>{f.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showForm && (
        <div className="modal-overlay" onClick={() => setShowForm(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">New FIR</div>
              <X size={16} style={{ cursor: "pointer", color: "var(--muted)" }} onClick={() => setShowForm(false)} />
            </div>

            <div className="field-label">Crime Type *</div>
            <select className="field-input" value={form.crime_type}
              onChange={(e) => setForm({ ...form, crime_type: e.target.value })}>
              <option value="">Select…</option>
              {CRIME_TYPES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>

            <div className="field-label">Location *</div>
            <input className="field-input" value={form.location}
              onChange={(e) => setForm({ ...form, location: e.target.value })} placeholder="e.g. Whitefield" />

            <div className="field-label">District *</div>
            <input className="field-input" value={form.district}
              onChange={(e) => setForm({ ...form, district: e.target.value })} placeholder="e.g. Bengaluru East" />

            <div className="field-label">Investigating Officer</div>
            <input className="field-input" value={form.investigating_officer}
              onChange={(e) => setForm({ ...form, investigating_officer: e.target.value })} placeholder="Officer name" />

            <div className="field-label">Description</div>
            <textarea className="field-textarea" value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Brief incident description" />

            <div className="modal-actions">
              <button className="fir-btn ghost" style={{ flex: 1, justifyContent: "center" }} onClick={() => setShowForm(false)}>Cancel</button>
              <button className="fir-btn primary" style={{ flex: 1, justifyContent: "center" }} onClick={submitFir} disabled={submitting}>
                {submitting ? "Saving…" : "Create FIR"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}