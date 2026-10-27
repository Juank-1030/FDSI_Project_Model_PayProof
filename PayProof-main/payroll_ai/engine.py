"""Motor determinista: convierte lo que dice el modelo en una operación sobre el CSV y
decide, con reglas fijas, si se ejecuta, necesita aprobación o se bloquea.

REGLA DE ORO: el modelo (IA) solo INTERPRETA. La autorización por rol vive aquí, en código
determinista que el modelo no puede influir.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .columns import CONCEPTS, FINANCIAL, PERSONAL, ColumnMapper
from .csvio import NumFormat, Table, detect_format
from .model import InstructionModel, Parsed, Span
from .text import norm, parse_number

# --------------------------------------------------------------------------- #
# Roles y política (editable)
# --------------------------------------------------------------------------- #
ROLE_LEVEL = {"usuario": 0, "operador": 1, "gerente": 2, "supervisor": 2, "administrador": 3, "admin": 3}
LEVEL_ROLE = {0: "usuario", 1: "operador", 2: "gerente", 3: "administrador", 4: "administrador"}
RISK_LABEL = {0: "Ninguno", 1: "Bajo", 2: "Medio", 3: "Alto", 4: "Crítico"}

POLICY = {
    "pct_big": 20.0,          # un ajuste porcentual mayor a esto sube el riesgo +1
    "rows_many": 10,          # más de N filas afectadas sube el riesgo +1 (selección financiera)
    "min_intent_conf": 0.60,  # confianza mínima del modelo para actuar
    "min_span_conf": 0.45,
}

PROHIBITED = {"drop_table", "drop_database", "modify_audit", "alter_credentials"}
SALARY_VERBS = {"gana", "ganan", "ganen", "cobra", "cobran", "cobren", "recibe", "reciben", "percibe", "perciben"}

DEFAULT_WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights")


# --------------------------------------------------------------------------- #
# Esquema inferido del CSV
# --------------------------------------------------------------------------- #
@dataclass
class ColumnInfo:
    idx: int
    header: str
    concept: str
    conf: float
    fmt: Optional[NumFormat] = None


@dataclass
class Schema:
    cols: List[ColumnInfo]

    def by_concept(self, concept: str) -> List[ColumnInfo]:
        return [c for c in self.cols if c.concept == concept]

    def describe(self) -> str:
        return "\n".join(f"  [{c.idx}] {c.header!r:28} -> {c.concept:12} ({c.conf:.0%})" for c in self.cols)


def _sim(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio()


def _tok_match(a: str, b: str) -> bool:
    return a == b or (len(a) >= 4 and len(b) >= 4 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.84)


def _toks(s: str) -> List[str]:
    return norm(s.replace(",", " ")).split()


def _name_match(vt: List[str], ct: List[str]) -> bool:
    return bool(vt) and all(any(_tok_match(v, c) for c in ct) for v in vt)


def _eq_match(value: str, cell: str) -> bool:
    v, c = norm(value), norm(cell)
    if v == c:
        return True
    if re.fullmatch(r"\d+", v) and re.fullmatch(r"\d+", c):
        return v.lstrip("0") == c.lstrip("0")
    vt, ct = v.split(), c.split()
    if len(vt) == len(ct) and all(_tok_match(a, b) for a, b in zip(vt, ct)):
        return True
    return False


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
@dataclass
class CellChange:
    row: int
    col: int
    old: str
    new: str


@dataclass
class Plan:
    instruction: str
    role: str
    decision: str                      # ALLOW | NEEDS_APPROVAL | BLOCKED | CLARIFY | ERROR
    message: str = ""
    kind: str = ""                     # select | set | increase | decrease | delete | notify
    intent: str = ""
    intent_conf: float = 0.0
    column: Optional[int] = None
    column_header: str = ""
    column_concept: str = ""
    amount: Optional[float] = None
    amount_unit: str = ""              # pct | abs
    value: Optional[str] = None
    rows: List[int] = field(default_factory=list)
    columns: List[int] = field(default_factory=list)          # para select
    changes: List[CellChange] = field(default_factory=list)
    risk_level: int = 0
    min_role: str = "usuario"
    requires_confirmation: bool = False
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    table_fingerprint: str = ""
    payload_hash: str = ""
    notify_message: str = ""

    def payload(self) -> dict:
        return {
            "kind": self.kind, "column": self.column, "amount": self.amount, "unit": self.amount_unit,
            "value": self.value, "rows": self.rows, "columns": self.columns,
            "changes": [(c.row, c.col, c.old, c.new) for c in self.changes], "table": self.table_fingerprint,
        }

    def compute_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.payload(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def explain(self, table: Optional[Table] = None, max_rows: int = 8) -> str:
        lines = [f"Instrucción : {self.instruction}",
                 f"Rol         : {self.role}",
                 f"Decisión    : {self.decision}"]
        if self.message:
            lines.append(f"Detalle     : {self.message}")
        if self.kind:
            lines.append(f"Operación   : {self.kind}  (confianza del modelo {self.intent_conf:.0%})")
        if self.column_header:
            lines.append(f"Columna     : {self.column_header} ({self.column_concept})")
        if self.rows or self.kind in ("increase", "decrease", "set", "delete"):
            lines.append(f"Filas       : {len(self.rows)}")
        lines.append(f"Riesgo      : {RISK_LABEL.get(self.risk_level, self.risk_level)} -> mínimo '{self.min_role}'"
                     + ("  (requiere confirmación explícita)" if self.requires_confirmation else ""))
        for r in self.reasons:
            lines.append(f"  - {r}")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        show_values = self.decision == "ALLOW" or not (self.column_concept in FINANCIAL)
        if self.changes and show_values:
            for c in self.changes[:max_rows]:
                who = ""
                if table is not None:
                    who = " | ".join(x for x in table.rows[c.row][:3])
                lines.append(f"    fila {c.row + 1}: {c.old} -> {c.new}   [{who}]")
            if len(self.changes) > max_rows:
                lines.append(f"    … y {len(self.changes) - max_rows} más")
        elif self.changes:
            lines.append("    (valores ocultos hasta que un rol autorizado apruebe)")
        return "\n".join(lines)

    def audit_record(self) -> dict:
        return {"instruction": self.instruction, "role": self.role, "decision": self.decision, "kind": self.kind,
                "rows": len(self.rows), "column": self.column_header, "risk": self.risk_level,
                "payload_hash": self.payload_hash}


@dataclass
class ExecResult:
    ok: bool
    message: str
    table: Optional[Table] = None
    selected: Optional[Table] = None
    changed: int = 0


def fingerprint(table: Table) -> str:
    return hashlib.sha256(json.dumps([table.header, table.rows], ensure_ascii=False).encode()).hexdigest()[:24]


# --------------------------------------------------------------------------- #
# Asistente
# --------------------------------------------------------------------------- #
class PayrollAssistant:
    def __init__(self, model: InstructionModel, mapper: ColumnMapper):
        self.model, self.mapper = model, mapper

    @classmethod
    def load(cls, weights_dir: str = DEFAULT_WEIGHTS) -> "PayrollAssistant":
        return cls(InstructionModel.load(os.path.join(weights_dir, "instructions")),
                   ColumnMapper.load(os.path.join(weights_dir, "columns")))

    # ---- esquema ----------------------------------------------------------------
    def analyze(self, table: Table, overrides: Optional[Dict[int, str]] = None) -> Schema:
        """overrides: {indice_columna: concepto} para corregir/forzar el significado de una columna."""
        cols = []
        for j, h in enumerate(table.header):
            vals = table.col(j)[:200]
            hdr = h if table.has_header else ""
            concept, conf, _ = self.mapper.classify(hdr, vals)
            if conf < 0.40:
                concept = "other"
            if overrides and j in overrides:
                concept, conf = overrides[j], 1.0
            fmt = detect_format([v for v in vals if v.strip()]) if concept in FINANCIAL else None
            cols.append(ColumnInfo(j, h, concept, conf, fmt))
        return Schema(cols)

    def _resolve_field(self, mention: str, schema: Schema) -> Tuple[Optional[ColumnInfo], str]:
        concept, conf, _ = self.mapper.classify(mention, None)
        cands = schema.by_concept(concept) if concept != "other" else []
        if not cands and concept in ("employee_id", "document_id"):      # 'cédula' vs 'id': columnas intercambiables como llave
            cands = schema.by_concept("employee_id" if concept == "document_id" else "document_id")
        if not cands:
            scored = sorted(((_sim(mention, c.header), c) for c in schema.cols), key=lambda x: -x[0])
            if scored and scored[0][0] >= 0.72:
                return scored[0][1], ""
            return None, f"No encuentro una columna que corresponda a '{mention}'."
        if len(cands) == 1:
            return cands[0], ""
        scored = sorted(((_sim(mention, c.header), c) for c in cands), key=lambda x: -x[0])
        if scored[0][0] - scored[1][0] < 0.08:
            return None, "'%s' es ambiguo, puede ser: %s." % (mention, ", ".join(c.header for c in cands))
        return scored[0][1], ""

    # ---- filtros ---------------------------------------------------------------
    def _rows_for(self, parsed: Parsed, table: Table, schema: Schema, write: bool) -> Tuple[Optional[List[int]], str, List[str]]:
        spans = parsed.spans
        warnings: List[str] = []
        n = len(table.rows)
        all_rows = set(range(n))
        groups: List[Tuple[object, set]] = []     # (clave, filas)  -> OR dentro de la misma clave, AND entre claves
        has_scope = any(s.tag == "ALL" for s in spans)

        pending_fcol: Optional[str] = None
        pending_ccol: Optional[str] = None
        pending_cmp: Optional[str] = None
        for sp in spans:
            if sp.tag == "FCOL":
                pending_fcol = sp.text
            elif sp.tag == "FVAL":
                has_scope = True
                col = None
                if pending_fcol:
                    col, err = self._resolve_field(pending_fcol, schema)
                    if col is None:
                        return None, err, warnings
                rows, key, err = self._match_value(sp.text, table, schema, col, write)
                pending_fcol = None
                if err:
                    return None, err, warnings
                groups.append((key, rows))
            elif sp.tag == "CCOL":
                pending_ccol = sp.text
            elif sp.tag in ("CLT", "CLE", "CGT", "CGE"):
                pending_cmp = sp.tag
            elif sp.tag == "CVAL":
                has_scope = True
                thr = parse_number(sp.text)
                if pending_cmp is None or thr is None:
                    return None, f"No pude interpretar la condición numérica '{sp.text}'.", warnings
                if pending_ccol:
                    col, err = self._resolve_field(pending_ccol, schema)
                    if col is None:
                        return None, err, warnings
                else:
                    c = schema.by_concept("salary")
                    if not c:
                        return None, "La condición numérica no indica columna y no hay columna de salario.", warnings
                    col = c[0]
                op = {"CLT": lambda a, b: a < b, "CLE": lambda a, b: a <= b, "CGT": lambda a, b: a > b, "CGE": lambda a, b: a >= b}[pending_cmp]
                rows = set()
                for i in range(n):
                    v = parse_number(table.rows[i][col.idx])
                    if v is not None and op(v, thr):
                        rows.add(i)
                groups.append((("cond", col.idx, pending_cmp), rows))
                pending_ccol = pending_cmp = None
        by_key: Dict[object, set] = {}
        for key, rows in groups:
            by_key[key] = by_key.get(key, set()) | rows
        result = set(all_rows)
        for rows in by_key.values():
            result &= rows
        if not has_scope:
            return None, "NOSCOPE", warnings
        return sorted(result), "", warnings

    def _match_value(self, value: str, table: Table, schema: Schema, col: Optional[ColumnInfo], write: bool):
        n = len(table.rows)
        name_cols = [c for c in schema.cols if c.concept in ("full_name", "first_name", "last_name")]
        vt = _toks(value)

        def name_rows():
            out = set()
            for i in range(n):
                ct = []
                for c in name_cols:
                    ct += _toks(table.rows[i][c.idx])
                if _name_match(vt, ct):
                    out.add(i)
            return out

        def check_amb(rows):
            if write and len(rows) > 1:
                ids = ", ".join(" ".join(table.rows[i][:2]) for i in sorted(rows)[:5])
                return f"'{value}' coincide con {len(rows)} empleados ({ids}…). Indica el id o el nombre completo."
            return ""

        if col is not None:
            if col.concept in ("full_name", "first_name", "last_name"):
                rows = name_rows()
                return rows, ("name",), check_amb(rows)
            rows = {i for i in range(n) if _eq_match(value, table.rows[i][col.idx])}
            if not rows:
                return None, None, f"No hay filas con {col.header} = '{value}'."
            return rows, ("col", col.idx), ""
        # sin columna explícita: se busca en qué columna aparece el valor
        best: Tuple[int, Optional[ColumnInfo], set] = (0, None, set())
        for c in schema.cols:
            if c.concept in ("employee_id", "document_id", "department", "position", "city", "status", "other") and not (c.concept == "other" and False):
                rows = {i for i in range(n) if _eq_match(value, table.rows[i][c.idx])}
                if len(rows) > best[0]:
                    best = (len(rows), c, rows)
        if best[1] is not None and best[1].concept != "other":
            return best[2], ("col", best[1].idx), ""
        if name_cols:
            rows = name_rows()
            if rows:
                return rows, ("name",), check_amb(rows)
        if best[1] is not None:
            return best[2], ("col", best[1].idx), ""
        return None, None, f"No encontré '{value}' en ninguna columna del archivo."

    # ---- interpretación y planificación -----------------------------------------
    def plan(self, instruction: str, table: Table, role: str = "usuario",
             overrides: Optional[Dict[int, str]] = None) -> Plan:
        role = role.lower().strip()
        if role not in ROLE_LEVEL:
            return Plan(instruction, role, "ERROR", message=f"Rol desconocido: '{role}'.")
        parsed = self.model.parse(instruction)
        p = Plan(instruction, role, "ERROR", intent=parsed.intent, intent_conf=parsed.intent_conf,
                 table_fingerprint=fingerprint(table))

        if parsed.intent in PROHIBITED:
            p.decision, p.kind = "BLOCKED", parsed.intent
            p.risk_level, p.message = 4, "Operación prohibida por política: bloqueo inmediato, sin excepción ni aprobación posible."
            p.reasons = ["Intención detectada: " + parsed.intent]
            p.payload_hash = p.compute_hash()
            return p
        if parsed.intent == "unknown" or parsed.intent_conf < POLICY["min_intent_conf"]:
            p.decision = "CLARIFY"
            p.message = "No estoy seguro de lo que quieres hacer. Reformula indicando acción, columna y a quiénes aplica."
            return p

        schema = self.analyze(table, overrides)
        p.kind = parsed.intent
        if not table.has_header and not overrides:
            p.warnings.append("El archivo no tiene encabezado: el significado de las columnas se dedujo solo por sus valores.")
        spans = parsed.spans
        weak = [s for s in spans if min(parsed.tag_conf[s.start:s.end]) < POLICY["min_span_conf"]]
        if weak:
            p.warnings.append("Baja confianza en: " + ", ".join(f"'{s.text}'" for s in weak))

        if parsed.intent == "notify":
            msg = next((s.text for s in spans if s.tag == "MSG"), "")
            p.notify_message, p.decision = msg, "ERROR"
            p.message = "Las notificaciones no modifican el CSV; se gestionan en el módulo de notificaciones."
            return p

        write = parsed.intent in ("set", "increase", "decrease", "delete")
        rows, err, warns = self._rows_for(parsed, table, schema, write)
        p.warnings += warns
        if rows is None:
            if err == "NOSCOPE":
                if write:
                    p.decision = "CLARIFY"
                    p.message = "No indicaste a quiénes aplicar el cambio (por ejemplo 'a todos', 'a los de ventas', 'a Juan Pérez')."
                    return p
                rows = list(range(len(table.rows)))
            else:
                p.decision, p.message = "CLARIFY", err
                return p
        p.rows = rows

        # ---- columna objetivo
        field_spans = [s for s in spans if s.tag == "FIELD"]
        col: Optional[ColumnInfo] = None
        cols: List[ColumnInfo] = []
        for fs in field_spans:
            c, e = self._resolve_field(fs.text, schema)
            if c is None:
                p.decision, p.message = "CLARIFY", e
                return p
            cols.append(c)
        if parsed.intent in ("increase", "decrease") and not cols:
            sal = schema.by_concept("salary")
            if not sal:
                p.decision, p.message = "CLARIFY", "No encuentro la columna de salario; indica qué columna ajustar."
                return p
            cols = [sal[0]]
        if parsed.intent == "select" and not cols and any(t.norm in SALARY_VERBS for t in parsed.tokens):
            sal = schema.by_concept("salary")
            cols = sal[:1]
        if parsed.intent == "set" and not cols:
            val = next((s.text for s in spans if s.tag == "VALUE"), None)
            c = self._field_from_value(val, table, schema) if val else None
            if c is None:
                p.decision, p.message = "CLARIFY", "No indicaste qué columna cambiar."
                return p
            cols = [c]
        if cols and parsed.intent != "select":
            col = cols[0]
            p.column, p.column_header, p.column_concept = col.idx, col.header, col.concept

        # ---- operación
        if parsed.intent == "select":
            idc = [c.idx for c in schema.cols if c.concept in ("employee_id", "full_name", "first_name", "last_name")][:3]
            p.columns = idc + [c.idx for c in cols if c.idx not in idc] if cols else list(range(len(schema.cols)))
            p.column_concept = "financial" if any(schema.cols[j].concept in FINANCIAL for j in p.columns) else ""
        elif parsed.intent in ("increase", "decrease"):
            if col.concept not in FINANCIAL:
                p.decision, p.message = "CLARIFY", f"La columna '{col.header}' no es numérica de nómina; no puedo aplicar un aumento/reducción."
                return p
            pct = [s for s in spans if s.tag == "AMT_PCT"]
            ab = [s for s in spans if s.tag == "AMT_ABS"]
            if len(pct) + len(ab) != 1:
                p.decision, p.message = "CLARIFY", "Indica un único monto (por ejemplo '10%' o '200000')."
                return p
            if pct:
                p.amount, p.amount_unit = parse_number(pct[0].text), "pct"
            else:
                p.amount, p.amount_unit = parse_number(ab[0].text), "abs"
            if p.amount is None or p.amount <= 0:
                p.decision, p.message = "CLARIFY", "El monto no es válido."
                return p
            if parsed.intent == "decrease" and p.amount_unit == "pct" and p.amount >= 100:
                p.decision, p.message = "ERROR", "No se puede reducir 100% o más."
                return p
            self._build_changes(p, table, schema, col)
            if p.decision == "ERROR" and p.message:
                return p
        elif parsed.intent == "set":
            vals = [s.text for s in spans if s.tag == "VALUE"]
            if len(vals) != 1:
                p.decision, p.message = "CLARIFY", "Indica el nuevo valor (uno solo)."
                return p
            p.value = vals[0]
            err = self._build_set(p, table, schema, col)
            if err:
                p.decision, p.message = "ERROR", err
                return p
        elif parsed.intent == "delete":
            for i in rows:
                p.changes.append(CellChange(i, -1, "fila", "(eliminada)"))

        if write and not p.rows:
            p.decision, p.message = "CLARIFY", "Ninguna fila cumple el criterio indicado."
            return p
        return self._decide(p, table, schema)

    def _field_from_value(self, value, table, schema):
        for c in schema.cols:
            if c.concept in ("position", "department", "city", "status"):
                if any(_eq_match(value, table.rows[i][c.idx]) for i in range(len(table.rows))):
                    return c
        return None

    def _build_changes(self, p: Plan, table: Table, schema: Schema, col: ColumnInfo):
        fmt = col.fmt or NumFormat()
        skipped = 0
        for i in p.rows:
            old = table.rows[i][col.idx]
            v = parse_number(old)
            if v is None:
                skipped += 1
                continue
            if p.amount_unit == "pct":
                nv = v * (1 + p.amount / 100) if p.kind == "increase" else v * (1 - p.amount / 100)
            else:
                nv = v + p.amount if p.kind == "increase" else v - p.amount
            if nv < 0:
                p.decision, p.message = "ERROR", f"El resultado sería negativo en la fila {i + 1}."
                return
            p.changes.append(CellChange(i, col.idx, old, fmt.render(nv)))
        if skipped:
            p.warnings.append(f"{skipped} fila(s) con valor no numérico en '{col.header}' se dejaron sin cambios.")
        p.rows = [c.row for c in p.changes]
        if p.rows and table.post:
            p.warnings.append("Hay filas de totales al final; no se recalculan automáticamente.")

    def _build_set(self, p: Plan, table: Table, schema: Schema, col: ColumnInfo) -> str:
        raw = p.value
        if col.concept in FINANCIAL:
            v = parse_number(raw)
            if v is None or v < 0:
                return f"'{raw}' no es un valor numérico válido para '{col.header}'."
            new = (col.fmt or NumFormat()).render(v)
        elif col.concept == "email":
            if not re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", raw):
                return f"'{raw}' no parece un correo válido."
            new = raw
        else:
            new = raw
            for i in range(len(table.rows)):              # respeta la grafía ya usada en el archivo
                if norm(table.rows[i][col.idx]) == norm(raw):
                    new = table.rows[i][col.idx]
                    break
        p.value = new
        for i in p.rows:
            if table.rows[i][col.idx] != new:
                p.changes.append(CellChange(i, col.idx, table.rows[i][col.idx], new))
        p.rows = [c.row for c in p.changes] if p.changes else p.rows
        return ""

    # ---- política de riesgo ------------------------------------------------------
    def _decide(self, p: Plan, table: Table, schema: Schema) -> Plan:
        n = len(p.rows)
        fin = p.column_concept in FINANCIAL or p.column_concept == "financial"
        pers = p.column_concept in PERSONAL
        lvl, reasons = 0, []
        if p.kind == "select":
            if fin:
                lvl += 1
                reasons.append("Consulta datos financieros (+1)")
                if n > POLICY["rows_many"]:
                    lvl += 1
                    reasons.append(f"Consulta masiva de datos financieros ({n} filas) (+1)")
            if any(schema.cols[j].concept in PERSONAL for j in p.columns):
                lvl += 1
                reasons.append("Incluye datos personales (+1)")
        elif p.kind in ("set", "increase", "decrease"):
            if n > 1:
                lvl = 4 if fin else 2
                reasons.append(f"Modificación masiva ({n} filas)" + (" de datos financieros" if fin else ""))
            else:
                lvl = 3 if fin else 1
                reasons.append("Modificación de datos financieros" if fin else "Modificación de un dato")
            if p.amount_unit == "pct" and p.amount and p.amount > POLICY["pct_big"]:
                lvl += 1
                reasons.append(f"Ajuste porcentual grande (>{POLICY['pct_big']:.0f}%) (+1)")
            if pers:
                lvl += 1
                reasons.append("Datos personales involucrados (+1)")
        elif p.kind == "delete":
            lvl = 4
            reasons.append("Eliminación de registros (irreversible sin respaldo)")
        lvl = min(lvl, 4)
        p.risk_level, p.reasons = lvl, reasons
        p.min_role = LEVEL_ROLE[lvl]
        p.requires_confirmation = lvl == 4
        if ROLE_LEVEL[p.role] >= min(lvl, 3):          # el nivel crítico exige administrador + confirmación
            p.decision = "ALLOW"
        else:
            p.decision = "NEEDS_APPROVAL"
            p.message = f"Requiere aprobación de un '{p.min_role}' o superior."
        p.payload_hash = p.compute_hash()
        return p

    # ---- ejecución -------------------------------------------------------------
    def execute(self, plan: Plan, table: Table, approver_role: Optional[str] = None, confirm: bool = False) -> ExecResult:
        if plan.decision == "BLOCKED":
            return ExecResult(False, "Bloqueado por política.")
        if plan.decision in ("CLARIFY", "ERROR"):
            return ExecResult(False, plan.message or "El plan no es ejecutable.")
        if plan.compute_hash() != plan.payload_hash:
            return ExecResult(False, "TAMPER_PAYLOAD: el plan fue alterado después de evaluarse.")
        if plan.table_fingerprint != fingerprint(table):
            return ExecResult(False, "El archivo cambió desde que se evaluó el plan; vuelve a generarlo.")
        auth = ROLE_LEVEL[plan.role]
        if plan.decision == "NEEDS_APPROVAL":
            if approver_role is None or ROLE_LEVEL.get(approver_role.lower(), -1) < min(plan.risk_level, 3):
                return ExecResult(False, f"Falta aprobación de un '{plan.min_role}' o superior.")
            if approver_role.lower() == plan.role and False:
                pass
            auth = ROLE_LEVEL[approver_role.lower()]
        if plan.requires_confirmation and not confirm:
            return ExecResult(False, "Operación de riesgo crítico: requiere confirmación explícita (confirm=True).")
        if auth < min(plan.risk_level, 3):
            return ExecResult(False, "Autoridad insuficiente.")

        if plan.kind == "select":
            cols = plan.columns
            sel = Table(table.delimiter, [], [table.header[j] for j in cols], True,
                        [[table.rows[i][j] for j in cols] for i in plan.rows], [], table.newline)
            return ExecResult(True, f"{len(sel.rows)} fila(s) consultadas.", None, sel)
        new = table.copy()
        if plan.kind == "delete":
            drop = set(plan.rows)
            new.rows = [r for i, r in enumerate(new.rows) if i not in drop]
            return ExecResult(True, f"{len(drop)} fila(s) eliminadas.", new, None, len(drop))
        for c in plan.changes:
            new.rows[c.row][c.col] = c.new
        return ExecResult(True, f"{len(plan.changes)} celda(s) modificadas en '{plan.column_header}'.", new, None, len(plan.changes))
