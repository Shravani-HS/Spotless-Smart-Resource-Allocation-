"""
Smart allocation engine for Spotless AI.
Matches reports to volunteers and NGOs by:
  - capability (skills + equipment)
  - proximity (Haversine distance)
  - reputation score (tie-breaker)

Micro-route: detects nearby clustered tasks and suggests grouped trips.
"""

import logging
from typing import Dict, List, Optional

from models.report_model import WasteReport
from models.volunteer_model import NGO, SEED_NGOS, SEED_VOLUNTEERS, Volunteer
from utils.geo_utils import haversine_km

logger = logging.getLogger(__name__)

# Waste type → required volunteer capabilities
WASTE_CAPABILITY_MAP: Dict[str, List[str]] = {
    "organic":    ["light_cleanup"],
    "plastic":    ["light_cleanup"],
    "dry":        ["light_cleanup"],
    "bulky":      ["heavy_waste"],
    "mixed":      ["light_cleanup", "heavy_waste"],
    "hazardous":  ["hazard_handling"],
}

# Waste type → required NGO capabilities
WASTE_NGO_CAPABILITY_MAP: Dict[str, List[str]] = {
    "organic":    ["organic_processing", "bulk_waste"],
    "plastic":    ["bulk_waste", "light_waste"],
    "dry":        ["bulk_waste", "light_waste"],
    "bulky":      ["bulk_waste"],
    "mixed":      ["bulk_waste", "organic_processing"],
    "hazardous":  ["hazmat"],
}

# Equipment bonus: these improve a volunteer's score when matching report
EQUIPMENT_BONUS: Dict[str, float] = {
    "vehicle":         1.5,
    "protective_gear": 1.2,
    "tools":           1.1,
    "gloves":          1.0,
}

WASTE_EQUIPMENT_MAP: Dict[str, List[str]] = {
    "organic": ["gloves"],
    "plastic": ["gloves"],
    "dry": ["gloves", "tools"],
    "bulky": ["vehicle", "tools"],
    "mixed": ["gloves", "vehicle"],
    "hazardous": ["protective_gear", "vehicle"],
}

MICRO_TASK_DISTANCE_KM = 1.5   # radius for micro-route clustering
MAX_VOLUNTEERS         = 3      # hard cap: never notify more than this


class AllocationEngine:
    def __init__(
        self,
        volunteers: Optional[List[Volunteer]] = None,
        ngos: Optional[List[NGO]] = None,
    ):
        self.volunteers = volunteers or list(SEED_VOLUNTEERS)
        self.ngos       = ngos       or list(SEED_NGOS)

    # ── Public interface ────────────────────────────────────────────────

    def allocate(self, report: WasteReport) -> Dict:
        """
        Assign the best NGO and top 2–3 volunteers for a report.
        Returns a dict with assignment details and micro-task flag.
        """
        waste_type    = (report.waste_type or "mixed").lower()
        req_caps      = WASTE_CAPABILITY_MAP.get(waste_type, ["light_cleanup"])
        req_ngo_caps  = WASTE_NGO_CAPABILITY_MAP.get(waste_type, ["bulk_waste"])

        req_equipment = WASTE_EQUIPMENT_MAP.get(waste_type, ["gloves"])
        scored_vols  = self._rank_volunteers(report.latitude, report.longitude, req_caps, req_equipment)
        top_vols     = scored_vols[:MAX_VOLUNTEERS]
        assigned_ngo = self._best_ngo(report.latitude, report.longitude, req_ngo_caps)

        micro_eligible = (
            (report.risk_score or "LOW") in (None, "LOW", "MEDIUM")
            and bool(scored_vols)
            and (scored_vols[0].distance_km or 999) <= MICRO_TASK_DISTANCE_KM
        )

        return {
            "assigned_ngo":        assigned_ngo,
            "assigned_volunteers": top_vols,
            "micro_task_eligible": micro_eligible,
            "total_eligible":      len(scored_vols),
            "selective_notice": (
                f"Only the top {len(top_vols)} most suitable volunteer(s) were notified "
                f"out of {len(scored_vols)} eligible."
            ),
        }

    def micro_route_notice(self, vol_lat: float, vol_lon: float, reports: list) -> str:
        """
        If >= 2 active tasks cluster within MICRO_TASK_DISTANCE_KM of the
        volunteer, return a grouped-trip suggestion string.
        """
        nearby = [
            r for r in reports
            if (getattr(r, "status", "pending") or "pending") != "completed"
            and haversine_km(vol_lat, vol_lon,
                             getattr(r, "latitude", 13.049),
                             getattr(r, "longitude", 77.512)) <= MICRO_TASK_DISTANCE_KM
        ]
        if len(nearby) >= 2:
            areas = list({getattr(r, "area_name", "") for r in nearby})[:3]
            area_str = ", ".join(filter(None, areas))
            return (
                f"You can complete {len(nearby)} nearby tasks in one trip"
                + (f" ({area_str})" if area_str else "") + "."
            )
        return ""

    # ── Private helpers ─────────────────────────────────────────────────

    def _rank_volunteers(
        self,
        lat: float,
        lon: float,
        required_caps: List[str],
        required_equipment: Optional[List[str]] = None,
    ) -> List[Volunteer]:
        """
        Score each available volunteer.
        Score = proximity_score + reputation_bonus + equipment_bonus
        Lower is better for proximity; we sort ascending by composite distance
        and descending by reputation as a secondary key.
        """
        eligible: List[Volunteer] = []

        for v in self.volunteers:
            if not v.available:
                continue
            # Must have at least one required capability
            if not any(c in v.capabilities for c in required_caps):
                continue

            dist_km = round(haversine_km(lat, lon, v.latitude, v.longitude), 2)
            v.distance_km = dist_km

            # Equipment bonus reduces effective distance and directly rewards
            # responders carrying the gear required for this waste type.
            equip_mult = 1.0
            for item in (v.equipment or []):
                equip_mult *= EQUIPMENT_BONUS.get(item.lower(), 1.0)
            matched_equipment = set(required_equipment or []) & {item.lower() for item in (v.equipment or [])}
            if matched_equipment:
                equip_mult *= 1 + (0.25 * len(matched_equipment))
            effective_dist = dist_km / max(equip_mult, 1.0)

            eligible.append((effective_dist, -v.reputation, v))

        # Sort: shortest effective distance first, then highest reputation
        eligible.sort(key=lambda x: (x[0], x[1]))
        return [item[2] for item in eligible]

    def _best_ngo(
        self,
        lat: float,
        lon: float,
        required_caps: List[str],
    ) -> Optional[NGO]:
        """Return the closest NGO that has at least one required capability."""
        candidates: List[NGO] = []
        for ngo in self.ngos:
            if any(c in ngo.capabilities for c in required_caps):
                ngo.distance_km = round(haversine_km(lat, lon, ngo.latitude, ngo.longitude), 2)
                candidates.append(ngo)
        if not candidates:
            return None
        candidates.sort(key=lambda n: n.distance_km or 999)
        return candidates[0]
