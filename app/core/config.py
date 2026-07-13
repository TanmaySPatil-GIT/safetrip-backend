import os
from pydantic_settings import BaseSettings  # pyrefly: ignore [missing-import]

class Settings(BaseSettings):
    # DEV-ONLY: Defaults to SQLite locally. Switch to PostgreSQL database in .env for production/deployment.
    DATABASE_URL: str = "sqlite:///./safetrip.db"
    JWT_SECRET: str = "supersecretjwtkeyforlocaldevelopmentonly123!"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440
    DEMO_MODE: bool = False
    
    # Demo configuration
    DEMO_REGION_NAME: str = "Yosemite National Park"
    DEMO_CENTER_LAT: float = 37.7456
    DEMO_CENTER_LNG: float = -119.5332

    # Third Party API keys
    GOOGLE_MAPS_API_KEY: str = ""
    OPENWEATHERMAP_API_KEY: str = ""
    PEXELS_API_KEY: str = ""
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""
    EMERGENCY_CONTACT_NUMBER: str = "+91XXXXXXXXXX"
    CHECKIN_SCHEDULER_INTERVAL_MINUTES: float = 5.0

    class Config:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
