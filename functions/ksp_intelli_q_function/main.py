import logging
import os
import json
import time
import hashlib
import datetime
import requests  # type: ignore
from flask import Request, make_response, jsonify # type: ignore
import zcatalyst_sdk #type: ignore


# =======================================================================
# RBAC — role tiers, decoupled from Rank/Designation on purpose.
#
# Rank ("Constable" .. "Superintendent of Police") is who someone is in
# the police hierarchy. Designation ("Station House Officer" ..) is what
# post they currently hold. Neither models "SCRB Analyst" at all — SCRB
# staff are often not field officers. So access tier lives in its own
# Role table, and an admin assigns Employee.RoleID explicitly — same
# pattern this file already uses for Employee.zuid (see
# get_current_officer below). This is intentional: RBAC tiers should
# never silently fall out of a rank/designation string match.
#
# Fail-safe default: any employee with no RoleID set gets the MOST
# restrictive tier (own cases only, no PII beyond that, no export),
# never the most permissive. See _get_officer_context.
# =======================================================================
DEFAULT_ROLE = {
    "role_id": None,
    "role_name": "Unassigned",
    "data_scope": "OWN_CASES",
    "can_view_pii": True,
    "can_export_ncrb": False,
    "level": 0,
}

# Ordered least -> most privileged, used only for the audit-log route's
# own access check.
SCOPE_RANK = {"OWN_CASES": 1, "STATION": 2, "DISTRICT": 3, "STATE": 4}


def handler(request: Request):
    """
    Single entry point for this Advanced I/O function.
    Catalyst routes every call here; we dispatch manually by path + method.

    Every route function now takes (app, request, officer, audit) instead
    of just (app, request):
      - officer: the caller's identity + RBAC context (see
        _get_officer_context). Never None — an unauthenticated/unlinked
        caller gets a stub officer dict with data_scope="NONE" so routes
        can uniformly deny rather than crash.
      - audit: a plain dict each route may fill in (resource_type,
        resource_id, record_count) before returning. This function writes
        the AuditLog row AFTER the route runs, using whatever the route
        set plus the response status — so logging happens exactly once,
        centrally, for every route, and a route can't forget to log.
    """
    logger = logging.getLogger()
    app = None
    officer = DEFAULT_OFFICER_STUB
    audit = {"resource_type": "", "resource_id": "", "record_count": None}
    response = None
    try:
        app = zcatalyst_sdk.initialize()
        officer = _get_officer_context(app)

        routes = {
            ("/get_current_officer", "GET"): get_current_officer,
            ("/get_lookups", "GET"): get_lookups,
            ("/get_dashboard_stats", "GET"): get_dashboard_stats,
            ("/search_case", "GET"): search_case,
            ("/add_case", "POST"): add_case,
            ("/update_case_status", "PUT"): update_case_status,
            ("/get_network_graph", "GET"): get_network_graph,
            ("/get_officers", "GET"): get_officers,
            ("/get_reports", "GET"): get_reports,
            ("/ai_assistant", "POST"): ai_assistant,
            ("/get_audit_log", "GET"): get_audit_log,
        }

        route_fn = routes.get((request.path, request.method))
        if not route_fn:
            response = make_response(jsonify({"error": "Unknown route"}), 404)
            return response

        response = route_fn(app, request, officer, audit)
        return response

    except Exception as err:
        logger.error(f"Exception in ksp_intelli_q_function: {err}")
        audit["result_override"] = "ERROR"
        # Surfacing the real detail so errors are debuggable from the browser
        # console / network tab without needing to check Catalyst's Logs tab
        # every time. If this ever ships beyond a hackathon demo, drop
        # "detail" and log-only instead — it can leak internal info.
        response = make_response(jsonify({"error": "Internal server error occurred.", "detail": str(err)}), 500)
        return response

    finally:
        # Centralized audit write — runs for every route, success or not.
        # Never let a logging failure take down the actual response.
        try:
            if app is not None and response is not None:
                _write_audit_log(app, officer, request, audit, response)
        except Exception as audit_err:
            logger.error(f"Audit logging failed (non-fatal): {audit_err}")


# ---------------------------------------------------------------------
# Small helper: run a ZCQL query and return just the row dicts for the
# named table (ZCQL wraps each row as {"TableName": {...columns...}})
# ---------------------------------------------------------------------
def zcql_rows(app, table_name, query):
    rows = app.zcql().execute_query(query)
    return [r[table_name] for r in rows]


# ---------------------------------------------------------------------
# Officer / RBAC context
# ---------------------------------------------------------------------
DEFAULT_OFFICER_STUB = {
    "authenticated": False,
    "provisioned": False,
    "employee_id": None,
    "name": None,
    "zuid": None,
    "unit_id": None,
    "district_id": None,
    **DEFAULT_ROLE,
}


