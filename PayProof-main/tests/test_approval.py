from datetime import datetime, timedelta, timezone

from securedb import approval, config, database
from securedb.models import OperationProposal
from securedb.risk_engine import classify

COMMENT_OK = "aprobado porque es necesario"


def make(**kwargs):
    base = dict(
        operation_type="select",
        target="empleados",
        intent="operacion de prueba",
        records_affected=1,
        involves_financial=False,
        involves_personal=False,
        reversible=True,
        recipient_scope="individual",
        filters={},
        changes={},
        message="",
    )
    base.update(kwargs)
    return OperationProposal(**base)


def create(conn, proposal):
    return approval.create_request(conn, "usuario", proposal, classify(proposal))


def test_comment_required_on_approve(db_path):
    proposal = make(operation_type="update", changes={"cargo": "Gerente"}, filters={"id": 1})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        ok, msg = approval.approve(conn, request_id, "operador", config.ROLE_OPERATOR, "corto")
        assert not ok
        assert "comentario" in msg.lower()


def test_operator_cannot_approve_medium(db_path):
    proposal = make(operation_type="update", records_affected=5, filters={"departamento": "Ventas"})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        ok, msg = approval.approve(conn, request_id, "operador", config.ROLE_OPERATOR, COMMENT_OK)
        assert not ok
        assert "autoridad" in msg.lower()


def test_supervisor_cannot_approve_critical(db_path):
    proposal = make(operation_type="delete", records_affected=1, filters={"id": 1})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        ok, _ = approval.approve(conn, request_id, "supervisor", config.ROLE_SUPERVISOR, COMMENT_OK)
        assert not ok


def test_operator_approves_low_and_executes(db_path):
    proposal = make(operation_type="update", changes={"cargo": "Vendedor Senior"}, filters={"id": 1})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        ok, msg = approval.approve(conn, request_id, "operador", config.ROLE_OPERATOR, COMMENT_OK)
        assert ok
        row = conn.execute("SELECT cargo FROM employees WHERE id = 1").fetchone()
        assert row["cargo"] == "Vendedor Senior"


def test_critical_requires_double_confirmation(db_path):
    proposal = make(operation_type="update", records_affected=3, involves_financial=True, changes={"salario": 5000}, filters={"departamento": "Ventas"})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        ok, msg = approval.approve(conn, request_id, "admin", config.ROLE_ADMIN, COMMENT_OK)
        assert ok
        assert "confirmación adicional" in msg.lower()
        row = conn.execute("SELECT status FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
        assert row["status"] == approval.STATUS_PENDING_CONFIRMATION
        ok2, msg2 = approval.confirm_execution(conn, request_id, "admin", COMMENT_OK)
        assert ok2
        row = conn.execute("SELECT status FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
        assert row["status"] == approval.STATUS_EXECUTED
        salaries = conn.execute("SELECT salario FROM employees WHERE departamento_id = 1").fetchall()
        assert all(s["salario"] == 5000 for s in salaries)


def test_confirmation_requires_same_admin(db_path):
    proposal = make(operation_type="delete", records_affected=1, filters={"id": 1})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        approval.approve(conn, request_id, "admin", config.ROLE_ADMIN, COMMENT_OK)
        ok, msg = approval.confirm_execution(conn, request_id, "admin2", COMMENT_OK)
        assert not ok
        assert "mismo administrador" in msg.lower()


def test_reject_requires_comment(db_path):
    proposal = make(operation_type="update", changes={"cargo": "X"}, filters={"id": 1})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        ok, msg = approval.reject(conn, request_id, "operador", config.ROLE_OPERATOR, "muy corto")
        assert not ok


def test_expiry(db_path):
    proposal = make(operation_type="update", changes={"cargo": "X"}, filters={"id": 1})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn.execute("UPDATE approval_requests SET expires_at = ? WHERE id = ?", (past, request_id))
        expired = approval.expire_requests(conn)
        assert request_id in expired
        row = conn.execute("SELECT status FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
        assert row["status"] == approval.STATUS_EXPIRED


def test_executor_update_without_filters_rejected(db_path):
    proposal = make(operation_type="update", changes={"cargo": "X"})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        ok, msg = approval.approve(conn, request_id, "admin", config.ROLE_ADMIN, COMMENT_OK)
        assert not ok


def test_delete_executes(db_path):
    proposal = make(operation_type="delete", records_affected=1, filters={"id": 12})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        ok, _ = approval.approve(conn, request_id, "admin", config.ROLE_ADMIN, COMMENT_OK)
        assert ok
        ok2, _ = approval.confirm_execution(conn, request_id, "admin", COMMENT_OK)
        assert ok2
        assert conn.execute("SELECT COUNT(*) AS c FROM employees WHERE id = 12").fetchone()["c"] == 0


def test_all_decisions_audited(db_path):
    proposal = make(operation_type="update", changes={"cargo": "X"}, filters={"id": 1})
    with database.get_conn() as conn:
        request_id = create(conn, proposal)
        approval.approve(conn, request_id, "operador", config.ROLE_OPERATOR, COMMENT_OK)
        events = [e["event_type"] for e in audit_events(conn)]
        assert "SOLICITUD_APROBADA" in events
        assert "EJECUCION_APROBADA" in events


def audit_events(conn):
    from securedb import audit

    return audit.get_events(conn)
