"""Pestaña de Streamlit: adjuntar un CSV de nómina (cualquier orden de columnas/filas) y editarlo con lenguaje natural."""
import json

import pandas as pd
import streamlit as st

from payroll_ai.columns import CONCEPTS
from payroll_ai.csvio import Table
from payroll_ai.engine import fingerprint
from securedb import audit, csv_requests, database

DECISION_TXT = {
    "ALLOW": "Permitido para tu rol",
    "NEEDS_APPROVAL": "Requiere aprobación de un rol superior",
    "BLOCKED": "BLOQUEADO por política",
    "CLARIFY": "Necesito más información",
    "ERROR": "No se puede ejecutar",
}


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _df(table: Table) -> pd.DataFrame:
    return pd.DataFrame(table.rows, columns=table.header)


def csv_tab(user):
    bot = csv_requests.bot()
    st.subheader("Editar nómina desde un CSV")
    st.caption("Adjunta cualquier CSV: no importa el orden de columnas ni de filas, ni el separador. "
               f"Tu rol: **{user['role']}**. Lo que puedes hacer lo decide el motor de permisos, no el modelo.")

    up = st.file_uploader("CSV de nómina", type=["csv", "txt", "tsv"], key="csv_up")
    if up is not None:
        key = f"{up.name}:{up.size}"
        if st.session_state.get("csv_key") != key:
            st.session_state.update(csv_key=key, csv_text=_decode(up.getvalue()), csv_name=up.name, csv_overrides={})
    if "csv_text" not in st.session_state:
        st.info("Adjunta un archivo para empezar.")
        _requests_section(user)
        return

    table = Table.from_text(st.session_state["csv_text"])
    overrides = st.session_state.get("csv_overrides", {})
    schema = bot.analyze(table, overrides)

    with st.expander("Cómo entendí las columnas (corrígelo si hace falta)", expanded=not table.has_header):
        if not table.has_header:
            st.warning("El archivo no tiene encabezado: deduje las columnas solo por sus valores. Revisa y corrige.")
        new = {}
        cols = st.columns(min(len(schema.cols), 4) or 1)
        for i, c in enumerate(schema.cols):
            with cols[i % len(cols)]:
                sel = st.selectbox(f"{c.header} ({c.conf:.0%})", CONCEPTS, index=CONCEPTS.index(c.concept), key=f"ov_{key_of(table)}_{i}")
                if sel != c.concept:
                    new[i] = sel
        if new != {k: v for k, v in overrides.items()}:
            st.session_state["csv_overrides"] = {**overrides, **new}
            overrides = st.session_state["csv_overrides"]

    st.dataframe(_df(table), use_container_width=True, height=260)
    st.caption(f"{len(table.rows)} filas · {len(table.header)} columnas · separador {table.delimiter!r}")

    with st.form("csv_instr", clear_on_submit=False):
        instruction = st.text_input("Instrucción", placeholder="Ej.: sube un 10% el salario a los de Ventas")
        go = st.form_submit_button("Analizar")
    if go and instruction.strip():
        plan = bot.plan(instruction.strip(), table, user["role"], overrides)
        st.session_state["csv_plan"] = (plan, instruction.strip())
        with database.get_conn() as conn:
            audit.log_event(conn, user["username"], "CSV_INSTRUCCION", instruction.strip(),
                            risk_level=str(plan.risk_level), payload=json.dumps(plan.audit_record(), ensure_ascii=False))
            if plan.decision == "BLOCKED":
                audit.register_incident(conn, "OPERACION_PROHIBIDA",
                                        f"{user['username']} intentó una operación prohibida sobre un CSV: {instruction.strip()}")

    if "csv_plan" in st.session_state:
        plan, ins = st.session_state["csv_plan"]
        if plan.table_fingerprint != fingerprint(table):
            st.session_state.pop("csv_plan")      # el archivo cambió: plan caducado
        else:
            _show_plan(user, bot, table, plan, ins, overrides)
    _requests_section(user)


def key_of(table):
    return abs(hash(tuple(table.header))) % 10**6


