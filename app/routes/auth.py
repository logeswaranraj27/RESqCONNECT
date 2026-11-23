from functools import wraps
from flask import Blueprint, redirect, render_template, request, session, url_for, flash, abort

from app.repositories.repositories import UserRepository
from app.services.services import AuthRoleService, session_app_database

bp = Blueprint("auth", __name__)


def login_required(roles=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = session.get("user")
            if not user:
                return redirect(url_for("auth.login"))
            if roles and user.get("role") not in roles:
                abort(403)
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
    identifier = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    remember_me = request.form.get("remember_me")

    user, err = service.authenticate(identifier, password)
    if err or not user:
        flash(err or "Invalid username or password.", "error")
        return render_template("login.html", username=identifier), 401

    service.set_session(user)
    if remember_me:
        session.permanent = True

    if user.get("must_change_password"):
        flash("For security, please update your initial password.", "info")
        return redirect(url_for("auth.change_password_required"))

    return redirect(url_for("main.dashboard"))


@bp.get("/register")
def register():
    if session.get("user"):
        return redirect(url_for("main.dashboard"))
    return render_template("register.html")


@bp.post("/register")
def register_post():
    service = AuthRoleService(session_app_database())
    ip = request.remote_addr or "127.0.0.1"
    try:
        user = service.register(
            full_name=request.form.get("full_name"),
            username=request.form.get("username"),
            email=request.form.get("email"),
            contact_number=request.form.get("contact_number"),
            password=request.form.get("password"),
            confirm_password=request.form.get("confirm_password"),
            honeypot=request.form.get("website", ""),
            ip=ip,
        )
        service.set_session(user)
        flash(f"Account created successfully. Welcome, {user['display_name']}!", "success")
        return redirect(url_for("main.dashboard"))
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template(
            "register.html",
            full_name=request.form.get("full_name"),
            username=request.form.get("username"),
            email=request.form.get("email"),
            contact_number=request.form.get("contact_number"),
        ), 400


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.get("/account")
@login_required()
def account():
    db = session_app_database()
    user = UserRepository(db).get(session["user"]["id"])
    return render_template("account.html", user=user)


@bp.post("/account/profile")
@login_required()
def update_profile():
    db = session_app_database()
    service = AuthRoleService(db)
    user_id = session["user"]["id"]
    try:
        service.update_profile(
            user_id=user_id,
            display_name=request.form.get("display_name"),
            email=request.form.get("email"),
            contact_number=request.form.get("contact_number"),
        )
        session["user"]["display_name"] = request.form.get("display_name").strip()
        flash("Your profile has been updated.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("auth.account"))


@bp.post("/account/password")
@login_required()
def change_password():
    db = session_app_database()
    service = AuthRoleService(db)
    user_id = session["user"]["id"]
    try:
        service.change_password(
            user_id=user_id,
            current_password=request.form.get("current_password"),
            new_password=request.form.get("new_password"),
            confirm_password=request.form.get("confirm_password"),
        )
        flash("Your password has been changed successfully.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("auth.account"))


@bp.get("/forgot-password")
def forgot_password():
    return render_template("forgot_password.html")


@bp.post("/forgot-password")
def forgot_password_post():
    db = session_app_database()
    service = AuthRoleService(db)
    identifier = request.form.get("identifier", "").strip()
    raw_token, email = service.request_password_reset(identifier)
    if raw_token:
        reset_link = url_for("auth.reset_password", token=raw_token, _external=True)
        return render_template("forgot_password.html", simulated_link=reset_link, email=email)
    flash("If that account exists, a password reset link has been generated.", "info")
    return render_template("forgot_password.html")


@bp.get("/reset-password/<token>")
def reset_password(token):
    return render_template("reset_password.html", token=token)


@bp.post("/reset-password/<token>")
def reset_password_post(token):
    db = session_app_database()
    service = AuthRoleService(db)
    try:
        service.reset_password_with_token(
            raw_token=token,
            new_password=request.form.get("password"),
            confirm_password=request.form.get("confirm_password"),
        )
        flash("Password reset successfully. Please log in with your new password.", "success")
        return redirect(url_for("auth.login"))
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template("reset_password.html", token=token), 400


@bp.get("/change-password-required")
@login_required()
def change_password_required():
    return render_template("change_password_required.html")


@bp.post("/change-password-required")
@login_required()
def change_password_required_post():
    db = session_app_database()
    service = AuthRoleService(db)
    user_id = session["user"]["id"]
    try:
        service.change_password(
            user_id=user_id,
            current_password=request.form.get("current_password"),
            new_password=request.form.get("new_password"),
            confirm_password=request.form.get("confirm_password"),
        )
        session["must_change_password"] = False
        flash("Password updated successfully. You now have full access.", "success")
        return redirect(url_for("main.dashboard"))
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template("change_password_required.html"), 400
