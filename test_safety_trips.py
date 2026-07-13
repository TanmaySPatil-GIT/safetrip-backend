import datetime
import sys
# pyrefly: ignore [missing-import]
from fastapi.testclient import TestClient

# Ensure backend folder is in path
from app.main import app
from app.core.database import SessionLocal
from app.models.models import DangerZone, User, Trip, LocationPing, Alert, OTPToken

client = TestClient(app)

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test data...")
    # Delete test danger zones
    db.query(DangerZone).filter(DangerZone.name == "Yosemite Danger Zone Test").delete()
    
    # Find test user
    user = db.query(User).filter(User.phone_number == "+19998887777").first()
    if user:
        # Delete alerts, pings, trips associated with user
        trips = db.query(Trip).filter(Trip.user_id == user.id).all()
        for trip in trips:
            db.query(Alert).filter(Alert.trip_id == trip.id).delete()
            db.query(LocationPing).filter(LocationPing.trip_id == trip.id).delete()
            db.query(Trip).filter(Trip.id == trip.id).delete()
        db.query(User).filter(User.id == user.id).delete()
    
    # Delete any lingering OTPs
    db.query(OTPToken).filter(OTPToken.phone_number == "+19998887777").delete()
    db.commit()
    print("[CLEANUP] Cleanup complete.")

