import sys
from fastapi.testclient import TestClient

# Ensure backend folder is in path
from app.main import app
from app.core.database import SessionLocal
from app.models.models import DangerZone, User, OTPToken

client = TestClient(app)

PHONE = "+19997776666"

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test data for briefing...")
    user = db.query(User).filter(User.phone_number == PHONE).first()
    if user:
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
        
        # Authenticate user
        token = authenticate_user(PHONE)
        headers = {"Authorization": f"Bearer {token}"}
        
        # Fetch briefing for Yosemite National Park
        print("\n=== STEP 3: Requesting Briefing for Yosemite National Park ===")
        briefing_payload = {"region": "Yosemite National Park"}
        res = client.post("/api/trips/briefing", json=briefing_payload, headers=headers)
        assert res.status_code == 200, f"Briefing request failed: {res.text}"
        data = res.json()
        
        print("\n=== BRIEFING RESULTS ===")
        print(f"Region: {data['region']}")
        print(f"Safe Hours: {data['safe_hours']}")
        print("Weather Info:")
        print(f"  - Temperature: {data['weather']['temp']} °C")
        print(f"  - Condition: {data['weather']['condition']}")
        print(f"  - Rainfall Status: {data['weather']['rainfall_status']}")
        print(f"  - Warning: {data['weather']['is_warning']}")
        
        print("Danger Zones:")
        for zone in data["danger_zones"]:
            print(f"  - {zone['name']} | Score: {zone['risk_score']} | Level: {zone['risk_level']}")
            
        print("Safety Tips (Rule-Based):")
        for tip in data["safety_tips"]:
            print(f"  - {tip}")
            
        print("Warnings:")
        for w in data["warnings"]:
            print(f"  - {w}")
            
        # Assertions
        assert data["region"] == "Yosemite National Park"
        assert len(data["danger_zones"]) > 0, "No danger zones matched!"
        assert any("Half Dome" in z["name"] for z in data["danger_zones"]), "Half Dome Cables zone missing!"
        assert len(data["safety_tips"]) == 3, f"Expected 3 safety tips, got {len(data['safety_tips'])}"
        
        # Confirm weather fields are present
        assert "temp" in data["weather"]
        assert "condition" in data["weather"]
        assert "rainfall_status" in data["weather"]
        
        # Confirm estimated safe hours is present
        assert len(data["safe_hours"]) > 0
        
        print("\n========================================")
        print("ALL BRIEFING TESTS PASSED SUCCESSFULLY!")
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