def _get_officer_context(app):
    """
    Resolves the caller's Catalyst session -> Employee row -> Role row,
    in one place, so every route (and the audit logger) sees the same
    identity and RBAC scope without repeating the lookup.

    Never raises — worst case returns the DEFAULT_OFFICER_STUB (deny by
    default), so a broken Role table or an unlinked login degrades to
    "no access" rather than a 500 or, worse, an accidental full-access
    fallback.
    """
    try:
        user = app.user_management().get_current_user()
    except Exception:
        return dict(DEFAULT_OFFICER_STUB)

    raw_user_id = user.get("user_id")
    raw_zuid = user.get("zuid")
    zuid = str(raw_user_id or raw_zuid or "")

    try:
        rows = zcql_rows(app, "Employee", f"SELECT * FROM Employee WHERE zuid = '{zuid}'")
    except Exception:
        rows = []

    if not rows:
        ctx = dict(DEFAULT_OFFICER_STUB)
        ctx["authenticated"] = True
        ctx["zuid"] = zuid
        return ctx

    emp = rows[0]
    ctx = dict(DEFAULT_OFFICER_STUB)
    ctx.update({
        "authenticated": True,
        "provisioned": True,
        "employee_id": emp["EmployeeID"],
        "name": emp.get("FirstName"),
        "zuid": zuid,
        "unit_id": emp.get("UnitID"),
        "district_id": None,  # filled in below from Unit
        "rank_id": emp.get("RankID"),
        "designation_id": emp.get("DesignationID"),
    })

    # Unit -> District (Employee.DistrictID exists too, but Unit is the
    # source of truth if an employee is transferred and DistrictID on
    # Employee goes stale — cheap enough to always resolve via Unit).
    try:
        unit = zcql_rows(app, "Unit", f"SELECT DistrictID FROM Unit WHERE UnitID = '{emp.get('UnitID')}'")
        if unit:
            ctx["district_id"] = unit[0]["DistrictID"]
        else:
            ctx["district_id"] = emp.get("DistrictID")
    except Exception:
        ctx["district_id"] = emp.get("DistrictID")

    # Role lookup — tolerant of the Role table or RoleID column not
    # existing yet (e.g. mid-migration), so this never hard-fails the
    # whole app. Falls back to the restrictive default instead.
    role_id = emp.get("RoleID")
    if role_id:
        try:
            role_rows = zcql_rows(app, "Role", f"SELECT * FROM Role WHERE RoleID = '{role_id}'")
            if role_rows:
                r = role_rows[0]
                ctx.update({
                    "role_id": r["RoleID"],
                    "role_name": r["RoleName"],
                    "data_scope": r["DataScope"],
                    "can_view_pii": bool(int(r.get("CanViewPII", 1))),
                    "can_export_ncrb": bool(int(r.get("CanExportNCRB", 0))),
                    "level": int(r.get("Level", 0)),
                })
        except Exception:
            pass  # keep DEFAULT_ROLE values already in ctx

    return ctx


# ---------------------------------------------------------------------
# GET /get_current_officer
# ---------------------------------------------------------------------
def get_current_officer(app, request, officer, audit):
    audit["resource_type"] = "officer_profile"
    if not officer["authenticated"]:
        return make_response(jsonify({"error": "not_authenticated"}), 401)
    if not officer["provisioned"]:
        return make_response(jsonify({
            "error": "not_provisioned",
            "message": "This login isn't linked to an officer profile yet. Contact your administrator.",
        }), 403)

    emp = zcql_rows(app, "Employee", f"SELECT * FROM Employee WHERE EmployeeID = '{officer['employee_id']}'")[0]
    rank = zcql_rows(app, "Rank", f"SELECT RankName FROM Rank WHERE RankID = '{emp['RankID']}'")
    unit = zcql_rows(app, "Unit", f"SELECT UnitName FROM Unit WHERE UnitID = '{emp['UnitID']}'")

    audit["resource_id"] = officer["employee_id"]

    officer_out = {
        "employee_id": emp["EmployeeID"],
        "name": emp["FirstName"],
        "role": rank[0]["RankName"] if rank else "Officer",   # police Rank — unchanged, for the ID badge
        "station": unit[0]["UnitName"] if unit else "Unassigned",
        "badge": emp["KGID"],
        # New RBAC fields the frontend uses for tab/feature gating:
        "accessRole": officer["role_name"],
        "dataScope": officer["data_scope"],
        "canViewPII": officer["can_view_pii"],
        "canExportNCRB": officer["can_export_ncrb"],
        "provisioned": True,
    }
    return make_response(jsonify({"officer": officer_out}), 200)


# ---------------------------------------------------------------------
# GET /get_lookups
# ---------------------------------------------------------------------
def _lookups(app):
    districts = zcql_rows(app, "District", "SELECT DistrictID, DistrictName FROM District WHERE Active = 1")
    units = zcql_rows(app, "Unit", "SELECT UnitID, UnitName, DistrictID FROM Unit WHERE Active = 1")
    crime_heads = zcql_rows(app, "CrimeHead", "SELECT CrimeHeadID, CrimeGroupName FROM CrimeHead WHERE Active = 1")
    crime_subheads = zcql_rows(app, "CrimeSubHead", "SELECT CrimeSubHeadID, CrimeHeadID, CrimeHeadName FROM CrimeSubHead")
    statuses = zcql_rows(app, "CaseStatusMaster", "SELECT CaseStatusID, CaseStatusName FROM CaseStatusMaster")
    return {
        "districts": districts,
        "units": units,
        "crime_heads": crime_heads,
        "crime_subheads": crime_subheads,
        "statuses": statuses,
    }


def get_lookups(app, request, officer, audit):
    audit["resource_type"] = "lookups"
    return make_response(jsonify(_lookups(app)), 200)


