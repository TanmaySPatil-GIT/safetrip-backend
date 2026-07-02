import datetime
import random
from app.core.database import SessionLocal
from app.models.models import User, Trip, Alert, DangerZone

def seed_historical_alerts():
    db = SessionLocal()
    try:
        print("[SEED] Seeding historical alerts for heatmap...")
        
        # 1. Create or get fake tourist user
        phone = "+19990000000"
        user = db.query(User).filter(User.phone_number == phone).first()
        if not user:
            user = User(
                phone_number=phone
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"[SEED] Created user: {phone}")
        else:
            print(f"[SEED] Using existing user: {phone}")
            
        # Clear previous alerts for this user/trips to prevent cluttering
        user_trips = db.query(Trip).filter(Trip.user_id == user.id).all()
        for t in user_trips:
            db.query(Alert).filter(Alert.trip_id == t.id).delete()
            db.query(Trip).filter(Trip.id == t.id).delete()
        db.commit()
        
        # 2. Create historical trips
        now = datetime.datetime.utcnow()
        
        # Recent Trip (last 7 days)
        end_recent = now - datetime.timedelta(days=5)
        trip_recent = Trip(
            user_id=user.id,
            start_date=now - datetime.timedelta(days=6),
            end_date=end_recent,
            region="Yosemite Valley",
            status="ended",
            auto_delete_at=end_recent + datetime.timedelta(days=30)
        )
        db.add(trip_recent)
        
        # Mid-range Trip (last 30 days)
        end_mid = now - datetime.timedelta(days=24)
        trip_mid = Trip(
            user_id=user.id,
            start_date=now - datetime.timedelta(days=25),
            end_date=end_mid,
            region="Yosemite Valley",
            status="ended",
            auto_delete_at=end_mid + datetime.timedelta(days=30)
        )
        db.add(trip_mid)
        
        # Historical Trip (older than 30 days)
        end_old = now - datetime.timedelta(days=79)
        trip_old = Trip(
            user_id=user.id,
            start_date=now - datetime.timedelta(days=80),
            end_date=end_old,
            region="Yosemite Valley",
            status="ended",
            auto_delete_at=end_old + datetime.timedelta(days=30)
        )
        db.add(trip_old)
        
        db.commit()
        db.refresh(trip_recent)
        db.refresh(trip_mid)
        db.refresh(trip_old)
        
        print(f"[SEED] Created 3 historical trips: {trip_recent.id}, {trip_mid.id}, {trip_old.id}")
        
        # 3. Define alert templates across the 3 Yosemite danger zones
        # Coordinates:
        # Half Dome Cables: approx [37.746, -119.533]
        # Tuolumne Meadows: approx [37.8735, -119.3555]
        # Mariposa Grove: approx [37.5135, -119.6005]
        
        danger_coords = [
            {"lat": 37.7460, "lng": -119.5330, "zone_name": "Half Dome Cables"},
            {"lat": 37.8735, "lng": -119.3555, "zone_name": "Tuolumne Meadows"},
            {"lat": 37.5135, "lng": -119.6005, "zone_name": "Mariposa Grove"}
        ]
        
        alert_types = ["sos", "distress_flag", "geofence"]
        
        # We need to seed 20 alerts:
        # - 6 alerts in the last 7 days (link to trip_recent)
        # - 8 alerts in the last 30 days but > 7 days (link to trip_mid)
        # - 6 alerts older than 30 days (link to trip_old)
        
        alert_distributions = [
            {"trip": trip_recent, "age_days_range": (1, 6), "count": 6},
            {"trip": trip_mid, "age_days_range": (8, 28), "count": 8},
            {"trip": trip_old, "age_days_range": (35, 90), "count": 6}
        ]
        
        alert_count = 0
        for dist in alert_distributions:
            trip = dist["trip"]
            min_days, max_days = dist["age_days_range"]
            for _ in range(dist["count"]):
                # Choose random danger zone coordinate and add minor jitter
                coord = random.choice(danger_coords)
                jitter_lat = random.uniform(-0.0005, 0.0005)
                jitter_lng = random.uniform(-0.0005, 0.0005)
                
                # Determine timestamp
                age_days = random.randint(min_days, max_days)
                timestamp = now - datetime.timedelta(days=age_days, hours=random.randint(0, 23))
                
                alert_type = random.choice(alert_types)
                
                alert = Alert(
                    trip_id=trip.id,
                    type=alert_type,
                    lat=coord["lat"] + jitter_lat,
                    lng=coord["lng"] + jitter_lng,
                    timestamp=timestamp,
                    status="resolved",
                    resolved_at=timestamp + datetime.timedelta(hours=2)
                )
                db.add(alert)
                alert_count += 1
                
        db.commit()
        print(f"[SEED] Successfully seeded {alert_count} fake historical alerts across Yosemite danger zones!")
        
    except Exception as e:
        print(f"[ERROR] Seeding failed: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_historical_alerts()
