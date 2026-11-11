"""Published texture sets: versioned images plus a ready MaterialX material.

A texture set (``wood``, ``concrete``) is published like a ``th::cache``:
its own versioned folder, not a department. It belongs either to an asset
or to the project's library::

    export:/assets/<category>/<asset>/_textures/<set>/vNNNN/
    export:/textures/<set>/vNNNN/

A version holds the images, renamed ``<set>_<role><tile><ext>``, a USD
layer ``<set>.usda`` whose default prim is a MaterialX ``Material`` reading
them by relative path (so the folder can move: another machine, an
archive restore), and ``context.json``, written last, which finishes the
version and records each map's role, file and colour space.

Colour spaces come from the role, not the file extension: the base colour
is ``sRGB - Texture`` (``ACEScg`` for an EXR, which holds linear data),
every other map is ``Raw``. The names are the project OCIO config's
(``_config/ocio/tumblehead.ocio``).

Roles are the tumbletrove Asset Browser's (``texture_role``): ``diffuse``,
``rough``, ``metallic``, ``normal``, ``displacement``, ``ao``, ``opacity``,
so a library set it downloaded publishes without translation.

Nothing here imports ``hou`` or ``pxr``; the layer is written as text.
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tumblepipe import naming
from tumblepipe.pipe.paths.version import (
    get_next_version_path,
    is_finished_version,
    list_version_paths,
)
from tumblepipe.util.io import (
    VERSION_MARKER,
    copy_version_contents,
    load_json_if_exists,
    store_json,
)
from tumblepipe.util.uri import Uri

#: ``kind`` of a texture version's ``context.json`` (a cache's is "cache").
KIND = 'texture'

#: The folder under an asset's export directory holding its texture sets.
ASSET_SEGMENT = '_textures'

#: The folder under ``export:/`` holding the project's library sets.
LIBRARY_SEGMENT = 'textures'

#: Every role a set can hold, in the order maps are listed.
ROLES = ('diffuse', 'rough', 'metallic', 'normal', 'displacement', 'ao',
         'opacity')

COLOUR_SPACE_SRGB = 'sRGB - Texture'
COLOUR_SPACE_LINEAR = 'ACEScg'
COLOUR_SPACE_DATA = 'Raw'

#: Extensions whose pixels are scene-linear, so a colour map in one is ACEScg.
LINEAR_EXTENSIONS = {'.exr', '.hdr'}

#: The UDIM token a map path may carry; tiles are four digits from 1001.
UDIM_TOKEN = '<UDIM>'
_TILE = re.compile(r'^\d{4}$')

# role -> (shader name, MaterialX image signature, USD type, surface input)
# A grey opacity map read as color3 drives all three channels alike; AO has
# no standard-surface input, so it is published but not wired.
_IMAGES = {
    'diffuse': ('tex_basecolor', 'color3', 'color3f', 'base_color'),
    'rough': ('tex_roughness', 'float', 'float', 'specular_roughness'),
    'metallic': ('tex_metalness', 'float', 'float', 'metalness'),
    'normal': ('tex_normal', 'vector3', 'vector3f', 'normal'),
    'displacement': ('tex_height', 'float', 'float', None),
    'ao': ('tex_ao', 'float', 'float', None),
    'opacity': ('tex_opacity', 'color3', 'color3f', 'opacity'),
}


class TextureError(ValueError):
    """A texture set that cannot be published as asked."""


###############################################################################
# Where sets live
###############################################################################
def set_uri(set_name: str, asset_uri: Optional[Uri] = None) -> Uri:
    """``export:/`` URI of the folder holding a set's versions: the asset's
    ``_textures/<set>`` when *asset_uri* is given, else the project
    library's ``textures/<set>``."""
    check_set_name(set_name)
    root = Uri.parse('export:/')
    if asset_uri is None:
        return root / LIBRARY_SEGMENT / set_name
    return root / asset_uri.segments / ASSET_SEGMENT / set_name


def sets_dir(asset_uri: Optional[Uri] = None) -> Path:
    """The folder holding every set of an asset (its ``_textures``), or of
    the project library when *asset_uri* is None."""
    from tumblepipe.api import api

    root = Uri.parse('export:/')
    uri = (root / LIBRARY_SEGMENT if asset_uri is None
           else root / asset_uri.segments / ASSET_SEGMENT)
    return Path(api.storage.resolve(uri))


