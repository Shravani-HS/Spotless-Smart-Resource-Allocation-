import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r") as fh:
        return json.load(fh)


def generate_id(prefix: str = "RPT") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


def format_timestamp(dt: datetime) -> str:
    return dt.strftime("%d %b %Y, %H:%M UTC")


def days_since(dt: datetime) -> float:
    delta = datetime.utcnow() - dt
    return delta.total_seconds() / 86400


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def severity_to_int(severity: str) -> int:
    return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(
        (severity or "low").lower(), 1
    )


def time_ago(dt: datetime) -> str:
    delta = datetime.utcnow() - dt
    secs = delta.total_seconds()
    if secs < 60:    return "just now"
    if secs < 3600:  return f"{int(secs//60)}m ago"
    if secs < 86400: return f"{int(secs//3600)}h ago"
    return f"{int(secs//86400)}d ago"
