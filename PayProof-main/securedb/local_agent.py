"""Agente de interpretación PROPIO (reemplaza a Groq/Ollama).

Usa el modelo entrenado desde cero en `payroll_ai` para convertir una instrucción en una
`OperationProposal`. El modelo solo interpreta; la clasificación de riesgo, los roles y las
aprobaciones siguen siendo código determinista (risk_engine / approval), como antes.
"""
from __future__ import annotations

from typing import Optional

from payroll_ai.columns import FINANCIAL
from payroll_ai.engine import PayrollAssistant, POLICY
from payroll_ai.text import norm, parse_number

from .database import get_conn
from .models import OperationProposal

_bot: Optional[PayrollAssistant] = None

KEY_BY_CONCEPT = {
    "employee_id": "id", "document_id": "id", "first_name": "nombre", "last_name": "apellido",
    "full_name": "nombre", "department": "departamento", "position": "cargo", "email": "email",
}
UPDATE_BY_CONCEPT = {"salary": "salario", "position": "cargo", "email": "email"}


def _model() -> PayrollAssistant:
    global _bot
    if _bot is None:
        _bot = PayrollAssistant.load()
    return _bot


def _canonical(conn, table_sql: str, value: str) -> Optional[str]:
    for (name,) in conn.execute(table_sql).fetchall():
        if norm(name) == norm(value):
            return name
    return None


def _filters(parsed, bot, conn) -> dict:
    filters, pending = {}, None
    for sp in parsed.spans:
        if sp.tag in ("CCOL", "CLT", "CLE", "CGT", "CGE", "CVAL"):
            raise ValueError("Las condiciones numéricas (p. ej. 'salario menor a X') no están soportadas en la base de "
                             "PayProof; indica departamento, cargo, id o nombre.")
        if sp.tag == "FCOL":
            pending = sp.text
        elif sp.tag == "FVAL":
            val = sp.text
            if pending:
                concept = bot.mapper.classify(pending, None)[0]
                key = KEY_BY_CONCEPT.get(concept)
                if key is None:
                    raise ValueError(f"No puedo filtrar por '{pending}'.")
                pending = None
            elif val.isdigit():
                key = "id"
            elif _canonical(conn, "SELECT nombre FROM departments", val):
                key = "departamento"
            elif _canonical(conn, "SELECT DISTINCT cargo FROM employees", val):
                key = "cargo"
            else:
                key = "nombre"
            if key == "departamento":
                val = _canonical(conn, "SELECT nombre FROM departments", val) or val
            elif key == "cargo":
                val = _canonical(conn, "SELECT DISTINCT cargo FROM employees", val) or val
            elif key == "nombre" and " " in val:
                first, _, last = val.partition(" ")
                filters["nombre"], filters["apellido"] = first, val.split()[-1]
                continue
            filters[key] = val
    return filters


def extract_proposal(instruction: str) -> OperationProposal:
    bot = _model()
    parsed = bot.model.parse(instruction)
    intent = parsed.intent
    if intent in ("drop_table", "drop_database", "modify_audit", "alter_credentials"):
        target = {"drop_table": "empleados", "drop_database": "sistema", "modify_audit": "auditoria",
                  "alter_credentials": "sistema"}[intent]
        return OperationProposal(operation_type=intent, target=target, intent=instruction, reversible=False)
    if intent == "unknown" or parsed.intent_conf < POLICY["min_intent_conf"]:
        raise ValueError("No pude interpretar la instrucción. Indica la acción, la columna y a quiénes aplica.")

    fields = [s for s in parsed.spans if s.tag == "FIELD"]
    concepts = [bot.mapper.classify(f.text, None)[0] for f in fields]

    with get_conn() as conn:
        if intent == "notify":
            msg = next((s.text for s in parsed.spans if s.tag == "MSG"), instruction)
            has_all = any(s.tag == "ALL" for s in parsed.spans)
            filters = _filters(parsed, bot, conn)
            dest = ", ".join(str(v) for v in filters.values()) or "Todos los empleados"
            scope = "large_group" if has_all else ("small_group" if set(filters) & {"departamento", "cargo"} else "individual")
            return OperationProposal(operation_type="notify", target="notificaciones", intent=instruction,
                                     recipient_scope=scope, filters={"destinatario": dest}, message=msg)

        filters = _filters(parsed, bot, conn)
        has_all = any(s.tag == "ALL" for s in parsed.spans)
        write = intent in ("set", "increase", "decrease", "delete")
        if write and not filters and not has_all:
            raise ValueError("Indica a quiénes aplicar el cambio (por ejemplo 'a todos' o 'a los de Ventas').")

        from .executor import _build_where
        where, params = _build_where(filters)
        count = conn.execute(f"SELECT COUNT(*) FROM employees{where}", params).fetchone()[0]
        if count == 0:
            raise ValueError("Ningún empleado coincide con el criterio indicado (revisa el departamento, cargo, id o nombre).")

        if intent == "select":
            fin = (not concepts) or any(c in FINANCIAL for c in concepts)
            pers = (not concepts) or any(c == "email" for c in concepts)
            return OperationProposal(operation_type="select", target="empleados", intent=instruction, records_affected=count,
                                     involves_financial=fin, involves_personal=pers, filters=filters)
        if intent == "delete":
            return OperationProposal(operation_type="delete", target="empleados", intent=instruction, records_affected=count,
                                     reversible=False, involves_personal=True, filters=filters)

        # ---- update
        if intent in ("increase", "decrease"):
            concept = concepts[0] if concepts else "salary"
            col = UPDATE_BY_CONCEPT.get(concept)
            if col != "salario":
                raise ValueError("Solo se puede ajustar el salario en esta base.")
            pct = [s for s in parsed.spans if s.tag == "AMT_PCT"]
            ab = [s for s in parsed.spans if s.tag == "AMT_ABS"]
            if len(pct) + len(ab) != 1:
                raise ValueError("Indica un único monto (por ejemplo '10%' o '200000').")
            sign = 1 if intent == "increase" else -1
            if pct:
                amount = parse_number(pct[0].text)
                if amount is None or amount <= 0 or (sign < 0 and amount >= 100):
                    raise ValueError("Porcentaje inválido.")
                change = {"pct": sign * amount}
            else:
                amount = parse_number(ab[0].text)
                if amount is None or amount <= 0:
                    raise ValueError("Monto inválido.")
                change = {"abs": sign * amount}
            return OperationProposal(operation_type="update", target="empleados", intent=instruction, records_affected=count,
                                     involves_financial=True, filters=filters, changes={"salario": change})

        # intent == "set"
        vals = [s.text for s in parsed.spans if s.tag == "VALUE"]
        if len(vals) != 1:
            raise ValueError("Indica el nuevo valor (uno solo).")
        concept = concepts[0] if concepts else ("position" if not vals[0].replace(".", "").isdigit() else "salary")
        col = UPDATE_BY_CONCEPT.get(concept)
        if col is None:
            raise ValueError("Solo se pueden modificar salario, cargo o email en esta base.")
        value = vals[0]
        if col == "salario":
            num = parse_number(value)
            if num is None or num < 0:
                raise ValueError("Salario inválido.")
            value = num
        return OperationProposal(operation_type="update", target="empleados", intent=instruction, records_affected=count,
                                 involves_financial=col == "salario", involves_personal=col == "email",
                                 filters=filters, changes={col: value})
