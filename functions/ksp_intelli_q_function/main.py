import logging
import os
import json
import time
import csv
import io
import hashlib
import datetime
from flask import Request, make_response, jsonify # type: ignore
import zcatalyst_sdk #type: ignore
import ncrb_export


# =======================================================================
# RBAC — seven tiers, one per Karnataka Police Rank, assigned to each
# officer through the Role table (Employee.RoleID -> Role.RoleID), same
# admin-assigned pattern this project always used. RoleID is kept
# separate from RankID on purpose: an officer's rank and their granted
# system-access tier are two different facts, and an admin should be
# able to correct one without the other silently moving. In practice
# every officer's RoleID will normally point at the Role row matching
# their real rank, but nothing in the code forces that 1:1.
#
# RANK_ACCESS below is the reference table for what to put in the Role
# table (RoleID/RoleName/DataScope/CanViewPII/CanExportNCRB/Level
# columns) — seed the Role table with these seven rows, then set each
# Employee's RoleID to the matching row. It doubles as the FALLBACK
# used automatically for any employee whose RoleID is still blank (or
# whose Role table row can't be resolved), keyed off their RankID
# instead — so newly-provisioned officers still get a sane,
# rank-appropriate access tier before an admin has explicitly assigned
# one, rather than dropping to DEFAULT_ROLE's bare minimum. See
# _get_officer_context for exactly how the two are combined.
#
#   Rank                         Data scope   Level  PII  NCRB export
#   ---------------------------  -----------  -----  ---  -----------
#   Constable                    OWN_CASES      1    Yes  No
#   Head Constable                OWN_CASES      2    Yes  No
#   Asst. Sub-Inspector (ASI)     STATION        3    Yes  No
#   Sub-Inspector (SI)            STATION        4    Yes  No
#   Inspector                     STATION        5    Yes  Yes
#   Deputy Superintendent (DySP)  DISTRICT       6    Yes  Yes
#   Superintendent of Police (SP) DISTRICT       7    Yes  Yes
#
# Rationale:
#   - Constable/Head Constable ("beat" ranks) only ever see cases they
#     are personally the investigating officer on — OWN_CASES.
#   - ASI/SI/Inspector are the ranks that actually staff a police
#     station as Investigating Officer/SHO, so they get STATION scope
#     over their whole station's caseload, not just their own cases.
#   - DySP/SP are the district-level oversight ranks (a DySP supervises
#     a sub-division of stations, an SP the whole district), so both
#     get DISTRICT scope. Statewide (STATE) scope is intentionally
#     unassigned to any of the seven ranks below — nobody sees beyond
#     their own district's data.
#   - can_view_pii is True for all seven: these are all operational
#     police ranks, not a read-only analyst tier, so nobody's identity
#     view is degraded to a masked pseudonym on account of rank alone.
#     Victim-identity masking in sensitive-crime matters (Sec 72 BNS —
#     the Bharatiya Nyaya Sanhita, 2023 successor to IPC Sec 228A; NOT
#     BNSS Sec 74, a different, unrelated provision — see the note at
#     _redact_victim below) and juvenile masking (Sec 74 JJ Act) are handled entirely
#     separately in _redact_victim/_redact_accused below and apply to
#     EVERY rank equally — that protection is about the case, not the
#     viewer's seniority, and it is never weakened here.
#   - can_export_ncrb (crime-return sign-off) is reserved for Inspector
#     and above, mirroring who is actually authorized to certify NCRB
#     returns in practice.
#
# Fail-safe default: an employee with no resolvable RoleID or RankID
# gets the MOST restrictive tier (own cases only, no export), never the
# most permissive. See _get_officer_context.
# =======================================================================
RANK_ACCESS = {
    "RNK01": {"role_name": "Constable",                  "data_scope": "OWN_CASES", "level": 1, "can_view_pii": True, "can_export_ncrb": False},
    "RNK02": {"role_name": "Head Constable",              "data_scope": "OWN_CASES", "level": 2, "can_view_pii": True, "can_export_ncrb": False},
    "RNK03": {"role_name": "Asst. Sub-Inspector",         "data_scope": "STATION",   "level": 3, "can_view_pii": True, "can_export_ncrb": False},
    "RNK04": {"role_name": "Sub-Inspector",               "data_scope": "STATION",   "level": 4, "can_view_pii": True, "can_export_ncrb": False},
    "RNK05": {"role_name": "Inspector",                   "data_scope": "STATION",   "level": 5, "can_view_pii": True, "can_export_ncrb": True},
    "RNK06": {"role_name": "Deputy Superintendent",       "data_scope": "DISTRICT",  "level": 6, "can_view_pii": True, "can_export_ncrb": True},
    "RNK07": {"role_name": "Superintendent of Police",    "data_scope": "DISTRICT",  "level": 7, "can_view_pii": True, "can_export_ncrb": True},
}

DEFAULT_ROLE = {
    "role_id": None,
    "role_name": "Unassigned",
    "data_scope": "OWN_CASES",
    "can_view_pii": True,
    "can_export_ncrb": False,
    "level": 0,
    "is_scrb_analyst": False,
}

# =======================================================================
# SCRB Analyst — an EIGHTH access tier, distinct from the seven police
# ranks above in one important way: it is not a rank at all. A State
# Crime Records Bureau analyst is a statistics/compliance role sitting
# above every district, whose whole job is consolidating what each
# district has already filed into the statewide NCRB return — not
# investigating cases. That difference is encoded directly, not just
# implied by scope:
#
#   Role                Data scope   Level  PII    NCRB export
#   ------------------  -----------  -----  -----  -----------
#   SCRB Analyst         STATE         6    No     Yes
#
# Unlike every RANK_ACCESS row, can_view_pii is False here on purpose:
# an SCRB analyst is exactly the "read-only/analyst tier" the comments
# above always anticipated — someone whose statutory function is
# counting and classifying cases, never learning who is in them. This
# single flag is what already makes _redact_victim/_redact_accused mask
# every name for this role (juveniles and sensitive-crime victims were
# always masked for a STATE-scope non-PII viewer; this makes it true
# for EVERY person, not only the ones already covered by those two
# specific rules) — no separate redaction path needed.
#
# level=6, not 7: deliberately kept BELOW OVERSIGHT_MIN_LEVEL (7), so
# an SCRB analyst never gets the Audit Log or Bias Audit tabs — those
# are internal police-oversight views about officer conduct, not
# statistical-return material, and stay Superintendent-of-Police-only.
# level is irrelevant to FIR_AMEND_MIN_LEVEL in practice anyway, since
# is_scrb_analyst below denies case-mutation routes outright regardless
# of level (see add_case / update_case_status).
#
# This is NOT a RANK_ACCESS row (SCRB analysts have no RankID/Employee
# rank — they're not police officers) — it's seeded directly into the
# Role table and picked up entirely through the RoleID path in
# _get_officer_context, same as any other Role row. To provision one:
#   1. Add an IsSCRBAnalyst column (Boolean/Number, default 0) to the
#      Role table, alongside the existing DataScope/CanViewPII/
#      CanExportNCRB/Level columns.
#   2. Insert one Role row: RoleName="SCRB Analyst", DataScope="STATE",
#      CanViewPII=0, CanExportNCRB=1, Level=6, IsSCRBAnalyst=1.
#   3. Set the SCRB user's Employee.RoleID to that row. (They still
#      need an Employee row + a UnitID/RankID for the login to resolve
#      at all — RankID can point at any placeholder Rank row, since
#      RoleID always wins over the RankID fallback once it resolves.)
# =======================================================================
SCRB_ROLE_SEED = {
    "role_name": "SCRB Analyst",
    "data_scope": "STATE",
    "level": 6,
    "can_view_pii": False,
    "can_export_ncrb": True,
    "is_scrb_analyst": True,
}

# Ordered least -> most privileged, used by the audit-log/bias-audit
# routes' own access checks. STATE is kept for forward-compatibility
# (e.g. a future SCRB/state-HQ tier) even though none of the seven
# current ranks reach it.
SCOPE_RANK = {"OWN_CASES": 1, "STATION": 2, "DISTRICT": 3, "STATE": 4}

# Two oversight-sensitive thresholds, expressed as rank LEVEL (see
# RANK_ACCESS) rather than a scope or a role-name string, so they read
# the same hierarchy the rest of the RBAC table uses:
#   - FIR_AMEND_MIN_LEVEL: rewriting FIR content (as opposed to just
#     viewing a case or updating its status) requires acting as an
#     Investigating Officer — ASI and above, per Sec 173 BNSS ("officer
#     in charge of a police station" and those assisting under their
#     direction). Constable/Head Constable can still register a case
#     and update its status, just not amend its substantive content.
#   - OVERSIGHT_MIN_LEVEL: the audit trail and the bias-audit view are
#     themselves privileged views ABOUT policing activity, not case
#     data — restricted to Superintendent of Police, the senior-most of
#     the seven ranks and the district's single point of oversight.
FIR_AMEND_MIN_LEVEL = 3
OVERSIGHT_MIN_LEVEL = 7


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
            ("/get_case_detail", "GET"): get_case_detail,
            ("/get_case_edit_detail", "GET"): get_case_edit_detail,
            ("/update_case_detail", "PUT"): update_case_detail,
            ("/get_linked_fir_matches", "GET"): get_linked_fir_matches,
            ("/extract_fir_entities", "POST"): extract_fir_entities,
            ("/get_network_graph", "GET"): get_network_graph,
            ("/get_person_profile", "GET"): get_person_profile,
            ("/get_investigation_brief", "GET"): get_investigation_brief,
            ("/get_officers", "GET"): get_officers,
            ("/get_reports", "GET"): get_reports,
            ("/get_ncrb_report", "GET"): get_ncrb_report,
            ("/export_ncrb_return", "GET"): export_ncrb_return,
            ("/export_ncrb_return_bundle", "GET"): export_ncrb_return_bundle,
            ("/ai_assistant", "POST"): ai_assistant,
            ("/get_audit_log", "GET"): get_audit_log,
            ("/get_hotspots", "GET"): get_hotspots,
            ("/get_trend_alerts", "GET"): get_trend_alerts,
            ("/get_incident_log", "GET"): get_incident_log,
            ("/get_crime_trend", "GET"): get_crime_trend,
            ("/get_bias_audit", "GET"): get_bias_audit,
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
            logger.error(f"Audit logging failed (non-fatal): {audit_err} | row_data={audit.get('_last_row_data')}")


# ---------------------------------------------------------------------
# Small helper: run a ZCQL query and return just the row dicts for the
# named table (ZCQL wraps each row as {"TableName": {...columns...}})
# ---------------------------------------------------------------------
def zcql_rows(app, table_name, query):
    rows = app.zcql().execute_query(query)
    return [r[table_name] for r in rows]


# ZCQL hard-caps every SELECT at 300 rows per call (regardless of
# columns selected) — a query with no LIMIT of its own doesn't get
# "everything", it throws once the table backing it has more than 300
# rows. Any unbounded query against a table that can plausibly grow
# past 300 rows (CaseMaster, Accused, ...) needs to page through with
# LIMIT {offset},300 and accumulate, rather than relying on ZCQL to
# just hand back the whole table.
ZCQL_MAX_PAGE = 300


