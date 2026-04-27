"""
Report data model for Spotless AI.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

STATUS_PENDING    = "pending"
STATUS_ASSIGNED   = "assigned"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED  = "completed"
STATUS_UNDER_REVIEW = "under_review"


@dataclass
class WasteReport:
    area_name: str
    observations: str
    latitude: float = 13.049
    longitude: float = 77.512

    organic_percent: float = 0.0
    plastic_percent: float = 0.0
    other_percent: float = 100.0
    collection_frequency: str = "irregular"
    infrastructure_condition: str = "average"

    timestamp: datetime = field(default_factory=datetime.utcnow)
    created_at: datetime = field(default_factory=datetime.utcnow)
    report_id: Optional[str] = None
    image_path: Optional[str] = None
    city: str = ""
    road_name: str = ""
    landmark: str = ""
    use_live_location: bool = False

    reporter_name: str = "Anonymous"
    reporter_role: str = "citizen"

    waste_type: Optional[str] = None
    severity: Optional[str] = None
    ai_summary: Optional[str] = None
    extracted_issues: Optional[list] = None
    waste_breakdown: dict = field(default_factory=dict)
    pickup_urgency: dict = field(default_factory=dict)
    recommended_actions: List[str] = field(default_factory=list)
    confidence: Optional[float] = None
    weather: dict = field(default_factory=dict)
    risk_score: Optional[str] = None
    urgency_label: Optional[str] = None
    days_unresolved: Optional[float] = None
    bio_escalated: bool = False

    status: str = STATUS_PENDING
    assigned_ngo_id: Optional[str] = None
    assigned_ngo_name: Optional[str] = None
    assigned_volunteer_ids: List[str] = field(default_factory=list)
    assigned_volunteer_names: List[str] = field(default_factory=list)
    completed_by_volunteer: Optional[str] = None
    completed_at: Optional[datetime] = None
    disputed: bool = False

    def time_ago(self) -> str:
        delta = datetime.utcnow() - self.created_at
        secs = delta.total_seconds()
        if secs < 60:    return "just now"
        if secs < 3600:  return f"{int(secs//60)} min ago"
        if secs < 86400: return f"{int(secs//3600)} hr ago"
        return f"{int(secs//86400)} days ago"

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "id": self.report_id,
            "area_name": self.area_name,
            "city": self.city,
            "road": self.road_name,
            "landmark": self.landmark,
            "description": self.observations,
            "observations": self.observations,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "organic_percent": self.organic_percent,
            "plastic_percent": self.plastic_percent,
            "other_percent": self.other_percent,
            "collection_frequency": self.collection_frequency,
            "infrastructure_condition": self.infrastructure_condition,
            "timestamp": self.timestamp.isoformat(),
            "created_at": self.created_at.isoformat(),
            "image_path": self.image_path,
            "road_name": self.road_name,
            "use_live_location": self.use_live_location,
            "reporter_name": self.reporter_name,
            "reporter_role": self.reporter_role,
            "waste_type": self.waste_type,
            "severity": self.severity,
            "ai_summary": self.ai_summary,
            "extracted_issues": self.extracted_issues or [],
            "waste_breakdown": self.waste_breakdown or {},
            "pickup_urgency": self.pickup_urgency or {},
            "recommended_actions": self.recommended_actions or [],
            "confidence": self.confidence,
            "weather": self.weather or {},
            "risk_score": self.risk_score,
            "urgency_label": self.urgency_label,
            "days_unresolved": self.days_unresolved,
            "bio_escalated": self.bio_escalated,
            "status": self.status,
            "assigned_ngo_id": self.assigned_ngo_id,
            "assigned_ngo_name": self.assigned_ngo_name,
            "assigned_to": self.assigned_ngo_name or ", ".join(self.assigned_volunteer_names or []),
            "assigned_volunteer_ids": self.assigned_volunteer_ids,
            "assigned_volunteer_names": self.assigned_volunteer_names,
            "completed_by_volunteer": self.completed_by_volunteer,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "disputed": self.disputed,
        }