def _show_plan(user, bot, table, plan, ins, overrides):
    box = {"ALLOW": st.success, "NEEDS_APPROVAL": st.warning, "BLOCKED": st.error}.get(plan.decision, st.info)
    box(f"**{DECISION_TXT.get(plan.decision, plan.decision)}**" + (f" — {plan.message}" if plan.message else ""))
    with st.expander("Detalle del plan", expanded=True):
        st.code(plan.explain(table), language="text")
    if plan.changes and plan.kind != "delete" and (plan.decision == "ALLOW" or plan.column_concept not in ("salary", "bonus", "deductions", "net_pay")):
        st.dataframe(pd.DataFrame([{"fila": c.row + 1, "id/nombre": " | ".join(table.rows[c.row][:3]),
                                    "antes": c.old, "después": c.new} for c in plan.changes]), use_container_width=True)

    if plan.decision == "ALLOW":
        confirm = True
        if plan.requires_confirmation:
            confirm = st.checkbox("Confirmo esta operación de riesgo crítico", key="csv_confirm")
        if st.button("Ejecutar", disabled=not confirm, type="primary"):
            res = bot.execute(plan, table, confirm=confirm)
            with database.get_conn() as conn:
                audit.log_event(conn, user["username"], "CSV_EJECUCION", res.message,
                                risk_level=str(plan.risk_level), payload=json.dumps(plan.audit_record(), ensure_ascii=False))
            if not res.ok:
                st.error(res.message)
            elif res.selected is not None:
                st.success(res.message)
                st.dataframe(_df(res.selected), use_container_width=True)
            else:
                st.session_state["csv_text"] = res.table.to_text()
                st.session_state.pop("csv_plan", None)
                st.session_state["csv_flash"] = res.message
                st.rerun()
    elif plan.decision == "NEEDS_APPROVAL":
        if st.button("Enviar a aprobación"):
            with database.get_conn() as conn:
                rid = csv_requests.create(conn, user, ins, st.session_state["csv_name"], st.session_state["csv_text"], overrides, plan)
                audit.log_event(conn, user["username"], "CSV_SOLICITUD", f"Solicitud CSV #{rid} enviada a aprobación ({plan.min_role}).",
                                risk_level=str(plan.risk_level), payload=json.dumps(plan.audit_record(), ensure_ascii=False))
            st.success(f"Solicitud #{rid} creada. Un '{plan.min_role}' (distinto de ti) debe aprobarla.")

    if st.session_state.get("csv_flash"):
        st.success(st.session_state.pop("csv_flash"))


def _requests_section(user):
    if "csv_flash" in st.session_state and "csv_plan" not in st.session_state:
        st.success(st.session_state.pop("csv_flash"))
    if "csv_text" in st.session_state:
        st.download_button("Descargar CSV editado", st.session_state["csv_text"].encode("utf-8"),
                           file_name="editado_" + st.session_state.get("csv_name", "nomina.csv"), mime="text/csv")
    st.divider()
    st.subheader("Solicitudes de CSV")
    with database.get_conn() as conn:
        pend = csv_requests.pending_for(conn, user)
        mine = csv_requests.mine(conn, user)
    st.markdown("**Para aprobar**" if pend else "_No hay solicitudes para que apruebes._")
    for r in pend:
        with st.expander(f"#{r['id']} · {r['solicitante']} ({r['role']}) · {r['summary']}"):
            t, plan = csv_requests.preview(r)
            st.code(plan.explain(t), language="text")
            confirm = st.checkbox("Confirmo (riesgo crítico)", key=f"cc_{r['id']}") if plan.requires_confirmation else True
            c1, c2 = st.columns(2)
            for col, label, ok in ((c1, "Aprobar", True), (c2, "Rechazar", False)):
                if col.button(label, key=f"{label}_{r['id']}", disabled=ok and not confirm):
                    with database.get_conn() as conn:
                        done, msg = csv_requests.decide(conn, r["id"], user, ok, confirm)
                        audit.log_event(conn, user["username"], "CSV_APROBACION" if ok else "CSV_RECHAZO",
                                        f"Solicitud CSV #{r['id']}: {msg}", risk_level=str(plan.risk_level))
                    (st.success if done else st.error)(msg)
                    if done:
                        st.rerun()
    if mine:
        st.markdown("**Mis solicitudes**")
        for r in mine:
            st.write(f"#{r['id']} · {r['status']} · {r['instruction']}" + (f" · por {r['decided_by']}" if r["decided_by"] else ""))
            if r["status"] == "aprobada" and r["result_text"]:
                st.download_button(f"Descargar resultado #{r['id']}", r["result_text"].encode("utf-8"),
                                   file_name=f"resultado_{r['id']}_{r['filename']}", mime="text/csv", key=f"dl_{r['id']}")
