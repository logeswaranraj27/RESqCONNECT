from flask import Flask
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
import os

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

    @app.context_processor
    def global_template_values():
        from flask import session
        return {
            "current_user": session.get("user"),
            "demo_mode": app.config.get("DEMO_MODE", True),
            "demo_password": os.getenv("DEMO_PASSWORD", "demo1234"),
        }

    return app
