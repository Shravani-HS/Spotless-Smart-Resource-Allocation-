import math
from typing import Tuple


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def centroid(points: list) -> Tuple[float, float]:
    if not points:
        return 0.0, 0.0
    return sum(p[0] for p in points)/len(points), sum(p[1] for p in points)/len(points)


def risk_to_weight(risk_score: str) -> float:
    return {"LOW": 0.25, "MEDIUM": 0.50, "HIGH": 0.75, "CRITICAL": 1.0}.get(
        risk_score or "LOW", 0.25
    )


# Area → approximate (lat, lon) for Bengaluru localities
AREA_COORDS = {
    "koramangala":    (13.0358, 77.6246),
    "indiranagar":    (12.9784, 77.6408),
    "hsr layout":     (12.9116, 77.6389),
    "whitefield":     (12.9698, 77.7499),
    "jayanagar":      (12.9308, 77.5836),
    "btm layout":     (12.9165, 77.6101),
    "marathahalli":   (12.9591, 77.6972),
    "jp nagar":       (12.9077, 77.5937),
    "malleswaram":    (13.0035, 77.5666),
    "yeshwantpur":    (13.0212, 77.5474),
    "rajajinagar":    (12.9907, 77.5544),
    "sadashivanagar": (13.0087, 77.5739),
    "electronic city":(12.8399, 77.6770),
    "yelahanka":      (13.1007, 77.5963),
}


def guess_coords_from_area(area_text: str):
    """Return (lat, lon) for a known area name, else default Bengaluru centre."""
    text = area_text.lower()
    for key, coords in AREA_COORDS.items():
        if key in text:
            return coords
    return (13.049, 77.512)