def set_dir(set_name: str, asset_uri: Optional[Uri] = None) -> Path:
    """The folder holding one set's versions."""
    check_set_name(set_name)
    return sets_dir(asset_uri) / set_name


#: The variable texture paths in node parameters are written with.
EXPORT_VAR = 'TH_EXPORT_PATH'


def portable_root(root: Path, export_base: Optional[str]) -> str:
    """*root* as ``$TH_EXPORT_PATH/<rest>`` when under *export_base*,
    else empty (the absolute path is written)."""
    if not export_base:
        return ''
    base = str(export_base).replace('\\', '/').rstrip('/')
    text = str(root).replace('\\', '/')
    if base and text.casefold().startswith(base.casefold() + '/'):
        return f'${EXPORT_VAR}{text[len(base):]}'
    return ''


def portable_path(path) -> str:
    """*path* spelled ``$TH_EXPORT_PATH/...`` when it is under this
    session's export folder, else as an absolute posix path: what a node
    parameter should hold, so the scene follows the project to the farm."""
    import os

    return (portable_root(Path(path), os.environ.get(EXPORT_VAR))
            or Path(path).as_posix())


def name_from(text: str) -> str:
    """A valid set name made from *text* (a library asset id like
    ``wood_floor_deck`` becomes ``woodFloorDeck``); ``texture`` when
    nothing usable is left. A Windows device name (``Prn`` -> ``prn``)
    gets ``Texture`` appended: it can be no folder on Windows, and the set
    publish refused it."""
    words = [w for w in re.split(r'[^A-Za-z0-9]+', text) if w]
    words = [''.join(c for c in w if c.isascii()) for w in words]
    words = [w for w in words if w]
    if not words:
        return 'texture'
    name = words[0][0].lower() + words[0][1:] + ''.join(
        w[0].upper() + w[1:] for w in words[1:])
    if name.lower() in naming.WINDOWS_RESERVED:
        name += 'Texture'
    return name


def check_set_name(set_name: str) -> None:
    """Raise unless *set_name* follows the entity name rule (letters and
    digits), the same rule asset and shot names follow."""
    problem = naming.entity_name_problem(set_name)
    if problem:
        raise TextureError(
            f"{set_name!r} is not a valid texture set name: it {problem}")


def check_spelling(sets_dir: Path, set_name: str) -> None:
    """Refuse *set_name* when *sets_dir* already holds a set spelled the
    same but for case: on Windows and macOS the two are one folder, so the
    publish would land in the other set (and on Linux the pair would break
    the project for anyone opening it there). Entity names follow the same
    rule."""
    other = existing_spelling(sets_dir, set_name)
    if other != set_name:
        raise TextureError(
            f"a texture set named {other!r} already exists here; "
            f"publish to {other!r} or choose another name")


def existing_spelling(sets_dir: Path, set_name: str) -> str:
    """The spelling *sets_dir* already holds for *set_name* ignoring case,
    or *set_name* itself when it holds none (or holds it exactly)."""
    try:
        siblings = [p.name for p in Path(sets_dir).iterdir() if p.is_dir()]
    except (FileNotFoundError, NotADirectoryError):
        return set_name
    if set_name in siblings:
        return set_name
    folded = set_name.casefold()
    return next((s for s in sorted(siblings) if s.casefold() == folded),
                set_name)


