"""I2 insights — Supabase Cloud Database Integration Service.

Connects to Supabase PostgreSQL using PostgREST endpoints.
Provides multi-device cloud synchronization for health camps and intake records.
Falls back seamlessly to local SQLite if Supabase credentials are not configured.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import requests

logger = logging.getLogger("doc_scanner.supabase")

class SupabaseService:
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.getenv("SUPABASE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
        self._circuit_open_until = 0.0
        self._failure_count = 0
        self._last_error = ""

    def is_configured(self) -> bool:
        """Check if Supabase URL and Key are provided."""
        return bool(self.url and self.key and "supabase.co" in self.url)

    def is_available(self) -> bool:
        """Check if Supabase is configured and circuit breaker is closed (healthy)."""
        if not self.is_configured():
            return False
        if time.time() < self._circuit_open_until:
            return False
        return True

    def _record_success(self):
        """Reset circuit breaker counters upon a successful network call."""
        self._failure_count = 0
        self._circuit_open_until = 0.0
        self._last_error = ""

    def _record_failure(self, error: Exception, cooldown: float = 60.0):
        """Record network failure and trip circuit breaker to prevent lagging requests."""
        self._failure_count += 1
        self._last_error = str(error)
        err_msg = str(error)
        is_dns_or_conn = any(term in err_msg for term in (
            "NameResolutionError", "Failed to resolve", "ConnectionRefused", "Max retries exceeded", "ConnectionError"
        ))
        trip_duration = 180.0 if is_dns_or_conn else cooldown
        self._circuit_open_until = time.time() + trip_duration
        if self._failure_count <= 2:
            logger.warning(
                "Supabase is unreachable (%s). Circuit breaker OPEN for %ds; falling back seamlessly to local SQLite.",
                error, int(trip_duration)
            )
        else:
            logger.debug("Supabase circuit breaker open: %s", error)

    def _headers(self) -> Dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }

    def check_tables_exist(self) -> Dict[str, bool]:
        """Check if camps and records tables exist in Supabase schema."""
        if not self.is_available():
            return {"camps": False, "records": False}
        status = {"camps": False, "records": False}
        try:
            r = requests.get(f"{self.url}/rest/v1/camps?limit=1", headers=self._headers(), timeout=3)
            status["camps"] = (r.status_code == 200)
            if status["camps"]:
                self._record_success()
        except Exception as e:
            self._record_failure(e)
            return status
        try:
            r = requests.get(f"{self.url}/rest/v1/records?limit=1", headers=self._headers(), timeout=3)
            status["records"] = (r.status_code == 200)
            if status["records"]:
                self._record_success()
        except Exception as e:
            self._record_failure(e)
        return status

    def list_camps_with_stats(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch all camps with computed intake stats from Supabase."""
        if not self.is_available():
            return None

        try:
            # Fetch camps
            r = requests.get(
                f"{self.url}/rest/v1/camps?select=*&order=created_at.desc",
                headers=self._headers(),
                timeout=5
            )
            r.raise_for_status()
            camps = [
                c for c in r.json()
                if not (
                    (c.get("name") or "").startswith("[PURGED") or 
                    "(demo)" in (c.get("name") or "").lower() or 
                    "demo" in (c.get("id") or "").lower()
                )
            ]

            # Fetch records summary to compute stats
            rec_res = requests.get(
                f"{self.url}/rest/v1/records?select=id,camp_id,status,person_name",
                headers=self._headers(),
                timeout=5
            )
            rec_res.raise_for_status()
            self._record_success()
            records = [
                rec for rec in rec_res.json()
                if rec.get("status") != "archived_mock" and
                not (rec.get("person_name") or "").startswith("[PURGED") and
                "demo" not in (rec.get("id") or "").lower()
            ]

            # Group stats by camp_id
            stats_by_camp: Dict[str, Dict[str, int]] = {}
            for rec in records:
                cid = rec.get("camp_id")
                if not cid:
                    continue
                if cid not in stats_by_camp:
                    stats_by_camp[cid] = {"scanned": 0, "to_check": 0, "reviewed": 0}
                stats_by_camp[cid]["scanned"] += 1
                if rec.get("status") == "needs_review":
                    stats_by_camp[cid]["to_check"] += 1
                elif rec.get("status") == "reviewed":
                    stats_by_camp[cid]["reviewed"] += 1

            enriched = []
            for c in camps:
                cid = c["id"]
                st = stats_by_camp.get(cid, {"scanned": 0, "to_check": 0, "reviewed": 0})
                expected = int(c.get("expected_records") or 100)
                scanned = st["scanned"]
                pct = round((scanned / expected * 100), 1) if expected > 0 else 0.0
                enriched.append({
                    "id": cid,
                    "name": c.get("name"),
                    "date_str": c.get("date_str") or "Today",
                    "expected_records": expected,
                    "total_scanned": scanned,
                    "to_check": st["to_check"],
                    "reviewed": st["reviewed"],
                    "progress_pct": min(100.0, pct),
                    "created_at": c.get("created_at")
                })
            return enriched
        except Exception as e:
            self._record_failure(e)
            return None

    def get_camp_by_id(self, camp_id: str) -> Optional[Dict[str, Any]]:
        """Fetch single camp by ID."""
        if not self.is_available():
            return None
        camps = self.list_camps_with_stats()
        if not camps:
            return None
        for c in camps:
            if c["id"] == camp_id:
                return c
        return None

    def create_camp(self, name: str, expected_records: int = 100, date_str: str = "Today") -> Dict[str, Any]:
        """Create a new screening camp in Supabase."""
        if not self.is_available():
            raise RuntimeError("Supabase credentials not configured or host unreachable.")

        camp_id = f"camp_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "id": camp_id,
            "name": name.strip(),
            "date_str": date_str.strip() or "Today",
            "expected_records": max(1, expected_records),
            "created_at": now
        }
        try:
            res = requests.post(
                f"{self.url}/rest/v1/camps",
                headers=self._headers(),
                json=payload,
                timeout=5
            )
            res.raise_for_status()
            self._record_success()
            return self.get_camp_by_id(camp_id) or payload
        except Exception as e:
            self._record_failure(e)
            raise

    def list_records(self, camp_id: Optional[str] = None, status: Optional[str] = None, limit: int = 100) -> Optional[List[Dict[str, Any]]]:
        """List records with optional camp isolation and status filters."""
        if not self.is_available():
            return None

        try:
            params = ["select=*,camps(name)", f"limit={limit}", "order=created_at.desc"]
            if camp_id:
                params.append(f"camp_id=eq.{camp_id}")
            if status:
                params.append(f"status=eq.{status}")

            qs = "&".join(params)
            res = requests.get(
                f"{self.url}/rest/v1/records?{qs}",
                headers=self._headers(),
                timeout=5
            )
            res.raise_for_status()
            self._record_success()
            rows = res.json()

            results = []
            for r in rows:
                if (r.get("person_name") or "").startswith("[PURGED") or r.get("status") == "archived_mock" or "demo" in (r.get("id") or "").lower():
                    continue
                camp_obj = r.get("camps") or {}
                camp_name = camp_obj.get("name") if isinstance(camp_obj, dict) else None
                results.append({
                    "id": r["id"],
                    "camp_id": r["camp_id"],
                    "camp_name": camp_name or "Health Camp",
                    "person_name": r.get("person_name") or "Unknown Patient",
                    "age": r.get("age"),
                    "gender": r.get("gender"),
                    "height": r.get("height"),
                    "weight": r.get("weight"),
                    "blood_pressure": r.get("blood_pressure"),
                    "examination_details": r.get("examination_details"),
                    "referral_details": r.get("referral_details"),
                    "missing_fields": r.get("missing_fields") or [],
                    "uncertain_fields": r.get("uncertain_fields") or [],
                    "extraction_notes": r.get("extraction_notes"),
                    "structured_record": r.get("structured_record") or {},
                    "status": r.get("status") or "needs_review",
                    "page1_preview": r.get("page1_preview"),
                    "page2_preview": r.get("page2_preview"),
                    "reviewer_notes": r.get("reviewer_notes"),
                    "reviewed_by": r.get("reviewed_by"),
                    "reviewed_at": r.get("reviewed_at"),
                    "created_at": r.get("created_at"),
                    "updated_at": r.get("updated_at")
                })
            return results
        except Exception as e:
            self._record_failure(e)
            return None

    def insert_record(self, record_data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a newly scanned record into Supabase."""
        if not self.is_configured():
            raise RuntimeError("Supabase credentials not configured.")

        rec_id = record_data.get("id") or f"rec_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
        now = datetime.now(timezone.utc).isoformat()

        payload = {
            "id": rec_id,
            "camp_id": record_data["camp_id"],
            "person_name": record_data.get("person_name") or "Unknown Patient",
            "age": record_data.get("age"),
            "gender": record_data.get("gender"),
            "height": record_data.get("height"),
            "weight": record_data.get("weight"),
            "blood_pressure": record_data.get("blood_pressure"),
            "examination_details": record_data.get("examination_details"),
            "referral_details": record_data.get("referral_details"),
            "missing_fields": record_data.get("missing_fields") or [],
            "uncertain_fields": record_data.get("uncertain_fields") or [],
            "extraction_notes": record_data.get("extraction_notes"),
            "structured_record": record_data.get("structured_record") or {},
            "status": record_data.get("status", "needs_review"),
            "page1_preview": record_data.get("page1_preview"),
            "page2_preview": record_data.get("page2_preview"),
            "reviewer_notes": record_data.get("reviewer_notes"),
            "reviewed_by": record_data.get("reviewed_by"),
            "reviewed_at": record_data.get("reviewed_at"),
            "created_at": now,
            "updated_at": now
        }

        res = requests.post(
            f"{self.url}/rest/v1/records",
            headers=self._headers(),
            json=payload,
            timeout=10
        )
        res.raise_for_status()
        return payload

    def get_record_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single record by its ID from Supabase."""
        if not self.is_configured():
            return None
        try:
            res = requests.get(
                f"{self.url}/rest/v1/records?id=eq.{record_id}&select=*,camps(name)&limit=1",
                headers=self._headers(),
                timeout=10
            )
            res.raise_for_status()
            rows = res.json()
            if not rows:
                return None
            r = rows[0]
            camp_obj = r.get("camps") or {}
            camp_name = camp_obj.get("name") if isinstance(camp_obj, dict) else None
            return {
                "id": r["id"],
                "camp_id": r["camp_id"],
                "camp_name": camp_name or "Health Camp",
                "person_name": r.get("person_name") or "Unknown Patient",
                "age": r.get("age"),
                "gender": r.get("gender"),
                "height": r.get("height"),
                "weight": r.get("weight"),
                "blood_pressure": r.get("blood_pressure"),
                "examination_details": r.get("examination_details"),
                "referral_details": r.get("referral_details"),
                "missing_fields": r.get("missing_fields") or [],
                "uncertain_fields": r.get("uncertain_fields") or [],
                "extraction_notes": r.get("extraction_notes"),
                "structured_record": r.get("structured_record") or {},
                "status": r.get("status") or "needs_review",
                "page1_preview": r.get("page1_preview"),
                "page2_preview": r.get("page2_preview"),
                "reviewer_notes": r.get("reviewer_notes"),
                "reviewed_by": r.get("reviewed_by"),
                "reviewed_at": r.get("reviewed_at"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at")
            }
        except Exception as e:
            logger.error("Supabase get_record error: %s", e)
            return None

    def update_reviewed_record(self, record_id: str, update_data: Dict[str, Any], reviewer: str) -> Optional[Dict[str, Any]]:
        """Update reviewed fields and mark record as reviewed in Supabase."""
        if not self.is_configured():
            return None
        now = datetime.now(timezone.utc).isoformat()
        payload = {
            "person_name": update_data.get("person_name", "").strip() or "Unnamed Patient",
            "age": update_data.get("age"),
            "gender": update_data.get("gender"),
            "height": update_data.get("height"),
            "weight": update_data.get("weight"),
            "blood_pressure": update_data.get("blood_pressure"),
            "examination_details": update_data.get("examination_details"),
            "referral_details": update_data.get("referral_details"),
            "missing_fields": update_data.get("missing_fields") or [],
            "uncertain_fields": update_data.get("uncertain_fields") or [],
            "extraction_notes": update_data.get("extraction_notes"),
            "reviewer_notes": update_data.get("reviewer_notes"),
            "reviewed_by": reviewer,
            "reviewed_at": now,
            "status": "reviewed",
            "updated_at": now
        }
        if update_data.get("structured_record") is not None:
            payload["structured_record"] = update_data.get("structured_record")

        try:
            res = requests.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json=payload,
                timeout=10
            )
            res.raise_for_status()
            return self.get_record_by_id(record_id)
        except Exception as e:
            logger.error("Supabase update_reviewed_record error: %s", e)
            return None

    def update_record(self, record_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update arbitrary record fields in Supabase."""
        if not self.is_configured():
            return None
        now = datetime.now(timezone.utc).isoformat()
        payload = {**update_data, "updated_at": now}
        try:
            res = requests.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json=payload,
                timeout=10
            )
            res.raise_for_status()
            return self.get_record_by_id(record_id)
        except Exception as e:
            logger.error("Supabase update_record error: %s", e)
            return None

    def delete_record(self, record_id: str) -> bool:
        """Delete a record from Supabase."""
        if not self.is_configured():
            return False
        try:
            res = requests.delete(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                timeout=10
            )
            if res.status_code in (200, 204) and len(res.json() if res.text.strip().startswith('[') else []) > 0:
                return True
            # If RLS prevented hard delete, purge via PATCH so it is immediately excluded
            patch_res = requests.patch(
                f"{self.url}/rest/v1/records?id=eq.{record_id}",
                headers=self._headers(),
                json={"status": "archived_mock", "person_name": "[PURGED RECORD]"},
                timeout=10
            )
            return patch_res.status_code in (200, 204)
        except Exception as e:
            logger.error("Supabase delete_record error: %s", e)
            return False

    def update_camp(self, camp_id: str, name: str, expected_records: int, date_str: str = "Today", location: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Update camp details in Supabase."""
        if not self.is_configured():
            return None
        payload = {
            "name": name.strip(),
            "expected_records": max(1, expected_records),
            "date_str": date_str.strip() or "Today",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        if location:
            payload["location"] = location
        try:
            res = requests.patch(
                f"{self.url}/rest/v1/camps?id=eq.{camp_id}",
                headers=self._headers(),
                json=payload,
                timeout=10
            )
            res.raise_for_status()
            return self.get_camp_by_id(camp_id)
        except Exception as e:
            logger.error("Supabase update_camp error: %s", e)
            return None

    def delete_camp(self, camp_id: str) -> bool:
        """Delete camp and cascade records in Supabase."""
        if not self.is_configured():
            return False
        try:
            # Delete records first in case FK cascade is not configured
            requests.delete(f"{self.url}/rest/v1/records?camp_id=eq.{camp_id}", headers=self._headers(), timeout=10)
            res = requests.delete(f"{self.url}/rest/v1/camps?id=eq.{camp_id}", headers=self._headers(), timeout=10)
            if res.status_code in (200, 204) and len(res.json() if res.text.strip().startswith('[') else []) > 0:
                return True
            # Purge via PATCH fallback
            requests.patch(
                f"{self.url}/rest/v1/records?camp_id=eq.{camp_id}",
                headers=self._headers(),
                json={"status": "archived_mock", "person_name": "[PURGED RECORD]"},
                timeout=10
            )
            patch_res = requests.patch(
                f"{self.url}/rest/v1/camps?id=eq.{camp_id}",
                headers=self._headers(),
                json={"name": f"[PURGED CAMP] {camp_id}"},
                timeout=10
            )
            return patch_res.status_code in (200, 204)
        except Exception as e:
            logger.error("Supabase delete_camp error: %s", e)
            return False

    def upload_storage_image(self, file_path: str, file_bytes: bytes, content_type: str = "image/jpeg", bucket: str = "intake-scans") -> Optional[str]:
        """Upload scanned intake image to Supabase Storage and return public URL."""
        if not self.is_configured():
            return None
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": content_type,
            "x-upsert": "true"
        }
        # Path format: {bucket}/{file_path}
        clean_path = file_path.lstrip("/")
        upload_url = f"{self.url}/storage/v1/object/{bucket}/{clean_path}"
        try:
            res = requests.post(upload_url, headers=headers, data=file_bytes, timeout=15)
            if res.status_code in (200, 201):
                # Public URL format
                public_url = f"{self.url}/storage/v1/object/public/{bucket}/{clean_path}"
                return public_url
            logger.warning("Supabase storage upload returned status %d: %s", res.status_code, res.text)
            return None
        except Exception as e:
            logger.error("Supabase storage upload error: %s", e)
            return None

    def auth_sign_in(self, email: str, password: str) -> Dict[str, Any]:
        """Sign in using Supabase Auth endpoint."""
        if not self.is_configured():
            return {"success": False, "error": "Supabase not configured"}
        url = f"{self.url}/auth/v1/token?grant_type=password"
        headers = {
            "apikey": self.key,
            "Content-Type": "application/json"
        }
        try:
            res = requests.post(url, headers=headers, json={"email": email, "password": password}, timeout=10)
            data = res.json()
            if res.status_code == 200 and "access_token" in data:
                user = data.get("user") or {}
                return {
                    "success": True,
                    "access_token": data["access_token"],
                    "user_id": user.get("id"),
                    "email": user.get("email"),
                    "user_metadata": user.get("user_metadata", {})
                }
            return {"success": False, "error": data.get("error_description") or data.get("msg") or "Invalid credentials"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def auth_sign_up(self, email: str, password: str, full_name: str, specialization: str = "General Physician") -> Dict[str, Any]:
        """Sign up new doctor via Supabase Auth."""
        if not self.is_configured():
            return {"success": False, "error": "Supabase not configured"}
        url = f"{self.url}/auth/v1/signup"
        headers = {
            "apikey": self.key,
            "Content-Type": "application/json"
        }
        payload = {
            "email": email,
            "password": password,
            "data": {
                "full_name": full_name,
                "specialization": specialization
            }
        }
        try:
            res = requests.post(url, headers=headers, json=payload, timeout=10)
            data = res.json()
            if res.status_code in (200, 201):
                user = data.get("user") or data
                return {
                    "success": True,
                    "user_id": user.get("id"),
                    "email": user.get("email"),
                    "access_token": data.get("access_token")
                }
            return {"success": False, "error": data.get("msg") or data.get("error_description") or "Sign up failed"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def sync_from_sqlite(self, db_path: str) -> Dict[str, int]:
        """Optionally migrate or seed existing SQLite camps & records into Supabase."""
        if not self.is_configured():
            return {"camps": 0, "records": 0}
        
        try:
            from backend import database
        except ImportError:
            import database

        camps = database.list_camps_with_stats(db_path)
        records = database.list_records(camp_id=None, limit=1000, db_path=db_path)
        
        camps_synced = 0
        records_synced = 0
        
        for c in camps:
            try:
                res = requests.post(
                    f"{self.url}/rest/v1/camps",
                    headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                    json={
                        "id": c["id"],
                        "name": c["name"],
                        "date_str": c.get("date_str") or "Today",
                        "expected_records": c.get("expected_records", 100),
                        "created_at": c.get("created_at") or datetime.now(timezone.utc).isoformat()
                    },
                    timeout=10
                )
                if res.status_code in (200, 201):
                    camps_synced += 1
            except Exception as e:
                logger.warning("Failed to sync camp %s to Supabase: %s", c["id"], e)

        for r in records:
            try:
                res = requests.post(
                    f"{self.url}/rest/v1/records",
                    headers={**self._headers(), "Prefer": "resolution=merge-duplicates"},
                    json={
                        "id": r["id"],
                        "camp_id": r["camp_id"],
                        "person_name": r.get("person_name") or "Unknown Patient",
                        "age": r.get("age"),
                        "gender": r.get("gender"),
                        "height": r.get("height"),
                        "weight": r.get("weight"),
                        "blood_pressure": r.get("blood_pressure"),
                        "examination_details": r.get("examination_details"),
                        "referral_details": r.get("referral_details"),
                        "missing_fields": r.get("missing_fields") or [],
                        "uncertain_fields": r.get("uncertain_fields") or [],
                        "extraction_notes": r.get("extraction_notes"),
                        "structured_record": r.get("structured_record") or {},
                        "status": r.get("status") or "needs_review",
                        "page1_preview": r.get("page1_preview"),
                        "page2_preview": r.get("page2_preview"),
                        "reviewer_notes": r.get("reviewer_notes"),
                        "reviewed_by": r.get("reviewed_by"),
                        "reviewed_at": r.get("reviewed_at"),
                        "created_at": r.get("created_at") or datetime.now(timezone.utc).isoformat(),
                        "updated_at": r.get("updated_at") or datetime.now(timezone.utc).isoformat()
                    },
                    timeout=10
                )
                if res.status_code in (200, 201):
                    records_synced += 1
            except Exception as e:
                logger.warning("Failed to sync record %s to Supabase: %s", r["id"], e)

        return {"camps": camps_synced, "records": records_synced}

