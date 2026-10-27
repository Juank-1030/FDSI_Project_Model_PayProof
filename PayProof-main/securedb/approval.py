from datetime import datetime, timedelta, timezone

from . import config
from .approval_token import build_approval_link, verify_token
from .audit import log_event, register_incident
from .biometric import get_provider as get_biometric_provider
from .database import now_iso
from .executor import execute
from .models import OperationProposal, RiskAssessment
from .notifier import notify, TIPO_ESCALAMIENTO, TIPO_PAGO_PENDIENTE, TIPO_RESULTADO

STATUS_PENDING = "pendiente"
STATUS_PENDING_CONFIRMATION = "pendiente_confirmacion"
STATUS_EXECUTED = "ejecutado"
STATUS_REJECTED = "rechazado"
STATUS_EXPIRED = "expirado"


def _destinatario(solicitante: str) -> str:
    """Número real si el canal es WhatsApp; usuario en el canal mock."""
    return config.NOTIFY_APPROVER_NUMBER or solicitante


def _notificar_pendiente(conn, row, proposal):
    detalle = proposal.intent
    if proposal.operation_type == "pay":
        detalle = (
            f"{proposal.amount} {proposal.currency} a {proposal.vendor_name} "
            f"(cuenta {proposal.account_number})"
        )
    link = build_approval_link(row["id"], row["payload_hash"] or proposal.payload_hash())
    notify(
        conn,
        TIPO_PAGO_PENDIENTE,
        _destinatario(row["solicitante"]),
        f"PayProof: aprobación pendiente #{row['id']} - {detalle}. Revisar y aprobar: {link}",
        meta=link,
    )


def create_request(conn, solicitante, proposal: OperationProposal, assessment: RiskAssessment):
    timeout = config.TIMEOUT_HOURS[assessment.level]
    expires_at = datetime.now(timezone.utc) + timedelta(hours=timeout)
    cur = conn.execute(
        "INSERT INTO approval_requests (solicitante, operation_type, target, intent, proposal_json, risk_level, status, requested_at, expires_at, payload_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            solicitante,
            proposal.operation_type,
            proposal.target,
            proposal.intent,
            proposal.model_dump_json(),
            assessment.level,
            STATUS_PENDING,
            now_iso(),
            expires_at.isoformat(),
            proposal.payload_hash(),
        ),
    )
    row = _get_request(conn, cur.lastrowid)
    _notificar_pendiente(conn, row, proposal)
    return cur.lastrowid


def can_act(role: str, level) -> bool:
    if level == config.RISK_PROHIBITED or level not in config.MIN_AUTHORITY:
        return False
    min_authority = config.MIN_AUTHORITY[level]
    if min_authority is None:
        return False
    return config.ROLE_LEVEL.get(role, 0) >= config.ROLE_LEVEL[min_authority]


def _get_request(conn, request_id):
    return conn.execute(
        "SELECT * FROM approval_requests WHERE id = ?", (request_id,)
    ).fetchone()


def _verify_payload_integrity(conn, row, proposal: OperationProposal):
    """Garantiza que el payload aprobado es exactamente el que se creó.

    Si el proposal_json fue alterado después de crear la solicitud, el hash
    no coincidirá con el guardado y la ejecución se bloquea (anti-TOCTOU).
    """
    stored_hash = row["payload_hash"] if "payload_hash" in row.keys() else None
    if stored_hash is None:
        return True, None
    if proposal.payload_hash() == stored_hash:
        return True, None
    log_event(
        conn,
        "sistema",
        "TAMPER_PAYLOAD",
        f"Se detectó alteración del payload de la solicitud #{row['id']}: el hash no coincide con el registrado al crearla.",
        risk_level=str(row["risk_level"]),
    )
    register_incident(
        conn,
        "TAMPER_PAYLOAD",
        f"El payload de la solicitud #{row['id']} fue modificado después de su creación; la ejecución fue bloqueada.",
    )
    return False, "Bloqueado: el payload fue alterado después de crear la solicitud (hash no coincide)."


def _is_expired(row):
    expires_at = datetime.fromisoformat(row["expires_at"])
    return datetime.now(timezone.utc) > expires_at


def _expire_request(conn, row):
    conn.execute(
        "UPDATE approval_requests SET status = ? WHERE id = ?",
        (STATUS_EXPIRED, row["id"]),
    )
    log_event(
        conn,
        "sistema",
        "SOLICITUD_EXPIRADA",
        f"La solicitud #{row['id']} expiró sin decisión y fue cancelada automáticamente.",
        risk_level=str(row["risk_level"]),
    )
    notify(
        conn,
        TIPO_ESCALAMIENTO,
        _destinatario(row["solicitante"]),
        f"PayProof: la solicitud #{row['id']} expiró sin decisión y fue cancelada. "
        "Escalada al supervisor para revisión (nunca se auto-aprueba).",
    )


def expire_requests(conn):
    rows = conn.execute(
        "SELECT * FROM approval_requests WHERE status IN (?, ?)",
        (STATUS_PENDING, STATUS_PENDING_CONFIRMATION),
    ).fetchall()
    expired_ids = []
    for row in rows:
        if _is_expired(row):
            _expire_request(conn, row)
            expired_ids.append(row["id"])
    return expired_ids


