import sys
import os
import traceback

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

backend_dir = os.path.join(PROJECT_ROOT, "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

try:
    from backend.app import app
except Exception as e:
    err_tb = traceback.format_exc()
    from flask import Flask, Response, jsonify
    app = Flask(__name__)
    
    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def handle_serverless_init_error(path):
        return Response(
            f"<h2>I2 Insights Serverless Init Error</h2>"
            f"<p>A module failed during serverless startup:</p>"
            f"<pre style='background:#f1f5f9;color:#b91c1c;padding:16px;border-radius:8px;'>{err_tb}</pre>",
            mimetype="text/html",
            status=500
        )
