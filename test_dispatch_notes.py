import sys
import datetime
import time
import csv
import io
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.models import User, Trip, Alert, AuthorityUser, OTPToken
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
            db.query(Alert).filter(Alert.trip_id == trip.id).delete()
            db.query(Trip).filter(Trip.id == trip.id).delete()
        db.query(User).filter(User.id == user.id).delete()
    
    op = db.query(AuthorityUser).filter(AuthorityUser.email == EMAIL).first()
    if op:
        # Before deleting operator, delete or nullify resolved alerts referencing it to avoid foreign key errors
        db.query(Alert).filter(Alert.resolved_by == op.id).update({Alert.resolved_by: None})
        db.query(AuthorityUser).filter(AuthorityUser.id == op.id).delete()
        
    db.query(OTPToken).filter(OTPToken.phone_number == PHONE).delete()
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

def main():
    db = SessionLocal()
    client = TestClient(app)
    
    try:
        cleanup_test_data(db)
        
        # 1. Setup operator and tourist
        setup_authority_operator(db)
        tourist_token = authenticate_tourist(client, PHONE)
        tourist_headers = {"Authorization": f"Bearer {tourist_token}"}
        
        # Start a trip
        trip_start_data = {
            "start_date": datetime.datetime.utcnow().isoformat(),
            "end_date": (datetime.datetime.utcnow() + datetime.timedelta(hours=4)).isoformat(),
            "region": "Yosemite Valley"
        }
        res = client.post("/api/trips/start", json=trip_start_data, headers=tourist_headers)
        assert res.status_code == 201, f"Failed to start trip: {res.text}"
        trip_id = res.json()["id"]
        print(f"[TRIP] Started trip {trip_id}")
        
        # 2. Trigger an alert (send a ping inside a geofenced area or manually trigger SOS)
        # For simplicity, we can insert an alert directly or trigger via SOS
        print("[TEST] Triggering manual SOS alert...")
        sos_res = client.post(f"/api/trips/{trip_id}/sos", json={"lat": 37.7456, "lng": -119.5332}, headers=tourist_headers)
        assert sos_res.status_code == 200, f"SOS trigger failed: {sos_res.text}"
        db.expire_all()
        alert = db.query(Alert).filter(Alert.trip_id == trip_id, Alert.type == "sos", Alert.status == "open").first()
        assert alert is not None, "Failed to find triggered alert in DB"
        alert_id = alert.id
        print(f"[ALERT] Triggered SOS alert {alert_id}")
        
        # 3. Authenticate Operator
        operator_token = authenticate_operator(client, EMAIL, PASSWORD)
        op_headers = {"Authorization": f"Bearer {operator_token}"}
        
        # 4. Verify Resolution Constraints
        print("\n=== STEP 1: Verifying resolve constraints (min 10 characters) ===")
        # Case A: No body/notes provided
        res_no_body = client.post(f"/api/trips/authority/alerts/{alert_id}/resolve", json={}, headers=op_headers)
        print(f"  No notes response code (expect 422): {res_no_body.status_code}")
        assert res_no_body.status_code == 422, "Expected 422 when resolving alert without body/notes"
        
        # Case B: Too short notes (< 10 characters)
        res_short_notes = client.post(f"/api/trips/authority/alerts/{alert_id}/resolve", json={"dispatch_notes": "Ranger"}, headers=op_headers)
        print(f"  Short notes response code (expect 422): {res_short_notes.status_code}")
        assert res_short_notes.status_code == 422, "Expected 422 when resolving alert with notes < 10 characters"
        
        # Case C: Valid dispatch notes (> 10 characters)
        valid_notes = "Sent Ranger Dave. Located tourist at camp. Tourist is safe."
        print(f"[TEST] Resolving alert with valid notes: '{valid_notes}'")
        res_success = client.post(
            f"/api/trips/authority/alerts/{alert_id}/resolve", 
            json={"dispatch_notes": valid_notes}, 
            headers=op_headers
        )
        print(f"  Successful resolution response code (expect 200): {res_success.status_code}")
        assert res_success.status_code == 200, f"Expected 200 on successful resolution: {res_success.text}"
        
        # Verify DB state
        db.expire_all()
        alert = db.query(Alert).filter(Alert.id == alert_id).first()
        assert alert.status == "resolved", "Alert status should be resolved in DB!"
        assert alert.dispatch_notes == valid_notes, "Dispatch notes was not saved in DB correctly!"
        print("[SUCCESS] DB verified: Alert status is resolved and notes successfully stored.")
        
        # 5. Verify CSV Export
        print("\n=== STEP 2: Verifying CSV Export ===")
        # Build date ranges (today and tomorrow to capture)
        today_str = datetime.date.today().isoformat()
        tomorrow_str = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        
        # Call GET /api/alerts/export with query param auth token (simulating browser download)
        export_res = client.get(f"/api/alerts/export?from={today_str}&to={tomorrow_str}&token={operator_token}")
        assert export_res.status_code == 200, f"CSV Export failed: {export_res.text}"
        assert export_res.headers["content-type"].startswith("text/csv"), "Expected text/csv content-type"
        
        csv_content = export_res.text
        print("\n=== SAMPLE CSV OUTPUT ===")
        print(csv_content)
        print("==========================\n")
        
        # Parse CSV to confirm values
        csv_file = io.StringIO(csv_content)
        reader = csv.reader(csv_file)
        rows = list(reader)
        
        # Header verification
        headers = rows[0]
        print(f"CSV Headers: {headers}")
        assert "Alert ID" in headers
        assert "Dispatch Notes" in headers
        
        # Find our row
        our_row = None
        for row in rows[1:]:
            if row[0] == str(alert_id):
                our_row = row
                break
                
        assert our_row is not None, f"Alert ID {alert_id} not found in exported CSV!"
        print(f"Exported row: {our_row}")
        
        # Assert column values in CSV
        # Headers: Alert ID (0), Trip ID (1), Tourist Phone (2), Alert Type (3), Lat (4), Lng (5), Triggered (6), Resolved (7), Resolver (8), Notes (9)
        assert our_row[2] == PHONE, f"Expected phone {PHONE}, got {our_row[2]}"
        assert our_row[3] == "sos", f"Expected type 'sos', got {our_row[3]}"
        assert our_row[9] == valid_notes, f"Expected notes '{valid_notes}', got {our_row[9]}"
        
        print("\n========================================")
        print("ALL DISPATCH NOTES & CSV TESTS PASSED!")
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
