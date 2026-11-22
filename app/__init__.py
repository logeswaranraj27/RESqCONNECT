from flask import Flask, render_template, session
from flask_wtf.csrf import CSRFError, CSRFProtect
from dotenv import load_dotenv
import os
import uuid

from app.repositories.database import Database

csrf = CSRFProtect()


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "development-only-secret"),
        DATABASE_URL=os.getenv("DATABASE_URL", "sqlite:///resqconnect.db"),
        ACKNOWLEDGEMENT_TIMEOUT_SECONDS=int(
            os.getenv("DEMO_ACKNOWLEDGEMENT_TIMEOUT_SECONDS")
            or os.getenv("ACKNOWLEDGEMENT_TIMEOUT_SECONDS", "120")
        ),
        DEMO_ACKNOWLEDGEMENT_TIMEOUT_SECONDS=(
            int(os.getenv("DEMO_ACKNOWLEDGEMENT_TIMEOUT_SECONDS"))
            if os.getenv("DEMO_ACKNOWLEDGEMENT_TIMEOUT_SECONDS")
            else None
        ),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    app.extensions["database"] = Database(app.config["DATABASE_URL"])
    app.extensions["database"].initialize()

    csrf.init_app(app)

    if not app.config.get("TESTING") and os.environ.get("WERKZEUG_RUN_MAIN") != "false":
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            scheduler = app.extensions.get("acknowledgement_scheduler")
            if scheduler is None:
                scheduler = BackgroundScheduler(daemon=True)
                app.extensions["acknowledgement_scheduler"] = scheduler

                def check_acknowledgements():
                    with app.app_context():
                        from app.services.services import AlertCoordinationService
                        AlertCoordinationService(app.extensions["database"]).escalate_expired(
                            app.config["ACKNOWLEDGEMENT_TIMEOUT_SECONDS"]
                        )

                scheduler.add_job(check_acknowledgements, "interval", seconds=5, id="acknowledgement-escalation",
                                  replace_existing=True)
                scheduler.start()
        except RuntimeError:
            pass

    from app.routes.main import bp as main_bp
    from app.routes.auth import bp as auth_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    @app.before_request
    def run_database_backed_escalation_check():
        if app.config.get("TESTING"):
            return
        from app.services.services import AlertCoordinationService
        AlertCoordinationService(app.extensions["database"]).escalate_expired(
            app.config["ACKNOWLEDGEMENT_TIMEOUT_SECONDS"]
        )

    @app.errorhandler(400)
    def handle_bad_request(e):
        return render_template(
            "error.html", code=400, title="Bad Request",
            message="Your session changed. Reload the page and try again."
        ), 400

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        return render_template(
            "error.html", code=400, title="Session Expired",
            message="Your session changed. Reload the page and try again."
        ), 400

    @app.errorhandler(403)
    def handle_forbidden(e):
        return render_template(
            "error.html", code=403, title="Forbidden",
            message="You do not have permission to access this page."
        ), 403

    @app.errorhandler(404)
    def handle_not_found(e):
        return render_template(
            "error.html", code=404, title="Page Not Found",
            message="The requested page or resource could not be found."
        ), 404

    @app.errorhandler(405)
    def handle_method_not_allowed(e):
        return render_template(
            "error.html", code=405, title="Method Not Allowed",
            message="The requested HTTP method is not allowed for this route."
        ), 405

    @app.errorhandler(500)
    def handle_internal_server_error(e):
        request_id = uuid.uuid4().hex[:8]
        app.logger.error(f"[Req {request_id}] Internal server error: {e}", exc_info=True)
        return render_template(
            "error.html", code=500, title="Internal Server Error",
            message=f"An unexpected server error occurred. (Request ID: {request_id})"
        ), 500

    @app.context_processor
    def global_template_values():
        from flask import session
        return {
            "current_user": session.get("user"),
            "demo_mode": app.config.get("DEMO_MODE", True),
            "demo_password": os.getenv("DEMO_PASSWORD", "demo1234"),
        }

    return app
