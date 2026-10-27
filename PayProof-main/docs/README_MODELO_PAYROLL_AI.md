# README — Modelo propio `payroll_ai`

Documento técnico de referencia del modelo de Inteligencia Artificial **entrenado desde cero** que
incorpora PayProof/SecureDB. Explica **qué se cambió**, **cómo está implementado el modelo**, **qué
se necesita para que funcione**, **cómo se entrenó** y **qué métricas cumple realmente** (medidas sobre
los pesos incluidos en el repositorio, no estimaciones).

> Regla de oro del sistema: **el modelo solo interpreta**. La autorización por rol, el riesgo, la
> aprobación y la auditoría viven en código determinista (`payroll_ai/engine.py`, `securedb/risk_engine.py`,
> `securedb/approval.py`, `securedb/audit.py`). Ninguna instrucción puede convencer al modelo de saltarse
> la política.

---

## 1. Resumen ejecutivo

| Aspecto | Valor |
|---|---|
| Tipo de modelo | Redes neuronales propias (MLP + etiquetador por ventana de contexto) |
| Framework | **Ninguno**: NumPy puro (forward, backprop y Adam escritos a mano) |
| Pesos preentrenados | **Ninguno** (inicialización aleatoria + datos sintéticos) |
| APIs externas | **Ninguna** en el modo por defecto (`LLM_PROVIDER=local`) |
| Parámetros totales | **2.831.553** |
| Tamaño de pesos en disco | **≈ 10,5 MB** (3 archivos `.npz` + `vocab.json`) |
| Idiomas | Español (instrucciones); encabezados de CSV en español e inglés |
| Exactitud de intención (test) | **99,68 %** |
| F1 de extracción de entidades (test, span exacto) | **93,54 %** |
| Exactitud del mapeador de columnas (test) | **99,50 %** |
| Encabezados nunca vistos, con valores | **99,70 %** |
| Encabezados nunca vistos, solo texto | **91,04 %** |
| CSV sin encabezado (solo valores) | **93,75 %** |
| Pruebas automatizadas del modelo | **24/24 OK** (`python -m unittest tests.test_payroll_ai`) |
| Pruebas automatizadas del proyecto | **89/89 OK** (`python -m pytest -q`) |

---

## 2. Cambios realizados en el proyecto

### 2.1 Paquete nuevo `payroll_ai/`

Se agregó un paquete completo con el modelo, los datos sintéticos, el motor determinista y los pesos:

| Archivo | Función |
|---|---|
| `nn.py` | Redes y optimizador Adam implementados a mano con NumPy |
| `text.py` | Tokenización, normalización, números en español, n-gramas y rasgos de forma |
| `datagen.py` | Generador de instrucciones sintéticas etiquetadas (intención + BIO) |
| `train_instructions.py` | Entrenamiento y evaluación del modelo de instrucciones |
| `model.py` | `InstructionModel`: clasificador de intención + etiquetador de entidades |
| `columns.py` | `ColumnMapper`: clasifica el significado de cada columna del CSV |
| `csvio.py` | Lectura/escritura de CSV desordenado y formatos numéricos |
| `engine.py` | `PayrollAssistant`: plan, riesgo, roles, ejecución, hash anti-manipulación |
| `__main__.py` | CLI (`python -m payroll_ai ...`) |
| `weights/` | Pesos entrenados incluidos (`.npz`) |
| `examples/` | CSV de ejemplo, incluido `nomina_2800_desordenado.csv` |

### 2.2 Integración con SecureDB

| Archivo | Cambio |
|---|---|
| `securedb/local_agent.py` | **Nuevo**. Convierte una instrucción en `OperationProposal` usando `payroll_ai`, sin APIs |
| `securedb/agent.py` | Si `LLM_PROVIDER=local`, delega en `local_agent.extract_proposal()` |
| `securedb/config.py` | `LLM_PROVIDER` pasa a tener **`local` por defecto** |
| `.env.example` | Documenta `LLM_PROVIDER=local` como valor inicial |
| `securedb/csv_requests.py` | **Nuevo**. Solicitudes de edición de CSV con aprobación de otro usuario |
| `securedb/executor.py` | Admite ajustes relativos de salario: `{"pct": 10}` y `{"abs": 200000}` |
| `payroll_ui.py` | **Nuevo**. Pestaña de Streamlit "Nómina CSV" |
| `app.py` | Agrega la pestaña **Nómina CSV** y la integra con auditoría |
| `requirements.txt` | Agrega `numpy>=1.26` (única dependencia del modelo) |
| `tests/test_payroll_ai.py` | **Nuevo**. 24 pruebas del modelo y del flujo de aprobación |
| `scripts/cargar_dataset_db.py` | **Nuevo**. Carga un CSV de nómina en la base simulada |
| `payroll_ai/examples/` | Datasets de ejemplo, incluido el de 2.800 filas desordenado |

