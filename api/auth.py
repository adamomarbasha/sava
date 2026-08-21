import logging
from datetime import datetime, timedelta
from typing import Optional
import os
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
from .db import engine, SessionLocal

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

# A development-only fallback secret. Every token Sava has ever issued locally
# is signed with it, and it is in the repository, so anyone can mint a valid
# token for any account. That is fine on a laptop and catastrophic in public.
_DEV_SECRET = "your-secret-key-change-in-production"


def _load_secret_key() -> str:
    """The JWT signing key. Refuses to start in production without a real one.

    Defaulting silently is the failure mode this exists to prevent: the server
    boots, logins succeed, everything looks healthy, and the entire auth system
    is forgeable by anyone who has read the source. Outside development the
    absence of SECRET_KEY is a startup error, not a warning.
    """
    configured = os.getenv("SECRET_KEY")
    environment = os.getenv("ENVIRONMENT", "development").lower()
    is_production = environment not in ("development", "dev", "test", "testing")

    if configured and configured != _DEV_SECRET:
        if len(configured) < 32 and is_production:
            raise RuntimeError(
                "SECRET_KEY is too short for production. Generate one with: "
                "python -c 'import secrets; print(secrets.token_urlsafe(64))'")
        return configured

    if is_production:
        raise RuntimeError(
            "SECRET_KEY is not set (or is still the development default) while "
            f"ENVIRONMENT={environment!r}. Refusing to start: every token would "
            "be forgeable. Generate one with: "
            "python -c 'import secrets; print(secrets.token_urlsafe(64))'")

    logger.warning(
        "SECRET_KEY is unset — using the insecure development default. "
        "This is refused when ENVIRONMENT is anything but development.")
    return _DEV_SECRET


SECRET_KEY = _load_secret_key()

# How long a session lasts before the user has to sign in again.
#
# Was 30 minutes with no refresh, which is a reasonable default for a banking
# API and wrong for a media library: it signed people out several times a day,
# and on a remote backend that reads as "the app keeps logging me out". Thirty
# days matches what a consumer app of this kind is expected to do. Revocation
# is the gap this leaves — see the deployment notes.
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("SAVA_TOKEN_TTL_MINUTES", str(60 * 24 * 30)))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

security = HTTPBearer()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_user_by_email(email: str):
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id, email, password_hash, created_at FROM users WHERE LOWER(email) = LOWER(:email)"),
            {"email": email.strip()}
        ).mappings().first()
        return dict(result) if result else None
    finally:
        db.close()

def authenticate_user(email: str, password: str):
    user = get_user_by_email(email)
    if not user:
        return False
    if not verify_password(password, user["password_hash"]):
        return False
    return user

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = get_user_by_email(email)
    if user is None:
        raise credentials_exception
    return user
