"""
Firestore persistence service for Spotless AI.
Uses service_account.json for Google Cloud auth (NOT keys.json).
"""

import logging
import os
from datetime import datetime
from typing import List

from models.report_model import WasteReport

logger = logging.getLogger(__name__)

COLLECTION_REPORTS    = "waste_reports"
COLLECTION_VOLUNTEERS = "volunteer_profiles"
COLLECTION_NGOS       = "ngo_profiles"

# Resolve service account path relative to this file's location
_SA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "service_account.json")
_SA_FILE = os.path.normpath(_SA_FILE)


class FirestoreService:
    def __init__(self):
        self._db = None
        self._available = False
        self._init_client()

    def _init_client(self) -> None:
        try:
            from google.cloud import firestore
            from google.oauth2 import service_account

            if os.path.exists(_SA_FILE):
                creds = service_account.Credentials.from_service_account_file(_SA_FILE)
                self._db = firestore.Client(credentials=creds)
                logger.info("Firestore initialised with service_account.json.")
            else:
                # Fall back to Application Default Credentials (env var / GCP metadata)
                self._db = firestore.Client()
                logger.info("Firestore initialised with ADC.")

            self._available = True
        except Exception as exc:
            logger.warning("Firestore unavailable (%s). Using in-memory only.", exc)

    @property
    def is_available(self) -> bool:
        return self._available

    def save_report(self, report: WasteReport) -> bool:
        if not self._available:
            return False
        try:
            doc = report.to_dict()
            doc["created_at"] = report.created_at.isoformat()
            doc["updated_at"] = datetime.utcnow().isoformat()
            self._db.collection(COLLECTION_REPORTS).document(report.report_id).set(doc)
            return True
        except Exception as exc:
            logger.error("Save report failed: %s", exc)
            return False

    def update_report_status(self, report_id: str, status: str,
                              assigned_ngo_name: str = None,
                              assigned_volunteer_names: list = None) -> bool:
        if not self._available:
            return False
        try:
            data = {"status": status, "updated_at": datetime.utcnow().isoformat()}
            if assigned_ngo_name:
                data["assigned_ngo_name"] = assigned_ngo_name
            if assigned_volunteer_names:
                data["assigned_volunteer_names"] = assigned_volunteer_names
            self._db.collection(COLLECTION_REPORTS).document(report_id).update(data)
            return True
        except Exception as exc:
            logger.error("Status update failed: %s", exc)
            return False

    def get_all_reports(self) -> List[dict]:
        if not self._available:
            return []
        try:
            docs = self._db.collection(COLLECTION_REPORTS).stream()
            return [d.to_dict() for d in docs]
        except Exception as exc:
            logger.error("Fetch reports failed: %s", exc)
            return []

    def save_volunteer_profile(self, profile: dict) -> bool:
        if not self._available:
            return False
        try:
            self._db.collection(COLLECTION_VOLUNTEERS).document(profile["volunteer_id"]).set(profile)
            return True
        except Exception:
            return False

    def get_volunteer_profile(self, email: str = "", phone: str = "") -> dict:
        if not self._available:
            return {}
        try:
            clean_email = (email or "").strip().lower()
            clean_phone = (phone or "").strip()
            collection = self._db.collection(COLLECTION_VOLUNTEERS)

            if clean_email:
                docs = collection.where("email", "==", clean_email).limit(1).stream()
                for doc in docs:
                    return doc.to_dict() or {}

            if clean_phone:
                docs = collection.where("phone", "==", clean_phone).limit(1).stream()
                for doc in docs:
                    return doc.to_dict() or {}
        except Exception as exc:
            logger.error("Fetch volunteer profile failed: %s", exc)
        return {}

    def save_ngo_profile(self, profile: dict) -> bool:
        if not self._available:
            return False
        try:
            self._db.collection(COLLECTION_NGOS).document(profile["ngo_id"]).set(profile)
            return True
        except Exception:
            return False
