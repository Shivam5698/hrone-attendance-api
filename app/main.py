
import os
import calendar
import json
import re
from bson import json_util
from datetime import datetime, timedelta, timezone,time
from typing import Optional,Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException,Query,Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from pymongo import MongoClient,ReturnDocument
from pymongo.errors import DuplicateKeyError
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"),tz_aware=True)
db = client[os.getenv("MONGO_DB", "attendance_db")]
db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
db.attendance_logs.create_index([("date",-1),("emp_code",1)])
db.attendance_logs.create_index([("status",1),("date",-1),("emp_code",1)])
db.employees.create_index("emp_code",unique=True)
db.employees.create_index([("department",1),("emp_code",1)])
db.employees.create_index([("joined_on",1)])

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

def employee_out(e:dict)->dict:
    return {
        "emp_code":e["emp_code"],
        "name":e["name"],
        "email":e["email"],
        "department": e["department"],
        "shift_start": e["shift_start"],
        "shift_end": e["shift_end"],
        "joined_on": e["joined_on"],
        "created_at": to_ms(e.get("created_at")),
    }

def round_half_up(x,places:int=2)->float:
    q=Decimal(1).scaleb(-places)
    return float(Decimal(str(x)).quantize(q,rounding=ROUND_HALF_UP))

def count_working_days(month:str,joined_on:str)->int:
    year,mon=map(int,month.split("-"))
    last_day=calendar.monthrange(year,mon)[1]
    start=datetime(year,mon,1).date()
    joined=datetime.fromisoformat(joined_on).date()
    if joined>start:
        start=joined
    end=datetime(year,mon,last_day).date()
    count=0
    d=start
    while d<=end:
        if d.weekday()<5:
            count+=1
        d+=timedelta(days=1)
    return count

def employee_monthly_pipeline(emp_code:str,month:str)->list:
    return[
        {"$match":{
            "emp_code":emp_code,
            "date":{"$gte":f"{month}-01","$lte":f"{month}-31"},

        }},
       {"$addFields": {
          "dow": {"$dayOfWeek": {"$dateFromString": {"dateString": "$date", "format": "%Y-%m-%d"}}},
       }},
        {"$group":{
            "_id":None,
            "present_days":{"$sum":{"$cond":[
                {"$and":[
                   {"$in":["$status",["PRESENT","WFH","ON_DUTY"]]},
                   {"$in":["$dow",[2,3,4,5,6]]},   
                ]},
                {"$cond":[{"$ifNull":["$half_day",False]},0.5,1]},
                0,
            ]}},
            "leave_days":{"$sum":{"$cond":[{"$eq":["$status","LEAVE"]},1,0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
            "total_late_minutes":{"$sum":{"$ifNull":["$late_minutes",0]}},
            "total_overtime_minutes":{"$sum":{"$ifNull":["$overtime_minutes",0]}},
        }},
    ]

def department_summary_pipeline(month:str,department:Optional[str]=None)->list:
    match={"joined_on":{"$lte":f"{month}-31"}}
    if department:
        match["department"]=department
    is_present={"$in":["$logs.status",["PRESENT","WFH","ON_DUTY"]]}
    has_hours={"$and":[is_present,{"$isNumber":"$logs.work_hours"}]}
    return [
        {"$match":match},
        {"$lookup":{
            "from":"attendance_logs",
            "localField":"emp_code",
            "foreignField":"emp_code",
            "pipeline":[
                {"$match":{"date":{"$gte":f"{month}-01","$lte":f"{month}-31"}}},
            ],
            "as":"logs",
        }},
        {"$unwind":{"path":"$logs","preserveNullAndEmptyArrays":True}},
        {"$addFields":{
            "dow":{"$dayOfWeek":{"$dateFromString":{"dateString":"$logs.date","format":"%Y-%m-%d"}}},
        }},
        {"$group":{
            "_id":"$department",
            "emp_codes":{"$addToSet":"$emp_code"},
            "present_days":{"$sum":{"$cond":[
                {"$and":[is_present,{"$in":["$dow",[2,3,4,5,6]]}]},
                {"$cond":[{"$ifNull":["$logs.half_day",False]},0.5,1]},
                0,
            ]}},
            "work_sum":{"$sum":{"$cond":[has_hours,"$logs.work_hours",0]}},
            "work_n":{"$sum":{"$cond":[has_hours,1,0]}},
            "late_count":{"$sum":{"$cond":[{"$gt":[{"$ifNull":["$logs.late_minutes",0]},0]},1,0]}},
            "total_late_minutes":{"$sum":{"$ifNull":["$logs.late_minutes",0]}},
            "leave_count":{"$sum":{"$cond":[{"$eq":["$logs.status","LEAVE"]},1,0]}},
            "on_duty_count":{"$sum":{"$cond":[{"$eq":["$logs.status","ON_DUTY"]},1,0]}},
        }},
        {"$project":{
            "_id":0,
            "department":"$_id",
            "headcount": {"$size": "$emp_codes"},      
            "present_days":1,
            "avg_work_hours":{"$cond":[{"$gt":["$work_n",0]},{"$divide":["$work_sum","$work_n"]},None]},
            "late_count":1,
            "total_late_minutes":1,
            "leave_count":1,
            "on_duty_count":1,
        }},
        {"$sort":{"department":1}},
    ]

def late_leaderboard_pipeline(month: str, limit: int, department: Optional[str] = None) -> list:
    pipeline = [
        {"$match": {"date": {"$gte": f"{month}-01", "$lte": f"{month}-31"}}},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "emp"}},
        {"$unwind": "$emp"},
    ]
    if department:
        pipeline.append({"$match": {"emp.department": department}})
    pipeline += [
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {
            "_id": 0,
            "rank": 1,
            "emp_code": "$_id",
            "name": "$emp.name",
            "department": "$emp.department",
            "total_late_minutes": 1,
            "late_count": 1,
        }},
    ]
    return pipeline

