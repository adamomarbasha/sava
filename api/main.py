from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl
from sqlalchemy import text
from datetime import timedelta
from .db import engine, init_db
from .auth import (
    authenticate_user, 
    create_access_token, 
    get_current_user, 
    get_password_hash,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    get_user_by_email,
    verify_password,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3002", "http://127.0.0.1:3002"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BookmarkIn(BaseModel):
    url: HttpUrl
    title: str | None = None
    note: str | None = None

class UserRegister(BaseModel):
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

@app.on_event("startup")
def on_startup():
    init_db()

def detect_platform(url: str) -> str:
    u = url.lower()
    if "tiktok.com" in u: return "tiktok"
    if "youtube.com" in u or "youtu.be" in u: return "youtube"
    if "instagram.com" in u: return "instagram"
    if "twitter.com" in u or "x.com" in u: return "twitter"
    return "web"

@app.get("/")
def health():
    return {"message": "Sava API is running 🚀"}

@app.post("/auth/register", response_model=dict)
def register(user: UserRegister):
    normalized_email = user.email.strip().lower()
    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM users WHERE LOWER(email) = :email"),
            {"email": normalized_email}
        ).mappings().first()
        
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered"
            )
        
        hashed_password = get_password_hash(user.password)
        result = conn.execute(
            text("""
                INSERT INTO users (email, password_hash)
                VALUES (:email, :password_hash)
            """),
            {"email": normalized_email, "password_hash": hashed_password}
        )
        
        user_id = result.lastrowid
        return {"id": user_id, "email": normalized_email, "message": "User created successfully"}

@app.post("/auth/login", response_model=Token)
def login(user: UserLogin):
    existing_user = get_user_by_email(user.email)
    if not existing_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email not found"
        )
    if not verify_password(user.password, existing_user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": existing_user["email"]}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/auth/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "email": current_user["email"],
        "created_at": current_user["created_at"]
    }

@app.get("/users")
def list_users():
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT id, email, created_at FROM users ORDER BY created_at DESC")
        ).mappings().all()
        return [dict(r) for r in rows]

@app.post("/bookmarks")
def create_bookmark(b: BookmarkIn, current_user: dict = Depends(get_current_user)):
    platform = detect_platform(str(b.url))
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO bookmarks (url, title, note, platform, user_id)
                    VALUES (:url, :title, :note, :platform, :user_id)
                """),
                {
                    "url": str(b.url), 
                    "title": b.title, 
                    "note": b.note, 
                    "platform": platform,
                    "user_id": current_user["id"]
                }
            )
            bookmark_id = result.lastrowid
            row = conn.execute(
                text("SELECT id, url, title, note, platform, created_at FROM bookmarks WHERE id = :id"),
                {"id": bookmark_id}
            ).mappings().one()
            return dict(row)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/bookmarks")
def list_bookmarks(
    query: str | None = None, 
    platform: str | None = None,
    current_user: dict = Depends(get_current_user)
):
    sql = "SELECT id, url, title, note, platform, created_at FROM bookmarks WHERE user_id = :user_id"
    params = {"user_id": current_user["id"]}
    
    if query:
        sql += " AND (url LIKE :q OR COALESCE(title,'') LIKE :q OR COALESCE(note,'') LIKE :q)"
        params["q"] = f"%{query}%"
    if platform:
        sql += " AND platform = :p"
        params["p"] = platform.lower()
    
    sql += " ORDER BY created_at DESC LIMIT 200"
    
    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]
