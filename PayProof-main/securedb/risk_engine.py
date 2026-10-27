from . import config
from .database import get_conn
from .models import OperationProposal, RiskAssessment

PROHIBITED_TYPES = {"drop_table", "drop_database", "modify_audit", "alter_credentials"}

BASE_RISK_BY_TYPE = {
    "select": config.RISK_NONE,
    "update": config.RISK_LOW,
    "delete": config.RISK_CRITICAL,
    "pay": config.RISK_HIGH,
}

NOTIFY_BASE = {
    "individual": config.RISK_NONE,
    "small_group": config.RISK_LOW,
    "large_group": config.RISK_MEDIUM,
}


def _base_level(proposal: OperationProposal) -> int:
    op = proposal.operation_type
    if op == "notify":
        return NOTIFY_BASE.get(proposal.recipient_scope, config.RISK_NONE)
    if op == "select":
        return config.RISK_NONE
    if op == "pay":
        return config.RISK_HIGH
    if op == "update":
        if proposal.records_affected > 1:
            return config.RISK_CRITICAL if proposal.involves_financial else config.RISK_MEDIUM
        if proposal.involves_financial:
            return config.RISK_HIGH
        return config.RISK_LOW
    if op == "delete":
        return config.RISK_CRITICAL
    return config.RISK_NONE


def _classify_pay(proposal: OperationProposal) -> RiskAssessment:
    """Clasifica pagos comparando el hash del beneficiario contra el registro de proveedores."""
    from . import vendors as vendors_mod

    with get_conn() as conn:
        verification = vendors_mod.verify_vendor(
            conn,
            proposal.vendor_name or "",
            proposal.bank_code or "",
            proposal.account_number or "",
        )

    reasons = []
    level = config.RISK_HIGH

    if verification["match"]:
        reasons.append(
            "Beneficiario verificado: el hash de la cuenta coincide con el registro del proveedor."
        )
    elif verification["registered"]:
        level = config.RISK_CRITICAL
        reasons.append(
            "HASH_MISMATCH: la cuenta informada NO coincide con la registrada para este "
            "proveedor (posible fraude de cambio de cuenta - BEC)."
        )
    else:
        level = config.RISK_CRITICAL
        reasons.append(
            "HASH_MISMATCH: el proveedor no tiene registro en el sistema (beneficiario desconocido)."
        )

    try:
        amount = float(proposal.amount or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount > config.PAY_LARGE_AMOUNT:
        level = config.RISK_CRITICAL
        reasons.append(
            f"Monto elevado (>{config.PAY_LARGE_AMOUNT}): requiere confirmación adicional."
        )

    return RiskAssessment(
        level=level,
        label=config.RISK_LABELS[level],
        prohibited=False,
        requires_approval=True,
        min_authority=config.MIN_AUTHORITY[level],
        requires_confirmation=level == config.RISK_CRITICAL,
        reasons=reasons,
    )


def classify(proposal: OperationProposal) -> RiskAssessment:
    if proposal.operation_type in PROHIBITED_TYPES:
        return RiskAssessment(
            level=config.RISK_PROHIBITED,
            label=config.RISK_LABELS[config.RISK_PROHIBITED],
            prohibited=True,
            requires_approval=False,
            min_authority=None,
            requires_confirmation=False,
            reasons=["Operación prohibida por política: bloqueo inmediato sin excepción."],
        )
    if proposal.operation_type in ("update", "delete") and proposal.target in (
        "auditoria",
        "audit_log",
    ):
        return RiskAssessment(
            level=config.RISK_PROHIBITED,
            label=config.RISK_LABELS[config.RISK_PROHIBITED],
            prohibited=True,
            requires_approval=False,
            min_authority=None,
            requires_confirmation=False,
            reasons=["El log de auditoría es inmutable: ninguna operación puede modificarlo."],
        )
    if proposal.operation_type == "pay":
        return _classify_pay(proposal)

    reasons = []
    level = _base_level(proposal)

    if proposal.involves_financial and proposal.operation_type in ("select", "notify"):
        level += 1
        reasons.append("Datos financieros involucrados (+1)")
    if proposal.records_affected > 100:
        level += 2
        reasons.append("Más de 100 registros afectados (+2)")
    elif proposal.records_affected > 10:
        level += 1
        reasons.append("Más de 10 registros afectados (+1)")
    if not proposal.reversible:
        level += 1
        reasons.append("Operación irreversible (+1)")
    if proposal.involves_personal:
        level += 1
        reasons.append("Datos personales involucrados (+1)")

    level = min(level, config.MAX_RISK_LEVEL)

    return RiskAssessment(
        level=level,
        label=config.RISK_LABELS[level],
        prohibited=False,
        requires_approval=level > config.RISK_NONE,
        min_authority=config.MIN_AUTHORITY[level],
        requires_confirmation=level == config.RISK_CRITICAL,
        reasons=reasons,
    )
