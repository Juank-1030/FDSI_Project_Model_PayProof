import hashlib
import hmac
import time

from . import config


def _signature(request_id: int, payload_hash: str, exp: int) -> str:
    message = f"{request_id}:{payload_hash}:{exp}"
    return hmac.new(
        config.APPROVAL_HMAC_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def issue_token(request_id: int, payload_hash: str, ttl_minutes: int = None) -> str:
    """Genera un token firmado para el deep link de aprobación.

    El token liga request_id + payload_hash + expiración: cualquiera con el
    link puede abrir la solicitud, pero el hash del payload no puede
    alterarse sin invalidar la firma.
    """
    ttl = ttl_minutes if ttl_minutes is not None else config.APPROVAL_TOKEN_TTL_MINUTES
    exp = int(time.time()) + int(ttl * 60)
    sig = _signature(request_id, payload_hash, exp)
    return f"{request_id}.{exp}.{sig}"


def verify_token(token: str, payload_hash: str):
    """Valida el token y devuelve (request_id, None) o (None, error)."""
    try:
        request_id_raw, exp_raw, sig = token.split(".")
        request_id = int(request_id_raw)
        exp = int(exp_raw)
    except (ValueError, AttributeError):
        return None, "El token de aprobación está malformado."
    if exp < time.time():
        return None, "El enlace de aprobación expiró."
    expected = _signature(request_id, payload_hash, exp)
    if not hmac.compare_digest(sig, expected):
        return None, "El enlace de aprobación tiene una firma inválida (posible manipulación)."
    return request_id, None


def build_approval_link(request_id: int, payload_hash: str) -> str:
    token = issue_token(request_id, payload_hash)
    base = config.APPROVAL_LINK_BASE.rstrip("/")
    return f"{base}/?view=aprobar&id={request_id}&token={token}"
