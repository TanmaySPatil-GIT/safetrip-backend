import sys
import datetime
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.models import User, Trip, DangerZone, TripFeedback, AuthorityUser, OTPToken
from app.core.security import get_password_hash

PHONE = "+19998889999"
EMAIL = "operator@yosemite.gov"
PASSWORD = "password123"

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test data...")
    user = db.query(User).filter(User.phone_number == PHONE).first()
    if user:
        trips = db.query(Trip).filter(Trip.user_id == user.id).all()
        for trip in trips:
            db.query(TripFeedback).filter(TripFeedback.trip_id == trip.id).delete()
            db.query(Trip).filter(Trip.id == trip.id).delete()
        db.query(User).filter(User.id == user.id).delete()
    
    op = db.query(AuthorityUser).filter(AuthorityUser.email == EMAIL).first()
    if op:
        db.query(AuthorityUser).filter(AuthorityUser.id == op.id).delete()
        
    db.query(OTPToken).filter(OTPToken.phone_number == PHONE).delete()
    
    # Clean up Yosemite National Park feedbacks if any remain
    db.commit()
    print("[CLEANUP] Cleanup complete.")

def authenticate_tourist(client, phone: str) -> str:
    print(f"[AUTH] Authenticating test tourist {phone}...")
    otp_res = client.post("/api/auth/tourist/otp", json={"phone_number": phone})
    assert otp_res.status_code == 200, f"OTP request failed: {otp_res.text}"
    verify_res = client.post("/api/auth/tourist/verify", json={
        "phone_number": phone,
        "code": "123456"
    })
    assert verify_res.status_code == 200, f"OTP verification failed: {verify_res.text}"
    return verify_res.json()["access_token"]

def setup_authority_operator(db):
    print(f"[SETUP] Ensuring operator account {EMAIL} exists...")
    op = db.query(AuthorityUser).filter(AuthorityUser.email == EMAIL).first()
    if not op:
        op = AuthorityUser(
            name="Yosemite Operator",
            email=EMAIL,
            password_hash=get_password_hash(PASSWORD),
            role="operator"
        )
        db.add(op)
        db.commit()
        db.refresh(op)
    return op

def authenticate_operator(client, email: str, password: str) -> str:
    print(f"[AUTH] Authenticating operator {email}...")
    res = client.post("/api/auth/authority/login", json={
        "email": email,
        "password": password
    })
    assert res.status_code == 200, f"Operator login failed: {res.text}"
    return res.json()["access_token"]

def setup_danger_zone(db):
    print("[SETUP] Ensuring danger zone 'Mist Trail' exists...")
    zone = db.query(DangerZone).filter(DangerZone.name == "Mist Trail").first()
    if not zone:
        zone = DangerZone(
            name="Mist Trail",
            polygon_coordinates=[
                [37.72, -119.54],
                [37.73, -119.54],
                [37.73, -119.53],
                [37.72, -119.53]
            ],
            risk_level="high",
            computed_risk_score=75.0
        )
        db.add(zone)
        db.commit()
        db.refresh(zone)
    return zone

