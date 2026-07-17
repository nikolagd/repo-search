from fastapi import Depends, FastAPI, Response

from microservices.auth_service.auth import (
    authenticate_admin_user,
    build_auth_response,
    clear_admin_cookie,
    require_admin_user,
    require_csrf_token,
    set_admin_cookie,
    set_csrf_cookie,
)
from microservices.common.db import get_connection
from microservices.common.observability import setup_observability
from microservices.common.schemas import AdminCredentials, AdminUserResponse, AuthResponse, HealthResponse
from microservices.common.security import require_api_token

app = FastAPI(title="Repo Search Auth Service", version="0.1.0")
setup_observability(app, "auth-service")


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(require_api_token)])
def health() -> HealthResponse:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        database = "ok"
    except Exception:
        database = "unavailable"
    finally:
        conn.close()

    return HealthResponse(status="ok", database=database)


@app.post("/auth/login", response_model=AuthResponse, dependencies=[Depends(require_api_token)])
def login(request: AdminCredentials, response: Response) -> AuthResponse:
    from fastapi import HTTPException

    admin_user = authenticate_admin_user(request.username, request.password)

    if admin_user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    set_admin_cookie(response, admin_user)
    return AuthResponse(**build_auth_response(admin_user))


@app.get("/auth/me", response_model=AdminUserResponse, dependencies=[Depends(require_api_token)])
def me(response: Response, admin_user: dict = Depends(require_admin_user)) -> AdminUserResponse:
    set_csrf_cookie(response)
    return AdminUserResponse(**admin_user)


@app.post(
    "/auth/logout",
    dependencies=[Depends(require_api_token), Depends(require_csrf_token)],
)
def logout(response: Response) -> dict[str, str]:
    clear_admin_cookie(response)
    return {"status": "ok"}
