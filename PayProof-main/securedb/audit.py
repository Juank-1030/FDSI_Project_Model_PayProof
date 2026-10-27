import hashlib
import json
import sqlite3

from .database import get_conn, now_iso

GENESIS_HASH = "0" * 64


def _body(timestamp, actor, event_type, risk_level, description, payload):
    return f"{timestamp}|{actor}|{event_type}|{risk_level}|{description}|{payload or ''}"


def _compute_hash(prev_hash: str, body: str) -> str:
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def log_event(conn, actor, event_type, description, risk_level=None, payload=None):
    row = conn.execute(
        "SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = row["hash"] if row else GENESIS_HASH
    timestamp = now_iso()
    body = _body(timestamp, actor, event_type, risk_level, description, payload)
    record_hash = _compute_hash(prev_hash, body)
    cur = conn.execute(
        "INSERT INTO audit_log (timestamp, actor, event_type, risk_level, description, payload, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (timestamp, actor, event_type, risk_level, description, payload, prev_hash, record_hash),
    )
    return cur.lastrowid


def verify_chain(conn):
    rows = conn.execute(
        "SELECT id, timestamp, actor, event_type, risk_level, description, payload, prev_hash, hash FROM audit_log ORDER BY id"
    ).fetchall()
    prev_hash = GENESIS_HASH
    for row in rows:
        body = _body(
            row["timestamp"],
            row["actor"],
            row["event_type"],
            row["risk_level"],
            row["description"],
            row["payload"],
        )
        expected = _compute_hash(prev_hash, body)
        if expected != row["hash"]:
            return False, row["id"]
        prev_hash = row["hash"]
    return True, None


def get_events(conn):
    rows = conn.execute(
        "SELECT id, timestamp, actor, event_type, risk_level, description FROM audit_log ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def register_incident(conn, tipo, descripcion):
    conn.execute(
        "INSERT INTO incidents (timestamp, tipo, descripcion) VALUES (?, ?, ?)",
        (now_iso(), tipo, descripcion),
    )


def list_incidents(conn):
    rows = conn.execute(
        "SELECT * FROM incidents ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def manage_incident(conn, incident_id, actor):
    conn.execute(
        "UPDATE incidents SET estado = 'gestionada', gestionada_por = ?, gestionada_at = ? WHERE id = ?",
        (actor, now_iso(), incident_id),
    )
    log_event(
        conn,
        actor,
        "INCIDENTE_GESTIONADO",
        f"El incidente #{incident_id} fue marcado como gestionado.",
    )


def simulate_tamper(conn, actor) -> str:
    try:
        conn.execute(
            "UPDATE audit_log SET description = 'REGISTRO_ALTERADO' WHERE id = (SELECT MAX(id) FROM audit_log)"
        )
    except sqlite3.IntegrityError:
        log_event(
            conn,
            actor,
            "TAMPER_INTENT",
            "Intento de alteración del log bloqueado por el trigger de inmutabilidad.",
        )
        register_incident(
            conn,
            "TAMPER_INTENT",
            f"El usuario {actor} intentó modificar el registro de auditoría; el trigger de inmutabilidad bloqueó la operación.",
        )
        return "El intento fue BLOQUEADO por el trigger de inmutabilidad y quedó registrado como incidente de seguridad."
    return "Alerta: el trigger de inmutabilidad no se encontraba activo. Verifique la integridad del log."
