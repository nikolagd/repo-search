from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from dotenv import load_dotenv
from fastapi import Depends, Header, HTTPException, Response, status
from fastapi.security import APIKeyCookie

from etl.db import get_connection

load_dotenv()

ADMIN_COOKIE_NAME = "repo_search_admin"
CSRF_COOKIE_NAME = "repo_search_admin_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
ADMIN_COOKIE_PATH = "/api"
CSRF_COOKIE_PATH = "/"
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = int(os.getenv("ADMIN_JWT_EXPIRY_MINUTES", "120"))
JWT_ROTATION_INTERVAL_MINUTES = int(os.getenv("ADMIN_JWT_ROTATION_INTERVAL_MINUTES", "15"))
JWT_MAX_SESSION_MINUTES = int(os.getenv("ADMIN_JWT_MAX_SESSION_MINUTES", "720"))
PASSWORD_ITERATIONS = 210_000

admin_cookie_scheme = APIKeyCookie(name=ADMIN_COOKIE_NAME, auto_error=False)
csrf_cookie_scheme = APIKeyCookie(name=CSRF_COOKIE_NAME, auto_error=False)


def get_jwt_secret() -> str:
    secret = os.getenv("ADMIN_JWT_SECRET", "").strip()

    if secret and len(secret.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_JWT_SECRET must be at least 32 bytes.",
        )

    return secret


def ensure_admin_schema() -> None:
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_user (
                    id SERIAL PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
                )
                """
            )
        conn.commit()
    finally:
        conn.close()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "$".join(
        [
            "pbkdf2_sha256",
            str(PASSWORD_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False

        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def normalize_username(username: str) -> str:
    return username.strip().lower()


def has_admin_users() -> bool:
    ensure_admin_schema()
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT EXISTS (SELECT 1 FROM admin_user)")
            return bool(cur.fetchone()[0])
    finally:
        conn.close()


def create_admin_user(username: str, password: str) -> dict[str, Any]:
    ensure_admin_schema()
    normalized_username = normalize_username(username)

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO admin_user (username, password_hash)
                VALUES (%s, %s)
                RETURNING id, username, created_at
                """,
                (normalized_username, hash_password(password)),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "id": row[0],
        "username": row[1],
        "created_at": row[2],
    }


def authenticate_admin_user(username: str, password: str) -> dict[str, Any] | None:
    ensure_admin_schema()
    normalized_username = normalize_username(username)
    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, password_hash, created_at
                FROM admin_user
                WHERE username = %s
                """,
                (normalized_username,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None or not verify_password(password, row[2]):
        return None

    return {
        "id": row[0],
        "username": row[1],
        "created_at": row[3],
    }


def utc_from_claim(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid admin token timestamp.",
    )


def get_session_started_at(payload: dict[str, Any]) -> datetime:
    return utc_from_claim(payload.get("session_started_at", payload["iat"]))


def build_access_token(admin_user: dict[str, Any], session_started_at: datetime | None = None) -> tuple[str, int]:
    secret = get_jwt_secret()

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_JWT_SECRET is not configured.",
        )

    now = datetime.now(timezone.utc)
    session_started_at = session_started_at or now
    expires_at = now + timedelta(minutes=JWT_EXPIRY_MINUTES)

    if JWT_MAX_SESSION_MINUTES > 0:
        max_session_expires_at = session_started_at + timedelta(minutes=JWT_MAX_SESSION_MINUTES)
        expires_at = min(expires_at, max_session_expires_at)

    max_age = int((expires_at - now).total_seconds())

    if max_age <= 0:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin session expired.",
        )

    payload = {
        "sub": admin_user["username"],
        "uid": admin_user["id"],
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "session_started_at": int(session_started_at.timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM), max_age


def create_access_token(admin_user: dict[str, Any], session_started_at: datetime | None = None) -> str:
    token, _ = build_access_token(admin_user, session_started_at=session_started_at)
    return token


def decode_access_token(token: str) -> dict[str, Any]:
    secret = get_jwt_secret()

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_JWT_SECRET is not configured.",
        )

    try:
        return jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "uid", "iat", "exp"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired admin token.",
        ) from exc


def require_admin_user(
    response: Response,
    token: str | None = Depends(admin_cookie_scheme),
) -> dict[str, Any]:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin session.",
        )

    payload = decode_access_token(token)
    admin_user = {
        "id": payload["uid"],
        "username": payload["sub"],
    }

    rotate_admin_cookie_if_needed(response, admin_user, payload)
    return admin_user


def should_rotate_access_token(payload: dict[str, Any]) -> bool:
    if JWT_ROTATION_INTERVAL_MINUTES <= 0:
        return False

    issued_at = utc_from_claim(payload["iat"])
    rotate_after = issued_at + timedelta(minutes=JWT_ROTATION_INTERVAL_MINUTES)
    return datetime.now(timezone.utc) >= rotate_after


def rotate_admin_cookie_if_needed(
    response: Response,
    admin_user: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    if not should_rotate_access_token(payload):
        return

    set_admin_cookie(
        response,
        admin_user,
        session_started_at=get_session_started_at(payload),
    )


def require_csrf_token(
    csrf_cookie: str | None = Depends(csrf_cookie_scheme),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER_NAME),
) -> None:
    if not csrf_cookie or not csrf_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing CSRF token.",
        )

    if not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token.",
        )


def use_secure_admin_cookie() -> bool:
    return os.getenv("ADMIN_COOKIE_SECURE", "false").lower() == "true"


def set_admin_cookie(
    response: Response,
    admin_user: dict[str, Any],
    session_started_at: datetime | None = None,
) -> None:
    token, max_age = build_access_token(admin_user, session_started_at=session_started_at)
    secure = use_secure_admin_cookie()

    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path=ADMIN_COOKIE_PATH,
    )

    set_csrf_cookie(response, max_age=max_age)


def set_csrf_cookie(response: Response, max_age: int | None = None) -> None:
    max_age = max_age or JWT_EXPIRY_MINUTES * 60
    secure = use_secure_admin_cookie()

    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=secrets.token_urlsafe(32),
        max_age=max_age,
        httponly=False,
        samesite="lax",
        secure=secure,
        path=CSRF_COOKIE_PATH,
    )


def clear_admin_cookie(response: Response) -> None:
    secure = use_secure_admin_cookie()

    response.delete_cookie(
        key=ADMIN_COOKIE_NAME,
        path=ADMIN_COOKIE_PATH,
        samesite="lax",
        secure=secure,
        httponly=True,
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path=CSRF_COOKIE_PATH,
        samesite="lax",
        secure=secure,
        httponly=False,
    )
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path=ADMIN_COOKIE_PATH,
        samesite="lax",
        secure=secure,
        httponly=False,
    )


def build_auth_response(admin_user: dict[str, Any]) -> dict[str, Any]:
    return {
        "expires_in": JWT_EXPIRY_MINUTES * 60,
        "admin": {
            "id": admin_user["id"],
            "username": admin_user["username"],
        },
    }
