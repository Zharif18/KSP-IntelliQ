"""
KSP IntelliQ — Synthetic Data Generator
Generates CSVs matching the REAL ER diagram schema (CaseMaster, Accused,
Victim, ArrestSurrender, Employee, Unit, District, etc.) provided by the
organizers — not an invented schema. Designed to be regenerated instantly
if a real dataset/API arrives later (just swap the data source, keep the
same column names your Data Store tables and Functions already expect).

Run: python3 generate_all.py
Output: ./output/*.csv
"""
import os
import random
from datetime import datetime, timedelta
from faker import Faker
import pandas as pd

fake = Faker("en_IN")
random.seed(42)  # reproducible demo data

OUTPUT = "output"
os.makedirs(OUTPUT, exist_ok=True)

# ---------------------------------------------------------------------
# SCALE — tune these up/down. Current values are demo-realistic and fast
# to generate; bump NUM_CASES for a "denser" looking platform later.
# ---------------------------------------------------------------------
NUM_UNITS_PER_DISTRICT = (4, 8)   # min, max police stations per district
NUM_EMPLOYEES = 260
NUM_CASES = 1200
REPEAT_OFFENDER_RATE = 0.16       # ~16% of accused reuse an existing PersonKey

def save(rows, name):
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUTPUT, name), index=False)
    print(f"✅ {name} — {len(df)} rows")
    return df

def rid(prefix, n, width=4):
    return f"{prefix}{n:0{width}d}"

# =======================================================================
# 1. State
# =======================================================================
states = [{"StateID": "ST01", "StateName": "Karnataka", "NationalityID": "IN", "Active": 1}]
save(states, "State.csv")

# =======================================================================
# 2. District — all 31 real Karnataka districts w/ approx HQ coordinates
# =======================================================================
DISTRICT_COORDS = {
    "Bagalkot": (16.1819, 75.6961), "Ballari": (15.1394, 76.9214),
    "Belagavi": (15.8497, 74.4977), "Bengaluru Rural": (13.2846, 77.6078),
    "Bengaluru Urban": (12.9716, 77.5946), "Bidar": (17.9104, 77.5199),
    "Chamarajanagar": (11.9236, 76.9391), "Chikkaballapur": (13.4351, 77.7315),
    "Chikkamagaluru": (13.3161, 75.7720), "Chitradurga": (14.2251, 76.3980),
    "Dakshina Kannada": (12.8438, 75.2479), "Davanagere": (14.4644, 75.9218),
    "Dharwad": (15.4589, 75.0078), "Gadag": (15.4297, 75.6350),
    "Hassan": (13.0068, 76.1004), "Haveri": (14.7936, 75.4044),
    "Kalaburagi": (17.3297, 76.8343), "Kodagu": (12.4244, 75.7382),
    "Kolar": (13.1372, 78.1290), "Koppal": (15.3547, 76.1548),
    "Mandya": (12.5242, 76.8958), "Mysuru": (12.2958, 76.6394),
    "Raichur": (16.2120, 77.3439), "Ramanagara": (12.7217, 77.2812),
    "Shivamogga": (13.9299, 75.5681), "Tumakuru": (13.3409, 77.1010),
    "Udupi": (13.3409, 74.7421), "Uttara Kannada": (14.7900, 74.7042),
    "Vijayapura": (16.8302, 75.7100), "Yadgir": (16.7702, 77.1376),
    "Vijayanagara": (15.2350, 76.4600),
}
districts = []
for i, (name, (lat, lng)) in enumerate(DISTRICT_COORDS.items(), start=1):
    districts.append({
        "DistrictID": rid("DIST", i), "DistrictName": name,
        "StateID": "ST01", "Active": 1,
        "_lat": lat, "_lng": lng,  # kept for Unit generation, not a real column
    })
df_districts = save([{k: v for k, v in d.items() if not k.startswith("_")} for d in districts], "District.csv")

# =======================================================================
# 3. UnitType
# =======================================================================
unit_types = [
    {"UnitTypeID": "UT01", "UnitTypeName": "Police Station", "CityDistState": "City", "Hierarchy": 4, "Active": 1},
    {"UnitTypeID": "UT02", "UnitTypeName": "Circle Office", "CityDistState": "District", "Hierarchy": 3, "Active": 1},
    {"UnitTypeID": "UT03", "UnitTypeName": "Sub-Division", "CityDistState": "District", "Hierarchy": 2, "Active": 1},
    {"UnitTypeID": "UT04", "UnitTypeName": "District HQ", "CityDistState": "District", "Hierarchy": 1, "Active": 1},
]
save(unit_types, "UnitType.csv")

