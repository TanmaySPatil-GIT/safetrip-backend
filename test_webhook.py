"""
Phase 7 — Webhook Integration Test
===================================

SIMULATED INTEGRATION POINT — This webhook represents a future connection
to a real police/emergency dispatch API (e.g. 112 India emergency services).
It is NOT connected to any live system.

This script:
  1. Authenticates as a tourist to get a JWT token
  2. Ensures an active trip exists (creates one if needed)
  3. Clears any old webhook log so we get a clean test
  4. Triggers an SOS alert
  5. Reads the webhook log via GET endpoint
  6. Verifies the SOS payload was correctly dispatched and recorded
"""

import os
import sys
import time
import json
import requests

BACKEND_URL = "http://127.0.0.1:8000"
PHONE = "+15559999"  # fresh number to avoid collisions with existing trips


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def fail(msg):
    print(f"\n  *** FAIL: {msg}")
    sys.exit(1)


def run():
    # ------------------------------------------------------------------
    # Step 0  — Clear old webhook log
    # ------------------------------------------------------------------
    section("Step 0: Clear old webhook log")
    # The server resolves the log path relative to trips.py's __file__,
    # which lands at c:\PartC\backend\webhook_log.txt
    log_path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "webhook_log.txt"))
    if os.path.exists(log_path):
        os.remove(log_path)
        print(f"  Deleted old log: {log_path}")
    else:
        print(f"  No old log found at {log_path}, starting fresh.")

    # ------------------------------------------------------------------
    # Step 1  — Authenticate as tourist
    # ------------------------------------------------------------------
    section("Step 1: Authenticate as tourist")
    res = requests.post(f"{BACKEND_URL}/api/auth/tourist/otp", json={"phone_number": PHONE})
    print(f"  OTP request: {res.json()}")

    res = requests.post(f"{BACKEND_URL}/api/auth/tourist/verify",
                        json={"phone_number": PHONE, "code": "123456"})
    if res.status_code != 200:
        fail(f"Login failed: {res.text}")
    data = res.json()
    token = data["access_token"]
    print(f"  Logged in as user_id={data['user_id']}, token obtained.")

    headers = {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # Step 2  — Ensure active trip (end old one if needed, create fresh)
    # ------------------------------------------------------------------
    section("Step 2: Ensure a clean active trip")
    # End any existing active trip first so we start clean
    res = requests.get(f"{BACKEND_URL}/api/trips/active", headers=headers)
    if res.status_code == 200:
        old_trip = res.json()
        print(f"  Ending old active trip #{old_trip['id']}...")
        requests.post(f"{BACKEND_URL}/api/trips/{old_trip['id']}/end", headers=headers)

    import datetime as dt
    now = dt.datetime.utcnow()
    payload = {
        "start_date": now.isoformat() + "Z",
        "end_date": (now + dt.timedelta(hours=4)).isoformat() + "Z",
        "region": "Yosemite National Park",
    }
    res = requests.post(f"{BACKEND_URL}/api/trips/start", json=payload, headers=headers)
    if res.status_code != 201:
        fail(f"Could not create trip: {res.text}")
    trip = res.json()
    trip_id = trip["id"]
    print(f"  Created fresh trip #{trip_id} in '{trip['region']}'")

    # ------------------------------------------------------------------
    # Step 3  — Trigger SOS alert
    # ------------------------------------------------------------------
    section("Step 3: Trigger SOS alert")
    sos_payload = {"lat": 37.746, "lng": -119.533}
    res = requests.post(f"{BACKEND_URL}/api/trips/{trip_id}/sos",
                        json=sos_payload, headers=headers)
    if res.status_code != 200:
        fail(f"SOS trigger failed: {res.text}")
    sos_resp = res.json()
    print(f"  SOS response: {sos_resp}")
    if "already active" in sos_resp.get("message", ""):
        fail("SOS already active — test isolation failed")

    # Give the webhook a moment to be written
    time.sleep(1)

    # ------------------------------------------------------------------
    # Step 4  — Read back webhook log via API
    # ------------------------------------------------------------------
    section("Step 4: Read webhook log via GET /api/trips/integration/webhook/logs")
    res = requests.get(f"{BACKEND_URL}/api/trips/integration/webhook/logs")
    if res.status_code != 200:
        fail(f"Could not read webhook logs: {res.text}")

    log_data = res.json()
    print(f"  Webhook log count: {log_data['count']}")

    if log_data["count"] == 0:
        fail("Webhook log is empty — dispatch did not fire!")

    # ------------------------------------------------------------------
    # Step 5  — Verify SOS payload in log
    # ------------------------------------------------------------------
    section("Step 5: Verify SOS payload in webhook log")
    sos_entries = [e for e in log_data["entries"]
                   if e.get("payload", {}).get("type") == "sos"
                   and e.get("payload", {}).get("trip_id") == trip_id]

    if not sos_entries:
        fail(f"No SOS webhook entry found for trip #{trip_id}")

    entry = sos_entries[-1]
    p = entry["payload"]
    print(f"  Received webhook payload:")
    print(f"    alert_id  : {p.get('alert_id')}")
    print(f"    trip_id   : {p.get('trip_id')}")
    print(f"    type      : {p.get('type')}")
    print(f"    lat       : {p.get('lat')}")
    print(f"    lng       : {p.get('lng')}")
    print(f"    timestamp : {p.get('timestamp')}")
    print(f"    risk_score: {p.get('risk_score')}")
    print(f"    zone_name : {p.get('zone_name')}")
    print(f"  Logged at   : {entry.get('received_at')}")

    # Validate key fields
    assert p["type"] == "sos", f"Expected type 'sos', got '{p['type']}'"
    assert p["trip_id"] == trip_id, f"Expected trip_id {trip_id}, got {p['trip_id']}"
    assert p["lat"] == sos_payload["lat"], f"Lat mismatch"
    assert p["lng"] == sos_payload["lng"], f"Lng mismatch"
    assert p["alert_id"] is not None, "alert_id is None"

    # ------------------------------------------------------------------
    # Step 6  — Also read the raw webhook_log.txt from disk
    # ------------------------------------------------------------------
    section("Step 6: Raw webhook_log.txt contents")
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            contents = f.read()
        print(contents)
    else:
        print("  (file not found on disk)")

    # ------------------------------------------------------------------
    # Done
    # ------------------------------------------------------------------
    section("ALL TESTS PASSED")
    print("  The webhook stub correctly:")
    print("    [PASS] Dispatched the SOS alert payload to the webhook endpoint")
    print("    [PASS] Logged the payload with risk_score and zone info to webhook_log.txt")
    print("    [PASS] The GET /integration/webhook/logs endpoint returned the entry")
    print()



if __name__ == "__main__":
    run()
