import logging
import os
import json
import time
import requests  # type: ignore
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
            ("/get_network_graph", "GET"): get_network_graph,
            ("/ai_assistant", "POST"): ai_assistant,
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
    except Exception as e:
        return make_response(jsonify({"error": "not_authenticated", "detail": str(e)}), 401)

    raw_user_id = user.get("user_id")
    raw_zuid = user.get("zuid")
    zuid = str(raw_user_id or raw_zuid or "")
    rows = zcql_rows(app, "Employee", f"SELECT * FROM Employee WHERE zuid = '{zuid}'")

    if not rows:
        return make_response(jsonify({
            "error": "not_provisioned",
            "message": "This login isn't linked to an officer profile yet. Contact your administrator.",
            # TEMP DEBUG — remove once the mismatch is found:
            "debug_zuid_used": zuid,
            "debug_user_id_field": raw_user_id,
            "debug_zuid_field": raw_zuid,
            "debug_user_keys": list(user.keys()) if hasattr(user, "keys") else str(type(user)),
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


def get_lookups(app, request):
    return make_response(jsonify(_lookups(app)), 200)


# ---------------------------------------------------------------------
# GET /get_dashboard_stats
# ---------------------------------------------------------------------
def _dashboard_stats(app):
    zcql = app.zcql()

    def count(where=""):
        q = f"SELECT COUNT(CaseMasterID) FROM CaseMaster{(' WHERE ' + where) if where else ''}"
        rows = zcql.execute_query(q)
        return rows[0]["CaseMaster"]["CaseMasterID"] if rows else 0

    return {
        "totalCrimes": count(),
        "openFirs": count("CaseStatusID = 'STA1'"),
        "solved": count("CaseStatusID = 'STA3'"),
        "activeInvestigations": count("CaseStatusID = 'STA1' OR CaseStatusID = 'STA2'"),
    }


def get_dashboard_stats(app, request):
    return make_response(jsonify(_dashboard_stats(app)), 200)


# ---------------------------------------------------------------------
# GET /search_case?district_id=&crime_subhead_id=&status_id=&limit=
# All filters optional. Filter by IDs (from /get_lookups), not names.
# ---------------------------------------------------------------------
def _search_cases(app, district_id="", crime_subhead_id="", status_id="", limit=50):
    conditions = []

    # District filter goes through Unit, since CaseMaster only stores PoliceStationID
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


def search_case(app, request):
    district_id = (request.args.get("district_id") or "").strip()
    crime_subhead_id = (request.args.get("crime_subhead_id") or "").strip()
    status_id = (request.args.get("status_id") or "").strip()
    limit = int(request.args.get("limit") or 50)

    cases = _search_cases(app, district_id, crime_subhead_id, status_id, limit)
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


# ---------------------------------------------------------------------
# GET /get_network_graph?district_id=&min_cases=&limit=
#
# Builds the criminal link-analysis graph: nodes are accused persons
# (grouped by the real-identity PersonKey, since one person can have
# several AccusedMasterID rows across cases/aliases) and police
# stations; edges are:
#   - "co-accused"  — two persons named on the same CaseMasterID
#   - "location"    — a person's case tied to a PoliceStationID
#
# min_cases filters which persons count as the graph's "focus" set
# (default 1 = everyone). We always pull in anyone co-accused with a
# focus person too, so a single repeat offender's whole ring shows up
# even if their associates only appear once. limit caps how many focus
# persons we keep (ranked by case count) so the frontend force layout
# stays smooth.
# ---------------------------------------------------------------------
def _network_graph(app, district_id="", min_cases=1, limit_persons=120):
    from collections import defaultdict

    case_rows = zcql_rows(app, "CaseMaster", "SELECT CaseMasterID, PoliceStationID, CrimeMinorHeadID FROM CaseMaster")
    case_station = {c["CaseMasterID"]: c["PoliceStationID"] for c in case_rows}
    case_crime = {c["CaseMasterID"]: c.get("CrimeMinorHeadID") for c in case_rows}

    accused_rows = zcql_rows(app, "Accused", "SELECT AccusedMasterID, CaseMasterID, AccusedName, PersonKey FROM Accused")

    unit_name = {u["UnitID"]: u["UnitName"] for u in zcql_rows(app, "Unit", "SELECT UnitID, UnitName FROM Unit")}
    subhead_name = {s["CrimeSubHeadID"]: s["CrimeHeadName"]
                     for s in zcql_rows(app, "CrimeSubHead", "SELECT CrimeSubHeadID, CrimeHeadName FROM CrimeSubHead")}

    # Build every person's FULL case history first, across all districts —
    # repeat-offender status and MO are properties of the person, not of
    # whichever district happens to be filtered.
    persons = defaultdict(list)          # PersonKey -> [{caseId, name}, ...]  (all districts)
    cases_to_persons = defaultdict(list) # CaseMasterID -> [PersonKey, ...]

    for a in accused_rows:
        pk = a["PersonKey"]
        persons[pk].append({"caseId": a["CaseMasterID"], "name": a["AccusedName"]})
        cases_to_persons[a["CaseMasterID"]].append(pk)

    # district_id only decides WHO is relevant (anyone with >=1 case there),
    # never how many of their cases "count" toward being a repeat offender.
    district_case_ids = None
    if district_id:
        unit_rows = zcql_rows(app, "Unit", f"SELECT UnitID FROM Unit WHERE DistrictID = '{district_id}'")
        unit_ids = {u["UnitID"] for u in unit_rows}
        district_case_ids = {cid for cid, station in case_station.items() if station in unit_ids}

    def touches_district(pk):
        return district_case_ids is None or any(e["caseId"] in district_case_ids for e in persons[pk])

    focus = [pk for pk, cs in persons.items() if len(cs) >= min_cases and touches_district(pk)]
    focus.sort(key=lambda pk: -len(persons[pk]))
    focus_set = set(focus[:limit_persons])

    # Pull in co-accused too, but only via cases relevant to the district
    # filter (if any) — keeps the graph visually focused on that district's
    # incidents while each node still reports the person's true full record.
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
        nodes.append({
            "id": pk,
            "type": "person",
            "label": entries[-1]["name"],
            "caseCount": len(entries),
            "repeatOffender": len(entries) >= 2,
            "stations": sorted({unit_name.get(s, s) for s in stations if s}),
            "crimeTypes": sorted({c for c in crimes if c}),
        })

    location_ids = {case_station[e["caseId"]] for pk in included for e in persons[pk] if case_station.get(e["caseId"])}
    for loc in location_ids:
        nodes.append({"id": f"loc::{loc}", "type": "location", "label": unit_name.get(loc, loc)})

    edge_weight = defaultdict(int)  # (nodeA, nodeB, type) -> weight, nodeA < nodeB
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


def get_network_graph(app, request):
    district_id = (request.args.get("district_id") or "").strip()
    min_cases = int(request.args.get("min_cases") or 1)
    limit_persons = int(request.args.get("limit") or 120)
    return make_response(jsonify(_network_graph(app, district_id, min_cases, limit_persons)), 200)


# ---------------------------------------------------------------------
# POST /ai_assistant
# Body: { message: str, history: [{role: "user"|"model", text: str}, ...] }
#
# A retrieval-then-generate assistant, not a free-floating chatbot:
#   1. We scan the message for a known district name / crime type / an
#      intent (network, hotspot, case-status) and pull the matching
#      LIVE rows using the same helpers the REST routes use above.
#   2. That real data — never invented — is handed to Gemini as
#      grounding context, along with an instruction to answer only
#      from it and say so plainly if the context doesn't cover the
#      question.
#   3. The model may end its answer with a hidden ACTION line telling
#      the frontend which tab/filter to jump to; we strip that out of
#      the displayed reply and return it separately as `action`.
#
# Requires a GEMINI_API_KEY environment variable set on this function
# (Catalyst Console > Functions > ksp_intelli_q_function > Environment
# Variables). Without it, this still responds — just without live AI —
# so the UI never hard-fails.
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


def _build_assistant_context(app, message):
    msg_low = message.lower()
    lookups = _lookups(app)
    district_id, district_name = _detect_district(message, lookups["districts"])
    subhead_id, subhead_name = _detect_crime_subhead(message, lookups["crime_subheads"])

    context = {"dashboardStats": _dashboard_stats(app)}
    suggested_tab = None

    network_kw = ["network", "repeat offender", "connection", "linked", "gang", "associat", "co-accused", "coaccused", "ring"]
    map_kw = ["hotspot", "map", "cluster", "where"]

    if any(k in msg_low for k in network_kw) or district_id:
        graph = _network_graph(app, district_id=district_id, min_cases=2, limit_persons=8)
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
        cases = _search_cases(app, district_id=district_id, crime_subhead_id=subhead_id, limit=5)
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


def ai_assistant(app, request):
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
        context, suggested_tab = _build_assistant_context(app, message)
    except Exception as e:
        context, suggested_tab = {"error": f"context lookup failed: {e}"}, None

    # Gemini has no "system" role inside contents — the system prompt goes in
    # its own top-level systemInstruction field instead. Roles here are
    # "user" / "model" (Groq/OpenAI used "user" / "assistant").
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
            time.sleep(1.5 * (attempt + 1))  # brief backoff, then retry once/twice more
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