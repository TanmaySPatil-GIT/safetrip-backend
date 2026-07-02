import sys
import datetime
import io
from fastapi.testclient import TestClient
from unittest.mock import patch
import requests

from app.main import app
from app.core.database import SessionLocal
from app.models.models import User, Trip, Alert, LocationPing, OTPToken
from app.core.config import settings

PHONE = "+19998889999"
CONTACT = "+91XXXXXXXXXX"

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test data...")
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

def authenticate_user(client, phone: str) -> str:
    otp_res = client.post("/api/auth/tourist/otp", json={"phone_number": phone})
    assert otp_res.status_code == 200
    verify_res = client.post("/api/auth/tourist/verify", json={
        "phone_number": phone,
        "code": "123456"
    })
    assert verify_res.status_code == 200
    return verify_res.json()["access_token"]

def main():
    db = SessionLocal()
    client = TestClient(app)
    
    try:
        cleanup_test_data(db)
        
        # Configure Twilio mocks
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
        assert res.status_code == 201
        trip_id = res.json()["id"]
        
        # We need a location ping in DB so that SOS payload works
        client.post(f"/api/trips/{trip_id}/ping", json={"lat": 37.7456, "lng": -119.5332}, headers=headers)

        print("\n=== TEST 1: SET HINDI & TRIGGER SOS ===")
        # 1. Update preferred language to Hindi ('hi')
        lang_res = client.put("/api/auth/tourist/language", json={"preferred_language": "hi"}, headers=headers)
        assert lang_res.status_code == 200
        assert lang_res.json()["preferred_language"] == "hi"
        print("  Successfully set language to 'hi' in database profile.")

        # Intercept output to capture Twilio SMS formatting
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        
        try:
            with patch("requests.post", side_effect=requests.exceptions.ConnectionError("simulated")):
                sos_res = client.post(f"/api/trips/{trip_id}/sos", json={"lat": 37.7456, "lng": -119.5332, "is_offline": True}, headers=headers)
            hi_captured = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
            
        print("  SOS Response code:", sos_res.status_code)
        assert sos_res.status_code == 200
        print("  Captured Hindi SMS Output:")
        print(hi_captured)
        
        # Assertions on Hindi translation markers
        assert "सेफट्रिप आपातकाल अलर्ट" in hi_captured
        assert f"पर्यटक: {PHONE}" in hi_captured
        assert "अंतिम स्थान: 37.7456, -119.5332" in hi_captured
        assert "समय:" in hi_captured
        assert "नक्शा:" in hi_captured
        assert "यह सेफट्रिप से एक स्वचालित आपातकालीन अलर्ट है।" in hi_captured
        print("[SUCCESS] Hindi SMS format verified!")

        print("\n=== TEST 2: SET MARATHI & TRIGGER SOS ===")
        # 2. Update preferred language to Marathi ('mr')
        lang_res = client.put("/api/auth/tourist/language", json={"preferred_language": "mr"}, headers=headers)
        assert lang_res.status_code == 200
        print("  Successfully set language to 'mr' in database profile.")

        sys.stdout = io.StringIO()
        try:
            with patch("requests.post", side_effect=requests.exceptions.ConnectionError("simulated")):
                sos_res = client.post(f"/api/trips/{trip_id}/sos", json={"lat": 37.7456, "lng": -119.5332, "is_offline": True}, headers=headers)
            mr_captured = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
            
        print("  SOS Response code:", sos_res.status_code)
        assert sos_res.status_code == 200
        print("  Captured Marathi SMS Output:")
        print(mr_captured)
        
        # Assertions on Marathi translation markers
        assert "सेफट्रिप आपत्कालीन अलर्ट" in mr_captured
        assert f"पर्यटक: {PHONE}" in mr_captured
        assert "अंतिम स्थान: 37.7456, -119.5332" in mr_captured
        assert "वेळ:" in mr_captured
        assert "नकाशा:" in mr_captured
        assert "हा सेफट्रिप कडून स्वयंचलित आपत्कालीन अलर्ट आहे।" in mr_captured
        print("[SUCCESS] Marathi SMS format verified!")

        print("\n=== TEST 3: SET ENGLISH & TRIGGER SOS ===")
        # 3. Update preferred language to English ('en')
        lang_res = client.put("/api/auth/tourist/language", json={"preferred_language": "en"}, headers=headers)
        assert lang_res.status_code == 200
        print("  Successfully set language to 'en' in database profile.")

        sys.stdout = io.StringIO()
        try:
            with patch("requests.post", side_effect=requests.exceptions.ConnectionError("simulated")):
                sos_res = client.post(f"/api/trips/{trip_id}/sos", json={"lat": 37.7456, "lng": -119.5332, "is_offline": True}, headers=headers)
            en_captured = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout
            
        print("  SOS Response code:", sos_res.status_code)
        assert sos_res.status_code == 200
        print("  Captured English SMS Output:")
        print(en_captured)
        
        # Assertions on English translation markers
        assert "SAFETRIP SOS ALERT" in en_captured
        assert f"Tourist: {PHONE}" in en_captured
        assert "Last Location: 37.7456, -119.5332" in en_captured
        assert "Time:" in en_captured
        assert "Maps:" in en_captured
        assert "This is an automated emergency alert from SafeTrip." in en_captured
        print("[SUCCESS] English SMS format verified!")

        print("\n========================================")
        print("ALL MULTILINGUAL SOS SMS TESTS PASSED!")
        print("========================================")
        
    except Exception as e:
        print(f"[FAIL] Test execution encountered error: {e}")
        sys.exit(1)
    finally:
        cleanup_test_data(db)
        db.close()

if __name__ == "__main__":
    main()