def main():
    db = SessionLocal()
    client = TestClient(app)
    
    try:
        cleanup_test_data(db)
        setup_authority_operator(db)
        setup_danger_zone(db)
        
        # Authenticate
        tourist_token = authenticate_tourist(client, PHONE)
        tourist_headers = {"Authorization": f"Bearer {tourist_token}"}
        
        op_token = authenticate_operator(client, EMAIL, PASSWORD)
        op_headers = {"Authorization": f"Bearer {op_token}"}
        
        # Start a trip
        trip_start_data = {
            "start_date": datetime.datetime.utcnow().isoformat(),
            "end_date": (datetime.datetime.utcnow() + datetime.timedelta(hours=4)).isoformat(),
            "region": "Yosemite National Park"
        }
        res = client.post("/api/trips/start", json=trip_start_data, headers=tourist_headers)
        assert res.status_code == 201, f"Failed to start trip: {res.text}"
        trip_id = res.json()["id"]
        print(f"[TRIP] Started trip {trip_id}")
        
        # 1. Verify Submit Feedback validation constraints
        print("\n=== STEP 1: Verifying submit feedback validation ===")
        # Invalid rating ge=1 le=5
        bad_rating_res = client.post(
            f"/api/trips/{trip_id}/feedback",
            json={
                "rating": 6,
                "felt_unsafe": False
            },
            headers=tourist_headers
        )
        print(f"  Invalid rating response code (expect 422): {bad_rating_res.status_code}")
        assert bad_rating_res.status_code == 422
        
        # 2. Submit valid feedback (rating=4, felt_unsafe=True)
        print("\n=== STEP 2: Submitting valid feedback ===")
        feedback_data = {
            "rating": 4,
            "felt_unsafe": True,
            "unsafe_location": "Mist Trail near the waterfall",
            "suggestions": "Add safety ropes near wet stairs."
        }
        success_res = client.post(
            f"/api/trips/{trip_id}/feedback",
            json=feedback_data,
            headers=tourist_headers
        )
        print(f"  Submit feedback response code (expect 201): {success_res.status_code}")
        assert success_res.status_code == 201
        
        # Verify db persistence
        db.expire_all()
        fb_db = db.query(TripFeedback).filter(TripFeedback.trip_id == trip_id).first()
        assert fb_db is not None
        assert fb_db.rating == 4
        assert fb_db.felt_unsafe is True
        assert fb_db.unsafe_location == "Mist Trail near the waterfall"
        assert fb_db.suggestions == "Add safety ropes near wet stairs."
        print("[SUCCESS] Feedback saved correctly in db.")
        
        # Try duplicate submission (expect 400)
        dup_res = client.post(
            f"/api/trips/{trip_id}/feedback",
            json=feedback_data,
            headers=tourist_headers
        )
        print(f"  Duplicate submission response code (expect 400): {dup_res.status_code}")
        assert dup_res.status_code == 400
        
        # 3. Verify Feedback Summary on Authority Dashboard
        print("\n=== STEP 3: Verifying Feedback Summary on Dashboard ===")
        summary_res = client.get("/api/trips/authority/feedback-summary", headers=op_headers)
        assert summary_res.status_code == 200, f"Failed to get feedback summary: {summary_res.text}"
        summary_data = summary_res.json()
        
        print("\n=== FEEDBACK SUMMARY PAYLOAD ===")
        print(summary_data)
        print("================================")
        
        # Check Average safety rating per region
        regions = summary_data["region_avg_ratings"]
        yosemite_region = next((r for r in regions if r["region"] == "Yosemite National Park"), None)
        assert yosemite_region is not None, "Yosemite National Park average rating missing!"
        assert yosemite_region["avg_rating"] == 4.0, f"Expected Yosemite avg rating 4.0, got {yosemite_region['avg_rating']}"
        print(f"[SUCCESS] Avg rating verified: {yosemite_region['region']} -> {yosemite_region['avg_rating']} stars")
        
        # Check Count of "felt unsafe" reports per danger zone
        zones = summary_data["zone_felt_unsafe_counts"]
        mist_trail_zone = next((z for z in zones if z["zone_name"] == "Mist Trail"), None)
        assert mist_trail_zone is not None, "Mist Trail zone report missing!"
        assert mist_trail_zone["felt_unsafe_count"] >= 1, f"Expected Mist Trail count >= 1, got {mist_trail_zone['felt_unsafe_count']}"
        print(f"[SUCCESS] Danger zone felt unsafe report counted: {mist_trail_zone['zone_name']} -> {mist_trail_zone['felt_unsafe_count']} reports")
        
        # Check Latest Suggestions
        suggs = summary_data["latest_suggestions"]
        assert len(suggs) > 0, "No suggestions returned!"
        assert suggs[0]["suggestions"] == "Add safety ropes near wet stairs.", f"Suggestions mismatch: {suggs[0]['suggestions']}"
        print(f"[SUCCESS] Latest traveler suggestions verified: {suggs[0]['suggestions']}")
        
        print("\n========================================")
        print("ALL POST-TRIP FEEDBACK TESTS PASSED!")
        print("========================================")
        
    except Exception as e:
        print(f"[FAIL] Test execution encountered error: {e}")
        sys.exit(1)
    finally:
        cleanup_test_data(db)
        db.close()

if __name__ == "__main__":
    main()