### 2.3 Compatibilidad

- `LLM_PROVIDER=groq` y `LLM_PROVIDER=ollama` siguen disponibles: el sistema conserva el agente
  anterior como opción, pero **ya no es necesario** para operar.
- Los permisos, el riesgo y la auditoría **no cambiaron**: el modelo se inserta solo en la fase de
  interpretación.

---

## 3. Arquitectura del modelo

```
instrucción en español
        │
        ▼
[1] InstructionModel (IA propia)
      ├─ clasificador de intención: select / set / increase / decrease /
      │                             delete / notify / drop_table / drop_database /
      │                             modify_audit / alter_credentials / unknown
      └─ etiquetador de entidades (BIO): FIELD, AMT_PCT, AMT_ABS, VALUE, ALL,
                                        FCOL, FVAL, CCOL, CLT, CLE, CGT, CGE,
                                        CVAL, MSG
        │
        ▼
[2] Lector de CSV sin estructura (csvio.py)
      delimitador, títulos, encabezado opcional, totales, formatos numéricos
        │
        ▼
[3] ColumnMapper (IA propia)
      significado de cada columna por encabezado y/o perfil de valores
        │
        ▼
[4] Motor determinista (engine.py)
      filas afectadas, cambios, riesgo, rol, aprobación, confirmación, hashes
```

### 3.1 Modelo de intención (`BagMLP`)

- Bolsa de IDs → embedding promedio → MLP → softmax sobre 11 clases.
- Vocabulario: **14.018** IDs (palabras + bigramas con hash + n-gramas de caracteres con hash).
- Dimensión de embedding: 64. Capa oculta: 128. Salida: 11.
- **906.891 parámetros**.

### 3.2 Etiquetador de entidades (`TaggerNet`)

- Por cada token: embedding de palabra (48) + embedding de n-gramas de caracteres (32) +
  embedding de forma (8) = 88 dimensiones.
- Ventana de contexto **±3 tokens** (7 posiciones) → MLP (192 ocultas) → 29 etiquetas BIO.
- **469.349 parámetros**.
- Corrección BIO posterior (`_repair_bio`) para evitar secuencias `I-X` sin `B-X`.

### 3.3 Mapeador de columnas (`ColumnMapper` / `BagMLP`)

- Entrada: IDs del encabezado (palabras, n-gramas, bigramas con hash) + IDs de valores distintos
  + **22 rasgos estadísticos** del perfil de la columna.
- Salida: 17 conceptos (`employee_id`, `first_name`, `last_name`, `full_name`, `email`, `phone`,
  `department`, `position`, `salary`, `bonus`, `deductions`, `net_pay`, `hire_date`, `document_id`,
  `status`, `city`, `other`).
- Embedding 64 + 22 rasgos densos, capa oculta 128.
- **1.455.313 parámetros**.

### 3.4 Perfil estadístico de columna (22 rasgos, selección)

| # | Rasgo |
|---|---|
| 0–2 | proporción de vacíos, de numéricos, de enteros |
| 3–4 | media y desviación de `log10(valor)` |
| 5–7 | proporción de emails, fechas y texto alfabético |
| 8–12 | longitud media, espacios, mayúscula inicial, unicidad, teléfonos |
| 13–15 | símbolos monetarios, alfanuméricos, mayúsculas |
| 17–18 | proporción de múltiplos de 1000 y de 100 |
| 19–21 | variabilidad de longitud, nombres propios, entropía de valores |

---

## 4. Qué se usa para que el modelo funcione

### 4.1 Dependencias

```text
numpy>=1.26      # único requisito del modelo
```

No se usa PyTorch, TensorFlow, scikit-learn, HuggingFace, LangChain ni ningún servicio externo para
el modelo. `langchain-*` sigue en `requirements.txt` solo para los proveedores opcionales
Groq/Ollama.

