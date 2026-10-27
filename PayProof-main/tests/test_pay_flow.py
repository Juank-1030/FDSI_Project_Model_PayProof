import json

from securedb import approval, config, database, executor, risk_engine, vendors
from securedb.models import OperationProposal

COMMENT_OK = "aprobado porque el pago es legítimo"

VENDOR = ("Papeleria El Sol", "Banco Andino", "ES91 2100 1234 5100 1234 5678")


def make_pay(cuenta=None, monto=100.0, nombre=None, banco=None):
    return OperationProposal(
        operation_type="pay",
        target="pagos",
        intent="pago de prueba",
        involves_financial=True,
        vendor_name=nombre or VENDOR[0],
        bank_code=banco or VENDOR[1],
        account_number=cuenta if cuenta is not None else VENDOR[2],
        amount=monto,
        currency="EUR",
        message="factura demo",
    )


class FakeCore:
    def __init__(self, fail=False):
        self.fail = fail
        self.transfers = []

    def get_source_account_uuid(self):
        return "cuenta-origen"

    def get_vendor_account_uuid(self):
        return "cuenta-destino"

    def create_transfer(self, from_account_uuid, to_account_uuid, amount, asset_code="EUR", description=""):
        if self.fail:
            raise ValueError("core caido")
        self.transfers.append(
            {
                "from": from_account_uuid,
                "to": to_account_uuid,
                "amount": amount,
                "asset_code": asset_code,
            }
        )
        return {"transfer_uuid": f"tx-{len(self.transfers)}", "status": "pending"}


# ---------------------------------------------------------------- riesgo


def test_pay_cuenta_registrada_riesgo_alto(db_path):
    a = risk_engine.classify(make_pay())
    assert a.level == config.RISK_HIGH
    assert a.requires_approval
    assert not a.requires_confirmation
    assert any("verificado" in r.lower() for r in a.reasons)


def test_pay_cuenta_alterada_riesgo_critico(db_path):
    a = risk_engine.classify(make_pay(cuenta=VENDOR[2][:-4] + "9999"))
    assert a.level == config.RISK_CRITICAL
    assert a.requires_confirmation
    assert any("HASH_MISMATCH" in r for r in a.reasons)


def test_pay_proveedor_desconocido_riesgo_critico(db_path):
    a = risk_engine.classify(make_pay(nombre="Fantasma SA"))
    assert a.level == config.RISK_CRITICAL
    assert any("HASH_MISMATCH" in r for r in a.reasons)


def test_pay_monto_elevado_critico(db_path):
    a = risk_engine.classify(make_pay(monto=5000.0))
    assert a.level == config.RISK_CRITICAL


# ---------------------------------------------------------------- flujo con core mockeado


def test_pago_aprobado_ejecuta_transferencia_en_core(db_path, monkeypatch):
    fake = FakeCore()
    monkeypatch.setattr(executor, "_core_client_instance", fake)
    proposal = make_pay()
    with database.get_conn() as conn:
        request_id = approval.create_request(conn, "usuario", proposal, risk_engine.classify(proposal))
        ok, msg = approval.approve(conn, request_id, "admin", config.ROLE_ADMIN, COMMENT_OK)
        assert ok, msg
        assert len(fake.transfers) == 1
        assert fake.transfers[0]["amount"] == 100.0
        row = conn.execute("SELECT * FROM payments WHERE vendor_name = ?", (VENDOR[0],)).fetchone()
        assert row is not None
        assert row["core_transfer_uuid"] == "tx-1"
        assert row["status"] == "ejecutado"


def test_pago_con_cuenta_alterada_no_se_ejecuta(db_path, monkeypatch):
    fake = FakeCore()
    monkeypatch.setattr(executor, "_core_client_instance", fake)
    proposal = make_pay(cuenta=VENDOR[2][:-4] + "9999")
    with database.get_conn() as conn:
        request_id = approval.create_request(conn, "usuario", proposal, risk_engine.classify(proposal))
        ok, _ = approval.approve(conn, request_id, "admin", config.ROLE_ADMIN, COMMENT_OK)
        assert ok
        ok2, msg2 = approval.confirm_execution(conn, request_id, "admin", COMMENT_OK)
        assert not ok2
        assert "HASH_MISMATCH" in msg2
        assert fake.transfers == []
        count = conn.execute("SELECT COUNT(*) AS c FROM payments").fetchone()["c"]
        assert count == 0


def test_payload_alterado_tras_creacion_se_bloquea(db_path, monkeypatch):
    fake = FakeCore()
    monkeypatch.setattr(executor, "_core_client_instance", fake)
    proposal = make_pay()
    with database.get_conn() as conn:
        request_id = approval.create_request(conn, "usuario", proposal, risk_engine.classify(proposal))
        alterado = proposal.model_dump()
        alterado["amount"] = 9999.0
        conn.execute(
            "UPDATE approval_requests SET proposal_json = ? WHERE id = ?",
            (json.dumps(alterado), request_id),
        )
        ok, msg = approval.approve(conn, request_id, "admin", config.ROLE_ADMIN, COMMENT_OK)
        assert not ok
        assert "alterado" in msg.lower()
        assert fake.transfers == []


def test_incidente_registrado_en_mismatch(db_path):
    from securedb import audit

    proposal = make_pay(cuenta=VENDOR[2][:-4] + "9999")
    with database.get_conn() as conn:
        assessment = risk_engine.classify(proposal)
        request_id = approval.create_request(conn, "usuario", proposal, assessment)
        audit.register_incident(
            conn,
            "HASH_MISMATCH_PROVEEDOR",
            f"Pago con cuenta alterada detectado (solicitud #{request_id}).",
        )
        incidents = audit.list_incidents(conn)
        assert any(i["tipo"] == "HASH_MISMATCH_PROVEEDOR" for i in incidents)


def test_verificacion_verdor_payload_hash_estable(db_path):
    p1 = make_pay()
    p2 = make_pay(monto=250.0)
    assert p1.payload_hash() != p2.payload_hash()
    assert p1.payload_hash() == make_pay().payload_hash()
