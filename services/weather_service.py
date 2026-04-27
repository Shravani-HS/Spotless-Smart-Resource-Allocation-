import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


def _default_weather():
    return {
        "temperature": 28,
        "humidity": 60,
        "condition": "clear",
    }


def _load_openweather_key():
    keys_path = Path(__file__).resolve().parent.parent / "keys.json"
    try:
        with open(keys_path, "r", encoding="utf-8") as f:
            keys = json.load(f)
        key = keys.get("openweather_api_key")
        if key and key != "XXXXX":
            return key
    except Exception as exc:
        logger.warning("Could not read OpenWeather key: %s", exc)
    return None


def _condition_from_weather(payload):
    weather = payload.get("weather") or []
    main = ""
    if weather:
        main = str(weather[0].get("main") or "").lower()

    clouds = payload.get("clouds") or {}
    cloud_pct = clouds.get("all") or 0

    if "rain" in main or "drizzle" in main or "thunderstorm" in main:
        return "rain"
    if "cloud" in main or cloud_pct >= 50:
        return "cloudy"
    return "clear"


def get_weather_data(lat, lon):
    api_key = _load_openweather_key()
    if not api_key:
        return _default_weather()

    try:
        response = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "lat": lat,
                "lon": lon,
                "appid": api_key,
                "units": "metric",
            },
            timeout=4,
        )
        response.raise_for_status()
        payload = response.json()
        main = payload.get("main") or {}

        return {
            "temperature": round(float(main.get("temp", 28)), 1),
            "humidity": round(float(main.get("humidity", 60)), 1),
            "condition": _condition_from_weather(payload),
        }
    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)
        return _default_weather()