### 4.2 Artefactos incluidos

| Archivo | Contenido | Tamaño |
|---|---|---|
| `payroll_ai/weights/instructions/intent.npz` | Pesos del clasificador de intención | 3.373.104 B |
| `payroll_ai/weights/instructions/tagger.npz` | Pesos del etiquetador de entidades | 1.745.891 B |
| `payroll_ai/weights/instructions/vocab.json` | Vocabulario (1.728 palabras frecuentes) | 28.057 B |
| `payroll_ai/weights/columns/columns.npz` | Pesos del mapeador de columnas | 5.417.417 B |
| **Total** | | **≈ 10,5 MB** |

### 4.3 Carga en tiempo de ejecución

```python
from payroll_ai.engine import PayrollAssistant

bot = PayrollAssistant.load()          # lee los .npz y vocab.json
plan = bot.plan("sube 10% el salario a los de Ventas", table, role="gerente")
```

`PayrollAssistant.load()` se memoiza en `securedb/csv_requests.py` y `securedb/local_agent.py`
para no recargar los pesos en cada interacción.

### 4.4 Requisitos de sistema

- Python 3.11+.
- CPU únicamente (no requiere GPU).
- Inferencia por instrucción: milisegundos; el CSV grande (2.800 filas) se procesa en segundos.

---

## 5. Entrenamiento

### 5.1 Datos sintéticos

`datagen.py` genera oraciones en español con variantes de:

- registro: formal, informal, imperativo, con prefijos ("por favor", "necesito que", "urgente:");
- ruido: minúsculas, mayúsculas, sin tildes, errores tipográficos (swap/delete/duplicate/substitute);
- números: `3200000`, `3.200.000`, `3,200,000`, `$ 1.500,75`, palabras ("tres millones");
- entidades: personas, departamentos, cargos, ciudades, IDs, emails, fechas y mensajes;
- alcances: todos, departamento, persona, ID, cargo, condición numérica y combinaciones.

**Separación train/test por entidades, no por frases**: el 25 % de los nombres, apellidos,
departamentos, cargos y ciudades se reserva para test (`split_list`). Así se mide generalización y
no memorización.

### 5.2 Hiperparámetros del modelo de instrucciones

| Parámetro | Intención | Entidades |
|---|---|---|
| Datos de entrenamiento | 60.000 | 60.000 |
| Datos de prueba | 4.000 | 4.000 |
| Épocas | 10 | 10 |
| Batch | 64 | 48 |
| Learning rate | 3e-3 | 2e-3 (decae ×0.5 cada 4 épocas) |
| Dropout de IDs / palabras→UNK | 8 % | 8 % (25 % en tokens con entidad) |
| Peso de clase | — | 2,0 para entidades, 1,0 para `O` |
| Optimizador | Adam (β1=0,9; β2=0,999; ε=1e-8; clip=5,0) | Adam |
| Inicialización | He / normal(0,1) escalada | He / normal(0,1) escalada |

Distribución de intenciones en el generador (pesos relativos):
`select 14 · set 16 · increase 16 · decrease 11 · delete 9 · notify 5 ·
drop_table 2,2 · drop_database 1,5 · modify_audit 2,2 · alter_credentials 2,4 · unknown 3`.

### 5.3 Hiperparámetros del mapeador de columnas

| Parámetro | Valor |
|---|---|
| Datos de entrenamiento | 40.000 columnas sintéticas |
| Datos de prueba | 3.000 |
| Épocas | 14 |
| Batch | 64 |
| Learning rate | 3e-3 |
| Dropout de IDs | 5 % |
| Encabezados genéricos forzados | 12 % + 15 % de ejemplos `other` |
| Valores presentes | 80 % de los ejemplos |

### 5.4 Reentrenar

```powershell
python -m payroll_ai.train_instructions          # ~30–40 min en CPU de 2 núcleos
python -m payroll_ai.columns                     # ~7 min
python -m payroll_ai.train_instructions --quick  # prueba rápida
python -m payroll_ai.columns --quick
```

Los pesos se guardan en `payroll_ai/weights/`.

---

## 6. Métricas reales (medidas sobre los pesos incluidos)