def _validate_comment(comment: str):
    if not comment or len(comment.strip()) < config.MIN_COMMENT_LENGTH:
        return (
            False,
            f"El comentario debe tener al menos {config.MIN_COMMENT_LENGTH} caracteres.",
        )
    return True, None


def approve(conn, request_id, actor, role, comment):
    ok, error = _validate_comment(comment)
    if not ok:
        return False, error
    row = _get_request(conn, request_id)
    if row is None:
        return False, "La solicitud no existe."
    if row["status"] != STATUS_PENDING:
        return False, f"La solicitud ya no está pendiente (estado actual: {row['status']})."
    if _is_expired(row):
        _expire_request(conn, row)
        return False, "La solicitud expiró y fue cancelada automáticamente."
    level = int(row["risk_level"])
    if not can_act(role, level):
        return False, "Su rol no tiene autoridad para aprobar esta solicitud (no delegación de autoridad)."
    proposal = OperationProposal.model_validate_json(row["proposal_json"])
    ok, error = _verify_payload_integrity(conn, row, proposal)
    if not ok:
        return False, error
    comment = comment.strip()
    if level == config.RISK_CRITICAL:
        conn.execute(
            "UPDATE approval_requests SET status = ?, decided_by = ?, decision = 'aprobado', comment = ?, decided_at = ? WHERE id = ?",
            (STATUS_PENDING_CONFIRMATION, actor, comment, now_iso(), request_id),
        )
        log_event(
            conn,
            actor,
            "SOLICITUD_APROBADA",
            f"El {role} '{actor}' aprobó la solicitud #{request_id} (nivel crítico). Pendiente confirmación adicional.",
            risk_level=str(level),
        )
        return True, "Aprobación registrada. Esta operación crítica requiere una confirmación adicional del administrador."
    try:
        result = execute(conn, proposal)
    except ValueError as exc:
        return False, f"La operación no pudo ejecutarse: {exc}"
    conn.execute(
        "UPDATE approval_requests SET status = ?, decided_by = ?, decision = 'aprobado', comment = ?, decided_at = ? WHERE id = ?",
        (STATUS_EXECUTED, actor, comment, now_iso(), request_id),
    )
    log_event(
        conn,
        actor,
        "SOLICITUD_APROBADA",
        f"El {role} '{actor}' aprobó la solicitud #{request_id}. Comentario: {comment}",
        risk_level=str(level),
    )
    log_event(
        conn,
        actor,
        "EJECUCION_APROBADA",
        f"Operación de la solicitud #{request_id} ejecutada: {result['summary']}",
        risk_level=str(level),
    )
    notify(
        conn,
        TIPO_RESULTADO,
        _destinatario(row["solicitante"]),
        f"PayProof: la solicitud #{request_id} fue aprobada y ejecutada. {result['summary']}",
    )
    return True, f"Operación aprobada y ejecutada. {result['summary']}"


def reject(conn, request_id, actor, role, comment):
    ok, error = _validate_comment(comment)
    if not ok:
        return False, error
    row = _get_request(conn, request_id)
    if row is None:
        return False, "La solicitud no existe."
    if row["status"] != STATUS_PENDING:
        return False, f"La solicitud ya no está pendiente (estado actual: {row['status']})."
    if _is_expired(row):
        _expire_request(conn, row)
        return False, "La solicitud expiró y fue cancelada automáticamente."
    level = int(row["risk_level"])
    if not can_act(role, level):
        return False, "Su rol no tiene autoridad para rechazar esta solicitud (no delegación de autoridad)."
    conn.execute(
        "UPDATE approval_requests SET status = ?, decided_by = ?, decision = 'rechazado', comment = ?, decided_at = ? WHERE id = ?",
        (STATUS_REJECTED, actor, comment.strip(), now_iso(), request_id),
    )
    log_event(
        conn,
        actor,
        "SOLICITUD_RECHAZADA",
        f"El {role} '{actor}' rechazó la solicitud #{request_id}. Comentario: {comment.strip()}",
        risk_level=str(level),
    )
    notify(
        conn,
        TIPO_RESULTADO,
        _destinatario(row["solicitante"]),
        f"PayProof: la solicitud #{request_id} fue rechazada. Comentario: {comment.strip()}",
    )
    return True, "Solicitud rechazada y registrada en el log de auditoría."