def list_sets(sets_dir: Path) -> list[str]:
    """The set names under an asset's ``_textures`` or the library folder
    that have at least one finished version, sorted."""
    try:
        children = sorted(p for p in Path(sets_dir).iterdir() if p.is_dir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    return [
        child.name for child in children
        if naming.is_name_alphabet(child.name)
        and latest_version(child) is not None
    ]


def _is_texture_version(version_path: Path) -> bool:
    return is_finished_version(version_path) and read_version(version_path) is not None


def finished_versions(set_dir: Path) -> list[str]:
    """A set's finished version names, oldest first: those whose
    ``context.json`` reads as a texture version."""
    return [v.name for v in list_version_paths(set_dir)
            if _is_texture_version(v)]


def latest_version(set_dir: Path) -> Optional[str]:
    """The newest finished version's name, or None when none is finished.

    Unlike ``get_latest_version_path`` this never falls back to an
    unfinished folder: every texture version is written with its
    ``context.json``, so one without is a publish that failed, and one
    whose record cannot be read (truncated, hand-edited, a sync conflict)
    is read past the same way."""
    for version_path in reversed(list_version_paths(set_dir)):
        if _is_texture_version(version_path):
            return version_path.name
    return None


def pin_note(set_name: str, pinned: str, finished: list[str]) -> str:
    """The import node's one-line status for a set pinned at *pinned*
    (``''`` = nothing pinned yet), given its *finished* versions oldest
    first. Whenever the pin is not the newest, the note names the newest."""
    if not finished:
        return f'{set_name} has no finished version'
    latest = finished[-1]
    if not pinned:
        return f'{set_name}: press Update to read {latest}'
    if pinned not in finished:
        return f'{set_name} {pinned} is not published (latest {latest})'
    if pinned == latest:
        return f'{set_name} {pinned} (latest)'
    return f'{set_name} {pinned}: {latest} is newer, press Update'


###############################################################################
# Roles, names and colour spaces
###############################################################################
def colour_space(role: str, file_name: str) -> str:
    """The OCIO colour space a map of *role* is read in.

    Only the base colour holds colour; it is sRGB-encoded unless the file
    is a linear format. Roughness, metalness, normals, height, AO and
    opacity are data and must not be colour-converted.
    """
    if role not in ROLES:
        raise TextureError(f"unknown texture role {role!r}")
    if role != 'diffuse':
        return COLOUR_SPACE_DATA
    if Path(file_name).suffix.lower() in LINEAR_EXTENSIONS:
        return COLOUR_SPACE_LINEAR
    return COLOUR_SPACE_SRGB


def published_name(set_name: str, role: str, source: str) -> str:
    """The file name a map is published as: ``<set>_<role>`` plus the
    source's UDIM token (kept as ``.<UDIM>``) and lower-case extension."""
    name = Path(source).name
    suffix = Path(name).suffix.lower()
    tile = f'.{UDIM_TOKEN}' if UDIM_TOKEN in name else ''
    return f'{set_name}_{role}{tile}{suffix}'


def source_files(source: str) -> list[tuple[Path, str]]:
    """The files a map path names, each with the tile it is (``''`` for a
    single image), sorted by tile.

    A path with ``<UDIM>`` names every existing four-digit tile matching
    it; none is an error, as is a single image that does not exist."""
    path = Path(source)
    if UDIM_TOKEN not in path.name:
        if not path.is_file():
            raise TextureError(f"texture file not found: {path}")
        return [(path, '')]
    head, tail = path.name.split(UDIM_TOKEN, 1)
    tiles = []
    try:
        entries = sorted(path.parent.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        entries = []
    for entry in entries:
        name = entry.name
        if not (name.startswith(head) and name.endswith(tail)):
            continue
        tile = name[len(head):len(name) - len(tail)]
        if _TILE.match(tile) and entry.is_file():
            tiles.append((entry, tile))
    if not tiles:
        raise TextureError(f"no UDIM tiles found for {path}")
    return tiles


###############################################################################
# Publishing
###############################################################################
@dataclass(frozen=True)
class PublishedMap:
    role: str
    file: str          # published name, with ``<UDIM>`` for a tiled map
    colour_space: str
    tiles: tuple[str, ...]   # () for a single image
    source: str


def publish_set(
    set_dir: Path,
    set_name: str,
    maps: dict[str, str],
    *,
    owner: Optional[Uri] = None,
    source: str = '',
    user: str = '',
    producer: Optional[dict] = None,
    staging_root: Optional[Path] = None,
    timestamp: Optional[str] = None,
) -> Path:
    """Publish *maps* (role -> image path, ``<UDIM>`` allowed) as the next
    version of the set in *set_dir*; returns the new version folder.

    The version is staged in a temporary folder, then copied into place
    with ``context.json`` last, so it only counts once whole. *owner* is
    the asset entity URI, or None for a library set; *source* says where
    the maps came from (``cop``, ``library``, ``files``) for the record.
    """
    check_set_name(set_name)
    unknown = sorted(set(maps) - set(ROLES))
    if unknown:
        raise TextureError(f"unknown texture roles: {', '.join(unknown)}")
    if not maps:
        raise TextureError(f"texture set {set_name!r} has no maps to publish")
    check_spelling(Path(set_dir).parent, set_name)
    version_path = get_next_version_path(Path(set_dir))
    version_name = version_path.name
    with tempfile.TemporaryDirectory(
            prefix='th-texture-', dir=staging_root) as staging:
        staged = Path(staging)
        published = []
        for role in ROLES:
            if role not in maps:
                continue
            files = source_files(maps[role])
            name = published_name(set_name, role, maps[role])
            for src, tile in files:
                target = name.replace(UDIM_TOKEN, tile) if tile else name
                shutil.copy2(src, staged / target)
            published.append(PublishedMap(
                role=role,
                file=name,
                colour_space=colour_space(role, name),
                tiles=tuple(tile for _, tile in files if tile),
                source=str(maps[role]),
            ))
        layer_name = f'{set_name}.usda'
        (staged / layer_name).write_bytes(
            material_layer(set_name, published).encode('utf-8'))
        store_json(staged / VERSION_MARKER, set_context(
            set_name=set_name,
            version_name=version_name,
            owner=owner,
            maps=published,
            layer=layer_name,
            source=source,
            user=user,
            producer=producer or {},
            timestamp=timestamp or dt.datetime.now().isoformat(),
        ))
        copy_version_contents(staged, version_path, copy=shutil.copy2)
    return version_path


def set_context(*, set_name, version_name, owner, maps, layer, source,
                user, producer, timestamp) -> dict:
    """The ``context.json`` of a texture version."""
    return dict(
        kind=KIND,
        set=set_name,
        version=version_name,
        uri=str(owner) if owner is not None else None,
        layer=layer,
        maps=[
            dict(role=m.role, file=m.file, colorspace=m.colour_space,
                 tiles=list(m.tiles), source=m.source)
            for m in maps
        ],
        source=source,
        timestamp=timestamp,
        user=user,
        producer=producer,
    )


def matching_version(set_dir: Path, maps: dict[str, str]) -> Optional[Path]:
    """The newest finished version, when it was published from exactly
    *maps* (same roles, same source paths) and none of those sources has
    changed since; else None.

    Dropping the same library set on an asset twice reuses its version
    instead of publishing an identical one.
    """
    latest = latest_version(Path(set_dir))
    if latest is None:
        return None
    version_path = Path(set_dir) / latest
    data = read_version(version_path)
    if data is None:
        return None
    recorded = {m.get('role'): m.get('source') for m in data.get('maps', [])}
    if recorded != {role: str(path) for role, path in maps.items()}:
        return None
    published = (version_path / VERSION_MARKER).stat().st_mtime
    for path in maps.values():
        try:
            files = source_files(path)
        except TextureError:
            return None
        if any(src.stat().st_mtime > published for src, _ in files):
            return None
    return version_path


def copy_version(src_version: Path, dst_set_dir: Path, *, copied_from: str,
                 user: str = '', timestamp: Optional[str] = None) -> Path:
    """Copy the finished version *src_version* into *dst_set_dir* as its
    next version; returns the version folder the destination now uses.

    How a set moves between a project and a library (sharing it, or using a
    library set in a project). The version is self-contained (its material
    layer reads its images by relative path), so the files copy as they
    are; its ``context.json`` is rewritten with the new version name, who
    copied it and ``copied_from`` (*copied_from* names the source, e.g.
    ``library:Studio/bark/v0003``). When a finished version of the
    destination was already copied from that same source, that one is
    returned and nothing is copied: using a library set twice pins one copy.
    """
    src_version = Path(src_version)
    data = read_version(src_version)
    if data is None:
        raise TextureError(f'{src_version} is not a finished texture version')
    set_name = str(data.get('set') or Path(src_version).parent.name)
    dst_set_dir = Path(dst_set_dir)
    check_set_name(dst_set_dir.name)
    for name in reversed(finished_versions(dst_set_dir)):
        existing = read_version(dst_set_dir / name)
        if existing is not None and existing.get('copied_from') == copied_from:
            return dst_set_dir / name
    if set_name != dst_set_dir.name:
        raise TextureError(
            f'{src_version} is set {set_name!r}, not {dst_set_dir.name!r}')
    check_spelling(dst_set_dir.parent, dst_set_dir.name)
    version_path = get_next_version_path(dst_set_dir)
    record = dict(data)
    record.update(
        version=version_path.name,
        # A copy is a library set (the project's or a shared library's),
        # whoever owned the original.
        uri=None,
        copied_from=copied_from,
        timestamp=timestamp or dt.datetime.now().isoformat(),
        user=user or data.get('user', ''),
    )
    with tempfile.TemporaryDirectory(prefix='th-texture-') as staging:
        staged = Path(staging)
        for item in sorted(src_version.iterdir()):
            if item.name == VERSION_MARKER:
                continue
            if item.is_dir():
                shutil.copytree(item, staged / item.name)
            else:
                shutil.copy2(item, staged / item.name)
        store_json(staged / VERSION_MARKER, record)
        copy_version_contents(staged, version_path, copy=shutil.copy2)
    return version_path


def read_version(version_path: Path) -> Optional[dict]:
    """A finished texture version's ``context.json``, or None when the
    folder is unfinished, holds something else, or its record cannot be
    read (a scan must not fail on one broken file)."""
    try:
        data = load_json_if_exists(Path(version_path) / VERSION_MARKER)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get('kind') != KIND:
        return None
    return data


def version_maps(version_path: Path) -> dict[str, str]:
    """Role -> absolute path (``<UDIM>`` kept) of a finished version's
    maps; {} when the version is not a finished texture version."""
    data = read_version(version_path)
    if data is None:
        return {}
    return {
        m['role']: (Path(version_path) / m['file']).as_posix()
        for m in data.get('maps', [])
        if m.get('role') in ROLES and m.get('file')
    }


###############################################################################
# The material layer
###############################################################################
def material_layer(set_name: str, maps: list[PublishedMap]) -> str:
    """A USD layer (usda text) whose default prim ``/<set>`` is a MaterialX
    standard-surface ``Material`` reading *maps* by relative path.

    It is what Houdini's Material Library writes for the same network
    (checked against Houdini 22): ``ND_image_*`` readers with the file's
    ``colorSpace`` metadata, ``ND_normalmap_float`` before the normal input,
    and height through ``ND_displacement_float`` at scale 0, so a set with
    a height map does not move the surface until someone asks it to.
    """
    root = f'/{set_name}'
    by_role = {m.role: m for m in maps}
    lines = [
        '#usda 1.0',
        '(',
        f'    defaultPrim = "{set_name}"',
        '    metersPerUnit = 1',
        ')',
        '',
        f'def Material "{set_name}" (',
        '    apiSchemas = ["MaterialXConfigAPI"]',
        ')',
        '{',
        '    string config:mtlx:version = "1.39"',
        f'    token outputs:mtlx:surface.connect = <{root}/surf.outputs:out>',
    ]
    if 'displacement' in by_role:
        lines.append(
            f'    token outputs:mtlx:displacement.connect = '
            f'<{root}/disp.outputs:out>')
    lines += [
        '',
        '    def Shader "surf"',
        '    {',
        '        uniform token info:id = "ND_standard_surface_surfaceshader"',
    ]
    for role in ROLES:
        if role not in by_role:
            continue
        shader, _, usd_type, surf_input = _IMAGES[role]
        if surf_input is None:
            continue
        source = (f'{root}/normalmap.outputs:out' if role == 'normal'
                  else f'{root}/{shader}.outputs:out')
        lines.append(
            f'        {usd_type} inputs:{surf_input}.connect = <{source}>')
    lines += ['        token outputs:out', '    }']
    for role in ROLES:
        if role not in by_role:
            continue
        m = by_role[role]
        shader, signature, usd_type, _ = _IMAGES[role]
        lines += [
            '',
            f'    def Shader "{shader}"',
            '    {',
            f'        uniform token info:id = "ND_image_{signature}"',
            f'        asset inputs:file = @./{m.file}@ (',
            f'            colorSpace = "{m.colour_space}"',
            '        )',
            f'        {usd_type} outputs:out',
            '    }',
        ]
    if 'normal' in by_role:
        lines += [
            '',
            '    def Shader "normalmap"',
            '    {',
            '        uniform token info:id = "ND_normalmap_float"',
            f'        vector3f inputs:in.connect = <{root}/tex_normal.outputs:out>',
            '        vector3f outputs:out',
            '    }',
        ]
    if 'displacement' in by_role:
        lines += [
            '',
            '    def Shader "disp"',
            '    {',
            '        uniform token info:id = "ND_displacement_float"',
            f'        float inputs:displacement.connect = '
            f'<{root}/tex_height.outputs:out>',
            '        float inputs:scale = 0',
            '        token outputs:out',
            '    }',
        ]
    lines += ['}', '']
    return '\n'.join(lines)
