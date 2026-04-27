"""
Urgency and risk scoring for Spotless AI.
Tracks report age in DAYS and escalates severity automatically when ignored.
Bio-urgency triggered for organic waste above temperature threshold.
"""

import logging
from datetime import datetime
from typing import List, Optional

import requests

from models.report_model import WasteReport

logger = logging.getLogger(__name__)

WEATHER_LAT        = 13.049
WEATHER_LON        = 77.512
OPENWEATHER_URL    = "https://api.openweathermap.org/data/2.5/weather"
BIO_TEMP_THRESHOLD = 28.0


class UrgencyEngine:

    def __init__(self, openweather_api_key: Optional[str] = None):
        self.ow_key       = openweather_api_key
        self._cached_temp: Optional[float] = None
        self._temp_fetched = False

    # ── Public interface ────────────────────────────────────────────────

    def enrich(self, report: WasteReport) -> WasteReport:
        """
        Attach urgency_label, days_unresolved, escalation_message,
        bio_escalated, and risk_score to the report.
        """
        days = self._days_since(report.timestamp)
        report.days_unresolved = round(days, 1)
        report.urgency_label   = self._time_urgency(days)
        report.bio_escalated   = False

        # Attention-aware escalation: override severity if report has been ignored
        if days >= 2 and (report.severity or "low") == "low":
            report.severity = "medium"
        if days >= 3 and (report.severity or "medium") in ("low", "medium"):
            report.severity = "high"

        # Bio-urgency: organic waste + heat
        if report.waste_type == "organic" and self.ow_key:
            temp = self._get_temperature()
            if temp is not None and temp > BIO_TEMP_THRESHOLD:
                report.urgency_label = "CRITICAL"
                report.bio_escalated = True

        report.risk_score         = self._composite_risk(report)
        report.escalation_message = self._escalation_message(days, report.risk_score)
        return report

    def enrich_batch(self, reports: List[WasteReport]) -> List[WasteReport]:
        return [self.enrich(r) for r in reports]

    def escalate_existing(self, reports: List[WasteReport]) -> List[WasteReport]:
        """Re-evaluate all unresolved reports (call on each dashboard load)."""
        for r in reports:
            if getattr(r, "status", "pending") != "completed":
                self.enrich(r)
        return reports

    # ── Urgency label ───────────────────────────────────────────────────

    @staticmethod
    def _time_urgency(days: float) -> str:
        """Day-based urgency tiers (not hours)."""
        if days < 1:  return "LOW"
        if days < 2:  return "MEDIUM"
        if days < 3:  return "HIGH"
        return "CRITICAL"

    @staticmethod
    def _escalation_message(days: float, risk: str) -> str:
        """
        Human-readable escalation string shown in the UI.
        Returns empty string for fresh, low-risk reports.
        """
        if days < 1:
            return ""
        days_label = f"{days:.1f}" if days != int(days) else str(int(days))
        day_word   = "day" if days <= 1.5 else "days"
        if days >= 3:
            return (
                f"This issue has been unresolved for {days_label} {day_word} "
                f"and is now at CRITICAL priority. Immediate action required."
            )
        if days >= 2:
            return (
                f"This issue has been unresolved for {days_label} {day_word} "
                f"and needs urgent attention."
            )
        return (
            f"This issue has been unresolved for {days_label} {day_word}. "
            f"Current risk level: {risk}."
        )

    # ── Composite risk score ────────────────────────────────────────────

    @staticmethod
    def _composite_risk(report: WasteReport) -> str:
        score = 0

        # Organic decomposition risk
        org = getattr(report, "organic_percent", 0) or 0
        if org > 60:   score += 2
        elif org > 30: score += 1

        score += {"daily": 0, "alternate": 1, "irregular": 2}.get(
            getattr(report, "collection_frequency", "irregular") or "irregular", 1
        )
        score += {"good": 0, "average": 1, "poor": 2}.get(
            getattr(report, "infrastructure_condition", "average") or "average", 1
        )
        score += {"low": 0, "medium": 1, "high": 2}.get(
            (getattr(report, "severity", "low") or "low").lower(), 0
        )
        score += {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}.get(
            getattr(report, "urgency_label", "LOW") or "LOW", 0
        )
        if getattr(report, "bio_escalated", False):
            score += 3

        if score <= 2:  return "LOW"
        if score <= 5:  return "MEDIUM"
        if score <= 9:  return "HIGH"
        return "CRITICAL"

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _days_since(ts) -> float:
        if ts is None:
            return 0.0
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                return 0.0
        if isinstance(ts, datetime):
            return max(0.0, (datetime.utcnow() - ts).total_seconds() / 86400)
        return 0.0

    def _get_temperature(self) -> Optional[float]:
        """Fetch temperature once per session (cached)."""
        if self._temp_fetched:
            return self._cached_temp
        self._temp_fetched = True
        if not self.ow_key:
            return None
        try:
            resp = requests.get(
                OPENWEATHER_URL,
                params={
                    "lat":   WEATHER_LAT,
                    "lon":   WEATHER_LON,
                    "appid": self.ow_key,
                    "units": "metric",
                },
                timeout=6,
            )
            resp.raise_for_status()
            self._cached_temp = float(resp.json()["main"]["temp"])
            return self._cached_temp
        except Exception as exc:
            logger.warning("OpenWeatherMap failed: %s", exc)
            return None
