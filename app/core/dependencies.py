from fastapi import Depends, HTTPException, status, Header
from typing import Optional
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.security import decode_token
from app.models.models import User, AuthorityUser

security_scheme = HTTPBearer()

def get_current_user_data(credentials: HTTPAuthorizationCredentials = Depends(security_scheme)) -> dict:
    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload

def get_current_tourist(
    payload: dict = Depends(get_current_user_data),
    db: Session = Depends(get_db)
) -> User:
    if payload.get("user_type") != "tourist":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access tourist endpoints"
        )
    
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tourist user not found"
        )
    return user

def get_current_authority(
    payload: dict = Depends(get_current_user_data),
    db: Session = Depends(get_db)
) -> AuthorityUser:
    if payload.get("user_type") != "authority":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access authority endpoints"
        )
    
    auth_user_id = payload.get("sub")
    user = db.query(AuthorityUser).filter(AuthorityUser.id == int(auth_user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authority user not found"
        )
    return user

def get_current_authority_for_export(
    token: Optional[str] = None,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
) -> AuthorityUser:
    actual_token = None
    if authorization and authorization.startswith("Bearer "):
        actual_token = authorization.split(" ")[1]
    elif token:
        actual_token = token
        
    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided"
        )
        
    payload = decode_token(actual_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    if payload.get("user_type") != "authority":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access authority endpoints"
        )
        
    auth_user_id = payload.get("sub")
    user = db.query(AuthorityUser).filter(AuthorityUser.id == int(auth_user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authority user not found"
        )
    return user
