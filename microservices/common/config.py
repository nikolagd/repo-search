import os


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def service_url(name: str, default: str) -> str:
    return env(name, default).rstrip("/")


API_TOKEN_HEADER = "X-API-Key"
