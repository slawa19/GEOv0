from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator


_ISO_CODE_RE = re.compile(r"^[A-Z]{3}$")


class EquivalentMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["fiat", "time", "commodity", "custom"]
    iso_code: Optional[str] = None

    @field_validator("iso_code")
    @classmethod
    def _validate_iso_code_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str) or not _ISO_CODE_RE.fullmatch(value):
            raise ValueError("iso_code must be ISO 4217-like (3 uppercase letters)")
        return value

    @field_validator("iso_code")
    @classmethod
    def _validate_iso_code_only_for_fiat(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return None
        # Pydantic v2: access other fields via info.data
        meta_type = (info.data or {}).get("type")
        if meta_type is not None and meta_type != "fiat":
            raise ValueError("iso_code is only allowed when metadata.type == 'fiat'")
        return value


def normalize_equivalent_metadata(raw: Any) -> Optional[dict]:
    """Validate/normalize Equivalent.metadata.

    Returns a plain dict suitable for persistence, or None if raw is None.
    """
    if raw is None:
        return None
    meta = EquivalentMetadata.model_validate(raw)
    return meta.model_dump(mode="json")
