import datetime
import sys
from fastapi.testclient import TestClient

# Ensure backend folder is in path
from app.main import app
from app.core.database import SessionLocal
from app.models.models import DangerZone, User, Trip, LocationPing, Alert, OTPToken, TripGroup, GroupAlert

client = TestClient(app)

PHONES = ["+19991112222", "+19992223333", "+19993334444"]

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test data for Trip Buddy...")
    
    # Resolve and delete group alerts
    db.query(GroupAlert).delete()
    
    # Delete trip groups
    db.query(TripGroup).delete()

    # Find test users and delete alerts, pings, trips associated with them
    for phone in PHONES:
        user = db.query(User).filter(User.phone_number == phone).first()
        if user:
            trips = db.query(Trip).filter(Trip.user_id == user.id).all()
            for trip in trips:
                db.query(Alert).filter(Alert.trip_id == trip.id).delete()
                db.query(LocationPing).filter(LocationPing.trip_id == trip.id).delete()
                db.query(Trip).filter(Trip.id == trip.id).delete()
            db.query(User).filter(User.id == user.id).delete()
        
        # Delete any lingering OTPs
        db.query(OTPToken).filter(OTPToken.phone_number == phone).delete()
    
    db.commit()
    print("[CLEANUP] Cleanup complete.")

