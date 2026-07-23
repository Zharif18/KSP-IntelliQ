"""
NCRB-compliant crime-return export module.

WHAT THIS IS
------------
NCRB (National Crime Records Bureau) publishes crime data every year in a
fixed set of statutory statement formats — the same "Crime in India"
annexures every state's SCRB (State Crime Records Bureau) already has to
produce: a district x crime-head incidence table, a Crime Against Women
statement, a Crime Against Children statement, and a persons-apprehended
statement. Today an SCRB analyst gets there by pulling station/district
returns and manually re-keying them into those shapes by hand.

This module builds those same shapes directly from CaseMaster/Victim/
Accused rows that the caller (main.py) has ALREADY fetched and scoped —
so a district Inspector/DySP/SP gets their own district's version of the
same standard forms (to check before they file upward), and an SCRB
analyst (STATE scope) gets the statewide consolidated version, with zero
manual re-keying in between: same code path, wider scope.

WHAT THIS IS NOT
----------------
This is NOT a certified CCTNS/NCRB submission channel — actual filing to
NCRB happens through CCTNS's own integration. This module exists to stop
manual re-keying of numbers that already exist in this system, in the
standard shapes NCRB publishes, so a human isn't retyping totals from one
screen into another spreadsheet upward.

LEGAL DESIGN, NOT AN AFTERTHOUGHT
----------------------------------
Every function in this file is aggregate-only by construction:
  - No function here accepts a name, PersonKey, or any other person
    identifier as input in the first place — main.py deliberately never
    SELECTs VictimName/AccusedName/PersonKey/PersonID for these routes,
    so there is no name to leak even by a future bug in this file. Only
    AgeYear/GenderID/CaseMasterID ever cross into this module.
  - Juvenile figures (victims AND accused under 18) are reported only as
    banded counts, never as individual rows — the JJ Act, 2015 Sec 74
    bar on disclosing anything that could identify a child (in conflict
    with law, in need of care, or a child victim/witness) applies just
    as much to a statistical return as to a newspaper report; an export
    of exactly one 16-year-old in a small taluk is still identifying.
  - Crime Against Women rows are counted the same way — Sec 72 BNS
    (Bharatiya Nyaya Sanhita, 2023; successor to IPC Sec 228A) bars
    disclosing a sexual-offence victim's identity, so this file never
    receives one to disclose.
  - Every builder groups by (district, offence classification, age
    band / disposal stage) and returns COUNTS. If a bucket would
    contain a small number of victims/accused in a real deployment,
    that is a k-anonymity question for whoever consumes the export, not
    something this module can decide — flagged in each table's CSV
    banner via SMALL_CELL_NOTE so it isn't a silent gap.
"""

import csv
import io
import zipfile
import datetime


SMALL_CELL_NOTE = (
    "Note: small counts in low-population districts/categories may still "
    "be identifying in combination with other public information. "
    "Apply your department's cell-suppression policy before wider release."
)


# ---------------------------------------------------------------------
# Age banding — shared by the women/children/persons-apprehended tables.
# ---------------------------------------------------------------------
def _child_age_band(age_year):
    """
    JJ Act, 2015 age bands (0-11 / 12-15 / 16-18), used ONLY inside the
    Crime Against Children statement. Returns None for anyone recorded
    as 18 or older, or with no age on file (excluded from that table).
    """
    try:
        age = int(age_year)
    except (TypeError, ValueError):
        return None
    if age < 0 or age >= 18:
        return None
    if age <= 11:
        return "0-11"
    if age <= 15:
        return "12-15"
    return "16-18"


def _juvenile_or_adult(age_year):
    try:
        age = int(age_year)
    except (TypeError, ValueError):
        return "Age not recorded"
    return "Juvenile (<18)" if age < 18 else "Adult (18+)"


GENDER_LABEL = {"M": "Male", "F": "Female"}


