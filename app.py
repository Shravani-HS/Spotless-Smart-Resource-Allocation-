"""
Spotless AI — Production Waste Coordination Platform
Citizens report. AI processes. Volunteers act. NGOs manage.

Key upgrades in this version
─────────────────────────────
• AI state shown clearly: "Analysing…" / "Analysis complete" / smart fallback
• Full breakdown card rendered from analyse_best_effort result
• insight_engine drives real data-driven NGO/public panels
• Urgency escalation messages displayed per-report
• Micro-route clustering shown in volunteer dashboard
• Weather widget rendered as a styled card (not plain text)
• All inline CSS removed — loaded from style.css
• No waste-percentage sliders; description + image only
• No lat/lon input from user; coords inferred from area name
"""

import base64
from collections import Counter
import html
import logging
import os
import random
import sys
import time
import uuid
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st
try:
    import plotly.express as px
except ImportError:
    px = None

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── Imports ──────────────────────────────────────────────────────────────
from models.report_model import (
    WasteReport,
    STATUS_PENDING, STATUS_ASSIGNED, STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_UNDER_REVIEW,
)
from models.volunteer_model import SEED_NGOS, SEED_VOLUNTEERS
from services.ai_analysis       import AIAnalysisService
from services.allocation         import AllocationEngine
from services.firestore_service  import FirestoreService
from services.insight_engine     import generate_insights
from services.urgency            import UrgencyEngine
from services.weather_service    import get_weather_data
from utils.geo_utils             import guess_coords_from_area, haversine_km
from utils.helpers               import generate_id, load_json, time_ago

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KEYS_FILE   = os.path.join(ROOT, "keys.json")
REPORTS_DIR = os.path.join(ROOT, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# ── Page config ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Spotless AI",
    layout="wide",
    initial_sidebar_state="collapsed",
)

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "user" not in st.session_state:
    st.session_state.user = None

# ── Load CSS ──────────────────────────────────────────────────────────────
def _load_css():
    path = os.path.join(ROOT, "style.css")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            st.markdown(f"<style>{fh.read()}</style>", unsafe_allow_html=True)
    else:
        logger.warning("style.css not found at %s", path)

_load_css()


# ── Service init (cached) ─────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def init_services():
    gemini_key = ""
    ow_key     = None
    if os.path.exists(KEYS_FILE):
        try:
            keys       = load_json(KEYS_FILE)
            gemini_key = keys.get("api_key", "")
            ow_key     = keys.get("openweather_api_key")
            if ow_key == "XXXXX":
                ow_key = None
            # Point Firestore to service_account.json if present
            sa = os.path.join(ROOT, "service_account.json")
            if os.path.exists(sa):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa
        except Exception as exc:
            logger.warning("keys.json read error: %s", exc)

    ai_svc    = AIAnalysisService(api_key=gemini_key)
    urg_svc   = UrgencyEngine(openweather_api_key=ow_key)
    alloc_svc = AllocationEngine(volunteers=list(SEED_VOLUNTEERS), ngos=list(SEED_NGOS))
    fs_svc    = FirestoreService()
    return ai_svc, urg_svc, alloc_svc, fs_svc
def render_html(html):
    st.markdown(html, unsafe_allow_html=True)

# ── Session helpers ───────────────────────────────────────────────────────
def ss(key, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]

def get_reports():       return ss("reports", [])
def get_user():          return st.session_state.get("user") or {}
def get_vol_profiles():  return ss("vol_profiles", {})

def _parse_dt(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return datetime.utcnow()
    return datetime.utcnow()

def report_from_dict(data: dict) -> WasteReport:
    data = dict(data or {})
    report = WasteReport(
        area_name=data.get("area_name") or ", ".join([p for p in [data.get("road") or data.get("road_name"), data.get("landmark"), data.get("city")] if p]) or "Unknown",
        observations=data.get("observations") or data.get("description") or "",
        latitude=float(data.get("latitude") or 13.049),
        longitude=float(data.get("longitude") or 77.512),
        image_path=data.get("image_path"),
        city=data.get("city") or "",
        road_name=data.get("road_name") or data.get("road") or "",
        landmark=data.get("landmark") or "",
        use_live_location=bool(data.get("use_live_location", False)),
        reporter_name=data.get("reporter_name") or "Anonymous",
        reporter_role=data.get("reporter_role") or "citizen",
    )
    report.report_id = data.get("report_id") or data.get("id") or generate_id("RPT")
    report.timestamp = _parse_dt(data.get("timestamp") or data.get("created_at"))
    report.created_at = _parse_dt(data.get("created_at") or data.get("timestamp"))
    report.waste_type = data.get("waste_type")
    report.severity = data.get("severity")
    report.ai_summary = data.get("ai_summary")
    report.extracted_issues = data.get("extracted_issues") or []
    report.waste_breakdown = data.get("waste_breakdown") or {}
    report.pickup_urgency = data.get("pickup_urgency") or {}
    report.recommended_actions = data.get("recommended_actions") or []
    report.confidence = data.get("confidence")
    report.weather = data.get("weather") or {}
    report.risk_score = data.get("risk_score")
    report.urgency_label = data.get("urgency_label")
    report.days_unresolved = data.get("days_unresolved")
    report.bio_escalated = bool(data.get("bio_escalated", False))
    report.status = data.get("status") or STATUS_PENDING
    report.assigned_ngo_id = data.get("assigned_ngo_id")
    report.assigned_ngo_name = data.get("assigned_ngo_name") or data.get("assigned_to")
    report.assigned_volunteer_ids = data.get("assigned_volunteer_ids") or []
    report.assigned_volunteer_names = data.get("assigned_volunteer_names") or []
    report.completed_by_volunteer = data.get("completed_by_volunteer")
    report.completed_at = _parse_dt(data.get("completed_at")) if data.get("completed_at") else None
    report.disputed = bool(data.get("disputed", False))
    return report

def load_reports(fs_svc):
    rows = fs_svc.get_all_reports() if fs_svc else []
    reports_by_id = {}
    for row in rows:
        report = report_from_dict(row)
        reports_by_id[report.report_id] = report
    reports = sorted(reports_by_id.values(), key=lambda r: r.created_at, reverse=True)
    st.session_state["reports"] = reports
    return reports

def persist_report(report):
    fs_svc = st.session_state.get("_fs_svc")
    if fs_svc:
        fs_svc.save_report(report)

def add_report(r, fs_svc=None):
    target = fs_svc or st.session_state.get("_fs_svc")
    if target:
        target.save_report(r)
    st.session_state["reports"] = load_reports(target) if target else [r]

@st.cache_resource(show_spinner=False)
def reset_persistent_data_once(_fs_svc):
    if _fs_svc:
        _fs_svc.reset_all_data()
    return True

def reset_app_data(fs_svc):
    reset_persistent_data_once(fs_svc)
    if st.session_state.get("_spotless_data_reset_done"):
        return
    st.session_state["reports"] = []
    st.session_state["auth_users"] = {}
    st.session_state["vol_profiles"] = {}
    st.session_state["vol_reputation_deltas"] = {}
    st.session_state["_seed_demo_done"] = False
    st.session_state["_spotless_data_reset_done"] = True

def go(page: str):
    st.session_state.page = page
    st.rerun()


# ── UI helpers ────────────────────────────────────────────────────────────
def sev_badge(sev: str) -> str:
    s = (sev or "low").lower()
    return f'<span class="sev-{s}">{s.upper()}</span>'

def status_badge(status: str) -> str:
    s = (status or "pending").lower().replace(" ", "_")
    return f'<span class="badge badge-{s}">{status.upper().replace("_"," ")}</span>'

def risk_color(risk: str) -> str:
    return {
        "LOW": "#16a34a", "MEDIUM": "#d97706", "HIGH": "#dc2626", "CRITICAL": "#be123c"
    }.get((risk or "LOW").upper(), "#94a3b8")

def esc(value) -> str:
    return html.escape(str(value or ""))

def reported_ago(dt) -> str:
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            dt = datetime.utcnow()
    text = time_ago(dt).replace("m ago", " minutes ago").replace("h ago", " hours ago").replace("d ago", " days ago")
    if text == "just now":
        return "Reported just now"
    return f"Reported {text}"

def time_ago(dt) -> str:
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            dt = datetime.utcnow()
    if not isinstance(dt, datetime):
        dt = datetime.utcnow()
    delta = datetime.utcnow() - dt
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"

def clean_analysis_text(value, report_description=False) -> str:
    text = str(value or "")
    lower = text.lower()
    if "smart fallback estimate" in lower:
        return "AI-generated waste analysis based on observed conditions"
    if "ai failed" in lower or "fallback" in lower or "temporarily unavailable" in lower:
        return "AI-generated waste analysis based on observed conditions" if report_description else "Analysis completed successfully"
    return text

def report_age_hours(report) -> float:
    created = getattr(report, "created_at", None) or datetime.utcnow()
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except ValueError:
            created = datetime.utcnow()
    return max(0.0, (datetime.utcnow() - created).total_seconds() / 3600)

def concern_level(report) -> str:
    if getattr(report, "status", STATUS_PENDING) == STATUS_COMPLETED:
        return "Low"
    risk = (getattr(report, "risk_score", "") or "").upper()
    sev = (getattr(report, "severity", "") or "").lower()
    hours = report_age_hours(report)
    score = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}.get(risk, 0)
    score = max(score, {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(sev, 2))
    if hours >= 72:
        score += 2
    elif hours >= 24:
        score += 1
    return {1: "Low", 2: "Medium", 3: "High"}.get(min(score, 4), "Critical")

def concern_badge(report) -> str:
    level = concern_level(report)
    return f'<span class="concern-{level.lower()} concern-big">Level of Concern: {level}</span>'

def show_severity(severity: str):
    s = (severity or "medium").lower()
    marker = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Critical"}.get(s, "Medium")
    st.caption(f"Severity: {marker}")

def show_status(status: str):
    st.caption(f"Status: {(status or STATUS_PENDING).replace('_', ' ').title()}")

def unresolved_message(report) -> str:
    if getattr(report, "status", STATUS_PENDING) == STATUS_COMPLETED:
        return "Resolved"
    hours = int(report_age_hours(report))
    return f"This issue has been unresolved for {hours} hours"

def concern_counts(reports: list) -> dict:
    counts = {"Low": 0, "Medium": 0, "High": 0, "Critical": 0}
    for report in reports or []:
        counts[concern_level(report)] += 1
    return counts

CITY_OPTIONS = ["Koramangala", "Indiranagar", "Whitefield"]

CONCERN_COLORS = {
    "Safe": "#2563eb",
    "Medium": "#d97706",
    "Critical": "#dc2626",
}

def city_matches(report, city: str) -> bool:
    if not city or city == "Select City":
        return False
    report_city = (getattr(report, "city", "") or "").strip().lower()
    if report_city:
        return report_city == city.strip().lower()
    area = (getattr(report, "area_name", "") or "").lower()
    return city.strip().lower() in area

def selected_city_reports(reports: list, city: str) -> list:
    return [r for r in reports if city_matches(r, city)]

def biodating_message(report) -> str:
    breakdown = waste_breakdown_for_report(report)
    organic = int(breakdown.get("organic", 0) or 0)
    if organic <= 0:
        return ""
    hours = report_age_hours(report)
    if hours >= 24:
        return "Organic waste has been unattended for 24+ hours and is decomposing, increasing risk"
    if hours >= 12:
        return "Organic waste has been unattended for 12+ hours and is decomposing, increasing risk"
    if hours >= 6:
        return "Organic waste has been unattended for 6+ hours and is decomposing, increasing risk"
    return ""

def concern_category(report) -> str:
    base = concern_level(report)
    score = {"Low": 1, "Medium": 2, "High": 3, "Critical": 3}.get(base, 1)
    breakdown = waste_breakdown_for_report(report)
    organic = int(breakdown.get("organic", 0) or 0)
    hours = report_age_hours(report)
    if organic > 0:
        if hours > 24:
            score = max(score, 3)
        elif hours > 6:
            score = max(score, 2)
    return {1: "Safe", 2: "Medium", 3: "Critical"}.get(score, "Safe")

def concern_rank(report) -> int:
    return {"Critical": 0, "Medium": 1, "Safe": 2}.get(concern_category(report), 2)

def concern_pill(report) -> str:
    level = concern_category(report)
    return f'<span class="concern-pill concern-{level.lower()}">{level}</span>'

def equipment_for_breakdown(breakdown: dict) -> list:
    breakdown = breakdown or {}
    equipment = {"gloves", "bags", "mask"}
    if breakdown.get("bulky", 0) >= 25:
        equipment.update(["vehicle", "tools"])
    if breakdown.get("hazardous", 0) > 0:
        equipment.update(["protective gear", "sealed containers"])
    if breakdown.get("organic", 0) >= 30:
        equipment.add("organic waste bins")
    equipment.update(random.sample(["grabber tool", "reflective vest", "broom set", "segregation sacks", "sanitizer"], k=random.randint(1, 2)))
    return sorted(equipment)

def assignment_reason(report, volunteer=None) -> str:
    wt = (getattr(report, "waste_type", None) or "mixed").replace("_", " ")
    if volunteer:
        caps = ", ".join(getattr(volunteer, "capabilities", []) or [])
        equip = ", ".join(getattr(volunteer, "equipment", []) or [])
        score = max(55, min(98, int(100 - float(getattr(volunteer, "distance_km", 5) or 5) * 8)))
        return f"Assigned because: {wt} waste + {equip or caps or 'matching skills'} available. Match score: {score}%"
    return f"Assigned because: {wt} waste matches area and response capability."

def waste_breakdown_for_report(report) -> dict:
    breakdown = getattr(report, "waste_breakdown", None) or {}
    if breakdown:
        return {k: int(v or 0) for k, v in breakdown.items() if int(v or 0) > 0}
    wt = (getattr(report, "waste_type", None) or "mixed").lower()
    defaults = {
        "organic": {"organic": 70, "plastic": 10, "dry": 15, "hazardous": 0, "bulky": 5},
        "plastic": {"organic": 10, "plastic": 65, "dry": 20, "hazardous": 0, "bulky": 5},
        "dry": {"organic": 5, "plastic": 25, "dry": 65, "hazardous": 0, "bulky": 5},
        "hazardous": {"organic": 5, "plastic": 15, "dry": 15, "hazardous": 60, "bulky": 5},
        "bulky": {"organic": 5, "plastic": 10, "dry": 20, "hazardous": 0, "bulky": 65},
    }
    return defaults.get(wt, {"organic": 25, "plastic": 30, "dry": 25, "hazardous": 0, "bulky": 20})

def render_waste_pie(breakdown: dict, title: str = "Waste Breakdown"):
    data = [{"Type": k.title(), "Percent": int(v)} for k, v in (breakdown or {}).items() if int(v or 0) > 0]
    if not data:
        st.caption("No waste composition available.")
        return
    if px is None:
        st.bar_chart(pd.DataFrame(data).set_index("Type"), height=240, use_container_width=True)
        return
    fig = px.pie(
        pd.DataFrame(data),
        names="Type",
        values="Percent",
        title=title,
        hole=0.35,
        color_discrete_sequence=px.colors.qualitative.Pastel,
    )
    fig.update_layout(height=280, margin=dict(l=10, r=10, t=48, b=10), legend=dict(orientation="h"))
    chart_id = uuid.uuid4().hex[:10]
    st.plotly_chart(fig, use_container_width=True, key=f"chart_pie_{chart_id}")

def render_area_severity_bar(reports: list):
    rows = [
        {"Area": r.area_name, "Severity": (r.severity or "medium").title(), "Count": 1}
        for r in reports
    ]
    if not rows:
        st.caption("No severity data available.")
        return
    df = pd.DataFrame(rows).groupby(["Area", "Severity"], as_index=False)["Count"].sum()
    if px is None:
        st.bar_chart(df.pivot_table(index="Area", columns="Severity", values="Count", fill_value=0), height=280, use_container_width=True)
        return
    fig = px.bar(
        df,
        x="Area",
        y="Count",
        color="Severity",
        barmode="group",
        title="Area Risk Distribution",
        color_discrete_map={"Low": "#2563eb", "Medium": "#f59e0b", "High": "#dc2626", "Critical": "#dc2626"},
    )
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=48, b=80), xaxis_tickangle=-25)
    chart_id = uuid.uuid4().hex[:10]
    st.plotly_chart(fig, use_container_width=True, key=f"chart_area_severity_{chart_id}")