def zcql_rows_paginated(app, table_name, base_query):
    all_rows = []
    offset = 0
    while True:
        page_query = f"{base_query} LIMIT {offset},{ZCQL_MAX_PAGE}"
        page = zcql_rows(app, table_name, page_query)
        all_rows.extend(page)
        if len(page) < ZCQL_MAX_PAGE:
            break
        offset += ZCQL_MAX_PAGE
    return all_rows


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

    # RBAC lookup — Role table (Employee.RoleID -> Role.RoleID) is the
    # PRIMARY source of truth, same admin-assigned pattern as before:
    # RoleID is deliberately a separate field from RankID so access tier
    # can be granted/revoked per officer without touching their rank.
    # Populate the Role table with the seven rows in RANK_ACCESS below
    # (RoleID/RoleName/DataScope/CanViewPII/CanExportNCRB/Level columns)
    # and set each Employee's RoleID to the matching row.
    #
    # Falls back to RANK_ACCESS[RankID] ONLY when RoleID is blank or
    # doesn't resolve (e.g. an officer not yet assigned a Role row, or
    # the Role table itself not reachable) — so the system still fails
    # to a sane, rank-appropriate default rather than DEFAULT_ROLE's
    # bare minimum for every unassigned employee, while still letting
    # the Role table override that default whenever one is set.
    role_id = emp.get("RoleID")
    resolved = False
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
                    # Defaults to False for every existing Role row that
                    # predates this column — only a row explicitly
                    # seeded per SCRB_ROLE_SEED above sets it, so this
                    # never silently upgrades an existing officer.
                    "is_scrb_analyst": bool(int(r.get("IsSCRBAnalyst", 0))),
                })
                resolved = True
        except Exception:
            pass  # Role table not reachable — fall through to RankID below

    if not resolved:
        rank_id = emp.get("RankID")
        access = RANK_ACCESS.get(rank_id)
        if access:
            ctx.update({"role_id": rank_id, **access})
        # else: keep DEFAULT_ROLE values already in ctx (fail closed)

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
        "role": rank[0]["RankName"] if rank else "Officer",   # police Rank, for the ID badge
        "station": unit[0]["UnitName"] if unit else "Unassigned",
        "badge": emp["KGID"],
        # RBAC fields the frontend uses for tab/feature gating. accessRole
        # is now always the same value as `role` above (RANK_ACCESS keys
        # off the same RankID), kept as its own field since the frontend
        # already treats it as the RBAC-specific one — see Dashboard.jsx.
        "accessRole": officer["role_name"],
        "dataScope": officer["data_scope"],
        "canViewPII": officer["can_view_pii"],
        "canExportNCRB": officer["can_export_ncrb"],
        "isSCRBAnalyst": officer["is_scrb_analyst"],
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
# Shared redaction rules — used by EVERY route that returns a named
# person (network graph, case detail, and anything added later), so
# the masking logic lives in exactly one place instead of being
# re-implemented per screen. That's the actual fix for "some tabs
# apply this and some don't": there is now only one place this logic
# can drift.
#
# _redact_accused: unchanged rule already used by the network graph —
#   juvenile -> always masked (Sec 74 JJ Act); otherwise masked only if
#   officer.can_view_pii is False (none of the seven ranks today, but
#   kept as a real check for any future read-only/analyst tier).
#
# _redact_victim: same juvenile/PII rules, PLUS a victim-privacy rule
#   that doesn't apply to accused: in a sensitive-crime case (proxy for
#   POCSO / sexual-offence matters, Sec 72 BNS victim-identity
#   protection — the Bharatiya Nyaya Sanhita, 2023 successor to IPC
#   Sec 228A; note this is a BNS section, NOT "BNSS Sec 74", which is a
#   different, unrelated procedural provision — easy citation to get
#   wrong since JJ Act Sec 74 is *also* an identity-protection section,
#   just for a different statute), the victim's name is masked for
#   anyone who is not the investigating officer or that officer's own
#   station — i.e. an SP overseeing a district, or an SCRB analyst
#   (STATE scope, can_view_pii=False by design — see SCRB_ROLE_SEED
#   below), never gets the victim's name for these cases, even though
#   they can see the rest of the case.
#   `viewer_has_case_access` should be True only when the case is the
#   caller's own case or their own station's case (OWN_CASES/STATION
#   scope, already matched) — never for DISTRICT/STATE scope.
# ---------------------------------------------------------------------
def _redact_accused(person_key, name, age_year, officer):
    if _is_juvenile(age_year):
        return _mask_person_label(person_key, "JUVENILE"), True
    if not officer["can_view_pii"]:
        return _mask_person_label(person_key, "PERSON"), True
    return name, False


def _redact_victim(person_key, name, age_year, crime_head_id, officer, viewer_has_case_access):
    if _is_juvenile(age_year):
        return _mask_person_label(person_key, "JUVENILE"), True
    if crime_head_id in SENSITIVE_CRIME_HEADS and not viewer_has_case_access:
        return _mask_person_label(person_key, "VICTIM"), True
    if not officer["can_view_pii"]:
        return _mask_person_label(person_key, "PERSON"), True
    return name, False


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
        return ""  # No rank among the current seven reaches STATE scope —
                    # kept for forward-compatibility with a future
                    # statewide tier, which would see everything.
    return "1=0"


def _require_scope_at_least(officer, min_scope):
    return SCOPE_RANK.get(officer["data_scope"], 0) >= SCOPE_RANK.get(min_scope, 99)


# ---------------------------------------------------------------------
# Case-level membership check — "is THIS specific case inside this
# officer's scope", as opposed to _case_scope_clause which builds a
# WHERE fragment for searching many cases at once. Every route that
# operates on a single case_master_id (update_case_status, and now
# get_case_detail) should use this so the boundary is defined once.
#
# Returns (in_scope: bool, is_direct_holder: bool). is_direct_holder is
# True only for OWN_CASES/STATION scope — i.e. the caller is the
# investigating officer or shares that officer's station. DISTRICT
# scope (DySP, SP) can be in_scope=True for oversight purposes
# without being a direct holder — used by _redact_victim to keep
# victim-privacy masking in place for oversight roles even though they
# can otherwise see the case.
# ---------------------------------------------------------------------
def _case_in_officer_scope(app, officer, case_row):
    scope = officer["data_scope"]
    if scope == "OWN_CASES":
        in_scope = case_row.get("PolicePersonID") == officer["employee_id"]
        return in_scope, in_scope
    if scope == "STATION":
        in_scope = case_row.get("PoliceStationID") == officer["unit_id"]
        return in_scope, in_scope
    if scope == "DISTRICT":
        if not officer["district_id"]:
            return False, False
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{officer['district_id']}'")
        in_scope = case_row.get("PoliceStationID") in {u["UnitID"] for u in unit_rows}
        return in_scope, False
    if scope == "STATE":
        return True, False
    return False, False


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
        # NOTE: ZCQL accepts "AS cnt" syntactically but does NOT rename the
        # key in the returned row — it comes back keyed by the original
        # expression (e.g. "COUNT(CaseMasterID)"), not the alias. Grab the
        # single value out of the row instead of indexing by the alias name,
        # so this doesn't depend on however ZCQL happens to key it.
        q = f"SELECT COUNT(CaseMasterID) AS cnt FROM CaseMaster{where}"
        rows = zcql.execute_query(q)
        if not rows:
            return 0
        row = rows[0]["CaseMaster"]
        return int(next(iter(row.values())))

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
    # Clamped to 300: ZCQL rejects a "SELECT *" query asking for more
    # than 300 rows outright (the query itself throws, not just a
    # truncated result), so this has to be enforced here, not just
    # trusted from the caller.
    limit = min(int(request.args.get("limit") or 50), 300)

    cases = _search_cases(app, officer, district_id, crime_subhead_id, status_id, limit)
    audit["resource_type"] = "case_search"
    audit["record_count"] = len(cases)
    return make_response(jsonify({"results": cases, "count": len(cases)}), 200)


# ---------------------------------------------------------------------
# POST /add_case
# RBAC: a Constable/Head Constable (OWN_CASES scope) may only register
# a case under their own PolicePersonID — enforced below via the
# station/self checks already in this route, not a rank denylist. Every
# one of the seven ranks may register a case, which mirrors real
# practice: any officer (including a duty constable) can record
# information under Sec 173 BNSS, with the station's SHO countersigning.
# ---------------------------------------------------------------------
def add_case(app, request, officer, audit):
    audit["resource_type"] = "case_create"

    if not officer["provisioned"]:
        return make_response(jsonify({"error": "forbidden", "message": "This login isn't linked to an officer profile yet."}), 403)

    # SCRB analysts (STATE scope, no case ownership) never register FIRs —
    # that's an investigating-officer function under Sec 173 BNSS, and an
    # SCRB analyst isn't one. See SCRB_ROLE_SEED above.
    if officer["is_scrb_analyst"]:
        return make_response(jsonify({"error": "forbidden", "message": "SCRB analysts have statewide read-only access for statistical reporting and cannot register FIRs."}), 403)

    body = request.get_json(force=True)

    required = ["crime_subhead_id", "police_station_id", "police_person_id"]
    missing = [f for f in required if not body.get(f)]

    # A real FIR always names who complained and who the victim(s) are —
    # those live in ComplainantDetails / Victim, not CaseMaster, but the
    # form is still incomplete without them. Accused are frequently
    # unknown at FIR-registration time, so that list stays optional.
    complainant = body.get("complainant") or {}
    if not complainant.get("name"):
        missing.append("complainant.name")

    victims = [v for v in (body.get("victims") or []) if v.get("name")]
    if not victims:
        missing.append("victims[].name")

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

    # Shared millisecond timestamp as an ID prefix, then an index suffix
    # per row — avoids collisions between the several rows this one
    # request inserts (a plain int(time.time()) is only second-granular
    # and would collide across complainant/victim/accused rows).
    ts = int(time.time() * 1000)

    complainant_row = {
        "ComplainantID": f"COMP{ts}",
        "CaseMasterID": new_id,
        "ComplainantName": complainant.get("name"),
        "AgeYear": complainant.get("age") or None,
        "Occupation": complainant.get("occupation", ""),
        "GenderID": complainant.get("gender", ""),
    }
    inserted_complainant = app.datastore().table("ComplainantDetails").insert_row(complainant_row)

    inserted_victims = []
    for i, v in enumerate(victims):
        victim_row = {
            "VictimMasterID": f"VIC{ts}{i}",
            "CaseMasterID": new_id,
            "VictimName": v.get("name"),
            "AgeYear": v.get("age") or None,
            "GenderID": v.get("gender", ""),
            "VictimPolice": 0,
        }
        inserted_victims.append(app.datastore().table("Victim").insert_row(victim_row).get("row"))

    accused_in = [a for a in (body.get("accused") or []) if a.get("name")]
    inserted_accused = []
    for i, a in enumerate(accused_in):
        accused_row = {
            "AccusedMasterID": f"ACC{ts}{i}",
            "CaseMasterID": new_id,
            "AccusedName": a.get("name"),
            "AgeYear": a.get("age") or None,
            "GenderID": a.get("gender", ""),
            "PersonID": f"A{i + 1}",
            # Each accused entered through this form is treated as a new
            # identity (no cross-case person matching here) — PersonKey
            # just needs to be unique to this row, same shape as the
            # synthetic-data generator's PER ids.
            "PersonKey": f"PER{ts}{i}",
        }
        inserted_accused.append(app.datastore().table("Accused").insert_row(accused_row).get("row"))

    return make_response(jsonify({
        "message": "Case created",
        "case": inserted.get("row"),
        "complainant": inserted_complainant.get("row"),
        "victims": inserted_victims,
        "accused": inserted_accused,
    }), 201)


# ---------------------------------------------------------------------
# PUT /update_case_status
# RBAC: same station/district boundary as add_case, checked against
# the case's OWN PoliceStationID (not the request body) so a caller
# can't bypass scope by guessing IDs.
# ---------------------------------------------------------------------
def update_case_status(app, request, officer, audit):
    audit["resource_type"] = "case_status_update"

    # SCRB analysts get in_scope=True below (STATE scope sees every
    # case for reporting purposes), but that's a read-only allowance,
    # not license to change a case's status — block it explicitly
    # rather than relying on is_direct_holder, which this route doesn't
    # otherwise check.
    if officer["is_scrb_analyst"]:
        return make_response(jsonify({"error": "forbidden", "message": "SCRB analysts have statewide read-only access for statistical reporting and cannot update case status."}), 403)

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

    in_scope, _ = _case_in_officer_scope(app, officer, case)
    if not in_scope:
        return make_response(jsonify({"error": "forbidden", "message": "That case is outside your access scope."}), 403)

    table = app.datastore().table("CaseMaster")
    updated = table.update_row({"ROWID": rows[0]["ROWID"], "CaseStatusID": status_id})

    return make_response(jsonify({"message": "Case updated", "case": updated.get("row")}), 200)


# ---------------------------------------------------------------------
# Shared by get_case_edit_detail / update_case_detail.
#
# RBAC: amending FIR content (crime type, narrative, and the
# complainant/victim/accused rolls) is deliberately a NARROWER
# permission than viewing a case or updating its status. Two things
# both have to be true:
#   1. Direct case holder (OWN_CASES scope where the case is the
#      caller's own, or STATION scope where it's the caller's own
#      station's case) — a DySP/SP with district oversight visibility
#      (_case_in_officer_scope in_scope True) can see the case but not
#      rewrite it, same as a real reviewing officer above station level.
#   2. Rank level >= FIR_AMEND_MIN_LEVEL (Asst. Sub-Inspector and
#      above) — a Constable/Head Constable can register a case and
#      update its status, but substantively amending FIR content is an
#      Investigating Officer function under Sec 173 BNSS, which starts
#      at ASI.
# Reassigning a case to a different station or officer is a transfer
# workflow, not an edit, so police_station_id / police_person_id are
# intentionally NOT editable here.
# ---------------------------------------------------------------------
def _case_edit_permission(app, officer, case_row):
    in_scope, is_direct_holder = _case_in_officer_scope(app, officer, case_row)
    if not in_scope:
        return False, make_response(jsonify({"error": "forbidden", "message": "That case is outside your access scope."}), 403)
    if not is_direct_holder or officer["level"] < FIR_AMEND_MIN_LEVEL:
        return False, make_response(jsonify({"error": "forbidden", "message": "Only the investigating officer or station (Asst. Sub-Inspector and above) may amend this FIR."}), 403)
    return True, None


