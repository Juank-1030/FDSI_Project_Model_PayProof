import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    path = os.path.join(_BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

RISK_NONE = 0
RISK_LOW = 1
RISK_MEDIUM = 2
RISK_HIGH = 3
RISK_CRITICAL = 4
RISK_PROHIBITED = "PROHIBIDO"

RISK_LABELS = {
    RISK_NONE: "Ninguno",
    RISK_LOW: "Bajo",
    RISK_MEDIUM: "Medio",
    RISK_HIGH: "Alto",
    RISK_CRITICAL: "Crítico",
    RISK_PROHIBITED: "Prohibido",
}

ROLE_USER = "usuario"
ROLE_OPERATOR = "operador"
ROLE_SUPERVISOR = "supervisor"
ROLE_ADMIN = "administrador"

ROLE_LEVEL = {
    ROLE_USER: 0,
    ROLE_OPERATOR: 1,
    ROLE_SUPERVISOR: 2,
    ROLE_ADMIN: 3,
}

MIN_AUTHORITY = {
    RISK_NONE: None,
    RISK_LOW: ROLE_OPERATOR,
    RISK_MEDIUM: ROLE_SUPERVISOR,
    RISK_HIGH: ROLE_ADMIN,
    RISK_CRITICAL: ROLE_ADMIN,
}

TIMEOUT_HOURS = {
    RISK_LOW: 24,
    RISK_MEDIUM: 8,
    RISK_HIGH: 4,
    RISK_CRITICAL: 2,
}

MIN_COMMENT_LENGTH = 10
MAX_RISK_LEVEL = RISK_CRITICAL

PAY_LARGE_AMOUNT = float(os.environ.get("PAYPROOF_LARGE_AMOUNT", "1000"))

# local = modelo propio (payroll_ai) | groq | ollama
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "local")

OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

FINAEGIS_BASE_URL = os.environ.get("FINAEGIS_BASE_URL", "http://localhost:8000")
FINAEGIS_EMAIL = os.environ.get("FINAEGIS_EMAIL", "")
FINAEGIS_PASSWORD = os.environ.get("FINAEGIS_PASSWORD", "")
FINAEGIS_FROM_ACCOUNT = os.environ.get("FINAEGIS_FROM_ACCOUNT", "")

# Canal de notificaciones: mock | evolution | whatsapp_cloud
NOTIFY_PROVIDER = os.environ.get("NOTIFY_PROVIDER", "mock")
NOTIFY_APPROVER = os.environ.get("NOTIFY_APPROVER", "admin")
NOTIFY_APPROVER_NUMBER = os.environ.get("NOTIFY_APPROVER_NUMBER", "")

# Evolution API (docker: evoapicloud/evolution-api)
EVOLUTION_BASE_URL = os.environ.get("EVOLUTION_BASE_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.environ.get("EVOLUTION_INSTANCE", "payproof")

# WhatsApp Cloud API (oficial de Meta)
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")

# Deep link de aprobación: debe ser alcanzable desde el teléfono del aprobador
# (ngrok o IP LAN en el demo; localhost solo sirve en la misma máquina).
APPROVAL_LINK_BASE = os.environ.get("APPROVAL_LINK_BASE", "http://localhost:8501")
APPROVAL_TOKEN_TTL_MINUTES = int(os.environ.get("APPROVAL_TOKEN_TTL_MINUTES", "60"))
APPROVAL_HMAC_SECRET = os.environ.get("APPROVAL_HMAC_SECRET", "dev-secret-cambiar-en-produccion")

# Biometría: mock | webauthn
BIOMETRIC_PROVIDER = os.environ.get("BIOMETRIC_PROVIDER", "mock")
WEBAUTHN_RP_ID = os.environ.get("WEBAUTHN_RP_ID", "localhost")
WEBAUTHN_RP_NAME = os.environ.get("WEBAUTHN_RP_NAME", "PayProof")
WEBAUTHN_ORIGIN = os.environ.get("WEBAUTHN_ORIGIN", "http://localhost:8501")
