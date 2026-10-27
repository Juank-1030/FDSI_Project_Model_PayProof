# Changelog

Todas las versiones notables de PayProof se documentan en este archivo.
Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/) y versionado semántico.

## [Sin publicar]

### Añadido
- `payroll_ai`: modelo propio (entrenado desde cero con numpy, sin APIs) que interpreta instrucciones de nómina en español sobre CSV de estructura arbitraria; permisos por rol en un motor determinista.
- `securedb/local_agent.py` y `LLM_PROVIDER=local` (nuevo valor por defecto) como reemplazo de Groq/Ollama.
- El ejecutor admite ajustes relativos de salario (`{"pct": 10}` / `{"abs": 200000}`).

## [0.2.1] - 2026-10-20

### Corregido
- Componente WebAuthn de la página de aprobación: `challenge`, `allowCredentials[].id`, `excludeCredentials[].id` y `user.id` se convierten de base64url a bytes antes de llamar `navigator.credentials` (antes el navegador rechazaba la verificación con passkey real).
- Dependencia `py-webauthn>=2.0` → `webauthn>=2.0` en `requirements.txt` (el paquete fue renombrado; CI no podía instalarlo).

## [0.2.0] - 2026-10-19

### Agregado
- Canal de notificaciones con providers intercambiables: `mock` (demo), `evolution` (Evolution API, WhatsApp Web) y `whatsapp_cloud` (API oficial de Meta).
- Deep link de aprobación firmado (HMAC-SHA256): liga `request_id + payload_hash + expiración`; el payload no puede alterarse sin invalidar el enlace.
- Página de aprobación por enlace (`?view=aprobar&id=&token=`): muestra el payload completo y su hash antes de aprobar.
- Aprobación biométrica híbrida: proveedor `mock` (demo) y `webauthn` (passkeys FIDO2 reales con `py_webauthn`; la biometría se valida en el dispositivo y firma un challenge ligado al `payload_hash`).
- Pestaña "Notificaciones" en la UI (log de envíos por canal).
- Escalamiento automático al expirar una solicitud: se notifica al supervisor; nunca se auto-aprueba.
- Registro de passkeys para aprobadores (primera vez).

### Seguridad
- Columna `approval_method` en las solicitudes: toda aprobación por enlace queda auditada como `biometrico`.
- Intentos de aprobación con token inválido se registran en auditoría (`TOKEN_APROBACION_INVALIDO`).
- Operaciones críticas exigen doble verificación biométrica.

### Tests
- 65 tests automatizados (tokens, canales de notificación, flujo biométrico completo, escalamiento, autoridad).

## [0.1.0] - 2026-10-19

### Seguridad
- Registro de proveedores con hash SHA-256 de la cuenta normalizada (detección de cambio de cuenta).
- `HASH_MISMATCH`: pagos a cuentas alteradas o proveedores desconocidos se clasifican como riesgo CRÍTICO con incidente.
- Aprobaciones ligadas al payload: `payload_hash` guardado al crear la solicitud; alteraciones post-creación se bloquean (`TAMPER_PAYLOAD`).
- Verificación final en el ejecutor: recalcula el hash del beneficiario antes de ejecutar; un aprobador equivocado no puede liberar una cuenta no registrada.
- Sin auto-aprobación: expiración por nivel de riesgo y doble confirmación para operaciones críticas.
- Secretos movidos a `.env` (Groq API key y credenciales del core) con `.gitignore`.

### Integración con el core bancario
- Cliente HTTP del core FinAegis (`core_client.py`): login, cuentas y `POST /api/v2/transfers` reales.
- Header `X-Channel: payproof` en toda llamada: el tráfico de PayProof aparece como canal propio en Prometheus/Grafana.

### Funcionalidad
- Nueva pestaña "Proveedores y pagos": registro de proveedores, solicitud de pagos, simulación de cuenta alterada (demo BEC) y pagos ejecutados.
- El agente LLM interpreta órdenes de pago en lenguaje natural.

### Tests
- 51 tests automatizados (vendors, riesgo de pagos, flujo con core mockeado, binding de payload).
- CI con GitHub Actions (pytest en cada push y PR).

## Tipos de cambios
- **Agregado** para nuevas funciones.
- **Cambiado** para cambios en funcionalidades existentes.
- **Eliminado** para funciones removidas.
- **Corregido** para arreglos de bugs.
- **Seguridad** para mejoras de seguridad.