# ---------------------------------------------------------------------
# GET /get_case_edit_detail?case_master_id=
#
# Pre-fills the amend-FIR form. Unlike get_case_detail, this returns
# UNREDACTED names — masking exists to limit exposure to viewers
# beyond the case's own officer/station (oversight roles, analysts,
# etc.), but this route is only reachable by the direct holder in the
# first place (_case_edit_permission), i.e. someone who already
# legitimately holds the case file. A masked juvenile/victim name
# would make it impossible to correct a typo in it.
# ---------------------------------------------------------------------
def get_case_edit_detail(app, request, officer, audit):
    audit["resource_type"] = "case_edit_detail"
    case_id = (request.args.get("case_master_id") or "").strip()
    if not case_id:
        return make_response(jsonify({"error": "case_master_id is required"}), 400)
    audit["resource_id"] = case_id

    rows = zcql_rows(app, "CaseMaster", f"SELECT * FROM CaseMaster WHERE CaseMasterID = '{case_id}'")
    if not rows:
        return make_response(jsonify({"error": "Case not found"}), 404)
    case = rows[0]

    can_edit, err = _case_edit_permission(app, officer, case)
    if not can_edit:
        return err

    accused_rows = zcql_rows(app, "Accused",
        f"SELECT AccusedMasterID, AccusedName, AgeYear, GenderID FROM Accused WHERE CaseMasterID = '{case_id}'")
    victim_rows = zcql_rows(app, "Victim",
        f"SELECT VictimMasterID, VictimName, AgeYear, GenderID FROM Victim WHERE CaseMasterID = '{case_id}'")
    complainant_rows = zcql_rows(app, "ComplainantDetails",
        f"SELECT ComplainantID, ComplainantName, AgeYear, Occupation, GenderID FROM ComplainantDetails WHERE CaseMasterID = '{case_id}'")

    complainant = complainant_rows[0] if complainant_rows else {}

    return make_response(jsonify({
        "case": {
            "caseMasterId": case["CaseMasterID"],
            "crime_subhead_id": case.get("CrimeMinorHeadID", ""),
            "incident_date": case.get("CrimeRegisteredDate", ""),
            "latitude": case.get("latitude", 0),
            "longitude": case.get("longitude", 0),
            "brief_facts": case.get("BriefFacts", ""),
        },
        "complainant": {
            "id": complainant.get("ComplainantID"),
            "name": complainant.get("ComplainantName", ""),
            "age": complainant.get("AgeYear"),
            "gender": complainant.get("GenderID", ""),
            "occupation": complainant.get("Occupation", ""),
        },
        "victims": [
            {"id": v["VictimMasterID"], "name": v.get("VictimName", ""), "age": v.get("AgeYear"), "gender": v.get("GenderID", "")}
            for v in victim_rows
        ],
        "accused": [
            {"id": a["AccusedMasterID"], "name": a.get("AccusedName", ""), "age": a.get("AgeYear"), "gender": a.get("GenderID", "")}
            for a in accused_rows
        ],
    }), 200)


# ---------------------------------------------------------------------
# PUT /update_case_detail
#
# The actual amend-FIR write path. Same RBAC gate as
# get_case_edit_detail (_case_edit_permission — direct holder only).
#
# Victims/accused are synced against what's already stored: entries
# with an existing id are updated in place, entries with no id are new
# rows inserted for this edit, and any previously-stored row whose id
# is no longer present in the payload is deleted — so the UI can just
# submit "the current full list" without the caller having to compute
# a diff itself. Complainant is a single row, always updated in place
# (a case should never end up with zero or multiple complainants).
# ---------------------------------------------------------------------
def update_case_detail(app, request, officer, audit):
    audit["resource_type"] = "case_detail_update"
    body = request.get_json(force=True)

    case_id = body.get("case_master_id")
    if not case_id:
        return make_response(jsonify({"error": "case_master_id is required"}), 400)
    audit["resource_id"] = case_id

    rows = zcql_rows(app, "CaseMaster", f"SELECT ROWID, PolicePersonID, PoliceStationID FROM CaseMaster WHERE CaseMasterID = '{case_id}'")
    if not rows:
        return make_response(jsonify({"error": "Case not found"}), 404)
    case = rows[0]

    can_edit, err = _case_edit_permission(app, officer, case)
    if not can_edit:
        return err

    complainant = body.get("complainant") or {}
    if not complainant.get("name"):
        return make_response(jsonify({"error": "Missing required fields: complainant.name"}), 400)

    victims = [v for v in (body.get("victims") or []) if v.get("name")]
    if not victims:
        return make_response(jsonify({"error": "Missing required fields: victims[].name"}), 400)

    accused = [a for a in (body.get("accused") or []) if a.get("name")]

    crime_subhead_id = body.get("crime_subhead_id")
    if crime_subhead_id:
        subhead = zcql_rows(app, "CrimeSubHead", f"SELECT CrimeHeadID FROM CrimeSubHead WHERE CrimeSubHeadID = '{crime_subhead_id}'")
        if not subhead:
            return make_response(jsonify({"error": "Unknown crime_subhead_id"}), 400)

    case_update = {"ROWID": rows[0]["ROWID"]}
    if crime_subhead_id:
        case_update["CrimeMinorHeadID"] = crime_subhead_id
    if "incident_date" in body:
        case_update["CrimeRegisteredDate"] = body.get("incident_date", "")
        case_update["IncidentFromDate"] = body.get("incident_date", "")
        case_update["IncidentToDate"] = body.get("incident_date", "")
    if "latitude" in body:
        case_update["latitude"] = body.get("latitude", 0)
    if "longitude" in body:
        case_update["longitude"] = body.get("longitude", 0)
    if "brief_facts" in body:
        case_update["BriefFacts"] = body.get("brief_facts", "")

    updated_case = app.datastore().table("CaseMaster").update_row(case_update)

    # Complainant — single row, always update in place.
    comp_id = complainant.get("id")
    comp_fields = {
        "ComplainantName": complainant.get("name"),
        "AgeYear": complainant.get("age") or None,
        "Occupation": complainant.get("occupation", ""),
        "GenderID": complainant.get("gender", ""),
    }
    comp_table = app.datastore().table("ComplainantDetails")
    if comp_id:
        updated_complainant = comp_table.update_row({"ComplainantID": comp_id, **comp_fields}).get("row")
    else:
        ts = int(time.time() * 1000)
        updated_complainant = comp_table.insert_row({"ComplainantID": f"COMP{ts}", "CaseMasterID": case_id, **comp_fields}).get("row")

    def _sync_people(table_name, id_field, name_field, existing_rows, incoming, prefix):
        table = app.datastore().table(table_name)
        existing_ids = {r[id_field] for r in existing_rows}
        incoming_ids = {p["id"] for p in incoming if p.get("id")}

        for stale_id in existing_ids - incoming_ids:
            table.delete_row(stale_id)

        ts = int(time.time() * 1000)
        out_rows = []
        for i, p in enumerate(incoming):
            fields = {name_field: p.get("name"), "AgeYear": p.get("age") or None, "GenderID": p.get("gender", "")}
            if p.get("id"):
                out_rows.append(table.update_row({id_field: p["id"], **fields}).get("row"))
            else:
                new_id = f"{prefix}{ts}{i}"
                extra = {"VictimPolice": 0} if table_name == "Victim" else {"PersonID": f"A{i + 1}", "PersonKey": f"PER{ts}{i}"}
                out_rows.append(table.insert_row({id_field: new_id, "CaseMasterID": case_id, **fields, **extra}).get("row"))
        return out_rows

    existing_victims = zcql_rows(app, "Victim", f"SELECT VictimMasterID FROM Victim WHERE CaseMasterID = '{case_id}'")
    existing_accused = zcql_rows(app, "Accused", f"SELECT AccusedMasterID FROM Accused WHERE CaseMasterID = '{case_id}'")

    updated_victims = _sync_people("Victim", "VictimMasterID", "VictimName", existing_victims, victims, "VIC")
    updated_accused = _sync_people("Accused", "AccusedMasterID", "AccusedName", existing_accused, accused, "ACC")

    return make_response(jsonify({
        "message": "Case updated",
        "case": updated_case.get("row"),
        "complainant": updated_complainant,
        "victims": updated_victims,
        "accused": updated_accused,
    }), 200)


# ---------------------------------------------------------------------
# GET /get_case_detail?case_master_id=
#
# The one place identity-bearing data (Accused, Victim, Complainant)
# is exposed beyond the network graph, so it's also the one place the
# same redaction rules from _redact_accused / _redact_victim above
# have to be applied — every name in the response is either the real
# name or a masked placeholder, never left to the frontend to decide.
#
# RBAC: same case-membership check as update_case_status
# (_case_in_officer_scope) — a case outside the caller's scope 403s
# before any row (redacted or not) is even fetched.
# ---------------------------------------------------------------------
def get_case_detail(app, request, officer, audit):
    audit["resource_type"] = "case_detail"
    case_id = (request.args.get("case_master_id") or "").strip()
    if not case_id:
        return make_response(jsonify({"error": "case_master_id is required"}), 400)

    audit["resource_id"] = case_id

    rows = zcql_rows(app, "CaseMaster", f"SELECT * FROM CaseMaster WHERE CaseMasterID = '{case_id}'")
    if not rows:
        return make_response(jsonify({"error": "Case not found"}), 404)
    case = rows[0]

    in_scope, viewer_has_case_access = _case_in_officer_scope(app, officer, case)
    if not in_scope:
        return make_response(jsonify({"error": "forbidden", "message": "That case is outside your access scope."}), 403)

    crime_head_id = case.get("CrimeMajorHeadID")

    lookups = _lookups(app)
    unit_name = {u["UnitID"]: u["UnitName"] for u in lookups["units"]}
    district_name = {d["DistrictID"]: d["DistrictName"] for d in lookups["districts"]}
    unit_district = {u["UnitID"]: u["DistrictID"] for u in lookups["units"]}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"] for s in lookups["crime_subheads"]}
    status_name = {s["CaseStatusID"]: s["CaseStatusName"] for s in lookups["statuses"]}

    accused_rows = zcql_rows(app, "Accused",
        f"SELECT AccusedMasterID, AccusedName, AgeYear, GenderID, PersonKey FROM Accused WHERE CaseMasterID = '{case_id}'")
    victim_rows = zcql_rows(app, "Victim",
        f"SELECT VictimMasterID, VictimName, AgeYear, GenderID, VictimPolice FROM Victim WHERE CaseMasterID = '{case_id}'")
    complainant_rows = zcql_rows(app, "ComplainantDetails",
        f"SELECT ComplainantID, ComplainantName, AgeYear, Occupation, GenderID FROM ComplainantDetails WHERE CaseMasterID = '{case_id}'")

    accused_out = []
    for a in accused_rows:
        pk = a.get("PersonKey") or a["AccusedMasterID"]
        label, redacted = _redact_accused(pk, a["AccusedName"], a.get("AgeYear"), officer)
        accused_out.append({
            "id": a["AccusedMasterID"], "name": label, "redacted": redacted,
            "age": None if redacted else a.get("AgeYear"), "gender": a.get("GenderID"),
        })

    victim_out = []
    for v in victim_rows:
        pk = v["VictimMasterID"]
        label, redacted = _redact_victim(pk, v["VictimName"], v.get("AgeYear"), crime_head_id, officer, viewer_has_case_access)
        victim_out.append({
            "id": v["VictimMasterID"], "name": label, "redacted": redacted,
            "age": None if redacted else v.get("AgeYear"), "gender": v.get("GenderID"),
        })

    # Complainant isn't necessarily the victim (can be a family member,
    # witness, or the reporting officer) but carries the same real-world
    # identifiability, so it's masked under the exact same rule set.
    complainant_out = []
    for c in complainant_rows:
        pk = c["ComplainantID"]
        label, redacted = _redact_victim(pk, c["ComplainantName"], c.get("AgeYear"), crime_head_id, officer, viewer_has_case_access)
        complainant_out.append({
            "id": c["ComplainantID"], "name": label, "redacted": redacted,
            "age": None if redacted else c.get("AgeYear"),
            "occupation": None if redacted else c.get("Occupation"),
            "gender": c.get("GenderID"),
        })

    audit["record_count"] = len(accused_out) + len(victim_out) + len(complainant_out)

    # Same "direct holder" concept _redact_victim already uses for
    # victim-privacy masking — a DySP/SP overseeing a district can see
    # this case, but only the investigating officer/station AND rank
    # ASI-and-above may amend it (see _case_edit_permission).
    editable = viewer_has_case_access and officer["level"] >= FIR_AMEND_MIN_LEVEL

    return make_response(jsonify({
        "case": {
            "caseMasterId": case["CaseMasterID"],
            "caseNo": case.get("CaseNo"),
            "crimeType": subhead_name.get(case.get("CrimeMinorHeadID"), "Unknown"),
            "station": unit_name.get(case.get("PoliceStationID"), "Unassigned"),
            "district": district_name.get(unit_district.get(case.get("PoliceStationID")), "\u2014"),
            "registeredDate": case.get("CrimeRegisteredDate"),
            "status": status_name.get(case.get("CaseStatusID"), "Unknown"),
            "briefFacts": case.get("BriefFacts", ""),
            "sensitiveCase": crime_head_id in SENSITIVE_CRIME_HEADS,
            "editable": editable,
        },
        "accused": accused_out,
        "victims": victim_out,
        "complainants": complainant_out,
    }), 200)