# ---------------------------------------------------------------------
# Sensitive-crime classification, used by both scoping and redaction.
# CH03 = "Crimes Against Women" in your CrimeHead table — the closest
# existing proxy for POCSO / victim-protection-sensitive matter. If you
# add a dedicated POCSO flag to CrimeSubHead later, plug it in here.
# ---------------------------------------------------------------------
SENSITIVE_CRIME_HEADS = {"CH03"}


def _is_juvenile(age_year):
    try:
        return age_year is not None and int(age_year) < 18
    except (TypeError, ValueError):
        return False


def _mask_person_label(person_key, reason):
    """
    Deterministic pseudonym: same PersonKey always masks to the same
    label within a session/export, so an analyst can still see "this is
    the same person across cases" without ever seeing who they are.
    """
    short_hash = hashlib.sha256(person_key.encode()).hexdigest()[:6].upper()
    return f"[{reason}-{short_hash}]"


# ---------------------------------------------------------------------
# Case-scoping — the RBAC heart of every case-touching route.
# Returns a SQL WHERE fragment (no leading "WHERE") or "" for
# STATE-scope roles that see everything. Never returns None: an
# officer with no recognizable scope gets an impossible condition
# ("1=0") so a broken/unassigned role fails closed, not open.
# ---------------------------------------------------------------------
def _case_scope_clause(app, officer):
    scope = officer["data_scope"]
    if scope == "OWN_CASES":
        if not officer["employee_id"]:
            return "1=0"
        return f"PolicePersonID = '{officer['employee_id']}'"
    if scope == "STATION":
        if not officer["unit_id"]:
            return "1=0"
        return f"PoliceStationID = '{officer['unit_id']}'"
    if scope == "DISTRICT":
        if not officer["district_id"]:
            return "1=0"
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{officer['district_id']}'")
        unit_ids = [u["UnitID"] for u in unit_rows]
        if not unit_ids:
            return "1=0"
        in_clause = ", ".join(f"'{u}'" for u in unit_ids)
        return f"PoliceStationID IN ({in_clause})"
    if scope == "STATE":
        return ""  # SCRB Analyst — statewide, but see _reports/_dashboard_stats
                    # for the fact that STATE scope + can_view_pii=False means
                    # aggregates only, never raw case rows with names attached.
    return "1=0"


def _require_scope_at_least(officer, min_scope):
    return SCOPE_RANK.get(officer["data_scope"], 0) >= SCOPE_RANK.get(min_scope, 99)


# ---------------------------------------------------------------------
# GET /get_dashboard_stats — scoped to the caller's operational area,
# not the whole state, unless their role IS statewide (SP=district,
# SCRB=state). A Constable's dashboard should reflect their beat, not
# the whole force.
# ---------------------------------------------------------------------
def _dashboard_stats(app, officer):
    zcql = app.zcql()
    scope_clause = _case_scope_clause(app, officer)

    def count(extra_where=""):
        conditions = [c for c in [scope_clause, extra_where] if c]
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        q = f"SELECT COUNT(CaseMasterID) FROM CaseMaster{where}"
        rows = zcql.execute_query(q)
        return rows[0]["CaseMaster"]["CaseMasterID"] if rows else 0

    return {
        "totalCrimes": count(),
        "openFirs": count("CaseStatusID = 'STA1'"),
        "solved": count("CaseStatusID = 'STA3'"),
        "activeInvestigations": count("CaseStatusID = 'STA1' OR CaseStatusID = 'STA2'"),
        "scope": officer["data_scope"],
    }


def get_dashboard_stats(app, request, officer, audit):
    audit["resource_type"] = "dashboard_stats"
    return make_response(jsonify(_dashboard_stats(app, officer)), 200)


# ---------------------------------------------------------------------
# GET /search_case?district_id=&crime_subhead_id=&status_id=&limit=
# All filters optional. Filter by IDs (from /get_lookups), not names.
# RBAC: the caller's own scope clause is always AND-ed in — a district
# filter can narrow what a DISTRICT-scope officer sees within their own
# district, but can never widen a STATION/OWN_CASES officer out of theirs.
# ---------------------------------------------------------------------
def _search_cases(app, officer, district_id="", crime_subhead_id="", status_id="", limit=50):
    conditions = []

    scope_clause = _case_scope_clause(app, officer)
    if scope_clause:
        conditions.append(scope_clause)

    if district_id:
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{district_id}'")
        unit_ids = [u["UnitID"] for u in unit_rows]
        if not unit_ids:
            return []
        in_clause = ", ".join(f"'{u}'" for u in unit_ids)
        conditions.append(f"PoliceStationID IN ({in_clause})")

    if crime_subhead_id:
        conditions.append(f"CrimeMinorHeadID = '{crime_subhead_id}'")
    if status_id:
        conditions.append(f"CaseStatusID = '{status_id}'")

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM CaseMaster{where_clause} ORDER BY CrimeRegisteredDate DESC LIMIT {limit}"
    return zcql_rows(app, "CaseMaster", query)


def search_case(app, request, officer, audit):
    district_id = (request.args.get("district_id") or "").strip()
    crime_subhead_id = (request.args.get("crime_subhead_id") or "").strip()
    status_id = (request.args.get("status_id") or "").strip()
    limit = int(request.args.get("limit") or 50)

    cases = _search_cases(app, officer, district_id, crime_subhead_id, status_id, limit)
    audit["resource_type"] = "case_search"
    audit["record_count"] = len(cases)
    return make_response(jsonify({"results": cases, "count": len(cases)}), 200)


