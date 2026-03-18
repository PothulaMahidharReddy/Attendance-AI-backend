import os
import json
import re
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Optional, List, Any, Annotated, Dict
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Body, status
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, ConfigDict, BeforeValidator, PlainSerializer, WithJsonSchema
from dotenv import load_dotenv
from groq import Groq
from bson import ObjectId

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Environment & Configuration ──────────────────────────────────
load_dotenv()

app = FastAPI(title="Biometric Attendance Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ SECURE: Load only from .env (NO hardcoded secrets)
MONGO_URL = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "Reports")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "biometricdatas")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# ✅ Safety check
if not MONGO_URL:
    raise ValueError("MONGO_URI not set in environment")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not set in environment")

try:
    mongo_client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    attendance_col = db[COLLECTION_NAME]
    groq_client = Groq(api_key=GROQ_API_KEY)
    logger.info(f"Connected to MongoDB: DB: {DB_NAME}, Collection: {COLLECTION_NAME}")
except Exception as e:
    logger.error(f"Error initializing clients: {e}")

IST = ZoneInfo("Asia/Kolkata")

# ── Helpers ──────────────────────────────────────────────────────

def to_ist(dt: datetime) -> datetime:
    if not isinstance(dt, datetime): return dt
    if dt.tzinfo is None: dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(IST)

def fmt_time_ist(dt) -> str:
    if not isinstance(dt, datetime): return "—"
    return to_ist(dt).strftime("%I:%M %p")

def fmt_date_ist(dt) -> str:
    if not isinstance(dt, datetime): return "—"
    return to_ist(dt).strftime("%Y-%m-%d")

def format_duration(minutes: int) -> str:
    if not minutes: return "0h 0m"
    return f"{minutes // 60}h {minutes % 60}m"

def serialize_doc(doc: dict) -> dict:
    login = doc.get("login")
    logout = doc.get("logout")
    date = doc.get("date")
    total_mins = doc.get("totalWorkedMinutes") or doc.get("total_worked_minutes") or doc.get("totalMinutes", 0)
    
    return {
        "id": str(doc.get("_id", "")),
        "userId": str(doc.get("userId", "")),
        "userName": doc.get("userName") or doc.get("employeeName") or "Unknown",
        "dateIST": fmt_date_ist(date),
        "loginIST": fmt_time_ist(login),
        "logoutIST": fmt_time_ist(logout),
        "status": doc.get("status", "present"),
        "totalWorkedMinutes": total_mins,
        "workDuration": format_duration(total_mins),
        "login": login.isoformat() if isinstance(login, datetime) else None,
        "logout": logout.isoformat() if isinstance(logout, datetime) else None,
        "date": date.isoformat() if isinstance(date, datetime) else None,
        "reason": doc.get("reason", "—"),
        "breakCount": len(doc.get("breaks", [])) if isinstance(doc.get("breaks"), list) else 0,
        "isOvernightShift": doc.get("isOvernightShift", False),
        "autoClosed": doc.get("autoClosed", False),
    }

def ist_to_utc_midnight(date_str: str) -> datetime:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return (dt - timedelta(days=1)).replace(hour=18, minute=30, second=0, microsecond=0, tzinfo=ZoneInfo("UTC"))

def resolve_mongo_types(obj):
    if isinstance(obj, dict):
        if "$date" in obj:
            ds = obj["$date"]
            if isinstance(ds, dict) and "$numberLong" in ds:
                return datetime.fromtimestamp(int(ds["$numberLong"]) / 1000, tz=ZoneInfo("UTC"))
            if isinstance(ds, str):
                if len(ds) >= 10 and re.match(r'^\d{4}-\d{2}-\d{2}', ds):
                    return ist_to_utc_midnight(ds[:10])
                return datetime.fromisoformat(ds.replace("Z", "+00:00"))
        if "$oid" in obj: return ObjectId(obj["$oid"])
        
        new_dict = {}
        for k, v in obj.items():
            if k == "date" and isinstance(v, str) and len(v) >= 10 and re.match(r'^\d{4}-\d{2}-\d{2}', v):
                mid = ist_to_utc_midnight(v[:10])
                new_dict[k] = {"$gte": mid - timedelta(minutes=10), "$lte": mid + timedelta(minutes=10)}
                continue
            
            if k in ["$gte", "$lte", "$gt", "$lt", "$eq"] and isinstance(v, str) and len(v) >= 10 and re.match(r'^\d{4}-\d{2}-\d{2}', v):
                new_dict[k] = ist_to_utc_midnight(v[:10])
            elif k in ["userId", "_id", "editedBy"] and isinstance(v, str) and ObjectId.is_valid(v):
                new_dict[k] = ObjectId(v)
            else:
                new_dict[k] = resolve_mongo_types(v)
        return new_dict
    elif isinstance(obj, list):
        return [resolve_mongo_types(i) for i in obj]
    elif isinstance(obj, str) and len(obj) >= 10 and re.match(r'^\d{4}-\d{2}-\d{2}', obj):
        return ist_to_utc_midnight(obj[:10])
    return obj

