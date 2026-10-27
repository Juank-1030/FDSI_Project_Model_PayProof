import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "securedb.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    apellido TEXT NOT NULL,
    email TEXT NOT NULL,
    departamento_id INTEGER NOT NULL REFERENCES departments(id),
    cargo TEXT NOT NULL,
    salario REAL NOT NULL,
    fecha_ingreso TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    destinatario TEXT NOT NULL,
    alcance TEXT NOT NULL,
    mensaje TEXT NOT NULL,
    estado TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    solicitante TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    target TEXT NOT NULL,
    intent TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    risk_level INTEGER NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_by TEXT,
    decision TEXT,
    comment TEXT,
    decided_at TEXT,
    payload_hash TEXT,
    approval_method TEXT
);

CREATE TABLE IF NOT EXISTS csv_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    solicitante TEXT NOT NULL,
    role TEXT NOT NULL,
    instruction TEXT NOT NULL,
    filename TEXT NOT NULL,
    csv_text TEXT NOT NULL,
    overrides_json TEXT NOT NULL DEFAULT '{}',
    plan_hash TEXT NOT NULL,
    min_role TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pendiente',
    created_at TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    result_text TEXT
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    banco TEXT NOT NULL,
    cuenta TEXT NOT NULL,
    hash TEXT NOT NULL,
    core_account_uuid TEXT,
    status TEXT NOT NULL DEFAULT 'activo',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id INTEGER REFERENCES vendors(id),
    vendor_name TEXT NOT NULL,
    bank_code TEXT NOT NULL,
    account_number TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    vendor_hash_match INTEGER NOT NULL,
    core_transfer_uuid TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL,
    destinatario TEXT NOT NULL,
    mensaje TEXT NOT NULL,
    canal TEXT NOT NULL,
    meta TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS passkeys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    credential_id TEXT NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    event_type TEXT NOT NULL,
    risk_level TEXT,
    description TEXT NOT NULL,
    payload TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    tipo TEXT NOT NULL,
    descripcion TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'abierta',
    gestionada_por TEXT,
    gestionada_at TEXT
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_LOG_INMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_LOG_INMUTABLE');
END;
"""

DEPARTMENTS = [
    "Ventas",
    "Ingeniería",
    "Finanzas",
    "Recursos Humanos",
    "Seguridad",
]

EMPLOYEES = [
    ("Ana", "Martinez", "ana.martinez@empresa.com", 1, "Analista Comercial", 2800, "2021-03-15"),
    ("Luis", "Gomez", "luis.gomez@empresa.com", 1, "Vendedor", 2200, "2022-01-10"),
    ("Carla", "Ruiz", "carla.ruiz@empresa.com", 1, "Gerente Comercial", 5200, "2019-06-01"),
    ("Pedro", "Sanchez", "pedro.sanchez@empresa.com", 2, "Desarrollador", 3900, "2020-09-21"),
    ("Maria", "Lopez", "maria.lopez@empresa.com", 2, "Arquitecta de Software", 6100, "2018-02-14"),
    ("Jorge", "Diaz", "jorge.diaz@empresa.com", 2, "DevOps", 4400, "2021-11-30"),
    ("Elena", "Castro", "elena.castro@empresa.com", 3, "Contadora", 3500, "2020-04-05"),
    ("Roberto", "Pena", "roberto.pena@empresa.com", 3, "Director Financiero", 8200, "2017-08-19"),
    ("Sofia", "Herrera", "sofia.herrera@empresa.com", 3, "Analista de Presupuesto", 3100, "2022-05-12"),
    ("Gabriela", "Torres", "gabriela.torres@empresa.com", 4, "Reclutadora", 2600, "2021-07-07"),
    ("Andres", "Vega", "andres.vega@empresa.com", 4, "Especialista de Nomina", 3300, "2019-10-28"),
    ("Camila", "Rojas", "camila.rojas@empresa.com", 4, "Directora de Talento", 5800, "2018-12-01"),
    ("Tatiana", "Gomez", "tatiana.gomez@empresa.com", 5, "Analista de Seguridad", 10000, "2023-02-10"),
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        count = conn.execute("SELECT COUNT(*) AS c FROM departments").fetchone()["c"]
        if count == 0:
            conn.executemany(
                "INSERT INTO departments (nombre) VALUES (?)",
                [(d,) for d in DEPARTMENTS],
            )
        emp_count = conn.execute("SELECT COUNT(*) AS c FROM employees").fetchone()["c"]
        if emp_count == 0:
            conn.executemany(
                "INSERT INTO employees (nombre, apellido, email, departamento_id, cargo, salario, fecha_ingreso) VALUES (?, ?, ?, ?, ?, ?, ?)",
                EMPLOYEES,
            )
        from .vendors import ensure_vendors

        ensure_vendors(conn)


def _migrate(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(approval_requests)").fetchall()]
    if "payload_hash" not in cols:
        conn.execute("ALTER TABLE approval_requests ADD COLUMN payload_hash TEXT")
    if "approval_method" not in cols:
        conn.execute("ALTER TABLE approval_requests ADD COLUMN approval_method TEXT")
