"""Mapeador de columnas: decide qué significa cada columna de un CSV sin estructura fija.

Es un modelo entrenado (ver `train_columns`) que combina:
  * el texto del encabezado (n-gramas de caracteres + palabras) y
  * un perfil estadístico de los valores de la columna (¿numéricos? ¿emails? ¿fechas?…)
Si el encabezado no existe o es genérico ("col3"), se apoya en los valores.
También se usa para entender la mención de un campo en la instrucción ("sueldo base").
"""
from __future__ import annotations

import os
import random
import re
from typing import Dict, List, Sequence

import numpy as np

from .datagen import CITIES, DEPTS, FIRST, LAST, POSITIONS
from .nn import Adam, BagMLP
from .text import norm, parse_number, stable_hash, strip_accents, char_ngrams

CONCEPTS = ["employee_id", "first_name", "last_name", "full_name", "email", "phone", "department",
            "position", "salary", "bonus", "deductions", "net_pay", "hire_date", "document_id",
            "status", "city", "other"]
C2I = {c: i for i, c in enumerate(CONCEPTS)}
FINANCIAL = {"salary", "bonus", "deductions", "net_pay"}
PERSONAL = {"email", "phone", "document_id"}
NUMERIC = {"salary", "bonus", "deductions", "net_pay"}

N_PROF = 22
HB_V = 4096
HB_VC = 4096
HB_W = 4096
HB_C = 8192
HB_B = 2048

HEADERS: Dict[str, List[str]] = {
    "employee_id": ["id", "id empleado", "codigo empleado", "cod empleado", "emp id", "employee id", "legajo", "numero empleado",
                    "no empleado", "n empleado", "codigo", "id_emp", "nro empleado", "employee number", "matricula", "emp no", "id trabajador"],
    "first_name": ["nombre", "nombres", "primer nombre", "first name", "name", "nom", "nombre pila", "given name", "nombre empleado"],
    "last_name": ["apellido", "apellidos", "last name", "surname", "ape", "family name", "apellido paterno", "primer apellido"],
    "full_name": ["nombre completo", "empleado", "full name", "nombre y apellido", "trabajador", "colaborador", "nombre empleado completo",
                  "nombres y apellidos", "apellidos y nombres", "employee name", "persona", "funcionario"],
    "email": ["email", "correo", "correo electronico", "e-mail", "mail", "email corporativo", "correo corporativo", "email address",
              "correo empresa", "mail trabajo", "e mail"],
    "phone": ["telefono", "celular", "movil", "phone", "tel", "telefono contacto", "cel", "mobile", "numero celular", "whatsapp"],
    "department": ["departamento", "area", "depto", "dpto", "department", "dept", "seccion", "dependencia", "division", "area trabajo",
                   "unidad", "gerencia", "centro de trabajo", "departamento empleado"],
    "position": ["cargo", "puesto", "position", "job title", "titulo", "rol laboral", "cargo actual", "posicion", "ocupacion", "job",
                 "denominacion cargo", "nombre cargo", "funcion"],
    "salary": ["salario", "sueldo", "salario base", "sueldo base", "salary", "base salary", "sal base", "sueldo mensual", "salario mensual",
               "remuneracion", "basico", "salario basico", "sueldo basico", "monthly salary", "asignacion basica", "paga", "ingreso mensual",
               "sueldo bruto", "salario bruto", "gross salary", "sal", "valor salario", "sueldo contractual"],
    "bonus": ["bono", "bonificacion", "bonus", "prima", "incentivo", "comision", "bonificaciones", "comisiones", "bono desempeno",
              "prima servicios", "variable", "bono anual", "commission"],
    "deductions": ["descuentos", "deducciones", "retencion", "deductions", "retenciones", "descuento", "aportes", "total descuentos",
                   "deduccion", "salud pension", "tax"],
    "net_pay": ["salario neto", "sueldo neto", "neto a pagar", "net pay", "total neto", "pago neto", "neto", "net salary", "total a pagar",
                "valor neto", "liquido a pagar"],
    "hire_date": ["fecha ingreso", "fecha de ingreso", "fecha contratacion", "hire date", "ingreso", "fecha entrada", "start date",
                  "fecha inicio", "f ingreso", "fecha vinculacion", "antiguedad desde", "date hired"],
    "document_id": ["cedula", "documento", "dni", "cc", "identificacion", "nit", "numero documento", "document id", "no documento", "rut",
                    "curp", "pasaporte", "numero identificacion", "tipo y numero documento", "nro documento"],
    "status": ["estado", "estatus", "status", "situacion", "activo", "estado laboral", "estado empleado", "condicion"],
    "city": ["ciudad", "sede", "city", "ubicacion", "sucursal", "municipio", "oficina", "location", "lugar de trabajo"],
    "other": ["observaciones", "notas", "comentarios", "jefe inmediato", "centro de costo", "tipo contrato", "banco", "cuenta bancaria",
              "eps", "arl", "horas extras", "genero", "fecha nacimiento", "direccion", "nivel educativo", "talla", "turno", "supervisor",
              "numero cuenta", "tipo de sangre", "observacion", "notes", "comments", "dias trabajados", "vacaciones", "edad", "estado civil",
              "nacionalidad", "contacto emergencia", "licencia", "horario", "id interno", "ref", "codigo centro costo",
              "quincena", "tarea desempenada", "horas trabajadas", "turno noche", "dias de licencia", "tipo de nomina", "periodo", "mes",
              "ano", "comentario jefe", "eps afiliada", "fondo pensiones", "caja compensacion", "motivo retiro", "fecha retiro",
              "nivel riesgo", "talla camisa", "contacto", "parentesco", "beneficiario", "horas nocturnas", "recargo nocturno", "auxilio transporte",
              "dotacion", "linea", "proyecto", "cliente", "codigo proyecto", "grupo", "categoria", "nivel", "grado", "escala", "version", "firma",
              "token", "hash", "created at", "updated at", "idx", "unnamed", "foto", "url", "observacion rrhh", "calificacion", "meta", "puntaje"],
}
GENERIC_HEADERS = ["", "col1", "col 2", "Column 3", "Unnamed: 4", "field_5", "0", "1", "A", "B", "C", "x", "Campo 6", "var7", "V8", "columna 9", "Unnamed: 0"]