def _gender_label(gender_id):
    return GENDER_LABEL.get((gender_id or "").strip().upper(), "Other / not recorded")


# ---------------------------------------------------------------------
# Shared disposal-stage bucketing — same CaseStatusMaster names used
# throughout main.py (STA1..STA4 -> these labels via the statuses
# lookup main.py already builds in _lookups).
# ---------------------------------------------------------------------
def _bump_disposal(row, status_name):
    if status_name == "Under Investigation":
        row["underInvestigation"] += 1
    elif status_name == "Charge Sheeted":
        row["chargeSheeted"] += 1
    elif status_name == "Closed":
        row["closed"] += 1
    elif status_name == "Undetected":
        row["undetected"] += 1


def _blank_disposal():
    return {"underInvestigation": 0, "chargeSheeted": 0, "closed": 0, "undetected": 0}


# =======================================================================
# TABLE A — District x Crime-Head incidence (the core "Table 1"-style
# statement). Case-centric: one row of counts per case.
# =======================================================================
TABLE_A_COLUMNS = [
    ("district", "District"),
    ("crimeHead", "Crime Head"),
    ("crimeSubHead", "Crime Sub-Head"),
    ("gravity", "Gravity"),
    ("registered", "Cases Registered"),
    ("underInvestigation", "Under Investigation"),
    ("chargeSheeted", "Charge Sheeted"),
    ("closed", "Closed"),
    ("undetected", "Undetected"),
]


def build_crime_head_district(cases, district_of_case, head_name, subhead_name, gravity_name, status_name):
    groups = {}
    for c in cases:
        cid = c["CaseMasterID"]
        district = district_of_case.get(cid, "Unassigned")
        crime_head = head_name.get(c.get("CrimeMajorHeadID"), "Unclassified")
        crime_subhead = subhead_name.get(c.get("CrimeMinorHeadID"), "Unclassified")
        gravity = gravity_name.get(c.get("GravityOffenceID"), "Not recorded")
        status = status_name.get(c.get("CaseStatusID"), "Unknown")

        key = (district, crime_head, crime_subhead, gravity)
        row = groups.setdefault(key, {
            "district": district, "crimeHead": crime_head, "crimeSubHead": crime_subhead,
            "gravity": gravity, "registered": 0, **_blank_disposal(),
        })
        row["registered"] += 1
        _bump_disposal(row, status)

    rows = list(groups.values())
    rows.sort(key=lambda r: (r["district"], r["crimeHead"], r["crimeSubHead"]))
    return rows


# =======================================================================
# TABLE B — Crime Against Women statement. Victim-centric (NCRB counts
# victims, not cases, for this statement — one case can have several
# women victims), restricted to CH03 ("Crimes Against Women", the same
# SENSITIVE_CRIME_HEADS proxy main.py already uses elsewhere) and to
# female victims specifically. Age band splits Adult/Minor because a
# molestation/rape victim can herself be a minor — those victims are
# counted here AND in Table C; NCRB's own published tables overlap the
# same way, since a case can genuinely belong to both statements.
# =======================================================================
TABLE_B_COLUMNS = [
    ("district", "District"),
    ("offence", "Offence (Crime Sub-Head)"),
    ("victimAgeBand", "Victim Age Band"),
    ("victimsRecorded", "Victims Recorded"),
    ("underInvestigation", "Under Investigation"),
    ("chargeSheeted", "Charge Sheeted"),
    ("closed", "Closed"),
    ("undetected", "Undetected"),
]

WOMEN_CRIME_HEAD_ID = "CH03"


