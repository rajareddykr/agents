"""Mesh identity + low-level A2A trust handshake.

Three things:
  * ``ensure_identity(node_id)`` — return the identity used to sign/verify
    handshakes. For **this process's own role** (``AGENT_ROLE``) we hand back
    the SDK's attached identity, which was recovered from key escrow (IMPL-067)
    — same DID across restarts. For any other ``node_id`` (peers in the local
    demo, or a role never bootstrapped) we mint a process-local Ed25519
    identity so in-process handshake still works.
  * ``handshake(initiator_did, peer_did)`` — challenge/signed-response exchange
    via ``TrustHandshake``. Publishes to the CP if mesh wiring is active.
  * ``attest_handshake(initiator_did, peer_did, ...)`` — the distributed path,
    where the peer's private key lives in another process. We can't run the
    crypto, so we record the edge with the peer's real DID from ``/whoami``.

Restart stability is delivered by the SDK — ``AutoKernel.mesh_engines.identity``
is the attached-from-escrow identity. This module just prefers it over
minting a new one, so ``base.Agent.__init__`` no longer sees a fresh DID on
every process start.
"""
import logging
import os
from datetime import datetime

from agentmesh.identity.agent_id import AgentIdentity, IdentityRegistry
from agentmesh.trust.handshake import TrustHandshake, HandshakeResult

log = logging.getLogger("agentic_ops.agents.mesh")

REGISTRY = IdentityRegistry()
_IDENTITIES: dict = {}
_MIN_TRUST = 500


def mesh_status() -> dict:
    """Quick view of whether THIS process is mesh-wired (for /api/mesh)."""
    process_did, pub = _sdk_mesh_wiring()
    return {
        "mesh_wired": pub is not None,
        "process_did": process_did,
        "publisher": type(pub).__name__ if pub is not None else None,
        "local_identities": list(_IDENTITIES.keys()),
    }


def _sdk_attached_identity():
    """Return the SDK's attached mesh identity, or ``None`` when unavailable.

    ``AutoKernel.mesh_engines.identity`` is the identity that came back from
    key-escrow recovery (IMPL-067) — same public key across restarts, so the
    ``sha256(public_key)`` DID is stable. When mesh wiring is off (no token /
    no passphrase / [mesh] extra missing) this returns ``None`` and callers
    fall back to a local synthetic identity.
    """
    try:
        from agt_sdk import AutoKernel
        engines = AutoKernel.instance().mesh_engines
    except Exception:
        return None
    if engines is None:
        return None
    return getattr(engines, "identity", None)


def _process_role() -> str:
    """The role this OS process is running as (``coordinator`` by default)."""
    return (os.environ.get("AGENT_ROLE") or "coordinator").strip()


def ensure_identity(node_id, capabilities=None):
    """Return the identity to use for ``node_id`` in local handshakes.

    For the process's own role we prefer the SDK's escrow-recovered identity so
    the DID is stable across restarts. For any other node we mint a
    process-local identity once and cache it — the in-process demo path still
    needs peer identities registered locally so the challenge/response can
    verify signatures.
    """
    if node_id in _IDENTITIES:
        return _IDENTITIES[node_id]

    if node_id == _process_role():
        sdk_identity = _sdk_attached_identity()
        if sdk_identity is not None:
            try:
                REGISTRY.register(sdk_identity)
            except Exception:  # noqa: BLE001 — registry may reject duplicates; safe to ignore
                pass
            _IDENTITIES[node_id] = sdk_identity
            log.info(
                "mesh: using SDK-attached identity for %s (did=%s) — stable across restarts",
                node_id, getattr(sdk_identity, "did", "?"),
            )
            return sdk_identity

    idn = AgentIdentity.create(name=node_id, sponsor="agentic-mcp-ops@demo.local",
                               capabilities=capabilities or [f"{node_id}.analyze"])
    REGISTRY.register(idn)
    _IDENTITIES[node_id] = idn
    return idn


def _sdk_mesh_wiring():
    """Return (process_did, publisher) from the SDK if mesh wiring is active.

    ``process_did`` is the DID the CP has a row for — the same one recovered
    from escrow on restart. We publish handshakes under it so the CP can key
    the trust-graph edge on both sides.
    """
    try:
        from agt_sdk import AutoKernel
        k = AutoKernel.instance()
    except Exception:
        return None, None
    publisher = getattr(k, "cp_handshake_publisher", None)
    process_did = None
    engines = getattr(k, "mesh_engines", None)
    ident = getattr(engines, "identity", None) if engines else None
    if ident is not None:
        process_did = str(getattr(ident, "did", "") or "") or None
    return process_did, publisher


async def handshake(initiator_did, peer_did) -> HandshakeResult:
    """Challenge/signed-response between two locally-registered DIDs.

    Runs the exchange, then publishes the result to the control plane so the
    AGT trust graph draws an edge. Fail-open on publish.
    """
    ts = TrustHandshake(agent_did=initiator_did,
                        identity=REGISTRY.get(initiator_did), registry=REGISTRY)
    result = await ts.initiate(peer_did=peer_did,
                               required_trust_score=_MIN_TRUST, use_cache=False)

    process_did, pub = _sdk_mesh_wiring()
    if pub is not None:
        try:
            await pub.publish_handshake(initiator_did=process_did or initiator_did,
                                        transport="a2a", result=result)
        except Exception as e:  # noqa: BLE001 — fail-open
            log.debug("CP publish failed (fail-open): %s", e)

    return result


async def attest_handshake(initiator_did, peer_did, peer_name=None,
                           capabilities=None) -> HandshakeResult:
    """Attest a handshake with a REMOTE peer (distributed mode).

    The peer runs in another process, so its private key isn't local. Peer's
    real DID came from its ``/whoami`` (recovered from escrow, so stable). We
    record the CP edge with that DID. Fail-open on publish.
    """
    result = HandshakeResult.success(
        peer_did=peer_did,
        trust_score=_MIN_TRUST,
        capabilities=capabilities or [],
        peer_name=peer_name,
        started=datetime.utcnow(),
    )
    process_did, pub = _sdk_mesh_wiring()
    if pub is not None:
        try:
            await pub.publish_handshake(initiator_did=process_did or initiator_did,
                                        transport="a2a", result=result)
        except Exception as e:  # noqa: BLE001 — fail-open
            log.debug("attest publish failed (fail-open): %s", e)
    return result
