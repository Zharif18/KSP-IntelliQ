import logging
from flask import Request, make_response, jsonify # type: ignore
import zcatalyst_sdk #type: ignore


def handler(request: Request):
    """
    Single entry point for this Advanced I/O function.
    Catalyst routes every call here; we dispatch manually by path + method.
    """
    logger = logging.getLogger()
    try:
        app = zcatalyst_sdk.initialize()

        routes = {
            ("/get_current_officer", "GET"): get_current_officer,
            ("/get_lookups", "GET"): get_lookups,
            ("/get_dashboard_stats", "GET"): get_dashboard_stats,
            ("/search_case", "GET"): search_case,
            ("/add_case", "POST"): add_case,
            ("/update_case_status", "PUT"): update_case_status,
        }

        route_fn = routes.get((request.path, request.method))
        if not route_fn:
            return make_response(jsonify({"error": "Unknown route"}), 404)

        return route_fn(app, request)

    except Exception as err:
        logger.error(f"Exception in ksp_intelli_q_function: {err}")
        # Surfacing the real detail so errors are debuggable from the browser
        # console / network tab without needing to check Catalyst's Logs tab
        # every time. If this ever ships beyond a hackathon demo, drop
        # "detail" and log-only instead — it can leak internal info.
        return make_response(jsonify({"error": "Internal server error occurred.", "detail": str(err)}), 500)


# ---------------------------------------------------------------------
# Small helper: run a ZCQL query and return just the row dicts for the
# named table (ZCQL wraps each row as {"TableName": {...columns...}})
# ---------------------------------------------------------------------
def zcql_rows(app, table_name, query):
    rows = app.zcql().execute_query(query)
    return [r[table_name] for r in rows]


# ---------------------------------------------------------------------
# GET /get_current_officer
# Looks up the logged-in user's Employee record by their Catalyst zuid.
# Requires a real signed-in Catalyst session (see LoginPage.jsx).
#
# There is no self-service linking here on purpose: an admin sets the
# zuid column on the matching Employee row manually, in Console > Data
# Store > Employee, once they've created that person's login in
# Console > Authentication > Users. Until that's done, this account
# can sign in but has no officer profile — access is denied cleanly.
# ---------------------------------------------------------------------
def get_current_officer(app, request):
    try:
        user = app.user_management().get_current_user()
    except Exception:
        return make_response(jsonify({"error": "not_authenticated"}), 401)

    zuid = str(user.get("user_id") or user.get("zuid") or "")
    rows = zcql_rows(app, "Employee", f"SELECT * FROM Employee WHERE zuid = '{zuid}'")

    if not rows:
        return make_response(jsonify({
            "error": "not_provisioned",
            "message": "This login isn't linked to an officer profile yet. Contact your administrator.",
        }), 403)

    emp = rows[0]
    rank = zcql_rows(app, "Rank", f"SELECT RankName FROM Rank WHERE RankID = '{emp['RankID']}'")
    unit = zcql_rows(app, "Unit", f"SELECT UnitName FROM Unit WHERE UnitID = '{emp['UnitID']}'")

    officer = {
        "employee_id": emp["EmployeeID"],
        "name": emp["FirstName"],
        "role": rank[0]["RankName"] if rank else "Officer",
        "station": unit[0]["UnitName"] if unit else "Unassigned",
        "badge": emp["KGID"],
    }
    return make_response(jsonify({"officer": officer}), 200)


# ---------------------------------------------------------------------
# GET /get_lookups
# Returns every lookup table in one payload so the frontend can build
# dropdowns and join display names client-side, instead of resolving
# names to IDs on every search request.
# ---------------------------------------------------------------------
def get_lookups(app, request):
    districts = zcql_rows(app, "District", "SELECT DistrictID, DistrictName FROM District WHERE Active = 1")
    units = zcql_rows(app, "Unit", "SELECT UnitID, UnitName, DistrictID FROM Unit WHERE Active = 1")
    crime_heads = zcql_rows(app, "CrimeHead", "SELECT CrimeHeadID, CrimeGroupName FROM CrimeHead WHERE Active = 1")
    crime_subheads = zcql_rows(app, "CrimeSubHead", "SELECT CrimeSubHeadID, CrimeHeadID, CrimeHeadName FROM CrimeSubHead")
    statuses = zcql_rows(app, "CaseStatusMaster", "SELECT CaseStatusID, CaseStatusName FROM CaseStatusMaster")

    return make_response(jsonify({
        "districts": districts,
        "units": units,
        "crime_heads": crime_heads,
        "crime_subheads": crime_subheads,
        "statuses": statuses,
    }), 200)


