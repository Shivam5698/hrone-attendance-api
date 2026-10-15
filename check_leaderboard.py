"""Checks GET /analytics/leaderboard/late (ties, limit after ranking, department filter).

The script creates its own test data through the API (late punch-ins on 14 Oct 2026):

    EMP0001  10:30  -> 60 min late   (Engineering)
    EMP0002  10:30  -> 60 min late   (Engineering)   tie with EMP0001
    EMP0003  10:20  -> 50 min late   (Sales)
    EMP0004  10:15  -> 45 min late   (Sales)
    EMP0006  09:35  ->  5 min  (not late, threshold is > 10 min) -> never on the board

Expected ranks for 2026-10: 1, 1, 3, 4   (standard competition ranking)

Before running:
  1. python sample_seed.py
  2. uvicorn app.main:app --reload --port 8000   (other terminal, keep running)
Then:
  python check_leaderboard.py
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
passed = 0
failed = 0


def request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def item(rank, emp_code, name, department, total, count):
    return {
        "rank": rank,
        "emp_code": emp_code,
        "name": name,
        "department": department,
        "total_late_minutes": total,
        "late_count": count,
    }


def check_items(name, path, expected):
    global passed, failed
    status, body = request("GET", path)
    if status != 200:
        failed += 1
        print(f"FAIL  {name}: status {status}")
        return
    got = body.get("items")
    if got == expected:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}")
        print("      expected:", json.dumps(expected))
        print("      got     :", json.dumps(got))


def check_status(name, path, expected):
    global passed, failed
    status, _ = request("GET", path)
    if status == expected:
        passed += 1
        print(f"PASS  {name}: {status}")
    else:
        failed += 1
        print(f"FAIL  {name}: got {status}, expected {expected}")


# ---------- setup ----------
print("--- setup (punch-ins on 14 Oct 2026) ---")
for emp, ms in [
    ("EMP0001", 1791954000000),  # 10:30
    ("EMP0002", 1791954000000),  # 10:30
    ("EMP0003", 1791953400000),  # 10:20
    ("EMP0004", 1791953100000),  # 10:15
    ("EMP0006", 1791950700000),  # 09:35
]:
    status, _ = request("POST", "/attendance/punch-in", {"emp_code": emp, "punched_at": ms})
    print(f"  {emp}: {status}  (201 = created, 409 = already there from an earlier run)")

ASHA = item(1, "EMP0001", "Asha Rao", "Engineering", 60, 1)
VIKRAM = item(1, "EMP0002", "Vikram Shah", "Engineering", 60, 1)
NEHA = item(3, "EMP0003", "Neha Iyer", "Sales", 50, 1)
ROHIT = item(4, "EMP0004", "Rohit Das", "Sales", 45, 1)

print("\n--- ranking ---")
check_items("T1 2026-10 default limit: ranks 1,1,3,4", "/analytics/leaderboard/late?month=2026-10", [ASHA, VIKRAM, NEHA, ROHIT])
check_items("T2 limit=1: tie at rank 1 returns 2 rows", "/analytics/leaderboard/late?month=2026-10&limit=1", [ASHA, VIKRAM])
check_items("T3 limit=2: rank 2 does not exist, still 2 rows", "/analytics/leaderboard/late?month=2026-10&limit=2", [ASHA, VIKRAM])
check_items("T4 limit=3: ranks 1,1,3", "/analytics/leaderboard/late?month=2026-10&limit=3", [ASHA, VIKRAM, NEHA])

print("\n--- department filter (ranked inside the department) ---")
check_items(
    "T5 Sales: ranks 1,2",
    "/analytics/leaderboard/late?month=2026-10&department=Sales",
    [item(1, "EMP0003", "Neha Iyer", "Sales", 50, 1), item(2, "EMP0004", "Rohit Das", "Sales", 45, 1)],
)
check_items("T6 Engineering: tie, ranks 1,1", "/analytics/leaderboard/late?month=2026-10&department=Engineering", [ASHA, VIKRAM])
check_items("T7 Support: nobody late", "/analytics/leaderboard/late?month=2026-10&department=Support", [])

print("\n--- other months ---")
check_items(
    "T8 2026-07 (seed data: only EMP0003 late)",
    "/analytics/leaderboard/late?month=2026-07",
    [item(1, "EMP0003", "Neha Iyer", "Sales", 35, 1)],
)
check_items("T9 2026-06 (no logs)", "/analytics/leaderboard/late?month=2026-06", [])

print("\n--- validation ---")
check_status("V1 month missing", "/analytics/leaderboard/late", 422)
check_status("V2 month=2026-13", "/analytics/leaderboard/late?month=2026-13", 422)
check_status("V3 limit=0", "/analytics/leaderboard/late?month=2026-10&limit=0", 422)
check_status("V4 limit=51", "/analytics/leaderboard/late?month=2026-10&limit=51", 422)
check_status("V5 limit=abc", "/analytics/leaderboard/late?month=2026-10&limit=abc", 422)

print(f"\nPASSED: {passed}   FAILED: {failed}")