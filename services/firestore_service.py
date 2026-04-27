"""
Firestore persistence service for Spotless AI.
"""

import logging
import json
import os
from datetime import datetime
from typing import List

from models.report_model import WasteReport

logger = logging.getLogger(__name__)

COLLECTION_REPORTS    = "waste_reports"
COLLECTION_VOLUNTEERS = "volunteer_profiles"
COLLECTION_NGOS       = "ngo_profiles"
COLLECTION_USERS      = "spotless_users"
LOCAL_DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "spotless_reports_db.json")


class FirestoreService:
    def __init__(self):
        self._db = None
        self._available = False
        self._init_client()

    def _init_client(self) -> None:
        try:
            from google.cloud import firestore
            self._db = firestore.Client()
            self._available = True
            logger.info("Firestore initialised.")
        except Exception as exc:
            logger.warning("Firestore unavailable (%s). Using local report store.", exc)

    @property
    def is_available(self) -> bool:
        return self._available

    def save_report(self, report: WasteReport) -> bool:
        try:
            doc = report.to_dict()
            doc["id"] = report.report_id
            doc["road"] = getattr(report, "road_name", "")
            doc["description"] = report.observations
            doc["assigned_to"] = (
                report.assigned_ngo_name
                or ", ".join(report.assigned_volunteer_names or [])
                or ""
            )
            doc["created_at"]  = report.created_at.isoformat()
            doc["updated_at"]  = datetime.utcnow().isoformat()
            if not self._available:
                self._upsert_local_report(doc)
                return True
            self._db.collection(COLLECTION_REPORTS).document(report.report_id).set(doc)
            return True
        except Exception as exc:
            logger.error("Save report failed: %s", exc)
            return False

    def update_report_status(self, report_id: str, status: str,
                              assigned_ngo_name: str = None,
                              assigned_volunteer_names: list = None) -> bool:
        try:
            data = {"status": status, "updated_at": datetime.utcnow().isoformat()}
            if assigned_ngo_name:
                data["assigned_ngo_name"] = assigned_ngo_name
                data["assigned_to"] = assigned_ngo_name
            if assigned_volunteer_names:
                data["assigned_volunteer_names"] = assigned_volunteer_names
                data["assigned_to"] = ", ".join(assigned_volunteer_names)
            if not self._available:
                reports = self._read_local_reports()
                for report in reports:
                    if report.get("report_id") == report_id or report.get("id") == report_id:
                        report.update(data)
                        break
                self._write_local_reports(reports)
                return True
            self._db.collection(COLLECTION_REPORTS).document(report_id).update(data)
            return True
        except Exception as exc:
            logger.error("Status update failed: %s", exc)
            return False

    def get_all_reports(self) -> List[dict]:
        try:
            if not self._available:
                return self._read_local_reports()
            docs = self._db.collection(COLLECTION_REPORTS).stream()
            return [d.to_dict() for d in docs]
        except Exception as exc:
            logger.error("Fetch reports failed: %s", exc)
            return []

    def reset_all_data(self) -> bool:
        try:
            self._write_local_reports([])
            if not self._available:
                return True
            for collection_name in (COLLECTION_REPORTS, COLLECTION_VOLUNTEERS, COLLECTION_NGOS, COLLECTION_USERS):
                docs = list(self._db.collection(collection_name).stream())
                for doc in docs:
                    doc.reference.delete()
            return True
        except Exception as exc:
            logger.error("Reset data failed: %s", exc)
            return False

    def _read_local_reports(self) -> List[dict]:
        if not os.path.exists(LOCAL_DB_FILE):
            return []
        try:
            with open(LOCAL_DB_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return list(data.get(COLLECTION_REPORTS, []))
        except Exception as exc:
            logger.error("Local report read failed: %s", exc)
            return []

    def _write_local_reports(self, reports: List[dict]) -> None:
        with open(LOCAL_DB_FILE, "w", encoding="utf-8") as fh:
            json.dump({COLLECTION_REPORTS: reports}, fh, indent=2)

    def _upsert_local_report(self, doc: dict) -> None:
        reports = self._read_local_reports()
        report_id = doc.get("report_id") or doc.get("id")
        for idx, existing in enumerate(reports):
            if existing.get("report_id") == report_id or existing.get("id") == report_id:
                reports[idx] = doc
                break
        else:
            reports.append(doc)
        self._write_local_reports(reports)

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
