from securedb import config
from securedb.models import OperationProposal
from securedb.risk_engine import classify


def make(**kwargs):
    base = dict(
        operation_type="select",
        target="empleados",
        intent="consulta de prueba",
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


def test_select_risk_none():
    a = classify(make(operation_type="select"))
    assert a.level == 0
    assert not a.requires_approval
    assert not a.prohibited


def test_notify_individual_none():
    a = classify(make(operation_type="notify", recipient_scope="individual"))
    assert a.level == 0


def test_notify_small_group_low():
    a = classify(make(operation_type="notify", recipient_scope="small_group"))
    assert a.level == 1
    assert a.min_authority == config.ROLE_OPERATOR


def test_notify_large_group_medium():
    a = classify(make(operation_type="notify", recipient_scope="large_group"))
    assert a.level == 2
    assert a.min_authority == config.ROLE_SUPERVISOR


def test_update_single_non_financial_low():
    a = classify(make(operation_type="update", changes={"cargo": "Analista Sr."}))
    assert a.level == 1


def test_update_multiple_non_financial_medium():
    a = classify(make(operation_type="update", records_affected=5))
    assert a.level == 2


def test_update_financial_single_high():
    a = classify(make(operation_type="update", records_affected=1, involves_financial=True))
    assert a.level == 3
    assert a.min_authority == config.ROLE_ADMIN
    assert not a.requires_confirmation


def test_update_financial_multiple_critical():
    a = classify(make(operation_type="update", records_affected=8, involves_financial=True))
    assert a.level == 4
    assert a.requires_confirmation


def test_delete_single_critical():
    a = classify(make(operation_type="delete", records_affected=1))
    assert a.level == 4
    assert a.requires_confirmation


def test_delete_multiple_critical():
    a = classify(make(operation_type="delete", records_affected=5))
    assert a.level == 4


def test_drop_table_prohibited():
    a = classify(make(operation_type="drop_table", target="empleados"))
    assert a.prohibited
    assert a.level == config.RISK_PROHIBITED


def test_modify_audit_prohibited():
    a = classify(make(operation_type="modify_audit", target="auditoria"))
    assert a.prohibited


def test_alter_credentials_prohibited():
    a = classify(make(operation_type="alter_credentials", target="sistema"))
    assert a.prohibited


def test_update_audit_target_prohibited():
    a = classify(make(operation_type="update", target="audit_log"))
    assert a.prohibited


def test_modifiers_escalate_select():
    a = classify(make(operation_type="select", involves_financial=True, records_affected=150))
    assert a.level == 3


def test_modifiers_cap_at_critical():
    a = classify(
        make(
            operation_type="update",
            records_affected=300,
            involves_financial=True,
            involves_personal=True,
            reversible=False,
        )
    )
    assert a.level == 4


def test_irreversible_modifier():
    a = classify(make(operation_type="update", reversible=False))
    assert a.level == 2


def test_personal_data_modifier():
    a = classify(make(operation_type="select", involves_personal=True))
    assert a.level == 1
