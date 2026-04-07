import os
from functools import wraps
from authlib.integrations.starlette_client import OAuth
from starlette.config import Config
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
ADMIN_GITHUB_USERNAME = os.getenv("ADMIN_GITHUB_USERNAME", "")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")

oauth = OAuth()
oauth.register(
    name="github",
    client_id=GITHUB_CLIENT_ID,
    client_secret=GITHUB_CLIENT_SECRET,
    access_token_url="https://github.com/login/oauth/access_token",
    authorize_url="https://github.com/login/oauth/authorize",
    api_base_url="https://api.github.com/",
    client_kwargs={"scope": "read:user"},
)


def get_current_user(request: Request):
    """Get the current user from session, or None."""
    return request.session.get("user")


def require_login(request: Request):
    """Raise 401 if not logged in."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def require_admin(request: Request):
    """Raise 403 if not admin."""
    user = require_login(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
