import sys
import datetime
import io
from fastapi.testclient import TestClient

# Ensure backend folder is in path
from app.main import app
from app.core.database import SessionLocal
from app.models.models import User, Trip, Alert, OTPToken
from app.core.config import settings

client = TestClient(app)

PHONE = "+19998889999"
CONTACT = "+18005550199"

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test data for Offline SOS...")
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

def authenticate_user(phone: str) -> str:
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
        
        # 1. Setup emergency contact setting
        settings.EMERGENCY_CONTACT_NUMBER = CONTACT
        # Set dummy Twilio credentials to trigger actual request path
        settings.TWILIO_ACCOUNT_SID = "ACdummy_sid"
        settings.TWILIO_AUTH_TOKEN = "dummy_auth_token"
        settings.TWILIO_FROM_NUMBER = "+12055550100"
        
        # 2. Authenticate user and start a trip
        token = authenticate_user(PHONE)
        headers = {"Authorization": f"Bearer {token}"}
        
        trip_start_data = {
            "start_date": datetime.datetime.utcnow().isoformat(),
            "end_date": (datetime.datetime.utcnow() + datetime.timedelta(hours=4)).isoformat(),
            "region": "Yosemite Valley"
        }
        res = client.post("/api/trips/start", json=trip_start_data, headers=headers)
        assert res.status_code == 201, f"Failed to start trip: {res.text}"
        trip_data = res.json()
        trip_id = trip_data["id"]
        print(f"[TRIP] Started trip {trip_id} for user {PHONE}")
        
        # 3. Simulate client WebView detecting offline mode and triggering SOS
        print("\n=== STEP 3: Triggering Offline SOS ===")
        sos_payload = {
            "lat": 37.7456,
            "lng": -119.5332,
            "is_offline": True
        }
        
        # Intercept print output to verify SMS payload format
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        
        from unittest.mock import patch
        import requests
        
        try:
            # Temporarily disable the network by mocking requests.post to raise ConnectionError
            with patch("requests.post", side_effect=requests.exceptions.ConnectionError("Network disconnected (simulated)")):
                sos_res = client.post(f"/api/trips/{trip_id}/sos", json=sos_payload, headers=headers)
            captured_output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
            
        print("[TEST] Captured stdout output from endpoint call:")
        print(captured_output)
        
        # 4. Assertions on response and stdout
        assert sos_res.status_code == 200, f"SOS call failed: {sos_res.text}"
        assert "[TWILIO SMS FAILED] Network disconnected (simulated)" in captured_output, "Twilio SMS fallback failure log missing!"
        assert "SAFETRIP SOS: Tourist may be in distress." in captured_output, "SOS text missing!"
        assert f"Tourist: Tourist {PHONE[-4:]} ({PHONE})" in captured_output, "Tourist name or phone missing!"
        assert "Last Known Coordinates: 37.7456, -119.5332" in captured_output, "Coordinates missing!"
        assert "Google Maps: https://maps.google.com/?q=37.7456,-119.5332" in captured_output, "Maps link missing!"
        
        print("\n========================================")
        assert len(db.query(Alert).filter(Alert.trip_id == trip_id, Alert.type == "sos").all()) == 1, "Alert record not created in DB!"
        print("ALL OFFLINE SOS FALLBACK TESTS PASSED!")
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