# ---------------------------------------------------------------------
# GET /get_linked_fir_matches?case_master_id=
#
# Duplicate/Linked FIR Detection: flags other FIRs — same crime head,
# same suspect, or similar narrative wording — that a records clerk
# reading one case at a time would never notice on their own. This is
# the classic "serial offender / same gang, different station" catch.
#
# Method (deliberately simple and auditable, not a black box):
#   - Zia keyphrase-extracts the target case's own BriefFacts once.
#   - For every OTHER case in the officer's own scope with the same
#     CrimeMajorHeadID, score it on three transparent signals:
#       * lexical overlap  — how many of the target's keyphrases show
#         up in the candidate's own narrative text
#       * shared suspect    — any accused PersonKey in common
#       * same crime subhead (minor head), same station
#   - Combine into one score in [0, 1] and return the strongest matches
#     with the reasons attached, so the officer can see WHY something
#     was flagged rather than just trusting a number.
# This is lexical, not semantic — it will miss paraphrased narratives
# and it will not catch anything written in a different language than
# the target case. Treat matches as leads to check, not conclusions.
#
# RBAC: target case must pass the same _case_in_officer_scope check as
# get_case_detail; candidates are pulled through the same
# _case_scope_clause every other route uses, so this can never surface
# a case outside the caller's own access scope.
# ---------------------------------------------------------------------
def _linked_fir_matches(app, officer, case_master_id, limit=5):
    from collections import Counter

    rows = zcql_rows(app, "CaseMaster", f"SELECT * FROM CaseMaster WHERE CaseMasterID = '{case_master_id}'")
    if not rows:
        return None, "not_found"
    target = rows[0]

    in_scope, _ = _case_in_officer_scope(app, officer, target)
    if not in_scope:
        return None, "forbidden"

    target_text = (target.get("BriefFacts") or "").strip()
    target_keyphrases = []
    if target_text:
        try:
            zia = app.zia()
            kw_data = zia.get_keyword_extraction([target_text[:1500]])
            kw = ((kw_data or [{}])[0] or {}).get("keyword_extractor", {}) or {}
            target_keyphrases = [p for p in (kw.get("keyphrases") or kw.get("keywords") or []) if len(p) > 2]
        except Exception as e:
            logging.getLogger().warning(f"Zia keyword extraction unavailable for linked-FIR matching: {e}")

    scope_clause = _case_scope_clause(app, officer)
    case_where = f" WHERE {scope_clause}" if scope_clause else ""
    candidate_rows = zcql_rows_paginated(app, "CaseMaster",
        f"SELECT CaseMasterID, CaseNo, PoliceStationID, CrimeMinorHeadID, CrimeMajorHeadID, "
        f"CrimeRegisteredDate, CaseStatusID, BriefFacts FROM CaseMaster{case_where}")
    candidate_rows = [c for c in candidate_rows
                       if c["CaseMasterID"] != case_master_id
                       and c.get("CrimeMajorHeadID") == target.get("CrimeMajorHeadID")]

    accused_rows = zcql_rows_paginated(app, "Accused", "SELECT CaseMasterID, PersonKey FROM Accused")
    accused_by_case = {}
    for a in accused_rows:
        accused_by_case.setdefault(a["CaseMasterID"], set()).add(a.get("PersonKey"))
    target_persons = accused_by_case.get(case_master_id, set())

    lookups = _lookups(app)
    unit_name = {u["UnitID"]: u["UnitName"] for u in lookups["units"]}
    unit_district = {u["UnitID"]: u["DistrictID"] for u in lookups["units"]}
    district_name = {d["DistrictID"]: d["DistrictName"] for d in lookups["districts"]}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"] for s in lookups["crime_subheads"]}
    status_name = {s["CaseStatusID"]: s["CaseStatusName"] for s in lookups["statuses"]}

    matches = []
    for c in candidate_rows:
        reasons = []
        lexical_score = 0.0
        cand_text = (c.get("BriefFacts") or "").lower()
        if target_keyphrases and cand_text:
            hit_terms = [p for p in target_keyphrases if p.lower() in cand_text]
            if hit_terms:
                lexical_score = len(hit_terms) / len(target_keyphrases)
                reasons.append("Overlapping narrative terms: " + ", ".join(hit_terms[:4]))

        shared_persons = target_persons & accused_by_case.get(c["CaseMasterID"], set())
        shared_persons.discard(None)
        shared_suspect = bool(shared_persons)
        if shared_suspect:
            reasons.append("Names a suspect also accused in this case")

        same_subhead = c.get("CrimeMinorHeadID") == target.get("CrimeMinorHeadID")
        if same_subhead:
            reasons.append("Same crime type")

        same_station = c.get("PoliceStationID") == target.get("PoliceStationID")
        if same_station and not same_subhead:
            reasons.append("Same station")

        score = (0.6 * min(lexical_score, 1.0)) + (0.3 if shared_suspect else 0.0) + (0.1 if same_subhead else 0.0)
        if score < 0.3 and not shared_suspect:
            continue  # not enough signal to surface as a lead

        matches.append({
            "caseMasterId": c["CaseMasterID"],
            "caseNo": c.get("CaseNo"),
            "station": unit_name.get(c.get("PoliceStationID"), "\u2014"),
            "district": district_name.get(unit_district.get(c.get("PoliceStationID")), "\u2014"),
            "crimeType": subhead_name.get(c.get("CrimeMinorHeadID"), "Unknown"),
            "status": status_name.get(c.get("CaseStatusID"), "Unknown"),
            "date": c.get("CrimeRegisteredDate"),
            "score": round(min(score, 1.0), 2),
            "reasons": reasons,
        })

    matches.sort(key=lambda m: -m["score"])
    return matches[:limit], None


def get_linked_fir_matches(app, request, officer, audit):
    case_master_id = (request.args.get("case_master_id") or "").strip()
    audit["resource_type"] = "linked_fir_matches"
    audit["resource_id"] = case_master_id
    if not case_master_id:
        return make_response(jsonify({"error": "case_master_id is required"}), 400)

    matches, err = _linked_fir_matches(app, officer, case_master_id)
    if err == "not_found":
        return make_response(jsonify({"error": "Case not found"}), 404)
    if err == "forbidden":
        return make_response(jsonify({"error": "forbidden", "message": "That case is outside your access scope."}), 403)

    audit["record_count"] = len(matches)
    return make_response(jsonify({"matches": matches}), 200)


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
#     (True for all seven ranks today; kept as a real check for any
#     future read-only/analyst tier).
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
    case_rows = zcql_rows_paginated(app, "CaseMaster", f"SELECT CaseMasterID, PoliceStationID, CrimeMinorHeadID, CrimeMajorHeadID, PolicePersonID FROM CaseMaster{case_where}")
    case_station = {c["CaseMasterID"]: c["PoliceStationID"] for c in case_rows}
    case_crime = {c["CaseMasterID"]: c.get("CrimeMinorHeadID") for c in case_rows}
    case_head = {c["CaseMasterID"]: c.get("CrimeMajorHeadID") for c in case_rows}
    case_officer = {c["CaseMasterID"]: c.get("PolicePersonID") for c in case_rows}
    scoped_case_ids = set(case_station.keys())

    accused_rows = zcql_rows_paginated(app, "Accused", "SELECT AccusedMasterID, CaseMasterID, AccusedName, PersonKey, AgeYear FROM Accused")
    accused_rows = [a for a in accused_rows if a["CaseMasterID"] in scoped_case_ids]

    unit_name = {u["UnitID"]: u["UnitName"] for u in zcql_rows(app, "Unit", "SELECT UnitID, UnitName FROM Unit")}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"]
                     for s in zcql_rows(app, "CrimeSubHead", "SELECT CrimeSubHeadID, CrimeHeadName FROM CrimeSubHead")}

    # Investigating-officer names are personnel data, gated the same way
    # as everywhere else in this file: officer["can_view_pii"]. All seven
    # ranks are operational police ranks, so this is always True today —
    # kept as a real check (not hardcoded) so a future read-only/analyst
    # tier can still suppress it without touching this route.
    show_investigators = officer["can_view_pii"]
    officer_name = {}
    if show_investigators:
        emp_rows = zcql_rows(app, "Employee", "SELECT EmployeeID, FirstName FROM Employee")
        officer_name = {e["EmployeeID"]: e["FirstName"] for e in emp_rows if e.get("FirstName")}

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
        is_sensitive = any(case_head.get(e["caseId"]) in SENSITIVE_CRIME_HEADS for e in entries)
        # A person can appear as a juvenile in one linked case and not in
        # another (age recorded at time of that case) — treat as juvenile
        # if it's true in ANY of their entries, same as before.
        oldest_age = next((e.get("age") for e in entries if _is_juvenile(e.get("age"))), entries[-1].get("age"))
        label, redacted = _redact_accused(pk, entries[-1]["name"], oldest_age, officer)
        investigators = sorted({
            officer_name[case_officer[e["caseId"]]]
            for e in entries
            if case_officer.get(e["caseId"]) and case_officer[e["caseId"]] in officer_name
        }) if show_investigators else []

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
            "investigatingOfficers": investigators,
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
# GET /get_person_profile?person_key=...
#
# Repeat-offender / MO profile: every incident tied to one PersonKey,
# across every jurisdiction, but ONLY within the caller's own case
# scope — same _case_scope_clause as every other case-touching route,
# so a STATION-scope SHO still can't pull a full cross-district trail
# on someone whose other cases sit outside their access.
#
# Redaction mirrors _network_graph exactly:
#   - juvenile -> label always masked (Sec 74 JJ Act), but the
#     incident trail/MO pattern is still shown to the investigating
#     side — the Act protects identity, not case-linkage visibility.
#   - officer.can_view_pii == False (no rank today, reserved for a
#     future read-only/analyst tier) -> label masked AND the
#     case-by-case incident list is suppressed entirely; that tier
#     would still get the aggregate MO pattern, never the trail.
# ---------------------------------------------------------------------
TIME_BANDS = [
    (0, 6, "Late Night (12–6am)"),
    (6, 12, "Morning (6am–12pm)"),
    (12, 17, "Afternoon (12–5pm)"),
    (17, 21, "Evening (5–9pm)"),
    (21, 24, "Night (9pm–12am)"),
]


def _time_band(hour):
    for lo, hi, label in TIME_BANDS:
        if lo <= hour < hi:
            return label
    return "Unknown"


def _person_profile(app, officer, person_key):
    from collections import Counter

    scope_clause = _case_scope_clause(app, officer)
    case_where = f" WHERE {scope_clause}" if scope_clause else ""
    case_rows = zcql_rows_paginated(app, "CaseMaster",
        f"SELECT CaseMasterID, CaseNo, PoliceStationID, CrimeMinorHeadID, CrimeMajorHeadID, "
        f"CaseStatusID, GravityOffenceID, CrimeRegisteredDate, IncidentFromDate FROM CaseMaster{case_where}")
    case_by_id = {c["CaseMasterID"]: c for c in case_rows}

    accused_rows = zcql_rows(app, "Accused",
        f"SELECT AccusedMasterID, CaseMasterID, AccusedName, PersonKey, AgeYear FROM Accused WHERE PersonKey = '{person_key}'")
    entries = [a for a in accused_rows if a["CaseMasterID"] in case_by_id]
    if not entries:
        return None

    lookups = _lookups(app)
    unit_name = {u["UnitID"]: u["UnitName"] for u in lookups["units"]}
    unit_district = {u["UnitID"]: u["DistrictID"] for u in lookups["units"]}
    district_name = {d["DistrictID"]: d["DistrictName"] for d in lookups["districts"]}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"] for s in lookups["crime_subheads"]}
    status_name = {s["CaseStatusID"]: s["CaseStatusName"] for s in lookups["statuses"]}
    gravity_name = {g["GravityOffenceID"]: g.get("LookupValue", g["GravityOffenceID"])
                    for g in zcql_rows(app, "GravityOffence", "SELECT GravityOffenceID, LookupValue FROM GravityOffence")}

    # Same "juvenile in ANY linked case" rule the network graph uses.
    oldest_age = next((e.get("AgeYear") for e in entries if _is_juvenile(e.get("AgeYear"))), entries[-1].get("AgeYear"))
    label, redacted = _redact_accused(person_key, entries[-1]["AccusedName"], oldest_age, officer)
    detail_allowed = officer["can_view_pii"]  # only the no-PII tier loses the incident-level trail

    incidents = []
    crime_counter, band_counter, gravity_counter = Counter(), Counter(), Counter()
    station_set, district_set = set(), set()

    for e in entries:
        case = case_by_id[e["CaseMasterID"]]
        station = case.get("PoliceStationID")
        crime = subhead_name.get(case.get("CrimeMinorHeadID"), "Unknown")
        gravity = gravity_name.get(case.get("GravityOffenceID"), "Unknown")
        station_set.add(station)
        district_set.add(unit_district.get(station))
        crime_counter[crime] += 1
        gravity_counter[gravity] += 1

        hour = None
        ts = case.get("IncidentFromDate")
        if ts and "T" in str(ts):
            try:
                hour = int(str(ts).split("T")[1][:2])
            except ValueError:
                hour = None
        band = _time_band(hour) if hour is not None else "Unknown"
        band_counter[band] += 1

        if detail_allowed:
            incidents.append({
                "caseId": e["CaseMasterID"],
                "caseNo": case.get("CaseNo"),
                "date": case.get("CrimeRegisteredDate"),
                "station": unit_name.get(station, station),
                "district": district_name.get(unit_district.get(station), "—"),
                "crimeType": crime,
                "gravity": gravity,
                "status": status_name.get(case.get("CaseStatusID"), "Unknown"),
                "timeBand": band,
            })

    def top(counter):
        return counter.most_common(1)[0][0] if counter else "—"

    return {
        "personKey": person_key,
        "label": label,
        "redacted": redacted,
        "caseCount": len(entries),
        "repeatOffender": len(entries) >= 2,
        "detailSuppressed": not detail_allowed,
        "jurisdictions": sorted({unit_name.get(s, s) for s in station_set if s}),
        "districts": sorted({district_name.get(d, d) for d in district_set if d}),
        "modusOperandi": {
            "dominantCrimeType": top(crime_counter),
            "crimeTypeBreakdown": [{"label": k, "count": v} for k, v in crime_counter.most_common()],
            "dominantTimeBand": top(band_counter),
            "timeBandBreakdown": [{"label": k, "count": v} for k, v in band_counter.most_common()],
            "dominantGravity": top(gravity_counter),
        },
        "incidents": sorted(incidents, key=lambda r: r.get("date") or "", reverse=True),
    }


