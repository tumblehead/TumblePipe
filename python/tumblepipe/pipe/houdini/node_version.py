"""Upgrade th:: HDA instances saved at an older definition version.

Houdini stores a node's parm values by parm *name*, so renaming a parm in an
HDA's DialogScript silently drops the value every saved scene holds for the
old name ("Skipping unrecognized parameter"). Houdini's own mechanism for
carrying such values forward is the definition's **Version** field plus its
``SyncNodeVersion`` event: when a node loads whose saved version differs from
the installed definition's, Houdini runs that handler with the node, the
saved version and the current one, after the saved parm values are loaded.

The saved value of a renamed parm only survives the load if the definition
still has a parm of that name, so each renamed parm stays in the
DialogScript as an *invisible carrier* under its old name. This module moves
a carrier's value (or expression, or keyframes) onto its new parm once, then
reverts the carrier; the node is then saved at the current version and
never synced again. A carrier left at its default carries nothing — a node
created after the rename, or an inner node whose HDA contents already name
the new parm — so it is skipped rather than allowed to clobber the new parm.

Every pipeline HDA whose parms were renamed calls :func:`sync` from its
``SyncNodeVersion`` section and has its Version field at the newest key of
:data:`RENAMES`. A node saved before the definition had a Version field
loads with an empty old version, which reads as version 1.

Cross-node references *to* an old parm name (another node's
``chs("../import_layer1/variant")``) are not rewritten: they read the
reverted carrier from then on. No pipeline HDA makes such a reference.
"""

import re

#: Parm renames per definition version: a node saved below a version gets
#: that version's renames applied, oldest first. ``#`` stands for a multiparm
#: instance number. Carrier parms named here must stay in the DialogScript.
RENAMES: dict[int, tuple[tuple[str, str], ...]] = {
    # The publish channel used to be called a variant, colliding with USD's
    # own variants (see tumblepipe.config.channels).
    2: (('variant', 'channel'), ('variant#', 'channel#')),
}

#: The definition version every migrated HDA declares.
CURRENT_VERSION = max(RENAMES)


def _version(text: str) -> int:
    """A saved Version string as a number; unversioned saves are version 1."""
    text = (text or '').strip()
    return int(text) if text.isdigit() else 1


def _pairs(node, old_name: str, new_name: str):
    """(old parm, new parm) for one rename on ``node``; multiparm-aware."""
    if '#' not in old_name:
        old, new = node.parm(old_name), node.parm(new_name)
        if old is not None and new is not None:
            yield old, new
        return
    # Parm names are identifiers, so '#' is the only character to translate.
    pattern = re.compile(old_name.replace('#', r'(\d+)') + '$')
    for parm in node.parms():
        match = pattern.match(parm.name())
        if match is None:
            continue
        new = node.parm(new_name.replace('#', match.group(1)))
        if new is not None:
            yield parm, new


def _move(old, new) -> None:
    """Carry ``old``'s authored value, expression or keyframes onto ``new``."""
    keyframes = old.keyframes()
    new.deleteAllKeyframes()
    if keyframes:
        for keyframe in keyframes:
            new.setKeyframe(keyframe)
    else:
        new.set(old.unexpandedString())
    old.deleteAllKeyframes()
    old.revertToDefaults()


def migrate(node, old_version: int) -> list[tuple[str, str]]:
    """Apply every rename newer than ``old_version``; the moved (old, new) names."""
    moved = []
    for version in sorted(RENAMES):
        if version <= old_version:
            continue
        for old_name, new_name in RENAMES[version]:
            for old, new in _pairs(node, old_name, new_name):
                if old.isAtDefault():
                    continue
                _move(old, new)
                moved.append((old.name(), new.name()))
    return moved


def sync(kwargs) -> None:
    """``SyncNodeVersion`` handler of every renamed-parm pipeline HDA."""
    migrate(kwargs['node'], _version(kwargs['old_version']))
