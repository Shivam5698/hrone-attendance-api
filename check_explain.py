"""Checks /admin/explain/* : plan must contain IXSCAN and no COLLSCAN,
and bad input must give 422.

Before running:
  1. python sample_seed.py
  2. uvicorn app.main:app --reload --port 8000   (other terminal, keep running)
Then:
  python check_explain.py
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
passed = 0
failed = 0


def get(path):
    try:
        with urllib.request.urlopen(BASE + path) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check_plan(name, path):
    global passed, failed
    status, text = get(path)
    if status != 200:
        failed += 1
        print(f"FAIL  {name}: status {status}")
        return
    body = json.loads(text)
    has_keys = {"endpoint", "collection", "explain"} <= set(body)
    ix = "IXSCAN" in text
    coll = "COLLSCAN" in text
    has_stats = "executionStats" in text  # for aggregate it is nested inside stages[].$cursor
    if has_keys and ix and not coll and has_stats:
        passed += 1
        print(f"PASS  {name}: IXSCAN found, no COLLSCAN, executionStats present")
    else:
        failed += 1
        print(f"FAIL  {name}: keys={has_keys} IXSCAN={ix} COLLSCAN={coll} executionStats={has_stats}")


def check_status(name, path, expected):
    global passed, failed
    status, _ = get(path)
    if status == expected:
        passed += 1
        print(f"PASS  {name}: {status}")
    else:
        failed += 1
        print(f"FAIL  {name}: got {status}, expected {expected}")


print("--- plans ---")
check_plan("attendance_list, no filter", "/admin/explain/attendance_list")
check_plan(
    "attendance_list, emp + dates",
    "/admin/explain/attendance_list?emp_code=EMP0001&date_from=2026-07-01&date_to=2026-07-31",
)
check_plan("attendance_list, status", "/admin/explain/attendance_list?status=PRESENT")
check_plan("attendance_list, dates only", "/admin/explain/attendance_list?date_from=2026-07-01&date_to=2026-07-31")
check_plan("employee_monthly", "/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-07")

print("--- validation ---")
check_status("unknown endpoint name", "/admin/explain/banana", 422)
check_status("employee_monthly without month", "/admin/explain/employee_monthly?emp_code=EMP0001", 422)
check_status("employee_monthly without emp_code", "/admin/explain/employee_monthly?month=2026-07", 422)
check_status("employee_monthly bad month", "/admin/explain/employee_monthly?emp_code=EMP0001&month=2026-13", 422)
check_status("attendance_list page=0", "/admin/explain/attendance_list?page=0", 422)
check_status("attendance_list page_size=1000", "/admin/explain/attendance_list?page_size=1000", 422)
check_status(
    "attendance_list date_from > date_to",
    "/admin/explain/attendance_list?date_from=2026-07-08&date_to=2026-07-07",
    422,
)

print(f"\nPASSED: {passed}   FAILED: {failed}")