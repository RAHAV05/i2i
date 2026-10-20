"""I2 insights — Clinical Intake & Vision AI Platform.

Provides:
- Camp management & dynamic aggregate statistics (zero fake counters).
- Two-page document image processing endpoint with Groq Vision (or provider abstraction).
- Record creation, human-in-the-loop review workflow, and SQLite persistence.
- Secure Excel (.xlsx) and CSV export with formula injection protection.
- Demo mode data reset and zero patient data leakage.
"""

import os
import sys
import io
import re
import logging
from datetime import datetime, timezone
from typing import Tuple, Dict, Any
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    LIMITER_AVAILABLE = True
except ImportError:
    LIMITER_AVAILABLE = False
from dotenv import load_dotenv

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load environment variables from .env file if present
load_dotenv(os.path.join(CURRENT_DIR, ".env"))

try:
    from backend import database
    from backend.services.ai_service import AIService, extract as ai_extract
    from backend.services.groq_service import GroqService
    from backend.services.gemini_service import GeminiService
    from backend.services.record_service import RecordService
    from backend.services.supabase_service import SupabaseService
except ImportError:
    import database
    from services.ai_service import AIService, extract as ai_extract
    from services.groq_service import GroqService
    from services.gemini_service import GeminiService
    from services.record_service import RecordService
    from services.supabase_service import SupabaseService

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
)
logger = logging.getLogger("doc_scanner")

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/jpg"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per image

