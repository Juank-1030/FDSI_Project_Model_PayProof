"""Generador de datos de entrenamiento para el modelo de instrucciones.

Genera oraciones en español (formal, informal, con errores, sin tildes, en minúsculas…)
junto con sus etiquetas EXACTAS (intención de la oración + etiqueta por token), a partir
de gramáticas con muchas variantes. Se divide en `train` / `test` usando plantillas y
entidades distintas, para medir generalización real y no memorización.
"""
from __future__ import annotations

import random
from typing import List, Tuple

from .text import strip_accents

INTENTS = [
    "select", "set", "increase", "decrease", "delete", "notify",
    "drop_table", "drop_database", "modify_audit", "alter_credentials", "unknown",
]

ENTITIES = ["FIELD", "AMT_PCT", "AMT_ABS", "VALUE", "ALL", "FCOL", "FVAL",
            "CCOL", "CLT", "CLE", "CGT", "CGE", "CVAL", "MSG"]
TAGS = ["O"] + [f"{p}-{e}" for e in ENTITIES for p in ("B", "I")]
TAG2ID = {t: i for i, t in enumerate(TAGS)}

# --------------------------------------------------------------------------- #
# Bancos de entidades
# --------------------------------------------------------------------------- #

FIRST = """Juan Maria Carlos Ana Luis Laura Pedro Sofia Andres Camila Diego Valentina Jorge Daniela
Miguel Paula Fernando Carolina Ricardo Natalia Santiago Isabella Mateo Gabriela Sebastian Lucia
Alejandro Marcela Javier Claudia Felipe Monica Oscar Patricia Hector Veronica Raul Adriana Daniel
Sergio Diana Julian Angela Esteban Johana Mauricio Liliana Cristian Sandra Nicolas Tatiana Ivan
Alvaro Beatriz Rodrigo Elena Hugo Marta Ramiro Teresa Gonzalo Rosa Emilio Silvia Samuel Karen
Brayan Yesenia Wilson Yolanda Jairo Luz Edwin Piedad Cesar Nubia Fabian Lorena Gustavo Pilar
Armando Susana Leonardo Irene Manuel Eliana Rafael Alicia Bernardo Gloria Orlando Mariana Pablo
Jimena Tomas Renata Ernesto Olga Franco Esperanza Ulises Dayana Anthony Brenda""".split()

LAST = """Perez Gomez Rodriguez Martinez Lopez Garcia Hernandez Gonzalez Ramirez Torres Diaz Vargas
Castro Rojas Moreno Jimenez Ortiz Romero Suarez Morales Cardenas Herrera Medina Aguilar Mejia
Salazar Pineda Rios Cortes Guerrero Navarro Delgado Vega Cruz Reyes Flores Mendoza Silva Rivera
Sanchez Ramos Cano Soto Parra Pardo Bernal Carvajal Ospina Quintero Duarte Montoya Valencia
Arango Zapata Giraldo Restrepo Henao Londono Escobar Naranjo Villa Muñoz Acosta Bautista Peña
Campos Cabrera Fuentes Ibarra Lara Leon Marin Nieto Ochoa Pacheco Rueda Serrano Tapia Uribe
Velez Yepes Zuluaga Barrera Cuesta Duque Espinosa Fajardo Gallego Hoyos Isaza Jaramillo""".split()

DEPTS = ["Ventas", "Finanzas", "Contabilidad", "Tecnología", "Recursos Humanos", "Marketing",
         "Logística", "Producción", "Operaciones", "Compras", "Legal", "Soporte", "Calidad",
         "Mantenimiento", "Investigación y Desarrollo", "Atención al Cliente", "Seguridad",
         "Comercial", "Administración", "Sistemas", "Auditoría Interna", "Talento Humano",
         "Almacén", "Servicio Técnico", "Diseño", "Planeación", "Cartera", "Tesorería",
         "Gerencia General", "Innovación", "Proyectos", "Exportaciones", "Nómina", "Bienestar"]

POSITIONS = ["Gerente", "Analista", "Analista Senior", "Auxiliar", "Director", "Coordinador",
             "Asistente", "Ingeniero", "Desarrollador", "Contador", "Supervisor", "Jefe de Área",
             "Vendedor", "Operario", "Técnico", "Asesor Comercial", "Líder de Proyecto",
             "Practicante", "Secretaria", "Abogado", "Diseñador", "Consultor", "Auditor",
             "Subgerente", "Recepcionista", "Mensajero", "Administrador", "Arquitecto",
             "Científico de Datos", "Jefe de Compras", "Tesorero", "Conductor", "Almacenista"]

CITIES = ["Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena", "Bucaramanga", "Pereira",
          "Manizales", "Cúcuta", "Madrid", "Lima", "Quito", "Santiago", "Ciudad de México"]

