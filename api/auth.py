import hmac
import os

from dotenv import load_dotenv
from fastapi import Header, HTTPException, status

load_dotenv()

API_TOKEN_HEADER = "X-API-Key"


def get_api_token() -> str:
    return os.getenv("API_TOKEN", "").strip()


def require_api_token(
    x_api_key: str | None = Header(default=None, alias=API_TOKEN_HEADER),
) -> None:
    api_token = get_api_token()

    if not api_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_TOKEN is not configured.",
        )

    if not x_api_key or not hmac.compare_digest(x_api_key, api_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token.",
        )
