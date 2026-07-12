import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from supabase import create_client

load_dotenv()

app = Flask(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY environment variables are required.")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

TABLES = {
    "departments": "departments",
    "positions": "positions",
    "employees": "employees",
    "attendance": "attendance",
    "leaves": "leaves",
    "payroll": "payroll",
}

ALLOWED_FIELDS = {
    "departments": {"name", "description", "status"},
    "positions": {"title", "description", "department_id", "level", "status"},
    "employees": {
        "name", "email", "phone", "department_id", "position_id", "salary",
        "status", "hire_date", "profile_pic"
    },
    "attendance": {"employee_id", "date", "check_in", "check_out", "status", "notes"},
    "leaves": {"employee_id", "leave_type", "start_date", "end_date", "status", "reason"},
    "payroll": {
        "employee_id", "pay_period", "basic_salary", "bonus",
        "deductions", "net_pay", "status", "notes"
    },
}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "JavaGoat HR"})


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    password = payload.get("password")

    if email == "admin@javagoat.hr" and password == "password123":
        return jsonify({
            "token": "mock-javagoat-hr-token",
            "user": {"email": email, "name": "Admin"}
        })

    return jsonify({"error": "Invalid email or password"}), 401


def clean_payload(entity, payload, partial=False):
    allowed = ALLOWED_FIELDS[entity]
    cleaned = {}

    for key, value in (payload or {}).items():
        if key in allowed:
            cleaned[key] = value

    if not partial and entity == "employees":
        cleaned.setdefault("profile_pic", None)

    if entity == "payroll":
        basic = float(cleaned.get("basic_salary") or 0)
        bonus = float(cleaned.get("bonus") or 0)
        deductions = float(cleaned.get("deductions") or 0)
        if "net_pay" not in cleaned:
            cleaned["net_pay"] = basic + bonus - deductions

    return cleaned


def table_query(entity):
    return supabase.table(TABLES[entity])


@app.get("/api/<entity>")
def list_records(entity):
    if entity not in TABLES:
        return jsonify({"error": "Unknown entity"}), 404

    try:
        response = table_query(entity).select("*").order("id", desc=False).execute()
        return jsonify(response.data or [])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/<entity>/<int:record_id>")
def get_record(entity, record_id):
    if entity not in TABLES:
        return jsonify({"error": "Unknown entity"}), 404

    try:
        response = table_query(entity).select("*").eq("id", record_id).single().execute()
        return jsonify(response.data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 404


@app.post("/api/<entity>")
def create_record(entity):
    if entity not in TABLES:
        return jsonify({"error": "Unknown entity"}), 404

    payload = clean_payload(entity, request.get_json(silent=True) or {}, partial=False)

    try:
        response = table_query(entity).insert(payload).execute()
        created = response.data[0] if response.data else payload
        return jsonify(created), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.put("/api/<entity>/<int:record_id>")
def update_record(entity, record_id):
    """
    Partial update route.

    This intentionally updates only fields present in the JSON payload.
    It prevents assignment operations like {"position_id": 4} from wiping
    employee name, email, salary, status or profile_pic.
    """
    if entity not in TABLES:
        return jsonify({"error": "Unknown entity"}), 404

    payload = clean_payload(entity, request.get_json(silent=True) or {}, partial=True)

    if not payload:
        return jsonify({"error": "No valid fields supplied"}), 400

    try:
        response = table_query(entity).update(payload).eq("id", record_id).execute()
        updated = response.data[0] if response.data else {"id": record_id, **payload}
        return jsonify(updated)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.delete("/api/<entity>/<int:record_id>")
def delete_record(entity, record_id):
    if entity not in TABLES:
        return jsonify({"error": "Unknown entity"}), 404

    try:
        table_query(entity).delete().eq("id", record_id).execute()
        return jsonify({"deleted": True, "id": record_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.get("/api/dashboard/stats")
def dashboard_stats():
    try:
        departments = supabase.table("departments").select("*").execute().data or []
        positions = supabase.table("positions").select("*").execute().data or []
        employees = supabase.table("employees").select("*").execute().data or []
        attendance = supabase.table("attendance").select("*").execute().data or []
        payroll = supabase.table("payroll").select("*").execute().data or []
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    dept_by_id = {d["id"]: d for d in departments}
    pos_by_id = {p["id"]: p for p in positions}

    today = date.today().isoformat()
    present_today = sum(
        1 for row in attendance
        if row.get("date") == today and row.get("status") in {"Present", "Remote", "Late"}
    )

    current_month = date.today().strftime("%Y-%m")
    payroll_month = sum(
        float(row.get("net_pay") or 0)
        for row in payroll
        if str(row.get("pay_period") or "").startswith(current_month)
    )

    month_labels = []
    month_keys = []
    base = date.today().replace(day=1)

    for i in range(5, -1, -1):
        year = base.year
        month = base.month - i
        while month <= 0:
            month += 12
            year -= 1
        key = f"{year:04d}-{month:02d}"
        month_keys.append(key)
        month_labels.append(datetime.strptime(key, "%Y-%m").strftime("%b"))

    hiring_counts = Counter()
    for emp in employees:
        hire_date = emp.get("hire_date")
        if hire_date:
            hiring_counts[str(hire_date)[:7]] += 1

    dept_counts = Counter()
    for emp in employees:
        dept = dept_by_id.get(emp.get("department_id"))
        dept_counts[dept.get("name") if dept else "Unassigned"] += 1

    status_counts = Counter(emp.get("status") or "Unknown" for emp in employees)

    last_10_days = [date.today() - timedelta(days=i) for i in range(9, -1, -1)]
    attendance_counts = []
    for day in last_10_days:
        day_iso = day.isoformat()
        attendance_counts.append(sum(
            1 for row in attendance
            if row.get("date") == day_iso and row.get("status") in {"Present", "Remote", "Late"}
        ))

    employees_by_position = []
    for emp in employees:
        pos = pos_by_id.get(emp.get("position_id"))
        dept = dept_by_id.get(emp.get("department_id"))
        employees_by_position.append({
            "id": emp.get("id"),
            "name": emp.get("name"),
            "profile_pic": emp.get("profile_pic"),
            "position": pos.get("title") if pos else "Unassigned",
            "department": dept.get("name") if dept else "No department",
        })

    position_counts = Counter(
        (pos_by_id.get(emp.get("position_id")) or {}).get("title", "Unassigned")
        for emp in employees
    )

    return jsonify({
        "cards": {
            "employees": len(employees),
            "departments": len(departments),
            "positions": len(positions),
            "present_today": present_today,
            "payroll_month": payroll_month,
        },
        "hiring_trend": {
            "labels": month_labels,
            "data": [hiring_counts[key] for key in month_keys],
        },
        "department_mix": {
            "labels": list(dept_counts.keys()) or ["No Data"],
            "data": list(dept_counts.values()) or [0],
        },
        "position_mix": {
            "labels": list(position_counts.keys()) or ["No Data"],
            "data": list(position_counts.values()) or [0],
        },
        "employees_by_position": employees_by_position,
        "attendance_trend": {
            "labels": [d.strftime("%b %d") for d in last_10_days],
            "data": attendance_counts,
        },
        "status_breakdown": {
            "labels": list(status_counts.keys()) or ["No Data"],
            "data": list(status_counts.values()) or [0],
        },
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=os.getenv("FLASK_DEBUG") == "1")