# =======================================================================
# 4. Unit — police stations per district, real-flavored names
# =======================================================================
STATION_PREFIXES = ["Town", "Rural", "Traffic", "East", "West", "North", "South",
                     "Central", "Women", "Cyber Crime", "Market", "Industrial"]
KNOWN_STATIONS = {
    "Bengaluru Urban": ["Whitefield", "Indiranagar", "Cubbon Park", "Jayanagar",
                        "Electronic City", "HAL", "Koramangala", "Yeshwanthpur",
                        "Rajajinagar", "Banashankari"],
    "Mysuru": ["Nazarbad", "Vijayanagar", "Devaraja", "Lakshmipuram", "Jayalakshmipuram"],
    "Belagavi": ["Camp", "Market", "Tilakwadi", "APMC"],
    "Dakshina Kannada": ["Mangaluru City", "Pandeshwar", "Kavoor", "Bunder"],
    "Dharwad": ["Hubballi City", "Vidyanagar", "Old Hubli"],
}
units = []
unit_counter = 1
for d in districts:
    n_stations = random.randint(*NUM_UNITS_PER_DISTRICT)
    name_pool = KNOWN_STATIONS.get(d["DistrictName"], [])
    for i in range(n_stations):
        if i < len(name_pool):
            station_label = name_pool[i]
        else:
            station_label = f"{d['DistrictName']} {random.choice(STATION_PREFIXES)}"
        lat = d["_lat"] + random.uniform(-0.06, 0.06)
        lng = d["_lng"] + random.uniform(-0.06, 0.06)
        units.append({
            "UnitID": rid("UNIT", unit_counter),
            "UnitName": f"{station_label} Police Station",
            "TypeID": "UT01",
            "ParentUnit": "",
            "NationalityID": "IN",
            "StateID": "ST01",
            "DistrictID": d["DistrictID"],
            "Active": 1,
            "_lat": lat, "_lng": lng,
        })
        unit_counter += 1
df_units = save([{k: v for k, v in u.items() if not k.startswith("_")} for u in units], "Unit.csv")

# =======================================================================
# 5. Rank & Designation
# =======================================================================
ranks = [
    {"RankID": "RNK01", "RankName": "Constable", "Hierarchy": 10, "Active": 1},
    {"RankID": "RNK02", "RankName": "Head Constable", "Hierarchy": 9, "Active": 1},
    {"RankID": "RNK03", "RankName": "Asst. Sub-Inspector", "Hierarchy": 8, "Active": 1},
    {"RankID": "RNK04", "RankName": "Sub-Inspector", "Hierarchy": 7, "Active": 1},
    {"RankID": "RNK05", "RankName": "Inspector", "Hierarchy": 6, "Active": 1},
    {"RankID": "RNK06", "RankName": "Deputy Superintendent", "Hierarchy": 5, "Active": 1},
    {"RankID": "RNK07", "RankName": "Superintendent of Police", "Hierarchy": 4, "Active": 1},
]
save(ranks, "Rank.csv")

designations = [
    {"DesignationID": "DSG01", "DesignationName": "Investigating Officer", "SortOrder": 1, "Active": 1},
    {"DesignationID": "DSG02", "DesignationName": "Station House Officer", "SortOrder": 2, "Active": 1},
    {"DesignationID": "DSG03", "DesignationName": "Beat Constable", "SortOrder": 3, "Active": 1},
    {"DesignationID": "DSG04", "DesignationName": "Circle Inspector", "SortOrder": 4, "Active": 1},
]
save(designations, "Designation.csv")

# =======================================================================
# 6. Employee (Officers)
# =======================================================================
GENDER_WEIGHTS = [("M", 0.78), ("F", 0.21), ("T", 0.01)]
def pick_gender():
    r = random.random()
    cum = 0
    for g, w in GENDER_WEIGHTS:
        cum += w
        if r <= cum:
            return g
    return "M"