def extract_json(res_text: str) -> dict:
    match = re.search(r'(\{.*\})', res_text, re.DOTALL)
    if match:
        try: return json.loads(match.group(1))
        except: pass
    raise ValueError("AI response parse failed")

# ── DB Sync ──────────────────────────────────────────────────────

def run_db_sync_script():
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(current_dir, "script_DB.py")
        
        if os.path.exists(script_path):
            logger.info("Starting background DB Sync script...")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            
            subprocess.Popen(
                [sys.executable, script_path],
                cwd=current_dir,
                env=env,
                start_new_session=True 
            )
        else:
            logger.warning(f"DB Sync script NOT found at: {script_path}")
    except Exception as e:
        logger.error(f"Failed to start Sync script: {e}")

@app.on_event("startup")
async def startup_event():
    if os.environ.get("RUN_MAIN") != "true":
        run_db_sync_script()

# ── API ──────────────────────────────────────────────────────────

@app.get("/reports")
async def get_reports(type: str, date: str):
    try:
        mid = ist_to_utc_midnight(date)
        
        if type == "daily":
            start_date = mid
            end_date = mid + timedelta(days=1)
        elif type == "weekly":
            end_date = mid + timedelta(days=1)
            start_date = mid - timedelta(days=6)
        elif type == "monthly":
            end_date = mid + timedelta(days=1)
            start_date = mid - timedelta(days=29)
        else:
            raise HTTPException(status_code=400, detail="Invalid report type")

        # Range filter for the selected period
        filt = {"date": {"$gte": start_date, "$lt": end_date}}
        
        cursor = attendance_col.find(filt).sort("date", -1)
        records = await cursor.to_list(length=10000)
        
        serialized = [serialize_doc(r) for r in records]
        
        return {
            "records": serialized,
            "count": len(serialized),
            "period": type,
            "referenceDate": date
        }
    except Exception as e:
        logger.error(f"Report generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status")
async def get_status():
    try:
        count = await attendance_col.count_documents({})
        return {"status": "connected", "total_records": count}
    except:
        return {"status": "offline", "total_records": 0}

@app.post("/dashboard-summary")
async def get_dashboard_summary(request: dict = Body(...)):
    try:
        date_str = request.get("date", "").strip()
        mid = ist_to_utc_midnight(date_str)
        cursor = attendance_col.find({"date": {"$gte": mid - timedelta(minutes=10), "$lte": mid + timedelta(minutes=10)}})
        records = await cursor.to_list(length=1000)
        
        present_list, late_list, all_worked = [], [], []
        total_mins = 0

        for r in records:
            s_doc = serialize_doc(r)
            all_worked.append(s_doc)
            
            if r.get("status") == "present":
                present_list.append(s_doc)
            
            is_late = r.get("status") == "late" or (
                isinstance(r.get("login"), datetime) and 
                to_ist(r.get("login")).hour >= 9 and 
                to_ist(r.get("login")).minute > 30
            )
            if is_late:
                late_list.append(s_doc)
                
            total_mins += r.get("totalWorkedMinutes", 0)

        avg = f"{(total_mins / len(records) / 60):.1f}" if records else "0.0"
        
        return {
            "aiSummary": f"Report for {date_str}",
            "presentCount": len(present_list),
            "presentEmployees": present_list,
            "lateCount": len(late_list),
            "lateEmployees": late_list,
            "avgHoursYesterday": avg,
            "workedEmployees": all_worked
        }
    except Exception as e:
        logger.error(f"Dashboard err: {e}")
        return {"aiSummary": "Error loading metrics"}

@app.post("/query")
async def natural_language_query(request: dict = Body(...)):
    try:
        q_text = request.get("query", "")
        today = datetime.now(IST).strftime("%Y-%m-%d")
        
        prompt = f"Building MongoDB query for: {q_text}\nToday is: {today}\nReturn JSON with filter and sort."
        
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        
        meta = extract_json(completion.choices[0].message.content)
        filt = resolve_mongo_types(meta.get("filter", {}))
        
        cursor = attendance_col.find(filt).sort(list(meta.get("sort", {"login": -1}).items())).limit(5000)
        data = await cursor.to_list(length=5000)
        
        return {
            "records": [serialize_doc(d) for d in data],
            "count": len(data)
        }
    except Exception as e:
        raise HTTPException(500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    run_db_sync_script()
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)