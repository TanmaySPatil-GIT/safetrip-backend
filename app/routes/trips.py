import datetime
import json
import logging
import random
import os
import requests
import shutil
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, File, Form, UploadFile, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.dependencies import get_current_tourist, get_current_authority, get_current_user_data, get_current_authority_for_export
from app.models.models import User, Trip, LocationPing, DangerZone, Alert, RiskFactor, AuthorityUser, ZonePhoto, TripGroup, GroupAlert, TripFeedback
from app.core.safety import is_point_in_polygon, evaluate_distress

router = APIRouter(prefix="/trips", tags=["trips"])

# ---------------------------------------------------------------------------
# SIMULATED INTEGRATION POINT — This webhook represents a future connection
# to a real police/emergency dispatch API (e.g. 112 India emergency services).
# It is NOT connected to any live system.
# ---------------------------------------------------------------------------
WEBHOOK_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "webhook_log.txt")
logger = logging.getLogger("safetrip.webhook")


def dispatch_webhook_for_alert(alert: Alert, db: Session):
    """
    SIMULATED INTEGRATION POINT — This webhook represents a future connection
    to a real police/emergency dispatch API (e.g. 112 India emergency services).
    It is NOT connected to any live system.

    Assembles the alert payload with zone risk_score (if available) and writes
    it to webhook_log.txt.  In production this would POST to an external
    dispatch API instead.
    """
    risk_score = None
    zone_name = None
    try:
        zones = db.query(DangerZone).all()
        for zone in zones:
            if is_point_in_polygon(alert.lat, alert.lng, zone.polygon_coordinates):
                risk_score = zone.computed_risk_score
                zone_name = zone.name
                break
    except Exception as e:
        logger.warning("Could not look up zone risk for webhook: %s", e)

    payload = {
        "alert_id": alert.id,
        "trip_id": alert.trip_id,
        "type": alert.type,
        "lat": alert.lat,
        "lng": alert.lng,
        "timestamp": alert.timestamp.isoformat() if alert.timestamp else None,
        "risk_score": risk_score,
        "zone_name": zone_name,
    }

    timestamp = datetime.datetime.utcnow().isoformat()
    entry = {"received_at": timestamp, "payload": payload}
    line = json.dumps(entry)

    try:
        log_path = os.path.normpath(WEBHOOK_LOG_FILE)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        logger.info("[WEBHOOK DISPATCHED] %s", line)
        print(f"[WEBHOOK] Dispatched alert {alert.id} → logged to webhook_log.txt")
    except Exception as e:
        logger.warning("Webhook log write failed (non-fatal): %s", e)


def create_group_alerts_for_alert(alert: Alert, db: Session):
    trip = alert.trip
    if not trip:
        return
    user_id = trip.user_id

    # Find active groups containing this user
    groups = db.query(TripGroup).all()
    for group in groups:
        if user_id in group.members:
            # Verify creator's trip is still active
            creator_trip = db.query(Trip).filter(Trip.id == group.trip_id).first()
            if creator_trip and creator_trip.status == "active":
                for member_id in group.members:
                    # Create a group alert for each member
                    existing = db.query(GroupAlert).filter(
                        GroupAlert.alert_id == alert.id,
                        GroupAlert.member_id == member_id
                    ).first()
                    if not existing:
                        group_alert = GroupAlert(
                            group_id=group.id,
                            alert_id=alert.id,
                            member_id=member_id,
                            status="open"
                        )
                        db.add(group_alert)
                db.commit()



# Pydantic Schemas
class TripCreate(BaseModel):
    start_date: datetime.datetime = Field(..., description="Start date and time of the trip")
    end_date: datetime.datetime = Field(..., description="Estimated end date and time of the trip")
    region: str = Field(..., description="Target region/route name for the trip")
    checkin_interval_hours: Optional[float] = Field(None, description="Check-in interval in hours")
    region_lat: Optional[float] = Field(None, description="Latitude of target destination")
    region_lng: Optional[float] = Field(None, description="Longitude of target destination")

class TripResponse(BaseModel):
    id: int
    user_id: int
    start_date: datetime.datetime
    end_date: datetime.datetime
    region: str
    status: str
    created_at: datetime.datetime
    auto_delete_at: datetime.datetime
    checkin_interval_hours: Optional[float] = None
    last_checkin_at: Optional[datetime.datetime] = None
    region_lat: Optional[float] = None
    region_lng: Optional[float] = None

    class Config:
        from_attributes = True

class LocationPingCreate(BaseModel):
    lat: float = Field(..., description="Latitude of the ping")
    lng: float = Field(..., description="Longitude of the ping")
    timestamp: Optional[datetime.datetime] = Field(None, description="Time of the ping (defaults to UTC now)")

class LocationPingResponse(BaseModel):
    id: int
    trip_id: int
    lat: float
    lng: float
    timestamp: datetime.datetime

    class Config:
        from_attributes = True

class PingIngestResponse(BaseModel):
    status: str
    alerts_triggered: List[str]

class GroupJoinRequest(BaseModel):
    join_code: str

class GroupResponse(BaseModel):
    id: int
    join_code: str
    trip_id: int
    members: List[int]

    class Config:
        from_attributes = True

class GroupMemberResponse(BaseModel):
    user_id: int
    phone_number: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    timestamp: Optional[datetime.datetime] = None
    color: str

class GroupAlertDetailResponse(BaseModel):
    id: int
    trip_id: int
    type: str
    lat: float
    lng: float
    timestamp: datetime.datetime
    status: str
    phone_number: str

class GroupAlertResponseItem(BaseModel):
    id: int
    group_id: int
    alert_id: int
    member_id: int
    status: str
    created_at: datetime.datetime
    alert: GroupAlertDetailResponse

    class Config:
        from_attributes = True

class GroupAlertActionRequest(BaseModel):
    action: str

class BriefingRequest(BaseModel):
    region: str
    lat: Optional[float] = None
    lng: Optional[float] = None

class BriefingZoneInfo(BaseModel):
    name: str
    risk_score: float
    risk_level: str
    photo_url: Optional[str] = None
    hazard_type: Optional[str] = None
    avoid_caption: Optional[str] = None

class BriefingWeatherInfo(BaseModel):
    temp: float
    condition: str
    rainfall_status: str
    is_warning: bool

class BriefingResponse(BaseModel):
    region: str
    danger_zones: List[BriefingZoneInfo]
    weather: BriefingWeatherInfo
    safety_tips: List[str]
    safe_hours: str
    warnings: List[str]
    destination_photo_url: Optional[str] = None



# Endpoints
@router.post("/start", response_model=TripResponse, status_code=status.HTTP_201_CREATED)
def start_trip(
    trip_data: TripCreate,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    # Check if there is an existing active trip for this user
    active_trip = db.query(Trip).filter(Trip.user_id == current_user.id, Trip.status == "active").first()
    if active_trip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have an active trip. End it before starting a new one."
        )

    # auto_delete_at is 24 hours (1 day) after end_date
    auto_delete_at = trip_data.end_date + datetime.timedelta(days=1)

    new_trip = Trip(
        user_id=current_user.id,
        start_date=trip_data.start_date,
        end_date=trip_data.end_date,
        region=trip_data.region,
        status="active",
        auto_delete_at=auto_delete_at,
        checkin_interval_hours=trip_data.checkin_interval_hours,
        last_checkin_at=trip_data.start_date,
        region_lat=trip_data.region_lat,
        region_lng=trip_data.region_lng
    )
    db.add(new_trip)
    db.commit()
    db.refresh(new_trip)
    return new_trip

