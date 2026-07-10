import json
import logging
import zcatalyst_sdk
from flask import Request, make_response, jsonify


def handler(request: Request):
    """
    Single entry point for this Advanced I/O function.
    Catalyst routes every call here; we dispatch manually by path + method.
    """
    logger = logging.getLogger()
    try:
        app = zcatalyst_sdk.initialize()

        if request.path == "/get_current_officer" and request.method == "GET":
            return get_current_officer(app)

        elif request.path == "/get_dashboard_stats" and request.method == "GET":
            return get_dashboard_stats(app)

        elif request.path == "/search_fir" and request.method == "GET":
            return search_fir(app, request)

        elif request.path == "/add_fir" and request.method == "POST":
            return add_fir(app, request)

        elif request.path == "/update_fir_status" and request.method == "PUT":
            return update_fir_status(app, request)

        else:
            response = make_response(jsonify({"error": "Unknown route"}), 404)
            return response

    except Exception as err:
        logger.error(f"Exception in ksp_intelli_q_function: {err}")
        return make_response(jsonify({"error": "Internal server error occurred."}), 500)


# ---------------------------------------------------------------------
# GET /get_current_officer
# Looks up the logged-in user's officer record by their Catalyst zuid
# ---------------------------------------------------------------------
def get_current_officer(app):
    user = app.user_management().get_current_user()
    zuid = user.get("user_id") or user.get("zuid")

    query = f"SELECT * FROM Officers WHERE zuid = '{zuid}'"
    rows = app.zcql().execute_query(query)

    if not rows:
        return make_response(jsonify({"error": "Officer record not found"}), 404)

    return make_response(jsonify({"officer": rows[0]["Officers"]}), 200)


# ---------------------------------------------------------------------
# GET /get_dashboard_stats
# ---------------------------------------------------------------------
def get_dashboard_stats(app):
    zcql = app.zcql()

    total = zcql.execute_query("SELECT COUNT(fir_id) FROM FIRs")
    open_firs = zcql.execute_query("SELECT COUNT(fir_id) FROM FIRs WHERE status = 'Open'")
    solved = zcql.execute_query("SELECT COUNT(fir_id) FROM FIRs WHERE status = 'Closed'")
    active = zcql.execute_query("SELECT COUNT(fir_id) FROM FIRs WHERE status = 'Under Investigation'")

    stats = {
        "totalCrimes": total[0]["FIRs"]["fir_id"] if total else 0,
        "openFirs": open_firs[0]["FIRs"]["fir_id"] if open_firs else 0,
        "solved": solved[0]["FIRs"]["fir_id"] if solved else 0,
        "activeInvestigations": active[0]["FIRs"]["fir_id"] if active else 0,
    }
    return make_response(jsonify(stats), 200)


# ---------------------------------------------------------------------
# GET /search_fir?crime_type=&district=&status=
# ---------------------------------------------------------------------
def search_fir(app, request):
    crime_type = (request.args.get("crime_type") or "").strip()
    district = (request.args.get("district") or "").strip()
    status = (request.args.get("status") or "").strip()

    conditions = []
    if crime_type:
        conditions.append(f"crime_type = '{crime_type}'")
    if district:
        conditions.append(f"district = '{district}'")
    if status:
        conditions.append(f"status = '{status}'")

    where_clause = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"SELECT * FROM FIRs{where_clause} ORDER BY date_reported DESC LIMIT 50"

    rows = app.zcql().execute_query(query)
    results = [row["FIRs"] for row in rows]

    return make_response(jsonify({"results": results, "count": len(results)}), 200)


# ---------------------------------------------------------------------
# POST /add_fir
# Body: { crime_type, location, district, status, investigating_officer, description }
# ---------------------------------------------------------------------
def add_fir(app, request):
    body = request.get_json(force=True)

    required = ["crime_type", "location", "district"]
    missing = [f for f in required if not body.get(f)]
    if missing:
        return make_response(jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400)

    row_data = {
        "crime_type": body.get("crime_type"),
        "location": body.get("location"),
        "district": body.get("district"),
        "status": body.get("status", "Open"),
        "investigating_officer": body.get("investigating_officer", ""),
        "description": body.get("description", ""),
    }

    table = app.datastore().table("FIRs")
    inserted = table.insert_row(row_data)

    return make_response(jsonify({"message": "FIR created", "fir": inserted.get("row")}), 201)


# ---------------------------------------------------------------------
# PUT /update_fir_status
# Body: { fir_id, status }
# ---------------------------------------------------------------------
def update_fir_status(app, request):
    body = request.get_json(force=True)

    fir_id = body.get("fir_id")
    new_status = body.get("status")
    if not fir_id or not new_status:
        return make_response(jsonify({"error": "fir_id and status are required"}), 400)

    table = app.datastore().table("FIRs")
    updated = table.update_row({"ROWID": fir_id, "status": new_status})

    return make_response(jsonify({"message": "FIR updated", "fir": updated.get("row")}), 200)