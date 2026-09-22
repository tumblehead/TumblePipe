#!/usr/bin/env python
"""Audit projects' `config:/discord` block against the channels the farm addresses.

Every farm job family ends in a notify job that posts to Discord, and the
channel it addresses is hardcoded per family (see the table below). The name
is looked up under `discord/channels` in the project's `config` database; a
project created from `scripts/project_template` carries that block EMPTY --
`token: ""`, no users, no channels -- so nothing resolves.

From 1.52.2 a project with no discord setup at all skips the post and the
notify job succeeds, so an empty block is quiet rather than fatal. That is
the point of this audit: quiet is easy to miss, and the projects that WANT
notifications look exactly like the ones that do not until you ask.

Reports, per project:

  ok           token + every addressed channel resolves
  unconfigured no token and no channels -- notifies skip (quiet, not broken)
  partial      a token or some channels, but not every addressed channel --
               the missing ones FAIL their notify job, and the notify is the
               last job in its family, so the whole batch reads as failed

Reads the config JSON straight off disk: no `hou`, no pipeline import, and no
dependency on whether the project's own `naming_convention.py` has been
migrated -- so it covers projects that will not even open.

    python audit_discord_config.py P:/
    python audit_discord_config.py P:/HideAndReek P:/Snail
    python audit_discord_config.py P:/ --users soren-n,magnus
"""

import argparse
import json
import sys
from pathlib import Path

# The channel names the pipeline hardcodes, and who addresses each one. Keep
# this in step with `grep -rn "channel_name\s*=\s*'" python/`.
ADDRESSED_CHANNELS = {
    'renders': 'render + playblast notifies (batch_submit)',
    'exports': 'publish, stage, propagate + lookdev/comp export notifies',
    'previews': 'th::build_comp and th::lookdev_studio previews',
    'comp': 'th::build_comp composite notifies',
}


def discord_node(project_path):
    """The project's `config:/discord` node, or None with a reason."""
    config_file = Path(project_path) / '_config' / 'db' / 'config.json'
    if not config_file.is_file():
        return None, f'no {config_file.name} under _config/db'
    try:
        data = json.loads(config_file.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        return None, f'unreadable config.json: {error}'
    node = (data.get('children') or {}).get('discord')
    if node is None:
        return None, 'config.json has no discord node'
    return node, None


def summarise(node):
    """(token_set, channels, users) for a discord node."""
    token = (node.get('properties') or {}).get('token')
    token_set = isinstance(token, str) and len(token.strip()) != 0
    children = node.get('children') or {}

    def names(key):
        return set((children.get(key) or {}).get('children') or {})

    return token_set, names('channels'), names('users')


def audit(project_path, expected_users):
    node, problem = discord_node(project_path)
    if node is None:
        return 'skipped', problem

    token_set, channels, users = summarise(node)
    missing = sorted(set(ADDRESSED_CHANNELS) - channels)

    if not token_set and len(channels) == 0:
        return 'unconfigured', 'no token, no channels -- notifies skip quietly'

    notes = []
    if not token_set:
        notes.append('no token (every notify fails)')
    if missing:
        notes.append(f'channels missing: {", ".join(missing)}')
    if expected_users:
        absent = sorted(expected_users - users)
        if absent:
            # Not fatal: an unknown user posts without a mention.
            notes.append(f'no mention for: {", ".join(absent)}')

    if not notes:
        return 'ok', f'{len(channels)} channels, {len(users)} users'
    fatal = (not token_set) or missing
    return ('partial' if fatal else 'ok'), '; '.join(notes)


def projects_under(root):
    """Every immediate child of `root` that looks like a project."""
    return sorted(
        path for path in Path(root).iterdir()
        if (path / '_config' / 'db' / 'config.json').is_file()
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        'paths', nargs='+',
        help='project directories, or a drive/folder holding them'
    )
    parser.add_argument(
        '--users', default='',
        help='comma-separated usernames that should resolve to a mention'
    )
    parser.add_argument(
        '--quiet', action='store_true',
        help='list only projects that are not ok'
    )
    args = parser.parse_args()

    expected_users = {
        name.strip().lower()
        for name in args.users.split(',') if name.strip()
    }

    targets = []
    for raw in args.paths:
        path = Path(raw)
        if (path / '_config' / 'db' / 'config.json').is_file():
            targets.append(path)
        elif path.is_dir():
            targets.extend(projects_under(path))
        else:
            print(f'not a project or directory: {path}', file=sys.stderr)

    if not targets:
        print('no projects found', file=sys.stderr)
        return 1

    width = max(len(path.name) for path in targets)
    counts = {}
    for path in targets:
        verdict, note = audit(path, expected_users)
        counts[verdict] = counts.get(verdict, 0) + 1
        if args.quiet and verdict == 'ok':
            continue
        print(f'{path.name:<{width}}  {verdict:<12}  {note}')

    print()
    print('  '.join(f'{verdict}: {count}' for verdict, count in sorted(counts.items())))
    if counts.get('partial'):
        print()
        print('A "partial" project fails its notify job, and the notify is the last')
        print('job in its family -- those batches read as failed. Fill the missing')
        print('names in under config:/discord in the config database editor.')
    return 1 if counts.get('partial') else 0


if __name__ == '__main__':
    sys.exit(main())