Todas las cifras siguientes se obtuvieron **cargando los `.npz` del repositorio** y evaluando sobre
un conjunto de prueba generado con semilla independiente (`seed=2` para instrucciones; `seed=99`
para columnas). No son cifras del entrenamiento ni estimaciones.

### 6.1 Modelo de instrucciones

| Métrica | Valor |
|---|---|
| Exactitud de intención | **0,9968 (99,68 %)** |
| F1 de spans (exacto: etiqueta + límites) | **0,9354 (93,54 %)** |
| Precisión de spans | **0,9341 (93,41 %)** |
| Recall de spans | **0,9367 (93,67 %)** |

**F1 por entidad:**

| Entidad | F1 | Entidad | F1 |
|---|---|---|---|
| `AMT_PCT` | **1,000** | `FIELD` | 0,979 |
| `CVAL` | **1,000** | `AMT_ABS` | 0,920 |
| `CCOL` | **1,000** | `VALUE` | 0,905 |
| `CLT` | 0,997 | `FVAL` | **0,832** |
| `CLE` | **1,000** | `MSG` | 0,992 |
| `CGT` | 0,996 | `ALL` | 0,994 |
| `CGE` | **1,000** | `FCOL` | 0,993 |

La entidad más débil es `FVAL` (valores de filtro: nombres y departamentos), porque el conjunto de
test usa entidades que no aparecen en entrenamiento. Es una medida **honesta de generalización**.

### 6.2 Mapeador de columnas

| Escenario | Exactitud |
|---|---|
| Test estándar (encabezados/valores nuevos) | **0,9950 (99,50 %)** |
| Encabezados **nunca vistos**, con valores | **0,9970 (99,70 %)** |
| Encabezados **nunca vistos**, solo texto | **0,9104 (91,04 %)** |
| **Sin encabezado**, solo valores | **0,9375 (93,75 %)** |

### 6.3 Dataset real de ejemplo (2.800 filas)

Sobre `payroll_ai/examples/nomina_2800_desordenado.csv`:

| Verificación | Resultado |
|---|---|
| Filas detectadas | **2.800** (+ fila de totales preservada) |
| Columnas detectadas | **12/12** con confianza 100 % |
| `SAL_BASE` | → `salary` |
| `ÁREA` | → `department` |
| `APELLIDOS` | → `last_name` |
| `NOMBRES` | → `first_name` |
| `PUESTO` | → `position` |
| `CORREO` | → `email` |
| `SEDE` | → `city` |
| `F_INGRESO` | → `hire_date` |
| `BONIFICACIÓN` | → `bonus` |
| `DEDUCCIONES` | → `deductions` |
| `CÓDIGO` | → `employee_id` |
| `SITUACIÓN` | → `status` |
| "sube un 10% el salario a los de Ventas" (admin) | **ALLOW**, 339 filas, decisión del modelo con confianza 100 % |
| Misma instrucción como `gerente` | **NEEDS_APPROVAL** |
| "muestra el salario de todos" como `usuario` | **NEEDS_APPROVAL**, valores ocultos |
| "borra la tabla de empleados" como admin | **BLOCKED** |

### 6.4 Pruebas automatizadas

```powershell
python -m unittest tests.test_payroll_ai   # 24 tests, OK
python -m pytest -q                        # 89 tests, OK
```

Cobertura funcional de `tests/test_payroll_ai.py`:

- números y palabras numéricas (`3.200.000`, `diez por ciento`, `dos millones`);
- CSV con `;`, títulos, totales, sin encabezado y round-trip;
- formato numérico preservado al escribir;
- esquema en español e inglés;
- aumento a un empleado y aumento masivo con confirmación;
- `NEEDS_APPROVAL` para gerente y ocultamiento de valores para usuario;
- lectura no sensible permitida a usuario;
- edición de cargo por operador;
- operaciones prohibidas bloqueadas incluso para administrador;
- `CLARIFY` sin alcance, con nombre ambiguo, valor inexistente y small talk;
- detección de `TAMPER_PAYLOAD` y de plan caducado;
- condiciones numéricas y números en palabras;
- CSV sin encabezado con `overrides`;
- 15 permutaciones de columnas/filas y ambos delimitadores;
- flujo de solicitud/aprobación sin auto-aprobación;
- 2.800 filas en ambos formatos.

---

## 7. Política determinista (no la decide el modelo)

### 7.1 Roles

```text
usuario(0) < operador(1) < gerente(2) = supervisor(2) < administrador(3) = admin(3)
```

