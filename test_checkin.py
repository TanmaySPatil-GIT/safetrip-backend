import sys
import datetime
import time
from fastapi.testclient import TestClient

from app.core.config import settings
# Override the scheduler interval to 5 seconds (0.0833 minutes) before importing app
settings.CHECKIN_SCHEDULER_INTERVAL_MINUTES = 5.0 / 60.0

from app.main import app
from app.core.database import SessionLocal
from app.models.models import User, Trip, Alert, OTPToken

PHONE = "+19998889999"

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test data for check-in...")
    user = db.query(User).filter(User.phone_number == PHONE).first()
    if user:
        trips = db.query(Trip).filter(Trip.user_id == user.id).all()
        for trip in trips:
            db.query(Alert).filter(Alert.trip_id == trip.id).delete()
            db.query(Trip).filter(Trip.id == trip.id).delete()
        db.query(User).filter(User.id == user.id).delete()
    db.query(OTPToken).filter(OTPToken.phone_number == PHONE).delete()
    db.commit()
    print("[CLEANUP] Cleanup complete.")

def authenticate_user(client, phone: str) -> str:
    print(f"[AUTH] Authenticating test user {phone}...")
    otp_res = client.post("/api/auth/tourist/otp", json={"phone_number": phone})
    assert otp_res.status_code == 200, f"OTP request failed: {otp_res.text}"
    
    verify_res = client.post("/api/auth/tourist/verify", json={
        "phone_number": phone,
        "code": "123456"
    })
    assert verify_res.status_code == 200, f"OTP verification failed: {verify_res.text}"
    auth_data = verify_res.json()
    return auth_data["access_token"]

def main():
    db = SessionLocal()
    try:
        cleanup_test_data(db)
        
        # We start the app using the TestClient context manager to trigger startup events (BackgroundScheduler)
        with TestClient(app) as client:
            token = authenticate_user(client, PHONE)
            headers = {"Authorization": f"Bearer {token}"}
            
            # Start a trip with 1 minute check-in interval (1/60 hours)
            trip_start_data = {
                "start_date": datetime.datetime.utcnow().isoformat(),
                "end_date": (datetime.datetime.utcnow() + datetime.timedelta(hours=4)).isoformat(),
                "region": "Yosemite Valley",
                "checkin_interval_hours": 1.0 / 60.0
            }
            
            print(f"[TEST] Starting trip with check-in interval: 1 minute...")
            res = client.post("/api/trips/start", json=trip_start_data, headers=headers)
            assert res.status_code == 201, f"Failed to start trip: {res.text}"
            trip_data = res.json()
            trip_id = trip_data["id"]
            print(f"[TRIP] Started trip {trip_id} for user {PHONE}")
            
            # Verify database fields were set
            trip = db.query(Trip).filter(Trip.id == trip_id).first()
            assert trip.checkin_interval_hours == 1.0 / 60.0, "checkin_interval_hours not set correctly!"
            assert trip.last_checkin_at is not None, "last_checkin_at was not initialized!"
            print(f"[TEST] Database fields verified. Last checkin initialized at: {trip.last_checkin_at}")
            
            print("\n=== STEP 2: Waiting 75 seconds without responding ===")
            print("[TEST] Waiting...")
            # We can sleep in segments to show progress
            for i in range(15):
                time.sleep(5)
                print(f"[TEST] Elapsed: {(i + 1) * 5} seconds")
                # Check if alert got created in DB already to exit early if possible
                db.expire_all()
                alert = db.query(Alert).filter(Alert.trip_id == trip_id, Alert.type == "missed_checkin").first()
                if alert:
                    print(f"[TEST] Alert detected in DB early at {(i + 1) * 5} seconds!")
                    break
            
            # Check final DB state for missed check-in alert
            db.expire_all()
            alert = db.query(Alert).filter(Alert.trip_id == trip_id, Alert.type == "missed_checkin").first()
            
            assert alert is not None, "Missed checkin alert was not created!"
            assert alert.status == "open", "Missed checkin alert status should be open!"
            print(f"\n[SUCCESS] Missed checkin alert created successfully:")
            print(f"  Alert ID: {alert.id}")
            print(f"  Type: {alert.type}")
            print(f"  Coordinates: {alert.lat}, {alert.lng}")
            print(f"  Timestamp: {alert.timestamp}")
            print(f"  Status: {alert.status}")
            
            # Test resolving check-in
            print("\n=== STEP 3: Testing Check-In Confirmation ===")
            checkin_res = client.post(f"/api/trips/{trip_id}/checkin", headers=headers)
            assert checkin_res.status_code == 200, f"Checkin endpoint failed: {checkin_res.text}"
            
            db.expire_all()
            trip = db.query(Trip).filter(Trip.id == trip_id).first()
            alert = db.query(Alert).filter(Alert.trip_id == trip_id, Alert.type == "missed_checkin").first()
            
            assert alert.status == "resolved", "Missed checkin alert was not resolved after checkin!"
            print(f"[SUCCESS] Checkin endpoint successfully verified!")
            print(f"  New Last Checkin: {trip.last_checkin_at}")
            print(f"  Alert Status: {alert.status} (Resolved at: {alert.resolved_at})")
            
            print("\n========================================")
            print("ALL CHECK-IN TIMER TESTS PASSED!")
            print("========================================")

    except Exception as e:
        print(f"\n[ERROR] Test execution failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        cleanup_test_data(db)
        db.close()

if __name__ == "__main__":
    main()
