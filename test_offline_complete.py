import sys
import datetime
import io
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.models import User, Trip, Alert, LocationPing, OTPToken
from app.core.config import settings

PHONE = "+19998889999"
CONTACT = "+91XXXXXXXXXX"

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test data for offline complete test...")
    user = db.query(User).filter(User.phone_number == PHONE).first()
    if user:
        trips = db.query(Trip).filter(Trip.user_id == user.id).all()
        for trip in trips:
            db.query(Alert).filter(Alert.trip_id == trip.id).delete()
            db.query(LocationPing).filter(LocationPing.trip_id == trip.id).delete()
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
    return verify_res.json()["access_token"]

def main():
    db = SessionLocal()
    client = TestClient(app)
    
    try:
        cleanup_test_data(db)
        
        # Configure test credentials
        settings.EMERGENCY_CONTACT_NUMBER = CONTACT
        settings.TWILIO_ACCOUNT_SID = "ACdummy_sid"
        settings.TWILIO_AUTH_TOKEN = "dummy_auth_token"
        settings.TWILIO_FROM_NUMBER = "+12055550100"
        
        # Authenticate
        token = authenticate_user(client, PHONE)
        headers = {"Authorization": f"Bearer {token}"}
        
        # Start a trip
        trip_start_data = {
            "start_date": datetime.datetime.utcnow().isoformat(),
            "end_date": (datetime.datetime.utcnow() + datetime.timedelta(hours=4)).isoformat(),
            "region": "Yosemite Valley"
        }
        res = client.post("/api/trips/start", json=trip_start_data, headers=headers)
        assert res.status_code == 201, f"Failed to start trip: {res.text}"
        trip_id = res.json()["id"]
        print(f"[TRIP] Started trip {trip_id} for user {PHONE}")
        
        print("\n=== STEP 1: Simulating Online Caching (Successful pings) ===")
        # Simulating client posting successful GPS coordinates with timestamps when online
        # We simulate 3 coordinates (which would be stored in the last 5 coordinates cache list)
        pings_to_cache = [
            {"lat": 37.7450, "lng": -119.5330},
            {"lat": 37.7453, "lng": -119.5331},
            {"lat": 37.7456, "lng": -119.5332} # This will be the last cached coordinates
        ]
        
        for idx, ping in enumerate(pings_to_cache):
            ping_res = client.post(f"/api/trips/{trip_id}/ping", json=ping, headers=headers)
            assert ping_res.status_code == 200, f"Ping {idx} failed: {ping_res.text}"
            print(f"  Ping {idx} sent: {ping['lat']}, {ping['lng']}")
            
        # Last known coordinates is from the third ping
        last_cached_lat = pings_to_cache[-1]["lat"]
        last_cached_lng = pings_to_cache[-1]["lng"]
        print(f"  Last cached coordinates in client: {last_cached_lat}, {last_cached_lng}")
        
        print("\n=== STEP 2 & 3: Simulating Offline Mode and Triggering SOS ===")
        # Simulator: mock navigator.onLine = false
        # When user clicks SOS, the client detects navigator.onLine = false and sends is_offline = True
        # using the last cached coordinates.
        sos_payload = {
            "lat": last_cached_lat,
            "lng": last_cached_lng,
            "is_offline": True
        }
        
        # Intercept output to verify Twilio SMS body format
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        
        from unittest.mock import patch
        import requests
        
        try:
            # We mock requests.post to simulate lack of connection to Twilio gateway/internet
            with patch("requests.post", side_effect=requests.exceptions.ConnectionError("Network disconnected (simulated)")):
                sos_res = client.post(f"/api/trips/{trip_id}/sos", json=sos_payload, headers=headers)
            captured_output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
            
        print("[TEST] Captured stdout output from endpoint call:")
        print(captured_output)
        
        assert sos_res.status_code == 200, f"SOS trigger failed: {sos_res.text}"
        
        # Verify Twilio SMS payload structure
        print("=== VERIFYING TWILIO SMS FORMAT ===")
        # The Twilio mock logs parameters, let's assert correct content is in the log.
        # Twilio sends format:
        # [TWILIO SMS FAILED] Network disconnected (simulated)
        # Payload: 🆘 SAFETRIP SOS ALERT ...
        assert "[TWILIO SMS FAILED] Network disconnected (simulated)" in captured_output
        assert "🆘 SAFETRIP SOS ALERT" in captured_output
        assert f"Tourist: {PHONE}" in captured_output
        assert f"Last Location: {last_cached_lat}, {last_cached_lng}" in captured_output
        assert "This is an automated emergency alert from SafeTrip." in captured_output
        print("[SUCCESS] Twilio SMS payload is correctly formed and verified!")
        
        print("\n=== STEP 4 & 5: Simulating Reconnection and Syncing Queued Pings ===")
        # While offline, the client queued pings locally.
        # We simulate 2 queued pings
        queued_pings = [
            {"lat": 37.7460, "lng": -119.5335},
            {"lat": 37.7463, "lng": -119.5338}
        ]
        
        # Simulation: coming back online
        print("  Client reconnected! Syncing 2 queued pings...")
        for idx, q_ping in enumerate(queued_pings):
            sync_res = client.post(f"/api/trips/{trip_id}/ping", json=q_ping, headers=headers)
            assert sync_res.status_code == 200, f"Sync ping {idx} failed: {sync_res.text}"
            print(f"  Queued ping {idx} synced to backend: {q_ping['lat']}, {q_ping['lng']}")
            
        # Verify pings are successfully recorded in DB
        db.expire_all()
        pings_db = db.query(LocationPing).filter(LocationPing.trip_id == trip_id).order_by(LocationPing.timestamp.asc()).all()
        # We had 3 online pings + 2 synced offline pings = 5 total pings
        print(f"  Total pings recorded in database: {len(pings_db)} (Expected: 5)")
        assert len(pings_db) == 5
        
        # Check coordinates of last two synced pings
        assert pings_db[-2].lat == 37.7460
        assert pings_db[-1].lat == 37.7463
        print("[SUCCESS] Queued pings successfully synced and recorded in backend!")
        
        print("\n========================================")
        print("ALL COMPLETED OFFLINE & SMS Fallback TESTS PASSED!")
        print("========================================")
        
    except Exception as e:
        print(f"[FAIL] Test execution encountered error: {e}")
        sys.exit(1)
    finally:
        cleanup_test_data(db)
        db.close()

if __name__ == "__main__":
    main()
