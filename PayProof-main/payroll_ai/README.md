# payroll_ai — modelo propio para editar nómina en lenguaje natural

Modelo **entrenado desde cero** (sin APIs, sin pesos preentrenados, sin PyTorch: solo `numpy`).
Permite que un usuario escriba "sube 10% el salario a los de Ventas" sobre un **CSV de estructura
arbitraria** y que el sistema decida, según el **rol**, si lo ejecuta, pide aprobación o lo bloquea.

## Arquitectura (4 piezas)

```
instrucción ──► [1] Modelo de instrucciones (IA propia)
                    ├─ clasificador de intención  (select/set/increase/decrease/delete/notify/prohibidas/unknown)
                    └─ etiquetador de entidades   (campo, monto, % , valor nuevo, filtros, condiciones, "todos"…)
CSV crudo ─────► [2] Lector de CSV sin estructura (delimitador, títulos, encabezado opcional, totales, formatos numéricos)
                 [3] Mapeador de columnas (IA propia): qué significa cada columna, por encabezado y/o por sus valores
                 [4] Motor determinista: filas afectadas, cambios, riesgo, ROL, aprobación, confirmación, hash anti-manipulación
```

**El modelo solo interpreta. La autorización la decide código determinista** (`engine.py`); ninguna
instrucción puede cambiar el rol ni saltarse la política.

| Archivo | Qué hace |
|---|---|
| `nn.py` | Redes y optimizador Adam escritos a mano (forward + backprop) |
| `datagen.py` | Genera miles de instrucciones etiquetadas (formal/informal, typos, sin tildes…) |
| `train_instructions.py` | Entrena y evalúa intención + entidades |
| `columns.py` | Datos sintéticos, entrenamiento y evaluación del mapeador de columnas |
| `csvio.py` | Lectura/escritura de CSV desordenado y formato numérico por columna |
| `engine.py` | Roles, riesgo, planes, ejecución |
| `weights/` | Pesos entrenados (`.npz`) |

## Uso en la app (Streamlit)

Pestaña **Nómina CSV**: adjuntas el CSV (cualquier orden de columnas/filas, separador `, ; | tab`, con o sin
encabezado/títulos/totales), revisas cómo entendió las columnas (puedes corregirlas), escribes la instrucción y
el motor aplica tu rol. Si tu rol no alcanza, **Enviar a aprobación**: otro usuario con rol suficiente la aprueba
en la misma pestaña (nadie aprueba lo suyo) y tú descargas el resultado. Todo queda en el log de auditoría.

## Uso por línea de comandos

```bash
pip install numpy
python -m payroll_ai payroll_ai/examples/nomina_es.csv --schema
python -m payroll_ai payroll_ai/examples/nomina_es.csv --role administrador "sube un 10% el salario a los de Ventas"
python -m payroll_ai payroll_ai/examples/nomina_es.csv --role administrador --confirm --apply salida.csv \
       "aumentale 200 mil al sueldo de Juan Pérez"
```

```python
from payroll_ai.engine import PayrollAssistant
from payroll_ai.csvio import Table

bot = PayrollAssistant.load()
table = Table.from_text(open("nomina.csv", encoding="utf-8").read())
plan = bot.plan("sube 10% el salario a los de Ventas", table, role="gerente")
print(plan.explain(table))            # decisión, filas, antes→después, riesgo, motivos
res = bot.execute(plan, table, approver_role="administrador", confirm=True)
open("salida.csv", "w", encoding="utf-8").write(res.table.to_text())
```

## Roles y política (editable en `engine.py`)

Roles: `usuario(0) < operador(1) < gerente(2) < administrador(3)` (`supervisor` = `gerente`).

| Operación | Riesgo | Quién puede |
|---|---|---|
| Consultar datos no sensibles | 0 | cualquiera |
| Consultar salarios / datos personales | +1 c/u (+1 si es masivo) | operador+ |
| Modificar un dato no financiero | 1 | operador+ |
| Modificar varios datos no financieros | 2 | gerente+ |
| Modificar 1 salario/bono | 3 | administrador |
| Modificar varios salarios/bonos | 4 | administrador **+ confirmación explícita** |
| Ajuste porcentual > 20 % | +1 | — |
| Eliminar filas | 4 | administrador + confirmación |
| `drop table`, `drop database`, tocar auditoría, cambiar roles/credenciales | PROHIBIDO | **nadie** (ni admin) |

Si el rol no alcanza → `NEEDS_APPROVAL`: otro usuario con rol suficiente debe aprobar
(`execute(..., approver_role=...)`) y los valores sensibles quedan ocultos en la vista previa.

Salvaguardas: sin alcance explícito ("a todos", "a los de X"…) el sistema **pregunta**, nunca
asume "todos"; nombres ambiguos ("Juan" ×2) se rechazan; el plan lleva un `payload_hash`
(si alguien altera el plan antes de ejecutar → `TAMPER_PAYLOAD`) y la huella del archivo
(si el CSV cambió → plan caducado); el CSV original nunca se modifica en memoria.

## Reentrenar

```bash
python -m payroll_ai.train_instructions   # ~30-40 min en CPU de 2 núcleos
python -m payroll_ai.columns              # ~7 min
```

Para enseñarle **nuevas frases** o **nuevos conceptos de columna**, agrega plantillas/sinónimos en
`datagen.py` / `columns.py` (`HEADERS`) y reentrena. No hace falta etiquetar a mano.

## Límites conocidos (honestos)

* **Idioma**: español. En inglés entiende los encabezados del CSV pero no las instrucciones.
* **Una operación por instrucción** (no "sube 5% y cambia el cargo…").
* Los datos de entrenamiento son **sintéticos**: la exactitud en pruebas del generador es alta, pero
  frases muy distintas a las plantillas pueden fallar. Ante la duda el motor responde `CLARIFY`
  en lugar de adivinar. Mejora real: guarda en producción las instrucciones fallidas, etiquétalas y
  agrégalas al entrenamiento.
* CSV **sin encabezado**: el significado se deduce solo por los valores; columnas de texto parecidas
  (área vs. cargo vs. ciudad) pueden confundirse → usa `overrides={indice: "concepto"}`.
* Las filas de totales no se recalculan (se avisa en `warnings`).
* No hay condiciones compuestas con "o", ni referencias a filas por posición.
