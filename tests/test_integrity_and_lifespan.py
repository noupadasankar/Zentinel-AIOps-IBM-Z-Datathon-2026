import json
import os
from unittest.mock import patch

import pytest

from zentinel.api.live_api import app, lifespan
from zentinel.config import ZentinelConfig, verify_manifest


def test_manifest_verification_succeeds_on_real_artifacts():
    verify_manifest()


def test_manifest_verification_fails_on_tampered_file(tmp_path):
    tampered_file = tmp_path / "tampered.bin"
    tampered_file.write_text("corrupted content", encoding="utf-8")

    fake_manifest = tmp_path / "manifest.json"
    manifest_data = {
        "version": "1.0.0",
        "artifacts": {
            "tampered.bin": {
                "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
                "size_bytes": 100,
            }
        },
    }
    fake_manifest.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Artifact integrity mismatch"):
        verify_manifest(manifest_path=fake_manifest, root_dir=tmp_path)


def test_manifest_verification_fails_on_missing_file(tmp_path):
    fake_manifest = tmp_path / "manifest.json"
    manifest_data = {
        "version": "1.0.0",
        "artifacts": {
            "nonexistent.bin": {
                "sha256": "abc12345",
                "size_bytes": 50,
            }
        },
    }
    fake_manifest.write_text(json.dumps(manifest_data), encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="Required artifact missing"):
        verify_manifest(manifest_path=fake_manifest, root_dir=tmp_path)


@pytest.mark.anyio
async def test_lifespan_refuses_web_concurrency_greater_than_one():
    with (
        patch.dict(os.environ, {"WEB_CONCURRENCY": "2"}),
        pytest.raises(RuntimeError, match="WEB_CONCURRENCY is set to 2"),
    ):
        async with lifespan(app):
            pass


def test_config_require_auth_fail_closed_defaults(monkeypatch):
    monkeypatch.delenv("ZENTINEL_REQUIRE_AUTH", raising=False)

    monkeypatch.setenv("ZENTINEL_ENV", "production")
    assert ZentinelConfig().require_auth is True

    monkeypatch.setenv("ZENTINEL_ENV", "development")
    assert ZentinelConfig().require_auth is False

    monkeypatch.setenv("ZENTINEL_ENV", "production")
    monkeypatch.setenv("ZENTINEL_REQUIRE_AUTH", "0")
    assert ZentinelConfig().require_auth is False

    monkeypatch.setenv("ZENTINEL_ENV", "development")
    monkeypatch.setenv("ZENTINEL_REQUIRE_AUTH", "1")
    assert ZentinelConfig().require_auth is True


@pytest.mark.anyio
async def test_lifespan_refuses_keyless_start_when_auth_required():
    from zentinel.api import live_api

    with (
        patch.object(live_api.CONFIG, "require_auth", True),
        patch.object(live_api.SECURITY, "_key_map", {}),
        pytest.raises(RuntimeError, match="Refusing to start: authentication is required"),
    ):
        async with lifespan(app):
            pass