# ---------------------------------------------------------------------
# POST /add_case
# RBAC: a Constable may only register a case under their own
# PolicePersonID. SHO/SP may register on behalf of any officer within
# their station/district. SCRB Analyst never writes case data (STATE +
# read-only tier) — denied outright.
# ---------------------------------------------------------------------
def add_case(app, request, officer, audit):
    audit["resource_type"] = "case_create"

    if officer["role_name"] == "SCRB Analyst" or not officer["provisioned"]:
        return make_response(jsonify({"error": "forbidden", "message": "Your role does not permit registering cases."}), 403)

    body = request.get_json(force=True)

    required = ["crime_subhead_id", "police_station_id", "police_person_id"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return make_response(jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400)

    if officer["data_scope"] == "OWN_CASES" and body["police_person_id"] != officer["employee_id"]:
        return make_response(jsonify({"error": "forbidden", "message": "You can only register cases under your own name."}), 403)

    if officer["data_scope"] == "STATION" and body["police_station_id"] != officer["unit_id"]:
        return make_response(jsonify({"error": "forbidden", "message": "You can only register cases at your own station."}), 403)

    if officer["data_scope"] == "DISTRICT":
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{officer['district_id']}'")
        if body["police_station_id"] not in {u["UnitID"] for u in unit_rows}:
            return make_response(jsonify({"error": "forbidden", "message": "That station is outside your district."}), 403)

    subhead = zcql_rows(app, "CrimeSubHead",
        f"SELECT CrimeHeadID FROM CrimeSubHead WHERE CrimeSubHeadID = '{body['crime_subhead_id']}'")
    if not subhead:
        return make_response(jsonify({"error": "Unknown crime_subhead_id"}), 400)
    crime_head_id = subhead[0]["CrimeHeadID"]
    gravity_id = "GRV1" if crime_head_id in ("CH01", "CH03") else "GRV2"

    new_id = f"CASE{int(time.time())}"

    row_data = {
        "CaseMasterID": new_id,
        "CrimeNo": new_id,
        "CaseNo": new_id,
        "CrimeRegisteredDate": body.get("incident_date", ""),
        "PolicePersonID": body["police_person_id"],
        "PoliceStationID": body["police_station_id"],
        "CaseCategoryID": "CAT1",
        "GravityOffenceID": gravity_id,
        "CrimeMajorHeadID": crime_head_id,
        "CrimeMinorHeadID": body["crime_subhead_id"],
        "CaseStatusID": "STA1",
        "IncidentFromDate": body.get("incident_date", ""),
        "IncidentToDate": body.get("incident_date", ""),
        "InfoReceivedPSDate": body.get("incident_date", ""),
        "latitude": body.get("latitude", 0),
        "longitude": body.get("longitude", 0),
        "BriefFacts": body.get("brief_facts", ""),
    }

    table = app.datastore().table("CaseMaster")
    inserted = table.insert_row(row_data)
    audit["resource_id"] = new_id

    return make_response(jsonify({"message": "Case created", "case": inserted.get("row")}), 201)


# ---------------------------------------------------------------------
# PUT /update_case_status
# RBAC: same station/district boundary as add_case, checked against
# the case's OWN PoliceStationID (not the request body) so a caller
# can't bypass scope by guessing IDs.
# ---------------------------------------------------------------------
def update_case_status(app, request, officer, audit):
    audit["resource_type"] = "case_status_update"
    body = request.get_json(force=True)

    case_id = body.get("case_master_id")
    status_id = body.get("status_id")
    if not case_id or not status_id:
        return make_response(jsonify({"error": "case_master_id and status_id are required"}), 400)

    audit["resource_id"] = case_id

    rows = zcql_rows(app, "CaseMaster", f"SELECT ROWID, PolicePersonID, PoliceStationID FROM CaseMaster WHERE CaseMasterID = '{case_id}'")
    if not rows:
        return make_response(jsonify({"error": "Case not found"}), 404)
    case = rows[0]

    if officer["role_name"] == "SCRB Analyst":
        return make_response(jsonify({"error": "forbidden", "message": "Your role does not permit updating cases."}), 403)
    if officer["data_scope"] == "OWN_CASES" and case["PolicePersonID"] != officer["employee_id"]:
        return make_response(jsonify({"error": "forbidden", "message": "You can only update your own cases."}), 403)
    if officer["data_scope"] == "STATION" and case["PoliceStationID"] != officer["unit_id"]:
        return make_response(jsonify({"error": "forbidden", "message": "That case is outside your station."}), 403)
    if officer["data_scope"] == "DISTRICT":
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{officer['district_id']}'")
        if case["PoliceStationID"] not in {u["UnitID"] for u in unit_rows}:
            return make_response(jsonify({"error": "forbidden", "message": "That case is outside your district."}), 403)

    table = app.datastore().table("CaseMaster")
    updated = table.update_row({"ROWID": rows[0]["ROWID"], "CaseStatusID": status_id})

    return make_response(jsonify({"message": "Case updated", "case": updated.get("row")}), 200)


# ---------------------------------------------------------------------
# GET /get_network_graph?district_id=&min_cases=&limit=
#
# RBAC/redaction: this is a cross-case analytical view, which is
# exactly where identity leakage across investigations is easiest —
# so it's the main place the redaction layer applies:
#   - Any person who appears as a juvenile (<18) in ANY linked case has
#     their label masked unconditionally (Sec 74 JJ Act). This does not
#     depend on role — even an SP sees "[JUVENILE-a1b2c3]", not a name.
#   - Everyone else is masked unless officer.can_view_pii is True
#     (false only for SCRB Analyst today).
# The district_id filter still limits who counts as the graph's focus
# set, same as before; RBAC further restricts it to the officer's own
# scope (a STATION-scope SHO cannot pass district_id=<other district>
# to see beyond their station's own cases' co-accused ring).
# ---------------------------------------------------------------------
def _network_graph(app, officer, district_id="", min_cases=1, limit_persons=120):
    from collections import defaultdict

    effective_district_id = district_id
    if officer["data_scope"] in ("OWN_CASES", "STATION") and officer["district_id"]:
        effective_district_id = officer["district_id"]  # station/own-cases: always pinned to home district
    elif officer["data_scope"] == "DISTRICT" and officer["district_id"]:
        effective_district_id = officer["district_id"] if not district_id else officer["district_id"]

    scope_clause = _case_scope_clause(app, officer)
    case_where = f" WHERE {scope_clause}" if scope_clause else ""
    case_rows = zcql_rows(app, "CaseMaster", f"SELECT CaseMasterID, PoliceStationID, CrimeMinorHeadID, CrimeMajorHeadID FROM CaseMaster{case_where}")
    case_station = {c["CaseMasterID"]: c["PoliceStationID"] for c in case_rows}
    case_crime = {c["CaseMasterID"]: c.get("CrimeMinorHeadID") for c in case_rows}
    case_head = {c["CaseMasterID"]: c.get("CrimeMajorHeadID") for c in case_rows}
    scoped_case_ids = set(case_station.keys())

    accused_rows = zcql_rows(app, "Accused", "SELECT AccusedMasterID, CaseMasterID, AccusedName, PersonKey, AgeYear FROM Accused")
    accused_rows = [a for a in accused_rows if a["CaseMasterID"] in scoped_case_ids]

    unit_name = {u["UnitID"]: u["UnitName"] for u in zcql_rows(app, "Unit", "SELECT UnitID, UnitName FROM Unit")}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"]
                     for s in zcql_rows(app, "CrimeSubHead", "SELECT CrimeSubHeadID, CrimeHeadName FROM CrimeSubHead")}

    persons = defaultdict(list)
    cases_to_persons = defaultdict(list)

    for a in accused_rows:
        pk = a["PersonKey"]
        persons[pk].append({"caseId": a["CaseMasterID"], "name": a["AccusedName"], "age": a.get("AgeYear")})
        cases_to_persons[a["CaseMasterID"]].append(pk)

    district_case_ids = None
    if effective_district_id:
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{effective_district_id}'")
        unit_ids = {u["UnitID"] for u in unit_rows}
        district_case_ids = {cid for cid, station in case_station.items() if station in unit_ids}

    def touches_district(pk):
        return district_case_ids is None or any(e["caseId"] in district_case_ids for e in persons[pk])

    focus = [pk for pk, cs in persons.items() if len(cs) >= min_cases and touches_district(pk)]
    focus.sort(key=lambda pk: -len(persons[pk]))
    focus_set = set(focus[:limit_persons])

    included = set(focus_set)
    for pk in focus_set:
        relevant_cases = [e["caseId"] for e in persons[pk]
                           if district_case_ids is None or e["caseId"] in district_case_ids]
        for cid in relevant_cases:
            included.update(cases_to_persons[cid])

    nodes = []
    for pk in included:
        entries = persons[pk]
        stations = {case_station.get(e["caseId"]) for e in entries if case_station.get(e["caseId"])}
        crimes = {subhead_name.get(case_crime.get(e["caseId"])) for e in entries if case_crime.get(e["caseId"])}
        is_juvenile = any(_is_juvenile(e.get("age")) for e in entries)
        is_sensitive = any(case_head.get(e["caseId"]) in SENSITIVE_CRIME_HEADS for e in entries)

        if is_juvenile:
            label = _mask_person_label(pk, "JUVENILE")
            redacted = True
        elif not officer["can_view_pii"]:
            label = _mask_person_label(pk, "PERSON")
            redacted = True
        else:
            label = entries[-1]["name"]
            redacted = False

        nodes.append({
            "id": pk,
            "type": "person",
            "label": label,
            "redacted": redacted,
            "sensitiveCase": is_sensitive,
            "caseCount": len(entries),
            "repeatOffender": len(entries) >= 2,
            "stations": sorted({unit_name.get(s, s) for s in stations if s}),
            "crimeTypes": [] if redacted and not officer["can_view_pii"] else sorted({c for c in crimes if c}),
        })

    location_ids = {case_station[e["caseId"]] for pk in included for e in persons[pk] if case_station.get(e["caseId"])}
    for loc in location_ids:
        nodes.append({"id": f"loc::{loc}", "type": "location", "label": unit_name.get(loc, loc)})

    edge_weight = defaultdict(int)
    def bump(a, b, etype):
        key = (a, b, etype) if a < b else (b, a, etype)
        edge_weight[key] += 1

    for pk in included:
        for e in persons[pk]:
            station = case_station.get(e["caseId"])
            if station:
                bump(pk, f"loc::{station}", "location")

    for pks in cases_to_persons.values():
        pks_in = sorted({p for p in pks if p in included})
        for i in range(len(pks_in)):
            for j in range(i + 1, len(pks_in)):
                bump(pks_in[i], pks_in[j], "co-accused")

    edges = [{"source": a, "target": b, "type": t, "weight": w} for (a, b, t), w in edge_weight.items()]

    return {"nodes": nodes, "edges": edges}


