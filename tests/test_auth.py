import hashlib, hmac, json, sys, time
from pathlib import Path
from urllib.parse import urlencode

import importlib.util
spec = importlib.util.spec_from_file_location(
    "auth", Path(__file__).parents[1] / "custom_components/tg_miniapp/auth.py")
auth = importlib.util.module_from_spec(spec); spec.loader.exec_module(auth)

TOKEN = "123456:TEST-token"


def make(user_id=42, auth_date=None, token=TOKEN, extra=None):
    fields = {
        "query_id": "AAH",
        "user": json.dumps({"id": user_id, "first_name": "Андрій"}, ensure_ascii=False, separators=(",", ":")),
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "signature": "sig",
    }
    fields.update(extra or {})
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_valid():
    assert auth.validate_init_data(make(), TOKEN)["id"] == 42


def test_wrong_token():
    try:
        auth.validate_init_data(make(token="999:other"), TOKEN); assert False
    except auth.InitDataError as e:
        assert str(e) == "bad hash"


def test_tampered_user():
    s = make().replace("%22id%22%3A42", "%22id%22%3A43")
    try:
        auth.validate_init_data(s, TOKEN); assert False
    except auth.InitDataError as e:
        assert str(e) == "bad hash"


def test_expired():
    try:
        auth.validate_init_data(make(auth_date=time.time() - 90000), TOKEN, max_age=86400); assert False
    except auth.InitDataError as e:
        assert str(e) == "expired"


def test_future_date():
    try:
        auth.validate_init_data(make(auth_date=time.time() + 3600), TOKEN); assert False
    except auth.InitDataError as e:
        assert str(e) == "expired"


def test_empty_and_no_hash():
    for s in ("", "auth_date=1&user=%7B%7D"):
        try:
            auth.validate_init_data(s, TOKEN); assert False
        except auth.InitDataError:
            pass