def get_person_profile(app, request, officer, audit):
    person_key = (request.args.get("person_key") or "").strip()
    audit["resource_type"] = "person_profile"
    audit["resource_id"] = person_key
    if not person_key:
        return make_response(jsonify({"error": "person_key is required"}), 400)
    profile = _person_profile(app, officer, person_key)
    if profile is None:
        return make_response(jsonify({
            "error": "not_found",
            "message": "No incidents for this person within your access scope.",
        }), 404)
    audit["record_count"] = profile["caseCount"]
    return make_response(jsonify(profile), 200)


# ---------------------------------------------------------------------
# GET /get_investigation_brief?person_key=...
#
# Auto-generated Investigation Brief: a one-page prep sheet for the
# investigating officer before an interrogation, built from the SAME
# scoped data _person_profile already exposes — a suspect's linked
# cases, MO pattern, and (new here) co-accused associates and
# recurring case-narrative terms.
#
# AI layer: same philosophy as ai_assistant — Catalyst Zia Text
# Analytics (Keyword/Keyphrase Extraction + Sentiment Analysis) runs
# ONLY over each linked case's own BriefFacts text (never over Zia's
# imagination), to surface recurring MO terms and the overall tone of
# the narratives. This is a deliberate design choice, not a stopgap:
# an investigation brief is evidence an officer may act on, so every
# sentence in the "summary" is composed deterministically from fields
# already present in `profile`/`associates`/`narrative_keywords`/
# `narrative_tone` — Zia is only ever allowed to EXTRACT signal from
# text that's already in the case record, never to author new prose,
# so the brief can never state a fact that isn't independently
# visible elsewhere in the app. Wiring in a third-party generative
# LLM here would trade that guarantee for fluent-sounding prose that
# could invent details — not a trade worth making for a document used
# in an interrogation room.
#
# RBAC: reuses _person_profile's own scope clause and its
# detailSuppressed gate. An officer whose rank loses the incident-level
# trail (can_view_pii == False — no rank today, reserved for a future
# tier) loses the associate network and narrative excerpts here too,
# for the same reason — aggregate MO pattern only, never a case-by-case
# or identity trail.
# ---------------------------------------------------------------------
def _investigation_brief(app, officer, person_key):
    from collections import Counter

    profile = _person_profile(app, officer, person_key)
    if profile is None:
        return None

    detail_allowed = not profile["detailSuppressed"]

    scope_clause = _case_scope_clause(app, officer)
    case_where = f" WHERE {scope_clause}" if scope_clause else ""
    case_rows = zcql_rows_paginated(app, "CaseMaster",
        f"SELECT CaseMasterID, BriefFacts FROM CaseMaster{case_where}")
    case_by_id = {c["CaseMasterID"]: c for c in case_rows}

    accused_rows = zcql_rows_paginated(app, "Accused",
        "SELECT AccusedMasterID, CaseMasterID, AccusedName, PersonKey, AgeYear FROM Accused")
    accused_rows = [a for a in accused_rows if a["CaseMasterID"] in case_by_id]

    person_case_ids = {a["CaseMasterID"] for a in accused_rows if a["PersonKey"] == person_key}

    # Co-accused: anyone else named on one of this person's own linked
    # cases (within scope) — the "who else was involved" the officer
    # would otherwise have to reconstruct by hand from each FIR.
    associate_counts = Counter()
    associate_latest = {}
    for a in accused_rows:
        if a["PersonKey"] != person_key and a["CaseMasterID"] in person_case_ids:
            associate_counts[a["PersonKey"]] += 1
            associate_latest[a["PersonKey"]] = a

    associate_out = []
    if detail_allowed:
        for pk2, cnt in associate_counts.most_common(6):
            a = associate_latest[pk2]
            label, redacted = _redact_accused(pk2, a["AccusedName"], a.get("AgeYear"), officer)
            associate_out.append({"personKey": pk2, "label": label, "redacted": redacted, "sharedCases": cnt})

    narrative_keywords = []
    narrative_tone = ""
    if detail_allowed:
        texts = [case_by_id[cid].get("BriefFacts") or "" for cid in person_case_ids]
        combined = " ".join(t for t in texts if t).strip()
        if combined:
            doc = [combined[:1500]]  # Zia Text Analytics input cap
            try:
                zia = app.zia()
                kw_data = zia.get_keyword_extraction(doc)
                kw = ((kw_data or [{}])[0] or {}).get("keyword_extractor", {}) or {}
                narrative_keywords = (kw.get("keyphrases") or kw.get("keywords") or [])[:8]
            except Exception as e:
                logging.getLogger().warning(f"Zia keyword extraction unavailable for brief, continuing without it: {e}")
            try:
                zia = app.zia()
                sentiment_data = zia.get_sentiment_analysis(doc)
                narrative_tone = ((sentiment_data or [{}])[0] or {}).get("document_sentiment", "") or ""
            except Exception as e:
                logging.getLogger().warning(f"Zia sentiment analysis unavailable for brief, continuing without it: {e}")

    mo = profile["modusOperandi"]
    lines = [
        f"{profile['label']} has {profile['caseCount']} linked case(s) within your access scope"
        + (f", spanning {', '.join(profile['districts'])}." if profile["districts"] else "."),
        f"Dominant pattern: {mo['dominantCrimeType']}, most often in the {mo['dominantTimeBand'].lower()} "
        f"time band, gravity typically classed as {mo['dominantGravity']}.",
    ]
    if profile["repeatOffender"]:
        lines.append("Flagged as a repeat offender — prior pattern should inform interview strategy.")
    if detail_allowed:
        if associate_out:
            names = ", ".join(
                f"{a['label']} ({a['sharedCases']} shared case{'s' if a['sharedCases'] > 1 else ''})"
                for a in associate_out[:3]
            )
            lines.append(f"Known associates on linked cases: {names}.")
        else:
            lines.append("No co-accused associates found on linked cases within your access scope.")
        if narrative_keywords:
            lines.append("Recurring terms across case narratives: " + ", ".join(narrative_keywords) + ".")
        if narrative_tone:
            lines.append(f"Overall tone of case narratives (Zia sentiment): {narrative_tone.lower()}.")
    else:
        lines.append("Incident-level detail and associate network are outside this role's data scope — aggregate MO pattern only.")

    return {
        "personKey": person_key,
        "label": profile["label"],
        "redacted": profile["redacted"],
        "generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
        "summary": " ".join(lines),
        "caseCount": profile["caseCount"],
        "repeatOffender": profile["repeatOffender"],
        "jurisdictions": profile["jurisdictions"],
        "districts": profile["districts"],
        "modusOperandi": mo,
        "associates": associate_out,
        "narrativeKeywords": narrative_keywords,
        "narrativeTone": narrative_tone,
        "detailSuppressed": profile["detailSuppressed"],
    }


def get_investigation_brief(app, request, officer, audit):
    person_key = (request.args.get("person_key") or "").strip()
    audit["resource_type"] = "investigation_brief"
    audit["resource_id"] = person_key
    if not person_key:
        return make_response(jsonify({"error": "person_key is required"}), 400)
    brief = _investigation_brief(app, officer, person_key)
    if brief is None:
        return make_response(jsonify({
            "error": "not_found",
            "message": "No incidents for this person within your access scope.",
        }), 404)
    audit["record_count"] = brief["caseCount"]
    return make_response(jsonify(brief), 200)


# ---------------------------------------------------------------------
# GET /get_officers?unit_id=&district_id=
# RBAC: rosters are operational (not personal-privacy) data, but "need
# to know" still applies via the usual scope clause — a Constable/ASI/SI
# only sees their own station's roster, a DySP/SP their district's.
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
    employees = zcql_rows_paginated(app, "Employee", f"SELECT * FROM Employee{where_clause}")

    case_rows = zcql_rows_paginated(app, "CaseMaster",
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
    unit_id = (request.args.get("unit_id") or "").strip()
    district_id = (request.args.get("district_id") or "").strip()
    officers = _officers(app, officer, unit_id, district_id)
    audit["record_count"] = len(officers)
    return make_response(jsonify({"officers": officers, "count": len(officers)}), 200)


# ---------------------------------------------------------------------
# GET /get_reports
# RBAC: aggregate counts only (no names), scoped the same way the
# dashboard is — a DySP/SP gets their whole district's breakdown, never
# a name-bearing row.
# ---------------------------------------------------------------------
def _reports(app, officer):
    scope_clause = _case_scope_clause(app, officer)
    where = f" WHERE {scope_clause}" if scope_clause else ""
    cases = zcql_rows_paginated(app, "CaseMaster",
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
# GET /get_ncrb_report?from=YYYY-MM-DD&to=YYYY-MM-DD&format=json|csv
#
# NCRB (National Crime Records Bureau) crime-return export. Unlike
# get_reports (ad-hoc dashboard aggregates), this groups registered
# cases by the classification NCRB actually files against — CrimeHead
# -> CrimeSubHead -> District, broken out by GravityOffence and
# CaseStatus for the requested period.
#
# RBAC: gated by officer["can_export_ncrb"], which RANK_ACCESS already
# sets True for Inspector and above (see the table at the top of this
# file) — this route is what finally makes that flag do something.
# Below Inspector: 403, same pattern as get_audit_log/get_bias_audit.
#
# Scope: reuses _case_scope_clause exactly like every other route — an
# Inspector's export is STATION-scoped, a DySP/SP's is DISTRICT-scoped.
# Nobody exports outside the case data they could otherwise see; this
# route only adds NCRB-shaped grouping on top of the same boundary.
# ---------------------------------------------------------------------
def _build_ncrb_rows(cases, lookups, gravity_lookup):
    head_name = {h["CrimeHeadID"]: h["CrimeGroupName"] for h in lookups["crime_heads"]}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"] for s in lookups["crime_subheads"]}
    unit_district = {u["UnitID"]: u["DistrictID"] for u in lookups["units"]}
    district_name = {d["DistrictID"]: d["DistrictName"] for d in lookups["districts"]}
    status_name = {s["CaseStatusID"]: s["CaseStatusName"] for s in lookups["statuses"]}

    groups = {}
    for c in cases:
        district = district_name.get(unit_district.get(c.get("PoliceStationID")), "Unassigned")
        crime_head = head_name.get(c.get("CrimeMajorHeadID"), "Unclassified")
        crime_subhead = subhead_name.get(c.get("CrimeMinorHeadID"), "Unclassified")
        gravity = gravity_lookup.get(c.get("GravityOffenceID"), "Not recorded")
        status = status_name.get(c.get("CaseStatusID"), "Unknown")

        key = (district, crime_head, crime_subhead, gravity)
        row = groups.setdefault(key, {
            "district": district,
            "crimeHead": crime_head,
            "crimeSubHead": crime_subhead,
            "gravity": gravity,
            "registered": 0,
            "underInvestigation": 0,
            "chargeSheeted": 0,
            "closed": 0,
        })
        row["registered"] += 1
        if status == "Under Investigation":
            row["underInvestigation"] += 1
        elif status == "Charge Sheeted":
            row["chargeSheeted"] += 1
        elif status == "Closed":
            row["closed"] += 1

    rows = list(groups.values())
    rows.sort(key=lambda r: (r["district"], r["crimeHead"], r["crimeSubHead"]))
    return rows


NCRB_CSV_COLUMNS = [
    ("district", "District"),
    ("crimeHead", "Crime Head"),
    ("crimeSubHead", "Crime Sub-Head"),
    ("gravity", "Gravity"),
    ("registered", "Cases Registered"),
    ("underInvestigation", "Under Investigation"),
    ("chargeSheeted", "Charge Sheeted"),
    ("closed", "Closed"),
]


def _ncrb_rows_to_csv(rows, period_from, period_to, officer):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f"NCRB Crime Return — period {period_from or 'ALL'} to {period_to or 'ALL'}"])
    writer.writerow([f"Generated by EmployeeID {officer['employee_id']} — scope {officer['data_scope']}"])
    writer.writerow([])
    writer.writerow([label for _, label in NCRB_CSV_COLUMNS])
    for r in rows:
        writer.writerow([r[key] for key, _ in NCRB_CSV_COLUMNS])
    return buf.getvalue()


