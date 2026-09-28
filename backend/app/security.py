import secrets

from fastapi import Header, HTTPException, status

from app.config import settings


def require_admin(x_admin_key: str | None = Header(default=None)) -> str:
    """Validate privileged API requests without logging the supplied secret."""
    if not settings.ADMIN_API_KEY:
        if settings.APP_ENV == "production":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="administrator authentication is not configured",
            )
        return "development-admin"
    if x_admin_key is None or not secrets.compare_digest(x_admin_key, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid administrator credentials",
        )
