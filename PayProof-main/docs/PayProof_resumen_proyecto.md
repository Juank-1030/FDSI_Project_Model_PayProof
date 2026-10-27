# PayProof: resumen del proyecto

## Resumen para una diapositiva

**PayProof es una capa de control y auditoría para transacciones financieras sensibles.** Protege operaciones iniciadas por personas, agentes de IA, procesos RPA o APIs: intercepta la acción antes de que se ejecute, aplica políticas de riesgo y, si corresponde, solicita aprobación humana.

**Caso de uso inicial:** cambios de cuentas bancarias de proveedores y liberación de transferencias en un ERP, TMS o payment hub.

**Mecanismo:** el solicitante propone la operación; PayProof verifica sus datos y contexto; una política permite, deniega o retiene la transacción. Si queda retenida, se notifica al aprobador por WhatsApp. El aprobador entra a un portal seguro y autoriza la operación con una passkey desbloqueada mediante la biometría local del sistema operativo. Si no responde a tiempo, PayProof alerta a un supervisor y mantiene el pago bloqueado; **nunca lo aprueba automáticamente**.

**Cliente inicial recomendado:** empresas grandes con cuentas por pagar o tesorería centralizada. PayProof podría ampliarse a bancos y plataformas CORE mediante integraciones aprobadas con sus APIs y eventos transaccionales.

> **PayProof no mueve el dinero ni reemplaza al ERP, al banco o al CORE.** Controla y registra las acciones que pueden modificar o liberar una transacción.

## Cómo funcionaría

```text
Persona, agente de IA, RPA o API
              │
              ▼
PayProof: conector y control previo
              │
              ├─ Permitir por política
              ├─ Denegar
              └─ Retener para aprobación
                     │
                     ├─ WhatsApp: notificación al aprobador
                     ├─ Portal seguro: revisión de la operación
                     └─ Passkey: autenticación local del aprobador
              │
              ▼
Executor de PayProof
              │
              ▼
ERP / payment hub / CORE
              │
              ▼
Auditoría ───────────────► SIEM/SOAR
```

1. **PayProof recibe una solicitud de acción.** Puede originarse en un usuario, agente de IA, RPA o integración. Cada actor debe tener una identidad identificable; para un agente también se registra quién es su responsable humano.
2. **Normaliza y evalúa la operación.** Por ejemplo, identifica que la acción es `vendor.bank_account.change` o `payment.release`, y evalúa proveedor, importe, cambios recientes y otros atributos.
3. **Aplica una política.** Las operaciones rutinarias dentro de los límites podrían continuar automáticamente. Cambios de cuentas, beneficiarios nuevos o pagos de riesgo pueden requerir aprobación; operaciones prohibidas se deniegan.
4. **Retiene la operación si requiere revisión.** No se actualiza el ERP ni se transmite el pago mientras la solicitud está pendiente.
5. **Ejecuta solo después de una aprobación válida.** Un componente separado, con credenciales limitadas, ejecuta la acción exacta en el sistema protegido.
6. **Registra la decisión y el resultado.** La auditoría incluye quién inició la solicitud, agente y patrocinador humano, política aplicada, aprobador y estado final.

La aprobación debe quedar ligada a **la transacción exacta**. Si se modifica después el beneficiario, el importe u otro dato material, PayProof invalida la aprobación y solicita otra.

## WhatsApp y biometría

**WhatsApp sería el canal de notificación y escalamiento, no el sistema que valida la huella.** Meta ofrece APIs para enviar mensajes, botones y plantillas de autenticación, como las que envían códigos OTP; la verificación biométrica ocurre localmente en el teléfono, mediante las capacidades del sistema operativo. [Meta: mensajería][Meta: plantillas de autenticación]

El flujo recomendado es:

1. PayProof envía al aprobador un mensaje de WhatsApp con un identificador de solicitud y un enlace seguro. El mensaje no incluye datos bancarios completos ni información sensible.
2. El aprobador abre el portal de PayProof, revisa proveedor, importe y cambios propuestos.
3. El sistema operativo solicita huella, Face ID, PIN o el método disponible para desbloquear una **passkey WebAuthn**.
4. El dispositivo devuelve a PayProof una prueba criptográfica de aprobación, vinculada a esa solicitud. La huella o su plantilla biométrica **no se envía a WhatsApp ni a PayProof**. [W3C WebAuthn][FIDO Passkeys]
5. PayProof verifica identidad, rol, plazo y que la operación no haya cambiado. NIST recomienda verificación biométrica local y establece que la biometría no debe ser el autenticador por sí sola. [NIST SP 800-63B-4]

