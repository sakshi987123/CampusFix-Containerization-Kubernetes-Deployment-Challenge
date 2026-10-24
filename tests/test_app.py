from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app
from database import get_db


@pytest.fixture
def app(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    application = create_app({
        "TESTING": True,
        "SECRET_KEY": "pytest-only-secret",
        "DATABASE_PATH": str(instance / "test.db"),
        "UPLOAD_FOLDER": str(instance / "uploads"),
        "WTF_CSRF_ENABLED": False,
    })
    with application.app_context():
        get_db().execute(
            "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
            ("admin@campus.test", generate_password_hash("a-strong-test-password"), "2026-01-01T00:00:00+00:00"),
        )
        get_db().commit()
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def submit(client, **overrides):
    values = {
        "student_name": "Taylor Student",
        "student_email": "taylor@campus.test",
        "student_id": "S-2048",
        "location": "Science Hall, Room 204",
        "category": "Electrical",
        "priority": "High",
        "title": "Lab lights are flickering",
        "description": "The lights above the lab benches flicker throughout the day.",
    }
    values.update(overrides)
    response = client.post("/complaints/new", data=values)
    return response, values


def ticket_from_response(response):
    return parse_qs(urlparse(response.headers["Location"]).query)["ticket_id"][0]


def login(client):
    return client.post("/admin/login", data={
        "email": "admin@campus.test",
        "password": "a-strong-test-password",
    }, follow_redirects=True)


def test_homepage_and_dashboard_use_database_counts(client):
    response, _ = submit(client)
    assert response.status_code == 302
    page = client.get("/")
    assert page.status_code == 200
    assert b"Dashboard" in page.data
    assert b">1<" in page.data
    assert b"Lab lights are flickering" in page.data


def test_complaint_submission_generates_ticket_and_history(client, app):
    response, values = submit(client)
    ticket_id = ticket_from_response(response)
    assert ticket_id.startswith("CF-")
    with app.app_context():
        complaint = get_db().execute(
            "SELECT * FROM complaints WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        history = get_db().execute(
            "SELECT status FROM complaint_history WHERE complaint_id = ?", (complaint["id"],)
        ).fetchall()
    assert complaint["student_email"] == values["student_email"]
    assert complaint["status"] == "Pending"
    assert [row["status"] for row in history] == ["Pending"]


def test_submission_rejects_missing_contact_and_bad_category(client):
    response, _ = submit(client, student_email="", category="Network")
    assert response.status_code == 400
    assert b"College email is required" in response.data
    assert b"Select a valid complaint category" in response.data


def test_submission_rejects_invalid_email_even_with_student_id(client):
    response, _ = submit(client, student_email="not-an-email", student_id="S-2048")
    assert response.status_code == 400
    assert b"Enter a valid email address" in response.data


def test_submission_accepts_verified_image_attachment(client, app):
    image_buffer = BytesIO()
    Image.new("RGB", (3, 3), color="red").save(image_buffer, format="WEBP")
    image_buffer.seek(0)
    response = client.post("/complaints/new", data={
        "student_name": "Taylor Student",
        "student_email": "taylor@campus.test",
        "location": "Science Hall, Room 204",
        "category": "Electrical",
        "priority": "High",
        "title": "Image attached",
        "description": "A valid image is attached.",
        "attachment": (image_buffer, "photo.webp", "image/webp"),
    })
    assert response.status_code == 302
    ticket_id = ticket_from_response(response)
    with app.app_context():
        filename = get_db().execute(
            "SELECT attachment_name FROM complaints ORDER BY id DESC LIMIT 1"
        ).fetchone()["attachment_name"]
    assert filename.endswith(".webp")
    assert (Path(app.config["UPLOAD_FOLDER"]) / filename).is_file()
    assert client.get(f"/complaints/{ticket_id}/attachment").status_code == 404
    client.post("/track", data={"ticket_id": ticket_id, "identifier": "taylor@campus.test"})
    attachment = client.get(f"/complaints/{ticket_id}/attachment")
    assert attachment.status_code == 200
    assert attachment.mimetype == "image/webp"


def test_submission_rejects_non_image_attachment(client):
    response = client.post("/complaints/new", data={
        "student_name": "Taylor Student",
        "student_email": "taylor@campus.test",
        "location": "Science Hall, Room 204",
        "category": "Electrical",
        "priority": "High",
        "title": "Bad attachment",
        "description": "This is not an image.",
        "attachment": (BytesIO(b"not an image"), "payload.exe", "application/octet-stream"),
    })
    assert response.status_code == 400
    assert b"not a valid image" in response.data


def test_student_tracking_requires_matching_identifier_and_hides_internal_notes(client, app):
    response, _ = submit(client)
    ticket_id = ticket_from_response(response)
    denied = client.post("/track", data={"ticket_id": ticket_id, "identifier": "wrong@campus.test"})
    assert denied.status_code == 401
    assert b"could not verify" in denied.data
    verified = client.post("/track", data={"ticket_id": ticket_id, "identifier": "taylor@campus.test"})
    assert verified.status_code == 302
    detail = client.get(verified.headers["Location"])
    assert detail.status_code == 200
    assert b"Taylor Student" in detail.data
    assert b"PRIVATE ADMIN NOTES" not in detail.data


def test_student_can_find_and_filter_all_matching_requests(client):
    first_response, _ = submit(client, title="Sink leak", category="Plumbing", priority="Low")
    ticket_id = ticket_from_response(first_response)
    submit(client, title="Hallway light", category="Electrical", priority="Medium")
    response = client.post(
        "/track", data={"ticket_id": "", "identifier": "taylor@campus.test"}, follow_redirects=True
    )
    assert response.status_code == 200
    assert b"Sink leak" in response.data
    assert b"Hallway light" in response.data
    filtered = client.get("/track?search=Sink&category=Plumbing")
    assert filtered.status_code == 200
    assert b"Sink leak" in filtered.data
    assert b"Hallway light" not in filtered.data
    ticket_search = client.get(f"/track?search={ticket_id}")
    assert ticket_search.status_code == 200
    assert b"Sink leak" in ticket_search.data
    assert b"Hallway light" not in ticket_search.data


def test_invalid_ticket_id_returns_not_found(client):
    with client.session_transaction() as session:
        session["verified_tickets"] = ["CF-UNKNOWN"]
    response = client.get("/complaints/CF-UNKNOWN")
    assert response.status_code == 404


def test_admin_login_and_admin_dashboard(client):
    assert client.get("/admin").status_code == 302
    response = login(client)
    assert response.status_code == 200
    assert b"Complaint management" in response.data
    assert b"admin@campus.test" in response.data


def test_admin_complaint_filters(client):
    submit(client, title="Broken sink", category="Plumbing", priority="Low")
    submit(client, title="Flickering lights", category="Electrical", priority="High")
    login(client)
    response = client.get("/admin?category=Plumbing&priority=Low")
    assert response.status_code == 200
    assert b"Broken sink" in response.data
    assert b"Flickering lights" not in response.data


def test_unauthorized_admin_update_does_not_change_status(client, app):
    response, _ = submit(client)
    ticket_id = ticket_from_response(response)
    with app.app_context():
        complaint_id = get_db().execute(
            "SELECT id FROM complaints WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()["id"]
    unauthorized = client.post(
        f"/admin/complaints/{complaint_id}/update",
        data={"status": "Resolved", "resolution_comment": "Done"},
    )
    assert unauthorized.status_code == 302
    assert "/admin/login" in unauthorized.headers["Location"]
    with app.app_context():
        status = get_db().execute(
            "SELECT status FROM complaints WHERE id = ?", (complaint_id,)
        ).fetchone()["status"]
    assert status == "Pending"


def test_admin_status_update_history_notes_and_public_resolution(client, app):
    response, _ = submit(client)
    ticket_id = ticket_from_response(response)
    login(client)
    with app.app_context():
        complaint_id = get_db().execute(
            "SELECT id FROM complaints WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()["id"]
    updated = client.post(
        f"/admin/complaints/{complaint_id}/update",
        data={
            "status": "Resolved",
            "assigned_staff": "Morgan Facilities",
            "resolution_comment": "Replaced the faulty fixture.",
            "internal_note": "Parts charge recorded internally.",
        },
        follow_redirects=True,
    )
    assert updated.status_code == 200
    assert b"Parts charge recorded internally" in updated.data
    with app.app_context():
        rows = get_db().execute(
            "SELECT status, changed_at FROM complaint_history WHERE complaint_id = ? ORDER BY id",
            (complaint_id,),
        ).fetchall()
        assert [row["status"] for row in rows] == ["Pending", "Resolved"]
        assert all(row["changed_at"] for row in rows)
    client.post("/track", data={"ticket_id": ticket_id, "identifier": "S-2048"})
    public_detail = client.get(f"/complaints/{ticket_id}")
    assert b"Replaced the faulty fixture" in public_detail.data
    assert b"Parts charge recorded internally" not in public_detail.data


def test_health_endpoint_checks_database(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "healthy"}


def test_health_endpoint_rejects_missing_required_table(app, client):
    with app.app_context():
        get_db().execute("DROP TABLE complaint_history")
        get_db().commit()
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json == {"status": "unhealthy"}


def test_complaint_persists_across_app_instances(tmp_path):
    database = tmp_path / "persistent.db"
    config = {
        "TESTING": True,
        "SECRET_KEY": "pytest-only-secret",
        "DATABASE_PATH": str(database),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "WTF_CSRF_ENABLED": False,
    }
    first = create_app(config)
    response, _ = submit(first.test_client())
    assert response.status_code == 302
    second = create_app(config)
    with second.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM complaints").fetchone()[0] == 1


def test_csrf_rejects_post_without_token(tmp_path):
    application = create_app({
        "TESTING": True,
        "SECRET_KEY": "pytest-only-secret",
        "DATABASE_PATH": str(tmp_path / "csrf.db"),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "WTF_CSRF_ENABLED": True,
    })
    response = application.test_client().post("/admin/login", data={"email": "a@b.test", "password": "x"})
    assert response.status_code == 400