"""Customer authentication. The sandbox PIN login issues a PIN-level session token; step-up (OTP) happens in the conversation."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.core.auth import LoginResult, auth_service

router = APIRouter(prefix="/auth", tags=["Customer Authentication"])


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=64, description="Subscriber id or phone number")
    pin: str = Field(min_length=4, max_length=12, pattern=r"^\d+$")


@router.post("/login", response_model=LoginResult)
def login(req: LoginRequest):
    res = auth_service.login(req.identifier, req.pin, correlation_id=f"login-{req.identifier[-4:]}")
    if not res.success:
        raise HTTPException(status_code=401, detail=res.message)
    return res
