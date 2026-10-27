import sqlite3

import pytest

from securedb import audit, database


def test_hash_chain_verifies(db_path):
    with database.get_conn() as conn:
        audit.log_event(conn, "tester", "TEST", "evento uno")
        audit.log_event(conn, "tester", "TEST", "evento dos")
        ok, bad = audit.verify_chain(conn)
        assert ok
        assert bad is None


def test_tamper_detected(db_path):
    with database.get_conn() as conn:
        audit.log_event(conn, "tester", "TEST", "evento uno")
        audit.log_event(conn, "tester", "TEST", "evento dos")
        conn.execute("DROP TRIGGER audit_log_no_update")
        conn.execute("UPDATE audit_log SET description = 'ALTERADO' WHERE id = 1")
        ok, bad = audit.verify_chain(conn)
        assert not ok
        assert bad == 1


def test_trigger_blocks_update(db_path):
    with database.get_conn() as conn:
        audit.log_event(conn, "tester", "TEST", "evento uno")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit_log SET description = 'HACKED' WHERE id = 1")


def test_trigger_blocks_delete(db_path):
    with database.get_conn() as conn:
        audit.log_event(conn, "tester", "TEST", "evento uno")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit_log WHERE id = 1")


def test_simulate_tamper_registers_incident(db_path):
    with database.get_conn() as conn:
        audit.log_event(conn, "tester", "TEST", "evento uno")
        msg = audit.simulate_tamper(conn, "tester")
        assert "BLOQUEADO" in msg
        incidents = audit.list_incidents(conn)
        assert len(incidents) == 1
        assert incidents[0]["tipo"] == "TAMPER_INTENT"