### 7.2 Riesgo y autoridad mínima

| Operación | Riesgo | Autoridad mínima |
|---|---|---|
| Consulta no sensible | 0 | cualquiera |
| Consulta de salario (1 fila) | 1 | operador |
| Consulta masiva de salario (>10 filas) | 2 | gerente |
| Modificar 1 dato no financiero | 1 | operador |
| Modificar varios datos no financieros | 2 | gerente |
| Modificar 1 salario/bono | 3 | administrador |
| Modificar varios salarios/bonos | 4 | administrador + confirmación |
| Ajuste porcentual > 20 % | +1 | — |
| Eliminar filas | 4 | administrador + confirmación |
| `drop_table`, `drop_database`, `modify_audit`, `alter_credentials` | Prohibido | **nadie** |

### 7.3 Salvaguardas

- Sin alcance explícito ("a todos", "a los de X") → `CLARIFY`, nunca asume "todos".
- Nombre ambiguo (varias coincidencias) → `CLARIFY` y pide ID.
- El plan lleva `payload_hash`: si se altera antes de ejecutar → `TAMPER_PAYLOAD`.
- El plan lleva huella del archivo: si el CSV cambia → plan caducado.
- El CSV original nunca se modifica en memoria.
- Los valores financieros se ocultan en la vista previa si el rol no alcanza.
- Ningún usuario aprueba su propia solicitud de CSV.

---

## 8. Cumplimiento del marco SecureDB

| Requisito del informe | Implementación | Estado |
|---|---|---|
| Clasificación de riesgo 0–4 + prohibido | `config.py`, `risk_engine.py` | ✅ |
| Tres niveles de aprobación | `operador`, `supervisor`, `administrador` | ✅ |
| No delegación de autoridad | `approval.can_act()` | ✅ |
| Comentario obligatorio ≥ 10 caracteres | `approval._validate_comment()` | ✅ |
| Timeouts 24/8/4/2 h | `config.TIMEOUT_HOURS` | ✅ |
| Confirmación adicional en riesgo 4 | `approval.confirm_execution()` | ✅ |
| Auditoría inmutable con hash chain | `audit.py` + triggers SQLite | ✅ |
| Detección de alteración del log | `audit.verify_chain()` | ✅ |
| Registro universal de interacciones | `audit.log_event()` en todos los flujos | ✅ |
| Incidentes de seguridad | `audit.register_incident()` | ✅ |
| El modelo solo interpreta | `engine.py` separa interpretación de política | ✅ |
| Sin APIs externas por defecto | `LLM_PROVIDER=local` | ✅ |

---

## 9. Límites conocidos (honestos)

1. **Idioma**: instrucciones en español. Los encabezados de CSV se entienden también en inglés.
2. **Una operación por instrucción**: no procesa "sube 5 % y cambia el cargo…".
3. **Datos sintéticos**: las métricas altas son sobre distribución del generador. Frases muy distintas
   a las plantillas pueden caer en `CLARIFY`; el sistema prefiere preguntar antes que adivinar.
4. **Sin encabezado**: columnas de texto parecidas (área vs. cargo vs. ciudad) pueden confundirse; se
   corrige con `overrides={indice: "concepto"}`.
5. **Filas de totales**: se preservan, pero no se recalculan (se avisa en `warnings`).
6. **Condiciones compuestas**: no soporta "o" ni referencias por posición de fila.
7. **FVAL** es la entidad con menor F1 (0,832) por la variedad de nombres y departamentos no vistos.

---

## 10. Guía paso a paso para probar el modelo

Esta secuencia permite verificar el modelo desde cero, sin necesidad del core bancario ni de APIs
externas. Todos los comandos se ejecutan en la **raíz del proyecto** (`PayProof-main`).

### Paso 0 — Requisitos previos

- Windows con PowerShell (los comandos también funcionan en Linux/macOS cambiando la activación).
- Python 3.11 o superior.
- Estar dentro de la carpeta que contiene `app.py`.

```powershell
cd "E:\FDSI\Proyecto_2\PayProof-con-modelo-propio (3)\PayProof-main"
dir app.py
```

**Resultado esperado:** se lista `app.py`.

