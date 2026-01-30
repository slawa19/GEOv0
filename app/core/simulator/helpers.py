from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional


def artifact_content_type(name: str) -> Optional[str]:
    if name.endswith(".ndjson"):
        return "application/x-ndjson"
    if name.endswith(".json"):
        return "application/json"
    if name.endswith(".zip"):
        return "application/zip"
    return None


def artifact_sha256(path: Path, *, max_bytes: Optional[int] = None) -> Optional[str]:
    try:
        h = hashlib.sha256()
        read_total = 0
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 128)
                if not chunk:
                    break
                if max_bytes is not None:
                    remaining = int(max_bytes) - read_total
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        chunk = chunk[:remaining]
                h.update(chunk)
                read_total += len(chunk)
        return h.hexdigest()
    except Exception:
        return None
