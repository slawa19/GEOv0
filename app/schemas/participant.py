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


class EquivalentAmount(BaseModel):
    equivalent: str
    amount: str


class ParticipantPublicStats(BaseModel):
    # 028 F-028-36 (owner В-3): one amount per equivalent, never a sum across them. Only what the
    # public profile disclosed before - incoming trust and membership date - is shown.
    total_incoming_trust: List[EquivalentAmount]
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


class ParticipantEquivalentStats(BaseModel):
    equivalent: str
    total_incoming_trust: str
    total_outgoing_trust: str
    total_debt: str
    total_credit: str
    net_balance: str


class ParticipantStats(BaseModel):
    # 028 F-028-36 (owner В-3): the participant's own figures per equivalent; the scalar totals
    # summed hryvnias and hours into one number and are gone.
    per_equivalent: List[ParticipantEquivalentStats]


class ParticipantWithStats(Participant):
    stats: ParticipantStats


class ParticipantUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    profile: Optional[ParticipantProfile] = None
    signature: str