from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.schemas.participant import ParticipantPublic

class ChallengeRequest(BaseModel):
    pid: str

class ChallengeResponse(BaseModel):
    challenge: str
    expires_at: datetime


class DeviceInfo(BaseModel):
    platform: Optional[str] = None
    app_version: Optional[str] = None


class LoginRequest(BaseModel):
    pid: str
    challenge: str
    signature: str
    device_info: Optional[DeviceInfo] = None


class RefreshRequest(BaseModel):
    refresh_token: str

class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = Field(default="Bearer", json_schema_extra={"example": "Bearer"})
    expires_in: int
    participant: ParticipantPublic