@router.post("/{trip_id}/end", response_model=TripResponse)
def end_trip(
    trip_id: int,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    trip = db.query(Trip).filter(Trip.id == trip_id, Trip.user_id == current_user.id).first()
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found"
        )
    if trip.status == "ended":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Trip has already ended"
        )

    trip.status = "ended"
    # Update end_date to current time if ended earlier than planned
    now = datetime.datetime.utcnow()
    if trip.end_date > now:
        trip.end_date = now
    # Update auto_delete_at to be 24 hours from now
    trip.auto_delete_at = now + datetime.timedelta(days=1)

    db.commit()
    db.refresh(trip)
    return trip

@router.get("/active", response_model=TripResponse)
def get_active_trip(
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    trip = db.query(Trip).filter(Trip.user_id == current_user.id, Trip.status == "active").first()
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active trip found for current user"
        )
    return trip

@router.post("/{trip_id}/ping", response_model=PingIngestResponse)
def ingest_location_ping(
    trip_id: int,
    ping_data: LocationPingCreate,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    trip = db.query(Trip).filter(Trip.id == trip_id, Trip.user_id == current_user.id).first()
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Trip not found"
        )
    if trip.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot ingest location pings for an inactive trip"
        )

    ping_time = ping_data.timestamp or datetime.datetime.utcnow()
    new_ping = LocationPing(
        trip_id=trip_id,
        lat=ping_data.lat,
        lng=ping_data.lng,
        timestamp=ping_time
    )
    db.add(new_ping)
    db.commit()
    db.refresh(new_ping)

    alerts_triggered = []

    # 1. Geofence checks
    zones = db.query(DangerZone).all()
    for zone in zones:
        if is_point_in_polygon(ping_data.lat, ping_data.lng, zone.polygon_coordinates):
            # Check if there is already an open geofence alert for this trip
            # To avoid spamming, we only create a new alert if no open alert of type 'geofence' exists for this trip/lat/lng
            existing_geofence_alert = db.query(Alert).filter(
                Alert.trip_id == trip_id,
                Alert.type == "geofence",
                Alert.status == "open"
            ).first()
            if not existing_geofence_alert:
                new_alert = Alert(
                    trip_id=trip_id,
                    type="geofence",
                    lat=ping_data.lat,
                    lng=ping_data.lng,
                    status="open",
                    timestamp=ping_time
                )
                db.add(new_alert)
                db.commit()
                db.refresh(new_alert)
                dispatch_webhook_for_alert(new_alert, db)
                alerts_triggered.append("geofence")
            break  # Triggered geofence once is enough per ping

    # 2. Distress evaluation
    # Fetch all pings for this trip sorted by timestamp ascending
    all_pings = db.query(LocationPing).filter(LocationPing.trip_id == trip_id).order_by(LocationPing.timestamp.asc()).all()
    distress_flags = evaluate_distress(all_pings, db)
    
    for df in distress_flags:
        # Check if an open distress_flag alert exists
        # We can trigger multiple different alert instances if they are not already open
        existing_distress_alert = db.query(Alert).filter(
            Alert.trip_id == trip_id,
            Alert.type == "distress_flag",
            Alert.status == "open"
        ).first()
        if not existing_distress_alert:
            new_alert = Alert(
                trip_id=trip_id,
                type="distress_flag",
                lat=ping_data.lat,
                lng=ping_data.lng,
                status="open",
                timestamp=ping_time
            )
            db.add(new_alert)
            db.commit()
            db.refresh(new_alert)
            dispatch_webhook_for_alert(new_alert, db)
            create_group_alerts_for_alert(new_alert, db)
            alerts_triggered.append(f"distress_{df}")

    return PingIngestResponse(status="success", alerts_triggered=alerts_triggered)

# New schemas for danger zones and authority views
class RiskFactorResponse(BaseModel):
    factor_type: str
    value: float
    weight: float

    class Config:
        from_attributes = True

class DangerZoneResponse(BaseModel):
    id: int
    name: str
    polygon_coordinates: List[List[float]]
    risk_level: Optional[str]
    computed_risk_score: float
    risk_factors: List[RiskFactorResponse]

    class Config:
        from_attributes = True

class SOSRequest(BaseModel):
    lat: float
    lng: float
    is_offline: Optional[bool] = False

class AuthorityActiveTrip(BaseModel):
    trip_id: int
    phone_number: str
    start_date: datetime.datetime
    end_date: datetime.datetime
    region: str
    latest_lat: Optional[float] = None
    latest_lng: Optional[float] = None
    latest_timestamp: Optional[datetime.datetime] = None
    alert_status: str

class AuthorityAlertResponse(BaseModel):
    id: int
    trip_id: int
    phone_number: str
    type: str
    lat: float
    lng: float
    timestamp: datetime.datetime
    status: str

# Endpoints
@router.get("/danger-zones", response_model=List[DangerZoneResponse])
def get_danger_zones(db: Session = Depends(get_db)):
    zones = db.query(DangerZone).all()
    return zones

def send_twilio_sms(body: str, to_number: str):
    from app.core.config import settings
    import requests
    from requests.auth import HTTPBasicAuth
    
    sid = settings.TWILIO_ACCOUNT_SID
    token = settings.TWILIO_AUTH_TOKEN
    from_num = settings.TWILIO_FROM_NUMBER
    
    if not sid or not token or not from_num or not to_number:
        log_msg = f"\n========================================\n[TWILIO SMS] Sent to {to_number or 'MOCK_CONTACT'}:\n{body}\n========================================"
        print(log_msg)
        logging.getLogger("uvicorn.error").info(log_msg)
        return {"status": "mock_sent", "body": body}
        
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    auth = HTTPBasicAuth(sid, token)
    data = {
        "From": from_num,
        "To": to_number,
        "Body": body
    }
    try:
        res = requests.post(url, data=data, auth=auth, timeout=5)
        if res.status_code == 201:
            return {"status": "sent"}
        else:
            raise Exception(f"Twilio API Error: HTTP {res.status_code} - {res.text}")
    except Exception as e:
        log_msg = f"\n========================================\n[TWILIO SMS FAILED] {e}\nPayload: {body}\n========================================"
        print(log_msg)
        logging.getLogger("uvicorn.error").warning(log_msg)
        raise e