def build_women_statement(cases, victims_by_case, district_of_case, subhead_name, status_name):
    groups = {}
    for c in cases:
        if c.get("CrimeMajorHeadID") != WOMEN_CRIME_HEAD_ID:
            continue
        cid = c["CaseMasterID"]
        district = district_of_case.get(cid, "Unassigned")
        offence = subhead_name.get(c.get("CrimeMinorHeadID"), "Unclassified")
        status = status_name.get(c.get("CaseStatusID"), "Unknown")

        for v in victims_by_case.get(cid, []):
            if _gender_label(v.get("GenderID")) != "Female":
                continue
            age_band = "Minor (<18)" if _child_age_band(v.get("AgeYear")) is not None else "Adult (18+)"
            key = (district, offence, age_band)
            row = groups.setdefault(key, {
                "district": district, "offence": offence, "victimAgeBand": age_band,
                "victimsRecorded": 0, **_blank_disposal(),
            })
            row["victimsRecorded"] += 1
            _bump_disposal(row, status)

    rows = list(groups.values())
    rows.sort(key=lambda r: (r["district"], r["offence"], r["victimAgeBand"]))
    return rows


# =======================================================================
# TABLE C — Crime Against Children statement. Victim-centric, cuts
# across EVERY crime head (unlike Table B) — a child can be the victim
# of theft, cyber crime, or a body offence just as much as a Chapter-V
# BNS offence, and NCRB's own Crime Against Children table is defined
# by victim age, not by which crime head the case sits under. Age
# banding follows JJ Act, 2015 conventions (0-11 / 12-15 / 16-18).
# =======================================================================
TABLE_C_COLUMNS = [
    ("district", "District"),
    ("crimeHead", "Crime Head"),
    ("offence", "Offence (Crime Sub-Head)"),
    ("victimAgeBand", "Victim Age Band (JJ Act bands)"),
    ("victimsRecorded", "Victims Recorded"),
    ("underInvestigation", "Under Investigation"),
    ("chargeSheeted", "Charge Sheeted"),
    ("closed", "Closed"),
    ("undetected", "Undetected"),
]


def build_children_statement(cases, victims_by_case, district_of_case, head_name, subhead_name, status_name):
    groups = {}
    for c in cases:
        cid = c["CaseMasterID"]
        band_hits = [_child_age_band(v.get("AgeYear")) for v in victims_by_case.get(cid, [])]
        band_hits = [b for b in band_hits if b is not None]
        if not band_hits:
            continue

        district = district_of_case.get(cid, "Unassigned")
        crime_head = head_name.get(c.get("CrimeMajorHeadID"), "Unclassified")
        offence = subhead_name.get(c.get("CrimeMinorHeadID"), "Unclassified")
        status = status_name.get(c.get("CaseStatusID"), "Unknown")

        for band in band_hits:
            key = (district, crime_head, offence, band)
            row = groups.setdefault(key, {
                "district": district, "crimeHead": crime_head, "offence": offence,
                "victimAgeBand": band, "victimsRecorded": 0, **_blank_disposal(),
            })
            row["victimsRecorded"] += 1
            _bump_disposal(row, status)

    rows = list(groups.values())
    rows.sort(key=lambda r: (r["district"], r["crimeHead"], r["offence"], r["victimAgeBand"]))
    return rows


# =======================================================================
# TABLE D — Persons Apprehended statement. Accused-centric, gender x
# age-band x crime-head x gravity counts ONLY — never a name, never a
# PersonKey, never a per-person row, so juvenile (child-in-conflict-
# with-law) figures are JJ Act Sec 74-safe by construction: the most
# granular this table ever gets is "N juveniles, this district, this
# offence category" — never "juvenile #N".
# =======================================================================
TABLE_D_COLUMNS = [
    ("district", "District"),
    ("crimeHead", "Crime Head"),
    ("gravity", "Gravity"),
    ("ageBand", "Age Band"),
    ("gender", "Gender"),
    ("personsRecorded", "Persons Recorded"),
]


