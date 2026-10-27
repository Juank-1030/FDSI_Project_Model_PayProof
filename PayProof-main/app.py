import json
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from securedb import (
    agent,
    approval,
    approval_token,
    audit,
    auth,
    biometric,
    config,
    database,
    executor,
    notifier,
    risk_engine,
    vendors,
)
from securedb.models import OperationProposal
from payroll_ui import csv_tab

st.set_page_config(page_title="PayProof", layout="wide", page_icon="logo.png")

database.init_db()
auth.ensure_demo_users()

LOGO_PATH = "logo.png"
HEADER_LOGO_PATH = "static/logo-escuela.png"

st.markdown(
    """
    <style>
    :root {
        --bg: #ffffff;
        --surface: #ffffff;
        --surface2: #f4f4f5;
        --border: #d4d4d8;
        --accent: #2563eb;
        --accent-dim: #dbeafe;
        --green: #15803d;
        --green-dim: #dcfce7;
        --orange: #c2410c;
        --purple: #6b21a8;
        --red: #b91c1c;
        --text: #000000;
        --text-muted: #000000;
    }

    .stApp {
        background-color: var(--bg);
    }

    html, body, .stApp {
        color: var(--text);
    }

    .stApp h1, .stApp h2, .stApp h3, .stApp h4 {
        color: var(--text);
    }

    .stApp p, .stApp li, .stApp label, .stApp .stCaption, .stApp caption {
        color: var(--text-muted);
    }

    [data-testid="stSidebar"] {
        background-color: var(--surface);
        border-right: 1px solid var(--border);
    }

    [data-testid="stHeader"] {
        background-color: var(--bg);
    }

    .block-container {
        padding-top: 3rem;
    }

    .stButton > button[kind="primary"] {
        background-color: var(--green-dim);
        color: var(--text);
        border: 1px solid var(--green);
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #bbf7d0;
        border-color: var(--green);
        color: var(--text);
    }
    .stButton > button[kind="primary"]:focus {
        background-color: var(--green-dim);
        border-color: var(--green);
        color: var(--text);
    }
    .stButton > button[kind="secondary"] {
        background-color: var(--surface2);
        color: var(--text);
        border: 1px solid var(--border);
    }
    .stButton > button[kind="secondary"]:hover {
        border-color: var(--accent);
        color: var(--text);
    }
    .stButton > button[kind="secondary"]:active {
        background-color: var(--green-dim);
        border-color: var(--green);
        color: var(--text);
    }

    .stDownloadButton > button, .stFormSubmitButton > button {
        background-color: var(--surface2);
        color: var(--text);
        border: 1px solid var(--border);
    }

    [data-testid="stExpander"] {
        background-color: var(--surface);
        border: 1px solid var(--border);
        border-radius: 10px;
    }

    .stTabs [data-baseweb="tab-list"] {
        border-bottom: 1px solid var(--border);
    }
    .stTabs [data-baseweb="tab"] {
        color: var(--text-muted);
    }
    .stTabs [aria-selected="true"] {
        color: var(--text);
        border-bottom-color: var(--accent);
    }

    div[data-testid="stChatInput"] textarea {
        background-color: var(--surface);
        border: 1px solid var(--border);
        color: var(--text);
    }

    [data-testid="stDataFrame"] {
        background-color: var(--surface);
    }

    .stAlert [data-baseweb="notification"] {
        border-radius: 8px;
    }

    input, textarea {
        background-color: var(--surface);
        border: 1px solid var(--border);
        color: var(--text);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def fmt_ts(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def login_screen():
    title_col, logo_col = st.columns([5, 1])
    with title_col:
        st.title("PayProof")
        st.subheader("Marco de validación jerárquica para el control de acciones sensibles ejecutadas por agentes de IA")
    with logo_col:
        st.image(HEADER_LOGO_PATH, width=140)
    st.write("Inicie sesión con un usuario de demostración:")
    with st.form("login"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        submitted = st.form_submit_button("Ingresar")
    if submitted:
        user = auth.authenticate(username.strip(), password)
        if user:
            st.session_state["user"] = user
            with database.get_conn() as conn:
                audit.log_event(conn, user["username"], "LOGIN", f"Inicio de sesión del usuario {user['username']} con rol {user['role']}.")
            st.rerun()
        else:
            st.error("Credenciales incorrectas.")
    st.info("Usuarios demo: admin / Admin123!, supervisor / Supervisor123!, operador / Operador123!, usuario / Usuario123!")


def render_chat_messages():
    for msg in st.session_state.get("chat", []):
        if msg["kind"] == "dataframe":
            st.dataframe(msg["df"])
        else:
            with st.chat_message(msg["kind"]):
                st.write(msg["text"])


def chat_tab(user):
    render_chat_messages()
    instruction = st.chat_input("Escriba una instrucción en lenguaje natural...")
    if not instruction:
        return
    st.session_state.setdefault("chat", []).append({"kind": "user", "text": instruction})
    with st.spinner("Interpretando la instrucción con el agente..."):
        try:
            proposal = agent.extract_proposal(instruction)
        except Exception as exc:
            st.session_state["chat"].append({"kind": "assistant", "text": f"No fue posible interpretar la instrucción: {exc}"})
            st.rerun()
    assessment = risk_engine.classify(proposal)
    with database.get_conn() as conn:
        audit.log_event(
            conn,
            user["username"],
            "INSTRUCCION",
            instruction,
            risk_level=str(assessment.level),
            payload=proposal.model_dump_json(),
        )
        if assessment.prohibited:
            audit.register_incident(
                conn,
                "OPERACION_PROHIBIDA",
                f"El usuario {user['username']} intentó una operación prohibida: {proposal.intent}",
            )
            audit.log_event(
                conn,
                user["username"],
                "OPERACION_PROHIBIDA",
                f"Operación bloqueada sin excepción: {proposal.intent}",
                risk_level=config.RISK_PROHIBITED,
            )
            st.session_state["chat"].append(
                {
                    "kind": "assistant",
                    "text": f"Operación BLOQUEADA: esta acción está prohibida por política del sistema. Se registró como incidente de seguridad y se notificó al administrador. Detalle: {proposal.intent}",
                }
            )
        elif not assessment.requires_approval:
            result = executor.execute(conn, proposal)
            audit.log_event(
                conn,
                user["username"],
                "EJECUCION_AUTOMATICA",
                f"Operación de riesgo nulo ejecutada automáticamente: {result['summary']}",
                risk_level="0",
            )
            st.session_state["chat"].append(
                {"kind": "assistant", "text": f"Operación ejecutada automáticamente (nivel de riesgo 0). {result['summary']}"}
            )
            if result.get("rows"):
                st.session_state["chat"].append({"kind": "dataframe", "df": pd.DataFrame(result["rows"])})
        else:
            request_id = approval.create_request(conn, user["username"], proposal, assessment)
            audit.log_event(
                conn,
                user["username"],
                "SOLICITUD_CREADA",
                f"Solicitud de aprobación #{request_id} creada: {proposal.intent}",
                risk_level=str(assessment.level),
            )
            timeout = config.TIMEOUT_HOURS[assessment.level]
            st.session_state["chat"].append(
                {
                    "kind": "assistant",
                    "text": f"Solicitud de aprobación #{request_id} creada. Nivel de riesgo: {assessment.label}. Autoridad mínima: {assessment.min_authority}. Expira en {timeout} horas si no hay decisión.",
                }
            )
    st.rerun()


def queue_tab(user):
    role = user["role"]
    with database.get_conn() as conn:
        expired = approval.expire_requests(conn)
        if expired:
            st.warning(f"{len(expired)} solicitud(es) expiraron y fueron canceladas automáticamente.")
        pending = approval.list_pending(conn)
    if role == config.ROLE_USER:
        own = [r for r in pending if r["solicitante"] == user["username"]]
        header_col, btn_col = st.columns([4, 1])
        with header_col:
            st.subheader("Mis solicitudes")
        with btn_col:
            st.button("↻ Refrescar", key="refresh_own_queue", on_click=st.rerun, use_container_width=True)
        if not own:
            st.info("No tiene solicitudes pendientes.")
        for r in own:
            with st.expander(f"#{r['id']} | {r['intent']} | Riesgo {r['risk_level']} ({config.RISK_LABELS[r['risk_level']]}) | {r['status']}"):
                proposal = OperationProposal.model_validate_json(r["proposal_json"])
                st.json(proposal.model_dump())
                st.caption(f"Vence: {fmt_ts(r['expires_at'])}")
        return
    header_col, btn_col = st.columns([4, 1])
    with header_col:
        st.subheader("Cola de aprobación jerárquica")
    with btn_col:
        st.button("↻ Refrescar", key="refresh_queue", on_click=st.rerun, use_container_width=True)
    if not pending:
        st.info("No hay solicitudes pendientes.")
        return
    for r in pending:
        level = int(r["risk_level"])
        can = approval.can_act(role, level)
        with st.expander(f"#{r['id']} | {r['intent']} | Riesgo {level} ({config.RISK_LABELS[level]}) | {r['solicitante']} | {r['status']}"):
            proposal = OperationProposal.model_validate_json(r["proposal_json"])
            st.json(proposal.model_dump())
            st.caption(f"Creada: {fmt_ts(r['requested_at'])} | Vence: {fmt_ts(r['expires_at'])}")
            if r["status"] == approval.STATUS_PENDING:
                if can:
                    comment = st.text_area(
                        f"Comentario obligatorio (mínimo {config.MIN_COMMENT_LENGTH} caracteres)",
                        key=f"comment_{r['id']}",
                    )
                    col1, col2 = st.columns(2)
                    if col1.button("Aprobar", key=f"approve_{r['id']}", type="primary"):
                        with database.get_conn() as conn:
                            ok, msg = approval.approve(conn, r["id"], user["username"], role, comment)
                        if ok:
                            st.success(msg)
                        else:
                            st.error(msg)
                        st.rerun()
                    if col2.button("Rechazar", key=f"reject_{r['id']}"):
                        with database.get_conn() as conn:
                            ok, msg = approval.reject(conn, r["id"], user["username"], role, comment)
                        if ok:
                            st.success(msg)
                        else:
                            st.error(msg)
                        st.rerun()
                else:
                    st.caption("Su rol no tiene autoridad para decidir sobre esta solicitud (visibilidad de solo lectura).")
            elif r["status"] == approval.STATUS_PENDING_CONFIRMATION:
                if role == config.ROLE_ADMIN:
                    comment = st.text_area(
                        f"Comentario de confirmación (mínimo {config.MIN_COMMENT_LENGTH} caracteres)",
                        key=f"confirm_{r['id']}",
                    )
                    if st.button("Confirmar ejecución", key=f"confirmbtn_{r['id']}", type="primary"):
                        with database.get_conn() as conn:
                            ok, msg = approval.confirm_execution(conn, r["id"], user["username"], comment)
                        if ok:
                            st.success(msg)
                        else:
                            st.error(msg)
                        st.rerun()
                else:
                    st.caption("Aprobación del administrador registrada; falta la confirmación adicional del administrador.")


def log_tab(user):
    role = user["role"]
    with database.get_conn() as conn:
        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 4])
        with btn_col1:
            st.button("↻ Refrescar", key="refresh_log", on_click=st.rerun, use_container_width=True)
        with btn_col2:
            if st.button("Verificar integridad de la cadena de hashes", key="verify_chain"):
                ok, bad_id = audit.verify_chain(conn)
                audit.log_event(
                    conn,
                    user["username"],
                    "INTEGRIDAD_VERIFICADA",
                    f"Verificación de integridad del log: {'OK' if ok else 'FALLO en registro ' + str(bad_id)}",
                )
                if ok:
                    st.success("Cadena de hashes verificada: el log es íntegro.")
                else:
                    st.error(f"ALTERACIÓN DETECTADA en el registro #{bad_id}.")
        events = audit.get_events(conn)
    df = pd.DataFrame(events)
    if role == config.ROLE_ADMIN:
        event_types = sorted(df["event_type"].unique().tolist()) if not df.empty else []
        selected = st.selectbox("Filtrar por tipo de evento", ["Todos"] + event_types)
        if selected != "Todos":
            df = df[df["event_type"] == selected]
        st.download_button(
            "Exportar CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name="audit_log.csv",
            mime="text/csv",
        )
        if st.button("Simular intento de alteración del log"):
            with database.get_conn() as conn:
                result = audit.simulate_tamper(conn, user["username"])
            st.error(result)
            st.rerun()
    st.dataframe(df)


def alerts_tab(user):
    role = user["role"]
    if role == config.ROLE_OPERATOR:
        st.info("Su rol no tiene acceso a esta sección (visibilidad restringida por la matriz de permisos).")
        return
    with database.get_conn() as conn:
        incidents = audit.list_incidents(conn)
        if not incidents:
            st.info("No hay incidentes registrados.")
            return
        for inc in incidents:
            with st.expander(f"#{inc['id']} | {inc['tipo']} | {inc['estado']} | {fmt_ts(inc['timestamp'])}"):
                st.write(inc["descripcion"])
                if role == config.ROLE_ADMIN and inc["estado"] == "abierta":
                    if st.button("Marcar como gestionada", key=f"manage_{inc['id']}"):
                        with database.get_conn() as conn:
                            audit.manage_incident(conn, inc["id"], user["username"])
                        st.rerun()


def data_tab():
    with database.get_conn() as conn:
        employees = conn.execute(
            "SELECT e.id, e.nombre, e.apellido, e.email, d.nombre AS departamento, e.cargo, e.salario FROM employees e JOIN departments d ON e.departamento_id = d.id ORDER BY e.id"
        ).fetchall()
        departments = conn.execute("SELECT * FROM departments ORDER BY id").fetchall()
        notifications = conn.execute("SELECT * FROM notifications ORDER BY id DESC").fetchall()
    header_col, btn_col = st.columns([4, 1])
    with header_col:
        st.subheader("Base de datos simulada")
    with btn_col:
        st.button("↻ Refrescar", key="refresh_data", on_click=st.rerun, use_container_width=True)
    st.subheader("Empleados")
    st.dataframe(pd.DataFrame([dict(r) for r in employees]))
    st.subheader("Departamentos")
    st.dataframe(pd.DataFrame([dict(r) for r in departments]))
    st.subheader("Notificaciones")
    st.dataframe(pd.DataFrame([dict(r) for r in notifications]))


def _alterar_cuenta(cuenta: str) -> str:
    digits = [ch for ch in cuenta if ch.isdigit()]
    if len(digits) < 4:
        return cuenta + "0000"
    last4 = "".join(digits[-4:])
    nuevo = str((int(last4) + 1111) % 10000).zfill(4)
    if cuenta.endswith(last4):
        return cuenta[: -len(last4)] + nuevo
    return cuenta + nuevo


def _solicitar_pago(nombre, banco, cuenta, monto, divisa, motivo, actor):
    proposal = OperationProposal(
        operation_type="pay",
        target="pagos",
        intent=f"Pago de {monto} {divisa} a {nombre}",
        involves_financial=True,
        vendor_name=nombre,
        bank_code=banco,
        account_number=cuenta,
        amount=monto,
        currency=divisa,
        message=motivo,
    )
    assessment = risk_engine.classify(proposal)
    with database.get_conn() as conn:
        verification = vendors.verify_vendor(conn, nombre, banco, cuenta)
        request_id = approval.create_request(conn, actor, proposal, assessment)
        audit.log_event(
            conn,
            actor,
            "SOLICITUD_CREADA",
            f"Solicitud de aprobación #{request_id} creada: {proposal.intent}",
            risk_level=str(assessment.level),
            payload=proposal.model_dump_json(),
        )
        if not verification["match"]:
            audit.register_incident(
                conn,
                "HASH_MISMATCH_PROVEEDOR",
                f"Pago a una cuenta que NO coincide con el registro del proveedor '{nombre}' (solicitud #{request_id}).",
            )
    return proposal, assessment, verification, request_id


def notifications_tab(user):
    with database.get_conn() as conn:
        items = notifier.list_notifications(conn)
    header_col, btn_col = st.columns([4, 1])
    with header_col:
        st.subheader("Notificaciones enviadas")
    with btn_col:
        st.button("↻ Refrescar", key="refresh_notifications", on_click=st.rerun, use_container_width=True)
    st.caption(
        f"Canal activo: **{config.NOTIFY_PROVIDER}** · Aprobador: **{config.NOTIFY_APPROVER}** · "
        f"Base de enlaces: **{config.APPROVAL_LINK_BASE}**"
    )
    if not items:
        st.info("Aún no se han enviado notificaciones. Cree una solicitud de aprobación para generar una.")
        return
    df = pd.DataFrame(
        [
            {
                "id": n["id"],
                "tipo": n["tipo"],
                "destinatario": n["destinatario"],
                "mensaje": n["mensaje"],
                "canal": n["canal"],
                "fecha": fmt_ts(n["created_at"]),
            }
            for n in items
        ]
    )
    st.dataframe(df, use_container_width=True)
    for n in items[:5]:
        with st.expander(f"#{n['id']} | {n['tipo']} | {n['canal']} | {fmt_ts(n['created_at'])}"):
            st.write(n["mensaje"])
            if n.get("meta"):
                st.code(n["meta"], language=None)


def payments_tab(user):
    role = user["role"]
    with database.get_conn() as conn:
        vendors_list = vendors.list_vendors(conn)
        payments_rows = conn.execute("SELECT * FROM payments ORDER BY id DESC").fetchall()

    st.subheader("Proveedores registrados")
    if vendors_list:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "id": v["id"],
                        "proveedor": v["nombre"],
                        "banco": v["banco"],
                        "cuenta": v["cuenta"],
                        "hash (sha256)": v["hash"][:16] + "…",
                        "cuenta_core": v["core_account_uuid"] or "auto",
                        "estado": v["status"],
                    }
                    for v in vendors_list
                ]
            ),
            use_container_width=True,
        )
    else:
        st.info("No hay proveedores registrados.")

    if role == config.ROLE_ADMIN:
        with st.expander("Registrar proveedor nuevo"):
            with st.form("registrar_proveedor"):
                nombre = st.text_input("Nombre del proveedor")
                banco = st.text_input("Banco")
                cuenta = st.text_input("Cuenta / IBAN")
                if st.form_submit_button("Registrar proveedor"):
                    if not (nombre and banco and cuenta):
                        st.error("Todos los campos son obligatorios.")
                    else:
                        with database.get_conn() as conn:
                            vendors.register_vendor(conn, nombre, banco, cuenta)
                            audit.log_event(
                                conn,
                                user["username"],
                                "PROVEEDOR_REGISTRADO",
                                f"Proveedor '{nombre}' registrado con hash de cuenta.",
                            )
                        st.success("Proveedor registrado con el hash de su cuenta.")
                        st.rerun()
        with st.expander("Actualizar cuenta de un proveedor (cambio legítimo)"):
            opciones = {v["nombre"]: v["id"] for v in vendors_list}
            if opciones:
                with st.form("actualizar_proveedor"):
                    seleccionado = st.selectbox("Proveedor", list(opciones.keys()))
                    nueva_cuenta = st.text_input("Nueva cuenta / IBAN")
                    if st.form_submit_button("Actualizar cuenta"):
                        if not nueva_cuenta:
                            st.error("La nueva cuenta es obligatoria.")
                        else:
                            with database.get_conn() as conn:
                                vendor = vendors.get_vendor(conn, opciones[seleccionado])
                                nuevo_hash = vendors.update_vendor_account(
                                    conn, opciones[seleccionado], vendor["banco"], nueva_cuenta, user["username"]
                                )
                                audit.log_event(
                                    conn,
                                    user["username"],
                                    "CAMBIO_CUENTA_PROVEEDOR",
                                    f"Cuenta del proveedor '{seleccionado}' actualizada. Nuevo hash: {nuevo_hash[:16]}…",
                                )
                                audit.register_incident(
                                    conn,
                                    "CAMBIO_CUENTA_PROVEEDOR",
                                    f"El administrador {user['username']} actualizó la cuenta del proveedor '{seleccionado}'.",
                                )
                            st.success("Cuenta actualizada (registrado en auditoría e incidentes).")
                            st.rerun()

    st.divider()
    st.subheader("Solicitar pago")
    opciones = {v["nombre"]: v for v in vendors_list}
    if not opciones:
        st.info("Registre un proveedor para poder solicitar pagos.")
        return
    seleccionado = st.selectbox("Proveedor beneficiario", list(opciones.keys()))
    vendor = opciones[seleccionado]
    col1, col2 = st.columns(2)
    with col1:
        nombre = st.text_input("Nombre", value=vendor["nombre"])
        banco = st.text_input("Banco", value=vendor["banco"])
    with col2:
        cuenta = st.text_input("Cuenta / IBAN", value=vendor["cuenta"])
        monto = st.number_input("Monto", min_value=0.01, value=100.0, step=10.0)
    divisa = st.selectbox("Divisa", ["EUR", "USD", "GBP"], index=0)
    motivo = st.text_input("Motivo / descripción", value="Pago de factura")
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        boton_pagar = st.button("Solicitar pago", type="primary", use_container_width=True)
    with btn_col2:
        boton_alterado = st.button("Simular cuenta alterada (fraude BEC)", use_container_width=True)

    if boton_alterado:
        cuenta = _alterar_cuenta(cuenta)
        st.warning(f"Cuenta simulada por el atacante: {cuenta}")

    if boton_pagar or boton_alterado:
        proposal, assessment, verification, request_id = _solicitar_pago(
            nombre, banco, cuenta, float(monto), divisa, motivo, user["username"]
        )
        if verification["match"]:
            st.success(
                f"Beneficiario VERIFICADO: el hash de la cuenta coincide con el registro. "
                f"Riesgo: {assessment.label}. Solicitud de aprobación #{request_id} creada."
            )
        else:
            st.error(
                f"HASH_MISMATCH: la cuenta no coincide con la registrada para '{nombre}'. "
                f"Riesgo: {assessment.label}. Solicitud #{request_id} retenida para aprobación humana e incidente registrado."
            )
        st.caption(f"Hash entrante: {verification['incoming_hash'][:16]}…")
        st.caption(f"Hash registrado: {(verification['registered_hash'] or '—')[:16]}")
        st.info("Revise la cola de aprobación: el pago NO se ejecuta sin aprobación humana.")

    st.divider()
    st.subheader("Pagos ejecutados (core FinAegis)")
    if payments_rows:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "id": p["id"],
                        "proveedor": p["vendor_name"],
                        "cuenta": p["account_number"],
                        "monto": p["amount"],
                        "divisa": p["currency"],
                        "hash_ok": bool(p["vendor_hash_match"]),
                        "transferencia_core": p["core_transfer_uuid"],
                        "estado": p["status"],
                    }
                    for p in payments_rows
                ]
            ),
            use_container_width=True,
        )
    else:
        st.info("Aún no hay pagos ejecutados.")


def _webauthn_component(credential_js: str, mode: str, height: int = 320):
    """Embebe JS en el navegador para solicitar una credencial WebAuthn.

    mode: 'create' (registro de passkey) o 'get' (firma de aprobación).
    El resultado llega de vuelta como el valor de retorno del componente.
    """
    credential_call = "create" if mode == "create" else "get"
    if mode == "get":
        response_fields = (
            "authenticatorData: b64(cred.response.authenticatorData),\n"
            "signature: b64(cred.response.signature),\n"
            "userHandle: cred.response.userHandle ? b64(cred.response.userHandle) : null,\n"
        )
    else:
        response_fields = (
            "attestationObject: b64(cred.response.attestationObject),\n"
            "transports: cred.response.getTransports ? cred.response.getTransports() : [],\n"
        )
    html = """
    <script>
    function b64(buf) {{
        let binary = '';
        const bytes = new Uint8Array(buf);
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        return btoa(binary);
    }}
    function b64urlToBytes(s) {{
        const pad = '='.repeat((4 - (s.length % 4)) % 4);
        const raw = atob((s || '').replace(/-/g, '+').replace(/_/g, '/') + pad);
        const out = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
        return out.buffer;
    }}
    async function main() {{
        try {{
            const options = {options};
            options.challenge = b64urlToBytes(options.challenge);
            if (options.allowCredentials) {{
                options.allowCredentials.forEach(c => {{ c.id = b64urlToBytes(c.id); }});
            }}
            if (options.excludeCredentials) {{
                options.excludeCredentials.forEach(c => {{ c.id = b64urlToBytes(c.id); }});
            }}
            if (options.user && options.user.id) {{
                options.user.id = b64urlToBytes(options.user.id);
            }}
            const cred = await navigator.credentials.{call}({{publicKey: options}});
            const json = {{
                rawId: b64(cred.rawId),
                type: cred.type,
                clientExtensionResults: cred.getClientExtensionResults(),
                response: {{
                    clientDataJSON: b64(cred.response.clientDataJSON),
                    {response_fields}
                }}
            }};
            window.parent.postMessage({{type: "streamlit:setComponentValue", value: JSON.stringify(json)}}, "*");
        }} catch (e) {{
            window.parent.postMessage({{type: "streamlit:setComponentValue", value: JSON.stringify({{__error__: String(e)}})}}, "*");
        }}
    }}
    main();
    </script>
    """.format(options=credential_js, call=credential_call, response_fields=response_fields)
    return components.html(html, height=height)


def render_approval_page():
    st.set_page_config(page_title="PayProof - Aprobación", layout="centered")
    st.title("Aprobación de operación")

    raw_id = st.query_params.get("id")
    token = st.query_params.get("token")
    if not raw_id or not token:
        st.error("Enlace de aprobación incompleto (faltan id o token).")
        return
    try:
        request_id = int(raw_id)
    except ValueError:
        st.error("El id de la solicitud es inválido.")
        return

    with database.get_conn() as conn:
        row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()

    if row is None:
        st.error("La solicitud no existe.")
        return

    try:
        proposal = OperationProposal.model_validate_json(row["proposal_json"])
    except Exception:
        st.error("El payload de la solicitud no se pudo interpretar.")
        return

    stored_hash = row["payload_hash"] or proposal.payload_hash()
    _, token_error = approval_token.verify_token(token, stored_hash)
    if token_error:
        st.error(token_error)
        return

    st.subheader(f"Solicitud #{request_id}")
    st.write(f"**Intención:** {row['intent']}")
    st.write(f"**Riesgo:** {row['risk_level']} - {config.RISK_LABELS.get(row['risk_level'], '?')}")
    st.write(f"**Estado:** {row['status']}")
    st.write(f"**Hash del payload (lo que se va a aprobar):** `{stored_hash[:24]}…`")
    with st.expander("Ver payload completo"):
        st.json(proposal.model_dump())

    if row["status"] not in (approval.STATUS_PENDING, approval.STATUS_PENDING_CONFIRMATION):
        st.info(f"Esta solicitud ya fue resuelta (estado: {row['status']}).")
        return

    provider_name = config.BIOMETRIC_PROVIDER.lower()

    if provider_name == "webauthn":
        username = st.text_input("Usuario aprobador", value=config.NOTIFY_APPROVER)
        with database.get_conn() as conn:
            has_passkey = (
                conn.execute(
                    "SELECT COUNT(*) AS c FROM passkeys WHERE username = ?", (username,)
                ).fetchone()["c"]
                > 0
            )
        if not has_passkey:
            st.warning("Este usuario no tiene passkey registrada. Regístrela primero en este dispositivo.")
            if st.button("Registrar passkey en este dispositivo"):
                with st.spinner("Preparando registro de passkey..."):
                    try:
                        opts, challenge = biometric.get_provider().register_begin(username)
                        result = _webauthn_component(opts, mode="create")
                        if result and "__error__" not in result:
                            biometric.get_provider().register_finish(username, result, challenge)
                            st.success("Passkey registrada correctamente.")
                            st.rerun()
                        elif result:
                            st.error(result["__error__"])
                    except Exception as exc:
                        st.error(f"Registro fallido: {exc}")
        else:
            if st.button("Aprobar con biometría (passkey)"):
                with st.spinner("Solicitando verificación biométrica..."):
                    try:
                        opts, challenge = biometric.get_provider().auth_options(
                            request_id, stored_hash, username
                        )
                        st.session_state["auth_challenge"] = challenge
                        result = _webauthn_component(opts, mode="get")
                        challenge = st.session_state.pop("auth_challenge", challenge)
                        if result and "__error__" not in result:
                            with database.get_conn() as conn:
                                ok, msg = approval.approve_biometric(
                                    conn, request_id, token, result, challenge_b64=challenge
                                )
                            if ok:
                                st.success(msg)
                            else:
                                st.error(msg)
                        elif result:
                            st.error(result["__error__"])
                    except Exception as exc:
                        st.error(f"Verificación fallida: {exc}")
    else:
        st.caption("Biometría simulada (demo): el dispositivo valida localmente y firma el challenge.")
        if st.button("Validar con biometría (demo)", type="primary"):
            proof = {"actor": config.NOTIFY_APPROVER}
            with database.get_conn() as conn:
                ok, msg = approval.approve_biometric(conn, request_id, token, proof)
            if ok:
                st.success(msg)
            else:
                st.error(msg)

    st.caption("Recuerde: la aprobación queda ligada al hash mostrado. Cualquier cambio en el payload invalida el enlace.")


if st.query_params.get("view") == "aprobar":
    render_approval_page()
    st.stop()

if "user" not in st.session_state:
    login_screen()
    st.stop()

user = st.session_state["user"]
role = user["role"]

with st.sidebar:
    st.write(f"**{user['username']}**")
    st.caption(f"Rol: {role}")
    st.caption(f"Nivel de jerarquía: {config.ROLE_LEVEL[role]}")
    st.divider()
    st.caption("Roles disponibles: usuario (solicitante), operador (N1), supervisor (N2), administrador (N3)")
    st.divider()
    if st.button("Cerrar sesión"):
        with database.get_conn() as conn:
            audit.log_event(conn, user["username"], "LOGOUT", f"Cierre de sesión del usuario {user['username']}.")
        st.session_state.pop("user")
        st.session_state.pop("chat", None)
        st.rerun()

title_col, logo_col = st.columns([6, 1])
with title_col:
    st.title("PayProof")
    st.caption("Clasificar antes de ejecutar, aprobar según nivel, registrar siempre, bloquear lo prohibido.")
with logo_col:
    st.image(HEADER_LOGO_PATH, width=140)

tabs = st.tabs(["Nómina CSV", "Chat con el agente", "Proveedores y pagos", "Notificaciones", "Cola de aprobación", "Log de auditoría", "Alertas de seguridad", "Base de datos simulada"])

with tabs[0]:
    csv_tab(user)
with tabs[1]:
    chat_tab(user)
with tabs[2]:
    payments_tab(user)
with tabs[3]:
    notifications_tab(user)
with tabs[4]:
    queue_tab(user)
with tabs[5]:
    log_tab(user)
with tabs[6]:
    alerts_tab(user)
with tabs[7]:
    data_tab()
