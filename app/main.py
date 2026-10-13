"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone,time
from typing import Optional,Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException,Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from pymongo import MongoClient,ReturnDocument
from pymongo.errors import DuplicateKeyError
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]
db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str,date_str:Optional[str]=None) -> int:
    h, m = map(int, shift_start.split(":"))
    if date_str:
        start=datetime.combine(datetime.fromisoformat(date_str).date(),time(h,m),tzinfo=IST)
    else:
        start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - start).total_seconds() / 60)
    second=int((punch_in - start).total_seconds())
    return minutes if second>600 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) ->float:
    hours=(punch_out - punch_in).total_seconds()/3600
    d_hours = Decimal(str(hours)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(d_hours)

def compute_overtime(punch_out: datetime, shift_end: str,shift_start:str, date_str: str) -> int:
    sh, sm = map(int, shift_start.split(":"))
    eh,em=map(int,shift_end.split(":"))
    start_total = sh * 60 + sm
    end_total =eh * 60 + em
    attendance_date = datetime.fromisoformat(date_str).date()
    if end_total <= start_total:
        end_date = attendance_date + timedelta(days=1)
    else:
        end_date = attendance_date
    end_dt = datetime.combine(end_date, time(eh, em), tzinfo=IST)
    punch_out_clean = punch_out.replace(microsecond=0)
    minutes = max(0, int((punch_out_clean - end_dt).total_seconds() / 60))
    return minutes if minutes >= 30 else 0


def to_ms(dt):
    return None if dt is None else int(dt.timestamp()*1000)

def from_ms(ms:int)->datetime:
    return datetime.fromtimestamp(ms/1000,tz=IST).replace(microsecond=0)
def from_ms(ms:int)->datetime:
    return datetime.fromtimestamp(ms/1000,tz=IST).replace(microsecond=0)
def attendance_date(ts:datetime, shift_start:str,shift_end:str)->str:
    sh,sm=map(int,shift_start.split(":"))
    eh,em=map(int,shift_end.split(":"))
    local=ts.astimezone(IST)
    d=local.date()
    overnight=(eh,em)<=(sh,sm)
    if overnight and local.replace(tzinfo=None).time()<time(eh,em):
        d=d-timedelta(days=1)
    return d.isoformat()

def record_out(rec:dict)->dict:
    history=[]
    for h in rec.get("history",[]):
        changes={}
        for k,v in h["changes"].items():
            if k in ("punch_in","punch_out"):
                changes[k]={"from":to_ms(v["from"]),"to":to_ms(v["to"])}
            else:
                changes[k]=v
        history.append({"at":to_ms(h["at"]),"by":h["by"],"reason":h["reason"],"changes":changes})
    return{
        "emp_code":rec["emp_code"],
        "date":rec["date"],
        "status":rec["status"],
        "punch_in":to_ms(rec.get("punch_in")),
        "punch_out":to_ms(rec.get("punch_out")),
        "work_hours":rec.get("work_hours"),
        "late_minutes":rec.get("late_minutes",0),
        "overtime_minutes":rec.get("overtime_minutes",0),
        "half_day":rec.get("half_day",False),
        "history":history,
    }

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = Field(None, ge=100000000000, le=4102444800000)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

class PunchOutIn(BaseModel):
    emp_code:str
    punched_at:Optional[StrictInt]=Field(None, ge=100000000000, le=4102444800000)

class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status:Optional[Literal["PRESENT","WFH","ON_DUTY","ABSENT","LEAVE"]]=None
    punch_in:Optional[StrictInt]=Field(None,ge=100000000000, le=4102444800000)
    punch_out:Optional[StrictInt]=Field(None,ge=100000000000, le=4102444800000)
    reason:str=Field(min_length=5,max_length=200)
    regularized_by:str=Field(min_length=1,max_length=50)

# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
      page: int =Query(1,ge=1),
      page_size: int=Query(20,ge=1,le=100),
):
    q = {}
    if department:
        q["department"] = department
    skip = (page-1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code",1).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404,"employee not found") 
    ts = (datetime.fromtimestamp(body.punched_at / 1000,tz=IST)
    if body.punched_at is not None
    else datetime.now(IST)
    ).replace(microsecond=0)
    d=attendance_date(ts,emp["shift_start"],emp["shift_end"])
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"],d),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        res = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409,"already punched in for this date")
    return record_out(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp=db.employees.find_one({"emp_code":body.emp_code})
    if not emp:
        raise HTTPException(404,"employee not found")

    ts=(
        datetime.fromtimestamp(body.punched_at/1000,tz=IST)
        if body.punched_at is not None
        else datetime.now(IST)
    ).replace(microsecond=0)

    rec=db.attendance_logs.find_one(
        {"emp_code":body.emp_code,"punch_in":{"$lte":ts}},
        sort=[("punch_in",-1)],
    )

    if not rec:
        raise HTTPException(404,"no punch-in found")
    if rec["punch_out"] is not None:
        raise HTTPException(409,"already punched out")

    if ts<=rec["punch_in"] or (ts-rec["punch_in"])>timedelta(hours=24):
        raise HTTPException(422,"punched_at must be after punch in and  within 24 hours")
    work_hours=compute_work_hours(rec["punch_in"],ts)
    overtime=compute_overtime(ts,emp["shift_end"],emp["shift_start"],rec["date"])
    half_day=work_hours<4.5

    updated=db.attendance_logs.find_one_and_update(
        {"_id":rec["_id"],"punch_out":None},
        {"$set": {
            "punch_out":ts,
            "work_hours":work_hours,
            "overtime_minutes":overtime,
            "half_day":half_day,
        }},
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(409,"already punched out")

    return record_out(updated)

@app.patch("/attendance/{emp_code}/{date}")
def regularize(emp_code:str,date:str,body:RegularizeIn):
    try:
        datetime.strptime(date,"%Y-%m-%d")
    except ValueError:
        raise HTTPException(422,"date must be YYYY_MM_DD")

    emp=db.employees.find_one({"emp_code":emp_code})
    if not emp:
        raise HTTPException(404,"no record for this date")
    rec=db.attendance_logs.find_one({"emp_code":emp_code,"date":date})
    if not rec:
        raise HTTPException(404,"no record for this date")
    
    old_in,old_out=rec.get("punch_in"),rec.get("punch_out")
    new_status=body.status or rec["status"]

    if new_status in ("ABSENT","LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422,"ABSENT/LEAVE cannot have punch times")
        new_in,new_out=None,None
    else:
        new_in=from_ms(body.punch_in) if body.punch_in is not None else old_in
        new_out=from_ms(body.punch_out) if body.punch_out is not None else old_out

        if new_in is None:
            raise HTTPException(422,"present status needs a punch_in")
        if attendance_date(new_in,emp["shift_start"],emp["shift_end"])!=date:
            raise HTTPException (422,"punch_in must stay on the record's date")
        if new_out is not None and (new_out<=new_in or (new_out-new_in)>timedelta(hours=24)):
            raise HTTPException(422,"punch_out must be after punch_in and within 24 hours")

    late=compute_late_minutes(new_in,emp["shift_start"],date) if new_in else 0
    if new_out is not None:
        work_hours=compute_work_hours(new_in,new_out)
        overtime=compute_overtime(new_out,emp["shift_end"],emp["shift_start"],date)
        half_day=work_hours<4.5
    else:
        work_hours,overtime,half_day=None,0,False

    new_vals={
        "status":new_status,
        "punch_in":new_in,
        "punch_out":new_out,
        "work_hours":work_hours,
        "late_minutes":late,
        "overtime_minutes":overtime,
        "half_day":half_day,
    }
    old_vals={
        "status":rec["status"],
        "punch_in":old_in,
        "punch_out":old_out,
        "work_hours":rec.get("work_hours"),
        "late_minutes":rec.get("late_minutes",0),
        "overtime_minutes":rec.get("overtime_minutes",0),
        "half_day":rec.get("half_day",False),
    }

    changes={}
    for key in new_vals:
       if new_vals[key]!=old_vals[key]:
           changes[key]={"from":old_vals[key],"to":new_vals[key]}
    if not changes:
        raise HTTPException(422,"request changes nothing")

    entry={
        "at":datetime.now(IST).replace(microsecond=0),
        "by": body.regularized_by,
        "reason":body.reason,
        "changes":changes,
    }

    n=len(rec.get("history",[]))
    updated=db.attendance_logs.find_one_and_update(
        {
            "_id":rec["_id"],
            "$expr":{"$eq":[{"$size":{"$ifNull":["$history",[]]}},n]},
        },
        {"$set":new_vals,"$push":{"history":entry}},
        return_document=ReturnDocument.AFTER,
    )

    if updated is None:
        raise HTTPException(409,"record was modified concurrently,retry")
    return record_out(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
