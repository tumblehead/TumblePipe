"""Who may change a project's shared defaults from inside Houdini.

A project's defaults (today its department pool) live in its config and are
read by everyone working on it, so the Projects settings page only lets an
organisation **owner** or **admin** change them. TumbleTrove Desktop says
who that is: it launches Houdini with

* ``TT_PROJECT_NAME`` — the project it launched;
* ``TT_ORGANIZATION_SLUG`` — the organisation the project belongs to, unset
  for a personal project;
* ``TT_ORGANIZATION_ROLE`` — ``OWNER``, ``ADMIN`` or ``MEMBER`` in it
  (Desktop 0.55+). Desktop always sets this one itself: a project cannot
  supply it through its own env vars. It is missing when Desktop was
  offline at launch, or older, and that reads as read-only.

This is a guard against a slip, not a security boundary: the config is
files on a share, and whoever can write the share can edit them by hand.
Folder permissions are what actually protect it.

Outside Desktop (no ``TT_PROJECT_NAME``) nothing says who anyone is, so
editing stays open, as it always was. A personal project is its owner's.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

#: The roles that may change a project's defaults.
EDITOR_ROLES = frozenset({"OWNER", "ADMIN"})


def launched_project_name(env: Mapping[str, str] | None = None) -> str:
    """The project TumbleTrove Desktop launched, or ``""`` for none.

    Named as ``PipelineProjectRegistry.bootstrap_from_env`` names it: the
    leaf folder of ``TH_PROJECT_PATH``.
    """
    env = os.environ if env is None else env
    path = env.get("TH_PROJECT_PATH", "").strip()
    return (Path(path).name or "default") if path else ""


def can_edit_project_defaults(project, env: Mapping[str, str] | None = None
                              ) -> tuple[bool, str]:
    """``(allowed, why not)`` for changing *project*'s shared defaults.

    *why not* is ``""`` when allowed; otherwise one sentence for the artist,
    saying what would let them.
    """
    env = os.environ if env is None else env
    if not env.get("TT_PROJECT_NAME", "").strip():
        return True, ""

    launched = launched_project_name(env)
    if launched and project.name != launched:
        return False, (
            f"Read-only: Desktop launched {launched}, so only its defaults "
            f"can be changed here. Open {project.name} from TumbleTrove "
            f"Desktop to change its defaults.")

    org = env.get("TT_ORGANIZATION_SLUG", "").strip()
    if not org:
        return True, ""

    role = env.get("TT_ORGANIZATION_ROLE", "").strip().upper()
    if role in EDITOR_ROLES:
        return True, ""
    if not role:
        return False, (
            f"Read-only: TumbleTrove Desktop did not say whether you are an "
            f"admin of {org}. It may have been offline when it launched "
            f"Houdini, or need updating; relaunch from an up-to-date Desktop "
            f"to change project defaults.")
    return False, (
        f"Read-only: only owners and admins of {org} can change project "
        f"defaults, because everyone on the project shares them. Ask one of "
        f"them, or to be made an admin.")
