"""Settings grouping — every documented env name still loads via its grouped field.

The operator setup guide at
``symbiosis-brain/projects/ljm-intelligence/reference/operator-setup-guide.md``
documents the env var names. After the Settings-grouping refactor the Python
field is grouped (e.g. ``s.gmail.sa_json``) but the env name must stay IDENTICAL
(``GMAIL_SA_JSON``). This test is the operator-contract tripwire: if an env
name silently drifts, pydantic-settings would fall back to the default and
this test fails loud.
"""

from __future__ import annotations

import pytest

from app.config import Settings

# (env_name, attribute path, expected value when loaded)
#
# Values chosen to be unambiguously distinct from the field defaults so a
# shadowed/misrouted env var fails here.
_DOCUMENTED_ENV = [
    # Gmail
    ("GMAIL_SA_JSON", "gmail.sa_json", "FAKE_SA_JSON"),
    ("GMAIL_IMPERSONATE", "gmail.impersonate", "ops@example.test"),
    ("GMAIL_ADMIN_IMPERSONATE", "gmail.admin_impersonate", "admin@example.test"),
    ("GMAIL_SCOPES_SEND", "gmail.scopes_send", "https://scope.example/send"),
    ("GMAIL_SCOPES_READ", "gmail.scopes_read", "https://scope.example/read"),
    ("GMAIL_SCOPES_ADMIN", "gmail.scopes_admin", "https://scope.example/admin"),
    ("GMAIL_PER_MAILBOX_RPS", "gmail.per_mailbox_rps", "3.5"),
    ("GMAIL_GLOBAL_RPS", "gmail.global_rps", "17.0"),
    ("GMAIL_BACKOFF_MAX_SECONDS", "gmail.backoff_max_seconds", "42"),
    # DAT
    ("DAT_BASE_URL", "dat.base_url", "https://dat.example.test"),
    ("DAT_SERVICE_ACCOUNT_EMAIL", "dat.service_account_email", "sa@dat.example"),
    ("DAT_SERVICE_ACCOUNT_PASSWORD", "dat.service_account_password", "secret-dat"),
    ("DAT_ORG_ID", "dat.org_id", "ORG-123"),
    ("DAT_RPS", "dat.rps", "0.9"),
    # CHR
    ("CHR_BASE_URL", "chr.base_url", "https://chr.example.test"),
    ("CHR_CLIENT_ID", "chr.client_id", "chr-cid"),
    ("CHR_CLIENT_SECRET", "chr.client_secret", "chr-cs"),
    ("CHR_CARRIER_CODE", "chr.carrier_code", "CODE-X"),
    ("CHR_SCOPE", "chr.scope", "custom.scope"),
    ("CHR_RPS", "chr.rps", "4.0"),
    # LB123
    ("LB123_BASE_URL", "lb123.base_url", "https://lb123.example.test"),
    ("LB123_API_KEY", "lb123.api_key", "lb-key"),
    ("LB123_CARRIER_USERNAME", "lb123.carrier_username", "lb-user"),
    ("LB123_CARRIER_PASSWORD", "lb123.carrier_password", "lb-pass"),
    ("LB123_RPS", "lb123.rps", "0.3"),
    # Truckstop
    ("TRUCKSTOP_BASE_URL", "truckstop.base_url", "https://ts.example.test"),
    ("TRUCKSTOP_INTEGRATION_ID", "truckstop.integration_id", "ts-iid"),
    ("TRUCKSTOP_USERNAME", "truckstop.username", "ts-user"),
    ("TRUCKSTOP_PASSWORD", "truckstop.password", "ts-pass"),
    ("TRUCKSTOP_RPS", "truckstop.rps", "5.5"),
    # Flat mail knobs documented in the operator guide — kept flat on purpose.
    ("MAIL_SENDER", "mail_sender", "gmail"),
    ("MAILBOX_SOURCE", "mailbox_source", "gmail"),
    ("MAIL_OWNER_SEND_ENABLED", "mail_owner_send_enabled", "true"),
]


def _resolve(obj, path: str):
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


@pytest.mark.parametrize("env_name,path,raw", _DOCUMENTED_ENV)
def test_documented_env_loads_into_grouped_field(
    monkeypatch: pytest.MonkeyPatch, env_name: str, path: str, raw: str
) -> None:
    monkeypatch.setenv(env_name, raw)
    s = Settings()
    value = _resolve(s, path)
    # Unwrap SecretStr for comparison.
    unwrapped = value.get_secret_value() if hasattr(value, "get_secret_value") else value
    # List CSV env vars → first element.
    if isinstance(unwrapped, list):
        unwrapped = unwrapped[0] if unwrapped else ""
    # Booleans / floats / ints are coerced; compare as strings.
    assert str(unwrapped).lower() == raw.lower(), (
        f"{env_name} did not route to {path}: got {unwrapped!r}"
    )