def get_network_graph(app, request, officer, audit):
    district_id = (request.args.get("district_id") or "").strip()
    min_cases = int(request.args.get("min_cases") or 1)
    limit_persons = int(request.args.get("limit") or 120)
    result = _network_graph(app, officer, district_id, min_cases, limit_persons)
    audit["resource_type"] = "network_graph"
    audit["record_count"] = len(result["nodes"])
    return make_response(jsonify(result), 200)


# ---------------------------------------------------------------------
# GET /get_officers?unit_id=&district_id=
# RBAC: rosters are operational (not personal-privacy) data, but "need
# to know" still applies — a Constable/SHO only needs their own
# station's roster, an SP their district's. SCRB Analyst (STATE scope,
# no operational need for a personnel roster) is denied.
# ---------------------------------------------------------------------
def _officers(app, officer, unit_id="", district_id=""):
    units = zcql_rows(app, "Unit", "SELECT UnitID, UnitName, DistrictID FROM Unit WHERE Active = 1")
    districts = zcql_rows(app, "District", "SELECT DistrictID, DistrictName FROM District WHERE Active = 1")
    ranks = zcql_rows(app, "Rank", "SELECT RankID, RankName FROM Rank")
    unit_by_id = {u["UnitID"]: u for u in units}
    district_by_id = {d["DistrictID"]: d["DistrictName"] for d in districts}
    rank_by_id = {r["RankID"]: r["RankName"] for r in ranks}

    conditions = []
    if officer["data_scope"] in ("OWN_CASES", "STATION"):
        if not officer["unit_id"]:
            return []
        conditions.append(f"UnitID = '{officer['unit_id']}'")
    elif officer["data_scope"] == "DISTRICT":
        district_scope = officer["district_id"]
        if district_id and district_id != district_scope:
            return []
        unit_ids = [u["UnitID"] for u in units if u["DistrictID"] == district_scope]
        if not unit_ids:
            return []
        in_clause = ", ".join(f"'{u}'" for u in unit_ids)
        conditions.append(f"UnitID IN ({in_clause})")
    else:
        if unit_id:
            conditions.append(f"UnitID = '{unit_id}'")
        elif district_id:
            unit_ids = [u["UnitID"] for u in units if u["DistrictID"] == district_id]
            if not unit_ids:
                return []
            in_clause = ", ".join(f"'{u}'" for u in unit_ids)
            conditions.append(f"UnitID IN ({in_clause})")

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    employees = zcql_rows(app, "Employee", f"SELECT * FROM Employee{where_clause}")

    case_rows = zcql_rows(app, "CaseMaster",
        "SELECT PolicePersonID, CaseStatusID FROM CaseMaster WHERE CaseStatusID = 'STA1' OR CaseStatusID = 'STA2'")
    active_caseload = {}
    for c in case_rows:
        pid = c.get("PolicePersonID")
        active_caseload[pid] = active_caseload.get(pid, 0) + 1

    officers = []
    for e in employees:
        unit = unit_by_id.get(e.get("UnitID"), {})
        officers.append({
            "employee_id": e["EmployeeID"],
            "name": e.get("FirstName", ""),
            "rank": rank_by_id.get(e.get("RankID"), "Officer"),
            "unit": unit.get("UnitName", "Unassigned"),
            "district": district_by_id.get(unit.get("DistrictID"), "—"),
            "badge": e.get("KGID", ""),
            "active_cases": active_caseload.get(e["EmployeeID"], 0),
        })
    officers.sort(key=lambda o: o["active_cases"], reverse=True)
    return officers