def main():
    db = SessionLocal()
    try:
        # Pre-cleanup in case of previous failures
        cleanup_test_data(db)

        # 1. Seed the Danger Zone
        print("[SEED] Seeding danger zone...")
        # Yosemite Danger Zone bounding box
        poly = [
            [37.745, -119.535],
            [37.747, -119.535],
            [37.747, -119.531],
            [37.745, -119.531]
        ]
        danger_zone = DangerZone(
            name="Yosemite Danger Zone Test",
            polygon_coordinates=poly,
            risk_level="high",
            computed_risk_score=90.0
        )
        db.add(danger_zone)
        db.commit()
        db.refresh(danger_zone)
        print(f"[SEED] Created danger zone with ID: {danger_zone.id}")

        # 2. Authenticate Tourist User
        print("[AUTH] Authenticating test user...")
        # Call OTP request
        otp_res = client.post("/api/auth/tourist/otp", json={"phone_number": "+19998887777"})
        assert otp_res.status_code == 200, f"OTP request failed: {otp_res.text}"
        
        # Verify using backdoor code '123456'
        verify_res = client.post("/api/auth/tourist/verify", json={
            "phone_number": "+19998887777",
            "code": "123456"
        })
        assert verify_res.status_code == 200, f"OTP verification failed: {verify_res.text}"
        auth_data = verify_res.json()
        token = auth_data["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"[AUTH] Logged in successfully. Token: {token[:20]}...")

        # ----------------------------------------------------
        # TEST CASE 1: Geofence Trigger
        # ----------------------------------------------------
        print("\n=== TEST CASE 1: Geofence Trigger ===")
        # Start trip
        trip_start_data = {
            "start_date": datetime.datetime.utcnow().isoformat(),
            "end_date": (datetime.datetime.utcnow() + datetime.timedelta(hours=5)).isoformat(),
            "region": "Yosemite National Park"
        }
        res = client.post("/api/trips/start", json=trip_start_data, headers=headers)
        assert res.status_code == 201, f"Failed to start trip: {res.text}"
        trip = res.json()
        trip_id = trip["id"]
        print(f"[TRIP] Started trip {trip_id}")

        # Ping 1: Safe point (outside danger zone)
        print("[PING] Sending safe ping outside danger zone...")
        ping_res = client.post(
            f"/api/trips/{trip_id}/ping",
            json={"lat": 37.700, "lng": -119.500, "timestamp": datetime.datetime.utcnow().isoformat()},
            headers=headers
        )
        assert ping_res.status_code == 200, ping_res.text
        ping_data = ping_res.json()
        print(f"[PING RESPONSE] {ping_data}")
        assert len(ping_data["alerts_triggered"]) == 0, "Should not trigger any alerts"

        # Ping 2: Inside Danger Zone
        print("[PING] Sending ping inside danger zone...")
        ping_res = client.post(
            f"/api/trips/{trip_id}/ping",
            json={"lat": 37.7456, "lng": -119.5332, "timestamp": datetime.datetime.utcnow().isoformat()},
            headers=headers
        )
        assert ping_res.status_code == 200, ping_res.text
        ping_data = ping_res.json()
        print(f"[PING RESPONSE] {ping_data}")
        assert "geofence" in ping_data["alerts_triggered"], "Should trigger 'geofence' alert"

        # End Trip 1
        res = client.post(f"/api/trips/{trip_id}/end", headers=headers)
        assert res.status_code == 200, f"Failed to end trip: {res.text}"
        print(f"[TRIP] Ended trip {trip_id}")

        # ----------------------------------------------------
        # TEST CASE 2: Stationary Distress Trigger
        # ----------------------------------------------------
        print("\n=== TEST CASE 2: Stationary Distress Trigger ===")
        # Start trip
        res = client.post("/api/trips/start", json=trip_start_data, headers=headers)
        assert res.status_code == 201, f"Failed to start trip: {res.text}"
        trip = res.json()
        trip_id = trip["id"]
        print(f"[TRIP] Started trip {trip_id}")

        # Send 4 pings at same location spaced by 5 minutes (300 seconds)
        base_time = datetime.datetime.utcnow()
        for i in range(4):
            ping_time = base_time + datetime.timedelta(seconds=300 * i)
            print(f"[PING] Sending stationary ping {i+1} at simulated time {ping_time.isoformat()}...")
            ping_res = client.post(
                f"/api/trips/{trip_id}/ping",
                json={"lat": 37.700, "lng": -119.500, "timestamp": ping_time.isoformat()},
                headers=headers
            )
            assert ping_res.status_code == 200, ping_res.text
            ping_data = ping_res.json()
            print(f"[PING RESPONSE] {ping_data}")
            if i == 3:
                # 4th ping is 15 minutes after 1st ping (900 seconds)
                assert "distress_stationary" in ping_data["alerts_triggered"], "Should trigger 'distress_stationary' alert"
            else:
                assert "distress_stationary" not in ping_data["alerts_triggered"], "Should not trigger 'distress_stationary' yet"

        # End Trip 2
        res = client.post(f"/api/trips/{trip_id}/end", headers=headers)
        assert res.status_code == 200, f"Failed to end trip: {res.text}"
        print(f"[TRIP] Ended trip {trip_id}")

        # ----------------------------------------------------
        # TEST CASE 3: Speed Drop Distress Trigger
        # ----------------------------------------------------
        print("\n=== TEST CASE 3: Speed Drop Distress Trigger ===")
        # Start trip
        res = client.post("/api/trips/start", json=trip_start_data, headers=headers)
        assert res.status_code == 201, f"Failed to start trip: {res.text}"
        trip = res.json()
        trip_id = trip["id"]
        print(f"[TRIP] Started trip {trip_id}")

        # Send 3 pings:
        # Ping 1: t=0, lat=37.700, lng=-119.500
        # Ping 2: t=10, lat=37.701, lng=-119.500 (distance = 111m, speed = 11.1m/s >= 5.0)
        # Ping 3: t=20, lat=37.701, lng=-119.500 (distance = 0m, speed = 0.0m/s < 0.5)
        base_time = datetime.datetime.utcnow()
        
        # Ping 1
        ping_res = client.post(
            f"/api/trips/{trip_id}/ping",
            json={"lat": 37.700, "lng": -119.500, "timestamp": base_time.isoformat()},
            headers=headers
        )
        assert ping_res.status_code == 200, ping_res.text
        print(f"[PING 1 RESPONSE] {ping_res.json()}")

        # Ping 2
        ping_res = client.post(
            f"/api/trips/{trip_id}/ping",
            json={"lat": 37.701, "lng": -119.500, "timestamp": (base_time + datetime.timedelta(seconds=10)).isoformat()},
            headers=headers
        )
        assert ping_res.status_code == 200, ping_res.text
        print(f"[PING 2 RESPONSE] {ping_res.json()}")

        # Ping 3
        ping_res = client.post(
            f"/api/trips/{trip_id}/ping",
            json={"lat": 37.701, "lng": -119.500, "timestamp": (base_time + datetime.timedelta(seconds=20)).isoformat()},
            headers=headers
        )
        assert ping_res.status_code == 200, ping_res.text
        ping_data = ping_res.json()
        print(f"[PING 3 RESPONSE] {ping_data}")
        assert "distress_speed_drop" in ping_data["alerts_triggered"], "Should trigger 'distress_speed_drop' alert"

        # End Trip 3
        res = client.post(f"/api/trips/{trip_id}/end", headers=headers)
        assert res.status_code == 200, f"Failed to end trip: {res.text}"
        print(f"[TRIP] Ended trip {trip_id}")

        # ----------------------------------------------------
        # TEST CASE 4: Signal Loss Distress Trigger
        # ----------------------------------------------------
        print("\n=== TEST CASE 4: Signal Loss Distress Trigger ===")
        # Start trip
        res = client.post("/api/trips/start", json=trip_start_data, headers=headers)
        assert res.status_code == 201, f"Failed to start trip: {res.text}"
        trip = res.json()
        trip_id = trip["id"]
        print(f"[TRIP] Started trip {trip_id}")

        # Send Ping 1 (inside Danger Zone) at t = 0
        base_time = datetime.datetime.utcnow()
        ping_res = client.post(
            f"/api/trips/{trip_id}/ping",
            json={"lat": 37.7456, "lng": -119.5332, "timestamp": base_time.isoformat()},
            headers=headers
        )
        assert ping_res.status_code == 200, ping_res.text
        print(f"[PING 1 RESPONSE] {ping_res.json()}")

        # Send Ping 2 (outside) at t = 600 (10 minutes later)
        ping_res = client.post(
            f"/api/trips/{trip_id}/ping",
            json={"lat": 37.700, "lng": -119.500, "timestamp": (base_time + datetime.timedelta(seconds=600)).isoformat()},
            headers=headers
        )
        assert ping_res.status_code == 200, ping_res.text
        ping_data = ping_res.json()
        print(f"[PING 2 RESPONSE] {ping_data}")
        assert "distress_signal_loss" in ping_data["alerts_triggered"], "Should trigger 'distress_signal_loss' alert"

        # End Trip 4
        res = client.post(f"/api/trips/{trip_id}/end", headers=headers)
        assert res.status_code == 200, f"Failed to end trip: {res.text}"
        print(f"[TRIP] Ended trip {trip_id}")

        print("\n========================================")
        print("ALL TESTS PASSED SUCCESSFULLY!")
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
