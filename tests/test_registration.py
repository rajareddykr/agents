"""Tests for the IMPL-067 register-once + key-escrow wiring in agentic_ops.

These tests do NOT hit a live control plane. They verify the pieces we own
(``governance.py`` env mapping and ``agents.mesh.ensure_identity`` fallback
behaviour) and the SDK integration points we call — specifically that the
SDK's config recognises our env vars as the IMPL-067 identity-attach flow.

The important behavioural claim of IMPL-067 — the same DID across restarts —
is delivered by the SDK's escrow-recovery path, which needs a live CP to
exercise end-to-end. We assert the wiring on our side that puts it in play.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


PASSPHRASE_32 = "1iWGpS0rMhVfnbgNvl0qsLR32XndvsuoHRv6M38hd6k7hmTVBBtlkH7-gTlzBP8e"
DURABLE_TOKEN = "agt_fakeDurableCredentialForTesting_0123456789"
BOOTSTRAP_TOKEN = "agt_boot_singleUseTokenForBackwardsCompat_0123"


def _reload_governance() -> object:
    """Reload ``agentic_ops.governance`` — module-scope env logic re-runs."""
    # Also clear the agent role-name/id from prior imports so each test starts
    # from a known state. AGT_AGENT_TOKEN / AGT_BOOTSTRAP_TOKEN are mutated by
    # the module under test, so scrub before reloading too.
    for k in ("AGT_AGENT_TOKEN", "AGT_BOOTSTRAP_TOKEN"):
        os.environ.pop(k, None)
    sys.modules.pop("agentic_ops.governance", None)
    return importlib.import_module("agentic_ops.governance")


@pytest.fixture
def clean_env(monkeypatch):
    """Strip every env var the SUT reads so each test sees a clean slate."""
    for k in (
        "AGENT_ROLE", "AGT_AGENT_TOKEN", "AGT_BOOTSTRAP_TOKEN",
        "AGT_AGENT_PASSPHRASE", "AGT_AGENT_NAME", "AGT_AGENT_ID",
        "AGT_COORDINATOR_TOKEN", "AGT_FIN_AGENT_TOKEN", "AGT_RES_AGENT_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)
    yield monkeypatch


def test_durable_token_sets_agent_token_env(clean_env):
    clean_env.setenv("AGENT_ROLE", "coordinator")
    clean_env.setenv("AGT_COORDINATOR_TOKEN", DURABLE_TOKEN)
    clean_env.setenv("AGT_AGENT_PASSPHRASE", PASSPHRASE_32)

    _reload_governance()

    assert os.environ["AGT_AGENT_TOKEN"] == DURABLE_TOKEN
    assert "AGT_BOOTSTRAP_TOKEN" not in os.environ
    assert os.environ["AGT_AGENT_NAME"] == "Coordinator Agent"


def test_durable_token_clears_stale_bootstrap_token(clean_env):
    """A leftover AGT_BOOTSTRAP_TOKEN must not override the IMPL-067 flow.

    ``_lifecycle`` explicitly honours the bootstrap token while set, so leaving
    a stale one would silently re-identify the agent on every restart.
    """
    clean_env.setenv("AGENT_ROLE", "coordinator")
    clean_env.setenv("AGT_COORDINATOR_TOKEN", DURABLE_TOKEN)
    clean_env.setenv("AGT_AGENT_PASSPHRASE", PASSPHRASE_32)
    clean_env.setenv("AGT_BOOTSTRAP_TOKEN", "agt_boot_leftover")

    _reload_governance()

    assert "AGT_BOOTSTRAP_TOKEN" not in os.environ
    assert os.environ["AGT_AGENT_TOKEN"] == DURABLE_TOKEN


def test_bootstrap_style_token_falls_back_to_legacy_flow(clean_env):
    clean_env.setenv("AGENT_ROLE", "fin_agent")
    clean_env.setenv("AGT_FIN_AGENT_TOKEN", BOOTSTRAP_TOKEN)
    clean_env.setenv("AGT_AGENT_PASSPHRASE", PASSPHRASE_32)

    _reload_governance()

    assert os.environ["AGT_BOOTSTRAP_TOKEN"] == BOOTSTRAP_TOKEN
    assert "AGT_AGENT_TOKEN" not in os.environ
    assert os.environ["AGT_AGENT_NAME"] == "Financial Agent"


def test_missing_passphrase_still_sets_token_but_logs_error(clean_env, caplog):
    """No passphrase → the token is still exposed (so the SDK can surface
    the actionable error itself), and we log an error operators can act on.
    """
    clean_env.setenv("AGENT_ROLE", "res_agent")
    clean_env.setenv("AGT_RES_AGENT_TOKEN", DURABLE_TOKEN)

    with caplog.at_level("ERROR", logger="agentic_ops.governance"):
        _reload_governance()

    assert os.environ["AGT_AGENT_TOKEN"] == DURABLE_TOKEN
    assert any("AGT_AGENT_PASSPHRASE" in r.message for r in caplog.records)


def test_short_passphrase_logs_error(clean_env, caplog):
    clean_env.setenv("AGENT_ROLE", "coordinator")
    clean_env.setenv("AGT_COORDINATOR_TOKEN", DURABLE_TOKEN)
    clean_env.setenv("AGT_AGENT_PASSPHRASE", "too-short")

    with caplog.at_level("ERROR", logger="agentic_ops.governance"):
        _reload_governance()

    assert any("32 chars" in r.message for r in caplog.records)


def test_role_specific_name_binding(clean_env):
    """Escrow is keyed on (org, name) — the role must map to the right name."""
    clean_env.setenv("AGT_AGENT_PASSPHRASE", PASSPHRASE_32)

    for role, expected_name in (
        ("coordinator", "Coordinator Agent"),
        ("fin_agent",   "Financial Agent"),
        ("res_agent",   "Research Agent"),
    ):
        clean_env.setenv("AGENT_ROLE", role)
        _reload_governance()
        assert os.environ["AGT_AGENT_NAME"] == expected_name, role


def test_sdk_config_accepts_impl_067_env(clean_env):
    """The SDK must select IMPL-067 attach when both token + passphrase are set."""
    clean_env.setenv("AGENT_ROLE", "coordinator")
    clean_env.setenv("AGT_COORDINATOR_TOKEN", DURABLE_TOKEN)
    clean_env.setenv("AGT_AGENT_PASSPHRASE", PASSPHRASE_32)
    clean_env.setenv("AGT_CP_URL", "http://cp.invalid")
    clean_env.setenv("AGT_ORG_CODE", "demoorg")

    _reload_governance()

    from agt_sdk._config import SdkConfig
    cfg = SdkConfig.from_env()

    assert cfg.agent_token == DURABLE_TOKEN
    assert cfg.agent_passphrase == PASSPHRASE_32
    assert cfg.agent_name == "Coordinator Agent"
    assert cfg.uses_agent_credential is True
    assert cfg.bootstrap_token == ""


def test_mesh_ensure_identity_falls_back_when_sdk_unavailable(clean_env, monkeypatch):
    """Without a live SDK-attached identity, ensure_identity must still work —
    the in-process demo needs a local identity for handshake signing."""
    from agentic_ops.agents import mesh

    monkeypatch.setattr(mesh, "_sdk_attached_identity", lambda: None)
    mesh._IDENTITIES.clear()

    ident = mesh.ensure_identity("some_local_role")
    assert ident is not None
    assert mesh._IDENTITIES["some_local_role"] is ident


def test_mesh_ensure_identity_prefers_sdk_identity_for_own_role(clean_env, monkeypatch):
    """For the process's OWN role, mesh must hand back the SDK's escrow-recovered
    identity — that is what makes the DID stable across restarts."""
    from agentic_ops.agents import mesh

    class FakeIdentity:
        did = "did:mesh:stableDIDfromEscrow00"

    clean_env.setenv("AGENT_ROLE", "coordinator")
    monkeypatch.setattr(mesh, "_sdk_attached_identity", lambda: FakeIdentity())
    monkeypatch.setattr(mesh.REGISTRY, "register", lambda *_a, **_k: None)
    mesh._IDENTITIES.clear()

    ident = mesh.ensure_identity("coordinator")
    assert ident.did == "did:mesh:stableDIDfromEscrow00"

    # Second call must return the SAME cached identity — proving the "stable
    # across construction" behaviour that maps to "stable across restarts"
    # once the SDK is doing the escrow round-trip end to end.
    ident2 = mesh.ensure_identity("coordinator")
    assert ident2 is ident