def confirm_execution(conn, request_id, actor, comment):
    ok, error = _validate_comment(comment)
    if not ok:
        return False, error
    row = _get_request(conn, request_id)
    if row is None:
        return False, "La solicitud no existe."
    if row["status"] != STATUS_PENDING_CONFIRMATION:
        return False, f"La solicitud no está pendiente de confirmación (estado actual: {row['status']})."
    if _is_expired(row):
        _expire_request(conn, row)
        return False, "La solicitud expiró y fue cancelada automáticamente."
    if actor != row["decided_by"]:
        return False, "La confirmación adicional debe realizarla el mismo administrador que aprobó la operación."
    proposal = OperationProposal.model_validate_json(row["proposal_json"])
    ok, error = _verify_payload_integrity(conn, row, proposal)
    if not ok:
        return False, error
    try:
        result = execute(conn, proposal)
    except ValueError as exc:
        return False, f"La operación no pudo ejecutarse: {exc}"
    conn.execute(
        "UPDATE approval_requests SET status = ?, decided_at = ? WHERE id = ?",
        (STATUS_EXECUTED, now_iso(), request_id),
    )
    log_event(
        conn,
        actor,
        "CONFIRMACION_CRITICA",
        f"Confirmación adicional del administrador '{actor}' para la solicitud #{request_id}. Comentario: {comment.strip()}",
        risk_level=str(row["risk_level"]),
    )
    log_event(
        conn,
        actor,
        "EJECUCION_APROBADA",
        f"Operación de la solicitud #{request_id} ejecutada tras doble confirmación: {result['summary']}",
        risk_level=str(row["risk_level"]),
    )
    return True, f"Operación crítica confirmada y ejecutada. {result['summary']}"


def approve_biometric(conn, request_id, token, proof, challenge_b64=None):
    """Aprobación vía deep link con verificación biométrica.

    El token del enlace valida que el payload no fue alterado desde que se
    generó el link; la biometría (mock o passkey WebAuthn) firma un challenge
    ligado al payload_hash. Para operaciones críticas exige doble paso.
    """
    row = _get_request(conn, request_id)
    if row is None:
        return False, "La solicitud no existe."
    if row["status"] not in (STATUS_PENDING, STATUS_PENDING_CONFIRMATION):
        return False, f"La solicitud ya no está pendiente (estado actual: {row['status']})."
    if _is_expired(row):
        _expire_request(conn, row)
        return False, "La solicitud expiró y fue cancelada automáticamente."

    proposal = OperationProposal.model_validate_json(row["proposal_json"])
    ok, error = _verify_payload_integrity(conn, row, proposal)
    if not ok:
        return False, error

    _, error = verify_token(token, row["payload_hash"] or proposal.payload_hash())
    if error:
        log_event(
            conn,
            "sistema",
            "TOKEN_APROBACION_INVALIDO",
            f"Intento de aprobación con token inválido en la solicitud #{request_id}: {error}",
            risk_level=str(row["risk_level"]),
        )
        return False, error

    provider = get_biometric_provider()
    username, error = provider.verify_approval(
        proof, approval_id=request_id, payload_hash=row["payload_hash"], challenge_b64=challenge_b64
    )
    if error:
        return False, error

    with_user = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
    if with_user is None:
        return False, "El usuario biométrico no existe en el sistema."
    role = with_user["role"]
    level = int(row["risk_level"])
    if not can_act(role, level):
        return False, "El usuario verificado biométricamente no tiene autoridad para esta solicitud."

    if row["status"] == STATUS_PENDING and level == config.RISK_CRITICAL:
        conn.execute(
            "UPDATE approval_requests SET status = ?, decided_by = ?, decision = 'aprobado_biometrico', "
            "approval_method = 'biometrico', comment = 'aprobado por biometría (1/2)', decided_at = ? WHERE id = ?",
            (STATUS_PENDING_CONFIRMATION, username, now_iso(), request_id),
        )
        log_event(
            conn,
            username,
            "SOLICITUD_APROBADA_BIOMETRICA",
            f"El usuario '{username}' aprobó con biometría la solicitud #{request_id} (nivel crítico). "
            "Pendiente confirmación biométrica adicional.",
            risk_level=str(level),
        )
        return True, "Biometría verificada. Esta operación crítica requiere una segunda confirmación biométrica."

    if row["status"] == STATUS_PENDING_CONFIRMATION and username != row["decided_by"]:
        return False, "La confirmación biométrica debe realizarla el mismo usuario que aprobó."

    try:
        result = execute(conn, proposal)
    except ValueError as exc:
        return False, f"La operación no pudo ejecutarse: {exc}"
    conn.execute(
        "UPDATE approval_requests SET status = ?, decided_by = ?, decision = 'aprobado_biometrico', "
        "approval_method = 'biometrico', comment = 'aprobado por biometría', decided_at = ? WHERE id = ?",
        (STATUS_EXECUTED, username, now_iso(), request_id),
    )
    log_event(
        conn,
        username,
        "EJECUCION_APROBADA",
        f"Operación de la solicitud #{request_id} ejecutada tras aprobación biométrica "
        f"({provider.provider_name}): {result['summary']}",
        risk_level=str(level),
    )
    notify(
        conn,
        TIPO_RESULTADO,
        _destinatario(row["solicitante"]),
        f"PayProof: la solicitud #{request_id} fue aprobada con biometría ({provider.provider_name}) "
        f"y ejecutada. {result['summary']}",
    )
    return True, f"Biometría verificada y operación ejecutada. {result['summary']}"


def list_pending(conn):
    rows = conn.execute(
        "SELECT * FROM approval_requests WHERE status IN (?, ?) ORDER BY id DESC",
        (STATUS_PENDING, STATUS_PENDING_CONFIRMATION),
    ).fetchall()
    return [dict(r) for r in rows]
