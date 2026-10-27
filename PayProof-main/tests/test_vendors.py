from securedb import database, vendors


def test_hash_es_determinista():
    a = vendors.vendor_hash("Papeleria El Sol", "Banco Andino", "ES91 2100 1234 5100 1234 5678")
    b = vendors.vendor_hash("Papeleria El Sol", "Banco Andino", "ES91 2100 1234 5100 1234 5678")
    assert a == b
    assert len(a) == 64


def test_normalizacion_ignora_espacios_y_mayusculas():
    a = vendors.vendor_hash("papeleria el sol", "banco andino", "es91 2100 1234 5100 1234 5678")
    b = vendors.vendor_hash("PAPELERIA EL SOL", "Banco Andino", "ES9121001234510012345678")
    assert a == b


def test_cambio_de_cuenta_cambia_el_hash():
    a = vendors.vendor_hash("Papeleria El Sol", "Banco Andino", "ES91 2100 1234 5100 1234 5678")
    b = vendors.vendor_hash("Papeleria El Sol", "Banco Andino", "ES91 2100 1234 5100 1234 9999")
    assert a != b


def test_seed_registra_proveedores(db_path):
    with database.get_conn() as conn:
        lista = vendors.list_vendors(conn)
        assert len(lista) >= 3
        assert all(v["hash"] and len(v["hash"]) == 64 for v in lista)


def test_verify_vendor_match(db_path):
    with database.get_conn() as conn:
        v = vendors.list_vendors(conn)[0]
        res = vendors.verify_vendor(conn, v["nombre"], v["banco"], v["cuenta"])
        assert res["match"] is True
        assert res["registered"] is True
        assert res["registered_hash"] == v["hash"]


def test_verify_vendor_mismatch_por_cuenta_alterada(db_path):
    with database.get_conn() as conn:
        v = vendors.list_vendors(conn)[0]
        res = vendors.verify_vendor(conn, v["nombre"], v["banco"], v["cuenta"][:-4] + "9999")
        assert res["match"] is False
        assert res["registered"] is True
        assert res["incoming_hash"] != res["registered_hash"]


def test_verify_vendor_desconocido(db_path):
    with database.get_conn() as conn:
        res = vendors.verify_vendor(conn, "Proveedor Fantasma", "Banco X", "ES00 0000 0000")
        assert res["match"] is False
        assert res["registered"] is False


def test_update_vendor_account_regenera_hash(db_path):
    with database.get_conn() as conn:
        v = vendors.list_vendors(conn)[0]
        nuevo_hash = vendors.update_vendor_account(conn, v["id"], v["banco"], "ES99 9999 9999 9999 9999 9999", "admin")
        assert nuevo_hash != v["hash"]
        actualizado = vendors.get_vendor(conn, v["id"])
        assert actualizado["hash"] == nuevo_hash
        assert actualizado["cuenta"] == "ES99 9999 9999 9999 9999 9999"
