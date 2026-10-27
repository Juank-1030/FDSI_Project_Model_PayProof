from securedb import approval_token


def test_token_valido_redondo():
    tok = approval_token.issue_token(17, "abc123", ttl_minutes=10)
    request_id, error = approval_token.verify_token(tok, "abc123")
    assert error is None
    assert request_id == 17


def test_token_con_payload_distinto_se_rechaza():
    tok = approval_token.issue_token(17, "abc123", ttl_minutes=10)
    _, error = approval_token.verify_token(tok, "abc999")
    assert error is not None
    assert "firma" in error.lower()


def test_token_expirado():
    tok = approval_token.issue_token(17, "abc123", ttl_minutes=-1)
    _, error = approval_token.verify_token(tok, "abc123")
    assert error is not None
    assert "expir" in error.lower()


def test_token_malformado():
    _, error = approval_token.verify_token("no-es-un-token", "abc123")
    assert error is not None


def test_link_contiene_vista_y_token():
    link = approval_token.build_approval_link(7, "hash-demo")
    assert "view=aprobar" in link
    assert "id=7" in link
    assert "token=" in link