def authenticate_user(phone: str) -> str:
    print(f"[AUTH] Authenticating test user {phone}...")
    otp_res = client.post("/api/auth/tourist/otp", json={"phone_number": phone})
    assert otp_res.status_code == 200, f"OTP request failed: {otp_res.text}"
    
    # Verify using backdoor code '123456'
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
        # Pre-cleanup
        cleanup_test_data(db)

        # 1. Authenticate 3 Tourists
        tokens = []
        headers_list = []
        for phone in PHONES:
            token = authenticate_user(phone)
            tokens.append(token)
            headers_list.append({"Authorization": f"Bearer {token}"})

        # 2. Start Active Trips for all 3 Tourists
        trip_ids = []
        trip_start_data = {
            "start_date": datetime.datetime.utcnow().isoformat(),
            "end_date": (datetime.datetime.utcnow() + datetime.timedelta(hours=5)).isoformat(),
            "region": "Yosemite Valley"
        }
        for idx, phone in enumerate(PHONES):
            print(f"[TRIP] Starting trip for Tourist {idx+1} ({phone})...")
            res = client.post("/api/trips/start", json=trip_start_data, headers=headers_list[idx])
            assert res.status_code == 201, f"Failed to start trip: {res.text}"
            trip = res.json()
            trip_ids.append(trip["id"])
            print(f"[TRIP] Started trip {trip['id']}")

        # 3. Tourist 1 creates a group trip
        print("\n=== STEP 3: Tourist 1 creates group trip ===")
        res = client.post("/api/trips/group/create", headers=headers_list[0])
        assert res.status_code == 201, f"Failed to create group: {res.text}"
        group_data = res.json()
        join_code = group_data["join_code"]
        group_id = group_data["id"]
        print(f"[GROUP] Created group {group_id} with Join Code: {join_code}")

        # 4. Tourists 2 and 3 join the group trip
        print("\n=== STEP 4: Tourists 2 and 3 join group ===")
        join_payload = {"join_code": join_code}
        
        # Tourist 2 joins
        res = client.post("/api/trips/group/join", json=join_payload, headers=headers_list[1])
        assert res.status_code == 200, f"Tourist 2 failed to join group: {res.text}"
        print(f"[GROUP] Tourist 2 joined successfully. Members: {res.json()['members']}")

        # Tourist 3 joins
        res = client.post("/api/trips/group/join", json=join_payload, headers=headers_list[2])
        assert res.status_code == 200, f"Tourist 3 failed to join group: {res.text}"
        print(f"[GROUP] Tourist 3 joined successfully. Members: {res.json()['members']}")

        # Verify group members count
        res = client.get("/api/trips/group/members", headers=headers_list[0])
        assert res.status_code == 200, f"Failed to get members: {res.text}"
        members = res.json()
        print(f"[GROUP] Current group members: {[m['phone_number'] for m in members]}")
        assert len(members) == 3, f"Expected 3 members, got {len(members)}"

        # 5. Tourist 2 triggers SOS
        print("\n=== STEP 5: Tourist 2 hits SOS ===")
        sos_payload = {"lat": 37.7456, "lng": -119.5332}
        res = client.post(f"/api/trips/{trip_ids[1]}/sos", json=sos_payload, headers=headers_list[1])
        assert res.status_code == 200, f"Tourist 2 failed to trigger SOS: {res.text}"
        print("[SOS] Tourist 2 SOS alert triggered successfully.")

        # 6. Confirm Tourists 1 and 3 receive the alert instantly
        print("\n=== STEP 6: Confirm Tourists 1 and 3 receive alert instantly ===")
        
        # Check Tourist 1 alerts
        res = client.get("/api/trips/group/alerts", headers=headers_list[0])
        assert res.status_code == 200, f"Failed to get alerts for Tourist 1: {res.text}"
        t1_alerts = res.json()
        print(f"[ALERT-T1] Tourist 1 received {len(t1_alerts)} alert(s):")
        for alert in t1_alerts:
            print(f"  - From: {alert['alert']['phone_number']} | Type: {alert['alert']['type']} | Location: {alert['alert']['lat']}, {alert['alert']['lng']} | Status: {alert['status']}")
        assert len(t1_alerts) == 1, "Tourist 1 did not receive the group alert!"
        assert t1_alerts[0]["alert"]["phone_number"] == PHONES[1], f"Expected alert from Tourist 2, got {t1_alerts[0]['alert']['phone_number']}"
        
        # Check Tourist 3 alerts
        res = client.get("/api/trips/group/alerts", headers=headers_list[2])
        assert res.status_code == 200, f"Failed to get alerts for Tourist 3: {res.text}"
        t3_alerts = res.json()
        print(f"[ALERT-T3] Tourist 3 received {len(t3_alerts)} alert(s):")
        for alert in t3_alerts:
            print(f"  - From: {alert['alert']['phone_number']} | Type: {alert['alert']['type']} | Location: {alert['alert']['lat']}, {alert['alert']['lng']} | Status: {alert['status']}")
        assert len(t3_alerts) == 1, "Tourist 3 did not receive the group alert!"
        assert t3_alerts[0]["alert"]["phone_number"] == PHONES[1], f"Expected alert from Tourist 2, got {t3_alerts[0]['alert']['phone_number']}"

        # 7. Tourist 1 responds to alert with "going_to_help"
        print("\n=== STEP 7: Tourist 1 responds with 'going_to_help' ===")
        respond_payload = {"action": "going_to_help"}
        t1_group_alert_id = t1_alerts[0]["id"]
        res = client.post(f"/api/trips/group/alerts/{t1_group_alert_id}/respond", json=respond_payload, headers=headers_list[0])
        assert res.status_code == 200, f"Tourist 1 respond failed: {res.text}"
        print("[RESPOND-T1] Recorded response successfully.")

        # Confirm Tourist 1 alerts is now empty (resolved/dismissed/responded status no longer in open list)
        res = client.get("/api/trips/group/alerts", headers=headers_list[0])
        assert len(res.json()) == 0, "Tourist 1 alerts should be empty after response!"
        print("[ALERT-T1] Verified alert is dismissed from open list.")

        # 8. Tourist 3 responds to alert with "call_authorities"
        print("\n=== STEP 8: Tourist 3 responds with 'call_authorities' ===")
        respond_payload = {"action": "call_authorities"}
        t3_group_alert_id = t3_alerts[0]["id"]
        res = client.post(f"/api/trips/group/alerts/{t3_group_alert_id}/respond", json=respond_payload, headers=headers_list[2])
        assert res.status_code == 200, f"Tourist 3 respond failed: {res.text}"
        print("[RESPOND-T3] Recorded response successfully.")

        # Confirm Tourist 3 alerts is now empty
        res = client.get("/api/trips/group/alerts", headers=headers_list[2])
        assert len(res.json()) == 0, "Tourist 3 alerts should be empty after response!"
        print("[ALERT-T3] Verified alert is dismissed from open list.")

        # 9. Tourist 2 cancels SOS
        print("\n=== STEP 9: Tourist 2 cancels SOS ===")
        res = client.post(f"/api/trips/{trip_ids[1]}/sos/cancel", headers=headers_list[1])
        assert res.status_code == 200, f"Tourist 2 failed to cancel SOS: {res.text}"
        print("[SOS] Tourist 2 SOS alert cancelled successfully.")

        # End all trips
        print("\n[CLEANUP] Ending trips...")
        for idx, trip_id in enumerate(trip_ids):
            client.post(f"/api/trips/{trip_id}/end", headers=headers_list[idx])
            print(f"[TRIP] Ended trip {trip_id}")

        print("\n========================================")
        print("ALL TRIP BUDDY GROUP TESTS PASSED SUCCESSFULLY!")
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