def get_ncrb_report(app, request, officer, audit):
    audit["resource_type"] = "ncrb_report"

    if not officer["can_export_ncrb"]:
        return make_response(jsonify({
            "error": "forbidden",
            "message": "NCRB export requires Inspector rank or above.",
        }), 403)

    period_from = (request.args.get("from") or "").strip()
    period_to = (request.args.get("to") or "").strip()
    out_format = (request.args.get("format") or "json").strip().lower()

    scope_clause = _case_scope_clause(app, officer)
    conditions = [scope_clause] if scope_clause else []
    if period_from:
        conditions.append(f"CrimeRegisteredDate >= '{period_from}'")
    if period_to:
        conditions.append(f"CrimeRegisteredDate <= '{period_to}'")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    cases = zcql_rows_paginated(app, "CaseMaster",
        "SELECT CaseMasterID, CrimeRegisteredDate, PoliceStationID, GravityOffenceID, "
        f"CrimeMajorHeadID, CrimeMinorHeadID, CaseStatusID FROM CaseMaster{where}")

    lookups = _lookups(app)
    gravity_rows = zcql_rows(app, "GravityOffence", "SELECT GravityOffenceID, LookupValue FROM GravityOffence")
    gravity_lookup = {g["GravityOffenceID"]: g["LookupValue"] for g in gravity_rows}

    rows = _build_ncrb_rows(cases, lookups, gravity_lookup)
    audit["record_count"] = len(cases)
    audit["resource_id"] = f"{period_from or 'ALL'}:{period_to or 'ALL'}"

    if out_format == "csv":
        csv_text = _ncrb_rows_to_csv(rows, period_from, period_to, officer)
        resp = make_response(csv_text, 200)
        resp.headers["Content-Type"] = "text/csv"
        fname = f"ncrb_report_{period_from or 'all'}_{period_to or 'all'}.csv"
        resp.headers["Content-Disposition"] = f"attachment; filename=\"{fname}\""
        return resp

    return make_response(jsonify({
        "period": {"from": period_from or None, "to": period_to or None},
        "generatedBy": officer["employee_id"],
        "scope": officer["data_scope"],
        "totalCasesRegistered": len(cases),
        "rows": rows,
    }), 200)


# ---------------------------------------------------------------------
# Shared by export_ncrb_return / export_ncrb_return_bundle.
#
# Fetches the SAME scoped/period-filtered CaseMaster rows get_ncrb_report
# already uses, plus Victim and Accused rows for those cases — but ONLY
# AgeYear/GenderID/CaseMasterID from each. VictimName, AccusedName,
# PersonKey and PersonID are never SELECTed here. That's not a redaction
# step applied after the fact; those columns simply never enter this
# function's memory, so there's nothing for a bug downstream in
# ncrb_export.py to accidentally leak. See ncrb_export.py's module
# docstring for the full legal rationale.
#
# Also resolves district_of_case (CaseMasterID -> DistrictName) once,
# since every one of the four standard tables groups by district.
# ---------------------------------------------------------------------
def _ncrb_export_source_data(app, officer, period_from, period_to):
    scope_clause = _case_scope_clause(app, officer)
    conditions = [scope_clause] if scope_clause else []
    if period_from:
        conditions.append(f"CrimeRegisteredDate >= '{period_from}'")
    if period_to:
        conditions.append(f"CrimeRegisteredDate <= '{period_to}'")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

    cases = zcql_rows_paginated(app, "CaseMaster",
        "SELECT CaseMasterID, CrimeRegisteredDate, PoliceStationID, GravityOffenceID, "
        f"CrimeMajorHeadID, CrimeMinorHeadID, CaseStatusID FROM CaseMaster{where}")
    case_ids = {c["CaseMasterID"] for c in cases}

    lookups = _lookups(app)
    head_name = {h["CrimeHeadID"]: h["CrimeGroupName"] for h in lookups["crime_heads"]}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"] for s in lookups["crime_subheads"]}
    unit_district = {u["UnitID"]: u["DistrictID"] for u in lookups["units"]}
    district_name = {d["DistrictID"]: d["DistrictName"] for d in lookups["districts"]}
    status_name = {s["CaseStatusID"]: s["CaseStatusName"] for s in lookups["statuses"]}
    gravity_rows = zcql_rows(app, "GravityOffence", "SELECT GravityOffenceID, LookupValue FROM GravityOffence")
    gravity_name = {g["GravityOffenceID"]: g["LookupValue"] for g in gravity_rows}
    district_of_case = {c["CaseMasterID"]: district_name.get(unit_district.get(c.get("PoliceStationID")), "Unassigned") for c in cases}

    # Deliberately NOT selecting VictimName/AccusedName/PersonKey/PersonID
    # here — see the docstring above.
    from collections import defaultdict
    victim_rows = zcql_rows_paginated(app, "Victim", "SELECT VictimMasterID, CaseMasterID, AgeYear, GenderID FROM Victim")
    victims_by_case = defaultdict(list)
    for v in victim_rows:
        if v["CaseMasterID"] in case_ids:
            victims_by_case[v["CaseMasterID"]].append(v)

    accused_rows = zcql_rows_paginated(app, "Accused", "SELECT AccusedMasterID, CaseMasterID, AgeYear, GenderID FROM Accused")
    accused_by_case = defaultdict(list)
    for a in accused_rows:
        if a["CaseMasterID"] in case_ids:
            accused_by_case[a["CaseMasterID"]].append(a)

    return {
        "cases": cases, "district_of_case": district_of_case,
        "head_name": head_name, "subhead_name": subhead_name,
        "gravity_name": gravity_name, "status_name": status_name,
        "victims_by_case": victims_by_case, "accused_by_case": accused_by_case,
    }


def _build_one_ncrb_table(table_key, src):
    if table_key == "crime_head_district":
        return ncrb_export.build_crime_head_district(
            src["cases"], src["district_of_case"], src["head_name"], src["subhead_name"],
            src["gravity_name"], src["status_name"])
    if table_key == "women":
        return ncrb_export.build_women_statement(
            src["cases"], src["victims_by_case"], src["district_of_case"], src["subhead_name"], src["status_name"])
    if table_key == "children":
        return ncrb_export.build_children_statement(
            src["cases"], src["victims_by_case"], src["district_of_case"], src["head_name"],
            src["subhead_name"], src["status_name"])
    if table_key == "persons_apprehended":
        return ncrb_export.build_persons_apprehended(
            src["cases"], src["accused_by_case"], src["district_of_case"], src["head_name"], src["gravity_name"])
    raise ValueError(f"Unknown NCRB table '{table_key}'")


def _scope_label(officer):
    if officer["is_scrb_analyst"]:
        return "Statewide (SCRB consolidated)"
    return {
        "OWN_CASES": "Own cases",
        "STATION": "Station",
        "DISTRICT": "District",
        "STATE": "Statewide",
    }.get(officer["data_scope"], officer["data_scope"])


# ---------------------------------------------------------------------
# GET /export_ncrb_return?table=crime_head_district|women|children|persons_apprehended
#                         &from=YYYY-MM-DD&to=YYYY-MM-DD&format=csv|json
#
# One standard NCRB-style statement, scoped exactly like get_ncrb_report
# (same officer["can_export_ncrb"] gate, same _case_scope_clause), but
# shaped like the actual published statements — see ncrb_export.py for
# what each of the four tables is and why it's built the way it is.
#
# An SCRB analyst (STATE scope) gets the statewide consolidated version
# of these same four tables through this exact route — no separate
# "SCRB view" pulling different data, so there is nothing to re-key
# between what a district already has and what SCRB exports.
# ---------------------------------------------------------------------
def export_ncrb_return(app, request, officer, audit):
    audit["resource_type"] = "ncrb_standard_export"

    if not officer["can_export_ncrb"]:
        return make_response(jsonify({
            "error": "forbidden",
            "message": "NCRB export requires Inspector rank or above, or SCRB Analyst access.",
        }), 403)

    table_key = (request.args.get("table") or "").strip()
    if table_key not in ncrb_export.TABLES:
        return make_response(jsonify({
            "error": "bad_request",
            "message": f"table must be one of: {', '.join(ncrb_export.TABLES.keys())}",
        }), 400)

    period_from = (request.args.get("from") or "").strip()
    period_to = (request.args.get("to") or "").strip()
    out_format = (request.args.get("format") or "json").strip().lower()

    src = _ncrb_export_source_data(app, officer, period_from, period_to)
    rows = _build_one_ncrb_table(table_key, src)
    audit["record_count"] = len(rows)
    audit["resource_id"] = f"{table_key}:{period_from or 'ALL'}:{period_to or 'ALL'}"

    scope_label = _scope_label(officer)
    officer_label = f"EmployeeID {officer['employee_id']}" if officer["employee_id"] else "SCRB Analyst"

    if out_format == "csv":
        csv_text = ncrb_export.table_to_csv(table_key, rows, period_from, period_to, officer_label, scope_label)
        resp = make_response(csv_text, 200)
        resp.headers["Content-Type"] = "text/csv"
        fname = f"ncrb_{table_key}_{period_from or 'all'}_{period_to or 'all'}.csv"
        resp.headers["Content-Disposition"] = f"attachment; filename=\"{fname}\""
        return resp

    return make_response(jsonify({
        "table": table_key,
        "title": ncrb_export.TABLES[table_key]["title"],
        "period": {"from": period_from or None, "to": period_to or None},
        "scope": scope_label,
        "rows": rows,
    }), 200)


# ---------------------------------------------------------------------
# GET /export_ncrb_return_bundle?from=YYYY-MM-DD&to=YYYY-MM-DD
#
# All four standard statements, zipped, in one download — this is the
# "SCRB doesn't manually re-key data upward" feature end to end: an
# SCRB analyst picks a period and gets every standard statement already
# scoped statewide, already in the standard shape, in one click.
# ---------------------------------------------------------------------
def export_ncrb_return_bundle(app, request, officer, audit):
    audit["resource_type"] = "ncrb_standard_export_bundle"

    if not officer["can_export_ncrb"]:
        return make_response(jsonify({
            "error": "forbidden",
            "message": "NCRB export requires Inspector rank or above, or SCRB Analyst access.",
        }), 403)

    period_from = (request.args.get("from") or "").strip()
    period_to = (request.args.get("to") or "").strip()

    src = _ncrb_export_source_data(app, officer, period_from, period_to)
    built = {key: _build_one_ncrb_table(key, src) for key in ncrb_export.TABLES}
    audit["record_count"] = sum(len(v) for v in built.values())
    audit["resource_id"] = f"bundle:{period_from or 'ALL'}:{period_to or 'ALL'}"

    scope_label = _scope_label(officer)
    officer_label = f"EmployeeID {officer['employee_id']}" if officer["employee_id"] else "SCRB Analyst"
    zip_bytes = ncrb_export.bundle_to_zip_bytes(built, period_from, period_to, officer_label, scope_label)

    resp = make_response(zip_bytes, 200)
    resp.headers["Content-Type"] = "application/zip"
    fname = f"ncrb_return_bundle_{period_from or 'all'}_{period_to or 'all'}.zip"
    resp.headers["Content-Disposition"] = f"attachment; filename=\"{fname}\""
    return resp


# ---------------------------------------------------------------------
# GET /get_hotspots?limit=&window_days=
#
# Replaces the frontend's old MOCK_HOTSPOTS. Aggregates CaseMaster rows
# from the last `window_days` (default 30) into per-station counts,
# using the SAME scope clause as the dashboard stats — a Constable/Head
# Constable only ever sees their own cases bucketed, an ASI/SI/Inspector
# their station, a DySP/SP their district. Nobody sees a station
# outside their own access just because it's "just a forecast panel".
#
# "risk" is a simple relative measure: each station's count as a
# percentage of the busiest station's count in the same scoped window.
# This is deliberately simple (not a real predictive model) but it is
# real, live, scope-correct data — not a hardcoded placeholder.
# ---------------------------------------------------------------------
def _hotspots(app, officer, limit=6, window_days=30):
    from collections import Counter, defaultdict

    scope_clause = _case_scope_clause(app, officer)
    cutoff = (datetime.date.today() - datetime.timedelta(days=window_days)).isoformat()
    conditions = [c for c in [scope_clause, f"CrimeRegisteredDate >= '{cutoff}'"] if c]
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    cases = zcql_rows_paginated(app, "CaseMaster",
        f"SELECT PoliceStationID, CrimeMinorHeadID FROM CaseMaster{where}")

    station_counts = Counter()
    station_types = defaultdict(Counter)
    for c in cases:
        sid = c.get("PoliceStationID")
        if not sid:
            continue
        station_counts[sid] += 1
        subhead = c.get("CrimeMinorHeadID")
        if subhead:
            station_types[sid][subhead] += 1

    if not station_counts:
        return []

    lookups = _lookups(app)
    unit_name = {u["UnitID"]: u["UnitName"] for u in lookups["units"]}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"] for s in lookups["crime_subheads"]}

    max_count = max(station_counts.values())
    hotspots = []
    for sid, count in station_counts.most_common(limit):
        top_subhead_id = station_types[sid].most_common(1)[0][0] if station_types[sid] else None
        hotspots.append({
            "area": unit_name.get(sid, sid),
            "type": subhead_name.get(top_subhead_id, "Mixed"),
            "risk": round((count / max_count) * 100),
            "caseCount": count,
        })
    return hotspots


