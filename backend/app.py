"""Doc Scanner - Minimal Clinic Demo MVP Flask REST API.

Provides:
- Camp management & dynamic aggregate statistics (zero fake counters).
- Two-page document image processing endpoint with Groq Vision (or provider abstraction).
- Record creation, human-in-the-loop review workflow, and SQLite persistence.
- Secure CSV export with formula injection protection.
- Demo mode data reset and zero patient data leakage.
"""

import os
import sys
import io
import logging
from typing import Tuple, Dict, Any
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load environment variables from .env file if present
load_dotenv(os.path.join(CURRENT_DIR, ".env"))

from backend import database
from backend.services.ai_service import AIService
from backend.services.groq_service import GroqService
from backend.services.record_service import RecordService

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
)
logger = logging.getLogger("doc_scanner")

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/jpg"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB per image

def create_app(db_path: str = database.DEFAULT_DB_PATH) -> Flask:
    """Application factory for Flask API."""
    frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
    app = Flask(__name__, static_folder=frontend_dir, static_url_path="")
    
    # Configure CORS for local development
    CORS(app, resources={r"/api/*": {"origins": ["http://localhost:*", "http://127.0.0.1:*", "http://localhost:3000", "http://localhost:5001"]}})
    
    # Initialize DB tables
    database.init_db(db_path)
    
    # Check if DB has camps; if completely empty, seed demo data once
    existing_camps = database.list_camps_with_stats(db_path)
    if not existing_camps:
        logger.info("Database is empty. Seeding initial synthetic demo data...")
        database.reset_demo_data(db_path)

    record_service = RecordService(db_path)

    def get_ai_service() -> AIService:
        provider = os.getenv("AI_PROVIDER", "groq").lower().strip()
        if provider == "groq":
            return GroqService()
        # Placeholder for NVIDIA NIM extension
        # elif provider == "nvidia":
        #     return NvidiaNimService()
        logger.warning("Unrecognized AI_PROVIDER '%s'; falling back to GroqService", provider)
        return GroqService()

    # --- Static Frontend Routes ---
    @app.route("/")
    def index():
        return send_from_directory(frontend_dir, "index.html")

    @app.route("/<path:filename>")
    def static_files(filename):
        return send_from_directory(frontend_dir, filename)

    # --- API Status & Metrics ---
    @app.route("/api/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "app": "doc-scanner-mvp", "demo_mode": True})

    @app.route("/api/status", methods=["GET"])
    def status():
        ai = get_ai_service()
        stats = database.get_overall_stats(db_path)
        return jsonify({
            "demo_mode": True,
            "demo_mode_label": "DEMO MODE — Synthetic Test Records Only",
            "ai_configured": ai.is_configured(),
            "ai_provider": ai.provider_name(),
            "overall_stats": stats
        })

    @app.route("/api/stats", methods=["GET"])
    def stats():
        return jsonify(database.get_overall_stats(db_path))

    # --- Camps API ---
    @app.route("/api/camps", methods=["GET"])
    def list_camps():
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
        camp = database.create_camp(name, expected, date_str, db_path)
        return jsonify(camp), 201

    @app.route("/api/camps/<camp_id>", methods=["GET"])
    def get_camp(camp_id):
        camp = database.get_camp_by_id(camp_id, db_path)
        if not camp:
            return jsonify({"error": "Camp not found"}), 404
        return jsonify(camp)

    # --- Records API ---
    @app.route("/api/records", methods=["GET"])
    def list_records():
        camp_id = request.args.get("camp_id")
        status_filter = request.args.get("status")
        limit = min(int(request.args.get("limit", 100)), 500)
        recs = database.list_records(camp_id=camp_id, status=status_filter, limit=limit, db_path=db_path)
        return jsonify(recs)

    @app.route("/api/records/<record_id>", methods=["GET"])
    def get_record(record_id):
        rec = database.get_record_by_id(record_id, db_path)
        if not rec:
            return jsonify({"error": "Record not found"}), 404
        return jsonify(rec)

    @app.route("/api/records", methods=["POST"])
    def create_record():
        payload = request.get_json(silent=True) or {}
        try:
            created = record_service.create_record(payload)
            return jsonify(created), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.exception("Error saving record")
            return jsonify({"error": "Failed to persist record"}), 500

    @app.route("/api/records/<record_id>/review", methods=["PUT", "POST"])
    def review_record(record_id):
        payload = request.get_json(silent=True) or {}
        reviewer = payload.get("reviewed_by", "Dr. Priya (Demo)")
        try:
            updated = record_service.review_record(record_id, payload, reviewer)
            return jsonify(updated)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.exception("Error reviewing record")
            return jsonify({"error": "Failed to update record"}), 500

    # --- AI Extraction API ---
    @app.route("/api/extract", methods=["POST"])
    def extract_document():
        """Accepts Page 1 and Page 2 image uploads and calls vision AI extractor."""
        if "page1" not in request.files or "page2" not in request.files:
            return jsonify({
                "error": "Both Page 1 and Page 2 images are required for extraction.",
                "code": "MISSING_PAGES"
            }), 400

        p1_file = request.files["page1"]
        p2_file = request.files["page2"]

        if not p1_file.filename or not p2_file.filename:
            return jsonify({"error": "One or both uploaded files are empty.", "code": "EMPTY_FILE"}), 400

        # Validate MIME types
        p1_mime = p1_file.mimetype or "image/jpeg"
        p2_mime = p2_file.mimetype or "image/jpeg"

        if p1_mime not in ALLOWED_MIME_TYPES or p2_mime not in ALLOWED_MIME_TYPES:
            return jsonify({
                "error": f"Invalid image format. Allowed formats: JPEG, PNG, WebP.",
                "code": "INVALID_MIME_TYPE"
            }), 400

        # Read bytes and validate size
        p1_bytes = p1_file.read()
        p2_bytes = p2_file.read()

        if len(p1_bytes) > MAX_FILE_SIZE or len(p2_bytes) > MAX_FILE_SIZE:
            return jsonify({
                "error": f"Image file size exceeds the 10 MB limit.",
                "code": "FILE_TOO_LARGE"
            }), 400

        ai_service = get_ai_service()
        if not ai_service.is_configured():
            return jsonify({
                "success": False,
                "error": "AI vision provider is not configured. Set GROQ_API_KEY in backend/.env to enable automated AI document transcription.",
                "code": "AI_NOT_CONFIGURED",
                "provider": ai_service.provider_name(),
                "manual_entry_allowed": True
            }), 200

        result = ai_service.extract_from_images(p1_bytes, p1_mime, p2_bytes, p2_mime)
        return jsonify(result), (200 if result.get("success") else 422)

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
        fmt = request.args.get("format", "xlsx").lower()
        if fmt == "csv":
            csv_data = record_service.generate_csv_export(camp_id=camp_id, only_reviewed=False)
            return Response(
                csv_data,
                mimetype="text/csv",
                headers={"Content-Disposition": f"attachment; filename=camp_{camp_id}_records.csv"}
            )

        excel_data = record_service.generate_excel_export(camp_id=camp_id, only_reviewed=False)
        return Response(
            excel_data,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=camp_{camp_id}_records.xlsx"}
        )

    # --- Reset Demo Data ---
    @app.route("/api/reset-demo", methods=["POST"])
    def reset_demo():
        database.reset_demo_data(db_path)
        logger.info("Demo data was successfully reset to default synthetic records.")
        return jsonify({"message": "Demo data reset successfully", "stats": database.get_overall_stats(db_path)})

    return app

if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv("PORT", 5001))
    logger.info("Starting Doc Scanner Clinic Demo API on port %d...", port)
    app.run(host="0.0.0.0", port=port, debug=False)
