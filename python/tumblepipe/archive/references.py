"""Absolute paths the archived scenes name, found by reading them as text.

An archive carries the project's own files. Two kinds of path in a scene
break that promise, and the plan should say so before anything is sent:

- a path **outside every root** (a texture on someone's desktop, a shared
  library drive): the file is not in the archive at all;
- a path **into the project by its absolute location**
  (``//TRUENAS/production/projects/HideAndReek/assets/…``, which lookdev
  exports write for textures): the file *is* archived, but the scene only
  finds it again when the project is restored at that same path.

Paths into a folder the archive leaves out (client material in
``resources/``) are reported with the first kind: the scene loads them, the
archive does not have them.

Recording references at save time (``hou.fileReferences``) costs every
artist save; reading at archive time costs nothing until someone archives.
Only text is read: plain ``.hip`` (``.hiplc`` / ``.hipnc`` are encoded and
skipped, and counted) of the latest workfile per department, and ``.usda``
files among the archived exports. A crate ``.usd`` is binary and skipped.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import re

from tumblepipe.archive.inventory import Item, Kind
from tumblepipe.archive.longpath import long_path

# Windows drive paths (C:/x, C:\x), UNC paths (//server/share, \\server\share)
# and POSIX absolute paths under the usual mount points. Stops at a quote,
# whitespace, '@' (USD asset delimiters), '`' or a control character.
_PATH = re.compile(
    rb'(?<![A-Za-z0-9])'
    rb'((?:[A-Za-z]:[\\/]|[\\/]{2}[A-Za-z0-9._-]+[\\/]|/(?:mnt|Volumes|home|Users|net|srv|projects)/)'
    rb'[^\s"\'@`<>|*?\x00-\x1f]+)'
)
_MAX_READ = 512 << 20       # a .hip larger than this is read in part


@dataclass
class ReferenceReport:
    #: absolute path -> archive files that name it, for paths outside the roots
    outside: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    #: absolute paths into an excluded folder -> files naming them
    excluded: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    #: archive files naming a project file by its absolute location
    absolute_inside: set[str] = field(default_factory=set)
    files_read: int = 0
    encoded_skipped: int = 0


def anchorless(text: str) -> str:
    """A path without what names its volume, lower case, forward slashes.

    ``W:/projects/X``, ``//TRUENAS/production/projects/X`` and
    ``/mnt/w/projects/X`` all become ``/projects/x``: the same folder seen
    through a drive letter, a share, or a mount. Comparing these tails lets a
    scene's path match a root however either was spelled.
    """
    path = text.replace('\\', '/').rstrip('/').lower()
    if path.startswith('//'):
        parts = path[2:].split('/', 2)          # server, share, rest
        return '/' + parts[2] if len(parts) > 2 else '/'
    if re.match(r'^[a-z]:(/|$)', path):
        return path[2:] or '/'
    match = re.match(r'^/(?:mnt|volumes)/[^/]+(/.*)?$', path)
    if match:
        return match.group(1) or '/'
    return path


_SERVER = re.compile(r'[A-Za-z0-9][A-Za-z0-9.-]{0,62}')


def plausible_path(text: str) -> bool:
    """Whether ``text`` can be a real path rather than encoded data.

    A ``.hip`` embeds geometry as base64, whose runs contain slashes:
    ``//10mJ1smIhEB…/…`` reads like a ``//server/share`` path. A real server
    name is a hostname (at most 63 characters, letters, digits, ``.``, ``-``)
    and no path segment is longer than 255 characters or empty.
    """
    path = text.replace('\\', '/')
    if path.startswith('//'):
        parts = path[2:].split('/')
        if len(parts) < 2 or not _SERVER.fullmatch(parts[0]):
            return False
        segments = parts[1:]
    else:
        segments = path.split('/')[1:]
    segments = segments[:-1] if segments and segments[-1] == '' else segments
    return all(0 < len(segment) <= 255 for segment in segments)


def scan_references(plan, read_bytes=None) -> ReferenceReport:
    """Read the plan's scenes for absolute paths and classify them."""
    roots = plan.inventory.roots
    if read_bytes is None:
        def read_bytes(item: Item) -> bytes:
            # Never read(_MAX_READ) blind: that allocates the whole cap per
            # call, ~0.1 s for a file of a few bytes.
            with long_path(item.path(roots)).open('rb') as handle:
                return handle.read(min(item.size, _MAX_READ) or 1)

    report = ReferenceReport()
    # The project folder and the roots beside it (HideAndReek_render, …);
    # roots inside the project (export/, assets/) are reached through it.
    root_names = {root.path.name.lower() for root in roots.roots if '/' not in root.prefix}
    project_name = roots.project_path.name.lower()
    excluded_dirs = {
        d.item.rel.split('/', 1)[0].lower()
        for d in plan.decisions
        if not d.include and d.item.root == 'project' and '/' in d.item.rel
        and d.item.kind != Kind.NOISE
    }
    # A root inside the project that the archive leaves out whole (the edit).
    included_roots = {d.item.root for d in plan.decisions if d.include}
    for root in roots.roots:
        if root.prefix.startswith(roots.project_path.name + '/') and root.purpose not in included_roots:
            excluded_dirs.add(root.prefix.split('/', 1)[1].split('/', 1)[0].lower())

    latest_workfile: dict[str, Item] = {}
    for decision in plan.decisions:
        item = decision.item
        if decision.include and item.kind == Kind.WORKFILE:
            key = f'{item.root}:{item.series}'
            if key not in latest_workfile or item.version > latest_workfile[key].version:
                latest_workfile[key] = item
    scenes = list(latest_workfile.values()) + [
        d.item for d in plan.decisions
        if d.include and d.item.rel.lower().endswith('.usda')
    ]

    for item in scenes:
        name = f'{roots.get(item.root).prefix}/{item.rel}'
        if item.kind == Kind.WORKFILE and not item.rel.lower().endswith('.hip'):
            report.encoded_skipped += 1
            continue
        try:
            data = read_bytes(item)
        except OSError:
            continue
        report.files_read += 1
        for match in _PATH.finditer(data):
            raw = match.group(1).decode('utf-8', errors='replace').rstrip('.,;:)]}')
            if not plausible_path(raw):
                continue
            where = locate(raw, project_name, root_names, excluded_dirs)
            if where == 'excluded':
                report.excluded[raw].add(name)
            elif where == 'inside':
                report.absolute_inside.add(name)
            elif where == 'outside':
                report.outside[raw].add(name)
    return report