def get_hotspots(app, request, officer, audit):
    audit["resource_type"] = "hotspot_forecast"
    limit = int(request.args.get("limit") or 6)
    window_days = int(request.args.get("window_days") or 30)
    result = _hotspots(app, officer, limit=limit, window_days=window_days)
    audit["record_count"] = len(result)
    return make_response(jsonify({"hotspots": result}), 200)


# ---------------------------------------------------------------------
# GET /get_trend_alerts?window_weeks=4&limit=8
#
# "Emerging trend" detection: for every (station, crime-category) pair
# inside the caller's own scope, compares THIS week's case count against
# a rolling baseline built from the preceding `window_weeks` complete
# weeks (mean + population std-dev), and flags the pair as an alert
# when the current week is a statistically unusual spike — a simple
# z-score, not a trained model, but genuinely computed from live scoped
# data every time (no hardcoded thresholds pretending to be "AI").
#
# z = (current_week_count - baseline_mean) / baseline_std
#   - baseline_std of 0 (a category that never happened before in this
#     window) is treated specially: any current activity at all is
#     already the signal, so it's flagged with a synthetic high z
#     rather than divided-by-zero or silently skipped.
#   - pairs with too few historical data points to mean anything
#     (fewer than MIN_SAMPLE total incidents across the whole window)
#     are excluded — a single case in an otherwise-empty bucket is
#     noise, not a trend.
# ---------------------------------------------------------------------
MIN_TREND_SAMPLE = 3
TREND_Z_THRESHOLD = 1.5


def _trend_alerts(app, officer, window_weeks=4, limit=8):
    from collections import defaultdict

    scope_clause = _case_scope_clause(app, officer)
    lookback_days = (window_weeks + 1) * 7
    cutoff = (datetime.date.today() - datetime.timedelta(days=lookback_days)).isoformat()
    conditions = [c for c in [scope_clause, f"CrimeRegisteredDate >= '{cutoff}'"] if c]
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    cases = zcql_rows_paginated(app, "CaseMaster",
        f"SELECT PoliceStationID, CrimeMinorHeadID, CrimeRegisteredDate FROM CaseMaster{where}")

    today = datetime.date.today()

    def week_index(date_str):
        # 0 = current (partial) week, 1 = last complete week, 2 = the one
        # before that, etc. — buckets by whole weeks back from today.
        try:
            d = datetime.date.fromisoformat(str(date_str)[:10])
        except ValueError:
            return None
        return (today - d).days // 7

    # buckets[(station, subhead)][week_index] = count
    buckets = defaultdict(lambda: defaultdict(int))
    for c in cases:
        sid, subhead = c.get("PoliceStationID"), c.get("CrimeMinorHeadID")
        if not sid or not subhead:
            continue
        wk = week_index(c.get("CrimeRegisteredDate"))
        if wk is None or wk > window_weeks:
            continue
        buckets[(sid, subhead)][wk] += 1

    lookups = _lookups(app)
    unit_name = {u["UnitID"]: u["UnitName"] for u in lookups["units"]}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"] for s in lookups["crime_subheads"]}

    alerts = []
    for (sid, subhead), by_week in buckets.items():
        current = by_week.get(0, 0)
        history = [by_week.get(w, 0) for w in range(1, window_weeks + 1)]
        total_sample = current + sum(history)
        if total_sample < MIN_TREND_SAMPLE:
            continue

        mean = sum(history) / len(history) if history else 0
        variance = sum((h - mean) ** 2 for h in history) / len(history) if history else 0
        std = variance ** 0.5

        if std == 0:
            if current <= mean:
                continue
            z = 3.0 if current > 0 and mean == 0 else (current - mean)
        else:
            z = (current - mean) / std

        if z < TREND_Z_THRESHOLD or current <= mean:
            continue

        alerts.append({
            "area": unit_name.get(sid, sid),
            "crimeType": subhead_name.get(subhead, "Unknown"),
            "currentCount": current,
            "baselineAvg": round(mean, 1),
            "zScore": round(z, 2),
            "severity": "critical" if z >= 2.5 else "elevated",
        })

    alerts.sort(key=lambda a: -a["zScore"])
    return alerts[:limit]


def get_trend_alerts(app, request, officer, audit):
    audit["resource_type"] = "trend_alerts"
    window_weeks = int(request.args.get("window_weeks") or 4)
    limit = int(request.args.get("limit") or 8)
    result = _trend_alerts(app, officer, window_weeks=window_weeks, limit=limit)
    audit["record_count"] = len(result)
    return make_response(jsonify({"alerts": result}), 200)


# ---------------------------------------------------------------------
# GET /get_incident_log?limit=
#
# Replaces the frontend's old MOCK_LOG. Deliberately built from the
# officer's own SCOPED CaseMaster rows (same _case_scope_clause as
# everything else) rather than the AuditLog table — AuditLog is
# restricted to DISTRICT+ scope for a different reason (reviewing who
# looked at what is itself privileged), but every role that can see the
# Dashboard tab should be able to see a feed of their own recent case
# activity.
# ---------------------------------------------------------------------
def _format_incident_time(value):
    if not value:
        return ""
    date_part, _, time_part = value.partition("T")
    try:
        y, m, d = date_part.split("-")
        month_name = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m) - 1]
        date_label = f"{int(d):02d} {month_name}"
    except Exception:
        date_label = date_part
    time_label = time_part[:5] if time_part else ""
    return f"{date_label}, {time_label}" if time_label else date_label


def _incident_log(app, officer, limit=8):
    scope_clause = _case_scope_clause(app, officer)
    where = f" WHERE {scope_clause}" if scope_clause else ""
    cases = zcql_rows(app, "CaseMaster",
        f"SELECT CaseNo, PoliceStationID, CrimeMinorHeadID, CaseStatusID, "
        f"CrimeRegisteredDate, InfoReceivedPSDate FROM CaseMaster{where} "
        f"ORDER BY CrimeRegisteredDate DESC LIMIT {limit}")

    lookups = _lookups(app)
    unit_name = {u["UnitID"]: u["UnitName"] for u in lookups["units"]}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"] for s in lookups["crime_subheads"]}
    status_name = {s["CaseStatusID"]: s["CaseStatusName"] for s in lookups["statuses"]}

    entries = []
    for c in cases:
        station = unit_name.get(c.get("PoliceStationID"), "Unassigned")
        crime = subhead_name.get(c.get("CrimeMinorHeadID"), "Case")
        status = status_name.get(c.get("CaseStatusID"), "")
        display_time = _format_incident_time(c.get("InfoReceivedPSDate") or c.get("CrimeRegisteredDate"))
        text = f"{crime} reported — {station}"
        if status:
            text += f" ({status})"
        entries.append({"time": display_time, "text": text, "caseNo": c.get("CaseNo")})
    return entries


def get_incident_log(app, request, officer, audit):
    audit["resource_type"] = "incident_log"
    limit = int(request.args.get("limit") or 8)
    result = _incident_log(app, officer, limit=limit)
    audit["record_count"] = len(result)
    return make_response(jsonify({"entries": result}), 200)


# ---------------------------------------------------------------------
# GET /get_crime_trend?days=
#
# Replaces the frontend's old MOCK_TREND. Daily case-registration
# counts for the last `days` days (default 7), scoped the same way as
# the dashboard stats. Always returns one entry per day in the window,
# even if the count is 0, so the chart's x-axis stays a full week.
# ---------------------------------------------------------------------
def _crime_trend(app, officer, days=7):
    scope_clause = _case_scope_clause(app, officer)
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days - 1)
    conditions = [c for c in [scope_clause, f"CrimeRegisteredDate >= '{start.isoformat()}'"] if c]
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    cases = zcql_rows_paginated(app, "CaseMaster", f"SELECT CrimeRegisteredDate FROM CaseMaster{where}")

    counts = {}
    for c in cases:
        d = (c.get("CrimeRegisteredDate") or "")[:10]
        if d:
            counts[d] = counts.get(d, 0) + 1

    trend = []
    for i in range(days):
        d = start + datetime.timedelta(days=i)
        iso = d.isoformat()
        trend.append({"day": d.strftime("%a"), "date": iso, "crimes": counts.get(iso, 0)})
    return trend


def get_crime_trend(app, request, officer, audit):
    audit["resource_type"] = "crime_trend"
    days = int(request.args.get("days") or 7)
    result = _crime_trend(app, officer, days=days)
    audit["record_count"] = len(result)
    return make_response(jsonify({"trend": result}), 200)


# ---------------------------------------------------------------------
# GET /get_bias_audit
#
# Bias/Fairness Auditing Dashboard: the app's "repeat offender" flag
# (used by Network Graph, hotspots, and investigation briefs) is the
# closest thing here to a predictive-policing risk signal, so this
# audits THAT flag for demographic and geographic disparity — not by
# guessing at bias, but by comparing each group's SHARE of flagged
# cases/persons against its share of the underlying case/person
# population. A group flagged in exact proportion to its share of the
# raw data gets a disparity ratio of ~1.0; a ratio far from 1.0 in
# either direction is worth a human look, not an automatic verdict.
#
# Deliberately excludes caste, religion, and any other protected
# category not already collected elsewhere in this schema — this
# audits geography (district) and the two demographic fields the app
# already records (gender, juvenile/adult), nothing broader.
#
# Small-sample guard: any group with fewer than MIN_SAMPLE cases/persons
# is reported but never marked "elevated" — a skewed ratio from 3
# cases is noise, not a finding.
#
# RBAC: same gate as the Audit Log — Superintendent of Police only
# (level >= OVERSIGHT_MIN_LEVEL), since this is an oversight view of
# patterns across cases, not a single-case read. Still uses the same
# _case_scope_clause as every other route underneath, so an SP only
# ever audits their own district, never statewide.
# ---------------------------------------------------------------------
MIN_SAMPLE = 5


def _disparity_rows(total_counter, flagged_counter, total_all, flagged_all, key_label):
    rows = []
    for key, total in total_counter.items():
        flagged = flagged_counter.get(key, 0)
        case_share = total / total_all if total_all else 0
        flag_share = flagged / flagged_all if flagged_all else 0
        ratio = round(flag_share / case_share, 2) if case_share > 0 else None
        elevated = bool(ratio is not None and total >= MIN_SAMPLE and (ratio > 1.3 or ratio < 0.7))
        rows.append({
            key_label: key or "Not recorded",
            "total": total,
            "flagged": flagged,
            "totalSharePct": round(case_share * 100, 1),
            "flaggedSharePct": round(flag_share * 100, 1),
            "disparityRatio": ratio,
            "elevated": elevated,
            "sampleTooSmall": total < MIN_SAMPLE,
        })
    rows.sort(key=lambda r: (-1 if r["elevated"] else 0, -(r["disparityRatio"] or 0)))
    return rows


def _bias_audit(app, officer):
    from collections import defaultdict, Counter

    scope_clause = _case_scope_clause(app, officer)
    case_where = f" WHERE {scope_clause}" if scope_clause else ""
    case_rows = zcql_rows_paginated(app, "CaseMaster", f"SELECT CaseMasterID, PoliceStationID FROM CaseMaster{case_where}")
    scoped_case_ids = {c["CaseMasterID"] for c in case_rows}

    accused_rows = zcql_rows_paginated(app, "Accused",
        "SELECT CaseMasterID, PersonKey, GenderID, AgeYear FROM Accused")
    accused_rows = [a for a in accused_rows if a["CaseMasterID"] in scoped_case_ids]

    persons = defaultdict(list)
    for a in accused_rows:
        persons[a["PersonKey"]].append(a)

    repeat_person_keys = {pk for pk, entries in persons.items()
                           if len({e["CaseMasterID"] for e in entries}) >= 2}

    flagged_case_ids = {e["CaseMasterID"] for pk in repeat_person_keys for e in persons[pk]}

    lookups = _lookups(app)
    unit_district = {u["UnitID"]: u["DistrictID"] for u in lookups["units"]}
    district_name = {d["DistrictID"]: d["DistrictName"] for d in lookups["districts"]}

    district_total, district_flagged = Counter(), Counter()
    for c in case_rows:
        d = district_name.get(unit_district.get(c.get("PoliceStationID")), "Unassigned")
        district_total[d] += 1
        if c["CaseMasterID"] in flagged_case_ids:
            district_flagged[d] += 1

    gender_total, gender_flagged = Counter(), Counter()
    age_total, age_flagged = Counter(), Counter()
    for pk, entries in persons.items():
        latest = entries[-1]
        gender = latest.get("GenderID") or "Not recorded"
        gender_total[gender] += 1
        band = "Juvenile" if _is_juvenile(latest.get("AgeYear")) else (
            "Adult" if latest.get("AgeYear") is not None else "Not recorded")
        age_total[band] += 1
        if pk in repeat_person_keys:
            gender_flagged[gender] += 1
            age_flagged[band] += 1

    total_cases = len(case_rows)
    total_flagged_cases = len(flagged_case_ids)
    total_persons = len(persons)
    total_flagged_persons = len(repeat_person_keys)

    return {
        "generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
        "scope": officer["data_scope"],
        "totals": {
            "cases": total_cases,
            "flaggedCases": total_flagged_cases,
            "persons": total_persons,
            "flaggedPersons": total_flagged_persons,
        },
        "geographic": _disparity_rows(district_total, district_flagged, total_cases, total_flagged_cases, "district"),
        "genderRepresentation": _disparity_rows(gender_total, gender_flagged, total_persons, total_flagged_persons, "group"),
        "ageBandRepresentation": _disparity_rows(age_total, age_flagged, total_persons, total_flagged_persons, "group"),
        "methodologyNote": (
            "Disparity ratio = a group's share of repeat-offender-flagged cases/persons, "
            "divided by that group's share of all cases/persons in scope. 1.0 means the group "
            "is flagged in proportion to its share of the data; further from 1.0 means it is "
            "over- or under-represented among flags relative to its share of raw case volume."
        ),
        "disclaimer": (
            "This measures statistical disparity in how the app's own repeat-offender flag is "
            "distributed, not proof of discriminatory intent or model error — smaller districts and "
            "groups naturally swing further from 1.0 on small samples. Treat 'elevated' rows as a "
            "prompt for human review, not a conclusion."
        ),
    }


