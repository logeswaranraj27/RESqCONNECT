from functools import wraps
from flask import Blueprint, redirect, render_template, request, session, url_for, flash

from app.services.services import AuthRoleService, session_app_database

bp = Blueprint("auth", __name__)


def login_required(roles=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = session.get("user")
            if not user:
                return redirect(url_for("auth.login"))
            if roles and user["role"] not in roles:
                return render_template("error.html", code=403, message="You do not have permission to access this page."), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


@bp.get("/login")
def login():
    if session.get("user"):
        return redirect(url_for("main.dashboard"))
    return render_template("login.html")


@bp.post("/login")
def login_post():
    service = AuthRoleService(session_app_database())
    user = service.authenticate(request.form.get("username", ""), request.form.get("password", ""))
    if not user:
        flash("Invalid username or password.", "error")
        return render_template("login.html"), 401
    service.set_session(user)
    return redirect(url_for("main.dashboard"))


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