employees = []
for i in range(1, NUM_EMPLOYEES + 1):
    unit = random.choice(units)
    dob = fake.date_of_birth(minimum_age=23, maximum_age=58)
    employees.append({
        "EmployeeID": rid("EMP", i),
        "DistrictID": unit["DistrictID"],
        "UnitID": unit["UnitID"],
        "RankID": random.choice(ranks)["RankID"],
        "DesignationID": random.choice(designations)["DesignationID"],
        "KGID": f"KGID{fake.unique.random_number(digits=8, fix_len=True)}",
        "FirstName": fake.first_name_male() if random.random() > 0.2 else fake.first_name_female(),
        "EmployeeDOB": dob.isoformat(),
        "GenderID": pick_gender(),
        "BloodGroupID": random.choice(["A+", "B+", "O+", "AB+", "A-", "B-", "O-"]),
        "PhysicallyChallenged": 0,
        "AppointmentDate": fake.date_between(start_date="-25y", end_date="-1y").isoformat(),
    })
df_employees = save(employees, "Employee.csv")

# =======================================================================
# 7. Lookup: CaseCategory, GravityOffence, CaseStatusMaster
# =======================================================================
case_categories = [
    {"CaseCategoryID": "CAT1", "LookupValue": "FIR"},
    {"CaseCategoryID": "CAT2", "LookupValue": "UDR"},
    {"CaseCategoryID": "CAT3", "LookupValue": "Zero FIR"},
    {"CaseCategoryID": "CAT4", "LookupValue": "PAR"},
]
save(case_categories, "CaseCategory.csv")

gravity = [
    {"GravityOffenceID": "GRV1", "LookupValue": "Heinous"},
    {"GravityOffenceID": "GRV2", "LookupValue": "Non-Heinous"},
]
save(gravity, "GravityOffence.csv")

case_status = [
    {"CaseStatusID": "STA1", "CaseStatusName": "Under Investigation"},
    {"CaseStatusID": "STA2", "CaseStatusName": "Charge Sheeted"},
    {"CaseStatusID": "STA3", "CaseStatusName": "Closed"},
    {"CaseStatusID": "STA4", "CaseStatusName": "Undetected"},
]
save(case_status, "CaseStatusMaster.csv")

# =======================================================================
# 8. CrimeHead & CrimeSubHead
# =======================================================================
crime_heads = [
    {"CrimeHeadID": "CH01", "CrimeGroupName": "Crimes Against Body", "Active": 1},
    {"CrimeHeadID": "CH02", "CrimeGroupName": "Crimes Against Property", "Active": 1},
    {"CrimeHeadID": "CH03", "CrimeGroupName": "Crimes Against Women", "Active": 1},
    {"CrimeHeadID": "CH04", "CrimeGroupName": "Economic Offences", "Active": 1},
    {"CrimeHeadID": "CH05", "CrimeGroupName": "Cyber Crime", "Active": 1},
    {"CrimeHeadID": "CH06", "CrimeGroupName": "Public Order & Miscellaneous", "Active": 1},
]
save(crime_heads, "CrimeHead.csv")

CRIME_SUBHEADS = {
    "CH01": ["Murder", "Attempt to Murder", "Grievous Hurt", "Assault", "Kidnapping"],
    "CH02": ["Theft", "Vehicle Theft", "Burglary", "Robbery", "Dacoity", "Chain Snatching"],
    "CH03": ["Molestation", "Dowry Death", "Domestic Violence", "Rape", "Eve Teasing"],
    "CH04": ["Cheating", "Forgery", "Criminal Breach of Trust", "Counterfeiting"],
    "CH05": ["Cyber Fraud", "Online Harassment", "Data Theft", "Phishing"],
    "CH06": ["Rioting", "Public Nuisance", "Unlawful Assembly"],
}
crime_subheads = []
sub_counter = 1
for head_id, names in CRIME_SUBHEADS.items():
    for seq, name in enumerate(names, start=1):
        crime_subheads.append({
            "CrimeSubHeadID": rid("CSH", sub_counter),
            "CrimeHeadID": head_id,
            "CrimeHeadName": name,
            "SeqID": seq,
        })
        sub_counter += 1
df_subheads = save(crime_subheads, "CrimeSubHead.csv")

# =======================================================================
# 9. CaseMaster (the FIRs) — with deliberate hotspot + time-of-day bias
#    so hotspot clustering / predictive scoring has real patterns to find
# =======================================================================
HOTSPOT_DISTRICTS = ["Bengaluru Urban", "Mysuru", "Belagavi", "Dakshina Kannada", "Dharwad"]
hotspot_units = [u for u in units if any(
    d["DistrictID"] == u["DistrictID"] and d["DistrictName"] in HOTSPOT_DISTRICTS for d in districts
)]
other_units = [u for u in units if u not in hotspot_units]

