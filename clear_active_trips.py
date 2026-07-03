import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.database import SessionLocal
from app.models.models import Trip

def clear_trips():
    db = SessionLocal()
    try:
        print("Connecting to database...")
        active_trips = db.query(Trip).filter(Trip.status == 'active').all()
        print(f"Found {len(active_trips)} active trips.")
        for trip in active_trips:
            print(f"Ending trip ID {trip.id} for user {trip.user_id}...")
            trip.status = 'ended'
        db.commit()
        print("All active trips have been ended successfully.")
    except Exception as e:
        print("Error clearing trips:", e)
    finally:
        db.close()

if __name__ == '__main__':
    clear_trips()
