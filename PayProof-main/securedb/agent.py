import json
import re

from . import config
from .models import OperationProposal

SYSTEM_PROMPT = """Eres el agente de interpretación de SecureDB, un sistema de gobernanza para agentes de IA con acceso a una base de datos simulada de empleados y a un módulo de pagos a proveedores. NO ejecutas ninguna operación: solo interpretas la instrucción del usuario y extraes una propuesta estructurada que luego pasará por un motor de clasificación de riesgo y un flujo de aprobación humana.

Reglas de interpretación:
- operation_type debe ser uno de: select, update, delete, notify, drop_table, drop_database, modify_audit, alter_credentials, pay.
- target indica la tabla o recurso destino: empleados, departamentos, notificaciones, auditoria, sistema o pagos.
- intent resume en una frase lo que el usuario quiere hacer.
- records_affected es el número estimado de registros afectados (1 si es uno solo, un número aproximado si son varios).
- involves_financial es true si la operación toca salarios, presupuestos, balances u otros datos financieros.
- involves_personal es true si toca datos personales sensibles (documentos de identidad, direcciones, teléfonos).
- reversible es false si la operación no tiene reversión sencilla (por ejemplo, eliminaciones o sobreescrituras sin respaldo).
- recipient_scope aplica solo a notificaciones: individual (una persona), small_group (equipo pequeño) o large_group (departamento o grupo grande).
- filters es un diccionario con filtros para localizar registros usando las columnas: id, nombre, apellido, cargo, email o departamento.
- changes es un diccionario con columnas a modificar y su nuevo valor: salario, cargo o email.
- message es el texto del mensaje si se trata de una notificación, o el motivo/descripción si es un pago.
- Para pagos (operation_type=pay): vendor_name es el nombre del proveedor beneficiario, bank_code el banco del beneficiario, account_number la cuenta o IBAN EXACTAMENTE como lo indique el usuario (sin inventar ni corregir dígitos), amount el monto numérico y currency el código de divisa (EUR, USD, ...). No modifiques la cuenta que diga el usuario: si viene con espacios o letras, consérvalos tal cual.
- Si la instrucción pide vaciar o eliminar tablas completas, usa drop_table. Si pide eliminar toda la base de datos, usa drop_database. Si pide tocar el log de auditoría, usa modify_audit. Si pide cambiar roles o credenciales, usa alter_credentials."""

_llm = None


def _get_llm():
    global _llm
    if _llm is None:
        if config.LLM_PROVIDER == "groq":
            from langchain_openai import ChatOpenAI

            _llm = ChatOpenAI(
                model=config.GROQ_MODEL,
                api_key=config.GROQ_API_KEY,
                base_url=config.GROQ_BASE_URL,
                temperature=0,
            )
        else:
            from langchain_ollama import ChatOllama

            _llm = ChatOllama(
                model=config.OLLAMA_MODEL,
                base_url=config.OLLAMA_BASE_URL,
                temperature=0,
            )
    return _llm


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    raw = match.group(0) if match else text
    return json.loads(raw)


def extract_proposal(instruction: str) -> OperationProposal:
    if config.LLM_PROVIDER == "local":
        # Modelo propio entrenado desde cero (payroll_ai): sin APIs ni pesos de terceros.
        from . import local_agent

        return local_agent.extract_proposal(instruction)
    llm = _get_llm()
    prompt = f"{SYSTEM_PROMPT}\n\nInstrucción del usuario: {instruction}"
    try:
        structured = llm.with_structured_output(OperationProposal)
        result = structured.invoke(prompt)
        if isinstance(result, OperationProposal):
            return result
        if isinstance(result, dict):
            return OperationProposal.model_validate(result)
        raise ValueError("Respuesta estructurada inesperada del modelo.")
    except Exception:
        raw = llm.invoke(
            f"{SYSTEM_PROMPT}\n\nResponde únicamente con un objeto JSON válido.\n\nInstrucción del usuario: {instruction}"
        )
        text = raw.content if hasattr(raw, "content") else str(raw)
        data = _extract_json(text)
        return OperationProposal.model_validate(data)