def get_officers(app, request, officer, audit):
    audit["resource_type"] = "officer_roster"
    if officer["role_name"] == "SCRB Analyst":
        return make_response(jsonify({"error": "forbidden", "message": "Personnel rosters are not part of your role's data scope."}), 403)

    unit_id = (request.args.get("unit_id") or "").strip()
    district_id = (request.args.get("district_id") or "").strip()
    officers = _officers(app, officer, unit_id, district_id)
    audit["record_count"] = len(officers)
    return make_response(jsonify({"officers": officers, "count": len(officers)}), 200)


# ---------------------------------------------------------------------
# GET /get_reports
# RBAC: aggregate counts only (no names), scoped the same way the
# dashboard is. SCRB Analyst gets the full statewide breakdown here —
# this IS their job — but never a name-bearing row.
# ---------------------------------------------------------------------
def _reports(app, officer):
    scope_clause = _case_scope_clause(app, officer)
    where = f" WHERE {scope_clause}" if scope_clause else ""
    cases = zcql_rows(app, "CaseMaster",
        f"SELECT CaseMasterID, CrimeMajorHeadID, PoliceStationID, CaseStatusID, CrimeRegisteredDate FROM CaseMaster{where}")
    lookups = _lookups(app)
    head_name = {h["CrimeHeadID"]: h["CrimeGroupName"] for h in lookups["crime_heads"]}
    unit_district = {u["UnitID"]: u["DistrictID"] for u in lookups["units"]}
    district_name = {d["DistrictID"]: d["DistrictName"] for d in lookups["districts"]}
    status_name = {s["CaseStatusID"]: s["CaseStatusName"] for s in lookups["statuses"]}

    def bucket(get_key, name_lookup):
        counts = {}
        for c in cases:
            key = get_key(c)
            counts[key] = counts.get(key, 0) + 1
        return sorted(
            [{"label": name_lookup.get(k, k or "Unknown"), "count": v} for k, v in counts.items()],
            key=lambda r: r["count"], reverse=True,
        )

    by_crime_type = bucket(lambda c: c.get("CrimeMajorHeadID"), head_name)
    by_district = bucket(lambda c: unit_district.get(c.get("PoliceStationID")), district_name)
    by_status = bucket(lambda c: c.get("CaseStatusID"), status_name)

    total = len(cases)
    solved = sum(1 for c in cases if c.get("CaseStatusID") == "STA3")

    return {
        "total_cases": total,
        "solve_rate": round((solved / total) * 100, 1) if total else 0,
        "by_crime_type": by_crime_type,
        "by_district": by_district,
        "by_status": by_status,
        "scope": officer["data_scope"],
    }


