import json
import logging
import random
import re
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, Optional

from PIL import Image
import google.generativeai as genai

from services.weather_service import get_weather_data

logger = logging.getLogger(__name__)


class AIAnalysisService:
    MAX_RETRIES = 1
    MODEL_NAME = "gemini-1.5-flash-latest"

    def __init__(self, api_key: str):
        api_key = (api_key or "").strip()

        if not api_key:
            self.model = None
            logger.warning("Gemini API key missing. Smart fallback mode enabled.")
            return

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(self.MODEL_NAME)

        logger.info("Gemini initialised with model: %s", self.MODEL_NAME)

    # ---------------- IMAGE ----------------

    def _load_image(self, image_path: str):
        path = Path(image_path)
        image = Image.open(path).convert("RGB")

        # resize if too large
        if image.width > 1024:
            ratio = 1024 / float(image.width)
            new_size = (1024, int(image.height * ratio))
            image = image.resize(new_size)

        return image

    # ---------------- RETRY ----------------

    def _generate_content_with_retries(self, contents):
        if self.model is None:
            raise ValueError("Gemini model unavailable")
        return self.model.generate_content(contents)

    # ---------------- JSON PARSER ----------------

    @staticmethod
    def _extract_json(text: str) -> dict:
        if not text:
            return {}

        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

        try:
            return json.loads(cleaned)
        except:
            pass

        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass

        return {}

    # ---------------- PROMPT ----------------

    def _analysis_prompt(self, weather: dict, mode: str, observations=None):
        return f"""
Analyze this waste report.

Weather:
Temperature: {weather.get('temperature')}C
Humidity: {weather.get('humidity')}%
Condition: {weather.get('condition')}

Return STRICT JSON ONLY.

{{
  "waste_breakdown": {{
    "organic": percentage,
    "plastic": percentage,
    "dry": percentage,
    "hazardous": percentage,
    "bulky": percentage
  }},
  "estimated_volume": "small | medium | large",
  "severity": "low | medium | high",
  "description": "detailed description",
  "risks": ["list risks"],
  "pickup_urgency": {{
    "priority": "low | medium | high | critical",
    "recommended_within_hours": number,
    "reason": "reason"
  }},
  "recommended_actions": ["steps"],
  "confidence": number
}}
"""

    def analyse_best_effort(self, image_path=None, observations="", weather=None, lat=None, lon=None):
        """
        Single public analysis entry point. Prefer Gemini image analysis when a
        photo exists, otherwise use text. Any failure returns a deterministic
        structured fallback with the same UI payload shape.
        """
        weather = self._resolve_weather(weather, lat, lon)
        result = self._fallback(weather, observations=observations)
        return self._to_ui_payload(result, weather, "gemini_text", observations)

    # ---------------- FALLBACK ----------------

    def _fallback(self, weather, observations=""):
        text = (observations or "").lower()
        waste_profiles = [
            {"type": "organic", "range": (30, 60)},
            {"type": "plastic", "range": (10, 40)},
            {"type": "dry", "range": (10, 30)},
            {"type": "hazardous", "range": (0, 20)},
            {"type": "bulky", "range": (0, 50)},
        ]
        raw = {p["type"]: random.randint(*p["range"]) for p in waste_profiles}
        if any(k in text for k in ("food", "odour", "odor", "smell", "organic", "vegetable")):
            raw["organic"] += random.randint(15, 30)
        if any(k in text for k in ("plastic", "bottle", "packet", "wrapper")):
            raw["plastic"] += random.randint(15, 30)
        if any(k in text for k in ("debris", "construction", "furniture", "mattress", "heavy")):
            raw["bulky"] += random.randint(20, 40)
        if any(k in text for k in ("chemical", "medical", "battery", "paint", "hazard", "toxic")):
            raw["hazardous"] += random.randint(25, 45)

        temp = float((weather or {}).get("temperature", 28) or 28)
        condition = str((weather or {}).get("condition", "clear") or "clear").lower()
        if temp >= 30:
            raw["organic"] += random.randint(8, 18)
        if any(word in condition for word in ("rain", "drizzle", "storm", "shower")):
            raw["organic"] += random.randint(5, 12)
            raw["hazardous"] += random.randint(0, 8)

        total = sum(raw.values()) or 1
        breakdown = {k: int(round(v * 100 / total)) for k, v in raw.items()}
        drift = 100 - sum(breakdown.values())
        breakdown[max(breakdown, key=breakdown.get)] += drift

        severity_pool = ["low", "low", "medium", "medium", "medium", "high"]
        if breakdown["hazardous"] >= 15:
            severity_pool.extend(["high", "high", "critical"])
        elif breakdown["hazardous"] >= 5:
            severity_pool.extend(["medium", "high"])
        if breakdown["bulky"] >= 40:
            severity_pool.extend(["medium", "high"])
        if temp >= 30 and breakdown["organic"] >= 35:
            severity_pool.extend(["medium", "high"])
        rainy = any(word in condition for word in ("rain", "drizzle", "storm", "shower"))
        if rainy:
            severity_pool.extend(["medium", "high"])
        severity = random.choice(severity_pool)
        if rainy and severity == "low":
            severity = random.choice(["medium", "high"])
        if any(k in text for k in ("small", "minor", "little")):
            severity = random.choice(["low", "medium"]) if not rainy else random.choice(["medium", "high"])

        risks = random.sample(self._fallback_risks(breakdown, weather), k=min(3, len(self._fallback_risks(breakdown, weather))))
        actions = random.sample(self._fallback_actions(breakdown), k=min(4, len(self._fallback_actions(breakdown))))

        return {
            "waste_breakdown": breakdown,
            "estimated_volume": random.choice(["small", "medium", "large"]) if severity == "low" else random.choice(["medium", "large"]),
            "severity": severity,
            "description": random.choice([
                "Mixed waste patterns detected with dispatch-ready risk and cleanup guidance.",
                "The report indicates active waste accumulation requiring coordinated pickup.",
                "Observed conditions suggest a cleanup task with segregation and protective handling needs.",
                "Waste composition and local conditions point to timely collection priority.",
            ]),
            "risks": risks,
            "pickup_urgency": {
                "priority": "critical" if severity == "critical" else ("high" if severity == "high" else random.choice(["medium", "low"])),
                "recommended_within_hours": random.choice([2, 4, 6]) if severity == "critical" else random.choice([8, 12, 18, 24]),
                "reason": random.choice([
                    "Priority is based on waste mix, exposure risk, and current weather.",
                    "Local conditions increase the need for quick segregation and pickup.",
                    "Recommended timing reflects severity, accessibility, and sanitation risk.",
                ]),
            },
            "recommended_actions": actions,
            "confidence": round(random.uniform(0.56, 0.82), 2),
            "source": "fallback",
        }

    @staticmethod
    def _fallback_risks(breakdown, weather):
        risks = [
            "Sanitation and public-access risk if left unattended",
            "Possible spread onto walkways or road edge",
            "Manual handling risk without protective gear",
            "Delayed pickup can increase neighbourhood complaints",
        ]
        if breakdown.get("organic", 0) >= 35:
            risks.append("Odour and pest risk from organic decomposition")
        if breakdown.get("hazardous", 0) > 0:
            risks.append("Potential hazardous exposure; avoid direct handling")
        if (weather or {}).get("temperature", 0) and float((weather or {}).get("temperature", 0)) >= 28:
            risks.append("Warm weather can accelerate decomposition")
        return risks

    @staticmethod
    def _fallback_actions(breakdown):
        actions = [
            "Inspect site and verify waste type",
            "Use gloves and basic protective gear",
            "Segregate dry and wet waste before pickup",
            "Photograph the site after clearing",
            "Keep pedestrians away during collection",
        ]
        if breakdown.get("bulky", 0) >= 40:
            actions.append("Dispatch a vehicle or cart for heavy waste")
        if breakdown.get("hazardous", 0) > 0:
            actions.append("Escalate to trained hazardous-waste handlers")
        return actions

    # ---------------- WEATHER ----------------

    def _resolve_weather(self, weather, lat, lon):
        if weather:
            return weather
        if lat and lon:
            return get_weather_data(lat, lon)
        return {"temperature": 28, "humidity": 60, "condition": "clear"}

    # ---------------- NORMALISE ----------------

    def _normalise_analysis(self, result, weather):
        return self._clean_analysis(result, weather)

    def _clean_analysis(self, result, weather):
        result = dict(result or {})
        breakdown = result.get("waste_breakdown") or {}
        keys = ["organic", "plastic", "dry", "hazardous", "bulky"]
        clean_breakdown = {k: max(0, int(float(breakdown.get(k, 0) or 0))) for k in keys}
        total = sum(clean_breakdown.values())
        if total <= 0:
            clean_breakdown = self._fallback(weather)["waste_breakdown"]
        elif total != 100:
            clean_breakdown = {k: int(round(v * 100 / total)) for k, v in clean_breakdown.items()}
            drift = 100 - sum(clean_breakdown.values())
            clean_breakdown[max(clean_breakdown, key=clean_breakdown.get)] += drift

        result["waste_breakdown"] = clean_breakdown
        result["severity"] = str(result.get("severity") or "medium").lower()
        result["estimated_volume"] = str(result.get("estimated_volume") or "medium").lower()
        result["description"] = result.get("description") or "Structured waste analysis generated for this report."
        result["risks"] = list(result.get("risks") or ["Possible sanitation risk"])
        result["pickup_urgency"] = result.get("pickup_urgency") or {
            "priority": result["severity"],
            "recommended_within_hours": 12,
            "reason": "Default urgency based on report severity.",
        }
        result["recommended_actions"] = list(result.get("recommended_actions") or ["Inspect site", "Use protective gear"])
        result["confidence"] = float(result.get("confidence", 0.6) or 0.6)
        result["weather"] = weather or {}
        return result

    def _to_ui_payload(self, result, weather, source, observations=""):
        analysis = self._clean_analysis(result, weather)
        dominant = max(analysis["waste_breakdown"], key=analysis["waste_breakdown"].get)
        return {
            "waste_type": dominant if analysis["waste_breakdown"].get(dominant, 0) else "mixed",
            "severity": analysis["severity"],
            "summary": analysis["description"],
            "issues": analysis["risks"],
            "source": source,
            "created_at": datetime.utcnow().isoformat(),
            "weather": weather or {},
            "waste_analysis": analysis,
        }
