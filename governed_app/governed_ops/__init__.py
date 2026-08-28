"""Governed multi-agent reference app.

Nothing in this package imports ``AutoKernel`` or calls into ``agt_sdk``
imperatively. The ONLY governance surface used is the two decorators,
imported at the top of each module that needs them:

    from agt_sdk import governed, peer_verified

Everything else — bootstrap, identity attach, key escrow, policy bundle
fetch, trust graph publishing — happens automatically the first time a
decorated function is defined (via ``AutoKernel.instance()`` inside the
decorator itself).
"""
# ``logging_setup`` MUST land first. ``config`` logs errors and warnings during
# its own import (a missing credential, a short passphrase), and ``agt_sdk``
# attaches its own stderr-only handler the first time it is imported — both of
# which need ROOT handlers already in place to reach a file. See the module
# docstring for the three ways diagnostics used to vanish.
from . import logging_setup  # noqa: F401

logging_setup.setup()

# ``config`` runs its dotenv loader + role→credential mapping at import
# time, so it MUST land before any module that imports ``agt_sdk`` (that is
# where ``AutoKernel.instance()`` snapshots the env into ``SdkConfig``).
from . import config  # noqa: E402,F401


def _patch_sdk_org_slug_regex() -> None:
    """Allow dotted org slugs (e.g. ``dev-mode.sqa1``) in agt_sdk URL composition.

    Upstream ``agt_sdk._paths._SLUG_RE`` enforces DNS-label rules (no dots),
    which drops paths to the legacy unscoped ``/api/v1`` form for any deployment
    whose org slug is a dotted name. The CP itself accepts dotted slugs in the
    path, so we widen the client-side regex to match.
    """
    import re
    try:
        from agt_sdk import _paths  # noqa: PLC2701 — vendor monkey-patch target
    except ImportError:
        return
    _paths._SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,61}[a-z0-9])?$")  # noqa: SLF001


_patch_sdk_org_slug_regex()

