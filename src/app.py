"""
Flask Application Factory
=========================

Main application with proper error handling and logging.
"""

from __future__ import annotations

import os

from flask import Flask
from markupsafe import Markup
import markdown as md

from src.blueprints import (
    api_bp,
    dashboard_bp,
    followups_bp,
    history_bp,
    kanban_bp,
    main_bp,
    prospects_bp,
)
from src.config import get_settings


# Markdown extensions
MARKDOWN_EXTENSIONS = ["nl2br", "tables", "fenced_code", "sane_lists"]


def create_app() -> Flask:
    """
    Application factory.
    
    Returns:
        Configured Flask application.
    """
    settings = get_settings()
    
    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static"
    )
    
    # Configuration
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["DEBUG"] = settings.debug
    app.config["JSON_SORT_KEYS"] = False
    
    # Register Jinja2 filters
    @app.template_filter('markdown')
    def markdown_filter(text):
        """Convert Markdown text to HTML."""
        if not text:
            return ""
        html = md.markdown(text, extensions=MARKDOWN_EXTENSIONS)
        return Markup(html)
    
    # Register blueprints
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(kanban_bp)
    app.register_blueprint(followups_bp)
    app.register_blueprint(prospects_bp)
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(e):
        return {"error": True, "message": "Not found"}, 404
    
    @app.errorhandler(500)
    def internal_error(e):
        return {"error": True, "message": "Internal error"}, 500
    
    # Health check
    @app.route("/health")
    def health():
        return {"status": "healthy", "service": "prospector-ui"}
    
    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    port = int(os.environ.get("PORT", settings.port))
    app.run(host="0.0.0.0", port=port, debug=settings.debug)
