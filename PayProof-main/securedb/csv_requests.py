"""Solicitudes de edición de CSV de nómina que requieren aprobación de otro usuario."""
import json
from typing import Optional

from payroll_ai.csvio import Table
from payroll_ai.engine import ROLE_LEVEL, PayrollAssistant

from .database import now_iso

_bot: Optional[PayrollAssistant] = None


def bot() -> PayrollAssistant:
    global _bot
    if _bot is None:
        _bot = PayrollAssistant.load()
    return _bot


def create(conn, user, instruction, filename, csv_text, overrides, plan):
    cur = conn.execute(
        "INSERT INTO csv_requests (solicitante, role, instruction, filename, csv_text, overrides_json, plan_hash, "
        "min_role, summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (user["username"], user["role"], instruction, filename, csv_text, json.dumps(overrides or {}),
         plan.payload_hash, plan.min_role, f"{plan.kind}: {len(plan.rows)} fila(s), columna {plan.column_header or '-'}",
         now_iso()),
    )
    return cur.lastrowid


def pending_for(conn, user):
    """Pendientes que este usuario podría aprobar (rol suficiente y no es el solicitante)."""
    out = []
    for r in conn.execute("SELECT * FROM csv_requests WHERE status='pendiente' ORDER BY id").fetchall():
        if r["solicitante"] != user["username"] and ROLE_LEVEL.get(user["role"], 0) >= min(ROLE_LEVEL[r["min_role"]], 3):
            out.append(dict(r))
    return out


def mine(conn, user):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM csv_requests WHERE solicitante=? ORDER BY id DESC LIMIT 20", (user["username"],)).fetchall()]


def _replan(req):
    t = Table.from_text(req["csv_text"])
    overrides = {int(k): v for k, v in json.loads(req["overrides_json"]).items()}
    return t, bot().plan(req["instruction"], t, req["role"], overrides)


def preview(req):
    """Plan completo tal como lo vería un aprobador (con valores)."""
    t, plan = _replan(req)
    return t, plan


def decide(conn, req_id, approver, approve: bool, confirm: bool = False):
    req = conn.execute("SELECT * FROM csv_requests WHERE id=?", (req_id,)).fetchone()
    if req is None or req["status"] != "pendiente":
        return False, "La solicitud no existe o ya fue resuelta."
    req = dict(req)
    if approver["username"] == req["solicitante"]:
        return False, "Nadie puede aprobar su propia solicitud."
    if not approve:
        conn.execute("UPDATE csv_requests SET status='rechazada', decided_by=?, decided_at=? WHERE id=?",
                     (approver["username"], now_iso(), req_id))
        return True, "Solicitud rechazada."
    t, plan = _replan(req)
    if plan.payload_hash != req["plan_hash"]:
        return False, "TAMPER_PAYLOAD: el plan recalculado no coincide con el original."
    res = bot().execute(plan, t, approver_role=approver["role"], confirm=confirm)
    if not res.ok:
        return False, res.message
    conn.execute("UPDATE csv_requests SET status='aprobada', decided_by=?, decided_at=?, result_text=? WHERE id=?",
                 (approver["username"], now_iso(), res.table.to_text() if res.table else None, req_id))
    return True, res.message
