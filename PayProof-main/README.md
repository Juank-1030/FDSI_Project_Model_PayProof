# PayProof

![CI](https://github.com/Helacio/PayProof/actions/workflows/ci.yml/badge.svg)

**Capa Human-in-the-Loop para operaciones financieras sensibles.** PayProof intercepta pagos y cambios de beneficiario, aplica política determinista y exige aprobación humana **ligada al hash del payload exacto** antes de ejecutar contra el core bancario. El iniciador (persona, RPA o agente de IA) nunca tiene credenciales del core.

## El problema

Uno de los fraudes más caros en finanzas es el **cambio de cuenta bancaria de un proveedor** (BEC/APP fraud): el atacante no rompe el banco, convence al humano de actualizar el número de cuenta — o compromete al agente que inicia pagos — y el siguiente pago se va a su cuenta. Los controles actuales (un correo, una llamada, un workflow que aprueba "acciones") no vinculan la aprobación al contenido exacto del pago.

## Cómo funciona

```
Iniciador (persona/RPA/agente IA)
   │  orden de pago (proveedor, banco, cuenta, monto)
   ▼
PayProof ── riesgo determinista:  ¿hash de la cuenta == registro del proveedor?
   │        ├─ coincide  → riesgo ALTO, requiere aprobación humana
   │        └─ NO coincide → riesgo CRÍTICO HASH_MISMATCH (fraude BEC) + incidente
   ▼
Aprobación humana (payload_hash guardado al crear la solicitud)
   │        └─ payload alterado después de crear → bloqueo TAMPER_PAYLOAD
   ▼
Ejecutor ── recalcula hash del beneficiario + hash del payload
   │        └─ mismatch → NO ejecuta (defensa en profundidad)
   ▼
Core bancario FinAegis ── POST /api/v2/transfers (header X-Channel: payproof)
   ▼
Auditoría (cadena de hashes inmutable) + observabilidad (Prometheus/Grafana, canal "payproof")
```

- **Hash de proveedores**: cada proveedor registra su cuenta canónica; se guarda el SHA-256 de los datos normalizados. Cualquier dígito distinto en la cuenta entrante produce `HASH_MISMATCH`.
- **Aprobación ligada al payload**: la solicitud guarda el `payload_hash`; si alguien altera el payload entre creación y ejecución, la ejecución se bloquea.
- **Sin auto-aprobación**: las solicitudes expiran y escalan; jamás se aprueban por silencio.

## Requisitos

- Python 3.11+
- Agente de interpretación: **modelo propio** `payroll_ai` (entrenado desde cero, solo `numpy`, sin APIs; por defecto `LLM_PROVIDER=local`). Opcionalmente [Groq](https://groq.com) o [Ollama](https://ollama.com) (`LLM_PROVIDER=groq|ollama`). Ver [payroll_ai/README.md](payroll_ai/README.md)
- **Core bancario FinAegis corriendo** en `http://localhost:8000` (Docker): ver el repo del core y su guía de arranque
- Un usuario del core con al menos dos cuentas fondadas en EUR (el demo provee `demo.user@gcu.global`)

## Instalación

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env     # rellena GROQ_API_KEY y las credenciales del core
```

## Ejecución

```powershell
streamlit run app.py
```

O con `run.bat`. La app queda en http://localhost:8501

## Usuarios de demostración

| Usuario     | Contraseña      | Rol           |
|-------------|-----------------|---------------|
| admin       | Admin123!       | administrador |
| supervisor  | Supervisor123!  | supervisor    |
| operador    | Operador123!    | operador      |
| usuario     | Usuario123!     | usuario       |

## Demo: fraude BEC en 5 pasos

1. Entra como `admin` → pestaña **Proveedores y pagos**: los proveedores demo ya tienen su cuenta registrada con hash.
2. **Solicitar pago** (cuenta correcta) → riesgo Alto → aprueba en la **Cola de aprobación** → el pago llega al core (tabla "Pagos ejecutados" y Grafana, canal `payproof`).
3. **Simular cuenta alterada (fraude BEC)** → `HASH_MISMATCH` → riesgo Crítico, incidente en "Alertas de seguridad".
4. Aunque un aprobador la apruebe, el **ejecutor rechaza** la cuenta no registrada: el dinero no sale.
5. Para cambiar la cuenta legítimamente: admin → "Actualizar cuenta de un proveedor" (queda auditado e incidentado).

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Los 51 tests corren también en GitHub Actions (ver `.github/workflows/ci.yml`).

## Modelo propio para editar nómina (`payroll_ai`)

Interpreta instrucciones en español sobre CSV de cualquier estructura ("sube 10% el salario a los de Ventas")
con un modelo entrenado desde cero; los permisos por rol los decide el motor determinista. Funciona solo
(`python -m payroll_ai nomina.csv --role administrador "..."`) y como agente del chat de PayProof.

## Ramas

GitFlow ligero: `main` (estable, solo PRs y tags de release) · `develop` (integración) · `feature/*` · `hotfix/*`.

## Releases

Versionado semántico. Cada release sale de `main` con tag `vX.Y.Z` y notas generadas (ver [CHANGELOG.md](CHANGELOG.md)).

## Documentación

- [Arquitectura](docs/arquitectura.md)
- [Resumen del proyecto](docs/PayProof_resumen_proyecto.md)

## Notificaciones con WhatsApp (Evolution API, demo)

PayProof puede enviar las notificaciones de aprobación por WhatsApp real usando [Evolution API](https://github.com/EvolutionAPI/evolution-api) (protocolo de WhatsApp Web, **no oficial — solo demo**, con riesgo de ban del número).

```powershell
docker compose -f docker/evolution/docker-compose.yml up -d
```

1. Crea la instancia y vincula tu WhatsApp:
   - `POST /instance/create` con `{"instanceName":"payproof","integration":"WHATSAPP-BAILEYS","qrcode":false}`
   - `GET /instance/connect/payproof` → devuelve `code` y `base64` (QR en data-URL); escanéalo en **Ajustes → Dispositivos vinculados**.
2. En `.env`: `NOTIFY_PROVIDER=evolution`, `EVOLUTION_API_KEY=<la clave del compose>`, `EVOLUTION_INSTANCE=payproof` y `NOTIFY_APPROVER_NUMBER=<número real>`.
3. Cada solicitud de aprobación llega a ese WhatsApp con el deep link firmado.

Usa un número desechable. En producción el canal oficial es la Cloud API de Meta (`NOTIFY_PROVIDER=whatsapp_cloud`).

## Confidencialidad

Nunca commitees `.env`, `securedb.db` ni claves de API. El `.gitignore` ya los excluye; revisa `git status` antes de cada commit.