def get_bias_audit(app, request, officer, audit):
    audit["resource_type"] = "bias_audit"
    if officer["level"] < OVERSIGHT_MIN_LEVEL:
        return make_response(jsonify({"error": "forbidden", "message": "Bias audit access requires Superintendent of Police level."}), 403)
    result = _bias_audit(app, officer)
    audit["record_count"] = result["totals"]["cases"]
    return make_response(jsonify(result), 200)


# ---------------------------------------------------------------------
# GET /get_audit_log?limit=
# Who can see the audit trail is itself an access-control question:
# only a Superintendent of Police (the senior-most of the seven ranks)
# can review it, scoped to their own district. Everyone else — Inspector
# and DySP included — cannot see the audit trail at all; reading
# who-looked-at-what is itself privileged.
# ---------------------------------------------------------------------
def get_audit_log(app, request, officer, audit):
    audit["resource_type"] = "audit_log"
    if officer["level"] < OVERSIGHT_MIN_LEVEL:
        return make_response(jsonify({"error": "forbidden", "message": "Audit trail access requires Superintendent of Police level."}), 403)

    limit = min(int(request.args.get("limit") or 200), 300)
    conditions = []
    if officer["data_scope"] == "DISTRICT":
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{officer['district_id']}'")
        emp_rows = zcql_rows_paginated(app, "Employee", "SELECT EmployeeID, UnitID FROM Employee")
        emp_ids = [e["EmployeeID"] for e in emp_rows if e.get("UnitID") in {u["UnitID"] for u in unit_rows}]
        if emp_ids:
            in_clause = ", ".join(f"'{e}'" for e in emp_ids)
            conditions.append(f"EmployeeID IN ({in_clause})")
        else:
            return make_response(jsonify({"entries": [], "count": 0}), 200)

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    entries = zcql_rows(app, "AuditLog", f"SELECT * FROM AuditLog{where_clause} ORDER BY Time_stamp DESC LIMIT {limit}")
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

    ist_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S")
    row_data = {
        "Time_stamp": ist_now,
        "EmployeeID": officer.get("employee_id") or "",
        "EmployeeName": officer.get("name") or "",
        "RoleName": officer.get("role_name") or "Unassigned",
        "Action": action,
        "Endpoint": request.path,
        "ResourceType": audit.get("resource_type") or "",
        "ResourceID": str(audit.get("resource_id") or ""),
        "RecordCount": audit.get("record_count") if audit.get("record_count") is not None else -1,
        "QueryParams": query_params,
        "_Result": result,
        "StatusCode": status_code,
    }
    audit["_last_row_data"] = row_data
    app.datastore().table("AuditLog").insert_row(row_data)


# ---------------------------------------------------------------------
# POST /ai_assistant
# RBAC: context is built through the SAME scoped helpers
# (_dashboard_stats, _network_graph, _search_cases) every REST route
# uses, so the assistant can never leak data outside the caller's own
# scope just because it's "just a chat answer."
#
# AI layer: Zoho Catalyst Zia AI — Text Analytics (Sentiment Analysis +
# Keyword/Keyphrase Extraction + Named Entity Recognition), called via
# app.zia() from this same serverless function. This runs entirely
# inside the Catalyst project using the function's own execution
# credentials: no external API key, no third-party LLM vendor, nothing
# to configure in Environment Variables.
#
# Zia only has to UNDERSTAND the analyst's question (sentiment +
# salient keywords/entities). The reply text itself is composed
# deterministically in _compose_zia_reply() from the same scoped data
# context every other route already produces — so the assistant can
# never "hallucinate" a case number or a statistic that isn't really
# in `context`.
# ---------------------------------------------------------------------
ZIA_LOCATION_TAGS = {"City", "State", "Country", "Location"}


def _zia_understand(app, message):
    """
    Runs the analyst's raw question through Catalyst Zia Text Analytics
    (three Zia AI calls: sentiment, keyword extraction, NER). Never
    raises — if Zia Services isn't enabled yet for this project, or a
    call fails for any reason, we degrade gracefully and the assistant
    still answers using the data context alone.
    """
    try:
        zia = app.zia()
        docs = [message[:1500]]  # Zia Text Analytics caps input at 1500 chars

        sentiment_data = zia.get_sentiment_analysis(docs)
        keyword_data = zia.get_keyword_extraction(docs)
        ner_data = zia.get_NER_prediction(docs)

        sentiment = ((sentiment_data or [{}])[0] or {}).get("document_sentiment", "")

        kw = ((keyword_data or [{}])[0] or {}).get("keyword_extractor", {}) or {}
        keywords = kw.get("keywords") or []
        keyphrases = kw.get("keyphrases") or []

        entities = (((ner_data or [{}])[0] or {}).get("ner", {}) or {}).get("general_entities") or []
        location_entities = [e["token"] for e in entities if e.get("ner_tag") in ZIA_LOCATION_TAGS]

        return {
            "sentiment": sentiment,
            "keywords": keywords[:6],
            "keyphrases": keyphrases[:3],
            "location_entities": location_entities,
        }
    except Exception as e:
        logging.getLogger().warning(f"Zia text analytics unavailable, continuing without it: {e}")
        return {"sentiment": "", "keywords": [], "keyphrases": [], "location_entities": []}


def _compose_zia_reply(context, suggested_tab, zia_signals):
    """
    Deterministic natural-language reply, built only from facts already
    present in `context` (never invented), lightly personalized using
    the Zia AI signals (sentiment / keyphrase) so it doesn't read like
    a static template every time.
    """
    if context.get("error"):
        return ("I couldn't pull live data for that just now — try rephrasing, "
                "or check the Dashboard directly."), ({"tab": suggested_tab} if suggested_tab else None)

    lines = []
    filt = [f for f in (context.get("filteredOnDistrict"), context.get("filteredOnCrimeType")) if f]
    scope = " and ".join(filt) if filt else "your current access scope"

    if "recentMatchingCases" in context:
        cases = context["recentMatchingCases"]
        if cases:
            latest = cases[0]
            lines.append(
                f"Found {len(cases)} matching case(s) for {scope}. Most recent is "
                f"{latest.get('caseNo') or 'an unlisted case no.'}, registered "
                f"{latest.get('date') or 'on an unspecified date'}."
            )
        else:
            lines.append(f"No matching cases found for {scope} in the records you can access.")

    if "topRepeatOffenders" in context:
        offenders = context["topRepeatOffenders"]
        if offenders:
            top = offenders[0]
            stations = ", ".join(top.get("stations") or []) or "multiple stations"
            lines.append(
                f"Top repeat pattern: {top['name']}, linked to {top['caseCount']} case(s) "
                f"across {stations}."
            )
        else:
            lines.append("No repeat-offender network stands out in this scope yet.")

    if not lines:
        stats = context.get("dashboardStats") or {}
        if stats:
            highlights = ", ".join(f"{k}: {v}" for k, v in list(stats.items())[:3])
            lines.append(f"Nothing specific matched, but your dashboard currently shows {highlights}.")
        else:
            lines.append(
                "I don't have a live data match for that yet — try naming a district, "
                "a crime type, or a case number."
            )

    if zia_signals.get("sentiment") == "Negative":
        lines.insert(0, "Understood — flagging this as high priority.")

    reply = " ".join(lines)
    action = {"tab": suggested_tab} if suggested_tab else None
    return reply, action


def _detect_district(message, districts, zia_location_entities=None):
    msg_low = message.lower()
    for d in districts:
        if d["DistrictName"].lower() in msg_low:
            return d["DistrictID"], d["DistrictName"]
    # Fallback: Zia AI's NER may catch a district name phrased in a way
    # the plain substring match misses (e.g. mid-sentence punctuation).
    for entity in (zia_location_entities or []):
        entity_low = entity.lower()
        for d in districts:
            if d["DistrictName"].lower() == entity_low:
                return d["DistrictID"], d["DistrictName"]
    return "", ""


def _detect_crime_subhead(message, crime_subheads):
    msg_low = message.lower()
    for s in crime_subheads:
        name = (s.get("CrimeHeadName") or "")
        if name and name.lower() in msg_low:
            return s["CrimeSubHeadID"], name
    return "", ""


# ---------------------------------------------------------------------
# POST /extract_fir_entities   body: {"text": "<free-text FIR narrative>"}
#
# FIR Text Mining: FIRs are still written as free text, so this is the
# single highest-value AI feature — run the narrative through Catalyst
# Zia NER + keyword extraction BEFORE the officer submits the form, and
# hand back suggested structured values (crime type, district) plus the
# raw extracted entities, so the officer can confirm/correct rather
# than re-type. Nothing here writes to the database — it only proposes
# values for the New FIR form fields the officer already controls.
#
# Same no-external-LLM philosophy as ai_assistant/get_investigation_brief:
# Zia only extracts; the suggestion logic (_detect_district /
# _detect_crime_subhead) is the same deterministic lookup match used
# for the chat assistant, so a suggestion can only ever be a value that
# already exists in your lookups, never a hallucinated one.
#
# RBAC: this doesn't touch CaseMaster/Accused/Victim at all — it's pure
# text-in, entities-out on whatever the officer has typed so far — so
# every authenticated officer can use it, same as ai_assistant.
# ---------------------------------------------------------------------
ZIA_PERSON_TAGS = {"Person", "PERSON"}


def _extract_fir_entities(app, officer, text):
    lookups = _lookups(app)
    zia_signals = _zia_understand(app, text)

    # Use existing helper matchers against extracted text/entities
    district_id, district_name = _detect_district(text, lookups["districts"], zia_signals.get("location_entities"))
    subhead_id, subhead_name = _detect_crime_subhead(text, lookups["crime_subheads"])

    persons = []
    locations = zia_signals.get("location_entities", [])

    try:
        zia = app.zia()
        ner_data = zia.get_NER_prediction([text[:1500]])
        entities = (((ner_data or [{}])[0] or {}).get("ner", {}) or {}).get("general_entities") or []
        for e in entities:
            tag = e.get("ner_tag")
            token = e.get("token")
            if tag in ZIA_PERSON_TAGS and token not in persons:
                persons.append(token)
            elif tag in ZIA_LOCATION_TAGS and token not in locations:
                locations.append(token)
    except Exception as e:
        logging.getLogger().warning(f"Zia NER extraction failed: {e}")

    return {
        "persons": persons,
        "locations": locations,
        "keywords": zia_signals.get("keywords", []),
        "keyphrases": zia_signals.get("keyphrases", []),
        "suggestedCrimeSubheadId": subhead_id,
        "suggestedCrimeSubheadName": subhead_name,
        "suggestedDistrictId": district_id,
        "suggestedDistrictName": district_name,
    }


def extract_fir_entities(app, request, officer, audit):
    audit["resource_type"] = "extract_fir_entities"
    body = request.get_json(force=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return make_response(jsonify({"error": "text is required"}), 400)

    res = _extract_fir_entities(app, officer, text)
    return make_response(jsonify(res), 200)


def ai_assistant(app, request, officer, audit):
    audit["resource_type"] = "ai_assistant"
    body = request.get_json(force=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return make_response(jsonify({"error": "message is required"}), 400)

    lookups = _lookups(app)
    zia_signals = _zia_understand(app, message)

    district_id, district_name = _detect_district(message, lookups["districts"], zia_signals.get("location_entities"))
    subhead_id, subhead_name = _detect_crime_subhead(message, lookups["crime_subheads"])

    context = {}
    if district_id or subhead_id:
        cases = _search_cases(app, officer, district_id=district_id, crime_subhead_id=subhead_id, limit=5)
        context["recentMatchingCases"] = [
            {"caseNo": c.get("CaseNo"), "date": c.get("CrimeRegisteredDate")} for c in cases
        ]
        if district_name:
            context["filteredOnDistrict"] = district_name
        if subhead_name:
            context["filteredOnCrimeType"] = subhead_name
    else:
        context["dashboardStats"] = _dashboard_stats(app, officer)

    suggested_tab = "search" if ("recentMatchingCases" in context) else "dashboard"
    reply, action = _compose_zia_reply(context, suggested_tab, zia_signals)

    return make_response(jsonify({"reply": reply, "action": action, "context": context}), 200)