# ---------------------------------------------------------------------
# GET /get_dashboard_stats
# ---------------------------------------------------------------------
def get_dashboard_stats(app, request):
    zcql = app.zcql()

    def count(where=""):
        q = f"SELECT COUNT(CaseMasterID) FROM CaseMaster{(' WHERE ' + where) if where else ''}"
        rows = zcql.execute_query(q)
        return rows[0]["CaseMaster"]["CaseMasterID"] if rows else 0

    stats = {
        "totalCrimes": count(),
        "openFirs": count("CaseStatusID = 'STA1'"),
        "solved": count("CaseStatusID = 'STA3'"),
        "activeInvestigations": count("CaseStatusID = 'STA1' OR CaseStatusID = 'STA2'"),
    }
    return make_response(jsonify(stats), 200)


# ---------------------------------------------------------------------
# GET /search_case?district_id=&crime_subhead_id=&status_id=&limit=
# All filters optional. Filter by IDs (from /get_lookups), not names.
# ---------------------------------------------------------------------
def search_case(app, request):
    district_id = (request.args.get("district_id") or "").strip()
    crime_subhead_id = (request.args.get("crime_subhead_id") or "").strip()
    status_id = (request.args.get("status_id") or "").strip()
    limit = int(request.args.get("limit") or 50)

    conditions = []

    # District filter goes through Unit, since CaseMaster only stores PoliceStationID
    if district_id:
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{district_id}'")
        unit_ids = [u["UnitID"] for u in unit_rows]
        if not unit_ids:
            return make_response(jsonify({"results": [], "count": 0}), 200)
        in_clause = ", ".join(f"'{u}'" for u in unit_ids)
        conditions.append(f"PoliceStationID IN ({in_clause})")

    if crime_subhead_id:
        conditions.append(f"CrimeMinorHeadID = '{crime_subhead_id}'")
    if status_id:
        conditions.append(f"CaseStatusID = '{status_id}'")

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM CaseMaster{where_clause} ORDER BY CrimeRegisteredDate DESC LIMIT {limit}"
    cases = zcql_rows(app, "CaseMaster", query)

    return make_response(jsonify({"results": cases, "count": len(cases)}), 200)


# ---------------------------------------------------------------------
# POST /add_case
# Body: { crime_subhead_id, police_station_id, incident_date, latitude,
#         longitude, brief_facts, police_person_id }
# ---------------------------------------------------------------------
def add_case(app, request):
    body = request.get_json(force=True)

    required = ["crime_subhead_id", "police_station_id", "police_person_id"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return make_response(jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400)

    # Resolve CrimeHeadID from the sub-head, and gravity from that head
    subhead = zcql_rows(app, "CrimeSubHead",
        f"SELECT CrimeHeadID FROM CrimeSubHead WHERE CrimeSubHeadID = '{body['crime_subhead_id']}'")
    if not subhead:
        return make_response(jsonify({"error": "Unknown crime_subhead_id"}), 400)
    crime_head_id = subhead[0]["CrimeHeadID"]
    gravity_id = "GRV1" if crime_head_id in ("CH01", "CH03") else "GRV2"

    # Simple readable ID — in production, fetch the current max and increment instead
    import time
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

    return make_response(jsonify({"message": "Case created", "case": inserted.get("row")}), 201)


# ---------------------------------------------------------------------
# PUT /update_case_status
# Body: { case_master_id, status_id }
# ---------------------------------------------------------------------
def update_case_status(app, request):
    body = request.get_json(force=True)

    case_id = body.get("case_master_id")
    status_id = body.get("status_id")
    if not case_id or not status_id:
        return make_response(jsonify({"error": "case_master_id and status_id are required"}), 400)

    rows = zcql_rows(app, "CaseMaster", f"SELECT ROWID FROM CaseMaster WHERE CaseMasterID = '{case_id}'")
    if not rows:
        return make_response(jsonify({"error": "Case not found"}), 404)

    table = app.datastore().table("CaseMaster")
    updated = table.update_row({"ROWID": rows[0]["ROWID"], "CaseStatusID": status_id})

    return make_response(jsonify({"message": "Case updated", "case": updated.get("row")}), 200)