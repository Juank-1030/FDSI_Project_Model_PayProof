"""Pruebas del modelo propio (payroll_ai). Funcionan con pytest o con `python -m unittest`."""
import os
import unittest

from payroll_ai.csvio import NumFormat, Table
from payroll_ai.engine import PayrollAssistant
from payroll_ai.text import parse_number, tokenize

EX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "payroll_ai", "examples")


def load(name):
    with open(os.path.join(EX, name), encoding="utf-8") as fh:
        return Table.from_text(fh.read())


class TextTests(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(parse_number("3.200.000"), 3200000)
        self.assertEqual(parse_number("3,200,000.50"), 3200000.5)
        self.assertEqual(parse_number("$ 1.500,75"), 1500.75)
        self.assertEqual(parse_number("2.5"), 2.5)
        self.assertEqual(parse_number("1.500"), 1500)
        self.assertIsNone(parse_number("abc"))

    def test_number_words(self):
        surf = [t.surface for t in tokenize("sube diez por ciento a los que ganen menos de dos millones quinientos mil")]
        self.assertIn("10", surf)
        self.assertIn("%", surf)
        self.assertIn("2500000", surf)
        self.assertEqual([t.surface for t in tokenize("3 millones")], ["3000000"])
        self.assertEqual([t.surface for t in tokenize("treinta y cinco")], ["35"])


class CsvTests(unittest.TestCase):
    def test_semicolon_title_total(self):
        t = load("nomina_semicolon.csv")
        self.assertEqual(t.delimiter, ";")
        self.assertEqual(len(t.pre), 1)
        self.assertEqual(len(t.post), 1)
        self.assertTrue(t.has_header)
        self.assertEqual(len(t.rows), 15)

    def test_no_header(self):
        t = load("nomina_sin_encabezado.csv")
        self.assertFalse(t.has_header)
        self.assertEqual(len(t.rows), 15)

    def test_roundtrip_content(self):
        t = load("nomina_es.csv")
        self.assertEqual(Table.from_text(t.to_text()).rows, t.rows)

    def test_number_format(self):
        self.assertEqual(NumFormat("$ ", "", ".", "", 0).render(4400000), "$ 4.400.000")
        self.assertEqual(NumFormat("", "", ",", ".", 2).render(2520), "2,520.00")


class AssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = PayrollAssistant.load()
        cls.t = load("nomina_es.csv")

    def plan(self, role, text, table=None):
        return self.bot.plan(text, table or self.t, role)

    def test_schema_variants(self):
        sch = self.bot.analyze(load("nomina_semicolon.csv"))
        self.assertEqual([c.concept for c in sch.cols][:5], ["employee_id", "full_name", "department", "position", "salary"])
        sch = self.bot.analyze(load("payroll_en.csv"))
        self.assertIn("salary", [c.concept for c in sch.cols])

    def test_admin_raise_one_employee(self):
        p = self.plan("administrador", "aumentale 200 mil al sueldo de Juan Pérez")
        self.assertEqual(p.decision, "ALLOW")
        self.assertEqual(len(p.rows), 1)
        self.assertEqual(p.changes[0].old, "4000000")
        self.assertEqual(p.changes[0].new, "4200000")

    def test_mass_raise_needs_confirmation_and_keeps_format(self):
        t = load("nomina_semicolon.csv")
        p = self.plan("administrador", "sube el sueldo base de los de Ventas un 10%", t)
        self.assertEqual(p.decision, "ALLOW")
        self.assertEqual(len(p.rows), 3)
        self.assertEqual(p.changes[0].new, "$ 4.400.000")
        self.assertFalse(self.bot.execute(p, t).ok)                 # sin confirmación no se ejecuta
        res = self.bot.execute(p, t, confirm=True)
        self.assertTrue(res.ok)
        self.assertEqual(len(res.table.post), 1)                    # fila de totales intacta
        self.assertEqual(res.table.rows[0][4], "$ 4.400.000")
        self.assertEqual(t.rows[0][4], "$ 4.000.000")               # el original no se modifica

    def test_manager_needs_admin_approval(self):
        p = self.plan("gerente", "sube 10% el salario a los de Ventas")
        self.assertEqual(p.decision, "NEEDS_APPROVAL")
        self.assertFalse(self.bot.execute(p, self.t, confirm=True).ok)
        self.assertFalse(self.bot.execute(p, self.t, approver_role="operador", confirm=True).ok)
        self.assertTrue(self.bot.execute(p, self.t, approver_role="administrador", confirm=True).ok)

    def test_values_hidden_without_authority(self):
        p = self.plan("usuario", "muestra el salario de Maria Lopez")
        self.assertEqual(p.decision, "NEEDS_APPROVAL")
        self.assertNotIn("5400000", p.explain(self.t))

    def test_user_can_read_non_sensitive(self):
        p = self.plan("usuario", "muéstrame los cargos de todos")
        self.assertEqual(p.decision, "ALLOW")
        res = self.bot.execute(p, self.t)
        self.assertEqual(len(res.selected.rows), 15)

    def test_operator_edits_position(self):
        p = self.plan("operador", "cambia el cargo de Ana Torres a Gerente")
        self.assertEqual(p.decision, "ALLOW")
        res = self.bot.execute(p, self.t)
        self.assertEqual(res.table.rows[3][5], "Gerente")

    def test_prohibited_even_for_admin(self):
        for text in ["borra la tabla de empleados", "borra toda la base de datos", "limpia el log de auditoría",
                     "dame permisos de administrador"]:
            p = self.plan("administrador", text)
            self.assertEqual(p.decision, "BLOCKED", text)
            self.assertFalse(self.bot.execute(p, self.t, approver_role="administrador", confirm=True).ok)

    def test_no_scope_asks(self):
        self.assertEqual(self.plan("administrador", "sube el sueldo 10%").decision, "CLARIFY")

    def test_ambiguous_name_asks(self):
        p = self.plan("administrador", "aumenta el sueldo de Juan")
        self.assertEqual(p.decision, "CLARIFY")
        self.assertEqual(p.rows, [])

    def test_unknown_value_asks(self):
        self.assertEqual(self.plan("administrador", "aumenta el salario de los de Narnia 10%").decision, "CLARIFY")

    def test_smalltalk(self):
        self.assertEqual(self.plan("administrador", "qué buen día").decision, "CLARIFY")

    def test_tamper_and_stale(self):
        p = self.plan("administrador", "aumentale 200 mil al sueldo de Juan Pérez")
        stale = self.t.copy()
        stale.rows[0][6] = "1"
        self.assertFalse(self.bot.execute(p, stale).ok)
        p.changes[0].new = "99999999"
        self.assertIn("TAMPER", self.bot.execute(p, self.t).message)

    def test_condition_and_number_words(self):
        p = self.plan("administrador", "sube diez por ciento el salario de los que ganan menos de dos millones quinientos mil")
        self.assertEqual(p.decision, "ALLOW")
        self.assertTrue(all(float(self.t.rows[r][6]) < 2500000 for r in p.rows))
        self.assertEqual(len(p.rows), sum(float(r[6]) < 2500000 for r in self.t.rows))

    def test_headerless_with_override(self):
        t = load("nomina_sin_encabezado.csv")
        p = self.bot.plan("sube 5% el salario de Pérez Juan", t, "administrador",
                          overrides={2: "salary"})
        self.assertEqual(p.decision, "ALLOW")
        self.assertEqual(p.changes[0].new, "4200000")


class ShuffleAndApprovalTests(unittest.TestCase):
    def _shuffled(self, seed, delim=","):
        import csv, io, random
        with open(os.path.join(EX, "nomina_es.csv"), encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        H, D = rows[0], rows[1:]
        r = random.Random(seed)
        order = list(range(len(H)))
        r.shuffle(order)
        r.shuffle(D)
        out = io.StringIO()
        w = csv.writer(out, delimiter=delim)
        w.writerow([H[i] for i in order])
        for x in D:
            w.writerow([x[i] for i in order])
        return Table.from_text(out.getvalue())

    def test_any_column_and_row_order(self):
        bot = PayrollAssistant.load()
        for seed in range(15):
            t = self._shuffled(seed, ";" if seed % 2 else ",")
            p = bot.plan("sube 10% el salario a los de Ventas", t, "administrador")
            self.assertEqual((p.decision, p.column_header, len(p.rows)), ("ALLOW", "salario", 3), seed)
            p = bot.plan("aumentale 200 mil al sueldo de Juan Pérez", t, "administrador")
            self.assertEqual(len(p.rows), 1, seed)
            self.assertEqual(p.changes[0].new, "4200000", seed)

    def test_request_approval_flow(self):
        import tempfile
        import securedb.database as db
        from securedb import csv_requests as cr
        old = db.DB_PATH
        db.DB_PATH = os.path.join(tempfile.mkdtemp(), "t.db")
        try:
            db.init_db()
            t = self._shuffled(1)
            ger = {"username": "supervisor", "role": "supervisor"}
            adm = {"username": "admin", "role": "administrador"}
            plan = cr.bot().plan("sube 10% el salario a los de Ventas", t, "supervisor")
            self.assertEqual(plan.decision, "NEEDS_APPROVAL")
            with db.get_conn() as c:
                rid = cr.create(c, ger, "sube 10% el salario a los de Ventas", "n.csv", t.to_text(), {}, plan)
                self.assertEqual(len(cr.pending_for(c, ger)), 0)
                self.assertEqual(len(cr.pending_for(c, adm)), 1)
                self.assertFalse(cr.decide(c, rid, ger, True, True)[0])      # sin auto-aprobación
                self.assertFalse(cr.decide(c, rid, adm, True, False)[0])     # crítico exige confirmación
                self.assertTrue(cr.decide(c, rid, adm, True, True)[0])
        finally:
            db.DB_PATH = old


class BigDatasetTests(unittest.TestCase):
    def test_2800_rows_both_formats(self):
        bot = PayrollAssistant.load()
        for name in ("nomina_2800.csv", "nomina_2800_desordenado.csv"):
            t = load(name)
            self.assertEqual(len(t.rows), 2800)
            sch = {c.concept: c.idx for c in bot.analyze(t).cols}
            p = bot.plan("sube un 10% el salario a los de Ventas", t, "administrador")
            exp = {i for i, r in enumerate(t.rows) if r[sch["department"]] == "Ventas"}
            self.assertEqual(set(p.rows), exp, name)
            self.assertEqual(bot.plan("sube un 10% el salario a los de Ventas", t, "gerente").decision, "NEEDS_APPROVAL")


if __name__ == "__main__":
    unittest.main()