Si no hay una decisión dentro del plazo configurado, PayProof puede enviar un recordatorio y luego escalar por WhatsApp a un supervisor o aprobador suplente. Al llegar al vencimiento, la solicitud expira o permanece bloqueada según la política; **no se convierte en una aprobación automática**. WhatsApp notifica el flujo, pero la autorización final se hace en el portal seguro.

## Integración con el CORE

PayProof tendría dos formas de integración, que conviene presentar como capacidades distintas:

**Control preventivo antes de ejecutar:** PayProof se integra en el punto de escritura, por ejemplo, mediante un API gateway, un conector, un workflow preconfirmación del ERP o el control del archivo de pagos antes de transmitirlo al banco. Esta modalidad permite detener la acción antes de que se complete.

**Observación de transacciones del CORE:** PayProof recibe eventos o metadatos mediante APIs, webhooks, colas, event streams, logs de auditoría o CDC autorizado. Esto permite detectar y correlacionar operaciones, como un cambio de cuenta seguido por un pago. Si el evento llega después de que se comprometió la transacción, sirve para monitoreo e investigación, pero **no permite afirmar que PayProof la previno**.

Por eso, es más preciso decir que PayProof **ingesta eventos y metadatos transaccionales del CORE** que decir que “captura todo el tráfico”. La captura pasiva de paquetes no garantiza que se entiendan las transacciones ni que se puedan detener, especialmente si el tráfico está cifrado o usa protocolos propietarios. Para evitar bypass, deben identificarse y protegerse las rutas de escritura; un agente no puede conservar una credencial alternativa para escribir directamente en el sistema. El FFIEC destaca como controles relevantes la autenticación basada en riesgo, el doble control de ciertas transacciones y los logs de auditoría. [FFIEC]

## Evidencia de relevancia empresarial

- **Fraude de pagos:** la encuesta AFP 2026, basada en **465 profesionales corporativos**, informó que el **76%** de las organizaciones encuestadas sufrió intentos o casos reales de fraude de pagos en 2025. Es una encuesta empresarial general, no exclusiva de bancos. [AFP]
- **Impacto de una brecha financiera:** IBM reportó un costo medio de brecha de **USD 6,29 millones** en el sector financiero. El estudio analizó **602 organizaciones afectadas por brechas**, en 17 industrias y 16 países o regiones, entre marzo de 2025 y febrero de 2026. Esa cifra mide el costo medio de una brecha, no el costo de un fraude de pagos ni el ahorro atribuible a PayProof. [IBM]
- **Incidentes en finanzas y seguros:** Verizon DBIR 2026 registró **3.809 incidentes** y **1.300 divulgaciones confirmadas** para la categoría combinada de finanzas y seguros. No separa bancos de aseguradoras y se basa en casos recopilados por colaboradores del informe. [Verizon]
- **Agentes de IA:** en una encuesta CSA de enero de 2026, **418 profesionales de TI y seguridad** respondieron que el 65% de sus organizaciones tuvo al menos un incidente relacionado con agentes en los últimos 12 meses. Fue patrocinada por Token Security y es autodeclarada; no es una medición auditada del mercado financiero. [CSA]

Estas fuentes sustentan la existencia de riesgo, no la eficacia o el retorno de inversión de PayProof. Para comprobar el interés comercial habría que validar el problema con equipos de tesorería, AP, fraude y seguridad.

## Mercado y diferenciación

PayProof entraría en un mercado activo. Entre las alternativas actuales están:

- **Trustpair:** valida cuentas bancarias de proveedores, supervisa datos maestros y controla archivos de pago. [Trustpair]
- **Bottomline Paymode/Payments Hub:** ofrece validación de beneficiarios, controles de pago y workflows de aprobación. [Bottomline]
- **Kyriba Payments Fraud Detection**, junto con Eftsure: detecta pagos sospechosos y valida información bancaria. [Kyriba/Eftsure]
- **SAP, Oracle y Microsoft Dynamics:** tienen workflows de aprobación para determinadas modificaciones de proveedores y pagos. [SAP][Oracle][Dynamics]
- **Apono, Delinea/StrongDM y Zenity:** ofrecen controles para identidades y acciones de agentes, como aprobación, autorización en runtime o bloqueo. [Apono][Delinea/StrongDM][Zenity]

