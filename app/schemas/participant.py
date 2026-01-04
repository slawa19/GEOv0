from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

class ParticipantBase(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(..., pattern="^(person|organization|hub)$")
    public_key: str

class ParticipantCreateRequest(ParticipantBase):
    signature: str
    profile: Optional[Dict[str, Any]] = None

class Participant(ParticipantBase):
    pid: str
    status: str = Field(..., pattern="^(active|suspended|left|deleted)$")
    verification_level: int = Field(default=0, ge=0, le=3)
    created_at: datetime
    updated_at: datetime
    profile: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)

class ParticipantsList(BaseModel):
    items: List[Participant]