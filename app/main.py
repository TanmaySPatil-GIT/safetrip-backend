from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.database import engine, Base
from app.models.models import User, AuthorityUser, Trip, LocationPing, DangerZone, RiskFactor, ZonePhoto, Alert, TripGroup, GroupAlert, TripFeedback

from app.routes.auth import router as auth_router
from app.routes.trips import router as trips_router, alerts_router

# Create database tables
Base.metadata.create_all(bind=engine)

def check_and_add_checkin_columns():
    from sqlalchemy import text
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(trips)"))
        columns = [row[1] for row in result.fetchall()]
        if "checkin_interval_hours" not in columns:
            conn.execute(text("ALTER TABLE trips ADD COLUMN checkin_interval_hours FLOAT"))
            print("[DB] Added checkin_interval_hours column to trips table")
        if "last_checkin_at" not in columns:
            conn.execute(text("ALTER TABLE trips ADD COLUMN last_checkin_at DATETIME"))
            print("[DB] Added last_checkin_at column to trips table")
        
        # Check alerts table for dispatch_notes column
        alert_result = conn.execute(text("PRAGMA table_info(alerts)"))
        alert_columns = [row[1] for row in alert_result.fetchall()]
        if "dispatch_notes" not in alert_columns:
            conn.execute(text("ALTER TABLE alerts ADD COLUMN dispatch_notes TEXT"))
            print("[DB] Added dispatch_notes column to alerts table")
            
        # Check users table for preferred_language column
        user_result = conn.execute(text("PRAGMA table_info(users)"))
        user_columns = [row[1] for row in user_result.fetchall()]
        if "preferred_language" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN preferred_language VARCHAR DEFAULT 'en' NOT NULL"))
            print("[DB] Added preferred_language column to users table")
            
        conn.commit()

check_and_add_checkin_columns()

app = FastAPI(title="SafeTrip API", version="1.0.0")

# Configure CORS for Reflex and external connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
