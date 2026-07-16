import uuid
from fastapi import APIRouter, Request, Response, HTTPException
from server.api.deps import (
    registry,
    _hash_password,
    _verify_password,
    _public_user,
    _require_user,
    _create_session,
    _session_token_from_request,
    _ensure_default_conversation,
    SESSION_COOKIE,
)
from server.models import UserORM, SessionORM

router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/signup")
async def signup(request: Request, response: Response):
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    name = str(body.get("name") or email.split("@")[0] or "User").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")
    if len(password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters"
        )

    with registry.SessionLocal() as session:
        existing = session.query(UserORM).filter(UserORM.email == email).first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="Email is already registered")
        user_count = session.query(UserORM).count()
        role = "admin" if user_count == 0 else "user"
        user = UserORM(
            email=email,
            name=name,
            password_hash=_hash_password(password),
            role=role,
        )
        session.add(user)
        session.flush()
        _ensure_default_conversation(session, user.user_id)
        session.commit()
        public = _public_user(user)
        user_id = user.user_id
    token = _create_session(response, user_id)
    return {"user": public, "token": token}

@router.post("/login")
async def login(request: Request, response: Response):
    body = await request.json()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    with registry.SessionLocal() as session:
        user = session.query(UserORM).filter(UserORM.email == email).first()
        if user is None or not _verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        public = _public_user(user)
        user_id = user.user_id
    token = _create_session(response, user_id)
    return {"user": public, "token": token}

@router.post("/logout")
async def logout(request: Request, response: Response):
    token = _session_token_from_request(request)
    if token:
        with registry.SessionLocal() as session:
            session_orm = session.get(SessionORM, token)
            if session_orm is not None:
                session.delete(session_orm)
                session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}

@router.get("/me")
async def me(request: Request):
    user = _require_user(request)
    return {"user": _public_user(user)}
