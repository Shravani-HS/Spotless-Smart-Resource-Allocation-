"""
Insight Engine for Spotless AI.
Produces real, data-driven intelligence from collected reports.
All insights are derived from actual report data — no dummy text.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import List, Dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _days_old(report) -> float:
    created = _safe_get(report, "created_at") or _safe_get(report, "timestamp")
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except ValueError:
            return 0.0
    if isinstance(created, datetime):
        return max(0.0, (datetime.utcnow() - created).total_seconds() / 86400)
    return 0.0


def _area(report) -> str:
    return str(_safe_get(report, "area_name") or "Unknown").strip() or "Unknown"


def _waste_type(report) -> str:
    return str(_safe_get(report, "waste_type") or "mixed").lower().strip()


def _severity(report) -> str:
    s = str(_safe_get(report, "severity") or "medium").lower().strip()
    return s if s in ("low", "medium", "high") else "medium"


def _risk(report) -> str:
    return str(_safe_get(report, "risk_score") or "LOW").upper()


def _status(report) -> str:
    return str(_safe_get(report, "status") or "pending").lower()


# ---------------------------------------------------------------------------
# Main insight generator
# ---------------------------------------------------------------------------

def generate_insights(reports) -> Dict:
    """
    Build structured insights from real report data.
    Returns a dict consumed directly by the NGO and public dashboards.
    """
    reports = list(reports or [])
    now = datetime.utcnow()

    if not reports:
        return {
            "hotspots": [],
            "emerging_issues": [],
            "collection_inefficiency": [],
            "dominant_waste_patterns": [],
            "risk_zones": [],
            "trend_summary": "No reports collected yet.",
            "actionable_recommendations": [
                "Start by collecting community reports to identify local waste patterns."
            ],
            "stats": {"total": 0, "pending": 0, "critical": 0, "avg_days_unresolved": 0.0},
        }

    # ── Group by area ──────────────────────────────────────────────────────
    by_area: dict = defaultdict(list)
    for r in reports:
        by_area[_area(r)].append(r)

    recent_cutoff  = now - timedelta(days=7)
    prev_cutoff    = now - timedelta(days=14)

    # ── Per-area aggregation ───────────────────────────────────────────────
    area_stats = []
    for area_name, area_reports in by_area.items():
        created_times = []
        for r in area_reports:
            t = _safe_get(r, "created_at") or _safe_get(r, "timestamp")
            if isinstance(t, str):
                try:
                    t = datetime.fromisoformat(t)
                except ValueError:
                    t = now
            if isinstance(t, datetime):
                created_times.append(t)
            else:
                created_times.append(now)

        recent_count  = sum(1 for t in created_times if t >= recent_cutoff)
        prev_count    = sum(1 for t in created_times if prev_cutoff <= t < recent_cutoff)
        unresolved    = [r for r in area_reports if _status(r) != "completed"]
        days_list     = [_days_old(r) for r in unresolved]
        avg_days      = round(sum(days_list) / len(days_list), 1) if days_list else 0.0
        waste_counts  = Counter(_waste_type(r) for r in area_reports)
        sev_scores    = {"low": 1, "medium": 2, "high": 3}
        avg_sev       = sum(sev_scores.get(_severity(r), 2) for r in area_reports) / len(area_reports)
        dominant_wt   = waste_counts.most_common(1)[0][0] if waste_counts else "mixed"
        critical_count= sum(1 for r in area_reports if _risk(r) == "CRITICAL")
        collection_freqs = Counter(
            str(_safe_get(r, "collection_frequency") or "irregular") for r in area_reports
        )
        # irregular is the worst outcome
        irregular_pct = int(100 * collection_freqs.get("irregular", 0) / len(area_reports))

        if recent_count > prev_count + 1:
            trend = "increasing"
        elif prev_count > recent_count + 1:
            trend = "decreasing"
        else:
            trend = "stable"

        area_stats.append({
            "area":            area_name,
            "total":           len(area_reports),
            "unresolved":      len(unresolved),
            "avg_days":        avg_days,
            "dominant_wt":     dominant_wt,
            "waste_counts":    dict(waste_counts),
            "avg_severity":    round(avg_sev, 2),
            "critical_count":  critical_count,
            "irregular_pct":   irregular_pct,
            "trend":           trend,
            "recent":          recent_count,
            "previous":        prev_count,
        })

    area_stats.sort(key=lambda x: (-x["avg_severity"], -x["total"]))

    # ── 1. Hotspots (most active areas) ───────────────────────────────────
    hotspots = [
        {
            "area":             s["area"],
            "total_reports":    s["total"],
            "unresolved":       s["unresolved"],
            "dominant_waste":   s["dominant_wt"],
            "insight":          (
                f"{s['area']} has {s['total']} report(s), "
                f"{s['unresolved']} still unresolved. "
                f"Dominant waste: {s['dominant_wt']}."
            ),
        }
        for s in sorted(area_stats, key=lambda x: x["total"], reverse=True)[:5]
    ]

    # ── 2. Emerging issues (areas with rising report counts) ──────────────
    emerging_issues = []
    for s in area_stats:
        if s["trend"] == "increasing" and s["recent"] >= 2:
            wt = s["dominant_wt"].title()
            emerging_issues.append({
                "area":    s["area"],
                "trend":   "increasing",
                "recent":  s["recent"],
                "previous":s["previous"],
                "insight": (
                    f"{wt} waste is increasing in {s['area']}: "
                    f"{s['recent']} report(s) this week vs {s['previous']} last week."
                ),
            })

    # ── 3. Collection inefficiency ─────────────────────────────────────────
    collection_inefficiency = []
    for s in area_stats:
        if s["irregular_pct"] >= 50 and s["unresolved"] >= 1:
            collection_inefficiency.append({
                "area":          s["area"],
                "irregular_pct": s["irregular_pct"],
                "avg_days":      s["avg_days"],
                "insight": (
                    f"{s['area']} shows poor collection frequency: "
                    f"{s['irregular_pct']}% of reports have irregular pickup, "
                    f"averaging {s['avg_days']} days unresolved."
                ),
            })

    # ── 4. Dominant waste patterns (across all areas) ─────────────────────
    global_waste: Counter = Counter()
    for s in area_stats:
        global_waste.update(s["waste_counts"])
    dominant_waste_patterns = [
        {"waste_type": wt, "count": cnt, "insight": f"{wt.title()} waste is the most reported type ({cnt} reports)."}
        for wt, cnt in global_waste.most_common(5)
    ]

    # ── 5. Risk zones ──────────────────────────────────────────────────────
    risk_zones = [
        {
            "area":           s["area"],
            "avg_severity":   s["avg_severity"],
            "critical_count": s["critical_count"],
            "avg_days":       s["avg_days"],
            "insight": (
                f"{s['area']} is a high-risk zone: "
                f"{s['critical_count']} critical report(s), "
                f"avg severity {s['avg_severity']:.1f}/3, "
                f"avg {s['avg_days']} days unresolved."
            ),
        }
        for s in area_stats
        if s["avg_severity"] >= 2.3 or s["critical_count"] >= 1
    ][:5]

    # ── 6. Repeated unresolved alerts ─────────────────────────────────────
    repeated_unresolved = []
    for s in area_stats:
        if s["avg_days"] >= 2 and s["unresolved"] >= 2:
            repeated_unresolved.append(
                f"{s['area']} has {s['unresolved']} reports unresolved for an average of {s['avg_days']} days."
            )

    # ── 7. Trend summary ───────────────────────────────────────────────────
    trend_counts = Counter(s["trend"] for s in area_stats)
    if trend_counts["increasing"] > trend_counts["decreasing"]:
        trend_summary = (
            f"Waste reports are increasing across {trend_counts['increasing']} area(s). "
            "Rapid dispatch is recommended for newly active zones."
        )
    elif trend_counts["decreasing"] > trend_counts["increasing"]:
        trend_summary = (
            f"Report volume is decreasing in {trend_counts['decreasing']} area(s), "
            "suggesting recent interventions are working."
        )
    else:
        trend_summary = "Report activity is broadly stable across monitored areas this week."

    # ── 8. Actionable recommendations ─────────────────────────────────────
    recommendations = []

    if hotspots:
        top = hotspots[0]
        recommendations.append(
            f"Prioritise {top['area']}: {top['total_reports']} reports, {top['unresolved']} unresolved."
        )

    if risk_zones:
        rz = risk_zones[0]
        recommendations.append(
            f"Deploy NGO-led response to {rz['area']}: {rz['critical_count']} critical alert(s), "
            f"{rz['avg_days']} days avg unresolved."
        )

    if emerging_issues:
        ei = emerging_issues[0]
        recommendations.append(
            f"Monitor {ei['area']}: {ei['insight']}"
        )

    if collection_inefficiency:
        ci = collection_inefficiency[0]
        recommendations.append(
            f"Fix collection frequency in {ci['area']}: {ci['insight']}"
        )

    if repeated_unresolved:
        recommendations.append(repeated_unresolved[0])

    if not recommendations:
        recommendations.append("Maintain routine monitoring and close low-risk reports promptly.")

    # ── 9. Global stats ────────────────────────────────────────────────────
    all_unresolved_days = [
        _days_old(r) for r in reports if _status(r) != "completed"
    ]
    stats = {
        "total":                 len(reports),
        "pending":               sum(1 for r in reports if _status(r) == "pending"),
        "critical":              sum(1 for r in reports if _risk(r) == "CRITICAL"),
        "avg_days_unresolved":   round(
            sum(all_unresolved_days) / len(all_unresolved_days), 1
        ) if all_unresolved_days else 0.0,
    }

    return {
        "hotspots":                 hotspots,
        "emerging_issues":          emerging_issues,
        "collection_inefficiency":  collection_inefficiency,
        "dominant_waste_patterns":  dominant_waste_patterns,
        "risk_zones":               risk_zones,
        "repeated_unresolved":      repeated_unresolved,
        "trend_summary":            trend_summary,
        "actionable_recommendations": recommendations,
        "stats":                    stats,
    }
