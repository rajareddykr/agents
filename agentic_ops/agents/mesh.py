"""Mesh identity + low-level A2A trust handshake (Steps 4a + 5).

Three things:
  * ``ensure_identity(node_id)`` — create/register an Ed25519 ``did:mesh`` in a
    process-local registry, once per node.
  * ``handshake(initiator_did, peer_did)`` — run the real challenge/signed-
    response exchange via ``TrustHandshake``, publish the result to the control
    plane (Step 5), and return the ``HandshakeResult``.
  * ``_sdk_publisher()`` — fetch the SDK's CP handshake publisher (or None).

This is the LOW-LEVEL layer (DID in, result out). The agent-facing wrapper that
emits the dashboard A2A events lives in ``base.Agent.handshake(self, peer)`` and
calls into this module — do NOT put the agent method here.
"""
import logging
from datetime import datetime

from agentmesh.identity.agent_id import AgentIdentity, IdentityRegistry
from agentmesh.trust.handshake import TrustHandshake, HandshakeResult

log = logging.getLogger("agentic_ops.agents.mesh")

REGISTRY = IdentityRegistry()
_IDENTITIES = {}
_MIN_TRUST = 500


def mesh_status() -> dict:
    """Quick view of whether THIS process is mesh-wired (for /api/mesh).

    ``mesh_wired`` True means the SDK bootstrapped with the [mesh] extra and has
    a CP handshake publisher — a prerequisite for handshakes to reach the CP.
    """
    process_did, pub = _sdk_mesh_wiring()
    return {
        "mesh_wired": pub is not None,
        "process_did": process_did,
        "publisher": type(pub).__name__ if pub is not None else None,
        "local_identities": list(_IDENTITIES.keys()),
    }


def ensure_identity(node_id, capabilities=None):
    if node_id in _IDENTITIES:
        return _IDENTITIES[node_id]
    idn = AgentIdentity.create(name=node_id, sponsor="agentic-mcp-ops@demo.local",
                               capabilities=capabilities or [f"{node_id}.analyze"])
    REGISTRY.register(idn)
    _IDENTITIES[node_id] = idn
    return idn


def _sdk_mesh_wiring():
    """Return (process_did, publisher) from the SDK if mesh wiring is active.

    ``process_did`` is the agent's REAL bootstrap did:mesh (the DID the CP has a
    row for). We publish handshakes under it — not the local ensure_identity()
    DID — or the CP trust graph can't key the edge and drops it silently.
    Returns (None, None) when the mesh extra isn't wired (no bootstrap, etc.).
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
    """Challenge/signed-response between two registered DIDs.

    Runs the exchange, then (Step 5) publishes the result to the control plane
    so the AGT trust graph draws an edge. Publishing is best-effort / fail-open:
    if the SDK mesh wiring isn't active it's skipped. Returns the result.
    """
    ts = TrustHandshake(agent_did=initiator_did,
                        identity=REGISTRY.get(initiator_did), registry=REGISTRY)
    result = await ts.initiate(peer_did=peer_did,
                               required_trust_score=_MIN_TRUST, use_cache=False)

    # Step 5 — publish to CP under the SDK's real bootstrap DID (best-effort).
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
    """Attest a handshake with a REMOTE peer (Step 5, distributed).

    The peer runs in another process, so its private key isn't local and we
    can't run the challenge/response ourselves. The peer's real DID came from
    its ``/whoami``; we record the edge on the CP so the trust graph shows real
    DIDs on both sides. Fail-open on publish.
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
