import re
import secrets
import uuid
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path

import click
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from PIL import Image, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from database import close_db, get_db, init_db

csrf = CSRFProtect()
CATEGORIES = ["Electrical", "Plumbing", "Cleaning", "Furniture", "Infrastructure", "Other"]
PRIORITIES = ["Low", "Medium", "High"]
STATUSES = ["Pending", "In Progress", "Resolved"]
ALLOWED_IMAGE_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if app.config["APP_ENV"] == "production" and (
        app.config["SECRET_KEY"] == "campusfix-local-development-only"
        or len(app.config["SECRET_KEY"]) < 32
    ):
        raise RuntimeError("Set SECRET_KEY to a unique value in production.")
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    csrf.init_app(app)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()

    @app.context_processor
    def template_globals():
        return {"categories": CATEGORIES, "priorities": PRIORITIES, "statuses": STATUSES}

    @app.get("/health")
    def health():
        try:
            db = get_db()
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            tables = {
                row["name"]
                for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            required_tables = {"users", "complaints", "complaint_history", "admin_notes"}
            if integrity != "ok" or not required_tables.issubset(tables):
                return {"status": "unhealthy"}, 503
        except Exception:
            app.logger.exception("Health check database query failed")
            return {"status": "unhealthy"}, 503
        return {"status": "healthy"}, 200

    @app.get("/")
    def dashboard():
        db = get_db()
        counts = {
            row["status"]: row["total"]
            for row in db.execute("SELECT status, COUNT(*) AS total FROM complaints GROUP BY status")
        }
        stats = {
            "total": sum(counts.values()),
            "pending": counts.get("Pending", 0),
            "progress": counts.get("In Progress", 0),
            "resolved": counts.get("Resolved", 0),
        }
        category_rows = db.execute(
            "SELECT category, COUNT(*) AS total FROM complaints GROUP BY category ORDER BY total DESC"
        ).fetchall()
        recent = db.execute(
            "SELECT ticket_id, title, category, priority, status, created_at "
            "FROM complaints ORDER BY id DESC LIMIT 8"
        ).fetchall()
        return render_template("dashboard.html", stats=stats, categories_summary=category_rows, recent=recent)

    @app.route("/complaints/new", methods=["GET", "POST"])
    def submit_complaint():
        if request.method == "GET":
            return render_template("submit_complaint.html")
        values = {
            "student_name": request.form.get("student_name", "").strip(),
            "student_email": request.form.get("student_email", "").strip().lower(),
            "student_id": request.form.get("student_id", "").strip(),
            "location": request.form.get("location", "").strip(),
            "category": request.form.get("category", "").strip(),
            "priority": request.form.get("priority", "").strip(),
            "title": request.form.get("title", "").strip(),
            "description": request.form.get("description", "").strip(),
        }
        errors = validate_complaint(values)
        image = request.files.get("attachment")
        attachment_name = None
        saved_path = None
        if image and image.filename:
            try:
                attachment_name, saved_path = save_image(image, app.config["UPLOAD_FOLDER"])
            except ValueError as error:
                errors["attachment"] = str(error)
        if errors:
            if saved_path:
                Path(saved_path).unlink(missing_ok=True)
            return render_template("submit_complaint.html", values=values, errors=errors), 400

        ticket_id = "CF-" + secrets.token_hex(5).upper()
        created_at = utc_now()
        db = get_db()
        try:
            cursor = db.execute(
                "INSERT INTO complaints (ticket_id, student_name, student_email, student_id, "
                "location, category, priority, title, description, attachment_name, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?)",
                (
                    ticket_id, values["student_name"], values["student_email"] or None,
                    values["student_id"] or None, values["location"], values["category"],
                    values["priority"], values["title"], values["description"], attachment_name, created_at,
                ),
            )
            db.execute(
                "INSERT INTO complaint_history (complaint_id, status, changed_at) VALUES (?, ?, ?)",
                (cursor.lastrowid, "Pending", created_at),
            )
            db.commit()
        except Exception:
            db.rollback()
            if saved_path:
                Path(saved_path).unlink(missing_ok=True)
            app.logger.exception("Could not save complaint")
            flash("We could not save your report. Please try again.", "error")
            return render_template("submit_complaint.html", values=values), 500
        flash(f"Complaint submitted. Your ticket ID is {ticket_id}.", "success")
        return redirect(url_for("track_complaint", ticket_id=ticket_id))

    @app.route("/track", methods=["GET", "POST"])
    def track_complaint():
        if request.method == "GET":
            tickets = session.get("verified_tickets", [])
            complaints = query_student_complaints(request.args, tickets) if tickets else []
            return render_template(
                "complaints.html", ticket_id=request.args.get("ticket_id", ""), complaints=complaints
            )
        ticket_id = request.form.get("ticket_id", "").strip().upper()
        identifier = request.form.get("identifier", "").strip()
        if not ticket_id and identifier:
            rows = get_db().execute(
                "SELECT ticket_id FROM complaints WHERE "
                "(student_email = ? COLLATE NOCASE OR student_id = ?) ORDER BY id DESC LIMIT 50",
                (identifier, identifier),
            ).fetchall()
            if not rows:
                flash("No requests matched that email or student ID.", "error")
                return render_template("complaints.html"), 404
            session["verified_tickets"] = [row["ticket_id"] for row in rows]
            return redirect(url_for("track_complaint"))
        complaint = get_db().execute(
            "SELECT id, student_email, student_id FROM complaints WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        matches_email = bool(complaint and complaint["student_email"] and secrets.compare_digest(
            complaint["student_email"].lower(), identifier.lower()))
        matches_id = bool(complaint and complaint["student_id"] and secrets.compare_digest(
            complaint["student_id"], identifier))
        if not (matches_email or matches_id):
            flash("We could not verify that ticket and student identifier.", "error")
            return render_template("complaints.html", ticket_id=ticket_id), 401
        verified = set(session.get("verified_tickets", []))
        verified.add(ticket_id)
        session["verified_tickets"] = sorted(verified)[-20:]
        return redirect(url_for("complaint_detail", ticket_id=ticket_id))

    @app.get("/complaints/<ticket_id>")
    def complaint_detail(ticket_id):
        if ticket_id.upper() not in session.get("verified_tickets", []):
            return redirect(url_for("track_complaint"))
        complaint = get_db().execute(
            "SELECT * FROM complaints WHERE ticket_id = ?", (ticket_id.upper(),)
        ).fetchone()
        if complaint is None:
            abort(404)
        history = get_db().execute(
            "SELECT status, changed_at FROM complaint_history WHERE complaint_id = ? ORDER BY id",
            (complaint["id"],),
        ).fetchall()
        return render_template("complaint_detail.html", complaint=complaint, history=history)

    @app.get("/complaints/<ticket_id>/attachment")
    def complaint_attachment(ticket_id):
        if ticket_id.upper() not in session.get("verified_tickets", []):
            abort(404)

        row = get_db().execute(
            "SELECT attachment_name FROM complaints WHERE ticket_id = ?",
            (ticket_id.upper(),)
        ).fetchone()

        if row is None or not row["attachment_name"]:
            abort(404)

        response = send_from_directory(
            app.config["UPLOAD_FOLDER"],
            row["attachment_name"],
            mimetype="image/" + row["attachment_name"].rsplit(".", 1)[-1].lower()
        )
        return response

    @app.get("/uploads/<path:filename>")
    def uploaded_file(filename):
        if not session.get("admin_id"):
            abort(404)
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)

    @app.get("/admin/attachments/<path:filename>")
    @admin_required
    def admin_attachment(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if session.get("admin_id"):
            return redirect(url_for("admin_dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            admin = get_db().execute(
                "SELECT id, email, password_hash FROM users WHERE email = ? AND role = 'admin'", (email,)
            ).fetchone()
            if admin and check_password_hash(admin["password_hash"], password):
                session.clear()
                session["admin_id"] = admin["id"]
                session["admin_email"] = admin["email"]
                flash("Signed in to the administrator workspace.", "success")
                return redirect(url_for("admin_dashboard"))
            flash("Email or password was not recognized.", "error")
        return render_template("login.html")

    @app.post("/admin/logout")
    @admin_required
    def admin_logout():
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("admin_login"))

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        db = get_db()
        stats = {
            "total": db.execute("SELECT COUNT(*) FROM complaints").fetchone()[0],
            "pending": db.execute("SELECT COUNT(*) FROM complaints WHERE status = 'Pending'").fetchone()[0],
            "progress": db.execute("SELECT COUNT(*) FROM complaints WHERE status = 'In Progress'").fetchone()[0],
            "resolved": db.execute("SELECT COUNT(*) FROM complaints WHERE status = 'Resolved'").fetchone()[0],
        }
        complaints = query_complaints(request.args)
        return render_template("admin_dashboard.html", stats=stats, complaints=complaints)

    @app.get("/admin/complaints/<int:complaint_id>")
    @admin_required
    def admin_complaint_detail(complaint_id):
        complaint = get_db().execute("SELECT * FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if complaint is None:
            abort(404)
        history = get_db().execute(
            "SELECT status, changed_at FROM complaint_history WHERE complaint_id = ? ORDER BY id",
            (complaint_id,),
        ).fetchall()
        notes = get_db().execute(
            "SELECT note, created_at FROM admin_notes WHERE complaint_id = ? ORDER BY id DESC",
            (complaint_id,),
        ).fetchall()
        return render_template("admin_complaint_detail.html", complaint=complaint, history=history, notes=notes)

    @app.post("/admin/complaints/<int:complaint_id>/update")
    @admin_required
    def admin_update_complaint(complaint_id):
        status = request.form.get("status", "")
        if status not in STATUSES:
            flash("Select a valid status.", "error")
            return redirect(url_for("admin_complaint_detail", complaint_id=complaint_id))
        assigned_staff = request.form.get("assigned_staff", "").strip()[:120]
        internal_note = request.form.get("internal_note", "").strip()[:4000]
        resolution_comment = request.form.get("resolution_comment", "").strip()[:2000]
        if status == "Resolved" and not resolution_comment:
            flash("Add a public resolution comment before resolving this complaint.", "error")
            return redirect(url_for("admin_complaint_detail", complaint_id=complaint_id))
        db = get_db()
        current = db.execute("SELECT status FROM complaints WHERE id = ?", (complaint_id,)).fetchone()
        if current is None:
            abort(404)
        changed_at = utc_now()
        db.execute(
            "UPDATE complaints SET status = ?, assigned_staff = ?, resolution_comment = ? WHERE id = ?",
            (status, assigned_staff or None, resolution_comment or None, complaint_id),
        )
        if current["status"] != status:
            db.execute(
                "INSERT INTO complaint_history (complaint_id, status, changed_by, changed_at) VALUES (?, ?, ?, ?)",
                (complaint_id, status, session["admin_id"], changed_at),
            )
        if internal_note:
            db.execute(
                "INSERT INTO admin_notes (complaint_id, admin_id, note, created_at) VALUES (?, ?, ?, ?)",
                (complaint_id, session["admin_id"], internal_note, changed_at),
            )
        db.commit()
        flash("Complaint updated.", "success")
        return redirect(url_for("admin_complaint_detail", complaint_id=complaint_id))

    @app.errorhandler(CSRFError)
    def csrf_error(_error):
        return render_template("error.html", code=400, message="Your form session expired. Refresh and try again."), 400

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def server_error(error):
        app.logger.error("Unhandled server error: %s", error)
        return render_template("500.html"), 500

    @app.cli.command("create-admin")
    def create_admin():
        """Create an administrator using an interactive password prompt."""
        email = click.prompt("Administrator email").strip().lower()
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        if not valid_email(email):
            raise click.ClickException("Enter a valid email address.")
        if len(password) < 12:
            raise click.ClickException("Password must contain at least 12 characters.")
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
                (email, generate_password_hash(password), utc_now()),
            )
            db.commit()
        except Exception as error:
            db.rollback()
            raise click.ClickException("Could not create administrator; email may already exist.") from error
        click.echo("Administrator created.")

    return app


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def validate_complaint(values):
    errors = {}
    for field, label, limit in [
        ("student_name", "Name", 100), ("student_email", "Email", 254),
        ("student_id", "Student ID", 80), ("location", "Location", 180),
        ("title", "Title", 160), ("description", "Description", 4000),
    ]:
        value = values[field]
        if field == "student_email" and not value:
            errors[field] = "College email is required."
        elif field != "student_id" and not value:
            errors[field] = f"{label} is required."
        if len(value) > limit:
            errors[field] = f"{label} must be at most {limit} characters."
    if values["student_email"] and not valid_email(values["student_email"]):
        errors["student_email"] = "Enter a valid email address."
    if values["category"] not in CATEGORIES:
        errors["category"] = "Select a valid complaint category."
    if values["priority"] not in PRIORITIES:
        errors["priority"] = "Select a valid priority."
    return errors


def valid_email(value):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def save_image(upload, upload_folder):
    payload = upload.read(MAX_IMAGE_BYTES + 1)
    if len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("Image must be 5 MB or smaller.")
    try:
        with Image.open(BytesIO(payload)) as image:
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            image_format = image.format
            if image_format not in ALLOWED_IMAGE_FORMATS:
                raise ValueError("Use a JPG, PNG, or WebP image.")
            image = image.convert("RGB") if image_format == "JPEG" else image.copy()
            filename = f"{uuid.uuid4().hex}.{ALLOWED_IMAGE_FORMATS[image_format]}"
            path = Path(upload_folder) / filename
            if image_format == "WEBP":
                image.save(path, format=image_format, method=6)
            else:
                image.save(path, format=image_format, optimize=True)
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("The uploaded file is not a valid image.") from error
    return filename, path


def query_complaints(args):
    query = "SELECT * FROM complaints WHERE 1 = 1"
    params = []
    search = args.get("search", "").strip()[:120]
    category = args.get("category", "").strip()
    priority = args.get("priority", "").strip()
    status = args.get("status", "").strip()
    if search:
        query += " AND (ticket_id LIKE ? OR title LIKE ? OR student_name LIKE ?)"
        params.extend([f"%{search}%"] * 3)
    if category in CATEGORIES:
        query += " AND category = ?"
        params.append(category)
    if priority in PRIORITIES:
        query += " AND priority = ?"
        params.append(priority)
    if status in STATUSES:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC"
    return get_db().execute(query, params).fetchall()


def query_student_complaints(args, tickets):
    tickets = [ticket for ticket in tickets if re.fullmatch(r"CF-[A-F0-9]{10}", ticket)]
    if not tickets:
        return []
    placeholders = ",".join("?" for _ in tickets)
    query = (
        "SELECT ticket_id, title, category, priority, status, created_at "
        f"FROM complaints WHERE ticket_id IN ({placeholders})"
    )
    params = list(tickets)
    search = args.get("search", "").strip()[:120]
    category = args.get("category", "").strip()
    priority = args.get("priority", "").strip()
    status = args.get("status", "").strip()
    if search:
        query += " AND (ticket_id LIKE ? OR title LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if category in CATEGORIES:
        query += " AND category = ?"
        params.append(category)
    if priority in PRIORITIES:
        query += " AND priority = ?"
        params.append(priority)
    if status in STATUSES:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    return get_db().execute(query, params).fetchall()


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)