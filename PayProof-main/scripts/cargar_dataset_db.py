"""Carga un CSV de nómina (cualquier orden de columnas) en la base SQLite de PayProof.

    python scripts/cargar_dataset_db.py payroll_ai/examples/nomina_2800.csv --reset
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from payroll_ai.csvio import Table  # noqa: E402
from payroll_ai.engine import PayrollAssistant  # noqa: E402
from payroll_ai.text import parse_number  # noqa: E402
from securedb import database  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--reset", action="store_true", help="borra los empleados actuales antes de cargar")
    a = ap.parse_args()
    with open(a.csv, encoding="utf-8-sig", newline="") as fh:
        table = Table.from_text(fh.read())
    schema = PayrollAssistant.load().analyze(table)
    col = {}
    for c in schema.cols:
        col.setdefault(c.concept, c.idx)
    need = ["first_name", "department", "position", "salary"]
    missing = [n for n in need if n not in col]
    if missing:
        sys.exit(f"No pude identificar las columnas: {missing}\n{schema.describe()}")
    database.init_db()
    with database.get_conn() as conn:
        if a.reset:
            conn.execute("DELETE FROM employees")
        deps = {r["nombre"]: r["id"] for r in conn.execute("SELECT id, nombre FROM departments")}
        n = 0
        for r in table.rows:
            d = r[col["department"]].strip()
            if d not in deps:
                deps[d] = conn.execute("INSERT INTO departments (nombre) VALUES (?)", (d,)).lastrowid
            sal = parse_number(r[col["salary"]])
            if sal is None:
                continue
            conn.execute(
                "INSERT INTO employees (nombre, apellido, email, departamento_id, cargo, salario, fecha_ingreso) "
                "VALUES (?,?,?,?,?,?,?)",
                (r[col["first_name"]], r[col["last_name"]] if "last_name" in col else "",
                 r[col["email"]] if "email" in col else "", deps[d], r[col["position"]], sal,
                 r[col["hire_date"]] if "hire_date" in col else ""),
            )
            n += 1
    print(f"{n} empleados cargados en {database.DB_PATH}")


if __name__ == "__main__":
    main()
