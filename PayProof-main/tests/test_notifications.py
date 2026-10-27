from securedb import config, database, notifier


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_mock_notify_registra_fila(db_path):
    with database.get_conn() as conn:
        notifier.notify(conn, notifier.TIPO_PAGO_PENDIENTE, "admin", "mensaje de prueba", meta="http://x")
        rows = notifier.list_notifications(conn)
        assert len(rows) == 1
        assert rows[0]["canal"] == "mock"
        assert rows[0]["tipo"] == notifier.TIPO_PAGO_PENDIENTE
        assert rows[0]["meta"] == "http://x"


def test_evolution_envia_payload_correcto(db_path, monkeypatch):
    capturas = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        capturas["url"] = url
        capturas["json"] = json
        capturas["headers"] = headers
        return FakeResponse(200)

    monkeypatch.setattr(notifier.httpx, "post", fake_post)
    monkeypatch.setattr(config, "EVOLUTION_BASE_URL", "http://localhost:8080")
    monkeypatch.setattr(config, "EVOLUTION_API_KEY", "clave-evo")
    monkeypatch.setattr(config, "EVOLUTION_INSTANCE", "payproof")

    provider = notifier.EvolutionNotifier()
    ok = provider.send("573001234567", "hola desde el test")
    assert ok is True
    assert capturas["url"] == "http://localhost:8080/message/sendText/payproof"
    assert capturas["json"] == {"number": "573001234567", "text": "hola desde el test"}
    assert capturas["headers"] == {"apikey": "clave-evo"}


def test_whatsapp_cloud_envia_a_graph(db_path, monkeypatch):
    capturas = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        capturas["url"] = url
        capturas["json"] = json
        capturas["headers"] = headers
        return FakeResponse(201)

    monkeypatch.setattr(notifier.httpx, "post", fake_post)
    monkeypatch.setattr(config, "WHATSAPP_TOKEN", "token-meta")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_ID", "phone-id")

    provider = notifier.WhatsAppCloudNotifier()
    ok = provider.send("573001234567", "pago pendiente")
    assert ok is True
    assert "graph.facebook.com" in capturas["url"]
    assert capturas["json"]["to"] == "573001234567"
    assert capturas["headers"]["Authorization"] == "Bearer token-meta"


def test_provider_con_error_no_rompe_el_flujo(db_path, monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResponse(500, "error interno")

    monkeypatch.setattr(notifier.httpx, "post", fake_post)
    monkeypatch.setattr(config, "NOTIFY_PROVIDER", "evolution")
    notifier._notifier_instance = None
    try:
        with database.get_conn() as conn:
            notifier.notify(conn, notifier.TIPO_RESULTADO, "573001234567", "fallará el envío")
            rows = notifier.list_notifications(conn)
            assert len(rows) == 1
            assert "error" in rows[0]["canal"]
    finally:
        notifier._notifier_instance = None
        monkeypatch.setattr(config, "NOTIFY_PROVIDER", "mock")
