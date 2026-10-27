# Arquitectura de PayProof

## Componentes

```
┌─────────────┐   interpreta   ┌──────────────┐   clasifica   ┌───────────────┐
│  Iniciador  │ ─────────────► │    agente    │ ────────────► │  risk_engine  │
│ persona/RPA │   orden NL     │  (LLM, groq/ │   propuesta   │  determinista │
│  /agente IA │                │    ollama)   │               │  + hash provee│
└─────────────┘                └──────────────┘               └───────┬───────┘
                                                                      │ requiere aprobación
                                                           ┌──────────▼──────────┐
                                                           │      approval       │
                                                           │ cola jerárquica +   │
                                                           │ payload_hash binding│
                                                           └──────────┬──────────┘
                                                                      │ aprobado
                                                           ┌──────────▼──────────┐
                                                           │      executor       │
                                                           │ verificación final  │
                                                           │ (hash beneficiario +│
                                                           │  hash del payload)  │
                                                           └──────────┬──────────┘
                                                                      │
                                              ┌───────────────────────▼────────┐
                                              │   core_client → FinAegis core  │
                                              │  POST /api/v2/transfers        │
                                              │  header X-Channel: payproof    │
                                              └──────────┬─────────────────────┘
                                                         │
                                             ┌───────────▼───────────┐
                                             │         audit         │
                                             │ cadena de hashes      │
                                             │ inmutable (sha256)    │
                                             └───────────────────────┘
```

## Módulos (`securedb/`)

| Módulo | Responsabilidad |
|---|---|
| `agent.py` | Interpreta lenguaje natural → `OperationProposal` (incluye pagos: proveedor, banco, cuenta, monto, divisa) |
| `risk_engine.py` | Clasificación determinista; para `pay` compara el hash del beneficiario contra el registro (`HASH_MISMATCH` → CRÍTICO) |
| `vendors.py` | Registro de proveedores: normalización (mayúsculas, sin espacios) + SHA-256 de `nombre|banco|cuenta` |
| `approval.py` | Cola jerárquica de aprobación con autoridad mínima por nivel, expiración, doble confirmación en críticos y verificación de `payload_hash` |
| `executor.py` | Ejecución como defensa en profundidad: recalcula el hash del beneficiario y del payload antes de ejecutar; los pagos salen al core |
| `core_client.py` | Cliente HTTP del core FinAegis: login, cuentas, transferencias; toda llamada lleva `X-Channel: payproof` |
| `audit.py` | Log inmutable (cadena SHA-256), verificación de integridad e incidentes |
| `database.py` | SQLite: usuarios, empleados, solicitudes, `vendors`, `payments` + migraciones ligeras |

## Garantías de seguridad

1. **Integridad del beneficiario**: el hash registrado del proveedor es la fuente de verdad; una cuenta alterada nunca coincide.
2. **Aprobación ligada al payload** (anti-TOCTOU): `payload_hash` se guarda al crear la solicitud y se verifica antes de ejecutar.
3. **Sin credenciales en el iniciador**: el agente no tiene token del core; solo el ejecutor de PayProof lo usa.
4. **Doble verificación en ejecución**: aunque un aprobador apruebe por error un `HASH_MISMATCH`, el ejecutor no paga cuentas no registradas.
5. **Sin auto-aprobación**: expiración + escalamiento; jamás aprobación por silencio.

## Observabilidad (con el core)

El header `X-Channel: payproof` permite al core atribuir el tráfico de PayProof en Prometheus/Grafana: las transferencias generadas por PayProof aparecen como canal `payproof` en el dashboard "Core Bancario – KPIs" (TPS, tasa de errores, latencia, volumen por canal).

## Próximas fases

- Notificación de aprobaciones vía canal de mensajería (simulado) con deep link a aprobación biométrica (WebAuthn/passkey en el dispositivo).
- Modo gateway: proxy selectivo para rutas sensibles del core.
- Escalamiento automático a supervisor por inactividad.
