from collections import Counter, defaultdict
from datetime import datetime, timedelta


SEVERITY_SCORES = {"low": 1, "medium": 2, "high": 3, "critical": 4, "hazardous": 4}


def _get(report, attr, default=None):
    if isinstance(report, dict):
        return report.get(attr, default)
    return getattr(report, attr, default)


def _as_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.utcnow()
    return datetime.utcnow()


def _normalise_waste_type(value):
    text = str(value or "mixed").strip().lower()
    if text in ("organic", "plastic", "dry", "hazardous", "bulky", "mixed"):
        return text
    return "mixed"


def _normalise_severity(report):
    severity = str(_get(report, "severity", "medium") or "medium").strip().lower()
    risk = str(_get(report, "risk_score", "") or "").strip().lower()
    waste_type = _normalise_waste_type(_get(report, "waste_type"))
    if waste_type == "hazardous" or risk == "critical":
        return "critical"
    if severity in ("low", "medium", "high", "critical"):
        return severity
    if risk in ("low", "medium", "high"):
        return risk
    return "medium"


def _infer_volume(report):
    days_unresolved = float(_get(report, "days_unresolved", 0) or 0)
    severity = _normalise_severity(report)
    if severity in ("high", "critical") or days_unresolved >= 3:
        return "large"
    if days_unresolved >= 1:
        return "medium"
    return "medium"


def _risk_flags(report):
    flags = []
    severity = _normalise_severity(report)
    risk = str(_get(report, "risk_score", "") or "").upper()
    waste_type = _normalise_waste_type(_get(report, "waste_type"))
    issues = _get(report, "extracted_issues", []) or []

    if severity in ("high", "critical"):
        flags.append("high_severity")
    if risk == "CRITICAL":
        flags.append("critical_risk")
    if waste_type == "hazardous":
        flags.append("hazardous_waste")
    for issue in issues[:4]:
        clean = str(issue).strip().lower().replace(" ", "_")
        if clean:
            flags.append(clean)
    return sorted(set(flags))


def _confidence(report):
    if _get(report, "waste_type") and _get(report, "severity"):
        return 0.8
    if _get(report, "ai_summary"):
        return 0.6
    return 0.4


def process_reports(raw_reports):
    processed = []
    for report in list(raw_reports or [])[-200:]:
        timestamp = _as_datetime(_get(report, "created_at") or _get(report, "timestamp"))
        area = str(_get(report, "area_name", "Unknown") or "Unknown").strip() or "Unknown"
        processed.append({
            "location": {
                "lat": float(_get(report, "latitude", 13.049) or 13.049),
                "lon": float(_get(report, "longitude", 77.512) or 77.512),
            },
            "area": area,
            "waste_type": _normalise_waste_type(_get(report, "waste_type")),
            "severity": _normalise_severity(report),
            "volume": _infer_volume(report),
            "timestamp": timestamp,
            "risk_flags": _risk_flags(report),
            "confidence": _confidence(report),
        })
    return processed


def aggregate_by_area(processed_reports):
    grouped = defaultdict(list)
    for report in processed_reports or []:
        grouped[report["area"]].append(report)

    aggregated = []
    now = datetime.utcnow()
    for area, reports in grouped.items():
        waste_counts = Counter(r["waste_type"] for r in reports)
        severity_values = [SEVERITY_SCORES.get(r["severity"], 2) for r in reports]
        recent_count = sum(1 for r in reports if r["timestamp"] >= now - timedelta(days=7))
        previous_count = sum(1 for r in reports if now - timedelta(days=14) <= r["timestamp"] < now - timedelta(days=7))

        if recent_count > previous_count:
            trend = "increasing"
        elif recent_count < previous_count:
            trend = "decreasing"
        else:
            trend = "stable"

        aggregated.append({
            "area": area,
            "location": reports[-1]["location"],
            "total_reports": len(reports),
            "dominant_waste_type": waste_counts.most_common(1)[0][0],
            "avg_severity": round(sum(severity_values) / max(len(severity_values), 1), 2),
            "trend": trend,
            "recent_reports": recent_count,
            "previous_reports": previous_count,
            "risk_flags": sorted(set(flag for r in reports for flag in r["risk_flags"])),
            "waste_counts": dict(waste_counts),
            "reports": reports,
        })

    return sorted(aggregated, key=lambda item: (item["avg_severity"], item["total_reports"]), reverse=True)
