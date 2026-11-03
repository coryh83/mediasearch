import os
import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

if not APP_PASSWORD:
    raise RuntimeError("APP_PASSWORD must be set. Refusing to start without a login.")

_basic = HTTPBasic()


def require_login(creds: HTTPBasicCredentials = Depends(_basic)) -> None:
    user_ok = secrets.compare_digest(creds.username.encode(), APP_USER.encode())
    pass_ok = secrets.compare_digest(creds.password.encode(), APP_PASSWORD.encode())
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login required",
            headers={"WWW-Authenticate": 'Basic realm="mediasearch"'},
        )


def block_cross_site_posts(request: Request) -> None:
    """Browsers send saved Basic-auth credentials on cross-site POSTs, so reject those."""
    if request.method == "POST" and request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-site request blocked")
