import pytest
from fastapi import HTTPException


def test_smtp_payload_rejects_email_address_as_host():
    from app.routers.settings import _clean_smtp_payload

    with pytest.raises(HTTPException) as exc:
        _clean_smtp_payload({
            "host": "smtp@gmail.com",
            "port": 587,
            "username": "accounts@example.test",
        })

    assert exc.value.status_code == 400
    assert "smtp.gmail.com" in exc.value.detail


def test_smtp_payload_strips_spaces_from_app_password():
    from app.routers.settings import _clean_smtp_payload

    cleaned = _clean_smtp_payload({
        "host": " smtp.gmail.com ",
        "port": 587,
        "username": " princemckenzie77@gmail.com ",
        "password": "abcd efgh ijkl mnop",
        "from_address": " princemckenzie77@gmail.com ",
        "from_name": " LCCCA Academic Office ",
    })

    assert cleaned["host"] == "smtp.gmail.com"
    assert cleaned["username"] == "princemckenzie77@gmail.com"
    assert cleaned["password"] == "abcdefghijklmnop"
    assert cleaned["from_address"] == "princemckenzie77@gmail.com"
    assert cleaned["from_name"] == "LCCCA Academic Office"
