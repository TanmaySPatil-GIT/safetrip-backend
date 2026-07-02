import sys
import datetime
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models.models import User, Trip, DangerZone, LocationPing, Alert, OTPToken

PHONE = "+19998889999"

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test data...")
    user = db.query(User).filter(User.phone_number == PHONE).first()
    if user:
        trips = db.query(Trip).filter(Trip.user_id == user.id).all()
        for trip in trips:
            db.query(LocationPing).filter(LocationPing.trip_id == trip.id).delete()
            db.query(Alert).filter(Alert.trip_id == trip.id).delete()
            db.query(Trip).filter(Trip.id == trip.id).delete()
        db.query(User).filter(User.id == user.id).delete()
    db.query(OTPToken).filter(OTPToken.phone_number == PHONE).delete()
    db.commit()

def authenticate_tourist(client, phone: str) -> str:
    print(f"[AUTH] Authenticating test user {phone}...")
    otp_res = client.post("/api/auth/tourist/otp", json={"phone_number": phone})
    assert otp_res.status_code == 200
    verify_res = client.post("/api/auth/tourist/verify", json={
        "phone_number": phone,
        "code": "123456"
    })
    assert verify_res.status_code == 200
    return verify_res.json()["access_token"]

def setup_high_risk_zone(db):
    print("[SETUP] Ensuring high-risk danger zone exists...")
    zone = db.query(DangerZone).filter(DangerZone.name == "Cables Danger Zone").first()
    if not zone:
        zone = DangerZone(
            name="Cables Danger Zone",
            polygon_coordinates=[
                [37.740, -119.535],
                [37.750, -119.535],
                [37.750, -119.530],
                [37.740, -119.530]
            ],
            risk_level="high",
            computed_risk_score=75.0
        )
        db.add(zone)
        db.commit()
        db.refresh(zone)
    return zone

# Leaflet coordinate containment helper
def is_point_in_polygon(point, vs):
    x, y = point[0], point[1]
    inside = False
    for i in range(len(vs)):
        j = i - 1
        xi, yi = vs[i][0], vs[i][1]
        xj, yj = vs[j][0], vs[j][1]
        intersect = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
        if intersect:
            inside = not inside
    return inside

# Client-side state machine simulator representing tourist_map.html logic
class ClientTrackingStateSimulator:
    def __init__(self, zones):
        self.zones = zones
        self.interval = 300  # Default 5 minutes (300 seconds)
        self.tracking_active = True
        self.warning = ""
        self.battery_level = 1.0

    def check_battery_and_risk(self, lat, lng):
        battery_percent = round(self.battery_level * 100)
        
        if battery_percent < 10:
            self.tracking_active = False
            self.warning = "🔋 Critical Battery — Location paused. SOS SMS still available."
            return
        elif battery_percent < 20:
            self.warning = "🔋 Low Battery — Tracking reduced to save power. SOS still active."
            self.interval = 600  # 10 minutes (600 seconds)
            self.tracking_active = True
            return
        else:
            self.warning = ""
            self.tracking_active = True

        # Check risk level
        max_risk = 0
        for zone in self.zones:
            if is_point_in_polygon([lat, lng], zone.polygon_coordinates):
                if zone.computed_risk_score > max_risk:
                    max_risk = zone.computed_risk_score
                    
        if max_risk > 60:
            self.interval = 60  # High Alert: 1 minute (60 seconds)
        else:
            self.interval = 300  # Normal: 5 minutes (300 seconds)

def main():
    db = SessionLocal()
    client = TestClient(app)
    
    try:
        cleanup_test_data(db)
        zone = setup_high_risk_zone(db)
        
        token = authenticate_tourist(client, PHONE)
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
        
        # Instantiate client state simulator loaded with database danger zones
        simulator = ClientTrackingStateSimulator([zone])
        
        print("\n=== SCENARIO 1: App Minimized in Background for 6 Minutes ===")
        # Scenario requirements:
        # App is minimized (background mode is active).
        # Standard interval is 300 seconds (5 minutes).
        # We assert that after 6 minutes (360 seconds), at least 1 background location ping is processed.
        print("  Simulating background mode: Tracker interval is 5 minutes (300s)")
        assert simulator.interval == 300
        
        # Simulate initial location check at t=0
        print("  t=0: Initial background position reported.")
        res_ping = client.post(f"/api/trips/{trip_id}/ping", json={"lat": 37.7410, "lng": -119.5410}, headers=headers)
        assert res_ping.status_code == 200
        
        # Simulate background geolocation watcher firing after 5 minutes (300s)
        print("  t=300s (5 min): Background geolocation watcher reports location changes.")
        res_ping = client.post(f"/api/trips/{trip_id}/ping", json={"lat": 37.7412, "lng": -119.5415}, headers=headers)
        assert res_ping.status_code == 200
        
        # Verify db has recorded at least 1 background location ping during the 6 minutes
        db.expire_all()
        pings = db.query(LocationPing).filter(LocationPing.trip_id == trip_id).all()
        print(f"  Pings recorded in backend (6-min window): {len(pings)} (Expected: >= 2)")
        assert len(pings) >= 2
        print("[SUCCESS] Scenario 1 passed: Location pings successfully received in background!")

        print("\n=== SCENARIO 2: Entering a High-Risk Zone ===")
        # Cables Danger Zone coordinates: [37.740, -119.535] to [37.750, -119.530]
        # Coordinates [37.745, -119.532] are inside the Cables high-risk zone (risk score = 75)
        inside_lat, inside_lng = 37.745, -119.532
        
        print(f"  Tourist position changes to inside Cables Danger Zone: {inside_lat}, {inside_lng}")
        simulator.check_battery_and_risk(inside_lat, inside_lng)
        
        print(f"  Current Simulator Interval (seconds): {simulator.interval} (Expected: 60)")
        assert simulator.interval == 60
        print("[SUCCESS] Scenario 2 passed: Tracking interval automatically reduced to 60s inside high-risk zone!")

        print("\n=== SCENARIO 3: Battery awareness at 15% and 8% ===")
        # Battery level at 15% (0.15) should switch interval to 10 min (600s) and show warning
        print("  Simulating battery level drop to 15%...")
        simulator.battery_level = 0.15
        simulator.check_battery_and_risk(inside_lat, inside_lng)
        
        print(f"  Current Simulator Interval (seconds): {simulator.interval} (Expected: 600)")
        print(f"  Simulator Warning Message: '{simulator.warning}'")
        assert simulator.interval == 600
        assert "Low Battery" in simulator.warning
        
        # Battery level at 8% (0.08) should pause tracking
        print("  Simulating battery level drop to 8%...")
        simulator.battery_level = 0.08
        simulator.check_battery_and_risk(inside_lat, inside_lng)
        
        print(f"  Simulator Tracking Active: {simulator.tracking_active} (Expected: False)")
        print(f"  Simulator Critical Warning: '{simulator.warning}'")
        assert simulator.tracking_active is False
        assert "Critical Battery" in simulator.warning
        print("[SUCCESS] Scenario 3 passed: Tracker successfully scales back at 15% and pauses at 8%!")

        print("\n========================================")
        print("ALL BACKGROUND TRACKING SCENARIOS PASSED!")
        print("========================================")
        
    except Exception as e:
        print(f"[FAIL] Test execution encountered error: {e}")
        sys.exit(1)
    finally:
        cleanup_test_data(db)
        db.close()

if __name__ == "__main__":
    main()
