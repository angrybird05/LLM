
import secrets

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from coding_agent.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


async def verify_api_key(api_key: str | None = Depends(api_key_header)) -> str | None:
    # Auth is opt-out via AUTH_ENABLED (it used to be silently disabled whenever
    # RATE_LIMIT_ENABLED was false in development, which left every endpoint open).
    if not settings.AUTH_ENABLED:
        return "local"
    if api_key and any(
        secrets.compare_digest(api_key, k) for k in settings.api_keys_list
    ):
        return api_key
    return None


async def verify_jwt(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> dict | None:
    if not credentials:
        return None
    try:
        payload = jwt.decode(credentials.credentials, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None


async def get_current_user(
    api_key: str | None = Depends(verify_api_key),
    jwt_payload: dict | None = Depends(verify_jwt)
) -> dict:
    if api_key is None and jwt_payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key or Token")
    return {"api_key": api_key, "jwt": jwt_payload}
