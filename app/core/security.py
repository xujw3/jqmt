from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


def verify_token(x_token: Annotated[str | None, Header()] = None) -> str:
    settings = get_settings()
    if x_token != settings.secret_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效，拒绝访问。",
        )
    return settings.secret_token

