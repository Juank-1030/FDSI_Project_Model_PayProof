from .database import now_iso
from .models import OperationProposal
from .risk_engine import PROHIBITED_TYPES

ALLOWED_UPDATE_COLUMNS = {"salario", "cargo", "email"}
ALLOWED_FILTER_COLUMNS = {"id", "nombre", "apellido", "cargo", "email", "departamento"}

_core_client_instance = None


def _core_client():
    global _core_client_instance
    if _core_client_instance is None:
        from . import core_client

        _core_client_instance = core_client.FinAegisClient()
    return _core_client_instance


def _build_where(filters):
    clauses = []
    params = []
    for key, value in (filters or {}).items():
        key = str(key).lower().strip()
        if key == "id":
            clauses.append("id = ?")
            params.append(int(value))
        elif key in ("nombre", "apellido", "cargo", "email"):
            clauses.append(f"{key} LIKE ?")
            params.append(f"%{value}%")
        elif key == "departamento":
            clauses.append("departamento_id IN (SELECT id FROM departments WHERE nombre LIKE ?)")
            params.append(f"%{value}%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def _select(conn, proposal):
    where, params = _build_where(proposal.filters)
    sql = (
        "SELECT e.id, e.nombre, e.apellido, e.email, d.nombre AS departamento, e.cargo, e.salario "
        f"FROM employees e JOIN departments d ON e.departamento_id = d.id{where} ORDER BY e.id"
    )
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return {
        "summary": f"Consulta ejecutada: {len(rows)} registro(s) encontrado(s).",
        "rows": rows,
        "count": len(rows),
    }


def _update(conn, proposal):
    changes = {
        k: v for k, v in (proposal.changes or {}).items() if k in ALLOWED_UPDATE_COLUMNS
    }
    if not changes:
        raise ValueError("La modificación no incluye columnas permitidas.")
    where, params = _build_where(proposal.filters)
    if not where:
        raise ValueError("La modificación requiere filtros que delimiten el alcance.")
    sets_parts, values = [], []
    for col, value in changes.items():
        if col == "salario" and isinstance(value, dict):
            # ajuste relativo: {"pct": 10} (aumento 10%) / {"pct": -5} / {"abs": 200000}
            if "pct" in value:
                factor = 1 + float(value["pct"]) / 100.0
                if factor < 0:
                    raise ValueError("Ajuste porcentual inválido.")
                sets_parts.append("salario = ROUND(salario * ?, 2)")
                values.append(factor)
            elif "abs" in value:
                sets_parts.append("salario = MAX(salario + ?, 0)")
                values.append(float(value["abs"]))
            else:
                raise ValueError("Ajuste de salario no reconocido.")
        else:
            sets_parts.append(f"{col} = ?")
            values.append(float(value) if col == "salario" else str(value))
    sets = ", ".join(sets_parts)
    values.extend(params)
    cur = conn.execute(f"UPDATE employees SET {sets}{where}", values)
    return {
        "summary": f"Modificación ejecutada: {cur.rowcount} registro(s) actualizado(s).",
        "rows": None,
        "count": cur.rowcount,
    }


def _delete(conn, proposal):
    where, params = _build_where(proposal.filters)
    if not where:
        raise ValueError("La eliminación requiere filtros explícitos que delimiten el alcance.")
    cur = conn.execute(f"DELETE FROM employees{where}", params)
    return {
        "summary": f"Eliminación ejecutada: {cur.rowcount} registro(s) eliminado(s).",
        "rows": None,
        "count": cur.rowcount,
    }


def _notify(conn, proposal):
    destinatario = str((proposal.filters or {}).get("destinatario", "Destinatario general"))
    mensaje = proposal.message or proposal.intent
    conn.execute(
        "INSERT INTO notifications (destinatario, alcance, mensaje, estado, created_at) VALUES (?, ?, ?, 'enviado', ?)",
        (destinatario, proposal.recipient_scope, mensaje, now_iso()),
    )
    return {
        "summary": f"Notificación enviada a {destinatario} (alcance: {proposal.recipient_scope}).",
        "rows": None,
        "count": 1,
    }


def _pay(conn, proposal):
    from . import vendors as vendors_mod

    verification = vendors_mod.verify_vendor(
        conn,
        proposal.vendor_name or "",
        proposal.bank_code or "",
        proposal.account_number or "",
    )
    if not verification["match"]:
        raise ValueError(
            "Ejecución bloqueada: HASH_MISMATCH del beneficiario detectado en la "
            "verificación final (la cuenta no coincide con la registrada para este proveedor)."
        )

    vendor = vendors_mod.get_vendor(conn, verification["vendor_id"])
    client = _core_client()
    from_account = client.get_source_account_uuid()
    to_account = (
        vendor.get("core_account_uuid")
        if vendor and vendor.get("core_account_uuid")
        else client.get_vendor_account_uuid()
    )

    result = client.create_transfer(
        from_account_uuid=from_account,
        to_account_uuid=to_account,
        amount=float(proposal.amount or 0),
        asset_code=proposal.currency or "EUR",
        description=proposal.message or proposal.intent,
    )

    conn.execute(
        "INSERT INTO payments (vendor_id, vendor_name, bank_code, account_number, amount, currency, "
        "proposal_hash, vendor_hash_match, core_transfer_uuid, status, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'ejecutado', ?, ?)",
        (
            verification["vendor_id"],
            proposal.vendor_name or "",
            proposal.bank_code or "",
            proposal.account_number or "",
            float(proposal.amount or 0),
            proposal.currency or "EUR",
            proposal.payload_hash(),
            result["transfer_uuid"],
            f"Transferencia real enviada al core FinAegis: {result['transfer_uuid']} ({result['status']}).",
            now_iso(),
        ),
    )

    return {
        "summary": (
            f"Pago a '{proposal.vendor_name}' ejecutado en el core FinAegis. "
            f"Transferencia: {result['transfer_uuid']} ({result['status']})."
        ),
        "transfer_uuid": result["transfer_uuid"],
    }


def execute(conn, proposal: OperationProposal) -> dict:
    op = proposal.operation_type
    if op in PROHIBITED_TYPES:
        raise ValueError("Operación prohibida: bloqueada por el ejecutor como defensa en profundidad.")
    if op == "select":
        return _select(conn, proposal)
    if op == "update":
        return _update(conn, proposal)
    if op == "delete":
        return _delete(conn, proposal)
    if op == "notify":
        return _notify(conn, proposal)
    if op == "pay":
        return _pay(conn, proposal)
    raise ValueError(f"Tipo de operación no soportado: {op}")
