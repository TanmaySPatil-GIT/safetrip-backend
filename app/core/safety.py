import math
import datetime
from app.models.models import DangerZone, RiskFactor

def calculate_distance(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees) in meters.
    """
    lon1, lat1, lon2, lat2 = map(math.radians, [lng1, lat1, lng2, lat2])

    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a)) 
    r = 6371000 # Radius of earth in meters
    return c * r

def is_point_in_polygon(lat: float, lng: float, polygon: list[list[float]]) -> bool:
    """
    Ray-casting algorithm to determine if a point (lat, lng) is inside a polygon.
    polygon is a list of [lat, lng] coordinate pairs.
    """
    num_vertices = len(polygon)
    if num_vertices < 3:
        return False
    
    inside = False
    p1_lat, p1_lng = polygon[0]
    for i in range(1, num_vertices + 1):
        p2_lat, p2_lng = polygon[i % num_vertices]
        if min(p1_lng, p2_lng) < lng <= max(p1_lng, p2_lng):
            if lat <= max(p1_lat, p2_lat):
                if p1_lng != p2_lng:
                    xinters = (lng - p1_lng) * (p2_lat - p1_lat) / (p2_lng - p1_lng) + p1_lat
                    if p1_lat == p2_lat or lat <= xinters:
                        inside = not inside
        p1_lat, p1_lng = p2_lat, p2_lng
        
    return inside

def evaluate_distress(pings: list, db_session) -> list[str]:
    """
    Evaluates historical pings (sorted by timestamp ascending) for a trip.
    Returns a list of reasons/flags: 'stationary', 'speed_drop', 'signal_loss'
    """
    flags = []
    if len(pings) < 2:
        return flags

    latest_ping = pings[-1]
    
    # 1. Stationary check:
    # Find the earliest ping from which the user has remained within 10 meters continuously up to latest_ping.
    anchor_lat, anchor_lng = latest_ping.lat, latest_ping.lng
    earliest_stationary_ping = latest_ping
    
    for p in reversed(pings[:-1]):
        dist = calculate_distance(anchor_lat, anchor_lng, p.lat, p.lng)
        if dist < 10.0:
            earliest_stationary_ping = p
        else:
            break
            
    time_span = (latest_ping.timestamp - earliest_stationary_ping.timestamp).total_seconds()
    if time_span >= 900:
        flags.append("stationary")

    # 2. Speed drop check:
    # If speed goes from high (>= 5.0 m/s) to low (< 0.5 m/s)
    if len(pings) >= 3:
        p3, p2, p1 = pings[-3], pings[-2], pings[-1]
        dt1 = (p2.timestamp - p3.timestamp).total_seconds()
        dt2 = (p1.timestamp - p2.timestamp).total_seconds()
        if dt1 > 0 and dt2 > 0:
            dist1 = calculate_distance(p3.lat, p3.lng, p2.lat, p2.lng)
            dist2 = calculate_distance(p2.lat, p2.lng, p1.lat, p1.lng)
            speed1 = dist1 / dt1
            speed2 = dist2 / dt2
            if speed1 >= 5.0 and speed2 < 0.5:
                flags.append("speed_drop")

    # 3. Signal loss check:
    # If the time gap between the last two pings is >= 10 mins (600 seconds)
    # and the previous ping (p2) was inside a danger zone.
    if len(pings) >= 2:
        p2, p1 = pings[-2], pings[-1]
        gap = (p1.timestamp - p2.timestamp).total_seconds()
        if gap >= 600:
            zones = db_session.query(DangerZone).all()
            in_zone = False
            for z in zones:
                if is_point_in_polygon(p2.lat, p2.lng, z.polygon_coordinates):
                    in_zone = True
                    break
            if in_zone:
                flags.append("signal_loss")

    return flags

def calculate_zone_risk(zone_id: int, db_session) -> float:
    """
    Computes a weighted risk score (0-100) for a danger zone based on its risk factors.
    Factors considered:
    - slope (0-10 scale)
    - forest_density (0-10 scale)
    - rainfall (0-10 scale)
    - network_coverage (0-10 scale, lower coverage = higher risk)
    Updates the danger zone's computed_risk_score in the database.
    """
    zone = db_session.query(DangerZone).filter(DangerZone.id == zone_id).first()
    if not zone:
        raise ValueError(f"DangerZone with ID {zone_id} not found")

    factors = db_session.query(RiskFactor).filter(RiskFactor.zone_id == zone_id).all()
    if not factors:
        zone.computed_risk_score = 0.0
        zone.last_scored_at = datetime.datetime.utcnow()
        db_session.commit()
        return 0.0

    total_weighted_risk = 0.0
    total_weight = 0.0

    for factor in factors:
        if factor.factor_type == "network_coverage":
            # lower coverage = higher risk
            risk_contribution = max(0.0, 10.0 - factor.value)
        elif factor.factor_type in ("slope", "forest_density", "rainfall"):
            risk_contribution = factor.value
        else:
            risk_contribution = factor.value

        total_weighted_risk += risk_contribution * factor.weight
        total_weight += factor.weight

    if total_weight > 0:
        computed_score = (total_weighted_risk / total_weight) * 10.0
    else:
        computed_score = 0.0

    # TODO: Wire feedback_unsafe_count into risk scoring Phase 2
    zone.computed_risk_score = round(computed_score, 2)
    zone.last_scored_at = datetime.datetime.utcnow()
    db_session.commit()
    db_session.refresh(zone)
    return zone.computed_risk_score