def biased_unit():
    # 55% of cases cluster into hotspot-district stations, rest spread out
    return random.choice(hotspot_units) if random.random() < 0.55 and hotspot_units else random.choice(units)

def biased_hour():
    # Crime skews toward evening/night — realistic + gives predictive charts a pattern
    return random.choices(
        population=list(range(24)),
        weights=[1,1,1,1,1,1,2,2,3,3,3,4,4,4,4,5,6,7,8,9,8,6,4,2],
        k=1
    )[0]

district_by_id = {d["DistrictID"]: d for d in districts}

BRIEF_TEMPLATES = [
    "Complainant reported that {item} was stolen from {place} on the night of the incident.",
    "An altercation broke out at {place}, resulting in injuries to the victim.",
    "Accused allegedly entered {place} and removed valuables without the owner's knowledge.",
    "Victim was approached by unidentified persons near {place} and robbed at knifepoint.",
    "A complaint was filed regarding harassment near {place} over the past few weeks.",
    "Accused is alleged to have used a fake identity to defraud the complainant near {place}.",
    "Physical assault reported outside {place} following a dispute between the parties.",
    "Vehicle registered to the complainant was found missing from outside {place}.",
    "Complainant alleges online fraud involving a transaction linked to {place}.",
    "A group of persons allegedly created a public disturbance near {place}.",
]
BRIEF_ITEMS = ["a two-wheeler", "jewellery", "a mobile phone", "cash", "a laptop", "documents"]
BRIEF_PLACES = ["the main market", "a residential complex", "the bus stand", "a parking area",
                "the railway station", "a commercial complex", "the local temple grounds", "a residential lane"]

def brief_facts():
    template = random.choice(BRIEF_TEMPLATES)
    return template.format(item=random.choice(BRIEF_ITEMS), place=random.choice(BRIEF_PLACES))

cases = []
for i in range(1, NUM_CASES + 1):
    unit = biased_unit()
    district = district_by_id[unit["DistrictID"]]
    subhead = random.choice(crime_subheads)
    reg_date = fake.date_between(start_date="-365d", end_date="today")
    hour = biased_hour()
    incident_dt = datetime.combine(reg_date, datetime.min.time()) + timedelta(hours=hour, minutes=random.randint(0, 59))
    employee = random.choice([e for e in employees if e["UnitID"] == unit["UnitID"]] or employees)

    crime_no = f"1{district['DistrictID'][-4:].zfill(4)}{unit['UnitID'][-4:].zfill(4)}{reg_date.year}{i:05d}"
    case_no = f"{reg_date.year}{i:05d}"

    cases.append({
        "CaseMasterID": rid("CASE", i, width=5),
        "CrimeNo": crime_no,
        "CaseNo": case_no,
        "CrimeRegisteredDate": reg_date.isoformat(),
        "PolicePersonID": employee["EmployeeID"],
        "PoliceStationID": unit["UnitID"],
        "CaseCategoryID": "CAT1",  # default FIR; vary if you want UDR/PAR mix
        "GravityOffenceID": "GRV1" if subhead["CrimeHeadID"] in ("CH01", "CH03") else "GRV2",
        "CrimeMajorHeadID": subhead["CrimeHeadID"],
        "CrimeMinorHeadID": subhead["CrimeSubHeadID"],
        "CaseStatusID": random.choices(
            ["STA1", "STA2", "STA3", "STA4"], weights=[0.35, 0.25, 0.30, 0.10])[0],
        "CourtID": "",
        "IncidentFromDate": incident_dt.isoformat(),
        "IncidentToDate": incident_dt.isoformat(),
        "InfoReceivedPSDate": incident_dt.isoformat(),
        "latitude": round(unit["_lat"] + random.uniform(-0.02, 0.02), 6),
        "longitude": round(unit["_lng"] + random.uniform(-0.02, 0.02), 6),
        "BriefFacts": brief_facts(),
    })
df_cases = save(cases, "CaseMaster.csv")

# =======================================================================
# 10. Victim
# =======================================================================
victims = []
v_counter = 1
for case in cases:
    for _ in range(random.choice([1, 1, 1, 2])):  # mostly 1 victim, sometimes 2
        gender = pick_gender()
        victims.append({
            "VictimMasterID": rid("VIC", v_counter, width=5),
            "CaseMasterID": case["CaseMasterID"],
            "VictimName": fake.name_male() if gender == "M" else fake.name_female(),
            "AgeYear": random.randint(5, 80),
            "GenderID": gender,
            "VictimPolice": 0,
        })
        v_counter += 1
