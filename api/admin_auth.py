from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import Cookie, HTTPException, Response, status

from etl.db import get_connection

load_dotenv()

ADMIN_COOKIE_NAME = "repo_search_admin"
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = int(os.getenv("ADMIN_JWT_EXPIRY_MINUTES", "120"))
PASSWORD_ITERATIONS = 210_000


def get_jwt_secret() -> str:
    return os.getenv("ADMIN_JWT_SECRET", "").strip()


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


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_access_token(admin_user: dict[str, Any]) -> str:
    secret = get_jwt_secret()

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_JWT_SECRET is not configured.",
        )

    now = datetime.now(timezone.utc)
    payload = {
        "sub": admin_user["username"],
        "uid": admin_user["id"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRY_MINUTES)).timestamp()),
    }
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    signing_input = ".".join(
        [
            base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{base64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    secret = get_jwt_secret()

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_JWT_SECRET is not configured.",
        )

    try:
        header_b64, payload_b64, signature_b64 = token.split(".", 2)
        signing_input = f"{header_b64}.{payload_b64}"
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()

        if not hmac.compare_digest(base64url_decode(signature_b64), expected_signature):
            raise ValueError("Invalid token signature.")

        header = json.loads(base64url_decode(header_b64))
        payload = json.loads(base64url_decode(payload_b64))

        if header.get("alg") != JWT_ALGORITHM:
            raise ValueError("Unsupported token algorithm.")

        if int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("Token expired.")

        return payload
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired admin token.",
        ) from exc


def require_admin_user(
    token: str | None = Cookie(default=None, alias=ADMIN_COOKIE_NAME),
) -> dict[str, Any]:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing admin session.",
        )

    payload = decode_access_token(token)
    return {
        "id": payload["uid"],
        "username": payload["sub"],
    }


def set_admin_cookie(response: Response, admin_user: dict[str, Any]) -> None:
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=create_access_token(admin_user),
        max_age=JWT_EXPIRY_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=os.getenv("ADMIN_COOKIE_SECURE", "false").lower() == "true",
        path="/api",
    )


def clear_admin_cookie(response: Response) -> None:
    response.delete_cookie(
        key=ADMIN_COOKIE_NAME,
        path="/api",
        samesite="lax",
        secure=os.getenv("ADMIN_COOKIE_SECURE", "false").lower() == "true",
        httponly=True,
    )


def build_auth_response(admin_user: dict[str, Any]) -> dict[str, Any]:
    return {
        "expires_in": JWT_EXPIRY_MINUTES * 60,
        "admin": {
            "id": admin_user["id"],
            "username": admin_user["username"],
        },
    }