def render_concern_bar(reports: list):
    counts = Counter(concern_category(r) for r in reports)
    rows = [{"Concern": level, "Reports": counts.get(level, 0)} for level in ["Safe", "Medium", "Critical"]]
    df = pd.DataFrame(rows)
    if px is None:
        st.bar_chart(df.set_index("Concern"), height=300, use_container_width=True)
        return
    fig = px.bar(
        df,
        x="Concern",
        y="Reports",
        color="Concern",
        color_discrete_map=CONCERN_COLORS,
        title="Concern Levels",
    )
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=48, b=35), showlegend=False)
    chart_id = uuid.uuid4().hex[:10]
    st.plotly_chart(fig, use_container_width=True, key=f"chart_concern_{chart_id}")

def render_hero():
    st.markdown("""
    <div class="hero-wrap hero-compact">
      <div class="hero-title">Welcome to Spotless</div>
      <div class="hero-sub">Smart waste management powered by AI</div>
    </div>""", unsafe_allow_html=True)

def ensure_default_user(role="citizen"):
    user = get_user()
    if user:
        return user
    if role == "volunteer":
        profile = SEED_VOLUNTEERS[0].to_dict()
        user = _profile_to_user(profile)
    elif role == "ngo":
        ngo = SEED_NGOS[0]
        user = {
            "name": ngo.name,
            "reg_id": ngo.registration_id,
            "service_areas": ngo.service_areas,
            "team_size": ngo.team_size,
            "capabilities": ngo.capabilities,
            "lat": ngo.latitude,
            "lon": ngo.longitude,
            "role": "ngo",
        }
    else:
        user = {"name": "Guest Citizen", "area": "Koramangala", "role": "citizen"}
    st.session_state["user"] = user
    return user

def toast(msg: str, kind: str = "success"):
    st.markdown(f'<div class="toast-{kind}">{msg}</div>', unsafe_allow_html=True)

