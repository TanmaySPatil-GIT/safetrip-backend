from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import engine, Base
from app.models.models import User, AuthorityUser, Trip, LocationPing, DangerZone, RiskFactor, ZonePhoto, Alert, TripGroup, GroupAlert, TripFeedback
from app.core.config import settings

from app.routes.auth import router as auth_router
from app.routes.trips import router as trips_router, alerts_router

# Create database tables
Base.metadata.create_all(bind=engine)

from sqlalchemy import inspect

def check_and_add_checkin_columns():
    from sqlalchemy import text
    inspector = inspect(engine)
    
    # Get columns for trips table
    columns = [col["name"] for col in inspector.get_columns("trips")]
    
    with engine.connect() as conn:
        if "checkin_interval_hours" not in columns:
            conn.execute(text("ALTER TABLE trips ADD COLUMN checkin_interval_hours FLOAT"))
            print("[DB] Added checkin_interval_hours column to trips table")
        if "last_checkin_at" not in columns:
            conn.execute(text("ALTER TABLE trips ADD COLUMN last_checkin_at TIMESTAMP"))
            print("[DB] Added last_checkin_at column to trips table")
        if "region_lat" not in columns:
            conn.execute(text("ALTER TABLE trips ADD COLUMN region_lat FLOAT"))
            print("[DB] Added region_lat column to trips table")
        if "region_lng" not in columns:
            conn.execute(text("ALTER TABLE trips ADD COLUMN region_lng FLOAT"))
            print("[DB] Added region_lng column to trips table")
        
        # Check alerts table for dispatch_notes column
        alert_columns = [col["name"] for col in inspector.get_columns("alerts")]
        if "dispatch_notes" not in alert_columns:
            conn.execute(text("ALTER TABLE alerts ADD COLUMN dispatch_notes TEXT"))
            print("[DB] Added dispatch_notes column to alerts table")
            
        # Check users table for preferred_language column
        user_columns = [col["name"] for col in inspector.get_columns("users")]
        if "preferred_language" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN preferred_language VARCHAR DEFAULT 'en' NOT NULL"))
            print("[DB] Added preferred_language column to users table")
            
        conn.commit()

check_and_add_checkin_columns()

app = FastAPI(title="SafeTrip API", version="1.0.0")

# Configure CORS for Reflex and external connections
origins = ["https://safetrip-reflex-backend.onrender.com"]
if settings.DEMO_MODE:
    origins.extend([
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ])

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.staticfiles import StaticFiles

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth_router, prefix="/api")
app.include_router(trips_router, prefix="/api")
app.include_router(alerts_router, prefix="/api")

from apscheduler.schedulers.background import BackgroundScheduler
from app.routes.trips import check_missed_checkins
from app.core.config import settings

scheduler = BackgroundScheduler()
scheduler.add_job(check_missed_checkins, "interval", minutes=settings.CHECKIN_SCHEDULER_INTERVAL_MINUTES, id="missed_checkins_job")

@app.on_event("startup")
def start_scheduler():
    scheduler.start()
    print("[SCHEDULER] Background scheduler started successfully.")

@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown()
    print("[SCHEDULER] Background scheduler stopped successfully.")

@app.get("/")
def read_root():
    return {"message": "SafeTrip API is running successfully!"}