def get_reports(app, request, officer, audit):
    audit["resource_type"] = "reports"
    return make_response(jsonify(_reports(app, officer)), 200)


# ---------------------------------------------------------------------
# GET /get_audit_log?limit=
# Who can see the audit trail is itself an access-control question: an
# SP can review their own district's activity; an SCRB Analyst can see
# everything, for statewide oversight. Station-level and below cannot
# see the audit trail at all — reading who-looked-at-what is itself
# privileged.
# ---------------------------------------------------------------------
def get_audit_log(app, request, officer, audit):
    audit["resource_type"] = "audit_log"
    if not _require_scope_at_least(officer, "DISTRICT"):
        return make_response(jsonify({"error": "forbidden", "message": "Audit trail access requires SP level or above."}), 403)

    limit = int(request.args.get("limit") or 200)
    conditions = []
    if officer["data_scope"] == "DISTRICT":
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{officer['district_id']}'")
        emp_rows = zcql_rows(app, "Employee", "SELECT EmployeeID, UnitID FROM Employee")
        emp_ids = [e["EmployeeID"] for e in emp_rows if e.get("UnitID") in {u["UnitID"] for u in unit_rows}]
        if emp_ids:
            in_clause = ", ".join(f"'{e}'" for e in emp_ids)
            conditions.append(f"EmployeeID IN ({in_clause})")
        else:
            return make_response(jsonify({"entries": [], "count": 0}), 200)

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    entries = zcql_rows(app, "AuditLog", f"SELECT * FROM AuditLog{where_clause} ORDER BY CreatedTime DESC LIMIT {limit}")
    audit["record_count"] = len(entries)
    return make_response(jsonify({"entries": entries, "count": len(entries)}), 200)


def _write_audit_log(app, officer, request, audit, response):
    """
    Writes one AuditLog row per request, always — this is what makes
    the trail complete rather than opt-in per route.
    """
    try:
        status_code = response.status_code
    except Exception:
        status_code = 0

    if audit.get("result_override"):
        result = audit["result_override"]
    elif status_code == 403:
        result = "DENIED"
    elif 200 <= status_code < 300:
        result = "SUCCESS"
    else:
        result = "ERROR"

    method_to_action = {"GET": "VIEW", "POST": "CREATE", "PUT": "UPDATE", "DELETE": "DELETE"}
    action = method_to_action.get(request.method, request.method)
    if request.path == "/search_case":
        action = "SEARCH"

    query_params = ""
    try:
        if request.method == "GET" and request.args:
            query_params = json.dumps(dict(request.args))[:500]
    except Exception:
        pass

    row_data = {
        "Timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "EmployeeID": officer.get("employee_id") or "",
        "EmployeeName": officer.get("name") or "",
        "RoleName": officer.get("role_name") or "Unassigned",
        "Action": action,
        "Endpoint": request.path,
        "ResourceType": audit.get("resource_type") or "",
        "ResourceID": str(audit.get("resource_id") or ""),
        "RecordCount": audit.get("record_count") if audit.get("record_count") is not None else -1,
        "QueryParams": query_params,
        "Result": result,
        "StatusCode": status_code,
    }
    app.datastore().table("AuditLog").insert_row(row_data)


