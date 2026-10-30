"""The project config layout version this package reads.

A project's ``_config/version.json`` records the last config migration it
took (``src/wizard/core/src/migration.rs``; ``tt_prepare --migrate`` applies
the pending ones, and TumbleTrove Desktop runs it on package upgrade). The
package reads exactly one layout: the one its own migrations end at. A
project on an older layout would not fail on its own — it would be read
wrongly (v13's publish channels, for one, would read as only ``default``) —
so a client refuses it up front and says how to fix it.

This is not a floor: every older layout still migrates, step by step.
"""

import json
from pathlib import Path

#: The layout the newest migration step writes; the scaffold's
#: ``scripts/project_template/_config/version.json`` carries the same number.
CONFIG_LAYOUT_VERSION = 17

VERSION_FILE = 'version.json'


class ProjectLayoutError(RuntimeError):
    """A project's config is not on the layout this package reads."""


def project_layout_version(config_path: Path) -> int:
    """The layout version ``config_path`` records; 0 when it records none
    (a project no migration has run on)."""
    path = Path(config_path) / VERSION_FILE
    if not path.is_file():
        return 0
    return int(json.loads(path.read_text(encoding='utf-8'))['version'])


def require_current_layout(config_path: Path) -> None:
    """Raise :class:`ProjectLayoutError` unless ``config_path`` is on
    :data:`CONFIG_LAYOUT_VERSION`."""
    version = project_layout_version(config_path)
    if version < CONFIG_LAYOUT_VERSION:
        raise ProjectLayoutError(
            f'The project config at {config_path} is on layout v{version}; '
            f'this TumblePipe reads v{CONFIG_LAYOUT_VERSION}. Launch the '
            'project from TumbleTrove Desktop, which migrates it (or run '
            'Migrate Project Config from its menu there), and open it again. '
            'If the launch reported that it could not migrate, fix what it '
            'named first.'
        )
    if version > CONFIG_LAYOUT_VERSION:
        raise ProjectLayoutError(
            f'The project config at {config_path} is on layout v{version}, '
            f'newer than this TumblePipe reads (v{CONFIG_LAYOUT_VERSION}). '
            'It was migrated by a newer TumblePipe: use that version.'
        )
