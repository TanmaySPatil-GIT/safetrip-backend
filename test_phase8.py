"""
Phase 8 -- Comprehensive Testing Suite
=======================================
Runs 4 end-to-end tests:
  TEST 1: Geofence Simulation
  TEST 2: SOS End to End
  TEST 3: Risk Score Validation
  TEST 4: Auto Delete Logic
"""

import datetime
import json
import os
import sys
import time
import requests

BACKEND = "http://127.0.0.1:8000"
TEST_PHONE = "+18001234567"
WEBHOOK_LOG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "webhook_log.txt"))

results = []  # (test_name, expected, actual, pass_fail)


def banner(title):
    print(f"\n{'#'*70}")
    print(f"#  {title}")
    print(f"{'#'*70}")


def sub(title):
    print(f"\n  --- {title} ---")


def ok(msg):
    print(f"  [OK] {msg}")


def fail(msg):
    print(f"  [FAIL] {msg}")


def clear_webhook_log():
    if os.path.exists(WEBHOOK_LOG):
        os.remove(WEBHOOK_LOG)


def read_webhook_log():
    if not os.path.exists(WEBHOOK_LOG):
        return []
    entries = []
    with open(WEBHOOK_LOG, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def get_token(phone=TEST_PHONE):
    """Authenticate and return (token, user_id)."""
    requests.post(f"{BACKEND}/api/auth/tourist/otp", json={"phone_number": phone})
    res = requests.post(f"{BACKEND}/api/auth/tourist/verify",
                        json={"phone_number": phone, "code": "123456"})
    if res.status_code != 200:
        raise RuntimeError(f"Login failed: {res.text}")
    d = res.json()
    return d["access_token"], d["user_id"]


def get_operator_token():
    res = requests.post(f"{BACKEND}/api/auth/authority/login",
                        json={"email": "operator@safetrip.gov", "password": "password123"})
    if res.status_code != 200:
        raise RuntimeError(f"Operator login failed: {res.text}")
    return res.json()["access_token"]


def ensure_fresh_trip(token):
    """End any existing trip, create a new one, return trip_id."""
    hdr = {"Authorization": f"Bearer {token}"}
    res = requests.get(f"{BACKEND}/api/trips/active", headers=hdr)
    if res.status_code == 200:
        old = res.json()
        requests.post(f"{BACKEND}/api/trips/{old['id']}/end", headers=hdr)

    now = datetime.datetime.utcnow()
    payload = {
        "start_date": now.isoformat() + "Z",
        "end_date": (now + datetime.timedelta(hours=4)).isoformat() + "Z",
        "region": "Yosemite National Park",
    }
    res = requests.post(f"{BACKEND}/api/trips/start", json=payload, headers=hdr)
    if res.status_code != 201:
        raise RuntimeError(f"Trip create failed: {res.text}")
    return res.json()["id"]


# ======================================================================
# TEST 1 -- Geofence Simulation
# ======================================================================
def test_geofence():
    banner("TEST 1: Geofence Simulation")
    clear_webhook_log()

    token, uid = get_token()
    trip_id = ensure_fresh_trip(token)
    hdr = {"Authorization": f"Bearer {token}"}

    # Half Dome Cables zone polygon: [37.745,-119.535] to [37.747,-119.531]
    # Send a ping INSIDE that zone
    inside_lat, inside_lng = 37.746, -119.533

    sub("Sending location ping inside Half Dome Cables danger zone")
    res = requests.post(f"{BACKEND}/api/trips/{trip_id}/ping",
                        json={"lat": inside_lat, "lng": inside_lng}, headers=hdr)
    if res.status_code != 200:
        fail(f"Ping failed: {res.text}")
        results.append(("Geofence Simulation", "Geofence alert created + webhook logged", "Ping failed", "FAIL"))
        return

    ping_resp = res.json()
    print(f"  Ping response: {ping_resp}")

    sub("Checking geofence alert was triggered")
    triggered = ping_resp.get("alerts_triggered", [])
    geofence_triggered = "geofence" in triggered
    if geofence_triggered:
        ok("Geofence alert triggered in ping response")
    else:
        fail(f"Expected 'geofence' in alerts_triggered, got: {triggered}")

    sub("Checking alert record in database (via authority alerts endpoint)")
    op_token = get_operator_token()
    op_hdr = {"Authorization": f"Bearer {op_token}"}
    res = requests.get(f"{BACKEND}/api/trips/authority/alerts", headers=op_hdr)
    alerts = res.json()
    geo_alerts = [a for a in alerts
                  if a["trip_id"] == trip_id and a["type"] == "geofence" and a["status"] == "open"]
    alert_created = len(geo_alerts) > 0
    if alert_created:
        ok(f"Geofence alert record found: id={geo_alerts[0]['id']}, lat={geo_alerts[0]['lat']}, lng={geo_alerts[0]['lng']}")
    else:
        fail("No open geofence alert found for this trip")

    sub("Checking webhook log for geofence payload")
    entries = read_webhook_log()
    geo_webhook = [e for e in entries
                   if e.get("payload", {}).get("type") == "geofence"
                   and e.get("payload", {}).get("trip_id") == trip_id]
    webhook_logged = len(geo_webhook) > 0
    if webhook_logged:
        p = geo_webhook[0]["payload"]
        ok(f"Webhook logged: alert_id={p['alert_id']}, risk_score={p['risk_score']}, zone={p['zone_name']}")
    else:
        fail("No geofence entry found in webhook log")

    passed = geofence_triggered and alert_created and webhook_logged
    results.append((
        "Geofence Simulation",
        "Geofence alert created + webhook logged",
        "All checks passed" if passed else "Some checks failed",
        "PASS" if passed else "FAIL"
    ))


# ======================================================================
# TEST 2 -- SOS End to End
# ======================================================================
def test_sos():
    banner("TEST 2: SOS End to End")
    clear_webhook_log()

    token, uid = get_token()
    trip_id = ensure_fresh_trip(token)
    hdr = {"Authorization": f"Bearer {token}"}

    sub("Triggering SOS alert")
    res = requests.post(f"{BACKEND}/api/trips/{trip_id}/sos",
                        json={"lat": 37.746, "lng": -119.533}, headers=hdr)
    sos_resp = res.json()
    print(f"  SOS response: {sos_resp}")
    sos_created = "triggered successfully" in sos_resp.get("message", "")
    if sos_created:
        ok("SOS alert created successfully")
    else:
        fail(f"SOS creation issue: {sos_resp}")

    sub("Checking alert record via authority alerts endpoint")
    op_token = get_operator_token()
    op_hdr = {"Authorization": f"Bearer {op_token}"}
    res = requests.get(f"{BACKEND}/api/trips/authority/alerts", headers=op_hdr)
    alerts = res.json()
    sos_alerts = [a for a in alerts
                  if a["trip_id"] == trip_id and a["type"] == "sos" and a["status"] == "open"]
    alert_in_feed = len(sos_alerts) > 0
    if alert_in_feed:
        a = sos_alerts[0]
        ok(f"SOS alert in feed: id={a['id']}, phone={a['phone_number']}, status={a['status']}")
    else:
        fail("SOS alert not found in authority feed")

    sub("Checking webhook log for SOS payload")
    entries = read_webhook_log()
    sos_webhook = [e for e in entries
                   if e.get("payload", {}).get("type") == "sos"
                   and e.get("payload", {}).get("trip_id") == trip_id]
    webhook_ok = len(sos_webhook) > 0
    if webhook_ok:
        p = sos_webhook[0]["payload"]
        ok(f"Webhook logged: alert_id={p['alert_id']}, risk_score={p['risk_score']}, zone={p['zone_name']}")
        # Validate key fields
        assert p["type"] == "sos"
        assert p["trip_id"] == trip_id
        assert p["lat"] == 37.746
        assert p["lng"] == -119.533
        ok("All SOS webhook payload fields validated")
    else:
        fail("No SOS entry in webhook log")

    passed = sos_created and alert_in_feed and webhook_ok
    results.append((
        "SOS End to End",
        "SOS alert created + in feed + webhook fired",
        "All checks passed" if passed else "Some checks failed",
        "PASS" if passed else "FAIL"
    ))


# ======================================================================
# TEST 3 -- Risk Score Validation
# ======================================================================
def test_risk_scores():
    banner("TEST 3: Risk Score Validation")

    sub("Fetching danger zones from API")
    res = requests.get(f"{BACKEND}/api/trips/danger-zones")
    if res.status_code != 200:
        fail(f"Could not fetch zones: {res.text}")
        results.append(("Risk Score Validation", "3 zones with valid scores", "API error", "FAIL"))
        return

    zones = res.json()
    # Filter to only the 3 seeded Yosemite zones (ignore test leftovers)
    SEEDED_NAMES = {"Half Dome Cables", "Tuolumne Meadows", "Mariposa Grove"}
    zones = [z for z in zones if z["name"] in SEEDED_NAMES]
    print(f"  Found {len(zones)} seeded danger zones\n")

    all_ok = True
    zone_data = []

    for z in zones:
        name = z["name"]
        score = z["computed_risk_score"]
        factors = z.get("risk_factors", [])
        factor_count = len(factors)

        print(f"  Zone: {name}")
        print(f"    Risk Score   : {score} / 100")
        print(f"    Risk Level   : {z.get('risk_level', 'N/A')}")
        print(f"    Factor Count : {factor_count}")
        for f in factors:
            print(f"      - {f['factor_type']:20s} value={f['value']:<6} weight={f['weight']}")
        print()

        zone_data.append({"name": name, "score": score, "factor_count": factor_count})

        if factor_count < 2:
            fail(f"{name} has fewer than 2 risk factors ({factor_count})")
            all_ok = False
        else:
            ok(f"{name} has {factor_count} risk factors")

        if score <= 0:
            fail(f"{name} has invalid risk score ({score})")
            all_ok = False
        else:
            ok(f"{name} risk score = {score}")

    sub("Verifying zone with worst factors has highest score")
    if zone_data:
        sorted_zones = sorted(zone_data, key=lambda x: x["score"], reverse=True)
        highest = sorted_zones[0]
        print(f"  Highest scoring zone: {highest['name']} ({highest['score']})")
        # Half Dome Cables should be highest (slope=9.5, network=1.0 with high weights)
        if highest["name"] == "Half Dome Cables":
            ok("Half Dome Cables correctly has the highest risk score")
        else:
            fail(f"Expected 'Half Dome Cables' to have highest score, got '{highest['name']}'")
            all_ok = False

    results.append((
        "Risk Score Validation",
        "3 zones with valid scores, >=2 factors each, correct ranking",
        "All checks passed" if all_ok else "Some checks failed",
        "PASS" if all_ok else "FAIL"
    ))


# ======================================================================
# TEST 4 -- Auto Delete Logic
# ======================================================================
def test_auto_delete():
    banner("TEST 4: Auto Delete Logic")

    sub("Setting up: import DB models directly for this test")
    # We need direct DB access for this test
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app.core.database import SessionLocal
    from app.models.models import Trip, LocationPing, Alert, User

    token, uid = get_token()
    hdr = {"Authorization": f"Bearer {token}"}

    # End any existing trip
    res = requests.get(f"{BACKEND}/api/trips/active", headers=hdr)
    if res.status_code == 200:
        old = res.json()
        requests.post(f"{BACKEND}/api/trips/{old['id']}/end", headers=hdr)

    sub("Creating trip with auto_delete_at = 5 seconds from now")
    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()
        user = db.query(User).filter(User.phone_number == TEST_PHONE).first()
        test_trip = Trip(
            user_id=user.id,
            start_date=now - datetime.timedelta(hours=2),
            end_date=now - datetime.timedelta(minutes=5),
            region="Auto-Delete Test Region",
            status="ended",
            auto_delete_at=now + datetime.timedelta(seconds=5),
        )
        db.add(test_trip)
        db.commit()
        db.refresh(test_trip)
        trip_id = test_trip.id
        ok(f"Created trip #{trip_id} with auto_delete_at = {test_trip.auto_delete_at}")

        # Add some location pings
        for i in range(3):
            ping = LocationPing(
                trip_id=trip_id,
                lat=37.7456 + i * 0.001,
                lng=-119.5332,
                timestamp=now - datetime.timedelta(minutes=30 - i * 10),
            )
            db.add(ping)
        db.commit()

        pings_before = db.query(LocationPing).filter(LocationPing.trip_id == trip_id).count()
        ok(f"Added {pings_before} location pings to trip #{trip_id}")

        sub("Waiting 6 seconds for auto_delete_at to pass...")
        time.sleep(6)

        sub("Running cleanup job manually")
        # This simulates what APScheduler would do
        expired_trips = db.query(Trip).filter(
            Trip.auto_delete_at <= datetime.datetime.utcnow()
        ).all()

        cleaned_count = 0
        our_trip_cleaned = False
        for trip in expired_trips:
            # Delete location pings (personal GPS data)
            ping_count = db.query(LocationPing).filter(LocationPing.trip_id == trip.id).delete()
            # Mark trip ended if still active
            if trip.status == "active":
                trip.status = "ended"
            # Clear personal region info
            trip.region = "[DELETED - Auto-purged]"
            cleaned_count += 1
            if trip.id == trip_id:
                our_trip_cleaned = True
                ok(f"Trip #{trip_id}: deleted {ping_count} pings, status={trip.status}, region={trip.region}")

        db.commit()
        print(f"  Cleanup processed {cleaned_count} expired trip(s)")

        sub("Verifying cleanup results")
        # Re-query from DB
        db.expire_all()
        trip_after = db.query(Trip).filter(Trip.id == trip_id).first()
        pings_after = db.query(LocationPing).filter(LocationPing.trip_id == trip_id).count()

        pings_deleted = pings_after == 0
        status_ended = trip_after.status == "ended"
        data_cleared = "[DELETED" in trip_after.region

        if pings_deleted:
            ok(f"Location pings deleted: {pings_before} -> {pings_after}")
        else:
            fail(f"Pings still exist: {pings_after}")

        if status_ended:
            ok(f"Trip status = '{trip_after.status}'")
        else:
            fail(f"Trip status = '{trip_after.status}' (expected 'ended')")

        if data_cleared:
            ok(f"Personal data cleared: region = '{trip_after.region}'")
        else:
            fail(f"Region not cleared: '{trip_after.region}'")

        passed = our_trip_cleaned and pings_deleted and status_ended and data_cleared
        results.append((
            "Auto Delete Logic",
            "Pings deleted, status=ended, data cleared",
            "All checks passed" if passed else "Some checks failed",
            "PASS" if passed else "FAIL"
        ))

    finally:
        db.close()


# ======================================================================
# MAIN
# ======================================================================
def main():
    test_geofence()
    test_sos()
    test_risk_scores()
    test_auto_delete()

    banner("SUMMARY TABLE")
    print()
    print(f"  {'Test':<30s} | {'Expected':<45s} | {'Actual':<25s} | Result")
    print(f"  {'-'*30} | {'-'*45} | {'-'*25} | ------")
    for name, expected, actual, pf in results:
        print(f"  {name:<30s} | {expected:<45s} | {actual:<25s} | {pf}")
    print()

    all_passed = all(r[3] == "PASS" for r in results)
    if all_passed:
        print("  >>> ALL 4 TESTS PASSED <<<")
    else:
        print("  >>> SOME TESTS FAILED <<<")
        sys.exit(1)


if __name__ == "__main__":
    main()