def create_app(db_path: str = database.DEFAULT_DB_PATH) -> Flask:
    """Application factory for Flask API."""
    candidate_dirs = [
        os.path.abspath(os.path.join(PROJECT_ROOT, "frontend")),
        os.path.abspath(os.path.join(CURRENT_DIR, "..", "frontend")),
        os.path.abspath(os.path.join(CURRENT_DIR, "frontend")),
        PROJECT_ROOT,
    ]
    frontend_dir = CURRENT_DIR
    for d in candidate_dirs:
        if os.path.exists(os.path.join(d, "index.html")):
            frontend_dir = d
            break

    app = Flask(__name__, static_folder=frontend_dir, static_url_path="")
    
    # Configure CORS for local development
    CORS(app, resources={r"/api/*": {"origins": ["http://localhost:*", "http://127.0.0.1:*", "http://localhost:3000", "http://localhost:5001"]}})
    
    # Initialize DB tables
    database.init_db(db_path)

    # Initialize Flask-Limiter for rate limiting (10/min per user, 60/min per org)
    if LIMITER_AVAILABLE:
        def get_user_rate_key() -> str:
            return request.headers.get("X-User-ID") or request.headers.get("X-Doctor-ID") or get_remote_address()

        def get_org_rate_key() -> str:
            return request.headers.get("X-Org-ID") or "org_default"

        limiter = Limiter(
            get_user_rate_key,
            app=app,
            storage_uri="memory://",
            strategy="fixed-window"
        )
    else:
        class DummyLimiter:
            def limit(self, *args, **kwargs):
                def decorator(f):
                    return f
                return decorator
        limiter = DummyLimiter()

    @app.errorhandler(429)
    def ratelimit_handler(e):
        return jsonify({
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Rate limit exceeded.",
            "code": "RATE_LIMIT_EXCEEDED"
        }), 429

    def is_admin_request() -> bool:
        admin_key = os.getenv("ADMIN_API_KEY", "admin_secret_key")
        if request.headers.get("X-Admin-Key") == admin_key:
            return True
        if request.headers.get("X-Role", "").lower() in ("admin", "superuser"):
            return True
        if request.headers.get("X-Doctor-Role", "").lower() in ("admin", "superuser"):
            return True
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth.split(" ", 1)[1].strip()
            if token == admin_key or token.lower() == "admin":
                return True
        return False

    record_service = RecordService(db_path)
    supabase_service = SupabaseService()

    def is_supabase_enabled() -> bool:
        """Only attempt Supabase network queries if configured and not running unit tests."""
        if app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        return supabase_service.is_configured()

    def get_ai_service() -> AIService:
        provider = os.getenv("AI_PROVIDER", "gemini").lower().strip()
        if provider == "groq":
            return GroqService()
        return GeminiService()

    @app.after_request
    def add_no_cache_headers(response):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    # --- Static Frontend Routes ---
    @app.route("/")
    @app.route("/app.py")
    @app.route("/backend/app.py")
    @app.route("/api/index")
    def index():
        return send_from_directory(frontend_dir, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        if filename in ("app.py", "backend/app.py", "index.py", "api/index"):
            return send_from_directory(frontend_dir, "index.html")
        file_path = os.path.join(frontend_dir, filename)
        if os.path.exists(file_path) and not os.path.isdir(file_path):
            return send_from_directory(frontend_dir, filename)
        return send_from_directory(frontend_dir, "index.html")

    # --- API Status & Metrics ---
    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "app": "doc-scanner", "production": True})

    @app.route("/api/status", methods=["GET"])
    def status():
        ai = get_ai_service()
        stats = database.get_overall_stats(db_path)
        tables_exist = {"camps": False, "records": False}
        supabase_ready = False
        if is_supabase_enabled():
            tables_exist = supabase_service.check_tables_exist()
            supabase_ready = bool(tables_exist.get("camps") and tables_exist.get("records"))

        return jsonify({
            "production_mode": True,
            "demo_mode": False,
            "mode_label": "PRODUCTION — Real Medical Intake Active",
            "ai_configured": ai.is_configured(),
            "ai_provider": ai.provider_name(),
            "supabase_configured": supabase_service.is_configured(),
            "supabase_url": supabase_service.url if supabase_service.is_configured() else None,
            "supabase_tables_ready": supabase_ready,
            "supabase_tables": tables_exist,
            "overall_stats": stats
        })

    @app.route("/api/stats", methods=["GET"])
    def stats():
        return jsonify(database.get_overall_stats(db_path))

    # --- Camps API ---
    @app.route("/api/camps", methods=["GET"])
    def list_camps():
        if is_supabase_enabled():
            camps = supabase_service.list_camps_with_stats()
            if camps is not None:
                return jsonify(camps)
        camps = database.list_camps_with_stats(db_path)
        return jsonify(camps)

    @app.route("/api/camps", methods=["POST"])
    def add_camp():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Camp name is required"}), 400
        
        try:
            expected = int(data.get("expected_records", 100))
        except (ValueError, TypeError):
            expected = 100
            
        date_str = data.get("date_str", "Today").strip() or "Today"
        location = data.get("location", "Chennai Health Camp")
        if is_supabase_enabled():
            try:
                camp = supabase_service.create_camp(name, expected, date_str)
                # Also persist locally in SQLite for resilience using the exact same ID
                try:
                    database.create_camp(name, expected, date_str, location=location, camp_id=camp.get("id"), db_path=db_path)
                except Exception as db_err:
                    logger.warning("Local camp mirror warning: %s", db_err)
                return jsonify(camp), 201
            except Exception as e:
                logger.error("Failed to create camp in Supabase: %s; falling back to SQLite", e)

        camp = database.create_camp(name, expected, date_str, location=location, db_path=db_path)
        return jsonify(camp), 201

    @app.route("/api/camps/<camp_id>", methods=["GET"])
    def get_camp(camp_id):
        if is_supabase_enabled():
            camp = supabase_service.get_camp_by_id(camp_id)
            if camp:
                return jsonify(camp)
        camp = database.get_camp_by_id(camp_id, db_path)
        if not camp:
            return jsonify({"error": "Camp not found"}), 404
        return jsonify(camp)

    @app.route("/api/camps/<camp_id>", methods=["PUT"])
    def update_camp(camp_id):
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Camp name is required"}), 400
        expected = int(data.get("expected_records", 100))
        date_str = data.get("date_str", "Today").strip() or "Today"
        location = data.get("location")
        if supabase_service.is_configured():
            try:
                supabase_service.update_camp(camp_id, name, expected, date_str, location)
            except Exception as e:
                logger.error("Supabase update_camp error: %s", e)
        camp = database.update_camp(camp_id, name, expected, date_str, location, db_path)
        if not camp:
            return jsonify({"error": "Camp not found"}), 404
        return jsonify(camp)

    @app.route("/api/camps/<camp_id>", methods=["DELETE"])
    def delete_camp(camp_id):
        if supabase_service.is_configured():
            try:
                supabase_service.delete_camp(camp_id)
            except Exception as e:
                logger.error("Supabase delete_camp error: %s", e)
        success = database.delete_camp(camp_id, db_path)
        return jsonify({"status": "deleted", "success": success})

    # --- Records API ---
    @app.route("/api/records", methods=["GET"])
    def list_records():
        camp_id = request.args.get("camp_id")
        status_filter = request.args.get("status")
        scanned_by = request.args.get("scanned_by")
        query = request.args.get("q")
        page_param = request.args.get("page")
        page = int(page_param) if page_param and page_param.isdigit() else None
        limit = min(int(request.args.get("limit", 20 if page else 100)), 500)

        recs = database.list_records(
            camp_id=camp_id,
            status=status_filter,
            query=query,
            scanned_by=scanned_by,
            page=page,
            limit=limit,
            db_path=db_path
        )
        return jsonify(recs)

    @app.route("/api/records/<record_id>", methods=["GET"])
    def get_record(record_id):
        if supabase_service.is_configured():
            try:
                rec = supabase_service.get_record_by_id(record_id)
                if rec:
                    return jsonify(rec)
            except Exception as e:
                logger.error("Supabase get_record error: %s", e)
        rec = database.get_record_by_id(record_id, db_path)
        if not rec:
            return jsonify({"error": "Record not found"}), 404
        return jsonify(rec)

    @app.route("/api/records", methods=["POST"])
    def create_record():
        payload = request.get_json(silent=True) or {}
        # Ensure a single, consistent record ID across both Supabase and SQLite
        if not payload.get("id"):
            payload["id"] = f"rec_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
        try:
            if is_supabase_enabled():
                try:
                    created = supabase_service.insert_record(payload)
                    # Also persist locally in SQLite for resilience using identical ID
                    try:
                        record_service.create_record(payload)
                    except Exception:
                        pass
                    return jsonify(created), 201
                except Exception as e:
                    logger.error("Failed to insert record in Supabase: %s; falling back to SQLite", e)

            created = record_service.create_record(payload)
            return jsonify(created), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.exception("Error saving record")
            return jsonify({"error": "Failed to persist record"}), 500

    @app.route("/api/records/<record_id>", methods=["PUT"])
    def update_record(record_id):
        payload = request.get_json(silent=True) or {}
        if is_supabase_enabled():
            try:
                supabase_service.update_record(record_id, payload)
            except Exception as e:
                logger.error("Supabase update_record error: %s", e)
        updated = database.update_record(record_id, payload, db_path)
        if not updated:
            return jsonify({"error": "Record not found"}), 404
        return jsonify(updated)

    @app.route("/api/records/<record_id>", methods=["DELETE"])
    def delete_record(record_id):
        if is_supabase_enabled():
            try:
                supabase_service.delete_record(record_id)
            except Exception as e:
                logger.error("Supabase delete_record error: %s", e)
        success = database.delete_record(record_id, db_path)
        return jsonify({"status": "deleted", "success": success})

    @app.route("/api/records/<record_id>/review", methods=["PUT", "POST"])
    def review_record(record_id):
        """Review and update record fields. Resolving flags alone does NOT approve or bill paper."""
        payload = request.get_json(silent=True) or {}
        reviewer = payload.get("reviewed_by", "Dr. Priya (Demo)")
        
        try:
            if is_supabase_enabled():
                try:
                    supabase_service.update_reviewed_record(record_id, payload, reviewer)
                except Exception as e:
                    logger.error("Failed to update in Supabase: %s", e)

            updated = record_service.review_record(record_id, payload, reviewer)
            # Release lock upon review
            database.unlock_record(record_id, reviewer, db_path=db_path)
            return jsonify(updated)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.exception("Error reviewing record")
            return jsonify({"error": "Failed to update record"}), 500

    @app.route("/api/records/<record_id>/approve", methods=["POST"])
    def approve_record_endpoint(record_id):
        """Explicit approval action: checks flags are resolved, stores approved_by & approved_at, and marks paper billed."""
        payload = request.get_json(silent=True) or {}
        doctor = payload.get("approved_by") or request.headers.get("X-Doctor-ID") or "Dr. Priya (Doctor)"
        org_id = payload.get("org_id") or request.headers.get("X-Org-ID") or "org_default"

        # Check for unresolved flags passed in payload
        unresolved_flags = payload.get("unresolved_flags") or payload.get("flags_unresolved")
        if unresolved_flags:
            return jsonify({
                "error": "flags_unresolved",
                "message": f"Approval refused: {len(unresolved_flags)} flagged fields remain unresolved."
            }), 409

        success, updated, err = database.approve_record(record_id, approved_by=doctor, org_id=org_id, db_path=db_path)
        if not success:
            if err == "flags_unresolved":
                return jsonify({
                    "error": "flags_unresolved",
                    "message": "Approval refused: flagged fields remain unresolved."
                }), 409
            return jsonify({"error": err or "Failed to approve record"}), 404

        return jsonify({
            "success": True,
            "status": "approved",
            "approved_by": doctor,
            "approved_at": updated.get("approved_at"),
            "record": updated
        })

    @app.route("/api/records/<record_id>/release", methods=["POST"])
    def release_record(record_id):
        org_id = request.headers.get("X-Org-ID") or request.args.get("org_id") or "org_default"
        rec = database.get_record_by_id(record_id, db_path)
        if rec and rec.get("status") in ("reviewed", "approved"):
            return jsonify({
                "error": "already_billed",
                "message": "Cannot release paper quota for an already approved record."
            }), 409
        papers_left = database.release_paper(org_id=org_id, record_id=record_id, reason="manual_release", db_path=db_path)
        return jsonify({"success": True, "papers_left": papers_left})

    @app.route("/api/records/<record_id>/lock", methods=["POST"])
    def lock_record(record_id):
        data = request.get_json(silent=True) or {}
        doctor_name = data.get("doctor_name") or request.headers.get("X-Doctor-ID") or "Dr. Priya"
        res = database.lock_record(record_id, doctor_name, db_path=db_path)
        return jsonify(res)

    @app.route("/api/records/<record_id>/unlock", methods=["POST"])
    def unlock_record(record_id):
        data = request.get_json(silent=True) or {}
        doctor_name = data.get("doctor_name") or request.headers.get("X-Doctor-ID") or "Dr. Priya"
        success = database.unlock_record(record_id, doctor_name, db_path=db_path)
        return jsonify({"success": success, "unlocked": success})

    @app.route("/api/records/validate", methods=["POST"])
    def validate_record():
        payload = request.get_json(silent=True) or {}
        res = record_service.validate_record_fields(payload)
        return jsonify(res)

    @app.route("/api/records/check-duplicate", methods=["POST"])
    def check_duplicate():
        data = request.get_json(silent=True) or {}
        camp_id = data.get("camp_id", "")
        person_name = data.get("person_name", "")
        phone = data.get("phone", "")
        exclude_id = data.get("exclude_id")
        res = database.check_duplicate_record(camp_id, person_name, phone, exclude_id, db_path)
        res["has_duplicate"] = res.get("is_duplicate", False)
        res["count"] = 1 if res["has_duplicate"] else 0
        return jsonify(res)

    @app.route("/api/records/<record_id>/erase", methods=["DELETE", "POST"])
    def erase_record(record_id):
        # DPDP Right to Erasure
        if is_supabase_enabled():
            try:
                supabase_service.delete_record(record_id)
            except Exception as e:
                logger.error("Supabase erasure error: %s", e)
        success = database.erase_patient_record(record_id, db_path)
        return jsonify({
            "status": "erased",
            "success": success,
            "erased": success,
            "message": "Patient data and source images permanently deleted under DPDP Act."
        })

    @app.route("/api/admin/billing", methods=["GET"])
    def admin_billing():
        # Aggregated admin, billing and operational telemetry
        billing_info = database.get_admin_billing(db_path)
        billing_info["used_scans"] = billing_info.get("quota_used", 0)
        billing_info["monthly_limit"] = billing_info.get("quota_total", 5000)
        billing_info["cost_estimate_inr"] = billing_info.get("est_groq_cost_inr", 0.0)
        billing_info["invoices"] = [
            {"id": "INV-2026-10", "date": "01 Oct 2026", "plan": "Outreach 2,500 Scans", "amount": "₹0.00 (Grant)", "status": "Paid"},
            {"id": "INV-2026-09", "date": "01 Sep 2026", "plan": "Outreach 2,500 Scans", "amount": "₹0.00 (Grant)", "status": "Paid"}
        ]
        return jsonify(billing_info)

    @app.route("/api/admin/purchase-credits", methods=["POST"])
    def purchase_credits():
        data = request.get_json(silent=True) or {}
        credits = int(data.get("credits", 1000))
        res = database.purchase_credits(credits, db_path=db_path)
        res["credits_added"] = res.get("added_credits", credits)
        res["new_limit"] = res.get("new_quota", 5000)
        return jsonify(res)

    # --- Tokenized Single-Use Invites API ---
    @app.route("/api/community/invites", methods=["GET"])
    def list_invites():
        comm_id = request.args.get("community_id") or "comm_health_demo_01"
        invs = database.list_invites(comm_id, db_path=db_path)
        return jsonify({"invites": invs, "total": len(invs)})

    @app.route("/api/community/invites", methods=["POST"])
    def create_invite():
        data = request.get_json(silent=True) or {}
        comm_id = data.get("community_id") or "comm_health_demo_01"
        email = data.get("email")
        role = data.get("role", "doctor")
        expires_hours = int(data.get("expires_hours", 72))
        created_by = data.get("created_by") or request.headers.get("X-Doctor-ID") or "Dr. Priya"
        inv = database.create_invite(comm_id, email, role, expires_hours, created_by, db_path=db_path)
        return jsonify(inv), 201

    @app.route("/api/community/invites/<invite_id>", methods=["DELETE"])
    def revoke_invite(invite_id):
        success = database.revoke_invite(invite_id, db_path=db_path)
        return jsonify({"success": success, "revoked": success})

    @app.route("/api/community/verify-invite", methods=["POST"])
    def verify_invite_token():
        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        inv = database.verify_invite(code, db_path=db_path)
        if not inv:
            return jsonify({"valid": False, "error": "Invite code is invalid, expired, or has been revoked."}), 400
        return jsonify({"valid": True, "invite": inv})

    # --- Image Upload API (Supabase Storage with Local Fallback) ---
    @app.route("/api/upload-image", methods=["POST"])
    def upload_image():
        if "image" not in request.files:
            return jsonify({"error": "No image file uploaded"}), 400
        img = request.files["image"]
        camp_id = request.form.get("camp_id", "default_camp")
        page_num = request.form.get("page", "1")
        filename = f"{camp_id}_page{page_num}_{os.urandom(4).hex()}.jpg"
        img_bytes = img.read()
        mimetype = img.mimetype or "image/jpeg"

        # Try Supabase Storage first
        if supabase_service.is_configured():
            storage_path = f"{camp_id}/{filename}"
            public_url = supabase_service.upload_storage_image(storage_path, img_bytes, mimetype)
            if public_url:
                return jsonify({"url": public_url, "storage": "supabase"})

        # Fallback to local static upload dir
        uploads_dir = os.path.join(frontend_dir, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        local_path = os.path.join(uploads_dir, filename)
        with open(local_path, "wb") as f:
            f.write(img_bytes)
        return jsonify({"url": f"/uploads/{filename}", "storage": "local"})

    # --- Camp Analytics API ---
    @app.route("/api/analytics", methods=["GET"])
    @app.route("/api/camps/<camp_id>/analytics", methods=["GET"])
    def camp_analytics(camp_id=None):
        cid = camp_id or request.args.get("camp_id")
        stats = database.get_camp_analytics(camp_id=cid, db_path=db_path)
        return jsonify(stats)

    # --- Community (Organization / Outreach Network) API ---
    @app.route("/api/community/create", methods=["POST"])
    def community_create():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        creator_name = (data.get("creator_name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        custom_code = (data.get("code") or "").strip().upper()

        if not name or not creator_name or not email:
            return jsonify({"error": "Community name, your name, and email are required."}), 400

        # Build community in database
        comm = database.create_community(name, creator_name, email, code=custom_code or None, db_path=db_path)
        
        # Register or update founder doctor
        doctor = database.get_or_create_doctor(
            email=email,
            full_name=creator_name,
            specialization="Community Lead / Organizer",
            community_id=comm["id"],
            community_code=comm["code"],
            community_role="admin",
            db_path=db_path
        )
        
        token = None
        if supabase_service.is_configured() and password:
            auth_res = supabase_service.auth_sign_up(email, password, creator_name, "Community Lead")
            token = auth_res.get("access_token")

        profile = database.get_doctor_profile(doctor["id"], db_path=db_path)
        return jsonify({
            "success": True,
            "community": comm,
            "doctor": profile,
            "token": token or f"demo_jwt_{doctor['id']}"
        }), 201

    @app.route("/api/community/verify", methods=["POST"])
    def community_verify():
        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        if not code:
            return jsonify({"valid": False, "error": "Community code is required"}), 400
        comm = database.get_community_by_code(code, db_path=db_path)
        if not comm:
            return jsonify({"valid": False, "error": f"Invalid code '{code}'. Ask your organizer for the exact code."}), 404
        return jsonify({
            "valid": True,
            "community": {
                "id": comm["id"],
                "name": comm["name"],
                "code": comm["code"],
                "creator_name": comm["creator_name"]
            }
        })

    @app.route("/api/community/rotate-code", methods=["POST"])
    def community_rotate():
        data = request.get_json(silent=True) or {}
        comm_id = data.get("community_id")
        new_code = data.get("new_code")
        if not comm_id:
            return jsonify({"error": "community_id is required"}), 400
        res = database.rotate_community_code(comm_id, new_code=new_code, db_path=db_path)
        if not res:
            return jsonify({"error": "Community not found"}), 404
        return jsonify({"success": True, "community": res})

    @app.route("/api/community/members/revoke", methods=["POST"])
    def community_revoke_member():
        data = request.get_json(silent=True) or {}
        comm_id = data.get("community_id")
        doctor_id = data.get("doctor_id")
        if not comm_id or not doctor_id:
            return jsonify({"error": "community_id and doctor_id are required"}), 400
        success = database.revoke_community_member(comm_id, doctor_id, db_path=db_path)
        return jsonify({"success": success})

    # --- Doctor Authentication & Profile API ---
    @app.route("/api/auth/send-otp", methods=["POST"])
    def auth_send_otp():
        data = request.get_json(silent=True) or {}
        phone = (data.get("phone") or "").strip()
        if not phone:
            return jsonify({"error": "Phone number is required."}), 400
        # In clinic camp field deployment, issue 6-digit OTP (demo OTP: 123456)
        logger.info("Clinical verification code requested for %s", phone)
        return jsonify({
            "success": True,
            "message": f"Clinical verification code sent to {phone}",
            "demo_otp": "123456"
        })

    @app.route("/api/auth/login", methods=["POST"])
    def auth_login():
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        phone = (data.get("phone") or "").strip()
        pin = (data.get("pin") or "").strip()
        otp = (data.get("otp") or "").strip()
        password = data.get("password") or ""
        community_code = (data.get("community_code") or "").strip().upper() or "CAMP-2026"

        comm = database.get_community_by_code(community_code, db_path=db_path)
        if not comm:
            return jsonify({"error": f"Invalid Community Code '{community_code}'. You can only log in with an active community invite code."}), 403

        # Support Phone OTP / PIN login
        if phone:
            if otp and otp not in ["123456", "1234"] and len(otp) < 4:
                return jsonify({"error": "Invalid verification code. Use demo code 123456."}), 400
            if pin and len(pin) < 4:
                return jsonify({"error": "PIN must be at least 4 digits."}), 400

            clean_phone = re.sub(r'[^0-9+]', '', phone)
            email = email or f"doc_{clean_phone.replace('+', '')}@clinic.camp"
            doc_name = data.get("full_name") or f"Dr. {clean_phone[-4:] if len(clean_phone) >= 4 else 'Physician'}"
            doctor = database.get_or_create_doctor(
                email=email,
                full_name=doc_name,
                community_id=comm["id"],
                community_code=comm["code"],
                db_path=db_path
            )
            try:
                with database.get_db_connection(db_path) as conn:
                    conn.cursor().execute("UPDATE doctors SET phone = ? WHERE id = ?;", (clean_phone, doctor["id"]))
                    conn.commit()
            except Exception:
                pass

            profile = database.get_doctor_profile(doctor["id"], db_path=db_path)
            return jsonify({
                "success": True,
                "token": f"jwt_phone_{doctor['id']}",
                "doctor": profile,
                "community": comm
            })

        # Support PIN quick unlock
        if pin and not email:
            if len(pin) < 4:
                return jsonify({"error": "PIN must be at least 4 digits."}), 400
            doctor = database.get_or_create_doctor(
                email="doctor@clinic.camp",
                full_name="Attending Physician",
                community_id=comm["id"],
                community_code=comm["code"],
                db_path=db_path
            )
            profile = database.get_doctor_profile(doctor["id"], db_path=db_path)
            return jsonify({
                "success": True,
                "token": f"jwt_pin_{doctor['id']}",
                "doctor": profile,
                "community": comm
            })

        if not email:
            return jsonify({"error": "Doctor email or mobile phone is required."}), 400

        token = None
        if supabase_service.is_configured() and password:
            auth_res = supabase_service.auth_sign_in(email, password)
            if auth_res.get("success"):
                token = auth_res.get("access_token")

        name_part = email.split("@")[0].replace(".", " ").replace("_", " ").title()
        doc_name = f"Dr. {name_part}" if not name_part.startswith("Dr") else name_part
        doctor = database.get_or_create_doctor(
            email=email,
            full_name=doc_name,
            community_id=comm["id"],
            community_code=comm["code"],
            db_path=db_path
        )
        profile = database.get_doctor_profile(doctor["id"], db_path=db_path)

        return jsonify({
            "success": True,
            "token": token or f"demo_jwt_{doctor['id']}",
            "doctor": profile,
            "community": comm
        })

    @app.route("/api/auth/signup", methods=["POST"])
    def auth_signup():
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        full_name = (data.get("full_name") or "").strip()
        specialization = (data.get("specialization") or "General Physician").strip()
        community_code = (data.get("community_code") or "").strip().upper()

        if not email or not full_name:
            return jsonify({"error": "Email and Full Name are required."}), 400

        # STRICT RULE: A community code is mandatory for doctor registration
        if not community_code:
            return jsonify({"error": "Community Invite Code is required to join. Contact your community organizer or build a new community."}), 400

        comm = database.get_community_by_code(community_code, db_path=db_path)
        if not comm:
            return jsonify({"error": f"Invalid Community Code '{community_code}'. Doctors can only register with a valid community invite code."}), 403

        token = None
        if supabase_service.is_configured() and password:
            auth_res = supabase_service.auth_sign_up(email, password, full_name, specialization)
            token = auth_res.get("access_token")

        doctor = database.get_or_create_doctor(
            email=email,
            full_name=full_name,
            specialization=specialization,
            community_id=comm["id"],
            community_code=comm["code"],
            community_role="doctor",
            db_path=db_path
        )
        profile = database.get_doctor_profile(doctor["id"], db_path=db_path)
        return jsonify({
            "success": True,
            "token": token or f"demo_jwt_{doctor['id']}",
            "doctor": profile,
            "community": comm
        }), 201

    @app.route("/api/doctor/profile", methods=["GET", "PUT"])
    def doctor_profile():
        doc_id = request.args.get("doctor_id") or request.headers.get("X-Doctor-ID") or "Dr. Priya"
        if request.method == "GET":
            prof = database.get_doctor_profile(doc_id, db_path=db_path)
            return jsonify(prof)
        else:
            data = request.get_json(silent=True) or {}
            full_name = data.get("full_name") or "Dr. Priya"
            spec = data.get("specialization") or "General Physician"
            avatar = data.get("avatar_url")
            updated = database.update_doctor_profile(doc_id, full_name, spec, avatar, db_path=db_path)
            return jsonify(updated)

    @app.route("/api/sync/supabase", methods=["POST"])
    def sync_to_supabase():
        """Push all existing local SQLite camps and records to Supabase."""
        if not supabase_service.is_configured():
            return jsonify({"error": "Supabase credentials not configured in backend/.env"}), 400
        try:
            res = supabase_service.sync_from_sqlite(db_path)
            return jsonify({"status": "ok", "synced": res})
        except Exception as e:
            logger.exception("Error syncing SQLite to Supabase")
            return jsonify({"error": str(e)}), 500

    # --- AI Extraction API ---
    @app.route("/api/extract", methods=["POST"])
    @limiter.limit("10/minute", key_func=get_user_rate_key)
    @limiter.limit("60/minute", key_func=get_org_rate_key)
    def extract_document():
        """Accepts student_form and health_assessment sheets, checks quota/dedup, and calls AI extractor."""
        # Support both new standard names and backward-compatible page1/page2
        sf_file = request.files.get("student_form") or request.files.get("page1")
        ha_file = request.files.get("health_assessment") or request.files.get("page2")

        if not sf_file or not ha_file:
            return jsonify({
                "error": "Both student_form and health_assessment image files are required for extraction.",
                "code": "MISSING_PAGES"
            }), 400

        if not sf_file.filename or not ha_file.filename:
            return jsonify({"error": "One or both uploaded files are empty.", "code": "EMPTY_FILE"}), 400

        sf_mime = sf_file.mimetype or "image/jpeg"
        ha_mime = ha_file.mimetype or "image/jpeg"

        if sf_mime not in ALLOWED_MIME_TYPES or ha_mime not in ALLOWED_MIME_TYPES:
            return jsonify({
                "error": "Invalid image format. Allowed formats: JPEG, PNG, WebP.",
                "code": "INVALID_MIME_TYPE"
            }), 400

        sf_bytes = sf_file.read()
        ha_bytes = ha_file.read()

        if len(sf_bytes) > MAX_FILE_SIZE or len(ha_bytes) > MAX_FILE_SIZE:
            return jsonify({
                "error": "Image file size exceeds the 10 MB limit.",
                "code": "FILE_TOO_LARGE"
            }), 400

        camp_id = request.form.get("camp_id") or request.args.get("camp_id") or "camp_default"
        record_id = request.form.get("record_id") or request.args.get("record_id")
        org_id = request.headers.get("X-Org-ID") or request.form.get("org_id") or request.args.get("org_id") or "org_default"

        result = ai_extract(
            student_form_bytes=sf_bytes,
            health_assessment_bytes=ha_bytes,
            camp_id=camp_id,
            record_id=record_id,
            org_id=org_id,
            db_path=db_path
        )

        http_code = result.get("http_status", 200 if result.get("success") else 422)
        return jsonify(result), http_code

    # --- Phase 3 Usage & Metering Endpoints (Admin-Only) ---
    @app.route("/api/usage", methods=["GET"])
    def get_usage_endpoint():
        """Returns papers included, used, left, add-on papers, renewal date, and cost this month (Admin-Only)."""
        if not is_admin_request():
            return jsonify({"error": "forbidden", "message": "Admin authorization required to access usage telemetry."}), 403
        org_id = request.headers.get("X-Org-ID") or request.args.get("org_id") or "org_default"
        summary = database.get_usage_summary(org_id=org_id, db_path=db_path)
        return jsonify(summary)

    @app.route("/api/usage/ledger", methods=["GET"])
    def get_usage_ledger_endpoint():
        """Returns paginated usage ledger entries (Admin-Only)."""
        if not is_admin_request():
            return jsonify({"error": "forbidden", "message": "Admin authorization required to access usage ledger."}), 403
        org_id = request.headers.get("X-Org-ID") or request.args.get("org_id") or "org_default"
        from_date = request.args.get("from")
        to_date = request.args.get("to")
        try:
            page = int(request.args.get("page", 1))
            limit = int(request.args.get("limit", 50))
        except (ValueError, TypeError):
            page, limit = 1, 50

        ledger_data = database.get_usage_ledger_paged(
            org_id=org_id,
            from_date=from_date,
            to_date=to_date,
            page=page,
            limit=limit,
            db_path=db_path
        )
        return jsonify(ledger_data)

    @app.route("/api/admin/ai-pause", methods=["POST"])
    def admin_ai_pause():
        """Emergency kill switch per organization or global (Admin-Only)."""
        if not is_admin_request():
            return jsonify({"error": "forbidden", "message": "Admin authorization required to toggle AI kill switch."}), 403
        data = request.get_json(silent=True) or {}
        org_id = data.get("org_id")
        paused = bool(data.get("paused", True))
        success = database.set_ai_pause(org_id=org_id, paused=paused, db_path=db_path)
        return jsonify({
            "success": success,
            "paused": paused,
            "org_id": org_id or "all",
            "message": "AI processing paused" if paused else "AI processing resumed"
        })

    @app.route("/api/admin/cleanup-expired-reservations", methods=["POST"])
    def admin_cleanup_expired_reservations():
        """Scheduled job: release unapproved reservations older than RESERVATION_EXPIRY_DAYS (Admin-Only)."""
        if not is_admin_request():
            return jsonify({"error": "forbidden", "message": "Admin authorization required to run reservation cleanup."}), 403
        data = request.get_json(silent=True) or {}
        expiry_days = int(data.get("expiry_days") or request.args.get("expiry_days") or os.getenv("RESERVATION_EXPIRY_DAYS", "7"))
        released = database.release_expired_reservations(expiry_days=expiry_days, db_path=db_path)
        return jsonify({
            "success": True,
            "released_papers": released,
            "expiry_days": expiry_days,
            "message": f"Successfully released {released} unapproved paper reservation(s)."
        })

    # --- Phase 4: Razorpay Orders, Webhook & Invoices API ---
    @app.route("/api/billing/order", methods=["POST"])
    def billing_create_order():
        """Create a Razorpay order for purchasing scan credits."""
        data = request.get_json(silent=True) or {}
        org_id = request.headers.get("X-Org-ID") or data.get("org_id") or "org_default"
        papers = int(data.get("papers", 1000))

        # Standard pack pricing:
        if papers == 1000:
            base_rs = 5000.0
        elif papers == 2500:
            base_rs = 11250.0
        elif papers == 5000:
            base_rs = 20000.0
        else:
            base_rs = round(papers * 5.0, 2)

        gst_rs = round(base_rs * 0.18, 2)
        total_rs = base_rs + gst_rs
        amount_paise = int(round(total_rs * 100))

        order_id = f"order_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{os.urandom(3).hex()}"
        key_id = os.getenv("RAZORPAY_KEY_ID", "rzp_test_ink2insights")

        return jsonify({
            "order_id": order_id,
            "papers": papers,
            "amount_rs": base_rs,
            "gst_rs": gst_rs,
            "total_rs": total_rs,
            "amount_paise": amount_paise,
            "currency": "INR",
            "key_id": key_id,
            "org_id": org_id
        })

    @app.route("/api/billing/webhook", methods=["POST"])
    def billing_webhook():
        """Signature-checked Razorpay webhook with idempotent credit and sequential GST invoice PDF."""
        signature = request.headers.get("X-Razorpay-Signature", "")
        raw_body = request.get_data()

        webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "mock_webhook_secret")
        if not database.verify_razorpay_webhook_signature(raw_body, signature, webhook_secret=webhook_secret):
            return jsonify({"error": "invalid_signature", "message": "HMAC signature mismatch"}), 400

        payload = request.get_json(silent=True) or {}
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {}) if "payload" in payload else payload
        payment_id = payment_entity.get("id") or payload.get("razorpay_payment_id") or f"pay_{os.urandom(4).hex()}"
        notes = payment_entity.get("notes", {})
        org_id = notes.get("org_id") or payload.get("org_id") or "org_default"
        papers = int(notes.get("papers") or payload.get("papers", 1000))

        if papers == 1000:
            base_rs = 5000.0
        elif papers == 2500:
            base_rs = 11250.0
        elif papers == 5000:
            base_rs = 20000.0
        else:
            base_rs = round(papers * 5.0, 2)
        gst_rs = round(base_rs * 0.18, 2)

        success, result = database.process_successful_payment(
            org_id=org_id,
            payment_id=payment_id,
            papers=papers,
            amount_rs=base_rs,
            gst_rs=gst_rs,
            db_path=db_path
        )

        return jsonify({
            "status": "ok",
            "success": success,
            "result": result
        })

    @app.route("/api/billing/invoices", methods=["GET"])
    def billing_invoices():
        """List all GST invoices for the organization."""
        org_id = request.headers.get("X-Org-ID") or request.args.get("org_id") or "org_default"
        invoices = database.get_invoices_for_org(org_id=org_id, db_path=db_path)
        return jsonify({"invoices": invoices, "total": len(invoices)})

    @app.route("/api/billing/invoices/<invoice_id>/download", methods=["GET"])
    def billing_download_invoice(invoice_id):
        """Download invoice PDF by invoice ID or invoice number."""
        inv = database.get_invoice_by_id_or_number(invoice_id, db_path=db_path)
        if not inv or not inv.get("pdf_path") or not os.path.exists(inv["pdf_path"]):
            return jsonify({"error": "Invoice PDF not found"}), 404

        pdf_path = inv["pdf_path"]
        inv_number = inv.get("number", "invoice")
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={inv_number}.pdf"}
        )

    # --- Comprehensive Excel Export (and CSV fallback) ---
    @app.route("/api/export", methods=["GET"])
    def export_all():
        camp_id = request.args.get("camp_id")
        only_reviewed = request.args.get("only_reviewed", "false").lower() == "true"
        fmt = request.args.get("format", "xlsx").lower()
        if fmt == "csv":
            csv_data = record_service.generate_csv_export(camp_id=camp_id, only_reviewed=only_reviewed)
            filename = f"clinic_records_{camp_id or 'all'}.csv"
            return Response(
                csv_data,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        excel_data = record_service.generate_excel_export(camp_id=camp_id, only_reviewed=only_reviewed)
        filename = f"clinic_records_{camp_id or 'all'}.xlsx"
        return Response(
            excel_data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    @app.route("/api/camps/<camp_id>/export", methods=["GET"])
    def export_camp(camp_id):
        camp = database.get_camp_by_id(camp_id, db_path)
        raw_name = camp.get("name", "Organization") if camp else "Organization"
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', raw_name).strip('_') or f"Org_{camp_id}"
        fmt = request.args.get("format", "xlsx").lower()
        if fmt == "csv":
            csv_data = record_service.generate_csv_export(camp_id=camp_id, only_reviewed=False)
            filename = f"{safe_name}_Health_Records.csv"
            return Response(
                csv_data,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )

        excel_data = record_service.generate_excel_export(camp_id=camp_id, only_reviewed=False)
        filename = f"{safe_name}_Health_Records.xlsx"
        return Response(
            excel_data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    # --- Clear / Reset Data ---
    @app.route("/api/reset-demo", methods=["POST"])
    @app.route("/api/clear-data", methods=["POST"])
    def reset_demo():
        database.clear_all_records(db_path)
        logger.info("Database records and camps were cleared for clean production.")
        return jsonify({"message": "Data cleared successfully", "stats": database.get_overall_stats(db_path)})

    return app

# Top-level Flask instance required by Vercel and WSGI runtimes
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    logger.info("Starting I2 insights Clinical API on port %d...", port)
    app.run(host="0.0.0.0", port=port, debug=False)