def split_header(h: str) -> List[str]:
    h = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(h))
    h = re.sub(r"[_\-./\\()\[\]:#]+", " ", h)
    return [w for w in norm(h).split() if w]


def header_ids(header: str) -> List[int]:
    words = split_header(header)
    ids: List[int] = []
    for w in words:
        ids.append(1 + stable_hash("w:" + w, HB_W))
        for g in char_ngrams(w, ns=(3, 4), cap=12):
            ids.append(1 + HB_W + stable_hash(g, HB_C))
    for a, b in zip(words, words[1:]):
        ids.append(1 + HB_W + HB_C + stable_hash(a + "_" + b, HB_B))
    return ids or [1 + HB_W + HB_C + HB_B]   # id reservado: sin encabezado


VOCAB = 3 + HB_W + HB_C + HB_B + HB_V + HB_VC
_VBASE = 2 + HB_W + HB_C + HB_B


# --------------------------------------------------------------------------- #
# Perfil estadístico de una columna
# --------------------------------------------------------------------------- #
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")
DATE_RE = re.compile(r"^(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4})([ T]\d{1,2}:\d{2}.*)?$")
PHONE_RE = re.compile(r"^\+?[\d\s\-()]{7,}$")


def profile(values: Sequence[str]) -> np.ndarray:
    vals = [str(v).strip() for v in values]
    n = max(len(vals), 1)
    ne = [v for v in vals if v != ""]
    m = max(len(ne), 1)
    nums = [(v, parse_number(v)) for v in ne]
    isnum = [(v, x) for v, x in nums if x is not None and re.fullmatch(r"[\s$€£A-Za-z.,\d\-()]*", v) and re.search(r"\d", v)
             and not DATE_RE.match(v) and not EMAIL_RE.match(v) and len(re.sub(r"[^A-Za-z]", "", v)) <= 3]
    ints = [v for v, x in isnum if re.fullmatch(r"-?\d+", v)]
    logs = np.array([np.log10(abs(x) + 1) for _, x in isnum]) if isnum else np.zeros(1)
    f = np.zeros(N_PROF, np.float32)
    f[0] = (n - len(ne)) / n
    f[1] = len(isnum) / m
    f[2] = len(ints) / m
    f[3] = min(float(logs.mean()) / 10, 1.5) if isnum else 0
    f[4] = min(float(logs.std()) / 3, 1.5) if isnum else 0
    f[5] = sum(bool(EMAIL_RE.match(v)) for v in ne) / m
    f[6] = sum(bool(DATE_RE.match(v)) for v in ne) / m
    f[7] = sum(bool(re.fullmatch(r"[^\W\d_]+(?:[ '\-.][^\W\d_]+)*", v)) for v in ne) / m
    f[8] = min(np.mean([len(v) for v in ne]) / 30 if ne else 0, 2)
    f[9] = sum(" " in v for v in ne) / m
    f[10] = sum(v[:1].isupper() for v in ne) / m
    f[11] = len(set(ne)) / m
    f[12] = sum(bool(PHONE_RE.match(v)) and len(re.sub(r"\D", "", v)) >= 7 and not re.fullmatch(r"\d+", v) for v in ne) / m
    f[13] = sum(bool(re.search(r"[$€£]|\d[.,]\d{3}", v)) for v in ne) / m
    f[14] = sum(bool(re.search(r"\d", v)) and bool(re.search(r"[A-Za-z]", v)) for v in ne) / m
    f[15] = sum(v.isupper() and len(v) > 1 for v in ne) / m
    f[16] = 1.0  # hay valores
    if isnum:
        xs = [abs(x) for _, x in isnum]
        f[17] = sum(x % 1000 == 0 for x in xs) / len(xs)
        f[18] = sum(x % 100 == 0 for x in xs) / len(xs)
    f[19] = min(float(np.std([len(v) for v in ne])) / 5, 2) if ne else 0
    f[20] = sum(len(re.findall(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+", v)) >= 2 for v in ne) / m
    f[21] = min(np.log10(len(set(ne)) + 1) / 3, 1.5)
    return f


def value_ids(values: Sequence[str], k: int = 14) -> List[int]:
    """Ids de las palabras (y trigramas) de algunos valores distintos de la columna."""
    seen, ids = set(), []
    for v in values:
        v = str(v).strip()
        if not v or v in seen or parse_number(v) is not None and re.fullmatch(r"[\d.,\s$-]+", v):
            continue
        seen.add(v)
        for w in re.findall(r"[^\W\d_]{3,}", norm(v))[:3]:
            ids.append(_VBASE + 1 + stable_hash("v:" + w, HB_V))
            for g in char_ngrams(w, ns=(3,), cap=4):
                ids.append(_VBASE + 1 + HB_V + stable_hash(g, HB_VC))
        if len(seen) >= k:
            break
    return ids


def col_ids(header: str, values) -> List[int]:
    return header_ids(header) + (value_ids(values) if values is not None and len(values) else [])


# --------------------------------------------------------------------------- #
# Datos sintéticos de columnas
# --------------------------------------------------------------------------- #
def _fmt_money(r, v):
    k = r.random()
    s = str(int(v))
    if k < 0.45:
        return s
    sep = r.choice([".", ","])
    parts = []
    while s:
        parts.append(s[-3:]); s = s[:-3]
    out = sep.join(reversed(parts))
    if r.random() < 0.25:
        out = r.choice(["$", "$ ", "COP ", "USD "]) + out
    return out


def gen_values(concept: str, r: random.Random, n: int) -> List[str]:
    ps = lambda: r.choice(FIRST) + " " + r.choice(LAST)
    out: List[str] = []
    mag = r.choice([(800_000, 15_000_000, 1), (1_000_000, 9_000_000, 1), (800, 9_000, 1), (1_200, 6_000, 1), (20_000, 400_000, 1)])
    base_id = r.randint(1, 5000)
    ccs = r.choice([8, 10, 10, 9])
    sep_e = r.choice(["", ".", "_"])
    dom = r.choice(["empresa.com", "corp.co", "mail.com", "gmail.com"])
    datefmt = r.choice(["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"])
    from .datagen import _pseudo
    def pool(bank, k_lo=3, k_hi=14):
        k = r.randint(k_lo, k_hi)
        items = [r.choice(bank) for _ in range(k)]
        if r.random() < 0.35:
            items = [_pseudo(r) if r.random() < 0.5 else x for x in items]
        return items
    P_DEPT, P_POS, P_CITY = pool(DEPTS), pool(POSITIONS, 4, 20), pool(CITIES, 1, 8)
    for i in range(n):
        if concept == "employee_id":
            out.append(r.choice([str(base_id + i), str(r.randint(100, 99999)), f"E{r.randint(1, 9999):04d}", f"EMP-{r.randint(1, 999):03d}"]) if r.random() < 0.3 else str(base_id + i))
        elif concept == "first_name":
            out.append(r.choice(FIRST) + (" " + r.choice(FIRST) if r.random() < 0.2 else ""))
        elif concept == "last_name":
            out.append(r.choice(LAST) + (" " + r.choice(LAST) if r.random() < 0.5 else ""))
        elif concept == "full_name":
            out.append(ps() if r.random() < 0.6 else f"{r.choice(LAST)} {r.choice(LAST)}, {r.choice(FIRST)}")
        elif concept == "email":
            out.append(strip_accents((r.choice(FIRST) + sep_e + r.choice(LAST)).lower()) + "@" + dom)
        elif concept == "phone":
            out.append(r.choice(["3", "3", "+57 3", "(1) "]) + "".join(str(r.randint(0, 9)) for _ in range(9)) if r.random() < 0.7 else
                       "-".join(["".join(str(r.randint(0, 9)) for _ in range(k)) for k in (3, 3, 4)]))
        elif concept == "department":
            out.append(r.choice(P_DEPT))
        elif concept == "position":
            out.append(r.choice(P_POS))
        elif concept in ("salary", "net_pay"):
            lo, hi, _ = mag
            v = r.randint(lo // 1000, hi // 1000) * 1000 if hi > 5000 else r.randint(lo // 10, hi // 10) * 10
            if concept == "net_pay":
                v = int(v * 0.88)
            out.append(_fmt_money(r, v))
        elif concept == "bonus":
            lo, hi, _ = mag
            out.append(_fmt_money(r, r.choice([0, 0, r.randint(lo // 40000, hi // 40000) * 10000 if hi > 5000 else r.randint(5, 80) * 10])))
        elif concept == "deductions":
            lo, hi, _ = mag
            out.append(_fmt_money(r, r.randint(lo // 15000, hi // 12000) * 1000 if hi > 5000 else r.randint(20, 900)))
        elif concept == "hire_date":
            import datetime
            d = datetime.date(r.randint(2005, 2025), r.randint(1, 12), r.randint(1, 28))
            out.append(d.strftime(datefmt))
        elif concept == "document_id":
            out.append("".join(str(r.randint(0, 9)) for _ in range(ccs)))
        elif concept == "status":
            out.append(r.choice(["Activo", "Inactivo", "Vacaciones", "Licencia", "activo", "ACTIVO", "Retirado", "Active", "Suspendido"]))
        elif concept == "city":
            out.append(r.choice(P_CITY))
        else:
            k = r.random()
            if k < 0.25:
                out.append(r.choice(["", "ok", "pendiente", "N/A", "ver nota", "revisar", "a tiempo"]))
            elif k < 0.45:
                out.append(str(r.randint(0, 60)))
            elif k < 0.6:
                out.append(r.choice(["Masculino", "Femenino", "M", "F", "Bancolombia", "Davivienda", "Sura", "Colpatria", "Indefinido", "Fijo", "Turno A", "Turno B", "Diurno", "Nocturno"]))
            elif k < 0.75:
                out.append("".join(str(r.randint(0, 9)) for _ in range(r.randint(4, 14))))
            elif k < 0.9:
                import datetime
                out.append(datetime.date(r.randint(1960, 2005), r.randint(1, 12), r.randint(1, 28)).strftime(datefmt))
            else:
                out.append(ps())
        if r.random() < 0.02:
            out[-1] = ""
    return out


def mangle_header(h: str, r: random.Random) -> str:
    words = h.split()
    k = r.random()
    if k < 0.2:
        s = "_".join(words)
    elif k < 0.3:
        s = "_".join(words).upper()
    elif k < 0.4:
        s = "".join(w.capitalize() for w in words)
    elif k < 0.5:
        s = words[0] + "".join(w.capitalize() for w in words[1:])
    elif k < 0.6:
        s = " ".join(words).upper()
    elif k < 0.75:
        s = " ".join(w.capitalize() for w in words)
    elif k < 0.8:
        s = "-".join(words)
    else:
        s = h
    if r.random() < 0.12:
        s = r.choice(["emp_", "EMP_", "Empleado ", "TB_"]) + s
    if r.random() < 0.1:
        s = s + r.choice([" (COP)", " mensual", " 2024", " $", "_1", " (usd)", " *"])
    if r.random() < 0.05 and len(s) > 5:
        j = r.randint(1, len(s) - 2)
        s = s[:j] + s[j + 1:]
    return s


def make_example(concept: str, r: random.Random, with_values=True, force_generic=False):
    h = r.choice(HEADERS[concept])
    header = mangle_header(h, r)
    if force_generic or (with_values and r.random() < 0.12):
        header = r.choice(GENERIC_HEADERS)
    if with_values:
        vals = gen_values(concept, r, r.randint(8, 40))
        feats, vids = profile(vals), value_ids(vals)
    else:
        feats, vids = np.zeros(N_PROF, np.float32), []
    return header, feats, vids


class ColumnMapper:
    def __init__(self, net: BagMLP):
        self.net = net

    def predict(self, header: str, values: Sequence[str] | None) -> np.ndarray:
        feats = profile(values) if values is not None and len(values) else np.zeros(N_PROF, np.float32)
        ids = np.array([col_ids(header, values)], np.int64)
        return self.net.predict_proba(ids, feats[None, :])[0]

    def classify(self, header: str, values):
        p = self.predict(header, values)
        k = int(p.argmax())
        return CONCEPTS[k], float(p[k]), p

    def save(self, directory):
        os.makedirs(directory, exist_ok=True)
        np.savez_compressed(os.path.join(directory, "columns.npz"), **self.net.state())

    @classmethod
    def load(cls, directory):
        return cls(BagMLP.from_state(np.load(os.path.join(directory, "columns.npz"))))


MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "columns")

# Encabezados que NO están en el banco de entrenamiento (evaluación honesta de generalización)
UNSEEN = {
    "employee_id": ["N° Legajo", "Nro. Colaborador", "ID_Personal", "Código del trabajador", "emp#"],
    "first_name": ["Nombre(s)", "Primer Nombre Empleado", "Names", "NOMBRE_PILA"],
    "last_name": ["Apellido(s)", "Apellidos Empleado", "Last_Nm", "SURNAMES"],
    "full_name": ["Apellido y nombre", "Nombre del Colaborador", "Employee Full Name", "NOMBRE_COMPLETO_EMP"],
    "email": ["Mail personal", "Correo e.", "EMAIL_CONTACTO", "Direccion de correo"],
    "phone": ["Tel. contacto", "Nº celular", "Telefono movil", "PHONE_NO"],
    "department": ["Unidad organizativa", "Area funcional", "DEPTO_ASIGNADO", "Dept. Name"],
    "position": ["Rol en la empresa", "Cargo desempenado", "JOB_POSITION", "Denominación del puesto"],
    "salary": ["Remuneración mensual básica", "Sueldo devengado", "SALARIO_MES", "Gross Pay", "Haber básico"],
    "bonus": ["Bonif.", "Gratificación", "Incentivo variable", "BONO_PRODUCTIVIDAD", "Extra pago"],
    "deductions": ["Deducciones de ley", "Total descuentos nómina", "DESC_LEY", "Retefuente"],
    "net_pay": ["Neto pagado", "Valor líquido", "NET_PAY_TOTAL", "Salario neto mes"],
    "hire_date": ["Fecha de alta", "F. Ingreso", "FECHA_CONTRATO", "Date of joining"],
    "document_id": ["Nº de documento", "Cédula ciudadanía", "DOC_IDENT", "Identification Number"],
    "status": ["Estado laboral actual", "Situación", "EMP_STATUS", "Condición"],
    "city": ["Ciudad de trabajo", "Sede principal", "CITY_NAME", "Municipio"],
}


def eval_unseen(mapper: "ColumnMapper", seed=123, with_values=True):
    r = random.Random(seed)
    ok = tot = 0
    for concept, hs in UNSEEN.items():
        for h in hs:
            for _ in range(5):
                vals = gen_values(concept, r, r.randint(10, 30)) if with_values else None
                ok += mapper.classify(h, vals)[0] == concept
                tot += 1
    return ok / tot


def train(n=40000, epochs=14, seed=0, quick=False):
    if quick:
        n, epochs = 4000, 3
    r = random.Random(seed)
    rng = np.random.default_rng(seed)

    def sample_set(m, rr):
        H, F, Y = [], [], []
        for _ in range(m):
            c = rr.choice(CONCEPTS[:-1]) if rr.random() < 0.9 else "other"
            if rr.random() < 0.15:
                c = "other"
            wv = rr.random() < 0.8
            h, f, vids = make_example(c, rr, with_values=wv)
            H.append(header_ids(h) + vids); F.append(f); Y.append(C2I[c])
        return H, np.stack(F), np.array(Y)

    H, F, Y = sample_set(n, r)
    Ht, Ft, Yt = sample_set(3000, random.Random(seed + 99))
    net = BagMLP(VOCAB, 64, N_PROF, 128, len(CONCEPTS), seed=seed)
    opt = Adam(net.p, lr=3e-3)
    from .model import pad_ids
    B = 64
    for ep in range(epochs):
        perm = rng.permutation(len(H)); tot = nb = 0
        for b in range(0, len(perm), B):
            idx = perm[b:b + B]
            ids = pad_ids([H[i] for i in idx])
            ids = np.where(rng.random(ids.shape) < 0.05, 0, ids)
            loss, g = net.loss_grads(ids, F[idx], Y[idx], rng)
            opt.step(g); tot += loss; nb += 1
        print(f"[columnas] época {ep + 1}/{epochs} pérdida {tot / nb:.4f}")
    acc = float((net.predict_proba(pad_ids(Ht), Ft).argmax(1) == Yt).mean())
    print(f"Exactitud en columnas de prueba (encabezados/valores nuevos): {acc:.4f}")
    mp = ColumnMapper(net)
    print(f"Encabezados NUNCA vistos, con valores : {eval_unseen(mp):.4f}")
    print(f"Encabezados NUNCA vistos, solo texto  : {eval_unseen(mp, with_values=False):.4f}")
    # sin encabezado (solo valores)
    r2 = random.Random(5); ok = tot = 0
    for c in CONCEPTS[:-1]:
        for _ in range(60):
            vals = gen_values(c, r2, 25)
            ok += mp.classify("", vals)[0] == c; tot += 1
    print(f"Sin encabezado (solo valores)         : {ok / tot:.4f}")
    mp.save(MODEL_DIR)
    return acc


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    train(quick=ap.parse_args().quick)