# ---------------------------------------------------------------------
# POST /ai_assistant
# RBAC: context is now built through the SAME scoped helpers
# (_dashboard_stats, _network_graph, _search_cases) every REST route
# uses, so the assistant can never leak data outside the caller's own
# scope just because it's "just a chat answer."
# ---------------------------------------------------------------------
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You are IntelliQ, an AI assistant embedded in the Karnataka State Police \
crime-intelligence platform, used by SCRB analysts and station officers. \
You will be given a CONTEXT block of real, live data pulled from the police records \
system for this specific question, plus the analyst's question. \
Rules:
- Answer ONLY using facts present in CONTEXT. Never invent case numbers, names, or statistics.
- If CONTEXT doesn't contain what's asked, say so plainly and suggest what to search instead.
- Keep answers under 100 words, professional, no bullet spam, plain prose.
- If pointing the analyst to a screen would help, end your reply on its own new line with:
  ACTION: {"tab": "Network"|"Crime Map"|"Cases"|"Dashboard"}
  Only include ACTION when genuinely useful, and it must be the last line, valid JSON, nothing after it."""


def _detect_district(message, districts):
    msg_low = message.lower()
    for d in districts:
        if d["DistrictName"].lower() in msg_low:
            return d["DistrictID"], d["DistrictName"]
    return "", ""


def _detect_crime_subhead(message, crime_subheads):
    msg_low = message.lower()
    for s in crime_subheads:
        name = (s.get("CrimeHeadName") or "")
        if name and name.lower() in msg_low:
            return s["CrimeSubHeadID"], name
    return "", ""


def _build_assistant_context(app, officer, message):
    msg_low = message.lower()
    lookups = _lookups(app)
    district_id, district_name = _detect_district(message, lookups["districts"])
    subhead_id, subhead_name = _detect_crime_subhead(message, lookups["crime_subheads"])

    # RBAC: a station/district-scoped officer can't use the chat to ask
    # about a district that isn't theirs.
    if officer["data_scope"] in ("OWN_CASES", "STATION", "DISTRICT") and district_id and district_id != officer["district_id"]:
        district_id, district_name = "", ""

    context = {"dashboardStats": _dashboard_stats(app, officer)}
    suggested_tab = None

    network_kw = ["network", "repeat offender", "connection", "linked", "gang", "associat", "co-accused", "coaccused", "ring"]
    map_kw = ["hotspot", "map", "cluster", "where"]

    if any(k in msg_low for k in network_kw) or district_id:
        graph = _network_graph(app, officer, district_id=district_id, min_cases=2, limit_persons=8)
        top_persons = sorted(
            [n for n in graph["nodes"] if n["type"] == "person"],
            key=lambda n: -n["caseCount"],
        )[:5]
        context["topRepeatOffenders"] = [
            {"name": p["label"], "caseCount": p["caseCount"], "stations": p["stations"], "crimeTypes": p["crimeTypes"]}
            for p in top_persons
        ]
        suggested_tab = "Network"

    if any(k in msg_low for k in map_kw):
        suggested_tab = suggested_tab or "Crime Map"

    if district_id or subhead_id or "case" in msg_low or "fir" in msg_low or "status" in msg_low:
        cases = _search_cases(app, officer, district_id=district_id, crime_subhead_id=subhead_id, limit=5)
        context["recentMatchingCases"] = [
            {
                "caseNo": c.get("CaseNo"),
                "date": c.get("CrimeRegisteredDate"),
                "status": c.get("CaseStatusID"),
                "station": c.get("PoliceStationID"),
            }
            for c in cases
        ]
        if district_id:
            context["filteredOnDistrict"] = district_name
        if subhead_id:
            context["filteredOnCrimeType"] = subhead_name
        suggested_tab = suggested_tab or "Cases"

    return context, suggested_tab


def ai_assistant(app, request, officer, audit):
    audit["resource_type"] = "ai_assistant_query"
    body = request.get_json(force=True) or {}
    message = (body.get("message") or "").strip()
    history = body.get("history") or []

    if not message:
        return make_response(jsonify({"error": "message is required"}), 400)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return make_response(jsonify({
            "reply": "The AI assistant isn't configured yet — an admin needs to set GEMINI_API_KEY "
                     "in Catalyst Console \u2192 Functions \u2192 ksp_intelli_q_function \u2192 Environment Variables.",
            "action": None,
        }), 200)

    try:
        context, suggested_tab = _build_assistant_context(app, officer, message)
    except Exception as e:
        context, suggested_tab = {"error": f"context lookup failed: {e}"}, None

    contents = []
    for turn in history[-6:]:
        role = "model" if turn.get("role") in ("ai", "model", "assistant") else "user"
        text = (turn.get("text") or "").strip()
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})

    contents.append({
        "role": "user",
        "parts": [{"text": f"CONTEXT (live data, current as of now):\n{json.dumps(context)}\n\nQuestion: {message}"}],
    })

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 400},
    }

    gemini_url = GEMINI_URL_TEMPLATE.format(model=GEMINI_MODEL)

    try:
        resp = None
        for attempt in range(3):
            resp = requests.post(
                gemini_url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
            if resp.status_code != 429:
                break
            time.sleep(1.5 * (attempt + 1))
        resp.raise_for_status()
        data = resp.json()
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            return make_response(jsonify({
                "reply": "IntelliQ is getting a lot of requests right now — give it about a minute and ask again.",
                "action": None,
                "detail": str(e),
            }), 200)
        return make_response(jsonify({
            "reply": "Couldn't reach the AI model just now. Try again in a moment.",
            "action": None,
            "detail": str(e),
        }), 200)
    except Exception as e:
        return make_response(jsonify({
            "reply": "Couldn't reach the AI model just now. Try again in a moment.",
            "action": None,
            "detail": str(e),
        }), 200)

    action = None
    reply = raw_text
    if "ACTION:" in raw_text:
        reply_part, _, action_part = raw_text.rpartition("ACTION:")
        reply = reply_part.strip()
        try:
            action = json.loads(action_part.strip())
        except (ValueError, json.JSONDecodeError):
            action = {"tab": suggested_tab} if suggested_tab else None

    return make_response(jsonify({"reply": reply or raw_text, "action": action}), 200)