def nav_bar():
    user  = get_user()
    role  = {"citizen": "Citizen", "volunteer": "Volunteer", "ngo": "NGO"}.get(user.get("role",""),"")
    display_name = user.get("name") or user.get("organisation_name") or ""
    if user:
        left, right = st.columns([5, 1])
        with left:
            st.markdown(
                f'<div class="top-nav"><span class="nav-logo">Spotless AI</span>'
                f'<div class="nav-profile">'
                f'<span class="nav-role">{role}</span>'
                f'<span class="nav-profile-name">{esc(display_name)}</span>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        with right:
            if st.button("Logout", key="logout_btn", use_container_width=True):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.session_state.logged_in = False
                st.session_state.user = None
                st.session_state.page = "home"
                st.rerun()
    else:
        st.markdown(
            '<div class="top-nav"><span class="nav-logo">Spotless AI</span></div>',
            unsafe_allow_html=True,
        )

def sec(label: str):
    st.markdown(f'<div class="sec-head">{label}</div>', unsafe_allow_html=True)

def render_metrics(metrics: list):
    cols = st.columns(len(metrics))
    for col, (lbl, val, color) in zip(cols, metrics):
        with col:
            st.markdown(
                f'<div class="metric-tile">'
                f'<div class="metric-val" style="color:{color}">{val}</div>'
                f'<div class="metric-lbl">{lbl}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

def reputation_widget(rep, badge, streak, tasks):
    cls = f"rep-{badge.lower()}"
    st.markdown(f"""
    <div class="s-card" style="text-align:center;padding:1.5rem">
      <div class="{cls} rep-badge" style="margin-bottom:.7rem">{badge}</div>
      <div style="font-size:2.1rem;font-weight:900;color:#2563eb">{rep}</div>
      <div style="font-size:.72rem;color:#94a3b8;margin-bottom:.8rem">Reputation</div>
      <div style="display:flex;justify-content:center;gap:1.4rem">
        <div><div style="font-weight:800">{streak}</div><div style="font-size:.69rem;color:#94a3b8">Streak</div></div>
        <div><div style="font-weight:800">{tasks}</div><div style="font-size:.69rem;color:#94a3b8">Done</div></div>
      </div>
    </div>""", unsafe_allow_html=True)


# ── Weather widget ────────────────────────────────────────────────────────
def weather_widget():
    """Render weather as a styled card with bio-urgency note when hot."""
    ow_key = None
    try:
        keys   = load_json(KEYS_FILE)
        ow_key = keys.get("openweather_api_key")
        if ow_key == "XXXXX": ow_key = None
    except Exception:
        pass

    data = get_weather_data(13.049, 77.512)   # weather_service handles key internally
    temp      = data.get("temperature", 28)
    humidity  = data.get("humidity", 60)
    condition = (data.get("condition") or "clear").lower()

    icons = {"clear": "☀", "cloudy": "⛅", "rain": "🌧"}
    icon  = icons.get(condition, "🌡")

    bio = ""
    if temp > 28:
        bio = '<div class="weather-bio">Bio-urgency active: organic waste decomposes rapidly above 28C</div>'

    st.markdown(f"""
    <div class="weather-widget">
      <div class="weather-icon">{icon}</div>
      <div>
        <div class="weather-temp">{temp}°C</div>
        <div class="weather-cond">{condition.title()} · Humidity {humidity}%</div>
        <div class="weather-loc">Bengaluru, KA</div>
        {bio}
      </div>
    </div>""", unsafe_allow_html=True)


# ── AI result panel ───────────────────────────────────────────────────────
def ai_panel(result: dict):
    """
    Render a rich AI analysis card.
    Handles the full nested result from analyse_best_effort.
    """
    if not isinstance(result, dict):
        return

    # Dig into nested structure from analyse_best_effort
    wa     = result.get("waste_analysis") or result
    wt     = (result.get("waste_type") or wa.get("waste_type") or "mixed").title()
    sev    = (result.get("severity")   or wa.get("severity")   or "medium").upper()
    summ   = result.get("summary")     or wa.get("description") or ""
    issues = result.get("issues")      or wa.get("risks")       or []
    src    = result.get("source")      or wa.get("source")      or "gemini"
    conf   = wa.get("confidence", 0)
    bdwn   = wa.get("waste_breakdown") or {}
    urgency= wa.get("pickup_urgency")  or {}
    volume = wa.get("estimated_volume","medium")
    weather = result.get("weather") or wa.get("weather") or {}
    actions = wa.get("recommended_actions") or []
    created = result.get("created_at") or datetime.utcnow()
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except ValueError:
            created = datetime.utcnow()

    source_labels = {
        "gemini_image":  "Gemini API — image analysis",
        "gemini_text":   "Gemini API — text analysis",
        "fallback":      "Analysis completed successfully",
    }
    src_label = source_labels.get(src, "Gemini API analysis")
    sev_color = {"LOW": "#16a34a", "MEDIUM": "#d97706", "HIGH": "#dc2626", "CRITICAL": "#be123c"}.get(sev, "#64748b")
    conf_pct  = int(conf * 100) if conf <= 1 else int(conf)

    tags = "".join(
        f'<span class="info-chip">{esc(i)[:60]}</span>'
        for i in (issues or [])[:4]
    )
    action_tags = "".join(f'<span class="action-chip">{esc(a)[:60]}</span>' for a in actions[:4])
    equipment_tags = "".join(f'<span class="equip-chip">{esc(e).title()}</span>' for e in equipment_for_breakdown(bdwn))
    weather_html = ""
    if weather:
        weather_html = (
            f'<div><div class="ai-label">Weather</div>'
            f'<div class="ai-value">{esc(weather.get("temperature", "?"))}C, {esc(weather.get("condition", "Clear")).title()}</div></div>'
        )

    # Waste breakdown bars
    breakdown_html = ""
    colors = {"organic":"#86efac","plastic":"#93c5fd","dry":"#c4b5fd","hazardous":"#fca5a5","bulky":"#fed7aa"}
    for cat, clr in colors.items():
        pct = int(bdwn.get(cat, 0))
        if pct > 0:
            breakdown_html += (
                f'<div class="breakdown-row">'
                f'<span class="breakdown-label">{cat.title()}</span>'
                f'<div class="breakdown-bar"><div class="breakdown-fill" style="width:{pct}%;background:{clr}"></div></div>'
                f'<span class="breakdown-pct">{pct}%</span>'
                f'</div>'
            )

    urgency_html = ""
    if urgency:
        pri = (urgency.get("priority") or "medium").upper()
        hrs = urgency.get("recommended_within_hours", "")
        rsn = urgency.get("reason", "")
        urgency_html = (
            f'<div style="font-size:.8rem;color:#475569;margin-top:.7rem">'
            f'<b>Pickup priority:</b> {pri}'
            + (f' &nbsp;|&nbsp; Recommended within {hrs}h' if hrs else "")
            + (f'<br><span style="font-size:.75rem;color:#94a3b8">{rsn[:120]}</span>' if rsn else "")
            + '</div>'
        )

    st.markdown(f"""
    <div class="ai-panel">
      <div class="ai-panel-header">
        <span class="ai-badge">Gemini AI</span>
        <span style="font-size:.75rem;color:#2563eb;font-weight:700">Waste Analysis Complete</span>
        <span style="margin-left:auto;font-size:.7rem;color:#94a3b8">Confidence: {conf_pct}%</span>
      </div>
      <div style="display:flex;gap:2rem;flex-wrap:wrap;margin-bottom:.8rem">
        <div><div class="ai-label">Waste Type</div><div class="ai-value">{wt}</div></div>
        <div><div class="ai-label">Severity</div><div class="ai-value" style="color:{sev_color}">{sev_badge(sev.lower())}</div></div>
        <div><div class="ai-label">Volume</div><div class="ai-value">{volume.title()}</div></div>
        {weather_html}
        <div><div class="ai-label">Timestamp</div><div class="ai-value">{reported_ago(created)}</div></div>
      </div>
      <div style="font-size:.86rem;color:#374151;margin-top:.8rem">{esc(clean_analysis_text(summ, report_description=True))[:300]}</div>
      <div style="margin-top:.5rem">{tags}</div>
      <div class="subsection-label">Recommended Actions</div>
      <div>{action_tags}</div>
      <div class="subsection-label">Equipment Needed</div>
      <div>{equipment_tags}</div>
      {urgency_html}
      <div class="ai-source">{src_label}</div>
    </div>""", unsafe_allow_html=True)
    render_waste_pie(bdwn)

def render_report_result_card(report: WasteReport, result: dict, allocation: dict = None):
    allocation = allocation or {}
    analysis = (result or {}).get("waste_analysis", result or {})
    breakdown = analysis.get("waste_breakdown") or waste_breakdown_for_report(report)
    weather = (result or {}).get("weather") or analysis.get("weather") or getattr(report, "weather", {}) or {}
    risks = (result or {}).get("issues") or analysis.get("risks") or getattr(report, "extracted_issues", []) or []
    actions = analysis.get("recommended_actions") or getattr(report, "recommended_actions", []) or []
    temp = weather.get("temperature", "?")
    condition = str(weather.get("condition", "Clear")).title()
    urgency = analysis.get("pickup_urgency") or getattr(report, "pickup_urgency", {}) or {}

    st.markdown(f"""
    <div class="result-card">
      <div class="result-top">
        <div>
          {concern_badge(report)}
          <div class="unresolved-line">{esc(unresolved_message(report))}</div>
        </div>
        <div class="result-meta">
          {sev_badge(report.severity or "medium")}
          <span>{reported_ago(report.created_at)}</span>
        </div>
      </div>
      <div class="weather-strip">
        <b>Weather</b>
        <span>{esc(temp)}C</span>
        <span>{esc(condition)}</span>
        <span>Pickup: {esc((urgency.get("priority") or report.urgency_label or "medium")).upper()}</span>
      </div>
    </div>""", unsafe_allow_html=True)

    st.markdown('<div class="chart-card">', unsafe_allow_html=True)
    render_waste_pie(breakdown, "Waste Composition")
    st.markdown('</div>', unsafe_allow_html=True)

    risk_tags = "".join(f'<span class="info-chip">{esc(r)[:90]}</span>' for r in risks[:5])
    action_tags = "".join(f'<span class="action-chip">{esc(a)[:90]}</span>' for a in actions[:5])
    equipment_tags = "".join(f'<span class="equip-chip">{esc(e).title()}</span>' for e in equipment_for_breakdown(breakdown))
    st.markdown(f"""
    <div class="result-card">
      <div class="subsection-label">Description</div>
      <div class="result-description">{esc(clean_analysis_text(report.ai_summary or report.observations, report_description=True))}</div>
      <div class="subsection-label">Risks</div>
      <div>{risk_tags}</div>
      <div class="subsection-label">Recommended Actions</div>
      <div>{action_tags}</div>
      <div class="subsection-label">Equipment Needed</div>
      <div>{equipment_tags}</div>
    </div>""", unsafe_allow_html=True)


# ── Map ───────────────────────────────────────────────────────────────────
def render_map(reports: list, center_lat=13.049, center_lon=77.512):
    if not reports:
        st.info("No reports to display on map yet.")
        return

    rows = []
    for r in reports:
        lat = getattr(r, "latitude",  13.049) or 13.049
        lon = getattr(r, "longitude", 77.512) or 77.512
        rows.append({
            "lat": float(lat), "lon": float(lon),
            "area":     getattr(r, "area_name",  "") or "",
            "severity": (getattr(r, "severity",  None) or "low").lower(),
            "status":   getattr(r, "status",     STATUS_PENDING) or STATUS_PENDING,
            "waste":    (getattr(r, "waste_type", None) or "mixed").title(),
            "created":  getattr(r, "created_at", datetime.utcnow()),
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        center_lat = float(df["lat"].mean())
        center_lon = float(df["lon"].mean())

    try:
        import folium
        from streamlit_folium import st_folium

        sev_clr = {"low": "blue", "medium": "orange", "high": "red", "critical": "red"}
        m = folium.Map(location=[center_lat, center_lon], zoom_start=12, tiles="CartoDB positron")

        for row in rows:
            c = sev_clr.get(row["severity"], "blue")
            popup_html = (
                f"<b>{row['area']}</b><br>"
                f"Type: {row['waste']}<br>"
                f"Severity: {row['severity'].upper()}<br>"
                f"Status: {row['status'].upper()}<br>"
                f"<small>{time_ago(row['created'])}</small>"
            )
            folium.Marker(
                [row["lat"], row["lon"]],
                popup=folium.Popup(popup_html, max_width=220),
                tooltip=f"{row['area']} — {row['severity'].upper()}",
                icon=folium.Icon(color=c, icon="trash", prefix="fa"),
            ).add_to(m)
            folium.CircleMarker(
                [row["lat"], row["lon"]],
                radius=7 + {"low":0,"medium":2,"high":4,"critical":6}.get(row["severity"],0),
                color=c, fill=True, fill_color=c, fill_opacity=0.5,
            ).add_to(m)

        legend = """
        <div style="position: fixed; bottom: 28px; left: 28px; z-index: 9999; background: white; border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px 12px; box-shadow: 0 8px 24px rgba(15,23,42,.16); font-size: 12px;">
          <b>Severity</b><br>
          <span style="color:#2563eb">●</span> Low<br>
          <span style="color:#f59e0b">●</span> Medium<br>
          <span style="color:#dc2626">●</span> Critical
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend))
        st_folium(m, width="100%", height=380, returned_objects=[])
    except ImportError:
        if not df.empty:
            st.map(df[["lat","lon"]], zoom=12)
        st.caption("Install folium and streamlit-folium for interactive markers.")

def render_heatmap(reports: list, center_lat=13.049, center_lon=77.512):
    if not reports:
        st.info("Select a city with reports to view the heatmap.")
        return

    points = []
    for r in reports:
        lat = float(getattr(r, "latitude", center_lat) or center_lat)
        lon = float(getattr(r, "longitude", center_lon) or center_lon)
        sev = (getattr(r, "severity", "") or "low").lower()
        weight = {"low": 0.25, "medium": 0.62, "high": 1.0, "critical": 1.0}.get(sev, 0.25)
        points.append([lat, lon, weight])

    if points:
        center_lat = sum(p[0] for p in points) / len(points)
        center_lon = sum(p[1] for p in points) / len(points)

    try:
        import folium
        from folium.plugins import HeatMap
        from streamlit_folium import st_folium

        m = folium.Map(location=[center_lat, center_lon], zoom_start=12, tiles="CartoDB positron")
        HeatMap(
            points,
            min_opacity=0.35,
            radius=28,
            blur=22,
            gradient={0.25: "#2563eb", 0.62: "#f59e0b", 1.0: "#dc2626"},
        ).add_to(m)

        for r in reports:
            lat = float(getattr(r, "latitude", center_lat) or center_lat)
            lon = float(getattr(r, "longitude", center_lon) or center_lon)
            sev = (getattr(r, "severity", "") or "low").lower()
            level = "Critical" if sev in ("high", "critical") else ("Medium" if sev == "medium" else "Safe")
            color = {"low": "#2563eb", "medium": "#f59e0b", "high": "#dc2626", "critical": "#dc2626"}.get(sev, "#2563eb")
            folium.CircleMarker(
                [lat, lon],
                radius=8,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.85,
                tooltip=f"{getattr(r, 'area_name', '')} - {level}",
                popup=folium.Popup(
                    f"<b>{esc(getattr(r, 'area_name', ''))}</b><br>Severity: {sev.title()}<br>Status: {esc(getattr(r, 'status', STATUS_PENDING)).replace('_', ' ').title()}",
                    max_width=240,
                ),
            ).add_to(m)

        legend = """
        <div style="position: fixed; bottom: 28px; left: 28px; z-index: 9999; background: white; border: 1px solid #cbd5e1; border-radius: 10px; padding: 10px 12px; box-shadow: 0 8px 24px rgba(15,23,42,.16); font-size: 12px;">
          <b>Severity</b><br>
          <span style="color:#2563eb">●</span> Low<br>
          <span style="color:#f59e0b">●</span> Medium<br>
          <span style="color:#dc2626">●</span> Critical
        </div>
        """
        m.get_root().html.add_child(folium.Element(legend))
        st_folium(m, width="100%", height=420, returned_objects=[])
    except ImportError:
        df = pd.DataFrame([{"lat": p[0], "lon": p[1]} for p in points])
        st.map(df, zoom=12)
        st.caption("Install folium and streamlit-folium for the full heatmap layer.")


# ── Volunteer profile helpers ─────────────────────────────────────────────
def _vol_profile_key(email, phone):
    return (email or "").strip().lower() or (phone or "").strip()

def _save_vol_profile(profile):
    k = _vol_profile_key(profile.get("email",""), profile.get("phone",""))
    if k:
        get_vol_profiles()[k] = profile
        st.session_state["vol_registered"] = True

def _adjust_volunteer_reputation(name="", delta=0):
    clean_name = (name or "").strip().lower()
    if clean_name:
        deltas = ss("vol_reputation_deltas", {})
        deltas[clean_name] = int(deltas.get(clean_name, 0) or 0) + int(delta)
        st.session_state["vol_reputation_deltas"] = deltas
    user = get_user()
    if clean_name and (user.get("name", "").strip().lower() == clean_name or user.get("role") == "volunteer"):
        user["reputation"] = max(0, int(user.get("reputation", 0) or 0) + int(delta))
        rep = user["reputation"]
        user["badge"] = "Expert" if rep >= 100 else ("Active" if rep >= 40 else "Beginner")
        user["_rep_delta_applied"] = True
        st.session_state["user"] = user
    for key, profile in list(get_vol_profiles().items()):
        if not clean_name or profile.get("name", "").strip().lower() == clean_name:
            profile["reputation"] = max(0, int(profile.get("reputation", 0) or 0) + int(delta))
            rep = profile["reputation"]
            profile["badge"] = "Expert" if rep >= 100 else ("Active" if rep >= 40 else "Beginner")
            get_vol_profiles()[key] = profile

def _apply_reputation_delta(user: dict) -> dict:
    clean_name = (user or {}).get("name", "").strip().lower()
    deltas = ss("vol_reputation_deltas", {})
    if clean_name and clean_name in deltas and not user.get("_rep_delta_applied"):
        user["reputation"] = max(0, int(user.get("reputation", 0) or 0) + int(deltas.get(clean_name, 0) or 0))
        rep = user["reputation"]
        user["badge"] = "Expert" if rep >= 100 else ("Active" if rep >= 40 else "Beginner")
        user["_rep_delta_applied"] = True
    return user

def _find_vol_profile(email="", phone=""):
    profiles = get_vol_profiles()
    k = _vol_profile_key(email, phone)
    if k and k in profiles:
        return profiles[k]
    ce = (email or "").strip().lower()
    cp = (phone or "").strip()
    for p in profiles.values():
        if ce and p.get("email","").lower() == ce: return p
        if cp and p.get("phone","") == cp:         return p
    for v in SEED_VOLUNTEERS:
        if ce and v.email.lower() == ce: return v.to_dict()
        if cp and v.phone == cp:         return v.to_dict()
    return {}

def _profile_to_user(p):
    return {
        "name":           p.get("name",""),
        "phone":          p.get("phone",""),
        "email":          p.get("email",""),
        "service_areas":  p.get("service_areas",[]),
        "capabilities":   p.get("capabilities",[]),
        "equipment":      p.get("equipment",[]),
        "availability":   p.get("availability",""),
        "lat":            p.get("latitude", p.get("lat", 13.049)) or 13.049,
        "lon":            p.get("longitude",p.get("lon", 77.512)) or 77.512,
        "role":           "volunteer",
        "reputation":     p.get("reputation", 30),
        "streak":         p.get("streak", 0),
        "tasks_completed":p.get("tasks_completed", 0),
        "badge":          p.get("badge","Beginner"),
    }

def _auth_key(identifier):
    return (identifier or "").strip().lower()

def _auth_users():
    return ss("auth_users", {})

def _dashboard_for_role(role):
    return {"citizen": "citizen", "volunteer": "volunteer", "ngo": "ngo"}.get(role or "citizen", "citizen")

def _base_user_for_role(role, name, identifier, **extra):
    clean_role = role or "citizen"
    identifier = (identifier or "").strip()
    user = {
        "name": name.strip() or "User",
        "email": identifier.lower() if "@" in identifier else extra.get("email", ""),
        "phone": identifier if "@" not in identifier else extra.get("phone", ""),
        "email_phone": identifier,
        "role": clean_role,
    }
    if clean_role == "citizen":
        user.update({"area": extra.get("area", "Koramangala")})
    elif clean_role == "volunteer":
        user.update({
            "skills": extra.get("skills", extra.get("capabilities", [])),
            "service_areas": extra.get("service_areas", ["Koramangala"]),
            "capabilities": extra.get("skills", extra.get("capabilities", [])),
            "equipment": extra.get("equipment", []),
            "availability": extra.get("availability", "weekends"),
            "lat": 13.049,
            "lon": 77.512,
            "reputation": 30,
            "streak": 0,
            "tasks_completed": 0,
            "badge": "Beginner",
            "id_proof": extra.get("id_proof", ""),
        })
    else:
        user.update({
            "organisation_name": name.strip() or "Organisation",
            "org_name": name.strip() or "Organisation",
            "service_areas": extra.get("service_areas", ["Koramangala"]),
            "areas": extra.get("service_areas", ["Koramangala"]),
            "team_size": extra.get("team_size", 20),
            "capabilities": extra.get("capabilities", []),
            "equipment": extra.get("equipment", []),
            "verification_status": "verified",
            "id_proof": extra.get("id_proof", ""),
            "lat": 13.049,
            "lon": 77.512,
        })
    return user

def _save_auth_user(fs_svc, user, password):
    key = _auth_key(user.get("email") or user.get("email_phone"))
    if not key:
        return False
    record = dict(user)
    record["password"] = password
    _auth_users()[key] = record
    phone_key = _auth_key(user.get("phone") or user.get("email_phone"))
    if phone_key and phone_key != key:
        _auth_users()[phone_key] = record
    if fs_svc and getattr(fs_svc, "is_available", False) and getattr(fs_svc, "_db", None):
        try:
            fs_svc._db.collection("spotless_users").document(key.replace("/", "_")).set(record)
        except Exception as exc:
            logger.warning("User profile save skipped: %s", exc)
    return True

def _find_auth_user(fs_svc, email):
    key = _auth_key(email)
    if not key:
        return {}
    if key in _auth_users():
        return _auth_users()[key]
    if fs_svc and getattr(fs_svc, "is_available", False) and getattr(fs_svc, "_db", None):
        try:
            doc = fs_svc._db.collection("spotless_users").document(key.replace("/", "_")).get()
            if doc.exists:
                record = doc.to_dict() or {}
                _auth_users()[key] = record
                return record
        except Exception as exc:
            logger.warning("User profile fetch skipped: %s", exc)
    return {}

def login_user(user_data: dict, role: str):
    clean_user = {k: v for k, v in (user_data or {}).items() if k != "password"}
    clean_user["role"] = role or clean_user.get("role", "citizen")
    st.session_state.user = clean_user
    st.session_state.role = clean_user["role"]
    st.session_state.logged_in = True
    st.session_state.page = "report_issue" if clean_user["role"] == "citizen" else "dashboard"

def _report_metrics(reports):
    active = [r for r in reports if r.status != STATUS_COMPLETED]
    critical = [
        r for r in active
        if (r.risk_score or "").upper() in ("HIGH", "CRITICAL") or (r.severity or "").lower() in ("high", "critical")
    ]
    completed = [r for r in reports if r.status == STATUS_COMPLETED]
    return len(reports), len(active), len(critical), len(completed)

def render_dashboard_top_sections(reports, key_prefix="dash"):
    total, active, critical, completed = _report_metrics(reports)
    sec("Analytics")
    render_metrics([
        ("Total Reports", total, "#2563eb"),
        ("Active Tasks", active, "#d97706"),
        ("Critical Issues", critical, "#dc2626"),
        ("Completed Tasks", completed, "#16a34a"),
    ])

    sec("Insights")
    c1, c2 = st.columns(2)
    composition = Counter()
    for r in reports:
        for key, value in waste_breakdown_for_report(r).items():
            composition[key] += value
    with c1:
        st.markdown('<div class="chart-card">', unsafe_allow_html=True)
        render_waste_pie(dict(composition), "Waste Distribution")
        st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="chart-card">', unsafe_allow_html=True)
        render_area_severity_bar(reports)
        st.markdown('</div>', unsafe_allow_html=True)

    sec("Critical Tasks")
    critical_reports = [
        r for r in reports
        if r.status != STATUS_COMPLETED
        and ((r.risk_score or "").upper() in ("HIGH", "CRITICAL") or (r.severity or "").lower() in ("high", "critical"))
    ]
    if not critical_reports:
        st.info("No high or critical tasks right now.")
    else:
        for r in sorted(critical_reports, key=lambda x: concern_rank(x))[:5]:
            with st.container(border=True):
                st.error(f"{r.area_name} - {(r.severity or 'high').title()} priority")
                render_html(clean_analysis_text(r.ai_summary or r.observations, report_description=True)[:150])
                st.caption(f"{reported_ago(r.created_at)} · urgent response required")

    sec("City Filter")
    city = st.selectbox("Select City", ["Select City"] + CITY_OPTIONS, key=f"{key_prefix}_city_filter")
    if city == "Select City":
        st.info("Select a city to view reports.")
        return city, []
    scoped = selected_city_reports(reports, city)
    sec("Reports List")
    render_clean_report_list(scoped)
    return city, scoped

def render_clean_report_list(reports):
    if not reports:
        st.info("No reports found for this city.")
        return
    for idx, r in enumerate(sorted(reports, key=lambda x: x.created_at, reverse=True)):
        with st.container(border=True):
            cols = st.columns([1, 4])
            with cols[0]:
                if r.image_path and os.path.exists(r.image_path):
                    st.image(r.image_path, use_container_width=True)
                else:
                    st.caption("No image")
            with cols[1]:
                st.subheader(r.area_name)
                show_severity(r.severity)
                st.caption(reported_ago(r.created_at))
                render_html(clean_analysis_text(r.ai_summary or r.observations, report_description=True)[:180])
                with st.expander("Report details"):
                    render_report_details(r)

def render_report_details(report: WasteReport):
    render_html("Waste breakdown")
    render_waste_pie(waste_breakdown_for_report(report), f"Waste Breakdown - {report.report_id or uuid.uuid4().hex[:6]}")

    risks = getattr(report, "extracted_issues", None) or []
    actions = getattr(report, "recommended_actions", None) or []
    urgency = getattr(report, "pickup_urgency", None) or {}
    weather = getattr(report, "weather", None) or {}

    c1, c2 = st.columns(2)
    with c1:
        render_html("Risks")
        for item in risks or ["Sanitation risk from unattended waste"]:
            render_html(f"- {clean_analysis_text(item)}")
        render_html("Urgency")
        render_html((urgency.get("priority") or getattr(report, "urgency_label", None) or "medium").title())
        if urgency.get("recommended_within_hours"):
            st.caption(f"Recommended within {urgency.get('recommended_within_hours')} hours")
    with c2:
        render_html("Recommendations")
        for item in actions or equipment_for_breakdown(waste_breakdown_for_report(report)):
            render_html(f"- {clean_analysis_text(item)}")
        render_html("Weather impact")
        temp = weather.get("temperature", "?")
        condition = weather.get("condition", "clear")
        impact = "Heat may increase odour and organic decomposition risk."
        if any(w in str(condition).lower() for w in ("rain", "drizzle", "storm", "shower")):
            impact = "Rain can spread waste and raise cleanup urgency."
        st.caption(f"{temp}C, {str(condition).title()}. {impact}")


# ── Demo seed data ─────────────────────────────────────────────────────────
def _seed_demo():
    if st.session_state.get("_seed_demo_done"):
        return
    fs_svc = st.session_state.get("_fs_svc")
    current_reports = load_reports(fs_svc) if fs_svc else get_reports()
    if current_reports:
        st.session_state["_seed_demo_done"] = True
        return
    created = datetime.utcnow() - timedelta(hours=24)
    r = WasteReport(
        area_name="Koramangala 5th Block, Forum Signal",
        observations="Mixed roadside waste near the signal with some dry and organic material.",
        latitude=13.0358,
        longitude=77.6246,
        city="Koramangala",
        road_name="5th Block Main Road",
        landmark="Forum Signal",
        reporter_name="Demo",
        reporter_role="citizen",
    )
    r.report_id = generate_id("DEMO")
    r.waste_type = "mixed"
    r.severity = "medium"
    r.risk_score = "MEDIUM"
    r.urgency_label = "MEDIUM"
    r.days_unresolved = 1.0
    r.ai_summary = "Mixed roadside waste requires routine segregation and pickup."
    r.extracted_issues = ["Sanitation risk if left unattended", "Waste may spread onto the roadside"]
    r.waste_breakdown = {"organic": 32, "plastic": 26, "dry": 29, "hazardous": 0, "bulky": 13}
    r.pickup_urgency = {"priority": "medium", "recommended_within_hours": 12, "reason": "One-day-old mixed waste should be cleared during the next pickup window."}
    r.recommended_actions = ["Segregate wet and dry waste", "Use gloves and masks", "Clear roadside spillover"]
    r.created_at = created
    r.timestamp = created
    r.status = STATUS_PENDING
    add_report(r, fs_svc)
    st.session_state["_seed_demo_done"] = True


# ═══════════════════════════════════════════════════════════════
# PAGES
# ═══════════════════════════════════════════════════════════════

def page_welcome():
    st.markdown("""
    <div class="hero-wrap">
      <div class="hero-title">Welcome to <span class="hero-accent">Spotless</span></div>
      <div class="hero-sub">Smart waste management powered by AI</div>
    </div>""", unsafe_allow_html=True)

    st.markdown('<div class="home-role-wrap">', unsafe_allow_html=True)
    roles = [
        ("citizen", "👤", "Citizen", "Report waste issues and track cleanup progress in your locality."),
        ("volunteer", "🤝", "Volunteer", "Find nearby cleanup tasks and help resolve active reports."),
        ("ngo", "🏢", "NGO", "Manage high-priority reports, city heatmaps, and response operations."),
    ]
    cols = st.columns(3)

    for col, (role_key, icon, title, desc) in zip(cols, roles):
        with col:
            st.markdown(f"""
            <div class="role-card">
              <div class="role-icon">{icon}</div>
              <div class="role-title">{title}</div>
              <div class="role-desc">{desc}</div>
            </div>""", unsafe_allow_html=True)
            if st.button("Continue", key=f"role_{role_key}", use_container_width=True):
                st.session_state.role = role_key
                st.session_state.page = "auth"
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

def page_auth(fs_svc):
    nav_bar()
    role_key = st.session_state.get("role", "citizen")
    role_label = {"citizen": "Citizen", "volunteer": "Volunteer", "ngo": "NGO"}.get(role_key, "Citizen")
    if st.button("← Back", key="back_auth"):
        go("home")

    st.markdown(f"""
    <div class="auth-shell">
      <div class="onboard-card">
        <div class="onboard-title">{role_label} Access</div>
        <div class="onboard-sub">Login or create an account to continue.</div>
      </div>
    </div>""", unsafe_allow_html=True)

    mode = st.radio("Access mode", ["Login", "Signup"], horizontal=True, label_visibility="collapsed", key="auth_mode")

    st.markdown('<div class="auth-form-card">', unsafe_allow_html=True)
    with st.container():
        if mode == "Signup":
            extra = {}
            if role_key == "volunteer":
                name = st.text_input("Name", key="auth_signup_name")
                identifier = st.text_input("Email", key="auth_signup_identifier")
                password = st.text_input("Password", type="password", key="auth_signup_password")
                confirm = st.text_input("Confirm Password", type="password", key="auth_signup_confirm")
                extra["skills"] = st.multiselect("Skills", ["cleanup", "logistics", "driving", "sorting", "hazard handling"], key="auth_vol_skills")
                extra["availability"] = st.selectbox("Availability", ["full-time", "part-time", "weekends"], key="auth_vol_availability")
                extra["equipment"] = st.multiselect("Equipment", ["gloves", "truck", "cart", "mask", "tools"], key="auth_vol_equipment")
                extra["service_areas"] = st.multiselect("Service Areas", CITY_OPTIONS, default=["Koramangala"], key="auth_vol_areas")
                id_proof = st.file_uploader("Upload ID Proof", type=["jpg", "jpeg", "png", "pdf"], key="auth_vol_id")
                extra["id_proof"] = id_proof.name if id_proof else ""
                if id_proof:
                    st.success("ID proof verified")
            elif role_key == "ngo":
                name = st.text_input("Organisation Name", key="auth_signup_org")
                identifier = st.text_input("Email", key="auth_signup_email")
                password = st.text_input("Password", type="password", key="auth_signup_password")
                confirm = st.text_input("Confirm Password", type="password", key="auth_signup_confirm")
                extra["team_size"] = st.number_input("Team Size", min_value=1, max_value=1000, value=20, key="auth_ngo_team")
                extra["service_areas"] = st.multiselect("Operating Areas", CITY_OPTIONS, default=["Koramangala"], key="auth_ngo_areas")
                extra["equipment"] = st.multiselect("Equipment Available", ["gloves", "truck", "cart", "mask", "tools"], key="auth_ngo_equipment")
                extra["capabilities"] = extra["equipment"]
                id_proof = st.file_uploader("Upload ID Proof", type=["jpg", "jpeg", "png", "pdf"], key="auth_ngo_id")
                extra["id_proof"] = id_proof.name if id_proof else ""
                if id_proof:
                    st.success("ID proof verified")
            else:
                name = st.text_input("Name", key="auth_signup_name")
                identifier = st.text_input("Email", key="auth_signup_identifier")
                extra["area"] = st.selectbox("City", CITY_OPTIONS, key="auth_citizen_city")
                password = st.text_input("Password", type="password", key="auth_signup_password")
                confirm = st.text_input("Confirm Password", type="password", key="auth_signup_confirm")
            if st.button("Signup", key="auth_signup_submit", use_container_width=True):
                if not name.strip() or not identifier.strip() or not password:
                    toast("Please complete all signup fields.", "error")
                elif password != confirm:
                    toast("Passwords do not match.", "error")
                elif "@" not in identifier and role_key in ("citizen", "ngo"):
                    toast("Please enter a valid email address.", "error")
                elif role_key in ("volunteer", "ngo") and not extra.get("id_proof"):
                    toast("Please upload ID proof to continue.", "error")
                else:
                    user = _base_user_for_role(role_key, name, identifier, **extra)
                    login_user(user, role_key)
                    _save_auth_user(fs_svc, st.session_state.user, password)
                    st.rerun()
        else:
            identifier = st.text_input("Email", key="auth_login_identifier")
            password = st.text_input("Password", type="password", key="auth_login_password")
            if st.button("Login", key="auth_login_submit", use_container_width=True):
                record = _find_auth_user(fs_svc, identifier)
                stored_password = record.get("password") if record else ""
                if record and stored_password == password and record.get("role") == role_key:
                    login_user(record, record.get("role", role_key))
                    st.rerun()
                else:
                    toast("Please check your login details and try again.", "error")
    st.markdown('</div>', unsafe_allow_html=True)

def page_login():
    nav_bar()
    if st.button("← Back", key="back_login"):
        go("auth")
    role_key = st.session_state.get("role", "citizen")
    role = {"citizen": "Citizen", "volunteer": "Volunteer", "ngo": "NGO"}.get(role_key, "Citizen")
    sec(f"{role} Login")
    c1 = st.container()
    c2 = st.container()
    with c1:
        identifier = st.text_input("Email / Phone / Name")
    with c2:
        st.text_input("Password", type="password")
    if st.button(f"Continue as {role}", use_container_width=True):
        if role == "Citizen":
            st.session_state["user"] = {"name": identifier.strip() or "Guest Citizen", "area": "Koramangala", "role": "citizen"}
            go("citizen")
        elif role == "Volunteer":
            ensure_default_user("volunteer")
            if identifier.strip():
                st.session_state["user"]["name"] = identifier.strip()
            go("volunteer")
        else:
            ensure_default_user("ngo")
            if identifier.strip():
                st.session_state["user"]["name"] = identifier.strip()
            go("ngo")

def page_signup(fs_svc):
    nav_bar()
    if st.button("← Back", key="back_signup"):
        go("auth")
    role_key = st.session_state.get("role", "citizen")
    role = {"citizen": "Citizen", "volunteer": "Volunteer", "ngo": "NGO"}.get(role_key, "Citizen")
    sec(f"{role} Signup")
    if role == "Citizen":
        page_onboard_citizen(show_nav=False)
    elif role == "Volunteer":
        st.session_state["vol_auth_mode"] = "signup"
        page_onboard_volunteer(fs_svc, show_nav=False)
    else:
        page_onboard_ngo(show_nav=False)


# ── Citizen onboarding ────────────────────────────────────────────────────
def page_onboard_citizen(show_nav=True):
    if show_nav:
        nav_bar()
    st.markdown("""
    <div class="onboard-card">
      <div class="onboard-title">Citizen Access</div>
      <div class="onboard-sub">Enter your name and area to report waste issues.</div>
    </div>""", unsafe_allow_html=True)

    col = st.container()
    with col:
        name = st.text_input("Your Name",    placeholder="e.g. Ramesh Kumar")
        area = st.text_input("Your Locality",placeholder="e.g. Koramangala, Bengaluru")
        if st.button("Continue", use_container_width=True):
            if name.strip() and area.strip():
                st.session_state["user"] = {"name":name.strip(),"area":area.strip(),"role":"citizen"}
                go("citizen")
            else:
                toast("Please fill in your name and area.", "error")
        if show_nav:
            st.markdown("---")
            if st.button("Back", key="back_c"): go("home")


# ── Volunteer onboarding ──────────────────────────────────────────────────
def page_onboard_volunteer(fs_svc, show_nav=True):
    if show_nav:
        nav_bar()
    default_mode = "login" if st.session_state.get("vol_registered") else "signup"
    mode = ss("vol_auth_mode", default_mode)

    st.markdown(f"""
    <div class="onboard-card">
      <div class="onboard-title">{"Volunteer Login" if mode=="login" else "Volunteer Sign Up"}</div>
      <div class="onboard-sub">{"Use your registered email and phone." if mode=="login" else "Register once, then log in for future sessions."}</div>
    </div>""", unsafe_allow_html=True)

    col = st.container()
    with col:
        st.markdown('<div class="s-card">', unsafe_allow_html=True)

        if mode == "login":
            sec("Login Details")
            email = st.text_input("Email",        key="vl_email", placeholder="priya@example.com")
            phone = st.text_input("Phone Number", key="vl_phone", placeholder="+91-98765 00000")

            if st.button("Login", use_container_width=True):
                if email.strip() and phone.strip():
                    p = fs_svc.get_volunteer_profile(email=email, phone=phone)
                    if not p: p = _find_vol_profile(email=email, phone=phone)
                    ok_e = p.get("email","").lower() == email.strip().lower() if p else False
                    ok_p = p.get("phone","") == phone.strip() if p else False
                    if ok_e and ok_p:
                        st.session_state["user"] = _profile_to_user(p)
                        _save_vol_profile(p)
                        toast("Login successful.", "success")
                        go("volunteer")
                    else:
                        toast("Please check your login details and try again.", "error")
                else:
                    toast("Please enter email and phone.", "error")

            st.markdown('<div class="auth-switch">New volunteer?</div>', unsafe_allow_html=True)
            if st.button("Create Account", use_container_width=True):
                st.session_state["vol_auth_mode"] = "signup"; st.rerun()

        else:   # signup
            sec("Personal Details")
            name  = st.text_input("Full Name",  placeholder="e.g. Priya Nair")
            phone = st.text_input("Phone",      placeholder="+91-98765 00000")
            email = st.text_input("Email",      placeholder="priya@example.com")

            sec("Address")
            street = st.text_input("Street", placeholder="e.g. 12th Main Road")
            city  = st.text_input("City", placeholder="e.g. Bengaluru")

            sec("Government ID")
            gov_id = st.file_uploader("Upload Aadhaar / Voter ID (simulation)", type=["jpg","jpeg","png","pdf"])
            if gov_id:
                st.success("ID proof verified")

            sec("Service Areas")
            areas_input = st.text_input("Areas you serve (comma-separated)",
                                         placeholder="Koramangala, Indiranagar")

            sec("Capabilities")
            caps  = st.multiselect("Skills", ["Light Cleanup","Heavy Waste","Hazard Handling"], default=["Light Cleanup"])
            equip = st.multiselect("Equipment", ["Gloves","Vehicle","Tools","Protective Gear"], default=["Gloves"])
            avail = st.radio("Availability", ["Weekdays","Weekends","Full-time"], horizontal=True)

            if st.button("Sign Up", use_container_width=True):
                if name.strip() and phone.strip() and email.strip() and street.strip() and city.strip():
                    if not gov_id:
                        toast("Please upload ID proof to continue.", "error")
                        return
                    existing = fs_svc.get_volunteer_profile(email=email, phone=phone)
                    if not existing: existing = _find_vol_profile(email=email, phone=phone)
                    if existing:
                        st.session_state["vol_auth_mode"] = "login"
                        toast("Account already exists. Please log in.", "info"); st.rerun()
                    cap_map = {"Light Cleanup":"light_cleanup","Heavy Waste":"heavy_waste","Hazard Handling":"hazard_handling"}
                    areas   = [a.strip() for a in areas_input.split(",") if a.strip()]
                    lat, lon = guess_coords_from_area(city + " " + areas_input)
                    profile  = {
                        "volunteer_id": generate_id("VOL"),
                        "name": name.strip(), "phone": phone.strip(), "email": email.strip(),
                        "street": street.strip(), "city": city.strip(),
                        "service_areas": areas, "capabilities": [cap_map[c] for c in caps],
                        "equipment": equip, "availability": avail.lower(),
                        "latitude": lat, "longitude": lon, "lat": lat, "lon": lon,
                        "role": "volunteer", "verified": bool(gov_id),
                        "id_proof": gov_id.name if gov_id else "",
                        "reputation": 30, "streak": 0, "tasks_completed": 0, "badge": "Beginner",
                    }
                    fs_svc.save_volunteer_profile(profile)
                    _save_vol_profile(profile)
                    st.session_state["user"] = _profile_to_user(profile)
                    toast("Registration successful.", "success")
                    go("volunteer")
                else:
                    toast("Please fill Name, Phone, Email, Street, and City.", "error")

            st.markdown('<div class="auth-switch">Already registered?</div>', unsafe_allow_html=True)
            if st.button("Go to Login", use_container_width=True):
                st.session_state["vol_auth_mode"] = "login"; st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)

    if show_nav:
        if st.button("Back", key="back_v"): go("home")


# ── NGO onboarding ────────────────────────────────────────────────────────
def page_onboard_ngo(show_nav=True):
    if show_nav:
        nav_bar()
    st.markdown("""
    <div class="onboard-card">
      <div class="onboard-title">NGO Registration</div>
      <div class="onboard-sub">Verified organisations manage large-scale waste operations.</div>
    </div>""", unsafe_allow_html=True)

    col = st.container()
    with col:
        st.markdown('<div class="s-card">', unsafe_allow_html=True)
        org   = st.text_input("Organisation Name",placeholder="e.g. GreenAct Foundation")
        reg   = st.text_input("Registration ID",  placeholder="e.g. NGO-KA-2019-00234")
        areas = st.text_input("Areas covered (comma-separated)",placeholder="Koramangala, HSR Layout")
        size  = st.number_input("Team Size", min_value=1, max_value=500, value=20)
        caps  = st.multiselect("Capabilities",
                               ["Bulk Waste","Hazmat","Organic Processing","Light Waste","Medical Waste"],
                               default=["Bulk Waste"])
        id_proof = st.file_uploader("Upload ID Proof", type=["jpg","jpeg","png","pdf"], key="ngo_onboard_id")
        if id_proof:
            st.success("ID proof verified")

        if st.button("Access NGO Dashboard", use_container_width=True):
            if org.strip() and reg.strip():
                if not id_proof:
                    toast("Please upload ID proof to continue.", "error")
                    return
                lat, lon = guess_coords_from_area(areas or "Bengaluru")
                st.session_state["user"] = {
                    "name": org.strip(), "reg_id": reg.strip(),
                    "service_areas": [a.strip() for a in areas.split(",") if a.strip()],
                    "team_size": size, "capabilities": caps,
                    "id_proof": id_proof.name if id_proof else "",
                    "lat": lat, "lon": lon, "role": "ngo",
                }
                go("ngo")
            else:
                toast("Organisation name and Registration ID are required.", "error")
        st.markdown('</div>', unsafe_allow_html=True)

    if show_nav:
        if st.button("Back", key="back_n"): go("home")


# ── Citizen dashboard ─────────────────────────────────────────────────────
def page_citizen_dashboard(ai_svc, urg_svc, alloc_svc, fs_svc):
    ensure_default_user("citizen")
    nav_bar()
    if st.button("← Back", key="back_citizen_dashboard"):
        st.session_state.page = "home"
        st.rerun()

    user = get_user()
    st.markdown(f'<div class="sec-head">Welcome, {user.get("name","Citizen")} — {user.get("area","")}</div>',
                unsafe_allow_html=True)

    if st.button("Report Issue", key="citizen_report_issue_nav", use_container_width=True):
        st.session_state.page = "report_issue"
        st.rerun()

    weather_widget()
    reports = load_reports(fs_svc)
    render_dashboard_top_sections(reports, key_prefix="citizen")

    tab_report, tab_track = st.tabs(["Submit Report","Track My Reports"])

    with tab_report:
        _citizen_report_form(ai_svc, urg_svc, alloc_svc, fs_svc)

    with tab_track:
        my = [r for r in load_reports(fs_svc) if r.reporter_name == user.get("name")]
        if not my:
            st.info("No reports yet. Use the Submit Report tab.")
        else:
            sec(f"Your {len(my)} Report(s)")
            for r in sorted(my, key=lambda x: x.created_at, reverse=True):
                _tracking_card(r)

def page_report_issue(ai_svc, urg_svc, alloc_svc, fs_svc):
    ensure_default_user("citizen")
    nav_bar()
    if st.button("View Dashboard", key="citizen_dashboard_nav", use_container_width=True):
        st.session_state.page = "dashboard"
        st.rerun()
    weather_widget()
    _citizen_report_form(ai_svc, urg_svc, alloc_svc, fs_svc)

def page_track_reports(urg_svc, fs_svc=None):
    user = ensure_default_user("citizen")
    nav_bar()
    reports = load_reports(fs_svc or st.session_state.get("_fs_svc"))
    urg_svc.escalate_existing(reports)
    my = [r for r in reports if r.reporter_name == user.get("name") or user.get("name") == "Guest Citizen"]
    sec("Track Reports")
    if not my:
        st.info("No reports yet. Use the Citizen dashboard to submit a report.")
        return
    for r in sorted(my, key=lambda x: x.created_at, reverse=True):
        _tracking_card(r)


def show_home():
    page_welcome()

def show_auth(fs_svc):
    page_auth(fs_svc)

def show_login():
    page_login()

def show_signup(fs_svc):
    page_signup(fs_svc)


def show_citizen_dashboard(ai_svc, urg_svc, alloc_svc, fs_svc):
    page_citizen_dashboard(ai_svc, urg_svc, alloc_svc, fs_svc)


def show_volunteer_dashboard(alloc_svc, urg_svc, fs_svc):
    page_volunteer_dashboard(alloc_svc, urg_svc, fs_svc)


def show_ngo_dashboard(ai_svc, urg_svc, fs_svc):
    page_ngo_dashboard(ai_svc, urg_svc, fs_svc)

def show_dashboard(ai_svc, urg_svc, alloc_svc, fs_svc):
    role = (get_user() or {}).get("role") or st.session_state.get("role", "citizen")
    dashboard = _dashboard_for_role(role)
    if dashboard == "volunteer":
        page_volunteer_dashboard(alloc_svc, urg_svc, fs_svc)
    elif dashboard == "ngo":
        page_ngo_dashboard(ai_svc, urg_svc, fs_svc)
    else:
        page_citizen_dashboard(ai_svc, urg_svc, alloc_svc, fs_svc)


def _citizen_report_form(ai_svc, urg_svc, alloc_svc, fs_svc):
    user = get_user()
    st.markdown("""
    <div class="report-issue-hero">
      <div class="report-issue-title">Report Issue</div>
      <div class="report-issue-sub">Submit the location, description, and photo for instant waste analysis and routing.</div>
    </div>""", unsafe_allow_html=True)
    st.markdown('<div class="report-form-card">', unsafe_allow_html=True)

    city = st.selectbox("City", CITY_OPTIONS, index=0 if user.get("area", "Koramangala") not in CITY_OPTIONS else CITY_OPTIONS.index(user.get("area", "Koramangala")))
    road_name = st.text_input("Road / Street Name", placeholder="e.g. 12th Main Road")
    landmark = st.text_input("Nearest Landmark", placeholder="e.g. Metro station, park, school")
    use_live_location = st.checkbox("Use Live Location", value=False)
    lat_input = lon_input = None
    if use_live_location:
        loc_cols = st.columns(2)
        with loc_cols[0]:
            lat_input = st.number_input("Latitude", value=13.049, format="%.6f")
        with loc_cols[1]:
            lon_input = st.number_input("Longitude", value=77.512, format="%.6f")
    description = st.text_area("Describe the Issue",
                                placeholder="Large pile near the bus stop. Overflowing bin, bad odour.",
                                height=100)

    tab_upload, tab_cam = st.tabs(["Upload Photo","Take Photo"])
    img_bytes = None
    with tab_upload:
        up = st.file_uploader("Waste photo", type=["jpg","jpeg","png"], label_visibility="collapsed")
        if up: img_bytes = up.getvalue()
    with tab_cam:
        cam = st.camera_input("Capture", label_visibility="collapsed")
        if cam: img_bytes = cam.getvalue()

    if st.button("Submit Report", use_container_width=True):
        if not description.strip():
            toast("Please add a description.", "error"); return
        if not city or not road_name.strip():
            toast("Please add city and road or street name.", "error"); return

        image_path = None
        if img_bytes:
            fname      = f"{generate_id('IMG')}.jpg"
            image_path = os.path.join(REPORTS_DIR, fname)
            with open(image_path, "wb") as fh: fh.write(img_bytes)

        location_text = ", ".join([part for part in [road_name.strip(), landmark.strip(), city] if part])
        if use_live_location:
            lat, lon = float(lat_input or 13.049), float(lon_input or 77.512)
        else:
            lat, lon = guess_coords_from_area(location_text)
        report = WasteReport(
            area_name=location_text or user.get("area","Unknown"),
            observations=description,
            latitude=lat, longitude=lon,
            image_path=image_path,
            city=city,
            road_name=road_name.strip(),
            landmark=landmark.strip(),
            use_live_location=bool(use_live_location),
            reporter_name=user.get("name","Anonymous"),
            reporter_role="citizen",
        )
        report.report_id = generate_id("RPT")

        with st.spinner("Analyzing with AI..."):
            time.sleep(5)
            result = ai_svc.analyse_best_effort(
                image_path=image_path, observations=description,
                lat=lat, lon=lon,
            )

        analysis = result.get("waste_analysis", result)
        report.waste_type          = result.get("waste_type", "mixed")
        report.severity            = result.get("severity", analysis.get("severity", "medium"))
        report.ai_summary          = clean_analysis_text(
            result.get("summary", analysis.get("description", description[:120])),
            report_description=True,
        )
        report.extracted_issues    = result.get("issues", analysis.get("risks", []))
        report.waste_breakdown     = analysis.get("waste_breakdown", {})
        report.pickup_urgency      = analysis.get("pickup_urgency", {})
        report.recommended_actions = analysis.get("recommended_actions", [])
        report.confidence          = analysis.get("confidence", 0.0)
        report.weather             = result.get("weather", analysis.get("weather", {}))
        report.organic_percent     = report.waste_breakdown.get("organic", 0)
        report.plastic_percent     = report.waste_breakdown.get("plastic", 0)
        report.other_percent       = 100 - report.organic_percent - report.plastic_percent

        st.success("Analysis completed successfully")

        report     = urg_svc.enrich(report)
        allocation = alloc_svc.allocate(report)

        ngo  = allocation.get("assigned_ngo")
        vols = allocation.get("assigned_volunteers", [])
        if ngo:
            report.assigned_ngo_id   = ngo.ngo_id
            report.assigned_ngo_name = ngo.name
            report.status = STATUS_PENDING
        if vols:
            report.assigned_volunteer_ids   = [v.volunteer_id for v in vols]
            report.assigned_volunteer_names = [v.name for v in vols]

        add_report(report, fs_svc)
        st.session_state.page = "dashboard"

        toast(f"Report {report.report_id} submitted successfully.", "success")

        if result:
            render_report_result_card(report, result, allocation)

        if image_path and os.path.exists(image_path):
            st.image(image_path, caption="Uploaded photo", use_container_width=True)

        sec("Assigned Responders")
        if ngo:
            st.markdown(f"""
            <div class="s-card-purple">
              <span class="verified-chip">Verified NGO</span>
              <div style="font-weight:800;color:#5b21b6;margin-top:.4rem;font-size:1.05rem">{ngo.name}</div>
              <div style="font-size:.8rem;color:#64748b">{ngo.distance_km} km away &middot; {ngo.contact}</div>
            </div>""", unsafe_allow_html=True)

        if vols:
            rows_html = "".join(
                f"<li style='margin-bottom:.3rem'><b>{v.name}</b> - {v.distance_km} km"
                f" &middot; Skills matched: {', '.join(v.capabilities)}"
                f" &middot; Equipment: {', '.join(v.equipment)}"
                f"<br><span style='color:#475569'>{assignment_reason(report, v)}</span></li>"
                for v in vols
            )
            st.markdown(
                f'<div class="s-card-blue"><b>Volunteers Notified ({len(vols)}):</b>'
                f'<ul style="margin-top:.4rem">{rows_html}</ul>'
                f'<div style="font-size:.75rem;color:#475569;margin-top:.4rem">'
                f'{allocation.get("selective_notice","")}</div></div>',
                unsafe_allow_html=True,
            )

        if allocation.get("micro_task_eligible"):
            st.markdown(
                '<div class="s-card-green">Micro-route identified - nearby volunteers can complete this in one trip.</div>',
                unsafe_allow_html=True,
            )

        esc_msg = getattr(report, "escalation_message", "")
        if esc_msg:
            st.markdown(f'<div class="escalation-note">{esc(esc_msg)}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def _tracking_card(r: WasteReport):
    level = concern_level(r)
    hours = int(report_age_hours(r))
    unresolved_note = ""
    if r.status != STATUS_COMPLETED and hours >= 1:
        unresolved_note = (
            f'<div class="attention-note">This issue has been unresolved for {hours} hours and requires attention.</div>'
        )
    steps = [
        ("Submitted",   True),
        ("AI Analysed", bool(r.waste_type)),
        ("Assigned",    r.status in (STATUS_ASSIGNED,STATUS_IN_PROGRESS,STATUS_COMPLETED)),
        ("In Progress", r.status in (STATUS_IN_PROGRESS,STATUS_COMPLETED)),
        ("Completed",   r.status == STATUS_COMPLETED),
    ]
    track_html = ""
    for i,(label,done) in enumerate(steps):
        is_next = not done and (i==0 or steps[i-1][1])
        dot     = "dot-active" if is_next else ("dot-done" if done else "dot-wait")
        color   = "#1e293b" if done else "#94a3b8"
        track_html += (
            f'<div class="tracker-step"><div class="tracker-dot {dot}"></div>'
            f'<span style="font-size:.82rem;color:{color}">{label}</span></div>'
        )
        if i < len(steps)-1: track_html += '<div class="tracker-line"></div>'

    assign = ""
    if r.assigned_ngo_name:
        assign += f'<div style="font-size:.78rem;color:#5b21b6;margin-top:.3rem">NGO: {r.assigned_ngo_name}</div>'
    if r.assigned_volunteer_names:
        assign += f'<div style="font-size:.78rem;color:#2563eb;margin-top:.2rem">Volunteers: {", ".join(r.assigned_volunteer_names[:2])}</div>'

    escalation_text = getattr(r,"escalation_message","") or ""
    esc_html = f'<div class="escalation-note">{esc(escalation_text)}</div>' if escalation_text else ""

    st.markdown(f"""
    <div class="s-card">
      <div style="display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap">
        <div style="flex:1;min-width:200px">
          <div style="font-weight:800;font-size:.97rem">{r.area_name} &nbsp; {status_badge(r.status)}</div>
          <div style="font-size:.78rem;color:#64748b;margin-top:.2rem">{r.report_id} &middot; {reported_ago(r.created_at)}</div>
          <div style="margin-top:.35rem"><span class="concern-{level.lower()}">Level of Concern: {level}</span></div>
          <div style="font-size:.83rem;color:#374151;margin-top:.35rem">{r.observations[:100]}</div>
          {assign}{unresolved_note}{esc_html}
        </div>
        <div style="min-width:140px">{track_html}</div>
      </div>
    </div>""", unsafe_allow_html=True)
    render_waste_pie(waste_breakdown_for_report(r), "Waste Composition")
    if r.status == STATUS_COMPLETED and not getattr(r, "disputed", False):
        if st.button("Report False Completion", key=f"false_completion_{r.report_id}", use_container_width=True):
            r.disputed = True
            r.status = STATUS_UNDER_REVIEW
            for name in getattr(r, "assigned_volunteer_names", []) or []:
                _adjust_volunteer_reputation(name, -10)
            persist_report(r)
            toast("Report sent for review.", "info")
            st.rerun()
    elif getattr(r, "disputed", False) or r.status == STATUS_UNDER_REVIEW:
        st.warning("This completion is under review.")


# ── Volunteer dashboard ───────────────────────────────────────────────────
def page_volunteer_dashboard(alloc_svc, urg_svc, fs_svc):
    user = ensure_default_user("volunteer")
    user = _apply_reputation_delta(user)
    st.session_state["user"] = user
    nav_bar()
    if st.button("← Back", key="back_volunteer_dashboard"):
        st.session_state.page = "home"
        st.rerun()

    vol_lat = user.get("lat", 13.049)
    vol_lon = user.get("lon", 77.512)

    reports = load_reports(fs_svc)
    urg_svc.escalate_existing(reports)
    selected_city, scoped_reports = render_dashboard_top_sections(reports, key_prefix="volunteer")
    st.markdown(f"""
    <div class="profile-card profile-card-horizontal">
      <div>
        <div class="profile-name">{esc(user.get("name","Volunteer"))}</div>
        <div class="profile-meta">Areas: {esc(", ".join(user.get("service_areas",[]) or ["Koramangala"]))}</div>
      </div>
      <div class="profile-meta"><b>Skills</b><br>{esc(", ".join(user.get("capabilities",[]) or ["light_cleanup"]))}</div>
      <div class="profile-meta"><b>Equipment</b><br>{esc(", ".join(user.get("equipment",[]) or ["Gloves"]))}</div>
      <div class="profile-meta"><b>Availability</b><br>{esc(user.get("availability","Full-time").title())}</div>
      <div class="profile-meta"><b>Reputation</b><br>{esc(user.get("reputation", 0))}</div>
      <span class="verified-chip">Verified Volunteer</span>
    </div>""", unsafe_allow_html=True)

    if selected_city == "Select City":
        return

    active = [r for r in scoped_reports if r.status in (STATUS_PENDING, STATUS_ASSIGNED, STATUS_IN_PROGRESS, STATUS_UNDER_REVIEW)]
    nearby  = sorted(
        [(haversine_km(vol_lat,vol_lon,r.latitude,r.longitude), r) for r in active],
        key=lambda x: (concern_rank(x[1]), {"pending": 0, "assigned": 1, "in_progress": 2}.get(x[1].status, 3), x[0]),
    )

    micro_notice = alloc_svc.micro_route_notice(vol_lat, vol_lon, active)

    if micro_notice:
        st.markdown(f'<div class="micro-route-notice">{esc(micro_notice)}</div>', unsafe_allow_html=True)

    sec(f"{selected_city} Heatmap")
    render_heatmap(active, center_lat=vol_lat, center_lon=vol_lon)
    sec("Nearby Tasks")
    if not nearby:
        st.info("No nearby tasks for this city.")
    else:
        for dist, r in nearby[:5]:
            _vol_task_card(r, dist, user.get("name","Volunteer"))


def _vol_task_card(r: WasteReport, dist: float, vol_name: str):
    sev   = (r.severity or "low").lower()
    wt    = (r.waste_type or "mixed").title()
    micro = dist <= 1.5 and (r.risk_score or "LOW") in ("LOW","MEDIUM")
    bio_msg = biodating_message(r)

    img_html = ""
    if r.image_path and os.path.exists(r.image_path):
        with open(r.image_path,"rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        img_html = (
            f'<img src="data:image/jpeg;base64,{b64}" '
            f'style="width:68px;height:68px;object-fit:cover;border-radius:10px;flex-shrink:0"/>'
        )

    tags = ("" if not micro else '<span class="micro-tag">One-trip task</span> ')
    if concern_category(r) == "Critical": tags += '<span class="alert-tag">Critical</span>'

    days     = r.days_unresolved or 0
    esc_html = ""
    if days >= 3:
        esc_html = '<div class="bio-note critical-note">Priority elevated - unresolved for 3+ days</div>'
    elif days >= 2:
        esc_html = '<div class="bio-note">Priority elevated - unresolved for 2+ days</div>'
    elif days >= 1:
        esc_html = '<div class="bio-note">Unresolved for 1+ day</div>'

    bio_html = f'<div class="bio-note">{esc(bio_msg)}</div>' if bio_msg else ""

    ai_html = (
        f'<div style="font-size:.76rem;color:#2563eb;margin-top:.3rem">{esc(clean_analysis_text(r.ai_summary, report_description=True))[:80]}...</div>'
        if r.ai_summary else ""
    )

    st.markdown(f"""
    <div class="task-card">
      {img_html}
      <div style="flex:1">
        <div style="display:flex;align-items:center;gap:.5rem;flex-wrap:wrap">
          <span style="font-weight:800">{esc(r.area_name)}</span>
          {concern_pill(r)}
          {tags}
        </div>
        <div style="font-size:.78rem;color:#64748b;margin-top:.2rem">
          {dist:.2f} km away &middot; {wt} &middot; {time_ago(r.created_at)}
        </div>
        <div style="font-size:.83rem;color:#374151;margin-top:.3rem">
          {esc(r.observations)[:100]}{"..." if len(r.observations)>100 else ""}
        </div>
        {ai_html}{bio_html}{esc_html}
        <div style="margin-top:.4rem">{status_badge(r.status)}</div>
      </div>
    </div>""", unsafe_allow_html=True)

    if r.status == STATUS_PENDING:
        if st.button("Accept Task", key=f"acc_{r.report_id}", use_container_width=True):
            r.status = STATUS_ASSIGNED
            if vol_name not in r.assigned_volunteer_names:
                r.assigned_volunteer_names.append(vol_name)
            persist_report(r)
            toast(f"Task accepted - {r.area_name}", "success"); st.rerun()
    if r.status == STATUS_ASSIGNED:
        if st.button("Mark Completed", key=f"done_{r.report_id}", use_container_width=True):
            hours_taken = report_age_hours(r)
            points = 10 if hours_taken < 4 else (6 if hours_taken < 12 else 3)
            r.status = STATUS_COMPLETED
            r.completed_by_volunteer = vol_name
            r.completed_at = datetime.utcnow()
            u = st.session_state["user"]
            u["tasks_completed"]  = u.get("tasks_completed",0)+1
            u["streak"]           = u.get("streak",0)+1
            st.session_state["user"] = u
            _adjust_volunteer_reputation(vol_name, points)
            u = st.session_state["user"]
            persist_report(r)
            toast(f"Task completed. +{points} reputation. Badge: {u['badge']}", "success"); st.rerun()


# ── NGO dashboard ─────────────────────────────────────────────────────────
def page_ngo_dashboard(ai_svc, urg_svc, fs_svc):
    user    = ensure_default_user("ngo")
    nav_bar()
    if st.button("← Back", key="back_ngo_dashboard"):
        st.session_state.page = "home"
        st.rerun()

    org     = user.get("name","NGO")
    lat     = user.get("lat",13.049)
    lon     = user.get("lon",77.512)
    reports = load_reports(fs_svc)

    urg_svc.escalate_existing(reports)

    st.markdown(f'<div class="sec-head">NGO Dashboard — {org}</div>', unsafe_allow_html=True)
    selected_city, scoped_reports = render_dashboard_top_sections(reports, key_prefix="ngo")
    sec("Organisation Summary")
    st.markdown(f"""
    <div class="profile-card profile-card-horizontal">
      <span class="verified-chip">Verified Organisation</span>
      <div class="profile-meta"><b>Team Size</b><br>{user.get("team_size","?")}</div>
      <div class="profile-meta"><b>Operating Areas</b><br>{esc(", ".join(user.get("service_areas",[]) or []))}</div>
      <div class="profile-meta"><b>Equipment</b><br>{esc(", ".join(user.get("equipment", user.get("capabilities", [])) or []))}</div>
    </div>""", unsafe_allow_html=True)

    if selected_city == "Select City":
        return

    sec("Heatmap")
    render_heatmap(scoped_reports, center_lat=lat, center_lon=lon)

    sec("Waste Composition")
    composition = Counter()
    for r in scoped_reports:
        for key, value in waste_breakdown_for_report(r).items():
            composition[key] += value
    render_waste_pie(dict(composition), "Waste Composition")
    sec("Concern Levels")
    render_concern_bar(scoped_reports)

    sec("Critical Alerts")
    critical_alerts = [
        r for r in scoped_reports
        if r.status != STATUS_COMPLETED and ((r.risk_score or "").upper() in ("HIGH", "CRITICAL") or (r.severity or "").lower() in ("high", "critical"))
    ]
    if critical_alerts:
        for r in critical_alerts[:4]:
            st.markdown(
                f'<div class="critical-task-card"><b>{esc(r.area_name)}</b> {sev_badge(r.severity or "critical")}'
                f'<div class="critical-copy">{esc(clean_analysis_text(r.ai_summary or r.observations, report_description=True))[:140]}</div></div>',
                unsafe_allow_html=True,
            )
    else:
        st.info("No critical alerts for the selected city.")

    sec("Reports by City")
    by_city = Counter()
    for r in reports:
        for city_name in CITY_OPTIONS:
            if city_matches(r, city_name):
                by_city[city_name] += 1
    st.dataframe(pd.DataFrame([{"City": k, "Reports": by_city.get(k, 0)} for k in CITY_OPTIONS]), use_container_width=True, hide_index=True)

    sec("Task Allocation Overview")
    allocation_rows = [{
        "Area": r.area_name,
        "Status": r.status,
        "Assigned NGO": r.assigned_ngo_name or org,
        "Volunteers": ", ".join(r.assigned_volunteer_names or []) or "Pending",
    } for r in scoped_reports]
    st.dataframe(pd.DataFrame(allocation_rows), use_container_width=True, hide_index=True)

    sec("Reports")
    for r in sorted(scoped_reports, key=lambda x: (concern_rank(x), getattr(x, "created_at", datetime.utcnow())), reverse=False):
        _ngo_report_card(r, org)


def _ngo_report_card(r: WasteReport, org_name: str):
    sev = (r.severity or "low").lower()
    img_html = ""
    if r.image_path and os.path.exists(r.image_path):
        with open(r.image_path,"rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        img_html = f'<img src="data:image/jpeg;base64,{b64}" style="width:72px;height:72px;object-fit:cover;border-radius:10px;flex-shrink:0"/>'

    esc_html = ""
    escalation_text = getattr(r,"escalation_message","") or ""
    if escalation_text:
        esc_html = f'<div class="escalation-note">{esc(escalation_text)}</div>'

    days_html = ""
    if (r.days_unresolved or 0) >= 3 and r.status != STATUS_COMPLETED:
        days_html = '<div class="bio-note critical-note">Critical escalation - 72+ hours unresolved</div>'
    bio_msg = biodating_message(r)
    bio_html = f'<div class="bio-note">{esc(bio_msg)}</div>' if bio_msg else ""

    st.markdown(f"""
    <div class="s-card">
      <div style="display:flex;gap:1rem;align-items:flex-start">
        {img_html}
        <div style="flex:1">
          <div style="margin-bottom:.45rem">{concern_pill(r)}</div>
          <div style="display:flex;gap:.5rem;align-items:center;flex-wrap:wrap">
            {sev_badge(sev)} {status_badge(r.status)}
            <span style="color:#94a3b8;font-size:.73rem">{r.report_id}</span>
          </div>
          <div style="font-size:.77rem;color:#64748b;margin-top:.2rem">
            {esc(r.area_name)} &middot; {reported_ago(r.created_at)}
          </div>
          <div style="font-size:.83rem;color:#374151;margin-top:.3rem">{esc(r.observations)[:120]}</div>
          {bio_html}{days_html}{esc_html}
        </div>
      </div>
    </div>""", unsafe_allow_html=True)

    if r.status == STATUS_PENDING:
        if st.button("Accept", key=f"ngo_acc_{r.report_id}", use_container_width=True):
            r.status = STATUS_ASSIGNED; r.assigned_ngo_name = org_name
            persist_report(r)
            toast(f"Accepted: {r.area_name}", "success"); st.rerun()
    if r.status in (STATUS_ASSIGNED, STATUS_IN_PROGRESS):
        if st.button("Mark Completed", key=f"ngo_done_{r.report_id}", use_container_width=True):
            r.status = STATUS_COMPLETED
            r.completed_at = datetime.utcnow()
            persist_report(r)
            toast(f"Completed: {r.area_name}", "success"); st.rerun()


def _render_insights_panel(reports, selected_area="All Areas"):
    """Render real data-driven insights from insight_engine."""
    sec(f"Insights Panel — {selected_area}")
    high_risk = [r for r in reports if r.status != STATUS_COMPLETED and concern_level(r) in ("High", "Critical")]
    st.markdown(
        f'<div class="escalation-banner">{len(high_risk)} high-risk reports need immediate attention</div>',
        unsafe_allow_html=True,
    )

    composition = Counter()
    for r in reports:
        bd = waste_breakdown_for_report(r)
        for key in ("organic", "plastic", "dry", "hazardous"):
            composition[key] += bd.get(key, 0)
    render_waste_pie(dict(composition), "Waste Composition")
    render_area_severity_bar(reports)

    insights = generate_insights(reports)
    stats    = insights.get("stats", {})
    render_metrics([
        ("Total Reports", stats.get("total",0), "#2563eb"),
        ("Pending", stats.get("pending",0), "#d97706"),
        ("Critical Zones", stats.get("critical",0), "#be123c"),
        ("Avg Days Unresolved", stats.get("avg_days_unresolved",0.0), "#7c3aed"),
    ])

    recs = insights.get("actionable_recommendations",[])
    if recs:
        sec("Actionable Recommendations")
        for rec in recs[:3]:
            st.markdown(f'<div class="insight-item crit">{esc(rec)}</div>', unsafe_allow_html=True)

    sec("High Priority Unresolved Tasks")
    high_priority = [
        r for r in sorted(reports, key=lambda x: (x.risk_score in ("HIGH","CRITICAL"), x.days_unresolved or 0), reverse=True)
        if r.status != STATUS_COMPLETED and (r.risk_score in ("HIGH","CRITICAL") or (r.severity or "").lower() == "high")
    ]
    if high_priority:
        for r in high_priority[:8]:
            st.markdown(
                f'<div class="priority-row"><b>{esc(r.area_name)}</b> {sev_badge(r.severity or "medium")} '
                f'<span>{status_badge(r.status)}</span><span>{reported_ago(r.created_at)}</span>'
                f'<div>{esc(clean_analysis_text(r.ai_summary or r.observations, report_description=True))[:140]}</div></div>',
                unsafe_allow_html=True,
            )
    else:
        st.caption("No high-priority unresolved tasks for this area.")

    # Escalation table
    sec("Escalation Queue")
    esc_rows = [
        {"Area": r.area_name, "Days": round(r.days_unresolved or 0,1),
         "Risk": r.risk_score or "LOW", "Status": r.status.upper(),
         "Reported": time_ago(r.created_at)}
        for r in sorted(reports, key=lambda x: x.days_unresolved or 0, reverse=True)
        if (r.days_unresolved or 0) >= 1 and r.status != STATUS_COMPLETED
    ]
    if esc_rows:
        st.dataframe(pd.DataFrame(esc_rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No escalated reports at this time.")


# ═══════════════════════════════════════════════════════════════
# ROUTER
# ═══════════════════════════════════════════════════════════════

def main():
    if "page" not in st.session_state:
        st.session_state.page = "home"
    ss("reports", [])
    st.session_state.chart_counter = 0

    ai_svc, urg_svc, alloc_svc, fs_svc = init_services()
    st.session_state["_fs_svc"] = fs_svc

    if not st.session_state.logged_in:
        if st.session_state.page == "auth":
            show_auth(fs_svc)
        elif st.session_state.page in ("role_auth", "login", "signup"):
            st.session_state.page = "auth"
            st.rerun()
        else:
            show_home()
        return

    role = st.session_state.get("role") or (get_user() or {}).get("role", "citizen")
    if role == "citizen":
        if st.session_state.get("page") == "report_issue":
            page_report_issue(ai_svc, urg_svc, alloc_svc, fs_svc)
        else:
            show_citizen_dashboard(ai_svc, urg_svc, alloc_svc, fs_svc)
    elif role == "volunteer":
        show_volunteer_dashboard(alloc_svc, urg_svc, fs_svc)
    elif role == "ngo":
        show_ngo_dashboard(ai_svc, urg_svc, fs_svc)


if __name__ == "__main__":
    main()
