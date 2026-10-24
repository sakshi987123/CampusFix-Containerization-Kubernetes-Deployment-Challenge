import sqlite3

from flask import current_app, g


COMPLAINT_COLUMNS = {
    "student_email": "TEXT",
    "student_id": "TEXT",
    "title": "TEXT NOT NULL DEFAULT 'Maintenance request'",
    "attachment_name": "TEXT",
    "assigned_staff": "TEXT",
    "resolution_comment": "TEXT",
}


def get_db():
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE_PATH"], timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        g.db = connection
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    connection = sqlite3.connect(current_app.config["DATABASE_PATH"], timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT UNIQUE NOT NULL,
            student_name TEXT NOT NULL,
            location TEXT NOT NULL,
            category TEXT NOT NULL CHECK (category IN ('Electrical','Plumbing','Cleaning','Furniture','Infrastructure','Other')),
            priority TEXT NOT NULL CHECK (priority IN ('Low','Medium','High')),
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending','In Progress','Resolved')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS complaint_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('Pending','In Progress','Resolved')),
            changed_by INTEGER REFERENCES users(id),
            changed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admin_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            complaint_id INTEGER NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
            admin_id INTEGER NOT NULL REFERENCES users(id),
            note TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_complaints_ticket ON complaints(ticket_id);
        CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status);
        """
    )
    existing_columns = {row["name"] for row in connection.execute("PRAGMA table_info(complaints)")}
    for column, sql_type in COMPLAINT_COLUMNS.items():
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE complaints ADD COLUMN {column} {sql_type}")
    legacy_rows = connection.execute(
        "SELECT id, status FROM complaints WHERE id NOT IN "
        "(SELECT complaint_id FROM complaint_history)"
    ).fetchall()
    for row in legacy_rows:
        connection.execute(
            "INSERT INTO complaint_history (complaint_id, status, changed_at) "
            "SELECT ?, ?, created_at FROM complaints WHERE id = ?",
            (row["id"], row["status"], row["id"]),
        )
    connection.commit()
    connection.close()