### Paso 1 — Crear el entorno e instalar dependencias (solo la primera vez)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Si PowerShell bloquea la activación:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.venv\Scripts\Activate.ps1
```

**Resultado esperado:** el prompt muestra `(.venv)` y la instalación termina sin errores.

### Paso 2 — Verificar que el modelo carga y que los 24 tests pasan

```powershell
python -m unittest tests.test_payroll_ai
```

**Resultado esperado:**

```text
Ran 24 tests in ...s

OK
```

Si falla con `No module named numpy`, el entorno no está activado o falta el Paso 1.

### Paso 3 — Inspeccionar cómo entiende las columnas del CSV

```powershell
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --schema
```

**Resultado esperado:** 12 columnas con su concepto y confianza. Ejemplos:

```text
  [7] 'SAL_BASE'   -> salary      (100%)
  [5] 'ÁREA'       -> department  (100%)
  [11] 'NOMBRES'   -> first_name  (100%)
  [0] 'APELLIDOS'  -> last_name   (100%)
```

### Paso 4 — Probar el rol administrador (debe permitir)

```powershell
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador "sube un 10% el salario a los de Ventas"
```

**Resultado esperado:**

- `Decisión : ALLOW`
- `Operación : increase (confianza del modelo 100%)`
- `Columna : SAL_BASE (salary)`
- `Filas : 339`
- `Riesgo : Crítico -> mínimo 'administrador' (requiere confirmación explícita)`
- Lista de cambios `antes -> después` (por ejemplo `$ 5.850.000 -> $ 6.435.000`).

### Paso 5 — Probar el rol gerente (debe pedir aprobación)

```powershell
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role gerente "sube un 10% el salario a los de Ventas"
```

**Resultado esperado:**

- `Decisión : NEEDS_APPROVAL`
- `Detalle : Requiere aprobación de un 'administrador' o superior.`
- `(valores ocultos hasta que un rol autorizado apruebe)`

### Paso 6 — Probar el rol usuario (debe pedir aprobación y ocultar valores)

```powershell
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role usuario "muestra el salario de todos"
```

**Resultado esperado:**

- `Decisión : NEEDS_APPROVAL`
- `Detalle : Requiere aprobación de un 'gerente' o superior.`
- No se muestran salarios.

### Paso 7 — Probar una operación prohibida (debe bloquear incluso al administrador)

```powershell
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador "borra la tabla de empleados"
```

**Resultado esperado:**

- `Decisión : BLOCKED`
- `Detalle : Operación prohibida por política: bloqueo inmediato, sin excepción ni aprobación posible.`
- `Operación : drop_table`

### Paso 8 — Aplicar el cambio y guardar el CSV

```powershell
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador --confirm --apply salida.csv "sube un 10% el salario a los de Ventas"
```

**Resultado esperado:**

- `-> 339 celda(s) modificadas en 'SAL_BASE'.`
- `Guardado en salida.csv`
- Al abrir `salida.csv`, **solo** cambiaron los salarios de Ventas; las demás filas y la fila de
  totales se conservan.

### Paso 9 — Probar instrucciones adicionales

```powershell
# Aumento absoluto a una persona
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador "aumentale 200 mil al sueldo de Juan Pérez"

# Condición numérica (el modelo entiende "dos millones" como 2000000)
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador "sube 8% el salario a los que ganen menos de 2 millones"

# Cambio de un dato no financiero
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role operador "cambia el cargo de Ana Torres a Gerente"

# Sin alcance: el sistema debe preguntar, no asumir "todos"
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador "sube el sueldo 10%"

# Nombre ambiguo: el sistema debe pedir el id
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador "aumenta el sueldo de Juan"
```

**Resultados esperados:**

- Las tres primeras terminan en `ALLOW` (la de operador con riesgo bajo).
- "sube el sueldo 10%" termina en `CLARIFY` pidiendo el alcance.
- "aumenta el sueldo de Juan" termina en `CLARIFY` si hay más de un Juan.

### Paso 10 — Probar la app web (opcional)

```powershell
streamlit run app.py
```

Abrir **`http://localhost:8501`** en el mismo equipo (no usar la IP de red para subir archivos).