#: Where software lives, not project data: a Houdini install, its user
#: preferences, installed packages, local app data, temp and scratch.
_SOFTWARE = re.compile(
    r'/(?:side effects software|sideef~\d|program files[^/]*|progra~\d)(?:/|$)'
    r'|^/program$'
    r'|/documents/houdini\d+\.\d+(?:/|$)'
    r'|/\.hpm(?:/|$)|/\.tumbletrove(?:/|$)|/appdata/'
    r'|/(?:tmp|temp)/|/th_temp/'
)


def locate(raw: str, project_name: str, root_names: set[str], excluded_dirs: set[str]) -> str | None:
    """Whether an absolute path is in the project, a left-out folder, outside, or software.

    Matched by folder name, not by volume: the same project is
    ``W:/projects/HideAndReek``, ``//TRUENAS/production/projects/HideAndReek``
    and ``P:/HideAndReek`` (a drive mapped one level deeper), so comparing
    paths below their volume is not enough. A path is in the project when one
    of its folders is named like the project or one of its roots
    (``HideAndReek_render``). Returns ``'inside'``, ``'excluded'``,
    ``'outside'``, or None for software (ignored).
    """
    tail = anchorless(raw)
    segments = [segment for segment in tail.split('/') if segment]
    for index, segment in enumerate(segments):
        if segment in root_names:
            rest = segments[index + 1:]
            if segment == project_name and rest and rest[0] in excluded_dirs:
                return 'excluded'
            return 'inside'
    if _SOFTWARE.search(tail):
        return None
    return 'outside'


def warnings_for(report: ReferenceReport, project_path: Path) -> list[str]:
    warnings = []
    if report.outside:
        sample = ', '.join(sorted(report.outside)[:5])
        warnings.append(
            f'{len(report.outside)} file path(s) outside the project are named by '
            f'the archived scenes and are not in the archive, e.g. {sample}.'
        )
    if report.excluded:
        sample = ', '.join(sorted(report.excluded)[:5])
        warnings.append(
            f'{len(report.excluded)} file path(s) in folders the archive leaves out '
            f'are named by the archived scenes, e.g. {sample}. Include the folder in '
            '_config/archive.json if the scenes need it.'
        )
    if report.absolute_inside:
        warnings.append(
            f'{len(report.absolute_inside)} archived scene(s) name project files by '
            f'absolute path ({project_path}); they find them only when the project '
            'is restored at that same path.'
        )
    if report.encoded_skipped:
        warnings.append(
            f'{report.encoded_skipped} latest workfile(s) are .hiplc/.hipnc, which '
            'cannot be read for file paths; check them in Houdini.'
        )
    return warnings
