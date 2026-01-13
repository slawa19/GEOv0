from datetime import datetime
from typing import List, Optional, Any, Dict

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


class ParticipantProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Optional[str] = None
    description: Optional[str] = None
    contacts: Optional[Dict[str, Any]] = None

class ParticipantBase(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=255)
    type: str = Field(default="person", pattern="^(person|business|hub)$")
    public_key: str

class ParticipantCreateRequest(ParticipantBase):
    signature: str
    profile: Optional[ParticipantProfile] = None


class ParticipantPublicStats(BaseModel):
    total_incoming_trust: str
    member_since: datetime

class Participant(ParticipantBase):
    pid: str
    status: str = Field(..., pattern="^(active|suspended|left|deleted)$")
    verification_level: int = Field(default=0, ge=0, le=3)
    created_at: datetime
    updated_at: datetime
    profile: Optional[ParticipantProfile] = None
    public_stats: Optional[ParticipantPublicStats] = None

    model_config = ConfigDict(from_attributes=True)

class ParticipantsList(BaseModel):
    items: List[Participant]


class ParticipantPublic(BaseModel):
    pid: str
    display_name: str
    status: str


class ParticipantStats(BaseModel):
    total_incoming_trust: str
    total_outgoing_trust: str
    total_debt: str
    total_credit: str
    net_balance: str


class ParticipantWithStats(Participant):
    stats: ParticipantStats


class ParticipantUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    profile: Optional[ParticipantProfile] = None
    signature: str