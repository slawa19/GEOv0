from datetime import datetime
from pydantic import BaseModel, Field

class ChallengeRequest(BaseModel):
    pid: str

class ChallengeResponse(BaseModel):
    challenge: str
    expires_at: datetime

class LoginRequest(BaseModel):
    pid: str
    challenge: str
    signature: str


class RefreshRequest(BaseModel):
    refresh_token: str

class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = Field(default="Bearer", json_schema_extra={"example": "Bearer"})