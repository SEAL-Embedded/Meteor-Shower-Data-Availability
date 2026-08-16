"""Flask application factory.

Cross-origin access is off unless configured. The published front end lives on a different origin to
this API, so the origin it will be served from has to be named in ``[api] allowed_origins`` -- an
allow-anything default would quietly expose whatever else this machine ends up serving.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .api import DEFAULT_REFRESH_S, StoreCache, create_api
from .config import Config

API_PREFIX = "/api/v1"


def create_app(
    config: Config,
    *,
    refresh_s: float | None = None,
    web_dir: Path | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["AVAILABILITY_CONFIG"] = config

    cache = StoreCache(config, refresh_s if refresh_s is not None else DEFAULT_REFRESH_S)
    app.extensions["availability_cache"] = cache
    app.register_blueprint(create_api(cache), url_prefix=API_PREFIX)

    allowed = set(config.allowed_origins)
    allow_any = "*" in allowed

    @app.after_request
    def apply_cors(response):
        origin = request.headers.get("Origin")
        if origin and (allow_any or origin in allowed):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers.add("Vary", "Origin")
        return response

    @app.errorhandler(404)
    def not_found(_error):
        return jsonify({"error": "not_found", "message": "No such resource"}), 404

    @app.errorhandler(500)
    def server_error(_error):  # pragma: no cover - exercised only on unexpected faults
        return jsonify({"error": "server_error", "message": "Unhandled server error"}), 500

    if web_dir is not None:
        _mount_web(app, web_dir)

    return app


def _mount_web(app: Flask, web_dir: Path) -> None:
    """Serve the static front end alongside the API, for local development."""
    root = web_dir.resolve()

    @app.get("/")
    def web_root():
        return send_from_directory(root, "index.html")

    @app.get("/<path:filename>")
    def web_file(filename: str):
        return send_from_directory(root, filename)
