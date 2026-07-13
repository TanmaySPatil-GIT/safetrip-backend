import datetime
import random
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, EmailStr
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.dependencies import get_current_tourist, get_current_authority
from app.models.models import User, AuthorityUser, OTPToken
from app.core.config import settings

logger = logging.getLogger("safetrip.auth")

router = APIRouter(prefix="/auth", tags=["authentication"])

# Pydantic Schemas
class TouristOTPRequest(BaseModel):
    phone_number: str = Field(..., description="Tourist phone number with country code")

class TouristOTPVerify(BaseModel):
    phone_number: str = Field(..., description="Tourist phone number")
    code: str = Field(..., description="6-digit verification code")

class AuthorityRegister(BaseModel):
    name: str = Field(..., min_length=2)
    email: EmailStr
    password: str = Field(..., min_length=6)
    role: str = Field("operator", description="Role: admin or operator")

class AuthorityLogin(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user_type: str
    user_id: int
    name: Optional[str] = None

class UserInfoResponse(BaseModel):
    id: int
    phone_number: str
    preferred_language: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True

class AuthorityInfoResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    created_at: datetime.datetime

    class Config:
        from_attributes = True

# Helper to send OTP (mocked)
def send_otp_via_sms(phone_number: str, code: str):
    # This is where real Twilio integration goes.
    # For MVP, we log it clearly in the terminal.
    log_msg = f"\n========================================\n[SMS GATEWAY] Sent OTP {code} to {phone_number}\n========================================"
    print(log_msg)
    logger.info(log_msg)

# Endpoints
@router.post("/tourist/otp", status_code=status.HTTP_200_OK)
def request_otp(payload: TouristOTPRequest, db: Session = Depends(get_db)):
    phone = payload.phone_number.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid phone number")
    
    # Generate 6-digit code
    code = f"{random.randint(100000, 999999)}"
    
    # Set expiration: 5 minutes from now
    expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)
    
    # Save code to database
    otp_entry = OTPToken(phone_number=phone, code=code, expires_at=expires_at)
    db.add(otp_entry)
    db.commit()
    
    # Trigger SMS sending
    send_otp_via_sms(phone, code)
    
    # Return friendly message including dev note
    return {
        "status": "success",
        "message": f"OTP sent successfully. (Dev note: check server logs or use {code} for testing)"
    }

@router.post("/tourist/verify", response_model=TokenResponse)
def verify_otp(payload: TouristOTPVerify, db: Session = Depends(get_db)):
    phone = payload.phone_number.strip()
    code = payload.code.strip()
    
    # Find matching OTP that is not expired
    now = datetime.datetime.utcnow()
    otp_record = db.query(OTPToken).filter(
        OTPToken.phone_number == phone,
        OTPToken.code == code,
        OTPToken.expires_at > now
    ).order_by(OTPToken.created_at.desc()).first()
    
    # Fallback to absolute backdoor for simple manual testing: '123456'
    if settings.DEMO_MODE and not otp_record and code == "123456":
        # Allow backdoor code '123456' for local testing
        otp_record = True
        logger.warning(f"Backdoor code used for {phone}")
        
    if not otp_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OTP code"
        )
    
    # Find or create tourist user
    user = db.query(User).filter(User.phone_number == phone).first()
    if not user:
        user = User(phone_number=phone)
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"Registered new tourist user: {phone}")
        
    # Delete OTP records to clean up (if not the backdoor)
    if otp_record is not True:
        db.query(OTPToken).filter(OTPToken.phone_number == phone).delete()
        db.commit()
        
    # Generate access token
    access_token = create_access_token(subject=user.id, user_type="tourist")
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_type": "tourist",
        "user_id": user.id,
        "name": phone
    }

@router.post("/authority/register", response_model=TokenResponse)
def register_authority(payload: AuthorityRegister, db: Session = Depends(get_db)):
    # Check if email exists
    existing = db.query(AuthorityUser).filter(AuthorityUser.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
        
    hashed_password = get_password_hash(payload.password)
    new_auth_user = AuthorityUser(
        name=payload.name,
        email=payload.email,
        password_hash=hashed_password,
        role=payload.role
    )
    db.add(new_auth_user)
    db.commit()
    db.refresh(new_auth_user)
    
    access_token = create_access_token(subject=new_auth_user.id, user_type="authority")
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_type": "authority",
        "user_id": new_auth_user.id,
        "name": new_auth_user.name
    }

@router.post("/authority/login", response_model=TokenResponse)
def login_authority(payload: AuthorityLogin, db: Session = Depends(get_db)):
    user = db.query(AuthorityUser).filter(AuthorityUser.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    access_token = create_access_token(subject=user.id, user_type="authority")
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_type": "authority",
        "user_id": user.id,
        "name": user.name
    }

@router.get("/me/tourist", response_model=UserInfoResponse)
def get_me_tourist(current_user: User = Depends(get_current_tourist)):
    return current_user

@router.get("/me/authority", response_model=AuthorityInfoResponse)
def get_me_authority(current_user: AuthorityUser = Depends(get_current_authority)):
    return current_user

class UpdateLanguageRequest(BaseModel):
    preferred_language: str

@router.put("/tourist/language", status_code=status.HTTP_200_OK)
def update_tourist_language(
    payload: UpdateLanguageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_tourist)
):
    lang = payload.preferred_language.strip().lower()
    if lang not in ["en", "hi", "mr"]:
        raise HTTPException(status_code=400, detail="Language must be 'en', 'hi', or 'mr'")
    current_user.preferred_language = lang
    db.commit()
    return {"status": "success", "preferred_language": lang}