1. Entrar con `admin` / `Admin123!`.
2. Abrir la pestaña **Nómina CSV**.
3. Adjuntar `payroll_ai/examples/nomina_2800_desordenado.csv`.
4. Desplegar **"Cómo entendí las columnas"** y revisar el mapeo.
5. Escribir `sube un 10% el salario a los de Ventas` y pulsar **Analizar**.
6. Marcar la confirmación y pulsar **Ejecutar**.
7. Usar **Descargar CSV editado** y comparar con el original.
8. Cerrar sesión y entrar como `supervisor` / `Supervisor123!`; repetir la instrucción y pulsar
   **Enviar a aprobación**.
9. Volver a entrar como `admin`, abrir **Solicitudes de CSV**, aprobar con confirmación.
10. Entrar de nuevo como `supervisor` y descargar el resultado.
11. Revisar la pestaña **Log de auditoría**: todas las acciones deben estar registradas.

### Paso 11 — Verificar las métricas del modelo incluido

```powershell
.venv\Scripts\python.exe -c "
from payroll_ai.datagen import build_dataset
from payroll_ai.model import InstructionModel
from payroll_ai.train_instructions import evaluate
m = InstructionModel.load(r'payroll_ai/weights/instructions')
print(evaluate(m, build_dataset(4000, 'test', seed=2)))
"
```

**Resultado esperado (aproximado):**

```text
intent_acc ≈ 0.9968
span_f1    ≈ 0.9354
span_prec  ≈ 0.9341
span_rec   ≈ 0.9367
```

### Paso 12 — Prueba completa del proyecto (opcional)

```powershell
python -m pytest -q
```

**Resultado esperado:** `89 passed`.

### Solución de problemas rápidos

| Síntoma | Causa probable | Solución |
|---|---|---|
| `No module named payroll_ai` | No está en la raíz del proyecto | `cd` a `PayProof-main` |
| `No module named numpy` / `streamlit` | Entorno no activado | `.venv\Scripts\Activate.ps1` |
| Streamlit muestra "Deploy this app" | `app.py` falló al importar | Ejecutar con `--logger.level=debug` |
| El navegador pide subir desde el celular | Se accedió por IP de red | Usar `http://localhost:8501` |
| Error al instalar `webauthn` | Dependencia opcional de biometría | Quitarla de `requirements.txt`; `BIOMETRIC_PROVIDER=mock` |

---

## 11. Reproducir las métricas

```powershell
# 1) Entorno
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2) Pruebas del modelo
python -m unittest tests.test_payroll_ai

# 3) Esquema del dataset grande
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --schema

# 4) Demostración de roles
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador "sube un 10% el salario a los de Ventas"
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role gerente "sube un 10% el salario a los de Ventas"
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role usuario "muestra el salario de todos"
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador "borra la tabla de empleados"

# 5) Aplicar y guardar
python -m payroll_ai payroll_ai/examples/nomina_2800_desordenado.csv --role administrador --confirm --apply salida.csv "sube un 10% el salario a los de Ventas"
```

Evaluación de métricas con los pesos incluidos:

```powershell
.venv\Scripts\python.exe -c "
from payroll_ai.datagen import build_dataset
from payroll_ai.model import InstructionModel
from payroll_ai.train_instructions import evaluate
m = InstructionModel.load(r'payroll_ai/weights/instructions')
print(evaluate(m, build_dataset(4000, 'test', seed=2)))
"
```

---

## 12. Mapa de archivos

```text
payroll_ai/
├── nn.py                     # MLP, TaggerNet, Adam (NumPy puro)
├── text.py                   # tokenización, números en español, rasgos
├── datagen.py                # generador de instrucciones etiquetadas
├── train_instructions.py     # entrenamiento + evaluación (intención y entidades)
├── model.py                  # InstructionModel (inferencia y persistencia)
├── columns.py                # ColumnMapper + datos sintéticos de columnas
├── csvio.py                  # Table, NumFormat, detección de estructura
├── engine.py                 # política, planes, ejecución, hashes
├── __main__.py               # CLI
├── weights/
│   ├── instructions/         # intent.npz, tagger.npz, vocab.json
│   └── columns/              # columns.npz
└── examples/                 # datasets de ejemplo
```

---

**Conclusión:** el modelo es propio y casero en el sentido técnico completo: no hay frameworks de
deep learning, no hay pesos preentrenados y no hay APIs. Es una implementación desde cero en NumPy,
entrenada con datos sintéticos, con métricas verificadas sobre los pesos entregados y con la
autorización separada en código determinista para que la IA nunca controle las decisiones sensibles.