STATUS_VALUES=("PRESENT","WFh","ON_DUTY","ABSENT","LEAVE")

def attendance_filter(emp_code,date_from,date_to,status)->dict:
    q={}
    if emp_code:
        q["emp_code"]=emp_code
    if date_from or date_to:
        q["date"]={}
        if date_from:
            q["date"]["$gte"]=date_from
        if date_to:
            q["date"]["$lte"]=date_to
    if status:
        q["status"]=status
    return q

def int_param(params,name:str,default:int,low:int,high:int)->int:
    try:
        value=int(params.get(name,default))
    except ValueError:
        raise HTTPException(422,f"{name} must be an integer")
    if not low<=value <=high:
        raise HTTPException(422,f"{name} must be between {low} and {high}")
    return value



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str=Field(pattern=r"^EMP\d{4,6}$")
    name: str=Field(min_length=1, max_length=100)
    email: str=Field(max_length=120,pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str=Field(min_length=1,max_length=50)
    shift_start: str =Field("09:30",pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field("18:30",pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str=Field(pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def check_fields(self):
        if self.shift_start==self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        datetime.strptime(self.joined_on,"%Y-%m-%d")
        return self
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
    doc=body.model_dump()
    doc["created_at"]=datetime.now(IST).replace(microsecond=0)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409,"emp_code already exists")
    return employee_out(doc)

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
    items =[
        employee_out(e)
        for e in db.employees.find(q).sort("emp_code",1).skip(skip).limit(page_size)
    ]
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
    date_from: Optional[str] = Query(None,pattern=r"^\d{4}-\d{2}$"),
    date_to: Optional[str] =Query(None,pattern=r"^\d{4}-\d{2}-\d{2}$"),
    status: Optional[Literal["PRESENT","WFH","ON_DUTY","ABSENT","LEAVE"]] = None,
    page: int = Query(1,ge=1),
    page_size: int = Query(20,ge=1,le=100),
):
    if date_from and date_to and date_from>date_to:
        raise HTTPException(422,"date_from must not be after date_to")
    
    q=attendance_filter(emp_code,date_from,date_to,status)

    total=db.attendance_logs.count_documents(q)
    cursor=(
        db.attendance_logs.find(q)
        .sort([("date",-1),("emp_code",1)])
        .skip((page-1)*page_size)
        .limit(page_size)
    )
    return{
        "items":[record_out(d) for d in cursor],
        "total":total,
        "page":page,
        "page_size":page_size,
    }

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code:str,
    month:str=Query(...,pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    emp=db.employees.find_one({"emp_code":emp_code})
    if not emp:
        raise HTTPException(404,"employee not found")
    working_days=count_working_days(month,emp["joined_on"])
    rows=list(db.attendance_logs.aggregate(employee_monthly_pipeline(emp_code,month)))
    r=rows[0] if rows else {}

    present_days=r.get("present_days",0)
    attendance_pct=(
        None if working_days==0
        else round_half_up(Decimal(str(present_days))*100/working_days)
    )

    return {
        "emp_code":emp_code,
        "month":month,
        "working_days":working_days,
        "present_days":present_days,
        "leave_days":r.get("leave_days",0),
        "late_count":r.get("late_count",0),
        "total_late_minutes":r.get("total_late_minutes",0),
        "total_overtime_minutes":r.get("total_ovetime_minutes",0),
        "attendance_pct":attendance_pct,
    }

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint:Literal["attendance_list","employee_monthly","department_summary","late_leaderboard","department_trend"],\
    request:Request,
):
    p=request.query_params

    if endpoint=="attendance_list":
        date_from,date_to,status=p.get("date_from"),p.get("date_to"),p.get("status")
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422,"date_from must not be after date_to")
        if status and status not in STATUS_VALUES:
            raise HTTPException(422,"invalid status")
        page=int_param(p,"page",1,1,10**9)
        page_size=int_param(p,"page_size",20,1,100)
        collection="attendance_logs"

        cmd={
            "find":collection,
            "filter":attendance_filter(p.get("emp_code"),date_from,date_to,status),
            "sort":{"date":-1,"emp_code":1},
            "skip":(page-1)*page_size,
            "limit":page_size,
        }
    elif endpoint=="employee_monthly":
        emp_code,month=p.get("emp_code"),p.get("month")
        if not emp_code or not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])",month):
            raise HTTPException(422,"emp_code and month (YYYY-MM) are required")
        collection="attendance_logs"
        cmd={
            "aggregate":collection,
            "pipeline":employee_monthly_pipeline(emp_code,month),
            "cursor":{},
        }
    elif endpoint=="department_summary":
        month=p.get("month")
        if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])",month):
            raise HTTPException(422,"month (YYYY-MM) is required")
        collection="employees"
        cmd={
            "aggregate":collection,
            "pipeline":department_summary_pipeline(month,p.get("department")),
            "cursor":{},
         }
    elif endpoint == "late_leaderboard":
        month = p.get("month")
        if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(422, "month (YYYY-MM) is required")
        limit = int_param(p, "limit", 10, 1, 50)
        collection = "attendance_logs"
        cmd = {
            "aggregate": collection,
            "pipeline": late_leaderboard_pipeline(month, limit, p.get("department")),
            "cursor": {},
        }
    
    else:
        raise HTTPException(501,f"explain for {endpoint} is not implemented")
    result = db.command({"explain": cmd, "verbosity": "executionStats"})
    return{
        "endpoint":endpoint,
        "collection":collection,
        "explain":json.loads(json_util.dumps(result)),
    }

@app.get("/analytics/departments/summary")
def department_summary(
    month:str=Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department:Optional[str]=None,
):
    rows=list(db.employees.aggregate(department_summary_pipeline(month,department)))
    for r in rows:
        if r["avg_work_hours"] is not None:
            r["avg_work_hours"]=round_half_up(r["avg_work_hours"])
    return {"month":month,"items":rows}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    rows = list(db.attendance_logs.aggregate(late_leaderboard_pipeline(month, limit, department)))
    return {"month": month, "items": rows} 


