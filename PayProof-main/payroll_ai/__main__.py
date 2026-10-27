"""Línea de comandos:

    python -m payroll_ai nomina.csv --role administrador "sube 10% el salario a los de ventas"
    python -m payroll_ai nomina.csv --role administrador "sube 10% ..." --apply salida.csv --confirm
    python -m payroll_ai nomina.csv --schema            # muestra cómo entendió las columnas
    python -m payroll_ai nomina.csv --role gerente      # modo interactivo
"""
from __future__ import annotations

import argparse
import sys

from .csvio import Table
from .engine import PayrollAssistant


def main(argv=None):
    ap = argparse.ArgumentParser(prog="payroll_ai")
    ap.add_argument("csv")
    ap.add_argument("instruction", nargs="?")
    ap.add_argument("--role", default="usuario", help="usuario | operador | gerente | administrador")
    ap.add_argument("--approver", help="rol de quien aprueba (si el plan requiere aprobación)")
    ap.add_argument("--confirm", action="store_true", help="confirmación explícita para riesgo crítico")
    ap.add_argument("--apply", metavar="SALIDA.csv", help="escribe el resultado en este archivo")
    ap.add_argument("--schema", action="store_true")
    a = ap.parse_args(argv)

    with open(a.csv, encoding="utf-8-sig", newline="") as fh:
        table = Table.from_text(fh.read())
    bot = PayrollAssistant.load()
    if a.schema:
        print(bot.analyze(table).describe())
        if not a.instruction:
            return 0

    def run(ins: str):
        nonlocal table
        plan = bot.plan(ins, table, a.role)
        print(plan.explain(table))
        if a.apply or a.approver or a.confirm:
            res = bot.execute(plan, table, approver_role=a.approver, confirm=a.confirm)
            print("->", res.message)
            if res.selected is not None:
                print(res.selected.to_text())
            if res.ok and res.table is not None and a.apply:
                with open(a.apply, "w", encoding="utf-8", newline="") as fh:
                    fh.write(res.table.to_text())
                print("Guardado en", a.apply)
        elif plan.decision == "ALLOW" and plan.kind == "select":
            res = bot.execute(plan, table)
            print(res.selected.to_text())

    if a.instruction:
        run(a.instruction)
        return 0
    print("Modo interactivo (Ctrl+D para salir). Columnas detectadas:")
    print(bot.analyze(table).describe())
    for line in sys.stdin:
        if line.strip():
            run(line.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
