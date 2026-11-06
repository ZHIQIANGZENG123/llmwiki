"""LAN exposure must fail closed: no shared password means no network binding."""

import pytest
from config import Settings
from pydantic import ValidationError


def _local(**overrides) -> Settings:
    base = {
        "MODE": "local",
        "LOCAL_LAN_ENABLED": False,
        "LOCAL_BIND_HOST": "127.0.0.1",
        "LOCAL_PUBLIC_URL": "",
        "LOCAL_AUTH_TOKEN": "",
        "LOCAL_ALLOWED_HOSTS": "",
        "LOCAL_ALLOWED_ORIGINS": "",
        "APP_URL": "http://localhost:3000",
        "DATABASE_URL": "",
        "S3_BUCKET": "",
    }
    base.update(overrides)
    return Settings(**base)


def test_loopback_defaults_keep_the_original_unauthenticated_behaviour():
    settings = _local()

    assert settings.lan_enabled is False
    assert settings.local_auth_required is False
    assert settings.local_public_url == "http://localhost:3000"


def test_lan_exposure_without_a_password_is_rejected():
    with pytest.raises(ValidationError, match="LOCAL_AUTH_TOKEN"):
        _local(LOCAL_LAN_ENABLED=True, LOCAL_PUBLIC_URL="http://192.168.1.10:3000")


def test_lan_exposure_without_a_public_url_is_rejected():
    with pytest.raises(ValidationError, match="LOCAL_PUBLIC_URL"):
        _local(LOCAL_LAN_ENABLED=True, LOCAL_AUTH_TOKEN="s3cret")


def test_binding_a_non_loopback_address_also_requires_the_lan_guards():
    with pytest.raises(ValidationError):
        _local(LOCAL_BIND_HOST="0.0.0.0")


def test_lan_configuration_seeds_the_host_and_origin_allowlists():
    settings = _local(
        LOCAL_LAN_ENABLED=True,
        LOCAL_AUTH_TOKEN="s3cret",
        LOCAL_PUBLIC_URL="http://192.168.1.10:3000",
    )

    assert settings.lan_enabled is True
    assert settings.local_auth_required is True
    assert "192.168.1.10" in settings.local_allowed_hosts
    assert "192.168.1.10:3000" in settings.local_allowed_hosts
    assert "http://192.168.1.10:3000" in settings.cors_origins
    assert settings.local_public_url == "http://192.168.1.10:3000"


def test_https_public_url_marks_the_session_cookie_secure():
    settings = _local(
        LOCAL_LAN_ENABLED=True,
        LOCAL_AUTH_TOKEN="s3cret",
        LOCAL_PUBLIC_URL="https://wiki.lan:8443",
    )
    assert settings.session_cookie_secure is True


def test_operator_can_add_extra_origins_and_hosts():
    settings = _local(
        LOCAL_LAN_ENABLED=True,
        LOCAL_AUTH_TOKEN="s3cret",
        LOCAL_PUBLIC_URL="http://192.168.1.10:3000",
        LOCAL_ALLOWED_HOSTS="wiki.internal",
        LOCAL_ALLOWED_ORIGINS="http://wiki.internal:3000",
    )

    assert "wiki.internal" in settings.local_allowed_hosts
    assert "http://wiki.internal:3000" in settings.cors_origins