save(victims, "Victim.csv")

# =======================================================================
# 11. Accused — WITH PersonKey so repeat offenders are detectable
#     (this is the field the real ER diagram is missing for network analysis)
# =======================================================================
person_key_pool = []  # reused identities to simulate serial offenders
accused_rows = []
a_counter = 1
for case in cases:
    n_accused = random.choice([1, 1, 2, 2, 3])
    used_keys_this_case = set()
    for j in range(n_accused):
        available_reuse = [p for p in person_key_pool if p["key"] not in used_keys_this_case]
        reuse = available_reuse and random.random() < REPEAT_OFFENDER_RATE
        if reuse:
            person_key = random.choice(available_reuse)
            name = person_key["name"]
            age = person_key["age"] + random.randint(0, 1)
            gender = person_key["gender"]
        else:
            gender = pick_gender()
            name = fake.name_male() if gender == "M" else fake.name_female()
            age = random.randint(16, 65)
            person_key = {"key": rid("PER", len(person_key_pool) + 1, width=5), "name": name, "age": age, "gender": gender}
            person_key_pool.append(person_key)

        used_keys_this_case.add(person_key["key"])
        accused_rows.append({
            "AccusedMasterID": rid("ACC", a_counter, width=5),
            "CaseMasterID": case["CaseMasterID"],
            "AccusedName": name,
            "AgeYear": age,
            "GenderID": gender,
            "PersonID": f"A{j+1}",
            "PersonKey": person_key["key"],  # <-- addition beyond the original ER diagram
        })
        a_counter += 1
df_accused = save(accused_rows, "Accused.csv")

repeat_count = sum(1 for p in person_key_pool if sum(1 for a in accused_rows if a["PersonKey"] == p["key"]) > 1)
print(f"   ↳ {repeat_count} of {len(person_key_pool)} unique persons appear in 2+ cases (repeat offenders for network analysis)")

# =======================================================================
# 12. ComplainantDetails (simplified: no separate caste/religion/occupation
#     lookup tables — stored as plain text to cut scope; upgrade later if
#     time allows)
# =======================================================================
OCCUPATIONS = ["Farmer", "Government Employee", "Private Employee", "Student", "Business", "Unemployed", "Homemaker"]
complainants = []
c_counter = 1
for case in cases:
    gender = pick_gender()
    complainants.append({
        "ComplainantID": rid("COMP", c_counter, width=5),
        "CaseMasterID": case["CaseMasterID"],
        "ComplainantName": fake.name_male() if gender == "M" else fake.name_female(),
        "AgeYear": random.randint(18, 75),
        "Occupation": random.choice(OCCUPATIONS),
        "GenderID": gender,
    })
    c_counter += 1
save(complainants, "ComplainantDetails.csv")

# =======================================================================
# 13. ArrestSurrender
# =======================================================================
arrests = []
ar_counter = 1
for case in cases:
    if case["CaseStatusID"] in ("STA2", "STA3") and random.random() < 0.7:
        case_accused = [a for a in accused_rows if a["CaseMasterID"] == case["CaseMasterID"]]
        if not case_accused:
            continue
        acc = random.choice(case_accused)
        arrest_date = (datetime.fromisoformat(case["CrimeRegisteredDate"]) + timedelta(days=random.randint(1, 60))).date()
        arrests.append({
            "ArrestSurrenderID": rid("ARR", ar_counter, width=5),
            "CaseMasterID": case["CaseMasterID"],
            "ArrestSurrenderTypeID": random.choice(["Arrest", "Surrender"]),
            "ArrestSurrenderDate": arrest_date.isoformat(),
            "ArrestSurrenderStateId": "ST01",
            "ArrestSurrenderDistrictId": next(u["DistrictID"] for u in units if u["UnitID"] == case["PoliceStationID"]),
            "PoliceStationID": case["PoliceStationID"],
            "IOID": case["PolicePersonID"],
            "CourtID": "",
            "AccusedMasterID": acc["AccusedMasterID"],
            "IsAccused": 1,
            "IsComplainantAccused": 0,
        })
        ar_counter += 1
save(arrests, "ArrestSurrender.csv")

print("\n✅ All CSVs generated in ./output — ready to import into Catalyst Data Store.")
