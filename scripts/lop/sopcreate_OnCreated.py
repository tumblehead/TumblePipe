"""Neutralize the H22-default Layer Save Path on every new SOP Create LOP.

H22 ships sopcreate with 'Layer Save Path' enabled and pointing at
$HIP/usd/$OS.usd (H21 shipped it off/empty). A save-path'ed layer is
written next to the workfile by the export ROP instead of flattening
into the published layer, so the publish composes empty on any other
machine. This creation script only sets the node's initial state — a
node that should genuinely save its layer can be re-enabled by hand.
"""

import logging

try:
    from tumblepipe.pipe.houdini.util import disable_layer_save_path
    disable_layer_save_path(kwargs['node'])  # noqa: F821 — Houdini-injected global
except Exception:
    logging.getLogger(__name__).warning(
        'Could not disable layer save path on %s', kwargs.get('node'),  # noqa: F821
        exc_info=True,
    )
