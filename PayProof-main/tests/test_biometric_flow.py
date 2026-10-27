import json

from securedb import approval, auth, config, database, executor, notifier, risk_engine
from securedb.models import OperationProposal

VENDOR = ("Papeleria El Sol", "Banco Andino", "ES91 2100 1234 5100 1234 5678")


def make_pay(cuenta=None, monto=100.0):
    return OperationProposal(
        operation_type="pay",
        target="pagos",
        intent="pago de prueba biométrico",
        involves_financial=True,
        vendor_name=VENDOR[0],
        bank_code=VENDOR[1],
        account_number=cuenta if cuenta is not None else VENDOR[2],
        amount=monto,
        currency="EUR",
        message="factura demo",
    )


class FakeCore:
    def get_source_account_uuid(self):
        return "cuenta-origen"

    def get_vendor_account_uuid(self):
        return "cuenta-destino"

    def create_transfer(self, from_account_uuid, to_account_uuid, amount, asset_code="EUR", description=""):
        return {"transfer_uuid": "tx-biometrico", "status": "pending"}


def setup_users():
    auth.ensure_demo_users()


def test_flujo_completo_con_notificacion_y_biometria(db_path, monkeypatch):
    monkeypatch.setattr(executor, "_core_client_instance", FakeCore())
    setup_users()
    proposal = make_pay()
    with database.get_conn() as conn:
        request_id = approval.create_request(conn, "usuario", proposal, risk_engine.classify(proposal))

        # notificación pendiente con deep link
        pendientes = [
            n for n in notifier.list_notifications(conn) if n["tipo"] == notifier.TIPO_PAGO_PENDIENTE
        ]
        assert len(pendientes) == 1
        assert "view=aprobar" in pendientes[0]["meta"]
        token = pendientes[0]["meta"].split("token=")[1]

        ok, msg = approval.approve_biometric(conn, request_id, token, {"actor": "admin"})
        assert ok, msg
        row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
        assert row["status"] == approval.STATUS_EXECUTED
        assert row["approval_method"] == "biometrico"
        assert row["decided_by"] == "admin"

        resultados = [
            n for n in notifier.list_notifications(conn) if n["tipo"] == notifier.TIPO_RESULTADO
        ]
        assert any("biometr" in n["mensaje"].lower() for n in resultados)
        assert conn.execute("SELECT COUNT(*) AS c FROM payments").fetchone()["c"] == 1


def test_token_invalido_no_aprueba(db_path, monkeypatch):
    monkeypatch.setattr(executor, "_core_client_instance", FakeCore())
    setup_users()
    proposal = make_pay()
    with database.get_conn() as conn:
        request_id = approval.create_request(conn, "usuario", proposal, risk_engine.classify(proposal))
        ok, msg = approval.approve_biometric(conn, request_id, "token.falso.firma", {"actor": "admin"})
        assert not ok
        row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
        assert row["status"] == approval.STATUS_PENDING


def test_operacion_critica_requiere_doble_biometria(db_path, monkeypatch):
    monkeypatch.setattr(executor, "_core_client_instance", FakeCore())
    setup_users()
    proposal = make_pay(cuenta=VENDOR[2][:-4] + "9999")
    with database.get_conn() as conn:
        request_id = approval.create_request(conn, "usuario", proposal, risk_engine.classify(proposal))
        token = [n["meta"] for n in notifier.list_notifications(conn) if n["tipo"] == notifier.TIPO_PAGO_PENDIENTE][
            0
        ].split("token=")[1]

        ok, msg = approval.approve_biometric(conn, request_id, token, {"actor": "admin"})
        assert ok
        assert "segunda confirmación" in msg.lower()
        row = conn.execute("SELECT * FROM approval_requests WHERE id = ?", (request_id,)).fetchone()
        assert row["status"] == approval.STATUS_PENDING_CONFIRMATION

        # la segunda confirmación ejecuta... y el ejecutor BLOQUEA el HASH_MISMATCH
        ok2, msg2 = approval.approve_biometric(conn, request_id, token, {"actor": "admin"})
        assert not ok2
        assert "HASH_MISMATCH" in msg2


def test_expiracion_escala_al_supervisor(db_path, monkeypatch):
    monkeypatch.setattr(executor, "_core_client_instance", FakeCore())
    setup_users()
    proposal = make_pay()
    with database.get_conn() as conn:
        request_id = approval.create_request(conn, "usuario", proposal, risk_engine.classify(proposal))
        conn.execute("UPDATE approval_requests SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?", (request_id,))
        expired = approval.expire_requests(conn)
        assert request_id in expired
        escalamientos = [
            n for n in notifier.list_notifications(conn) if n["tipo"] == notifier.TIPO_ESCALAMIENTO
        ]
        assert len(escalamientos) == 1
        assert "nunca se auto-aprueba" in escalamientos[0]["mensaje"]


def test_aprobador_sin_autoridad_rechazado(db_path, monkeypatch):
    monkeypatch.setattr(executor, "_core_client_instance", FakeCore())
    setup_users()
    proposal = make_pay()
    with database.get_conn() as conn:
        request_id = approval.create_request(conn, "usuario", proposal, risk_engine.classify(proposal))
        token = [n["meta"] for n in notifier.list_notifications(conn) if n["tipo"] == notifier.TIPO_PAGO_PENDIENTE][
            0
        ].split("token=")[1]
        ok, msg = approval.approve_biometric(conn, request_id, token, {"actor": "usuario"})
        assert not ok
        assert "autoridad" in msg.lower()