_SYL_A = ["ka", "ri", "mo", "ta", "ne", "lu", "va", "so", "de", "mi", "ra", "to", "ge", "pa", "zu", "fe"]
_SYL_B = ["lan", "ver", "mar", "dor", "nis", "tin", "sel", "ron", "mex", "tra", "bel", "gan"]


def _pseudo(rng, cap=True):
    w = "".join(rng.choice(_SYL_A) for _ in range(rng.randint(2, 3))) + rng.choice(_SYL_B)
    return w.capitalize() if cap else w


def split_list(items, part, ratio=0.25):
    """Reparto determinista train/test de una lista de entidades."""
    test = [x for i, x in enumerate(items) if i % int(1 / ratio) == 3]
    train = [x for i, x in enumerate(items) if i % int(1 / ratio) != 3]
    return train if part == "train" else test


FIELDS = {
    "salary": ["salario", "sueldo", "salario base", "sueldo base", "remuneración", "sueldo mensual",
               "salario mensual", "paga", "pago", "ingreso", "salario neto", "salario bruto"],
    "bonus": ["bono", "bonificación", "bonus", "prima", "incentivo", "comisión"],
    "deductions": ["descuento", "deducción", "retención", "descuentos", "deducciones"],
    "position": ["cargo", "puesto", "posición", "título"],
    "department": ["departamento", "área", "depto", "dependencia", "sección", "dpto", "area"],
    "email": ["correo", "email", "correo electrónico", "mail", "e-mail"],
    "phone": ["teléfono", "celular", "móvil", "telefono"],
    "city": ["ciudad", "sede", "ubicación"],
}
FIELD_W = {"salary": 5, "bonus": 1.5, "deductions": 1, "position": 1.5, "department": 1.5,
           "email": 1, "phone": 0.5, "city": 0.5}
ID_WORDS = ["id", "código", "codigo", "cédula", "cedula", "documento", "número de empleado",
            "numero de empleado", "código de empleado", "identificación", "legajo"]
DEPT_WORDS = ["departamento", "área", "area", "dependencia", "sección", "depto"]
POS_WORDS = ["cargo", "puesto", "posición"]

MESSAGES = [
    "la nómina se paga el viernes", "hay reunión mañana a las 9", "revisen sus colillas de pago",
    "el pago de este mes se retrasará dos días", "recuerden entregar los reportes",
    "mañana no hay servicio de transporte", "se aprobó el ajuste salarial", "actualicen sus datos en el sistema",
    "el 15 de este mes cierra la nómina", "felicitaciones por los resultados del trimestre",
    "la capacitación es el lunes a las 8", "feliz cumpleaños", "por favor confirmen su asistencia",
    "el sistema estará en mantenimiento esta noche", "se acerca la fecha límite de vacaciones",
    "pasen por recursos humanos a firmar el contrato", "gracias por su trabajo este año",
    "el bono se pagará con la próxima quincena", "tienen 3 días para entregar los documentos",
]

# --------------------------------------------------------------------------- #
# Constructor de oraciones etiquetadas
# --------------------------------------------------------------------------- #


class S:
    def __init__(self):
        self.t: List[str] = []
        self.g: List[str] = []

    def o(self, text: str):
        for w in text.split():
            self.t.append(w)
            self.g.append("O")
        return self

    def e(self, tag: str, words):
        ws = words.split() if isinstance(words, str) else list(words)
        for i, w in enumerate(ws):
            self.t.append(w)
            self.g.append(("B-" if i == 0 else "I-") + tag)
        return self

    def out(self):
        return self.t, self.g


def _plural(w: str) -> str:
    if w.endswith(("ón", "r", "l", "z")):
        return (w[:-2] + "ones") if w.endswith("ón") else w + "es"
    if w.endswith("s"):
        return w
    return w + "s"


def _plural_first(words):
    ws = list(words)
    ws[0] = _plural(ws[0].lower()).capitalize() if ws[0][0].isupper() else _plural(ws[0])
    return ws


def _num_str(rng, v, style=None):
    style = style or rng.choice(["plain", "plain", "dot", "comma"])
    s = str(int(v))
    if style == "plain" or len(s) <= 3:
        return s
    parts = []
    while s:
        parts.append(s[-3:])
        s = s[:-3]
    sep = "." if style == "dot" else ","
    return sep.join(reversed(parts))