Por tanto, PayProof no debería presentarse como “un botón de aprobación” ni como un producto sin competidores. La **hipótesis de diferenciación** sería controlar la cadena completa *cambio de cuenta de proveedor → preparación y liberación del pago*, sin importar si la inició una persona o un agente, y hacer cumplir la política en el punto de ejecución a través de varios sistemas existentes. Trustpair, los workflows nativos del ERP y las plataformas de seguridad de agentes ya cubren partes importantes de esa cadena; habría que validar si las empresas perciben que la integración actual les deja una brecha y pagarían por cerrarla.

El modelo posible es SaaS empresarial por conectores y flujos protegidos, con opción híbrida o instalada en el entorno del cliente para sistemas financieros regulados. Una primera demo y producto piloto deberían enfocarse en un único flujo, no en todos los CORE y ERP.

## Demo universitaria

La demo puede simular un ERP, un payment hub y un CORE mediante APIs ficticias. No debe usar dinero, cuentas ni datos reales.

- Un agente recibe una factura de prueba e intenta cambiar la cuenta bancaria del proveedor y preparar un pago.
- PayProof detecta la acción, aplica una política y retiene el cambio y el pago.
- WhatsApp envía una notificación a la persona aprobadora y, si vence el plazo, escala al supervisor.
- El aprobador entra al portal, revisa los datos enmascarados y confirma con una passkey desbloqueada mediante el sistema operativo.
- El executor actualiza el sistema simulado solo si el payload coincide exactamente con el aprobado.
- Un stream de eventos simulado del CORE alimenta la auditoría y demuestra correlación de transacciones.
- Las pruebas muestran que no se puede autoaprobar, reutilizar una aprobación vencida, alterar el payload después de aprobar o llamar directamente a la API protegida.

Una pila posible es FastAPI, PostgreSQL, OPA/Cedar, Keycloak u OIDC, WebAuthn, API de WhatsApp Business para avisos, un agente con herramientas limitadas, APIs simuladas de ERP/pagos y un log append-only exportable a un SIEM.

## Fuentes

- [AFP, 2026 Payments Fraud and Control Survey](https://www.financialprofessionals.org/training-resources/resources/survey-research-economic-data/Details/payments-fraud). Muestra de 465 profesionales corporativos; datos de fraude de pagos de 2025.
- [IBM, Cost of a Data Breach Report 2026](https://www.ibm.com/downloads/documents/us-en/16dd90a741c46c2c). Muestra de 602 organizaciones afectadas por brechas.
- [Verizon, 2026 Data Breach Investigations Report](https://www.verizon.com/business/resources/reports/dbir/). Estadísticas agregadas y por industria.
- [Cloud Security Alliance, Autonomous but Not Controlled, 2026](https://cloudsecurityalliance.org/press-releases/2026/04/21/new-cloud-security-alliance-survey-reveals-82-of-enterprises-have-unknown-ai-agents-in-their-environments). Encuesta n=418 patrocinada por Token Security.
- [Meta, WhatsApp Cloud API: envío de mensajes](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages) y [plantillas de autenticación](https://developers.facebook.com/documentation/business-messaging/whatsapp/templates/authentication-templates/authentication-templates).
- [W3C WebAuthn](https://www.w3.org/TR/webauthn-3), [FIDO Passkeys](https://fidoalliance.org/passkeys) y [NIST SP 800-63B-4](https://pages.nist.gov/800-63-4/sp800-63b.html), sobre passkeys y verificación biométrica local.
- [FFIEC, Authentication and Access to Financial Institution Services and Systems](https://www.ffiec.gov/press/PDF/Authentication-and-Access-to-Financial-Institution-Services-and-Systems.pdf).
- [Trustpair](https://trustpair.com/p2p-teams), [Bottomline](https://www.bottomline.com/resources/prevent-payment-errors-and-fraud-payee-management), [Kyriba](https://www.kyriba.com/resources/fact-sheets/payments-fraud-detection), [SAP](https://help.sap.com/docs/SAP_S4HANA_CLOUD/b8e68a90bfff4ff0906d5db5342bdece/69cfb731c11541efb72599165b03f8c5.html), [Oracle](https://docs.oracle.com/en/cloud/saas/procurement/26c/oapro/internal-supplier-profile-bank-account-change.html), [Apono](https://docs.apono.io/docs/agent-privilege-guard/apono-agent-privilege-guard), [Delinea/StrongDM](https://delinea.com/news/delinea-delivers-runtime-authorization-for-ai-agents) y [Zenity](https://zenity.io/platform/runtime-boundaries), para el mapa competitivo.
