"""
Volunteer, NGO, and Citizen data models for Spotless AI.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Volunteer:
    volunteer_id: str
    name: str
    phone: str
    email: str
    street: str
    city: str
    service_areas: List[str]
    capabilities: List[str]       # light_cleanup / heavy_waste / hazard_handling
    equipment: List[str]          # gloves / vehicle / tools / protective_gear
    availability: str             # weekdays / weekends / full_time
    latitude: float = 13.049
    longitude: float = 77.512
    available: bool = True
    reputation: int = 0
    streak: int = 0
    tasks_completed: int = 0
    assigned_task: Optional[str] = None
    distance_km: Optional[float] = None
    verified: bool = True

    @property
    def badge(self) -> str:
        if self.reputation >= 100: return "Expert"
        if self.reputation >= 40:  return "Active"
        return "Beginner"

    def to_dict(self) -> dict:
        return {
            "volunteer_id": self.volunteer_id,
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "street": self.street,
            "city": self.city,
            "service_areas": self.service_areas,
            "capabilities": self.capabilities,
            "equipment": self.equipment,
            "availability": self.availability,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "available": self.available,
            "reputation": self.reputation,
            "streak": self.streak,
            "tasks_completed": self.tasks_completed,
            "verified": self.verified,
        }


@dataclass
class NGO:
    ngo_id: str
    name: str
    registration_id: str
    service_areas: List[str]
    team_size: int
    capabilities: List[str]
    latitude: float = 13.049
    longitude: float = 77.512
    contact: str = ""
    distance_km: Optional[float] = None
    verified: bool = True

    def to_dict(self) -> dict:
        return {
            "ngo_id": self.ngo_id,
            "name": self.name,
            "registration_id": self.registration_id,
            "service_areas": self.service_areas,
            "team_size": self.team_size,
            "capabilities": self.capabilities,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "contact": self.contact,
            "verified": self.verified,
        }


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_VOLUNTEERS: List[Volunteer] = [
    Volunteer("V001", "Arjun Sharma",  "+91-9876501234", "arjun@mail.com",
              "12 MG Road", "Bengaluru", ["Koramangala", "Indiranagar"],
              ["light_cleanup", "heavy_waste"], ["gloves", "vehicle"], "weekdays",
              13.050, 77.513, reputation=120, streak=5, tasks_completed=12),
    Volunteer("V002", "Priya Nair",    "+91-9876502345", "priya@mail.com",
              "45 Brigade Road", "Bengaluru", ["HSR Layout", "BTM Layout"],
              ["light_cleanup"], ["gloves", "tools"], "weekends",
              13.055, 77.510, reputation=45, streak=2, tasks_completed=4),
    Volunteer("V003", "Ravi Kumar",    "+91-9876503456", "ravi@mail.com",
              "78 Residency Road", "Bengaluru", ["Whitefield", "Marathahalli"],
              ["heavy_waste", "hazard_handling"], ["vehicle", "protective_gear"], "full_time",
              13.045, 77.518, reputation=200, streak=8, tasks_completed=20),
    Volunteer("V004", "Sneha Rao",     "+91-9876504567", "sneha@mail.com",
              "3 Church Street", "Bengaluru", ["Jayanagar", "JP Nagar"],
              ["light_cleanup", "hazard_handling"], ["gloves", "protective_gear"], "weekends",
              13.060, 77.505, reputation=60, streak=3, tasks_completed=6),
    Volunteer("V005", "Vikram Mehta",  "+91-9876505678", "vikram@mail.com",
              "22 Cunningham Road", "Bengaluru", ["Yeshwantpur", "Rajajinagar"],
              ["heavy_waste"], ["vehicle", "tools"], "full_time",
              13.042, 77.525, reputation=30, streak=1, tasks_completed=3),
    Volunteer("V006", "Ananya Iyer",   "+91-9876506789", "ananya@mail.com",
              "56 Lavelle Road", "Bengaluru", ["Sadashivanagar", "Malleswaram"],
              ["light_cleanup"], ["gloves"], "weekdays",
              13.065, 77.508, reputation=10, streak=0, tasks_completed=1),
]

SEED_NGOS: List[NGO] = [
    NGO("N001", "GreenAct Foundation",  "NGO-KA-2019-00234",
        ["Koramangala", "Indiranagar", "HSR Layout"], 45,
        ["bulk_waste", "organic_processing"],
        13.049, 77.512, "greenact@ngo.in"),
    NGO("N002", "CleanCity Collective", "NGO-KA-2017-00891",
        ["Whitefield", "Marathahalli", "Electronic City"], 60,
        ["hazmat", "bulk_waste"],
        13.058, 77.520, "cleancity@ngo.in"),
    NGO("N003", "WasteSmart NGO",       "NGO-KA-2021-01045",
        ["Jayanagar", "BTM Layout", "JP Nagar"], 30,
        ["organic_processing", "light_waste"],
        13.040, 77.500, "wastesmart@ngo.in"),
]
