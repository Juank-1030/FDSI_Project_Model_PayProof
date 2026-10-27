import httpx

from . import config
from .database import now_iso

TIPO_PAGO_PENDIENTE = "PAGO_PENDIENTE"
TIPO_RESULTADO = "RESULTADO"
TIPO_ESCALAMIENTO = "ESCALAMIENTO"


class NotifierError(Exception):
    pass


class BaseNotifier:
    canal = "base"

    def send(self, destinatario: str, mensaje: str) -> bool:
        raise NotImplementedError


class MockNotifier(BaseNotifier):
    """Canal de demo: el mensaje solo queda registrado en la tabla de notificaciones."""

    canal = "mock"

    def send(self, destinatario: str, mensaje: str) -> bool:
        return True


class EvolutionNotifier(BaseNotifier):
    """EnvÃ­a mensajes reales vÃ­a Evolution API (WhatsApp Web, no oficial).

    Requiere el servicio corriendo (docker: evoapicloud/evolution-api) y una
    instancia conectada con QR. Config: EVOLUTION_BASE_URL, EVOLUTION_API_KEY,
    EVOLUTION_INSTANCE y NOTIFY_APPROVER_NUMBER.
    """

    canal = "evolution"

    def _endpoint(self) -> str:
        base = config.EVOLUTION_BASE_URL.rstrip("/")
        return f"{base}/message/sendText/{config.EVOLUTION_INSTANCE}"

    def send(self, destinatario: str, mensaje: str) -> bool:
        if not destinatario:
            raise NotifierError("NOTIFY_APPROVER_NUMBER no estÃ¡ configurado.")
        payload = {"number": destinatario, "text": mensaje}
        try:
            resp = httpx.post(
                self._endpoint(),
                json=payload,
                headers={"apikey": config.EVOLUTION_API_KEY},
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise NotifierError(f"No se pudo enviar el mensaje vÃ­a Evolution API: {exc}") from exc
        if resp.status_code not in (200, 201):
            raise NotifierError(
                f"Evolution API rechazÃ³ el envÃ­o (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return True


class WhatsAppCloudNotifier(BaseNotifier):
    """EnvÃ­a vÃ­a la API oficial de WhatsApp Business (Cloud API de Meta).

    Requiere template aprobado, WHATSAPP_TOKEN y WHATSAPP_PHONE_ID.
    Para mensajes proactivos el contenido debe coincidir con la plantilla.
    """

    canal = "whatsapp_cloud"

    def send(self, destinatario: str, mensaje: str) -> bool:
        if not (config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_ID):
            raise NotifierError("WHATSAPP_TOKEN o WHATSAPP_PHONE_ID no estÃ¡n configurados.")
        payload = {
            "messaging_product": "whatsapp",
            "to": destinatario,
            "type": "text",
            "text": {"body": mensaje},
        }
        try:
            resp = httpx.post(
                f"https://graph.facebook.com/v21.0/{config.WHATSAPP_PHONE_ID}/messages",
                json=payload,
                headers={
                    "Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise NotifierError(f"No se pudo enviar el mensaje vÃ­a WhatsApp Cloud API: {exc}") from exc
        if resp.status_code not in (200, 201):
            raise NotifierError(
                f"WhatsApp Cloud API rechazÃ³ el envÃ­o (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return True


_notifier_instance = None


def get_notifier() -> BaseNotifier:
    global _notifier_instance
    if _notifier_instance is not None:
        return _notifier_instance
    provider = (config.NOTIFY_PROVIDER or "mock").lower()
    if provider == "evolution":
        _notifier_instance = EvolutionNotifier()
    elif provider == "whatsapp_cloud":
        _notifier_instance = WhatsAppCloudNotifier()
    else:
        _notifier_instance = MockNotifier()
    return _notifier_instance


def notify(conn, tipo: str, destinatario: str, mensaje: str, meta: str = None):
    """Registra la notificaciÃ³n y la envÃ­a por el canal configurado.

    El envÃ­o fallido no rompe el flujo: queda registrado como enviado=False
    vÃ­a excepciÃ³n capturada y registrada.
    """
    notifier = get_notifier()
    enviado = False
    error = None
    try:
        enviado = notifier.send(destinatario, mensaje)
    except NotifierError as exc:
        error = str(exc)
    conn.execute(
        "INSERT INTO notification_log (tipo, destinatario, mensaje, canal, meta, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            tipo,
            destinatario,
            mensaje,
            f"{notifier.canal}{' (error: ' + error + ')' if error else ''}",
            meta,
            now_iso(),
        ),
    )
    return enviado


def list_notifications(conn):
    rows = conn.execute("SELECT * FROM notification_log ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

