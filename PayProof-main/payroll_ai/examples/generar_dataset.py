"""Genera datasets sintéticos de nómina para probar (determinista por semilla).

    python payroll_ai/examples/generar_dataset.py            # crea nomina_2800.csv y nomina_2800_desordenado.csv
    python payroll_ai/examples/generar_dataset.py --n 5000 --seed 7
"""
import argparse
import csv
import os
import random
import unicodedata

FIRST = ("Juan Maria Carlos Ana Luis Laura Pedro Sofia Andres Camila Diego Valentina Jorge Daniela Miguel Paula Fernando "
         "Carolina Ricardo Natalia Santiago Isabella Mateo Gabriela Sebastian Lucia Alejandro Marcela Javier Claudia Felipe "
         "Monica Oscar Patricia Hector Veronica Raul Adriana Sergio Diana Julian Angela Esteban Johana Mauricio Liliana "
         "Cristian Sandra Nicolas Tatiana Ivan Alvaro Beatriz Rodrigo Elena Hugo Marta Ramiro Teresa Gonzalo Rosa Emilio "
         "Silvia Samuel Karen Brayan Yesenia Wilson Yolanda Jairo Luz Edwin Piedad Cesar Nubia Fabian Lorena Gustavo Pilar "
         "Armando Susana Leonardo Irene Manuel Eliana Rafael Alicia Bernardo Gloria Orlando Mariana Pablo Jimena Tomas "
         "Renata Ernesto Olga Franco Esperanza Ulises Dayana").split()
LAST = ("Perez Gomez Rodriguez Martinez Lopez Garcia Hernandez Gonzalez Ramirez Torres Diaz Vargas Castro Rojas Moreno "
        "Jimenez Ortiz Romero Suarez Morales Cardenas Herrera Medina Aguilar Mejia Salazar Pineda Rios Cortes Guerrero "
        "Navarro Delgado Vega Cruz Reyes Flores Mendoza Silva Rivera Sanchez Ramos Cano Soto Parra Pardo Bernal Carvajal "
        "Ospina Quintero Duarte Montoya Valencia Arango Zapata Giraldo Restrepo Henao Londono Escobar Naranjo Villa "
        "Munoz Acosta Bautista Pena Campos Cabrera Fuentes Ibarra Lara Leon Marin Nieto Ochoa Pacheco Rueda Serrano "
        "Tapia Uribe Velez Yepes Zuluaga Barrera Cuesta Duque Espinosa Fajardo Gallego Hoyos Isaza Jaramillo").split()
# departamento -> [(cargo, salario_min, salario_max)]
ORG = {
    "Ventas": [("Vendedor", 1.8, 3.5), ("Asesor Comercial", 2.2, 4.2), ("Coordinador", 4.0, 6.5), ("Gerente", 7.0, 12.0)],
    "Finanzas": [("Auxiliar", 1.8, 2.8), ("Analista", 3.0, 5.5), ("Contador", 4.5, 7.5), ("Gerente", 8.0, 14.0)],
    "Tecnología": [("Desarrollador", 3.5, 8.0), ("Ingeniero", 4.0, 9.0), ("Analista", 3.0, 5.5), ("Líder de Proyecto", 8.0, 13.0)],
    "Recursos Humanos": [("Auxiliar", 1.8, 2.8), ("Analista", 3.0, 5.0), ("Coordinador", 4.0, 6.5), ("Gerente", 7.5, 12.0)],
    "Logística": [("Operario", 1.5, 2.4), ("Almacenista", 1.7, 2.6), ("Conductor", 1.9, 3.0), ("Supervisor", 3.5, 5.5)],
    "Producción": [("Operario", 1.5, 2.5), ("Técnico", 2.2, 3.8), ("Supervisor", 3.5, 5.8), ("Jefe de Planta", 6.5, 10.0)],
    "Marketing": [("Diseñador", 2.5, 5.0), ("Analista", 3.0, 5.5), ("Coordinador", 4.0, 6.5), ("Gerente", 7.5, 12.5)],
    "Compras": [("Auxiliar", 1.8, 2.8), ("Analista", 3.0, 5.0), ("Jefe de Compras", 6.0, 9.5)],
    "Legal": [("Abogado", 4.5, 9.0), ("Asistente", 2.0, 3.2), ("Director", 9.0, 15.0)],
    "Soporte": [("Técnico", 2.0, 3.6), ("Analista", 2.8, 4.8), ("Supervisor", 3.8, 6.0)],
    "Calidad": [("Auditor", 3.5, 6.0), ("Analista", 3.0, 5.0), ("Coordinador", 4.5, 7.0)],
    "Administración": [("Secretaria", 1.8, 2.8), ("Recepcionista", 1.6, 2.4), ("Asistente", 2.0, 3.4), ("Director", 9.0, 15.0)],
}
CITIES = ["Bogotá", "Medellín", "Cali", "Barranquilla", "Cartagena", "Bucaramanga", "Pereira", "Manizales"]
W = [0.30, 0.24, 0.30, 0.16]