@router.post("/{trip_id}/sos")
def trigger_sos(
    trip_id: int,
    payload: SOSRequest,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    trip = db.query(Trip).filter(Trip.id == trip_id, Trip.user_id == current_user.id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    
    # Check if there is already an open SOS alert
    existing_sos = db.query(Alert).filter(
        Alert.trip_id == trip_id,
        Alert.type == "sos",
        Alert.status == "open"
    ).first()
    
    if not existing_sos:
        new_alert = Alert(
            trip_id=trip_id,
            type="sos",
            lat=payload.lat,
            lng=payload.lng,
            status="open",
            timestamp=datetime.datetime.utcnow()
        )
        db.add(new_alert)
        db.commit()
        db.refresh(new_alert)
        dispatch_webhook_for_alert(new_alert, db)
        create_group_alerts_for_alert(new_alert, db)
        
    if payload.is_offline:
        from app.core.config import settings
        user = trip.user
        lang = getattr(user, "preferred_language", "en")
        timestamp_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        google_maps_link = f"https://maps.google.com/?q={payload.lat},{payload.lng}"
        
        if lang == "hi":
            body = (
                f"🆘 सेफट्रिप आपातकाल अलर्ट\n"
                f"पर्यटक: {user.phone_number}\n"
                f"अंतिम स्थान: {payload.lat}, {payload.lng}\n"
                f"समय: {timestamp_str}\n"
                f"नक्शा: {google_maps_link}\n"
                f"यह सेफट्रिप से एक स्वचालित आपातकालीन अलर्ट है।"
            )
        elif lang == "mr":
            body = (
                f"🆘 सेफट्रिप आपत्कालीन अलर्ट\n"
                f"पर्यटक: {user.phone_number}\n"
                f"अंतिम स्थान: {payload.lat}, {payload.lng}\n"
                f"वेळ: {timestamp_str}\n"
                f"नकाशा: {google_maps_link}\n"
                f"हा सेफट्रिप कडून स्वयंचलित आपत्कालीन अलर्ट आहे।"
            )
        else:
            body = (
                f"🆘 SAFETRIP SOS ALERT\n"
                f"Tourist: {user.phone_number}\n"
                f"Last Location: {payload.lat}, {payload.lng}\n"
                f"Time: {timestamp_str}\n"
                f"Maps: {google_maps_link}\n"
                f"This is an automated emergency alert from SafeTrip."
            )
        try:
            send_twilio_sms(body, settings.EMERGENCY_CONTACT_NUMBER)
        except Exception as e:
            logging.getLogger("uvicorn.error").error(f"Failed to send Twilio SMS fallback: {e}")
        
    return {"status": "success", "message": "SOS alert triggered successfully"}

@router.post("/{trip_id}/sos/cancel")
def cancel_sos(
    trip_id: int,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    trip = db.query(Trip).filter(Trip.id == trip_id, Trip.user_id == current_user.id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    
    # Resolve all open SOS alerts for this trip
    open_sos_alerts = db.query(Alert).filter(
        Alert.trip_id == trip_id,
        Alert.type == "sos",
        Alert.status == "open"
    ).all()
    
    if open_sos_alerts:
        for alert in open_sos_alerts:
            alert.status = "resolved"
            alert.resolved_at = datetime.datetime.utcnow()
            # Also resolve any open group alerts linked to this alert
            db.query(GroupAlert).filter(
                GroupAlert.alert_id == alert.id,
                GroupAlert.status == "open"
            ).update({GroupAlert.status: "resolved"}, synchronize_session=False)
        db.commit()
        return {"status": "success", "message": "SOS alert cancelled"}
    
    return {"status": "success", "message": "No active SOS alert to cancel"}

@router.get("/authority/active-trips", response_model=List[AuthorityActiveTrip])
def get_authority_active_trips(
    current_operator: AuthorityUser = Depends(get_current_authority),
    db: Session = Depends(get_db)
):
    active_trips = db.query(Trip).filter(Trip.status == "active").all()
    results = []
    for trip in active_trips:
        # Get latest location ping
        latest_ping = db.query(LocationPing).filter(LocationPing.trip_id == trip.id).order_by(LocationPing.timestamp.desc()).first()
        
        # Get open alerts
        open_alerts = db.query(Alert).filter(Alert.trip_id == trip.id, Alert.status == "open").all()
        
        # Determine alert status
        alert_status = "safe"
        if open_alerts:
            types = [a.type for a in open_alerts]
            if "sos" in types:
                alert_status = "sos"
            elif "distress_flag" in types or "missed_checkin" in types:
                alert_status = "distress"
            elif "geofence" in types:
                alert_status = "geofence"
            else:
                alert_status = "caution"
                
        results.append(AuthorityActiveTrip(
            trip_id=trip.id,
            phone_number=trip.user.phone_number,
            start_date=trip.start_date,
            end_date=trip.end_date,
            region=trip.region,
            latest_lat=latest_ping.lat if latest_ping else None,
            latest_lng=latest_ping.lng if latest_ping else None,
            latest_timestamp=latest_ping.timestamp if latest_ping else None,
            alert_status=alert_status
        ))
    return results

@router.get("/authority/alerts", response_model=List[AuthorityAlertResponse])
def get_authority_alerts(
    payload: dict = Depends(get_current_user_data),
    db: Session = Depends(get_db)
):
    alerts = db.query(Alert).order_by(Alert.timestamp.desc()).all()
    results = []
    for alert in alerts:
        # Find tourist user
        trip = alert.trip
        phone = trip.user.phone_number if trip and trip.user else "Unknown"
        results.append(AuthorityAlertResponse(
            id=alert.id,
            trip_id=alert.trip_id,
            phone_number=phone,
            type=alert.type,
            lat=alert.lat,
            lng=alert.lng,
            timestamp=alert.timestamp,
            status=alert.status
        ))
    return results

class ResolveAlertRequest(BaseModel):
    dispatch_notes: str = Field(..., min_length=10, description="Notes on dispatch actions taken")

@router.post("/authority/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: int,
    payload: ResolveAlertRequest,
    current_operator: AuthorityUser = Depends(get_current_authority),
    db: Session = Depends(get_db)
):
    alert = db.query(Alert).filter(Alert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    alert.status = "resolved"
    alert.resolved_by = current_operator.id
    alert.resolved_at = datetime.datetime.utcnow()
    alert.dispatch_notes = payload.dispatch_notes
    db.commit()
    return {"status": "success", "message": f"Alert {alert_id} resolved"}

import csv
import io
from fastapi.responses import StreamingResponse

alerts_router = APIRouter(prefix="/alerts", tags=["alerts"])

@alerts_router.get("/export")
def export_alerts_csv(
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    token: Optional[str] = Query(None),
    current_operator: AuthorityUser = Depends(get_current_authority_for_export),
    db: Session = Depends(get_db)
):
    query = db.query(Alert).filter(Alert.status == "resolved")
    
    if from_date:
        try:
            clean_from = from_date.replace('Z', '')
            if 'T' in clean_from:
                dt_from = datetime.datetime.fromisoformat(clean_from).replace(tzinfo=None)
            else:
                dt_from = datetime.datetime.strptime(clean_from, "%Y-%m-%d")
            query = query.filter(Alert.timestamp >= dt_from)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid from date format. Use YYYY-MM-DD or ISO format.")
            
    if to_date:
        try:
            clean_to = to_date.replace('Z', '')
            if 'T' in clean_to:
                dt_to = datetime.datetime.fromisoformat(clean_to).replace(tzinfo=None)
            else:
                dt_to = datetime.datetime.strptime(clean_to, "%Y-%m-%d") + datetime.timedelta(days=1) - datetime.timedelta(seconds=1)
            query = query.filter(Alert.timestamp <= dt_to)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid to date format. Use YYYY-MM-DD or ISO format.")
            
    alerts = query.order_by(Alert.timestamp.desc()).all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers
    writer.writerow([
        "Alert ID", "Trip ID", "Tourist Phone", "Alert Type", 
        "Latitude", "Longitude", "Triggered At", "Resolved At", 
        "Resolved By (Operator ID)", "Dispatch Notes"
    ])
    
    for alert in alerts:
        trip = alert.trip
        phone = trip.user.phone_number if trip and trip.user else "Unknown"
        writer.writerow([
            alert.id,
            alert.trip_id,
            phone,
            alert.type,
            alert.lat,
            alert.lng,
            alert.timestamp.isoformat() if alert.timestamp else "",
            alert.resolved_at.isoformat() if alert.resolved_at else "",
            alert.resolved_by or "",
            alert.dispatch_notes or ""
        ])
        
    output.seek(0)
    
    headers = {
        "Content-Disposition": "attachment; filename=incident_reports.csv"
    }
    return StreamingResponse(output, media_type="text/csv", headers=headers)

class ZonePhotoCreate(BaseModel):
    photo_url: str
    lat: float
    lng: float

class ZonePhotoResponse(BaseModel):
    id: int
    zone_id: int
    uploaded_by: Optional[int]
    photo_url: str
    lat: float
    lng: float
    flagged: bool
    created_at: datetime.datetime

    class Config:
        from_attributes = True

@router.post("/danger-zones/{zone_id}/photos", response_model=ZonePhotoResponse, status_code=status.HTTP_201_CREATED)
def add_zone_photo(
    zone_id: int,
    photo_data: ZonePhotoCreate,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    # Verify danger zone exists
    zone = db.query(DangerZone).filter(DangerZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Danger zone not found")

    new_photo = ZonePhoto(
        zone_id=zone_id,
        uploaded_by=current_user.id,
        photo_url=photo_data.photo_url,
        lat=photo_data.lat,
        lng=photo_data.lng,
        flagged=False
    )
    db.add(new_photo)
    db.commit()
    db.refresh(new_photo)
    return new_photo

@router.get("/danger-zones/{zone_id}/photos", response_model=List[ZonePhotoResponse])
def get_zone_photos(
    zone_id: int,
    db: Session = Depends(get_db)
):
    # Verify danger zone exists
    zone = db.query(DangerZone).filter(DangerZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Danger zone not found")

    photos = db.query(ZonePhoto).filter(ZonePhoto.zone_id == zone_id).all()
    return photos

@router.post("/danger-zones/{zone_id}/photos/upload", response_model=ZonePhotoResponse, status_code=status.HTTP_201_CREATED)
def upload_zone_photo(
    zone_id: int,
    file: UploadFile = File(...),
    lat: float = Form(...),
    lng: float = Form(...),
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    # Verify danger zone exists
    zone = db.query(DangerZone).filter(DangerZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Danger zone not found")

    # Make sure static/uploads exists
    os.makedirs("static/uploads", exist_ok=True)

    # Generate filename
    file_ext = os.path.splitext(file.filename)[1]
    filename = f"zone_{zone_id}_{int(datetime.datetime.utcnow().timestamp())}{file_ext}"
    filepath = os.path.join("static/uploads", filename)

    # Save file
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    photo_url = f"/static/uploads/{filename}"

    new_photo = ZonePhoto(
        zone_id=zone_id,
        uploaded_by=current_user.id,
        photo_url=photo_url,
        lat=lat,
        lng=lng,
        flagged=False
    )
    db.add(new_photo)
    db.commit()
    db.refresh(new_photo)
    return new_photo


# ---------------------------------------------------------------------------
# SIMULATED INTEGRATION POINT — This webhook represents a future connection
# to a real police/emergency dispatch API (e.g. 112 India emergency services).
# It is NOT connected to any live system.
# ---------------------------------------------------------------------------

@router.post("/integration/webhook")
def receive_webhook(payload: dict):
    """
    SIMULATED INTEGRATION POINT — This webhook represents a future connection
    to a real police/emergency dispatch API (e.g. 112 India emergency services).
    It is NOT connected to any live system.

    Local stub that receives alert payloads and logs them to webhook_log.txt.
    In production this endpoint would be replaced by a real dispatch system.
    """
    timestamp = datetime.datetime.utcnow().isoformat()
    entry = {
        "received_at": timestamp,
        "payload": payload,
    }
    line = json.dumps(entry)

    # Append to log file
    log_path = os.path.normpath(WEBHOOK_LOG_FILE)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    logger.info("[WEBHOOK RECEIVED] %s", line)
    print(f"[WEBHOOK] Logged dispatch payload: {line}")

    return {"status": "received", "logged_at": timestamp}


    return {"entries": entries, "count": len(entries)}


# ---------------------------------------------------------------------------
# TRIP BUDDY GROUP TRIP ENDPOINTS
# ---------------------------------------------------------------------------
from sqlalchemy.orm.attributes import flag_modified

@router.post("/group/create", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group_trip(
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    # Check if there is an active trip for this user
    active_trip = db.query(Trip).filter(Trip.user_id == current_user.id, Trip.status == "active").first()
    if not active_trip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must start a trip before creating a group trip."
        )

    # Check if this user is already in another active group
    all_groups = db.query(TripGroup).all()
    for g in all_groups:
        if current_user.id in g.members:
            # check if creator's trip is active
            creator_trip = db.query(Trip).filter(Trip.id == g.trip_id).first()
            if creator_trip and creator_trip.status == "active":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"You are already in an active group trip (Join code: {g.join_code}). Leave or end it first."
                )

    # Generate unique 6-digit code
    code = None
    for _ in range(10):
        test_code = f"{random.randint(100000, 999999)}"
        existing = db.query(TripGroup).filter(TripGroup.join_code == test_code).first()
        if not existing:
            code = test_code
            break
    if not code:
        raise HTTPException(status_code=500, detail="Failed to generate a unique join code. Please try again.")

    new_group = TripGroup(
        join_code=code,
        trip_id=active_trip.id,
        members=[current_user.id]
    )
    db.add(new_group)
    db.commit()
    db.refresh(new_group)
    return new_group

@router.post("/group/join", response_model=GroupResponse)
def join_group_trip(
    payload: GroupJoinRequest,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    active_trip = db.query(Trip).filter(Trip.user_id == current_user.id, Trip.status == "active").first()
    if not active_trip:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You must start a trip before joining a group trip."
        )

    # Check if user is already in another active group
    all_groups = db.query(TripGroup).all()
    for g in all_groups:
        if current_user.id in g.members:
            creator_trip = db.query(Trip).filter(Trip.id == g.trip_id).first()
            if creator_trip and creator_trip.status == "active":
                # If they are trying to join the same group, just return it
                if g.join_code == payload.join_code.strip():
                    return g
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"You are already in an active group trip (Join code: {g.join_code})."
                )

    # Find the group
    group = db.query(TripGroup).filter(TripGroup.join_code == payload.join_code.strip()).first()
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid join code"
        )

    # Verify group creator's trip is still active
    creator_trip = db.query(Trip).filter(Trip.id == group.trip_id).first()
    if not creator_trip or creator_trip.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This group trip is no longer active."
        )

    # Add member
    if current_user.id not in group.members:
        members_copy = list(group.members)
        members_copy.append(current_user.id)
        group.members = members_copy
        flag_modified(group, "members")
        db.commit()
        db.refresh(group)

    return group

@router.post("/group/leave")
def leave_group_trip(
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    groups = db.query(TripGroup).all()
    user_group = None
    for g in groups:
        if current_user.id in g.members:
            user_group = g
            break
    if not user_group:
        raise HTTPException(status_code=404, detail="You are not currently in a group trip.")

    # Remove user
    members_copy = [m for m in user_group.members if m != current_user.id]
    user_group.members = members_copy
    flag_modified(user_group, "members")
    db.commit()
    return {"status": "success", "message": "Successfully left the group trip."}

@router.get("/group/my-group", response_model=GroupResponse)
def get_my_group(
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    groups = db.query(TripGroup).all()
    for g in groups:
        if current_user.id in g.members:
            # Check if creator trip is active
            creator_trip = db.query(Trip).filter(Trip.id == g.trip_id).first()
            if creator_trip and creator_trip.status == "active":
                return g
    raise HTTPException(status_code=404, detail="No active group trip found for this user.")

@router.get("/group/members", response_model=List[GroupMemberResponse])
def get_group_members(
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    # Find active group
    groups = db.query(TripGroup).all()
    user_group = None
    for g in groups:
        if current_user.id in g.members:
            creator_trip = db.query(Trip).filter(Trip.id == g.trip_id).first()
            if creator_trip and creator_trip.status == "active":
                user_group = g
                break

    if not user_group:
        return []

    # List of Leaflet marker colors
    colors = ["blue", "red", "green", "gold", "violet", "orange", "yellow", "grey", "black"]

    results = []
    for idx, member_id in enumerate(user_group.members):
        member = db.query(User).filter(User.id == member_id).first()
        if not member:
            continue
        
        # Get active trip
        active_trip = db.query(Trip).filter(Trip.user_id == member_id, Trip.status == "active").first()
        lat, lng, timestamp = None, None, None
        if active_trip:
            latest_ping = db.query(LocationPing).filter(LocationPing.trip_id == active_trip.id).order_by(LocationPing.timestamp.desc()).first()
            if latest_ping:
                lat = latest_ping.lat
                lng = latest_ping.lng
                timestamp = latest_ping.timestamp

        results.append(GroupMemberResponse(
            user_id=member_id,
            phone_number=member.phone_number,
            lat=lat,
            lng=lng,
            timestamp=timestamp,
            color=colors[idx % len(colors)]
        ))

    return results

@router.get("/group/alerts", response_model=List[GroupAlertResponseItem])
def get_group_alerts(
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    # Find active group for the user
    groups = db.query(TripGroup).all()
    user_group = None
    for g in groups:
        if current_user.id in g.members:
            creator_trip = db.query(Trip).filter(Trip.id == g.trip_id).first()
            if creator_trip and creator_trip.status == "active":
                user_group = g
                break
    if not user_group:
        return []

    # Get open group alerts for this user in this group
    alerts = db.query(GroupAlert).filter(
        GroupAlert.group_id == user_group.id,
        GroupAlert.member_id == current_user.id,
        GroupAlert.status == "open"
    ).all()

    # Construct response items
    response_items = []
    for ga in alerts:
        original_alert = ga.alert
        if not original_alert or original_alert.status != "open":
            continue
        
        phone = original_alert.trip.user.phone_number if original_alert.trip and original_alert.trip.user else "Unknown"
        detail = GroupAlertDetailResponse(
            id=original_alert.id,
            trip_id=original_alert.trip_id,
            type=original_alert.type,
            lat=original_alert.lat,
            lng=original_alert.lng,
            timestamp=original_alert.timestamp,
            status=original_alert.status,
            phone_number=phone
        )
        response_items.append(GroupAlertResponseItem(
            id=ga.id,
            group_id=ga.group_id,
            alert_id=ga.alert_id,
            member_id=ga.member_id,
            status=ga.status,
            created_at=ga.created_at,
            alert=detail
        ))
    return response_items

@router.post("/group/alerts/{group_alert_id}/respond")
def respond_to_group_alert(
    group_alert_id: int,
    payload: GroupAlertActionRequest,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    group_alert = db.query(GroupAlert).filter(
        GroupAlert.id == group_alert_id,
        GroupAlert.member_id == current_user.id
    ).first()
    if not group_alert:
        raise HTTPException(status_code=404, detail="Group alert not found")

    if payload.action not in ["going_to_help", "call_authorities", "acknowledged", "dismissed"]:
        raise HTTPException(status_code=400, detail="Invalid action")

    group_alert.status = payload.action
    db.commit()
    return {"status": "success", "message": f"Action '{payload.action}' recorded successfully."}


# ---------------------------------------------------------------------------
# PRE-TRIP RISK BRIEFING
# ---------------------------------------------------------------------------

REGION_BOUNDS = {
    "yosemite": {"lat": [37.4, 38.0], "lng": [-119.7, -119.3]},
}

@router.post("/briefing", response_model=BriefingResponse)
def get_pre_trip_briefing(
    payload: BriefingRequest,
    db: Session = Depends(get_db)
):
    region_name = payload.region.strip()
    
    # Detect if trip is Yosemite (curated) or arbitrary
    is_yosemite = False
    if "yosemite" in region_name.lower():
        is_yosemite = True
    elif payload.lat is not None and payload.lng is not None:
        if abs(payload.lat - 37.7456) < 0.4 and abs(payload.lng - (-119.5332)) < 0.4:
            is_yosemite = True
            
    # 1. Match danger zones in the target region (if Yosemite)
    matched_zones = []
    if is_yosemite:
        lat_min, lat_max, lng_min, lng_max = None, None, None, None
        
        if payload.lat is not None and payload.lng is not None:
            lat_min = payload.lat - 0.3
            lat_max = payload.lat + 0.3
            lng_min = payload.lng - 0.3
            lng_max = payload.lng + 0.3
        else:
            for name_key, bounds in REGION_BOUNDS.items():
                if name_key in region_name.lower():
                    lat_min, lat_max = bounds["lat"]
                    lng_min, lng_max = bounds["lng"]
                    break
                
        all_zones = db.query(DangerZone).all()
        for zone in all_zones:
            if lat_min is not None and lat_max is not None:
                in_bbox = False
                for pt in zone.polygon_coordinates:
                    if lat_min <= pt[0] <= lat_max and lng_min <= pt[1] <= lng_max:
                        in_bbox = True
                        break
                if in_bbox:
                    matched_zones.append(zone)
            else:
                words = [w.lower() for w in region_name.split() if len(w) > 3]
                if any(w in zone.name.lower() for w in words):
                    matched_zones.append(zone)
                    
        if not matched_zones and "yosemite" in region_name.lower():
            lat_min, lat_max = [37.4, 38.0]
            lng_min, lng_max = [-119.7, -119.3]
            for zone in all_zones:
                for pt in zone.polygon_coordinates:
                    if lat_min <= pt[0] <= lat_max and lng_min <= pt[1] <= lng_max:
                        matched_zones.append(zone)
                        break

    # 2. OpenWeatherMap current and forecast retrieval
    from app.core.config import settings
    lat = payload.lat if payload.lat is not None else settings.DEMO_CENTER_LAT
    lng = payload.lng if payload.lng is not None else settings.DEMO_CENTER_LNG
    
    if not payload.lat and matched_zones:
        all_lats = []
        all_lngs = []
        for zone in matched_zones:
            for pt in zone.polygon_coordinates:
                all_lats.append(pt[0])
                all_lngs.append(pt[1])
        if all_lats:
            lat = sum(all_lats) / len(all_lats)
            lng = sum(all_lngs) / len(all_lngs)

    api_key = settings.OPENWEATHERMAP_API_KEY
    temp, condition, rainfall_status = 18.0, "Partly Cloudy", "No Rain"
    is_warning = False
    forecast_rain = False
    safe_hours = "24+ hours (Clear weather)"
    rain_volume = 0.0
    wind_speed = 3.0 # default low wind speed in m/s
    
    if api_key:
        try:
            weather_url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lng}&appid={api_key}&units=metric"
            res = requests.get(weather_url, timeout=5)
            if res.status_code == 200:
                wdata = res.json()
                temp = wdata.get("main", {}).get("temp", temp)
                conds = wdata.get("weather", [])
                if conds:
                    condition = conds[0].get("description", condition).title()
                
                rain_volume = wdata.get("rain", {}).get("1h", 0.0)
                wind_speed = wdata.get("wind", {}).get("speed", wind_speed)
                if rain_volume > 0:
                    rainfall_status = f"{rain_volume} mm/h"
                    if rain_volume > 5.0:
                        is_warning = True
                        rainfall_status += " (Heavy)"
                    elif rain_volume > 2.0:
                        rainfall_status += " (Moderate)"
                
                forecast_url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lng}&appid={api_key}&units=metric"
                fres = requests.get(forecast_url, timeout=5)
                if fres.status_code == 200:
                    fdata = fres.json()
                    found_threat_hour = None
                    for idx, item in enumerate(fdata.get("list", [])[:8]):
                        f_rain = item.get("rain", {}).get("3h", 0.0) / 3.0
                        f_cond = item.get("weather", [{}])[0].get("main", "").lower()
                        if f_rain > 2.0 or "storm" in f_cond or "thunderstorm" in f_cond:
                            found_threat_hour = (idx + 1) * 3
                            break
                    if found_threat_hour:
                        safe_hours = f"{found_threat_hour} hours"
                        forecast_rain = True
            else:
                raise Exception(f"HTTP {res.status_code}")
        except Exception as e:
            logger.warning("Failed to fetch weather from OpenWeatherMap, using mock fallback: %s", e)
            api_key = None

    if not api_key:
        temp = 16.5
        condition = "Overcast & Drizzle"
        rainfall_status = "4.2 mm/h (Moderate)"
        is_warning = True
        forecast_rain = True
        safe_hours = "3 hours (Rain expected to worsen)"
        rain_volume = 4.2
        wind_speed = 6.5

    # 3. Rule-based safety tips & risk calculations
    if not is_yosemite:
        # Fetch real terrain/elevation data from Open-Elevation
        elevation = 0.0
        max_diff = 0.0
        slope_factor = 1.0 # default flat
        elevation_factor = 0.0
        network_factor = 8.0 # default high coverage (low risk)
        
        if payload.lat is not None and payload.lng is not None:
            try:
                delta = 0.0045 # ~500m offset
                locations = [
                    {"latitude": payload.lat, "longitude": payload.lng},
                    {"latitude": payload.lat + delta, "longitude": payload.lng}, # North
                    {"latitude": payload.lat - delta, "longitude": payload.lng}, # South
                    {"latitude": payload.lat, "longitude": payload.lng + delta}, # East
                    {"latitude": payload.lat, "longitude": payload.lng - delta}  # West
                ]
                el_url = "https://api.open-elevation.com/api/v1/lookup"
                el_res = requests.post(el_url, json={"locations": locations}, timeout=6)
                if el_res.status_code == 200:
                    results = el_res.json().get("results", [])
                    if len(results) >= 5:
                        elevations = [item.get("elevation", 0.0) for item in results]
                        elevation = elevations[0]
                        center = elevations[0]
                        others = elevations[1:]
                        max_diff = max(abs(el - center) for el in others) if others else 0.0
                        
                        # Elevation Factor (0-10 scale): mapping 500m to 2500m
                        if elevation < 500.0:
                            elevation_factor = 0.0
                        elif elevation > 2500.0:
                            elevation_factor = 10.0
                        else:
                            elevation_factor = (elevation - 500.0) / 200.0
                            
                        # Slope Factor (0-10 scale): mapping 5m to 50m max diff over 500m
                        if max_diff < 5.0:
                            slope_factor = 1.0
                        elif max_diff > 50.0:
                            slope_factor = 10.0
                        else:
                            slope_factor = (max_diff - 5.0) / 5.0 + 1.0
                            
                        # Simulate network factor (worse in remote mountainous regions)
                        network_factor = max(1.0, 10.0 - (elevation_factor * 0.4 + slope_factor * 0.6))
            except Exception as e:
                logger.warning("Open-Elevation lookup failed, falling back: %s", e)

        # Calculate dynamic weather factors
        rainfall_factor = min(10.0, rain_volume * 2.0) if rain_volume > 0.0 else 0.0
        wind_factor = min(10.0, (wind_speed - 3.0) / 1.2) if wind_speed >= 3.0 else 0.0
        
        if temp < 5.0:
            temp_factor = min(10.0, (5.0 - temp) * 1.5)
        elif temp > 30.0:
            temp_factor = min(10.0, (temp - 30.0) * 1.0)
        else:
            temp_factor = 0.0

        # Weighted calculation (Yosemite ratios): slope=1.5, forest=1, network=1, rain=2, wind=1.5, temp=1.5, elevation=1.5
        total_weighted_risk = (
            slope_factor * 1.5 +
            2.0 * 1.0 + # forest density default (low risk)
            (10.0 - network_factor) * 1.0 +
            rainfall_factor * 2.0 +
            wind_factor * 1.5 +
            temp_factor * 1.5 +
            elevation_factor * 1.5
        )
        total_weight = 1.5 + 1.0 + 1.0 + 2.0 + 1.5 + 1.5 + 1.5 # 10.0
        area_risk_score = round(min(100.0, max(0.0, (total_weighted_risk / total_weight) * 10.0)), 2)

        highest_factors = {
            "rainfall": rainfall_factor,
            "wind_speed": wind_factor,
            "temp": temp_factor,
            "network_coverage": 10.0 - network_factor,
            "slope": slope_factor,
            "forest_density": 2.0,
            "elevation": elevation_factor
        }
    else:
        # Rule-based safety tips
        highest_factors = {}
        for zone in matched_zones:
            factors = db.query(RiskFactor).filter(RiskFactor.zone_id == zone.id).all()
            for f in factors:
                val = f.value
                if f.factor_type == "network_coverage":
                    risk_contrib = max(0.0, 10.0 - val)
                else:
                    risk_contrib = val
                
                if f.factor_type not in highest_factors or risk_contrib > highest_factors[f.factor_type]:
                    highest_factors[f.factor_type] = risk_contrib

    tips_pool = []
    # Location-specific recommendations based on actual factors
    if highest_factors.get("rainfall", 0) >= 5.0 or is_warning or forecast_rain:
        tips_pool.append("Carry robust waterproof gear and avoid high-water river crossings or low trail sections.")
    if highest_factors.get("elevation", 0) >= 6.0:
        tips_pool.append("High altitude detected. Rest to acclimatize, monitor for symptoms of altitude sickness, and carry warm layers.")
    if highest_factors.get("slope", 0) >= 5.0:
        tips_pool.append("Terrain is very steep and rugged. Use sturdy hiking boots, stick to marked paths, and avoid cliffs.")
    if highest_factors.get("network_coverage", 0) >= 6.0:
        tips_pool.append("Download offline maps for the region and share your check-in schedule with your Trip Buddy.")
    if highest_factors.get("wind_speed", 0) >= 6.0:
        tips_pool.append("High wind warning. Secure loose gear, watch for falling branches, and avoid exposed ridge trails.")

    general_tips = [
        "Carry at least 2 liters of water per person and high-energy trail snacks.",
        "Check your phone's battery level and carry a portable power bank.",
        "Stay on designated trails; shortcuts can cause trail erosion and disorientation."
    ]
    for gt in general_tips:
        if len(tips_pool) < 3:
            tips_pool.append(gt)
            
    safety_tips = tips_pool[:3]

    # 4. Warnings compilation
    warnings = []
    if is_warning:
        warnings.append("Active Rainfall Warning: Rain levels currently present a mudslide or flash flood hazard.")
    if forecast_rain:
        warnings.append(f"Worsening weather expected: Heavy rain or storms forecasted within {safe_hours}.")
    
    if is_yosemite:
        if any(z.computed_risk_score >= 70.0 for z in matched_zones):
            warnings.append("High Risk Areas Detected: The planned region contains zones with extreme risk ratings.")
    else:
        if area_risk_score >= 70.0:
            warnings.append(f"High Risk Area Warning: The selected location has an elevated area risk rating of {area_risk_score}/100.")

    # 5. Fetch destination photo via Wikipedia Summary API
    def fetch_wikipedia_photo(place_name: str) -> Optional[str]:
        import urllib.parse
        headers = {"User-Agent": "SafeTrip-App/1.0"}
        
        try:
            encoded = urllib.parse.quote(place_name.strip())
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
            res = requests.get(url, headers=headers, timeout=4)
            if res.status_code == 200:
                data = res.json()
                img = data.get("originalimage", {}).get("source") or data.get("thumbnail", {}).get("source")
                if img:
                    return img
        except Exception:
            pass
            
        if "," in place_name:
            try:
                first_part = place_name.split(",")[0].strip()
                encoded = urllib.parse.quote(first_part)
                url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
                res = requests.get(url, headers=headers, timeout=4)
                if res.status_code == 200:
                    data = res.json()
                    img = data.get("originalimage", {}).get("source") or data.get("thumbnail", {}).get("source")
                    if img:
                        return img
            except Exception:
                pass
        return None

    dest_photo = fetch_wikipedia_photo(region_name)
    if not dest_photo:
        dest_photo = "https://images.unsplash.com/photo-1524661135-423995f22d0b?auto=format&fit=crop&w=800&q=80"

    # Helper definitions for fetching hazard-specific representative photos
    HAZARD_FALLBACKS = {
        "heavy_rain": "https://images.unsplash.com/photo-1534274988757-a28bf1a57c17?auto=format&fit=crop&w=600&q=80",
        "steep_terrain": "https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?auto=format&fit=crop&w=600&q=80",
        "high_altitude": "https://images.unsplash.com/photo-1506744038136-46273834b3fb?auto=format&fit=crop&w=600&q=80",
        "low_visibility": "https://images.unsplash.com/photo-1494548162494-384bba4ab999?auto=format&fit=crop&w=600&q=80",
        "strong_wind": "https://images.unsplash.com/photo-1508739773434-c26b3d09e071?auto=format&fit=crop&w=600&q=80",
        "clear_weather": "https://images.unsplash.com/photo-1501555088652-021faa106b9b?auto=format&fit=crop&w=600&q=80",
    }
    HAZARD_SEARCH_TERMS = {
        "heavy_rain": "flooded trail rain",
        "steep_terrain": "steep mountain slope",
        "high_altitude": "high altitude mountain landscape",
        "low_visibility": "foggy mist trail",
        "strong_wind": "windy stormy weather",
    }

    def fetch_pexels_image(hazard_type: str, api_key: str) -> str:
        if not api_key:
            return HAZARD_FALLBACKS.get(hazard_type, "")
        global _pexels_cache
        if '_pexels_cache' not in globals():
            globals()['_pexels_cache'] = {}
        if hazard_type in globals()['_pexels_cache']:
            return globals()['_pexels_cache'][hazard_type]
        
        import urllib.parse
        query = HAZARD_SEARCH_TERMS.get(hazard_type, "mountain safety")
        url = f"https://api.pexels.com/v1/search?query={urllib.parse.quote(query)}&per_page=1"
        headers = {"Authorization": api_key}
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                photos = data.get("photos", [])
                if photos:
                    img_url = photos[0].get("src", {}).get("medium", "")
                    if img_url:
                        globals()['_pexels_cache'][hazard_type] = img_url
                        return img_url
        except Exception as e:
            logger.warning("Pexels fetch failed for %s: %s", hazard_type, e)
        return HAZARD_FALLBACKS.get(hazard_type, "")

    # 6. Format danger zones list
    danger_zones_info = []
    if is_yosemite:
        for z in matched_zones:
            photo_url = None
            if z.photos:
                photo_url = z.photos[0].photo_url
            if not photo_url:
                photo_url = fetch_wikipedia_photo(z.name)
            if not photo_url:
                photo_url = "https://images.unsplash.com/photo-1524661135-423995f22d0b?auto=format&fit=crop&w=800&q=80"
                
            avoid_caption = "Proceed with caution and stick to designated trails."
            if "half dome" in z.name.lower():
                avoid_caption = "Use high-grip shoes, wear safety harnesses/gloves, and abort the climb if storm clouds build up."
            elif "mist trail" in z.name.lower():
                avoid_caption = "Watch out for slippery granite steps, hold onto guardrails, and wear waterproof outer layers."
                
            danger_zones_info.append(
                BriefingZoneInfo(
                    name=z.name,
                    risk_score=z.computed_risk_score,
                    risk_level=z.risk_level or "unknown",
                    photo_url=photo_url,
                    hazard_type="curated_danger_zone",
                    avoid_caption=avoid_caption
                )
            )
    else:
        # Dynamic location hazards
        hazards = []
        
        # 1. High Altitude
        if elevation_factor >= 5.0:
            hazards.append({
                "name": "High Altitude Zone",
                "risk_score": round(elevation_factor * 10.0, 2),
                "risk_level": "high" if elevation_factor >= 7.0 else "medium",
                "hazard_type": "high_altitude",
                "avoid_caption": "Acclimatize to altitude, monitor for symptoms of mountain sickness (headache/nausea), and carry warm layers."
            })
            
        # 2. Steep Slope
        if slope_factor >= 5.0:
            hazards.append({
                "name": "Steep Slope / Rugged Terrain",
                "risk_score": round(slope_factor * 10.0, 2),
                "risk_level": "high" if slope_factor >= 7.0 else "medium",
                "hazard_type": "steep_terrain",
                "avoid_caption": "Use rugged hiking footwear, stay on marked paths, avoid cliff edges, and watch for loose rocks."
            })
            
        # 3. Rain / Flood
        if rainfall_factor >= 4.0 or is_warning or forecast_rain:
            hazards.append({
                "name": "Rain / Flash Flood Risk",
                "risk_score": round(max(rainfall_factor * 10.0, 40.0), 2),
                "risk_level": "high" if (rainfall_factor >= 7.0 or is_warning) else "medium",
                "hazard_type": "heavy_rain",
                "avoid_caption": "Wear heavy waterproof shells, avoid low-lying trails, and do not attempt to cross flooded streams."
            })
            
        # 4. Strong Wind
        if wind_factor >= 5.0:
            hazards.append({
                "name": "High Wind Hazard",
                "risk_score": round(wind_factor * 10.0, 2),
                "risk_level": "high" if wind_factor >= 7.0 else "medium",
                "hazard_type": "strong_wind",
                "avoid_caption": "Secure lose gear, avoid ridge trails, watch out for falling branches/debris, and prepare windbreaks."
            })
            
        # 5. Low Visibility
        if "fog" in condition.lower() or "mist" in condition.lower() or "haze" in condition.lower() or rainfall_factor >= 7.0:
            hazards.append({
                "name": "Low Visibility Trail",
                "risk_score": 70.0 if rainfall_factor >= 7.0 else 50.0,
                "risk_level": "high" if rainfall_factor >= 7.0 else "medium",
                "hazard_type": "low_visibility",
                "avoid_caption": "Use trail markings and GPS navigation, carry high-visibility gear or a headlamp, and slow down your pace."
            })
            
        # 6. Fallback clear weather
        if not hazards:
            hazards.append({
                "name": "Low Risk Area",
                "risk_score": area_risk_score,
                "risk_level": "low",
                "hazard_type": "clear_weather",
                "avoid_caption": "Enjoy your trip! Keep tracking active, monitor weather changes, and check in on schedule."
            })
            
        for h in hazards:
            img = fetch_pexels_image(h["hazard_type"], settings.PEXELS_API_KEY)
            danger_zones_info.append(
                BriefingZoneInfo(
                    name=h["name"],
                    risk_score=h["risk_score"],
                    risk_level=h["risk_level"],
                    photo_url=img,
                    hazard_type=h["hazard_type"],
                    avoid_caption=h["avoid_caption"]
                )
            )

    return BriefingResponse(
        region=payload.region,
        danger_zones=danger_zones_info,
        weather=BriefingWeatherInfo(
            temp=round(temp, 1),
            condition=condition,
            rainfall_status=rainfall_status,
            is_warning=is_warning
        ),
        safety_tips=safety_tips,
        safe_hours=safe_hours,
        warnings=warnings,
        destination_photo_url=dest_photo
    )

@router.post("/{trip_id}/checkin")
def checkin_trip(
    trip_id: int,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    trip = db.query(Trip).filter(Trip.id == trip_id, Trip.user_id == current_user.id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    
    trip.last_checkin_at = datetime.datetime.utcnow()
    # Resolve all open missed_checkin alerts for this trip
    open_alerts = db.query(Alert).filter(
        Alert.trip_id == trip_id,
        Alert.type == "missed_checkin",
        Alert.status == "open"
    ).all()
    for alert in open_alerts:
        alert.status = "resolved"
        alert.resolved_at = datetime.datetime.utcnow()
        # Resolve group alerts
        db.query(GroupAlert).filter(
            GroupAlert.alert_id == alert.id,
            GroupAlert.status == "open"
        ).update({GroupAlert.status: "resolved"}, synchronize_session=False)
    db.commit()
    return {"status": "success", "message": "Check-in successful"}

def check_missed_checkins():
    from app.core.database import SessionLocal
    from app.models.models import Trip, Alert, LocationPing, GroupAlert
    from app.core.config import settings
    
    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()
        active_trips = db.query(Trip).filter(
            Trip.status == "active",
            Trip.checkin_interval_hours.isnot(None)
        ).all()
        
        for trip in active_trips:
            base_time = trip.last_checkin_at or trip.start_date
            interval_hours = trip.checkin_interval_hours
            
            # Determine grace period: if interval is 1 minute (1/60 hours) or less, use 10 seconds. Otherwise 15 minutes.
            if interval_hours <= (1.0 / 60.0):
                grace_period = datetime.timedelta(seconds=10)
            else:
                grace_period = datetime.timedelta(minutes=15)
                
            interval_delta = datetime.timedelta(hours=interval_hours)
            missed_deadline = base_time + interval_delta + grace_period
            
            if now > missed_deadline:
                # Check if there is already an open missed_checkin alert
                existing = db.query(Alert).filter(
                    Alert.trip_id == trip.id,
                    Alert.type == "missed_checkin",
                    Alert.status == "open"
                ).first()
                
                if not existing:
                    # Get latest location ping or fallback
                    latest_ping = db.query(LocationPing).filter(
                        LocationPing.trip_id == trip.id
                    ).order_by(LocationPing.timestamp.desc()).first()
                    
                    lat = latest_ping.lat if latest_ping else settings.DEMO_CENTER_LAT
                    lng = latest_ping.lng if latest_ping else settings.DEMO_CENTER_LNG
                    
                    new_alert = Alert(
                        trip_id=trip.id,
                        type="missed_checkin",
                        lat=lat,
                        lng=lng,
                        status="open",
                        timestamp=now
                    )
                    db.add(new_alert)
                    db.commit()
                    db.refresh(new_alert)
                    
                    # Notify webhook and group alerts
                    dispatch_webhook_for_alert(new_alert, db)
                    create_group_alerts_for_alert(new_alert, db)
                    
                    msg = f"[CHECKIN] Missed check-in alert created automatically for Trip {trip.id} of user {trip.user.phone_number}"
                    print(msg)
                    logging.getLogger("uvicorn.error").info(msg)
    except Exception as e:
        msg = f"[CHECKIN ERROR] Failed checking check-ins: {e}"
        print(msg)
        logging.getLogger("uvicorn.error").error(msg)
        db.rollback()
    finally:
        db.close()

class TripFeedbackCreate(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    felt_unsafe: bool
    unsafe_location: Optional[str] = None
    suggestions: Optional[str] = None

@router.post("/{trip_id}/feedback", status_code=status.HTTP_201_CREATED)
def submit_trip_feedback(
    trip_id: int,
    payload: TripFeedbackCreate,
    current_user: User = Depends(get_current_tourist),
    db: Session = Depends(get_db)
):
    trip = db.query(Trip).filter(Trip.id == trip_id, Trip.user_id == current_user.id).first()
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
        
    existing = db.query(TripFeedback).filter(TripFeedback.trip_id == trip_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Feedback already submitted for this trip")
        
    feedback = TripFeedback(
        trip_id=trip_id,
        rating=payload.rating,
        felt_unsafe=payload.felt_unsafe,
        unsafe_location=payload.unsafe_location,
        suggestions=payload.suggestions,
        created_at=datetime.datetime.utcnow()
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return {"status": "success", "message": "Feedback submitted successfully"}

@router.get("/authority/feedback-summary")
def get_feedback_summary(
    current_operator: AuthorityUser = Depends(get_current_authority),
    db: Session = Depends(get_db)
):
    from sqlalchemy import func
    
    # 1. Average safety rating per region
    region_stats = db.query(
        Trip.region,
        func.avg(TripFeedback.rating).label("avg_rating")
    ).join(TripFeedback, Trip.id == TripFeedback.trip_id).group_by(Trip.region).all()
    
    region_summary = [{"region": r[0], "avg_rating": round(float(r[1]), 2) if r[1] is not None else 0.0} for r in region_stats]
    
    # 2. Count of "felt unsafe" reports per danger zone
    danger_zones = db.query(DangerZone).all()
    feedbacks = db.query(TripFeedback).filter(TripFeedback.felt_unsafe == True).all()
    
    zone_summary = []
    for zone in danger_zones:
        count = 0
        for fb in feedbacks:
            if fb.unsafe_location and zone.name.lower() in fb.unsafe_location.lower():
                count += 1
        zone_summary.append({
            "zone_name": zone.name,
            "felt_unsafe_count": count
        })
        
    # TODO: Wire feedback_unsafe_count into risk scoring Phase 2
    
    # 3. Latest suggestions (last 5, newest first)
    latest_feedbacks = db.query(TripFeedback).filter(
        TripFeedback.suggestions != None, 
        TripFeedback.suggestions != ""
    ).order_by(TripFeedback.created_at.desc()).limit(5).all()
    
    suggestions = [{
        "trip_id": fb.trip_id,
        "suggestions": fb.suggestions,
        "created_at": fb.created_at.strftime("%Y-%m-%d %H:%M:%S") if fb.created_at else ""
    } for fb in latest_feedbacks]
    
    return {
        "region_avg_ratings": region_summary,
        "zone_felt_unsafe_counts": zone_summary,
        "latest_suggestions": suggestions
    }


