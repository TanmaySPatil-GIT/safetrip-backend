import sys
from app.core.database import SessionLocal
from app.models.models import DangerZone, RiskFactor
from app.core.safety import calculate_zone_risk

def cleanup_test_data(db):
    print("[CLEANUP] Cleaning up test danger zones and factors...")
    test_zones = db.query(DangerZone).filter(DangerZone.name.like("% Test")).all()
    test_zone_ids = [z.id for z in test_zones]
    if test_zone_ids:
        # Delete associated risk factors
        db.query(RiskFactor).filter(RiskFactor.zone_id.in_(test_zone_ids)).delete(synchronize_session=False)
        # Delete danger zones
        db.query(DangerZone).filter(DangerZone.id.in_(test_zone_ids)).delete(synchronize_session=False)
        db.commit()
    print("[CLEANUP] Cleanup complete.")

def main():
    db = SessionLocal()
    try:
        # Pre-cleanup
        cleanup_test_data(db)

        # Definition of our 3 danger zones and their risk factors
        zones_definition = [
            {
                "name": "Half Dome Cables Test",
                "polygon": [[37.746, -119.533], [37.747, -119.533], [37.747, -119.532], [37.746, -119.532]],
                "factors": [
                    {"factor_type": "slope", "value": 9.5, "weight": 1.5},
                    {"factor_type": "forest_density", "value": 4.0, "weight": 0.8},
                    {"factor_type": "rainfall", "value": 8.0, "weight": 1.2},
                    {"factor_type": "network_coverage", "value": 1.0, "weight": 1.5}
                ]
            },
            {
                "name": "Tuolumne Meadows Test",
                "polygon": [[37.873, -119.356], [37.874, -119.356], [37.874, -119.355], [37.873, -119.355]],
                "factors": [
                    {"factor_type": "slope", "value": 1.0, "weight": 1.5},
                    {"factor_type": "forest_density", "value": 2.0, "weight": 0.8},
                    {"factor_type": "rainfall", "value": 2.0, "weight": 1.2},
                    {"factor_type": "network_coverage", "value": 8.5, "weight": 1.5}
                ]
            },
            {
                "name": "Mariposa Grove Test",
                "polygon": [[37.513, -119.601], [37.514, -119.601], [37.514, -119.600], [37.513, -119.600]],
                "factors": [
                    {"factor_type": "slope", "value": 4.0, "weight": 1.5},
                    {"factor_type": "forest_density", "value": 8.5, "weight": 0.8},
                    {"factor_type": "rainfall", "value": 5.0, "weight": 1.2},
                    {"factor_type": "network_coverage", "value": 5.0, "weight": 1.5}
                ]
            }
        ]

        seeded_zones = []

        # Seed the data
        print("\n[SEED] Seeding test danger zones and factors...")
        for zone_def in zones_definition:
            zone = DangerZone(
                name=zone_def["name"],
                polygon_coordinates=zone_def["polygon"],
                risk_level="medium" if "Mariposa" in zone_def["name"] else ("high" if "Half" in zone_def["name"] else "low")
            )
            db.add(zone)
            db.commit()
            db.refresh(zone)
            
            # Seed risk factors for this zone
            for factor_def in zone_def["factors"]:
                factor = RiskFactor(
                    zone_id=zone.id,
                    factor_type=factor_def["factor_type"],
                    value=factor_def["value"],
                    weight=factor_def["weight"]
                )
                db.add(factor)
            db.commit()
            seeded_zones.append(zone)
            print(f"[SEED] Created '{zone.name}' with ID: {zone.id}")

        # Compute risk scores
        print("\n[SCORING] Running risk scoring engine...")
        results = {}
        for zone in seeded_zones:
            score = calculate_zone_risk(zone.id, db)
            results[zone.name] = {
                "id": zone.id,
                "score": score,
                "risk_level": zone.risk_level
            }

        # Print explainable output
        print("\n=== RISK SCORE EXPLANATION ===")
        for zone in seeded_zones:
            db.refresh(zone)
            print(f"\nDanger Zone: {zone.name} (ID: {zone.id})")
            print(f"Computed Risk Score: {zone.computed_risk_score} / 100")
            print(f"Risk Level: {zone.risk_level}")
            print("Factors:")
            for factor in zone.risk_factors:
                risk_contrib = 10.0 - factor.value if factor.factor_type == "network_coverage" else factor.value
                print(f"  - {factor.factor_type}:")
                print(f"      Raw Value: {factor.value}")
                print(f"      Risk Contribution (0-10): {risk_contrib}")
                print(f"      Weight: {factor.weight}")

        # Verifications and Assertions
        half_dome_score = results["Half Dome Cables Test"]["score"]
        tuolumne_score = results["Tuolumne Meadows Test"]["score"]
        mariposa_score = results["Mariposa Grove Test"]["score"]

        print("\n=== VERIFYING ASSERTIONS ===")
        print(f"Half Dome Cables Score: {half_dome_score} (Expected: ~81.1)")
        print(f"Mariposa Grove Score: {mariposa_score} (Expected: ~52.6)")
        print(f"Tuolumne Meadows Score: {tuolumne_score} (Expected: ~15.5)")

        # Confirm score bounds
        assert abs(half_dome_score - 81.1) < 0.1, f"Half Dome score unexpected: {half_dome_score}"
        assert abs(mariposa_score - 52.6) < 0.1, f"Mariposa score unexpected: {mariposa_score}"
        assert abs(tuolumne_score - 15.5) < 0.1, f"Tuolumne score unexpected: {tuolumne_score}"
        
        # Confirm sorting
        assert half_dome_score > mariposa_score > tuolumne_score, "Scoring order logic failed!"
        print("Success: Correct scoring order verified (Worst factors get the highest score).")

        print("\n========================================")
        print("ALL SCORING TESTS PASSED SUCCESSFULLY!")
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