class Gen:
    def __init__(self, part: str, seed: int):
        self.part = part
        self.rng = random.Random(seed)
        self.first = split_list(FIRST, part)
        self.last = split_list(LAST, part)
        self.depts = split_list(DEPTS, part)
        self.pos = split_list(POSITIONS, part)
        self.cities = split_list(CITIES, part)

    # ---- entidades -------------------------------------------------------------
    def first_name(self):
        r = self.rng
        return _pseudo(r) if r.random() < 0.15 else r.choice(self.first)

    def last_name(self):
        r = self.rng
        return _pseudo(r) if r.random() < 0.15 else r.choice(self.last)

    def person(self):
        r = self.rng
        k = r.random()
        if k < 0.2:
            return [self.first_name()]
        if k < 0.9:
            return [self.first_name(), self.last_name()]
        return [self.first_name(), self.first_name(), self.last_name()]

    def dept(self):
        r = self.rng
        return _pseudo(r).split() if r.random() < 0.1 else r.choice(self.depts).split()

    def position(self):
        r = self.rng
        return [_pseudo(r)] if r.random() < 0.08 else r.choice(self.pos).split()

    def emp_id(self):
        r = self.rng
        n = r.choice([3, 4, 4, 4, 5, 6])
        return str(r.randint(10 ** (n - 1), 10 ** n - 1))

    def email(self):
        r = self.rng
        a = (self.first_name() + r.choice(["", ".", "_"]) + self.last_name()).lower()
        a = strip_accents(a)
        return a + "@" + r.choice(["empresa", "corp", "mail", "gmail", "outlook", "miempresa"]) + r.choice([".com", ".co", ".com.co", ".net"])

    def salary_value(self):
        r = self.rng
        k = r.random()
        if k < 0.6:
            return r.randint(10, 150) * 100_000 // 1 * 1 if False else r.randint(8, 200) * 100_000
        if k < 0.85:
            return r.randint(8, 200) * 500
        return r.randint(1, 30) * 1_000_000 + r.choice([0, 0, 500_000])

    def abs_amount(self):
        r = self.rng
        k = r.random()
        if k < 0.5:
            return r.randint(1, 20) * 50_000
        if k < 0.8:
            return r.randint(1, 40) * 25
        return r.randint(1, 10) * 100_000

    def pct_amount(self):
        r = self.rng
        k = r.random()
        if k < 0.75:
            return str(r.randint(1, 30))
        if k < 0.9:
            return str(r.choice([35, 40, 50, 60, 75, 100]))
        return r.choice(["2,5", "7,5", "3.5", "1,5", "12,5", "0,5"])

    # ---- campos ----------------------------------------------------------------
    def pick_field(self, only=None):
        r = self.rng
        keys = [k for k in FIELDS if (only is None or k in only)]
        k = r.choices(keys, [FIELD_W[x] for x in keys])[0]
        return k, r.choice(FIELDS[k])

    def field_seg(self, s: S, name: str, plural_ok=True, det=True):
        r = self.rng
        words = name
        if det:
            pl = plural_ok and r.random() < 0.25
            if pl:
                ws = name.split()
                ws[0] = _plural(ws[0])
                words = " ".join(ws)
                s.o(r.choice(["los", "los", "mis"]) if not name.startswith(("area", "área", "ubicaci", "sección", "paga", "ciudad", "sede", "retenci", "deducci", "comisi", "bonifi", "prima", "remuner")) else r.choice(["las", "las"]))
            else:
                if name.split()[0].endswith("s"):
                    s.o("las" if name.split()[0].endswith(("ciones", "ntas")) else "los")
                    s.e("FIELD", words)
                    return
                art = "la" if name.split()[0].endswith("a") or name.split()[0].endswith(("ión", "ción", "ad")) or name.split()[0] in ("área", "area") else "el"
                if name.split()[0] in ("área", "area"):
                    art = "el"
                s.o(art if r.random() < 0.9 else "")
        s.e("FIELD", words)

    # ---- alcances (scope) ---------------------------------------------------------
    def scope(self, s: S, mode: str, allow=("all", "dept", "person", "id", "pos", "cond", "combo")):
        """mode: dat -> 'a X' | gen -> 'de X' | subj -> 'X' | none -> sin preposición"""
        r = self.rng
        kind = r.choices(allow, [2, 3.0, 4.5, 2.5, 1.5, 1.5, 1.0][: len(allow)] if len(allow) == 7 else None)[0]
        prep = {"dat": r.choice(["a", "a", "para"]), "gen": "de", "subj": "", "none": ""}[mode]

        def P(done_article=False):
            if prep:
                s.o(prep)

        if kind == "all":
            if mode == "gen":
                prep_all = r.choice(["de todos los empleados", "de todos", "de todo el personal", "de toda la plantilla",
                                     "de todos los trabajadores", "de la nómina completa", "de toda la empresa"])
            elif mode == "dat":
                prep_all = r.choice(["a todos los empleados", "a todos", "a todo el personal", "a toda la plantilla",
                                     "a todos los trabajadores", "a toda la empresa", "para todos", "para toda la nómina",
                                     "a absolutamente todos"])
            else:
                prep_all = r.choice(["todos los empleados", "todos", "todo el personal", "toda la plantilla",
                                     "todos los trabajadores", "toda la empresa"])
            ws = prep_all.split()
            # ALL = 'todos'/'todo'/'toda' (+ resto del sintagma)
            i = next(i for i, w in enumerate(ws) if w.startswith(("todo", "toda", "absolutamente", "la")) and w != "de")
            s.o(" ".join(ws[:i]))
            s.e("ALL", ws[i:] if ws[i] != "la" else ws[i:])
        elif kind == "dept":
            P()
            noun = r.choice(["los empleados", "los", "el personal", "los trabajadores", "el equipo", "la gente", "todos los",
                             "empleados", "los que trabajan", "los colaboradores"])
            v = r.random()
            if v < 0.45:
                s.o(noun)
                s.o(r.choice(["de", "del"]) if False else "de")
                if r.random() < 0.7:
                    pass
                s.e("FVAL", self.dept())
            elif v < 0.9:
                s.o(noun)
                w = r.choice(DEPT_WORDS)
                s.o("de la" if w in ("área", "area", "sección", "dependencia") else "del")
                s.e("FCOL", w)
                if r.random() < 0.6:
                    s.o("de")
                s.e("FVAL", self.dept())
            else:
                s.o(r.choice(["los que están en", "los que pertenecen a", "los que laboran en", "los que trabajan en"]))
                s.e("FVAL", self.dept())
        elif kind == "person":
            P()
            if r.random() < 0.35:
                s.o(r.choice(["el empleado", "la empleada", "el trabajador", "la trabajadora", "el colaborador", "la señora", "el señor", "el"]))
            s.e("FVAL", self.person())
        elif kind == "id":
            P()
            if r.random() < 0.7:
                s.o(r.choice(["el empleado", "la empleada", "el trabajador", "el registro", "el colaborador"]))
            if r.random() < 0.8:
                s.o(r.choice(["con", "con", "cuyo", "cuyo"]))
                s.e("FCOL", r.choice(ID_WORDS))
                if r.random() < 0.15:
                    s.o("igual a")
                elif r.random() < 0.2:
                    s.o("es")
            else:
                s.o("número")
            s.e("FVAL", self.emp_id())
        elif kind == "pos" and r.random() < 0.4:
            P()
            s.o(r.choice(["los", "todos los", "las", "todas las", "los", "los"]))
            s.e("FVAL", _plural_first(self.position()) if r.random() < 0.8 else self.position())
        elif kind == "dept" and r.random() < 0.15:
            P()
            if r.random() < 0.5:
                s.o(r.choice(["el departamento de", "el área de", "la gente de", "el equipo de"]))
            s.e("FVAL", self.dept())
        elif kind == "pos":
            P()
            noun = r.choice(["los empleados", "los", "todos los", "los que son", "los trabajadores", "los que tienen"])
            if noun in ("los que son",):
                s.o(noun)
                s.e("FVAL", self.position())
            else:
                s.o(noun)
                s.o(r.choice(["con", "con", "que tienen"]))
                s.o(r.choice(["el", "el", ""]))
                s.e("FCOL", r.choice(POS_WORDS))
                s.o(r.choice(["de", "de", "como", "="]) if r.random() < 0.7 else "")
                s.e("FVAL", self.position())
        elif kind == "cond":
            P()
            s.o(r.choice(["los empleados", "los que", "los trabajadores", "todos los que", "quienes", "los colaboradores", "los empleados que"]))
            self.cond(s)
        else:  # combo: depto + condición / depto + cargo
            P()
            s.o(r.choice(["los empleados", "los", "el personal", "los trabajadores"]))
            s.o(r.choice(["de", "del"]) if False else "de")
            if r.random() < 0.4:
                pass
            s.e("FVAL", self.dept())
            if r.random() < 0.6:
                s.o(r.choice(["que", "y que", "con", "pero solo los que", "cuyo"]) if False else r.choice(["que", "y que", "pero solo los que"]))
                self.cond(s, bare=True)
            else:
                s.o(r.choice(["con", "que tengan", "que son"]))
                if r.random() < 0.6:
                    s.e("FCOL", r.choice(POS_WORDS))
                    s.o("de")
                s.e("FVAL", self.position())

    def cond(self, s: S, bare=False):
        r = self.rng
        cmps = {
            "CLT": ["menor a", "menor que", "inferior a", "menos de", "por debajo de", "debajo de", "<"],
            "CLE": ["menor o igual a", "máximo", "hasta", "no más de", "<="],
            "CGT": ["mayor a", "mayor que", "superior a", "más de", "por encima de", "arriba de", ">"],
            "CGE": ["mayor o igual a", "mínimo", "al menos", "desde", "no menos de", ">="],
        }
        ck = r.choices(list(cmps), [3, 1, 3, 1])[0]
        phrase = r.choice(cmps[ck])
        form = r.random()
        verbs_gana = ["ganan", "ganen", "cobran", "cobren", "perciben", "reciben"]
        if form < 0.4:
            # "ganan menos de X": sin columna explícita (implica salario)
            s.o(r.choice(verbs_gana))
            self._cmp_val(s, ck, phrase)
        else:
            k, fname = self.pick_field(only={"salary", "bonus", "deductions"})
            if form < 0.75:
                s.o(r.choice(["tienen", "tengan", "con", "cuyo", "cuyos"]))
                s.o(r.choice(["un", ""])) if False else None
                s.e("CCOL", fname)
                s.o(r.choice(["sea", "es", "", "esté", "estén"]) if False else r.choice(["sea", "es", "", ""]))
            else:
                s.o("con")
                s.e("CCOL", fname)
            self._cmp_val(s, ck, phrase)

    def _cmp_val(self, s: S, ck: str, phrase: str):
        r = self.rng
        ws = ["<", "="] if phrase == "<=" else (([">", "="]) if phrase == ">=" else phrase.split())
        s.e(ck, ws)
        if r.random() < 0.3:
            s.o(r.choice(["$", "$"]))
        s.e("CVAL", _num_str(r, self.salary_value()))
        if r.random() < 0.2:
            s.o(r.choice(["pesos", "COP", "dólares", "USD"]))

    # ---- montos ---------------------------------------------------------------------
    def amount(self, s: S, kind: str):
        r = self.rng
        if kind == "pct":
            n = self.pct_amount()
            f = r.random()
            if f < 0.3:
                s.o("en un")
            elif f < 0.5:
                s.o("un")
            elif f < 0.7:
                s.o("en")
            elif f < 0.8:
                s.o("por")
            elif f < 0.9:
                s.o("de")
            s.e("AMT_PCT", n)
            s.o("%")
        else:
            f = r.random()
            if f < 0.4:
                s.o("en")
            elif f < 0.6:
                s.o("por")
            elif f < 0.75:
                s.o("de")
            if r.random() < 0.25:
                s.o("$")
            s.e("AMT_ABS", _num_str(r, self.abs_amount()))
            if r.random() < 0.2:
                s.o(r.choice(["pesos", "COP", "dólares", "USD", "euros"]))

    # ---- plantillas por intención ---------------------------------------------------------
    PRE = ["", "", "", "", "por favor ", "necesito que ", "quiero ", "quiero que ", "puedes ", "podrías ",
           "me ayudas a ", "ayúdame a ", "hay que ", "debes ", "favor ", "oye ", "hola, ", "ok, ", "urgente: ",
           "como administrador, ", "como gerente, ", "necesito "]

    def pre(self, s: S):
        p = self.rng.choice(self.PRE)
        if p:
            s.o(p.replace(",", " ,").replace(":", " :")) if False else s.o(p.rstrip(", :")) if False else s.o(p.strip())

    INC = ["aumenta", "aumentar", "aumentes", "sube", "subir", "subas", "incrementa", "incrementar", "incrementes",
           "reajusta", "reajustar", "eleva", "elevar", "aumenta", "sube", "incrementa", "ajusta al alza",
           "auméntale", "súbele", "subele", "aumentale", "increméntale", "reajústale", "elévale", "auméntales", "súbeles"]
    DEC = ["reduce", "reducir", "reduzcas", "disminuye", "disminuir", "baja", "bajar", "bajes", "recorta", "recortar",
           "rebaja", "rebajar", "resta", "restar", "descuenta", "descontar", "reduce", "baja", "recorta",
           "descuéntale", "descuentale", "réstale", "restale", "quítale", "bájale", "bajale", "recórtale", "rebájale", "redúcele", "descuéntales", "bájales"]

    def gen_adjust(self, direction: str):
        r = self.rng
        s = S()
        verbs = self.INC if direction == "increase" else self.DEC
        kind = "pct" if r.random() < 0.7 else "abs"
        fam = r.random()
        no_scope = r.random() < 0.04
        _, fname = self.pick_field(only={"salary", "bonus", "deductions"}) if r.random() < 0.85 else (None, None)
        self.pre(s)
        if fam < 0.22:  # A1  VERB el FIELD [a S] AMT
            s.o(r.choice(verbs))
            if fname:
                self.field_seg(s, fname)
            if not no_scope:
                self.scope(s, "dat")
            self.amount(s, kind)
        elif fam < 0.38:  # A2  VERB a S el FIELD AMT
            s.o(r.choice(verbs))
            if not no_scope:
                self.scope(s, "dat")
            if fname:
                self.field_seg(s, fname)
            self.amount(s, kind)
        elif fam < 0.55:  # A3  VERB AMT el FIELD de S
            s.o(r.choice(verbs))
            self.amount(s, kind)
            if fname:
                s.o(r.choice(["al", "en el", "a"]) if False else r.choice(["a", "en"]))
                self.field_seg(s, fname)
            if not no_scope:
                self.scope(s, "gen" if fname else "dat")
        elif fam < 0.75:  # A4  VERB el FIELD de S AMT
            s.o(r.choice(verbs))
            if fname:
                self.field_seg(s, fname)
                if not no_scope:
                    self.scope(s, "gen")
            elif not no_scope:
                self.scope(s, "dat")
            self.amount(s, kind)
        elif fam < 0.9:  # A5  aplica un aumento de AMT a S
            if direction == "increase":
                s.o(r.choice(["aplica un aumento", "da un aumento", "dale un aumento", "hazle un aumento", "otorga un aumento",
                              "aplica un incremento", "haz un reajuste", "aplica un alza"]))
            else:
                s.o(r.choice(["aplica una reducción", "haz un recorte", "aplica un descuento", "aplica una rebaja", "haz una reducción"]))
            self.amount(s, kind)
            if fname and r.random() < 0.5:
                s.o("al" if False else "en")
                self.field_seg(s, fname)
            if not no_scope:
                self.scope(s, "dat")
        else:  # A6  S debe recibir un aumento de AMT
            if not no_scope:
                self.scope(s, "subj", allow=("all", "dept", "person", "id", "pos", "cond"))
            if direction == "increase":
                s.o(r.choice(["debe recibir un aumento", "deben recibir un aumento", "recibirá un aumento", "recibirán un aumento",
                              "merece un aumento", "merecen un aumento", "tendrá un incremento", "tendrán un incremento"]))
            else:
                s.o(r.choice(["tendrá una reducción", "tendrán una reducción", "recibirá un recorte", "recibirán un recorte"]))
            self.amount(s, kind)
            if fname and r.random() < 0.4:
                s.o("en")
                self.field_seg(s, fname)
        return s

    SET = ["cambia", "cambiar", "cambies", "establece", "establecer", "pon", "poner", "ponle", "actualiza", "actualizar",
           "modifica", "modificar", "fija", "fijar", "asigna", "asignar", "corrige", "corregir", "deja", "define"]

    def value_for(self, s: S, k: str):
        r = self.rng
        if k == "salary" or k == "bonus" or k == "deductions":
            if r.random() < 0.2:
                s.o("$")
            s.e("VALUE", _num_str(r, self.salary_value() if k == "salary" else self.abs_amount() * r.randint(1, 4)))
        elif k == "position":
            s.e("VALUE", self.position())
        elif k == "department":
            s.e("VALUE", self.dept())
        elif k == "email":
            s.e("VALUE", self.email())
        elif k == "phone":
            s.e("VALUE", "3" + "".join(str(r.randint(0, 9)) for _ in range(9)))
        else:
            s.e("VALUE", r.choice(self.cities).split())

    def gen_set(self):
        r = self.rng
        s = S()
        k, fname = self.pick_field(only={"salary", "bonus", "position", "department", "email", "phone", "city", "deductions"})
        fam = r.random()
        no_scope = r.random() < 0.03
        self.pre(s)
        if fam < 0.3:  # S1 cambia el FIELD de S a VALUE
            s.o(r.choice(self.SET))
            self.field_seg(s, fname)
            if not no_scope:
                self.scope(s, "gen")
            s.o(r.choice(["a", "a", "en", "por", "como", "a ser", "al valor de", "="]))
            self.value_for(s, k)
        elif fam < 0.5:  # S2 asigna a S el FIELD de VALUE
            s.o(r.choice(self.SET))
            if not no_scope:
                self.scope(s, "dat", allow=("person", "id", "dept", "pos"))
            self.field_seg(s, fname)
            s.o(r.choice(["de", "en", "a", "como", "con"]) if k not in ("email",) else r.choice(["a", "en", "como"]))
            self.value_for(s, k)
        elif fam < 0.72:  # S3 el FIELD de S debe ser VALUE
            if r.random() < 0.3:
                s.o(r.choice(["que", "quiero que", "necesito que"]))
            self.field_seg(s, fname)
            if not no_scope:
                self.scope(s, "gen")
            s.o(r.choice(["debe ser", "es", "ahora es", "pasa a ser", "sea", "será", "quedó en", "queda en", "debería ser", "tiene que ser"]))
            self.value_for(s, k)
        elif fam < 0.82 and k in ("position", "department", "city"):
            # traslados / ascensos: FIELD implícito o explícito
            if k == "position":
                s.o(r.choice(["promueve", "asciende", "nombra", "designa"]))
                self.scope(s, "dat", allow=("person", "id"))
                s.o(r.choice(["a", "como", "en el cargo de", "al cargo de"]))
                if r.random() < 0.5:
                    pass
                self.value_for(s, k)
            else:
                s.o(r.choice(["traslada", "mueve", "pasa", "transfiere", "reubica"]))
                self.scope(s, "dat", allow=("person", "id", "pos", "cond"))
                s.o(r.choice(["al", "a"]) if False else "al")
                s.e("FIELD", fname)
                s.o("de" if r.random() < 0.7 else "")
                self.value_for(s, k)
        else:  # S5 FIELD de S: VALUE   /  S es VALUE
            s.o(r.choice(self.SET))
            if not no_scope:
                self.scope(s, "dat", allow=("person", "id", "dept", "all", "pos"))
            s.o(r.choice(["su", "el"]))
            s.e("FIELD", fname)
            s.o(r.choice(["a", "en", "como", ":"]))
            self.value_for(s, k)
        return s

    SELECT = ["muestra", "muéstrame", "mostrar", "dame", "dime", "lista", "listar", "enséñame", "ver", "consulta", "consultar",
              "busca", "buscar", "revisa", "trae", "dame los datos", "extrae", "necesito ver", "quiero ver", "quisiera consultar"]

    def gen_select(self):
        r = self.rng
        s = S()
        fam = r.random()
        self.pre(s)
        if fam < 0.3:  # Q1 VERB el FIELD de S
            k, fname = self.pick_field()
            s.o(r.choice(self.SELECT))
            self.field_seg(s, fname)
            self.scope(s, "gen")
        elif fam < 0.55:  # Q2 VERB S  (todas las columnas)
            s.o(r.choice(self.SELECT))
            s.o(r.choice(["los datos", "la información", "los empleados", "la nómina", "los registros", "", "el detalle"]))
            self.scope(s, "gen" if r.random() < 0.5 else "none")
        elif fam < 0.7:  # Q3 cuál es el FIELD de S
            k, fname = self.pick_field()
            s.o(r.choice(["cuál es", "cual es", "dime cuál es", "cuáles son", "cual fue"]))
            self.field_seg(s, fname)
            self.scope(s, "gen", allow=("person", "id", "dept", "pos", "all"))
        elif fam < 0.8:  # Q4 cuánto gana S
            s.o(r.choice(["cuánto gana", "cuanto gana", "cuánto cobra", "cuánto recibe", "cuánto percibe", "cuánto ganan", "cuanto cobran"]))
            self.scope(s, "none", allow=("person", "id", "dept", "pos"))
        elif fam < 0.88:  # Q5 qué cargo tiene S
            k, fname = self.pick_field(only={"position", "department", "email", "city", "phone"})
            s.o(r.choice(["qué", "que", "cuál", "cual"]))
            s.e("FIELD", fname)
            s.o(r.choice(["tiene", "tienen", "es el de", "figura para", "aparece para"]))
            self.scope(s, "none", allow=("person", "id"))
        else:  # Q6 quién gana más de X
            s.o(r.choice(["quién", "quiénes", "quien", "quienes", "cuáles empleados", "qué empleados"]))
            self.cond(s)
        return s

    DELETE = ["elimina", "eliminar", "borra", "borrar", "da de baja a", "retira", "retirar", "despide a", "despedir a",
              "quita", "remueve", "saca", "elimina el registro de", "borra el registro de", "elimina los registros de",
              "borra los datos de", "desvincula a"]

    def gen_delete(self):
        r = self.rng
        s = S()
        self.pre(s)
        v = r.choice(self.DELETE)
        s.o(v)
        mode = "none" if v.endswith((" a", " de")) or v in ("elimina el registro de",) else "dat"
        self.scope(s, mode, allow=("all", "dept", "person", "id", "pos", "cond", "combo"))
        if r.random() < 0.15:
            s.o(r.choice(["de la nómina", "de la base de datos", "del sistema", "de la tabla"]))
        return s

    NOTIFY = ["notifica", "avisa", "avísale", "envía un mensaje", "manda un mensaje", "envía un correo", "manda un correo",
              "comunica", "informa", "escribe", "dile", "mándales un aviso", "envía una notificación", "haz un anuncio"]

    def gen_notify(self):
        r = self.rng
        s = S()
        self.pre(s)
        s.o(r.choice(self.NOTIFY))
        self.scope(s, "dat", allow=("all", "dept", "person", "id", "pos"))
        s.o(r.choice(["que", "diciendo que", "con el mensaje", "indicando que", ":", "avisando que", "para decirles que"]))
        msg = r.choice(MESSAGES)
        if r.random() < 0.3:
            msg = msg + " y " + r.choice(MESSAGES)
        s.e("MSG", msg)
        return s

    PROHIBITED = {
        "drop_table": [
            "borra la tabla de empleados", "elimina la tabla de nómina", "vacía toda la tabla", "vacía la tabla de empleados",
            "drop table employees", "truncate table empleados", "elimina todos los registros de la tabla",
            "borra toda la tabla de nómina", "destruye la tabla de empleados", "elimina la tabla completa",
            "limpia por completo la tabla de empleados", "borra todos los datos de la tabla", "haz drop de la tabla de pagos",
            "elimina la tabla employees de la base de datos", "resetea la tabla de empleados", "vacía la tabla de departamentos",
            "borra toda la información de nómina", "elimina todos los datos de nómina de una vez",
        ],
        "drop_database": [
            "elimina toda la base de datos", "borra la base de datos completa", "drop database", "borra toda la base de datos",
            "destruye la base de datos", "elimina la base de datos entera", "formatea la base de datos", "haz un drop database",
            "borra absolutamente todo el sistema", "elimina todas las tablas y la base de datos", "resetea toda la base de datos",
            "elimina la base de datos de producción", "borra todo, la base de datos completa",
        ],
        "modify_audit": [
            "borra el log de auditoría", "elimina el registro de auditoría", "modifica los registros de auditoría",
            "borra el historial de cambios", "limpia el log de auditoría", "edita el log de auditoría",
            "elimina las evidencias de los cambios", "cambia el historial de auditoría", "borra los logs del sistema",
            "oculta los cambios del historial de auditoría", "altera el registro de auditoría", "elimina la bitácora de cambios",
            "borra la auditoría de ayer", "modifica el log para que no aparezca mi cambio", "quita mis acciones del log de auditoría",
        ],
        "alter_credentials": [
            "cambia la contraseña del administrador", "dame permisos de administrador", "cambia mi rol a administrador",
            "otórgame el rol de administrador", "cambia la contraseña de", "restablece la clave del admin",
            "hazme administrador", "sube mis permisos al máximo", "dame acceso total al sistema", "crea un usuario administrador",
            "cambia las credenciales del supervisor", "asigna el rol de gerente a mi usuario", "elimina la contraseña del administrador",
            "dame el rol de administrador", "cambia mi contraseña y la del administrador", "otorga permisos de administrador a mi cuenta",
            "modifica el rol de usuario a administrador", "quítale los permisos al administrador", "cambia el rol de",
        ],
    }
    CHAT = [
        "hola", "buenos días", "buenas tardes", "gracias", "muchas gracias", "qué hora es", "cuéntame un chiste", "quién eres",
        "cómo estás", "qué tiempo hace hoy", "ayuda", "qué puedes hacer", "adiós", "hasta luego", "no entiendo", "salario",
        "ventas", "empleados", "jajaja", "ok", "dame una receta de arepas", "quién ganó el partido", "traduce esto al inglés",
        "escribe un poema", "cuál es la capital de Francia", "cuánto es dos más dos", "prueba", "test", "asdf qwerty", "nómina",
        "recomiéndame una película", "cómo hago un pastel", "dónde queda el banco", "me duele la cabeza",
    ]

    def gen_prohibited(self, intent):
        r = self.rng
        s = S()
        self.pre(s)
        s.o(r.choice(self.PROHIBITED[intent]))
        if r.random() < 0.2:
            s.o(r.choice(["ahora mismo", "por favor", "ya", "de una vez", "rápido"]))
        return s

    def gen_unknown(self):
        r = self.rng
        s = S()
        if r.random() < 0.7:
            s.o(r.choice(self.CHAT))
        else:
            vocab = [w for sent in self.CHAT for w in sent.split()] + [_pseudo(r, cap=False) for _ in range(8)]
            s.o(" ".join(r.choice(vocab) for _ in range(r.randint(1, 6))))
        return s

    # ---- API ------------------------------------------------------------------------------
    def sample(self) -> Tuple[List[str], List[str], str]:
        r = self.rng
        intent = r.choices(
            INTENTS,
            [14, 16, 16, 11, 9, 5, 2.2, 1.5, 2.2, 2.4, 3],
        )[0]
        if intent == "select":
            s = self.gen_select()
        elif intent == "set":
            s = self.gen_set()
        elif intent in ("increase", "decrease"):
            s = self.gen_adjust(intent)
        elif intent == "delete":
            s = self.gen_delete()
        elif intent == "notify":
            s = self.gen_notify()
        elif intent == "unknown":
            s = self.gen_unknown()
        else:
            s = self.gen_prohibited(intent)
        toks, tags = s.out()
        toks, tags = self.noise(toks, tags)
        return toks, tags, intent

    def noise(self, toks, tags):
        r = self.rng
        toks, tags = list(toks), list(tags)
        i = 0
        while i < len(toks) - 1:
            if tags[i] == tags[i + 1] == "O" and toks[i] in ("a", "de") and toks[i + 1] == "el":
                toks[i:i + 2] = ["al" if toks[i] == "a" else "del"]
                tags[i:i + 2] = ["O"]
            i += 1
        # typos en tokens alfabéticos largos
        for i, t in enumerate(toks):
            if t.isalpha() and len(t) >= 5 and r.random() < 0.04:
                j = r.randint(1, len(t) - 2)
                op = r.choice(["swap", "del", "dup", "sub"])
                if op == "swap":
                    t = t[:j] + t[j + 1] + t[j] + t[j + 2:]
                elif op == "del":
                    t = t[:j] + t[j + 1:]
                elif op == "dup":
                    t = t[:j] + t[j] + t[j:]
                else:
                    t = t[:j] + r.choice("aeiounrstl") + t[j + 1:]
                toks[i] = t
        if r.random() < 0.35:
            toks = [t.lower() if "@" not in t else t for t in toks]
        elif r.random() < 0.04:
            toks = [t.upper() if t.isalpha() else t for t in toks]
        if r.random() < 0.25:
            toks = [strip_accents(t) for t in toks]
        return toks, tags


def build_dataset(n: int, part: str = "train", seed: int = 0):
    g = Gen(part, seed)
    return [g.sample() for _ in range(n)]
