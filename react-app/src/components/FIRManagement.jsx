import { useState, useEffect } from "react";
import { Search, Plus, X, FileText, Lock, Eye, Sparkles, Link2, MapPin, User, Crosshair } from "lucide-react";

/* ---------------------------------------------------------------------
   Calls the real backend routes:
     GET  /server/ksp_intelli_q_function/get_lookups
     GET  /server/ksp_intelli_q_function/get_current_officer
     GET  /server/ksp_intelli_q_function/search_case?district_id=&crime_subhead_id=&status_id=
     POST /server/ksp_intelli_q_function/add_case
   All dropdowns are populated from get_lookups instead of hardcoded
   strings, and the create form submits real IDs, matching what
   main.py's add_case() actually expects.
------------------------------------------------------------------------ */

async function fetchJSON(endpoint, opts) {
  const res = await fetch(`/server/ksp_intelli_q_function/${endpoint}`, {
    credentials: "include",
    ...opts,
  });
  if (!res.ok) throw new Error(`${endpoint} failed (${res.status})`);
  return res.json();
}

function EmptyForm() {
  return {
    crime_subhead_id: "",
    police_station_id: "",
    incident_date: "",
    latitude: "",
    longitude: "",
    brief_facts: "",
  };
}

export default function FIRManagement() {
  const [lookups, setLookups] = useState(null);
  const [officer, setOfficer] = useState(null);
  const [results, setResults] = useState([]);
  const [filters, setFilters] = useState({ district_id: "", crime_subhead_id: "", status_id: "" });
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(EmptyForm());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const [detailCaseId, setDetailCaseId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(null);
  const [detailForbidden, setDetailForbidden] = useState(false);

  // FIR Text Mining (entity extraction on the Brief Facts narrative)
  const [extraction, setExtraction] = useState(null);
  const [extracting, setExtracting] = useState(false);
  const [extractionError, setExtractionError] = useState(null);

  // Duplicate/Linked FIR Detection
  const [linkedMatches, setLinkedMatches] = useState(null);
  const [linkedLoading, setLinkedLoading] = useState(false);
  const [linkedError, setLinkedError] = useState(null);

  const openCaseDetail = (caseMasterId) => {
    setDetailCaseId(caseMasterId);
    setDetail(null);
    setDetailError(null);
    setDetailForbidden(false);
    setDetailLoading(true);
    setLinkedMatches(null);
    setLinkedError(null);
    fetch(`/server/ksp_intelli_q_function/get_case_detail?case_master_id=${encodeURIComponent(caseMasterId)}`, {
      credentials: "include",
    })
      .then(async (res) => {
        const body = await res.json().catch(() => ({}));
        if (res.status === 403) {
          setDetailForbidden(true);
          return;
        }
        if (!res.ok) throw new Error(body.error || `Failed (${res.status})`);
        setDetail(body);
      })
      .catch((err) => setDetailError(err.message))
      .finally(() => setDetailLoading(false));
  };

  const closeCaseDetail = () => {
    setDetailCaseId(null);
    setDetail(null);
    setDetailError(null);
    setDetailForbidden(false);
    setLinkedMatches(null);
    setLinkedError(null);
  };

  const checkLinkedFirs = () => {
    if (!detailCaseId) return;
    setLinkedMatches(null);
    setLinkedError(null);
    setLinkedLoading(true);
    fetchJSON(`get_linked_fir_matches?case_master_id=${encodeURIComponent(detailCaseId)}`)
      .then((data) => setLinkedMatches(data.matches || []))
      .catch((err) => setLinkedError(err.message))
      .finally(() => setLinkedLoading(false));
  };

  // --- FIR Text Mining: send the Brief Facts narrative to Zia NER +
  // keyword extraction and surface entities / a suggested crime type
  // and district for the officer to confirm, never auto-submitted.
  const runExtraction = async () => {
    if (!form.brief_facts.trim()) return;
    setExtracting(true);
    setExtractionError(null);
    try {
      const data = await fetchJSON("extract_fir_entities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: form.brief_facts }),
      });
      if (data.error) throw new Error(data.error);
      setExtraction(data);
    } catch (err) {
      setExtractionError(err.message);
    } finally {
      setExtracting(false);
    }
  };

  const applySuggestedCrimeType = () => {
    if (extraction?.suggestedCrimeSubheadId) {
      setForm((f) => ({ ...f, crime_subhead_id: extraction.suggestedCrimeSubheadId }));
    }
  };

  useEffect(() => {
    Promise.all([fetchJSON("get_lookups"), fetchJSON("get_current_officer")])
      .then(([lookupData, officerData]) => {
        setLookups(lookupData);
        setOfficer(officerData.officer);
      })
      .catch((err) => setError(err.message));
  }, []);

  const runSearch = () => {
    setLoading(true);
    setError(null);
    const params = new URLSearchParams(
      Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
    ).toString();
    fetchJSON(`search_case${params ? `?${params}` : ""}`)
      .then((data) => setResults(data.results || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (lookups) runSearch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookups]);

  const subheadName = (id) =>
    lookups?.crime_subheads.find((s) => s.CrimeSubHeadID === id)?.CrimeHeadName || "—";
  const unitName = (id) => lookups?.units.find((u) => u.UnitID === id)?.UnitName || "—";
  const districtName = (id) => lookups?.districts.find((d) => d.DistrictID === id)?.DistrictName || "—";
  const statusName = (id) => lookups?.statuses.find((s) => s.CaseStatusID === id)?.CaseStatusName || "—";
  const districtOfUnit = (unitId) => lookups?.units.find((u) => u.UnitID === unitId)?.DistrictID;

  const closeForm = () => {
    setShowForm(false);
    setExtraction(null);
    setExtractionError(null);
  };

  const submitFir = async () => {
    if (!form.crime_subhead_id || !form.police_station_id || !officer) return;
    setSubmitting(true);
    try {
      const res = await fetchJSON("add_case", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...form,
          police_person_id: officer.employee_id,
          latitude: form.latitude ? Number(form.latitude) : 0,
          longitude: form.longitude ? Number(form.longitude) : 0,
        }),
      });
      if (res.error) throw new Error(res.error);
      closeForm();
      setForm(EmptyForm());
      runSearch();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fir-mgmt">
      <style>{`
        .fir-mgmt {
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
        .fir-btn:disabled { opacity: 0.5; cursor: default; }
        .fir-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
        .fir-table th { text-align: left; padding: 10px 12px; color: var(--muted); font-size: 10.5px;
          text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 1px solid var(--border); }
        .fir-table td { padding: 12px; border-bottom: 1px solid var(--border); }
        .fir-table tr:last-child td { border-bottom: none; }
        .fir-row { cursor: pointer; }
        .fir-row:hover td { background: var(--panel-raised); }
        .redacted-name { display: inline-flex; align-items: center; gap: 5px; color: var(--muted);
          font-family: monospace; font-size: 11.5px; }
        .detail-section { margin-top: 16px; }
        .detail-section-title { font-size: 11px; color: var(--muted); text-transform: uppercase;
          letter-spacing: 0.06em; font-weight: 600; margin-bottom: 8px; }
        .detail-person-row { display: flex; align-items: center; justify-content: space-between;
          padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 12.5px; }
        .detail-person-row:last-child { border-bottom: none; }
        .sensitive-banner { display: flex; align-items: center; gap: 8px; background: rgba(193,122,122,0.12);
          border: 1px solid rgba(193,122,122,0.3); color: var(--wine); border-radius: 8px;
          padding: 8px 12px; font-size: 11.5px; margin-top: 12px; }
        .status-pill { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 10.5px;
          font-weight: 600; background: rgba(255,255,255,0.06); }
        .fir-empty { text-align: center; padding: 40px; color: var(--muted); font-size: 13px; }
        .fir-error { text-align: center; padding: 16px; color: var(--wine); font-size: 12.5px; }

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

        .fir-extract-row { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
        .fir-inline-note { font-size: 10.5px; color: var(--muted); margin-top: 4px; }
        .fir-inline-note.error { color: var(--wine); }
        .fir-extraction-panel { margin-top: 10px; background: var(--panel-raised); border: 1px solid var(--border);
          border-radius: 8px; padding: 12px; }
        .fir-extract-suggestion { display: flex; align-items: center; justify-content: space-between; gap: 10px;
          font-size: 11.5px; margin-bottom: 10px; flex-wrap: wrap; }
        .fir-apply-btn { background: var(--gold); color: var(--ink); border-radius: 6px; padding: 4px 9px;
          font-size: 10.5px; font-weight: 700; cursor: pointer; white-space: nowrap; }
        .fir-apply-btn:hover { background: var(--gold-strong); }
        .fir-extract-weapon { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 8px;
          padding: 5px 9px; background: var(--panel); border: 1px solid var(--gold); border-radius: 6px;
          font-size: 11.5px; color: var(--text); }
        .fir-extract-weapon-sep { color: var(--muted); margin: 0 2px; }
        .fir-extract-group { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin-bottom: 6px; color: var(--muted); }
        .fir-extract-group:last-child { margin-bottom: 0; }
        .fir-extract-chip { background: var(--panel); border: 1px solid var(--border); border-radius: 6px;
          padding: 3px 8px; font-size: 11px; color: var(--text); }
        .linked-fir-row { background: var(--panel-raised); border: 1px solid var(--border); border-radius: 8px;
          padding: 10px 12px; margin-top: 8px; }
        .linked-fir-score { font-size: 10.5px; font-weight: 700; color: var(--gold-strong); background: var(--panel);
          border: 1px solid var(--border); border-radius: 6px; padding: 2px 7px; }
      `}</style>

      <div className="fir-card">
        <div className="fir-toolbar">
          <div style={{ fontWeight: 700, fontSize: 14, display: "flex", alignItems: "center", gap: 8 }}>
            <FileText size={15} /> FIR Management
          </div>
          <div className="fir-filters">
            <select className="fir-select" value={filters.district_id}
              onChange={(e) => setFilters({ ...filters, district_id: e.target.value })}>
              <option value="">All districts</option>
              {lookups?.districts.map((d) => (
                <option key={d.DistrictID} value={d.DistrictID}>{d.DistrictName}</option>
              ))}
            </select>
            <select className="fir-select" value={filters.crime_subhead_id}
              onChange={(e) => setFilters({ ...filters, crime_subhead_id: e.target.value })}>
              <option value="">All crime types</option>
              {lookups?.crime_subheads.map((s) => (
                <option key={s.CrimeSubHeadID} value={s.CrimeSubHeadID}>{s.CrimeHeadName}</option>
              ))}
            </select>
            <select className="fir-select" value={filters.status_id}
              onChange={(e) => setFilters({ ...filters, status_id: e.target.value })}>
              <option value="">All statuses</option>
              {lookups?.statuses.map((s) => (
                <option key={s.CaseStatusID} value={s.CaseStatusID}>{s.CaseStatusName}</option>
              ))}
            </select>
            <button className="fir-btn ghost" onClick={runSearch}><Search size={13} /> Search</button>
            <button className="fir-btn primary" onClick={() => { setExtraction(null); setExtractionError(null); setShowForm(true); }} disabled={!officer}>
              <Plus size={13} /> New FIR
            </button>
          </div>
        </div>

        {error && <div className="fir-error">{error}</div>}

        {loading || !lookups ? (
          <div className="fir-empty">Loading case records…</div>
        ) : results.length === 0 ? (
          <div className="fir-empty">No cases match these filters.</div>
        ) : (
          <table className="fir-table">
            <thead>
              <tr>
                <th>Case ID</th><th>Crime Type</th><th>Station</th><th>District</th><th>Date</th><th>Status</th><th></th>
              </tr>
            </thead>
            <tbody>
              {results.map((c) => (
                <tr key={c.CaseMasterID} className="fir-row" onClick={() => openCaseDetail(c.CaseMasterID)}>
                  <td className="mono" style={{ fontFamily: "monospace" }}>{c.CaseMasterID}</td>
                  <td>{subheadName(c.CrimeMinorHeadID)}</td>
                  <td>{unitName(c.PoliceStationID)}</td>
                  <td>{districtName(districtOfUnit(c.PoliceStationID))}</td>
                  <td>{c.CrimeRegisteredDate}</td>
                  <td><span className="status-pill">{statusName(c.CaseStatusID)}</span></td>
                  <td><Eye size={13} color="var(--muted)" /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showForm && (
        <div className="modal-overlay" onClick={closeForm}>
          <div className="modal" style={{ width: 460 }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">New FIR</div>
              <X size={16} style={{ cursor: "pointer", color: "var(--muted)" }} onClick={closeForm} />
            </div>

            <div className="field-label">Crime Type *</div>
            <select className="field-input" value={form.crime_subhead_id}
              onChange={(e) => setForm({ ...form, crime_subhead_id: e.target.value })}>
              <option value="">Select…</option>
              {lookups?.crime_subheads.map((s) => (
                <option key={s.CrimeSubHeadID} value={s.CrimeSubHeadID}>{s.CrimeHeadName}</option>
              ))}
            </select>

            <div className="field-label">Police Station *</div>
            <select className="field-input" value={form.police_station_id}
              onChange={(e) => setForm({ ...form, police_station_id: e.target.value })}>
              <option value="">Select…</option>
              {lookups?.units.map((u) => (
                <option key={u.UnitID} value={u.UnitID}>{u.UnitName}</option>
              ))}
            </select>

            <div className="field-label">Incident Date</div>
            <input type="date" className="field-input" value={form.incident_date}
              onChange={(e) => setForm({ ...form, incident_date: e.target.value })} />

            <div className="field-label">Latitude</div>
            <input className="field-input" value={form.latitude}
              onChange={(e) => setForm({ ...form, latitude: e.target.value })} placeholder="e.g. 12.9698" />

            <div className="field-label">Longitude</div>
            <input className="field-input" value={form.longitude}
              onChange={(e) => setForm({ ...form, longitude: e.target.value })} placeholder="e.g. 77.7500" />

            <div className="field-label" style={{ marginTop: 12 }}>Brief Facts</div>
            <textarea className="field-textarea" value={form.brief_facts}
              onChange={(e) => setForm({ ...form, brief_facts: e.target.value })} placeholder="Brief incident description" />

            <div className="fir-extract-row">
              <div className="fir-btn ghost" style={{ fontSize: 11.5 }} onClick={runExtraction}>
                <Sparkles size={12} /> {extracting ? "Extracting…" : "Auto-extract entities"}
              </div>
              {extraction && !extraction.ziaAvailable && (
                <span className="fir-inline-note">Text mining ran without Zia — fewer entities than usual.</span>
              )}
            </div>
            {extractionError && <div className="fir-inline-note error">{extractionError}</div>}

            {extraction && (
              <div className="fir-extraction-panel">
                {(extraction.suggestedCrimeSubheadName || extraction.suggestedDistrictName) && (
                  <div className="fir-extract-suggestion">
                    <span>
                      {extraction.suggestedCrimeSubheadName && <>Suggested crime type: <strong>{extraction.suggestedCrimeSubheadName}</strong></>}
                      {extraction.suggestedCrimeSubheadName && extraction.suggestedDistrictName && " · "}
                      {extraction.suggestedDistrictName && <>District: <strong>{extraction.suggestedDistrictName}</strong></>}
                    </span>
                    {extraction.suggestedCrimeSubheadId && (
                      <div className="fir-apply-btn" onClick={applySuggestedCrimeType}>Apply crime type</div>
                    )}
                  </div>
                )}
                {extraction.weapons && extraction.weapons.length > 0 && (
                  <div className="fir-extract-weapon">
                    <Crosshair size={12} />
                    {extraction.weapons.map((w, i) => (
                      <span key={w}>
                        <strong>Weapon{extraction.weapons.length > 1 ? ` ${i + 1}` : ""}:</strong> {w}
                        {i < extraction.weapons.length - 1 && <span className="fir-extract-weapon-sep">·</span>}
                      </span>
                    ))}
                  </div>
                )}
                {extraction.persons.length > 0 && (
                  <div className="fir-extract-group">
                    <User size={11} /> {extraction.persons.map((p) => (
                      <span key={p} className="fir-extract-chip">{p}</span>
                    ))}
                  </div>
                )}
                {extraction.locations.length > 0 && (
                  <div className="fir-extract-group">
                    <MapPin size={11} /> {extraction.locations.map((l) => (
                      <span key={l} className="fir-extract-chip">{l}</span>
                    ))}
                  </div>
                )}
                {(extraction.keyphrases.length > 0 || extraction.keywords.length > 0) && (
                  <div className="fir-extract-group">
                    <Sparkles size={11} /> {(extraction.keyphrases.length ? extraction.keyphrases : extraction.keywords).map((k) => (
                      <span key={k} className="fir-extract-chip">{k}</span>
                    ))}
                  </div>
                )}
                {extraction.persons.length === 0 && extraction.locations.length === 0 &&
                  extraction.keywords.length === 0 && extraction.keyphrases.length === 0 &&
                  (!extraction.weapons || extraction.weapons.length === 0) && (
                    <div className="fir-inline-note">No entities detected in this text yet.</div>
                )}
              </div>
            )}

            <div className="modal-actions">
              <button className="fir-btn ghost" style={{ flex: 1, justifyContent: "center" }} onClick={closeForm}>Cancel</button>
              <button className="fir-btn primary" style={{ flex: 1, justifyContent: "center" }} onClick={submitFir} disabled={submitting}>
                {submitting ? "Saving…" : "Create FIR"}
              </button>
            </div>
          </div>
        </div>
      )}

      {detailCaseId && (
        <div className="modal-overlay" onClick={closeCaseDetail}>
          <div className="modal" style={{ width: 460 }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">Case Detail — {detailCaseId}</div>
              <X size={16} style={{ cursor: "pointer", color: "var(--muted)" }} onClick={closeCaseDetail} />
            </div>

            {detailLoading && <div className="fir-empty" style={{ padding: 20 }}>Loading case detail…</div>}

            {detailForbidden && (
              <div className="fir-error">
                That case is outside your access scope. This view is logged, and denied
                attempts are recorded in the audit trail.
              </div>
            )}

            {detailError && <div className="fir-error">{detailError}</div>}

            {detail && (
              <>
                <div style={{ fontSize: 12.5, color: "var(--muted)" }}>
                  {detail.case.crimeType} · {detail.case.station} · {detail.case.district}
                </div>
                <div style={{ fontSize: 12.5, marginTop: 6 }}>{detail.case.briefFacts}</div>

                {detail.case.sensitiveCase && (
                  <div className="sensitive-banner">
                    <Lock size={13} /> Sensitive-crime case — victim &amp; complainant identity is
                    restricted to the investigating officer/station under victim-privacy rules.
                  </div>
                )}

                <div className="detail-section">
                  <div className="detail-section-title">Accused ({detail.accused.length})</div>
                  {detail.accused.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12 }}>None on file.</div>}
                  {detail.accused.map((a) => (
                    <div className="detail-person-row" key={a.id}>
                      <span>
                        {a.redacted ? (
                          <span className="redacted-name"><Lock size={11} /> {a.name}</span>
                        ) : a.name}
                      </span>
                      <span style={{ color: "var(--muted)" }}>{a.age ? `${a.age} yrs` : ""}</span>
                    </div>
                  ))}
                </div>

                <div className="detail-section">
                  <div className="detail-section-title">Victim(s) ({detail.victims.length})</div>
                  {detail.victims.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12 }}>None on file.</div>}
                  {detail.victims.map((v) => (
                    <div className="detail-person-row" key={v.id}>
                      <span>
                        {v.redacted ? (
                          <span className="redacted-name"><Lock size={11} /> {v.name}</span>
                        ) : v.name}
                      </span>
                      <span style={{ color: "var(--muted)" }}>{v.age ? `${v.age} yrs` : ""}</span>
                    </div>
                  ))}
                </div>

                <div className="detail-section">
                  <div className="detail-section-title">Complainant(s) ({detail.complainants.length})</div>
                  {detail.complainants.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12 }}>None on file.</div>}
                  {detail.complainants.map((c) => (
                    <div className="detail-person-row" key={c.id}>
                      <span>
                        {c.redacted ? (
                          <span className="redacted-name"><Lock size={11} /> {c.name}</span>
                        ) : c.name}
                      </span>
                      <span style={{ color: "var(--muted)" }}>{c.occupation || ""}</span>
                    </div>
                  ))}
                </div>

                <div className="detail-section">
                  <div className="detail-section-title" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <span><Link2 size={11} style={{ verticalAlign: -1, marginRight: 4 }} />Possibly Linked FIRs</span>
                    {!linkedMatches && !linkedLoading && (
                      <span className="fir-apply-btn" onClick={checkLinkedFirs}>Check for matches</span>
                    )}
                  </div>
                  {linkedLoading && <div style={{ color: "var(--muted)", fontSize: 12 }}>Scanning cases within your access scope…</div>}
                  {linkedError && <div className="fir-inline-note error">{linkedError}</div>}
                  {linkedMatches && linkedMatches.length === 0 && (
                    <div style={{ color: "var(--muted)", fontSize: 12 }}>No likely matches found within your access scope.</div>
                  )}
                  {linkedMatches && linkedMatches.map((m) => (
                    <div key={m.caseMasterId} className="linked-fir-row">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span className="mono" style={{ fontSize: 12 }}>{m.caseNo || m.caseMasterId}</span>
                        <span className="linked-fir-score">{Math.round(m.score * 100)}% match</span>
                      </div>
                      <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 2 }}>
                        {m.crimeType} · {m.station} · {m.district} · {m.date}
                      </div>
                      {m.reasons.length > 0 && (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 6 }}>
                          {m.reasons.map((r) => (
                            <span key={r} className="fir-extract-chip">{r}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}