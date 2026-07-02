import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True, nullable=False)
    preferred_language = Column(String, default="en", nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    trips = relationship("Trip", back_populates="user")
    zone_photos = relationship("ZonePhoto", back_populates="uploader")

class AuthorityUser(Base):
    __tablename__ = "authority_users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="operator")  # e.g., admin, operator
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    resolved_alerts = relationship("Alert", back_populates="resolver")

class Trip(Base):
    __tablename__ = "trips"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    region = Column(String, nullable=False)
    status = Column(String, default="active")  # active, ended
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    auto_delete_at = Column(DateTime, nullable=False)
    checkin_interval_hours = Column(Float, nullable=True)
    last_checkin_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="trips")
    location_pings = relationship("LocationPing", back_populates="trip", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="trip", cascade="all, delete-orphan")
    feedback = relationship("TripFeedback", back_populates="trip", uselist=False, cascade="all, delete-orphan")

class LocationPing(Base):
    __tablename__ = "location_pings"

    id = Column(Integer, primary_key=True, index=True)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    trip = relationship("Trip", back_populates="location_pings")

class DangerZone(Base):
    __tablename__ = "danger_zones"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    polygon_coordinates = Column(JSON, nullable=False)  # List of [lat, lng] lists defining the boundary
    risk_level = Column(String, nullable=True)  # low, medium, high (manually set optional override)
    computed_risk_score = Column(Float, default=0.0)
    last_scored_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    risk_factors = relationship("RiskFactor", back_populates="zone", cascade="all, delete-orphan")
    photos = relationship("ZonePhoto", back_populates="zone", cascade="all, delete-orphan")

class RiskFactor(Base):
    __tablename__ = "risk_factors"

    id = Column(Integer, primary_key=True, index=True)
    zone_id = Column(Integer, ForeignKey("danger_zones.id"), nullable=False)
    factor_type = Column(String, nullable=False)  # slope, forest_density, rainfall, network_coverage
    value = Column(Float, nullable=False)
    weight = Column(Float, default=1.0)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Relationships
    zone = relationship("DangerZone", back_populates="risk_factors")

class ZonePhoto(Base):
    __tablename__ = "zone_photos"

    id = Column(Integer, primary_key=True, index=True)
    zone_id = Column(Integer, ForeignKey("danger_zones.id"), nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    photo_url = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    flagged = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    zone = relationship("DangerZone", back_populates="photos")
    uploader = relationship("User", back_populates="zone_photos")

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=False)
    type = Column(String, nullable=False)  # geofence, sos, distress_flag
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String, default="open")  # open, resolved
    resolved_by = Column(Integer, ForeignKey("authority_users.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    dispatch_notes = Column(String, nullable=True)

    # Relationships
    trip = relationship("Trip", back_populates="alerts")
    resolver = relationship("AuthorityUser", back_populates="resolved_alerts")

class OTPToken(Base):
    __tablename__ = "otp_tokens"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, index=True, nullable=False)
    code = Column(String, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class TripGroup(Base):
    __tablename__ = "trip_groups"

    id = Column(Integer, primary_key=True, index=True)
    join_code = Column(String, unique=True, index=True, nullable=False)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=False)
    members = Column(JSON, nullable=False)  # JSON array of user_ids, e.g. [1, 2, 3]

    # Relationships
    trip = relationship("Trip")

class GroupAlert(Base):
    __tablename__ = "group_alerts"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("trip_groups.id"), nullable=False)
    alert_id = Column(Integer, ForeignKey("alerts.id"), nullable=False)
    member_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, default="open")  # open, going_to_help, call_authorities, acknowledged, dismissed
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    group = relationship("TripGroup")
    alert = relationship("Alert")
    member = relationship("User")

class TripFeedback(Base):
    __tablename__ = "trip_feedbacks"

    id = Column(Integer, primary_key=True, index=True)
    trip_id = Column(Integer, ForeignKey("trips.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    felt_unsafe = Column(Boolean, nullable=False)
    unsafe_location = Column(String, nullable=True)
    suggestions = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    trip = relationship("Trip", back_populates="feedback")