def strip(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn").lower()


def build(n, seed):
    r = random.Random(seed)
    rows, used = [], set()
    for i in range(n):
        a, b, c = r.choice(FIRST), r.choice(LAST), r.choice(LAST)
        email = f"{strip(a)}.{strip(b)}"
        k = 1
        while email in used:
            k += 1
            email = f"{strip(a)}.{strip(b)}{k}"
        used.add(email)
        dep = r.choices(list(ORG), [8, 7, 9, 4, 8, 7, 4, 3, 2, 4, 3, 3])[0]
        pos, lo, hi = r.choices(ORG[dep], W[: len(ORG[dep])])[0]
        sal = int(round(r.uniform(lo, hi) * 1_000_000 / 50_000)) * 50_000
        bono = r.choice([0, 0, 0, 100_000, 200_000, 300_000, 500_000]) if sal < 7_000_000 else r.choice([0, 500_000, 1_000_000, 1_500_000])
        desc = int(sal * r.choice([0.08, 0.08, 0.09, 0.10]) / 1000) * 1000
        rows.append({
            "id": 10001 + i, "nombre": a, "apellido": f"{b}" if r.random() < .6 else f"{b} {c}",
            "email": f"{email}@empresa.com", "departamento": dep, "cargo": pos, "salario": sal, "bono": bono,
            "descuentos": desc, "fecha_ingreso": f"{r.randint(2008, 2025)}-{r.randint(1, 12):02d}-{r.randint(1, 28):02d}",
            "ciudad": r.choice(CITIES), "estado": r.choices(["Activo", "Vacaciones", "Licencia"], [92, 5, 3])[0],
        })
    return rows


def money(v):
    return "$ " + f"{v:,}".replace(",", ".")


def write_clean(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_messy(rows, path, seed):
    """Otro formato: separador ';', líneas de título, columnas y filas en desorden, encabezados distintos, $ y total."""
    r = random.Random(seed + 1)
    cols = [("id", "CÓDIGO"), ("nombre", "NOMBRES"), ("apellido", "APELLIDOS"), ("departamento", "ÁREA"), ("cargo", "PUESTO"),
            ("salario", "SAL_BASE"), ("bono", "BONIFICACIÓN"), ("descuentos", "DEDUCCIONES"), ("email", "CORREO"),
            ("fecha_ingreso", "F_INGRESO"), ("ciudad", "SEDE"), ("estado", "SITUACIÓN")]
    r.shuffle(cols)
    data = rows[:]
    r.shuffle(data)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("NÓMINA CONSOLIDADA - EMPRESA XYZ S.A.S.\nGenerado automáticamente\n\n")
        w = csv.writer(fh, delimiter=";")
        w.writerow([h for _, h in cols])
        for x in data:
            w.writerow([money(x[k]) if k in ("salario", "bono", "descuentos") else x[k] for k, _ in cols])
        w.writerow([("TOTAL" if i == 0 else money(sum(x[k] for x in rows)) if k == "salario" else "") for i, (k, _) in enumerate(cols)])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2800)
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    rows = build(a.n, a.seed)
    write_clean(rows, os.path.join(here, f"nomina_{a.n}.csv"))
    write_messy(rows, os.path.join(here, f"nomina_{a.n}_desordenado.csv"), a.seed)
    print(f"{a.n} empleados -> nomina_{a.n}.csv y nomina_{a.n}_desordenado.csv")
