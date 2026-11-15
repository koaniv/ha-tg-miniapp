"""Validation of Telegram Mini App initData.

Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
hash       = hex(HMAC_SHA256(key=secret_key, msg=data_check_string))
data_check_string = sorted "key=value" pairs (except "hash"), joined by "\n".
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(Exception):
    """initData is missing, malformed, forged or expired."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age: int = 86400,
    now: float | None = None,
) -> dict:
    """Return the Telegram user dict if initData is valid, else raise InitDataError."""
    if not init_data:
        raise InitDataError("empty initData")

    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise InitDataError("no hash")
    # "signature" is for third-party validation; it is part of data_check_string
    # for the bot-token method, so it stays in `data`.

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        raise InitDataError("bad hash")

    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError as err:
        raise InitDataError("bad auth_date") from err
    now = time.time() if now is None else now
    if auth_date <= 0 or now - auth_date > max_age or auth_date - now > 300:
        raise InitDataError("expired")

    try:
        user = json.loads(data.get("user", ""))
    except ValueError as err:
        raise InitDataError("bad user") from err
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise InitDataError("no user id")
    return user
