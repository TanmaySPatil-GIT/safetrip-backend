import sys
from app.core.database import SessionLocal
from app.models.models import DangerZone, RiskFactor, AuthorityUser
from app.core.safety import calculate_zone_risk
from app.core.security import get_password_hash

def seed():
    db = SessionLocal()
    try:
        # Define Yosemite danger zones
        zones_definition = [
            {
                "name": "Half Dome Cables",
                "polygon": [[37.745, -119.535], [37.747, -119.535], [37.747, -119.531], [37.745, -119.531]],
                "risk_level": "high",
                "factors": [
                    {"factor_type": "slope", "value": 9.5, "weight": 1.5},
                    {"factor_type": "forest_density", "value": 4.0, "weight": 0.8},
                    {"factor_type": "rainfall", "value": 8.0, "weight": 1.2},
                    {"factor_type": "network_coverage", "value": 1.0, "weight": 1.5}
                ]
            },
            {
                "name": "Tuolumne Meadows",
                "polygon": [[37.873, -119.356], [37.874, -119.356], [37.874, -119.355], [37.873, -119.355]],
                "risk_level": "low",
                "factors": [
                    {"factor_type": "slope", "value": 1.0, "weight": 1.5},
                    {"factor_type": "forest_density", "value": 2.0, "weight": 0.8},
                    {"factor_type": "rainfall", "value": 2.0, "weight": 1.2},
                    {"factor_type": "network_coverage", "value": 8.5, "weight": 1.5}
                ]
            },
            {
                "name": "Mariposa Grove",
                "polygon": [[37.513, -119.601], [37.514, -119.601], [37.514, -119.600], [37.513, -119.600]],
                "risk_level": "medium",
                "factors": [
                    {"factor_type": "slope", "value": 4.0, "weight": 1.5},
                    {"factor_type": "forest_density", "value": 8.5, "weight": 0.8},
                    {"factor_type": "rainfall", "value": 5.0, "weight": 1.2},
                    {"factor_type": "network_coverage", "value": 5.0, "weight": 1.5}
                ]
            }
        ]

        print("[SEED] Seeding Yosemite danger zones...")
        for zone_def in zones_definition:
            # Check if exists
            existing = db.query(DangerZone).filter(DangerZone.name == zone_def["name"]).first()
            if existing:
                print(f"[SEED] Zone '{zone_def['name']}' already exists, updating...")
                zone = existing
                zone.polygon_coordinates = zone_def["polygon"]
                zone.risk_level = zone_def["risk_level"]
            else:
                zone = DangerZone(
                    name=zone_def["name"],
                    polygon_coordinates=zone_def["polygon"],
                    risk_level=zone_def["risk_level"]
                )
                db.add(zone)
                db.commit()
                db.refresh(zone)
                print(f"[SEED] Created zone '{zone.name}' with ID: {zone.id}")

            # Update risk factors
            db.query(RiskFactor).filter(RiskFactor.zone_id == zone.id).delete()
            for factor_def in zone_def["factors"]:
                factor = RiskFactor(
                    zone_id=zone.id,
                    factor_type=factor_def["factor_type"],
                    value=factor_def["value"],
                    weight=factor_def["weight"]
                )
                db.add(factor)
            db.commit()

            # Calculate score
            calculate_zone_risk(zone.id, db)
            print(f"[SEED] Computed risk score for {zone.name}: {zone.computed_risk_score} / 100")

        # Seed a default Operator account
        operator_email = "operator@safetrip.gov"
        existing_op = db.query(AuthorityUser).filter(AuthorityUser.email == operator_email).first()
        if not existing_op:
            op_pwd = get_password_hash("password123")
            op_user = AuthorityUser(
                name="Yosemite Dispatcher",
                email=operator_email,
                password_hash=op_pwd,
                role="operator"
            )
            db.add(op_user)
            db.commit()
            print(f"[SEED] Default Operator created: {operator_email} / password123")
        else:
            print(f"[SEED] Operator '{operator_email}' already exists.")

        print("[SEED] Seeding completed successfully!")

    except Exception as e:
        print(f"[SEED] Seeding failed: {e}", file=sys.stderr)
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed()