def build_persons_apprehended(cases, accused_by_case, district_of_case, head_name, gravity_name):
    groups = {}
    for c in cases:
        cid = c["CaseMasterID"]
        district = district_of_case.get(cid, "Unassigned")
        crime_head = head_name.get(c.get("CrimeMajorHeadID"), "Unclassified")
        gravity = gravity_name.get(c.get("GravityOffenceID"), "Not recorded")

        for a in accused_by_case.get(cid, []):
            age_band = _juvenile_or_adult(a.get("AgeYear"))
            gender = _gender_label(a.get("GenderID"))
            key = (district, crime_head, gravity, age_band, gender)
            row = groups.setdefault(key, {
                "district": district, "crimeHead": crime_head, "gravity": gravity,
                "ageBand": age_band, "gender": gender, "personsRecorded": 0,
            })
            row["personsRecorded"] += 1

    rows = list(groups.values())
    rows.sort(key=lambda r: (r["district"], r["crimeHead"], r["ageBand"], r["gender"]))
    return rows


# =======================================================================
# Table registry + shared CSV/zip formatting.
# =======================================================================
TABLES = {
    "crime_head_district": {
        "title": "TABLE A — District-wise Crime Head Incidence & Disposal",
        "columns": TABLE_A_COLUMNS,
    },
    "women": {
        "title": "TABLE B — Crime Against Women Statement (Sec 72 BNS victim-identity protected — victims only, never named)",
        "columns": TABLE_B_COLUMNS,
    },
    "children": {
        "title": "TABLE C — Crime Against Children Statement (JJ Act 2015 Sec 74 — victims only, never named, JJ Act age bands)",
        "columns": TABLE_C_COLUMNS,
    },
    "persons_apprehended": {
        "title": "TABLE D — Persons Apprehended Statement (JJ Act 2015 Sec 74 — juvenile figures aggregate-only, never named)",
        "columns": TABLE_D_COLUMNS,
    },
}


def _table_to_csv(table_key, rows, period_from, period_to, officer_label, scope_label):
    meta = TABLES[table_key]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([meta["title"]])
    writer.writerow([f"Period: {period_from or 'ALL'} to {period_to or 'ALL'}  |  Scope: {scope_label}  |  Generated by: {officer_label}  |  Generated at: {datetime.datetime.utcnow().isoformat()}Z"])
    writer.writerow([SMALL_CELL_NOTE])
    writer.writerow([])
    writer.writerow([label for _, label in meta["columns"]])
    for r in rows:
        writer.writerow([r[key] for key, _ in meta["columns"]])
    return buf.getvalue()


def table_to_csv(table_key, rows, period_from, period_to, officer_label, scope_label):
    if table_key not in TABLES:
        raise ValueError(f"Unknown NCRB table '{table_key}'")
    return _table_to_csv(table_key, rows, period_from, period_to, officer_label, scope_label)


def bundle_to_zip_bytes(built_tables, period_from, period_to, officer_label, scope_label):
    """
    built_tables: dict of table_key -> rows (already built by the
    build_* functions above). Produces one CSV per table in a single
    zip — this is the "SCRB doesn't manually re-key data upward" path:
    one download, every standard statement, already scoped correctly.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for table_key, rows in built_tables.items():
            csv_text = _table_to_csv(table_key, rows, period_from, period_to, officer_label, scope_label)
            zf.writestr(f"ncrb_{table_key}.csv", csv_text)
        readme = (
            "This bundle contains the standard NCRB-style crime-return statements "
            f"for the period {period_from or 'ALL'} to {period_to or 'ALL'}, scope: {scope_label}.\n\n"
            "These are aggregate-only by construction: no victim, accused, or "
            "complainant name/identifier was read from the database to build any "
            "table in this bundle (see ncrb_export.py's module docstring). "
            "Juvenile and sexual-offence-victim figures are reported strictly as "
            "banded counts per JJ Act, 2015 Sec 74 and Sec 72 BNS.\n\n"
            "This bundle is a re-keying aid for your own SCRB workflow, not a "
            "certified NCRB/CCTNS submission by itself.\n\n"
            f"{SMALL_CELL_NOTE}\n"
        )
        zf.writestr("README.txt", readme)
    return buf.getvalue()