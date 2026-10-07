"""Pull the files an exported layer depends on into its version folder.

A layer is exported into a temp folder, then copied into its version folder
and composed from there on every machine. Anything it points at outside that
folder has to travel with it, or it composes empty / renders black after the
copy - silently, because the prim itself is still there.

Two ways that used to happen, both from assets dropped in from a library
outside the project (an asset browser download folder):

- A referenced file that itself references more files. A library wrapper
  (``Barrel_02_yup.usda``) references its geometry (``usd_2k_usd.usd``) and
  that references ``textures/``. Copying only the wrapper left the inner
  arcs dangling, so the asset published as empty Xforms.
- A texture on a prim in the exported layer (a dome light's HDRI). The USD
  ROP rewrites it relative to the temp export folder
  (``../../../library/x.exr``), which points nowhere once the layer moves.

Needs USD (``pxr``) but not Houdini.
"""

import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Where an externally composed file and its dependencies land, below the
# layer's folder. Asset attributes (textures) go to EXTERNAL_DIR directly.
EXTERNAL_DIR = 'external'

_LAYER_SUFFIXES = {'.usd', '.usda', '.usdc', '.usdz'}
# Texture tile tokens; a path carrying one names a set of files.
_TILE_TOKEN = re.compile(r'<UDIM>|<UVTILE>|%\(UDIM\)d', re.IGNORECASE)


@dataclass
class LocalizeResult:
    """What a localisation pass did, for the export to report."""
    # raw authored path -> path it was rewritten to
    rewritten: dict = field(default_factory=dict)
    # dependencies of an external file that do not exist on disk
    missing_layers: list = field(default_factory=list)
    missing_assets: list = field(default_factory=list)


def _looks_like_uri(asset_path: str) -> bool:
    # Same rule as pipe.usd._looks_like_uri: a 2+ letter scheme, so a
    # Windows drive letter is still a path.
    return bool(re.match(r'^[A-Za-z][A-Za-z0-9+.\-]+:', asset_path))


def _resolved(path: Path) -> Path | None:
    # Resolve the folder, not the name: a texture name may carry a <UDIM>
    # token, which Windows refuses as a file name.
    try:
        return path.parent.resolve() / path.name
    except OSError:
        return None


def _under_any(path: Path, roots) -> bool:
    resolved = _resolved(path)
    if resolved is None:
        return False
    return any(resolved.is_relative_to(root) for root in roots)


def _resolve_roots(roots) -> list[Path]:
    result = []
    for root in roots:
        try:
            result.append(Path(root).resolve())
        except OSError:
            continue
    return result


def _unique_name(name: str, used: set) -> str:
    if name not in used:
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while f'{stem}_{n}{suffix}' in used:
        n += 1
    return f'{stem}_{n}{suffix}'


def _iter_prim_specs(layer):
    """Every prim spec in ``layer``, variants included."""
    stack = list(layer.rootPrims)
    while stack:
        prim = stack.pop()
        yield prim
        stack.extend(prim.nameChildren.values())
        for variant_set in prim.variantSets.values():
            for variant in variant_set.variants.values():
                stack.append(variant.primSpec)


def composition_arc_paths(layer) -> set:
    """Raw asset paths of every sublayer, reference and payload in ``layer``."""
    paths = {str(p) for p in layer.subLayerPaths}
    for prim in _iter_prim_specs(layer):
        for arc_list in (prim.referenceList, prim.payloadList):
            for item in arc_list.GetAddedOrExplicitItems():
                asset_path = str(getattr(item, 'assetPath', '') or '')
                if asset_path:
                    paths.add(asset_path)
    return paths


def _is_layer_path(path: str) -> bool:
    return Path(path.split('[', 1)[0]).suffix.lower() in _LAYER_SUFFIXES


def _copy_closure(src: Path, layer_files, asset_files, target: Path) -> Path:
    """Copy ``src`` and its dependencies under ``target``; return src's copy.

    The files keep their layout relative to the deepest folder they share,
    so the relative paths between them hold as they are. Each copied layer
    then has every path that names a copied file rewritten relative to
    itself, which turns absolute paths into relative ones (and leaves the
    already-relative ones as they were). ``UsdUtils.LocalizeAsset`` is not
    used: it flattens each source folder into a numbered one and writes
    paths like ``1/diff.png`` that do not resolve from a layer in ``0/``.
    """
    from pxr import Sdf, UsdUtils

    files = {}
    for raw in [str(src), *layer_files, *asset_files]:
        path = _resolved(Path(raw))
        if path is not None:
            files[path] = None
    try:
        common = Path(os.path.commonpath([str(p.parent) for p in files]))
    except ValueError:
        # Different drives: keep the source's folder as the root and give
        # the stragglers their own subfolders.
        common = _resolved(Path(src)).parent
    outside = 0
    for path in files:
        if path.is_relative_to(common):
            files[path] = target / path.relative_to(common)
        else:
            files[path] = target / '_outside' / str(outside) / path.name
            outside += 1
    for path, copy in files.items():
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, copy)

    copied_dirs = {path.parent: copy.parent for path, copy in files.items()}
    for layer_file in layer_files:
        original = _resolved(Path(layer_file))
        copy_path = files.get(original)
        if copy_path is None:
            continue
        layer = Sdf.Layer.FindOrOpen(str(copy_path))
        if layer is None:
            continue

        def remap(p: str, original=original, copy_path=copy_path) -> str:
            if not p or _looks_like_uri(p):
                return p
            raw = Path(p)
            source = raw if raw.is_absolute() else original.parent / raw
            resolved_source = _resolved(source)
            if resolved_source is None:
                return p
            if _TILE_TOKEN.search(source.name):
                new_dir = copied_dirs.get(resolved_source.parent)
                if new_dir is None:
                    return p
                new = new_dir / source.name
            else:
                new = files.get(resolved_source)
                if new is None:
                    return p
            rel = Path(os.path.relpath(new, copy_path.parent)).as_posix()
            # Anchored, not a search path.
            return rel if rel.startswith('../') else f'./{rel}'

        UsdUtils.ModifyAssetPaths(layer, remap)
        layer.Save()
    return files[_resolved(Path(src))]


def localize_external_arcs(layer_path, skip_roots=()) -> LocalizeResult:
    """Copy every external reference/payload, with what it depends on, in.

    An arc whose file sits outside the layer's folder is copied in and
    rewritten to point at the copy. A self-contained file (no arcs or asset
    paths of its own - the ``asset_payload`` sidecar) lands next to the layer
    as before; a file with dependencies is localised with all of them into
    its own folder under ``EXTERNAL_DIR``, keeping its layout (see
    ``_copy_closure``).

    Files under ``skip_roots`` (versioned caches that publish by reference)
    and files that do not exist (left for the dangling-path guard) are left
    alone. Dependencies of a localised file that do not exist are reported
    in the result rather than raised here.
    """
    from pxr import Sdf, UsdUtils

    result = LocalizeResult()
    layer_path = Path(layer_path)
    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if layer is None:
        return result
    layer_dir = layer_path.parent
    layer_root = layer_dir.resolve()
    skip = _resolve_roots(skip_roots)
    try:
        used = {p.name for p in layer_dir.iterdir()}
    except OSError:
        used = set()
    external_root = layer_dir / EXTERNAL_DIR
    try:
        used_external = {p.name for p in external_root.iterdir()}
    except OSError:
        used_external = set()

    for prim in _iter_prim_specs(layer):
        for arc_list in (prim.referenceList, prim.payloadList):
            for item in arc_list.GetAddedOrExplicitItems():
                raw = str(getattr(item, 'assetPath', '') or '')
                if not raw or raw in result.rewritten or _looks_like_uri(raw):
                    continue
                src = Path(raw)
                abs_src = src if src.is_absolute() else layer_dir / src
                resolved = _resolved(abs_src)
                if resolved is None or resolved.is_relative_to(layer_root):
                    continue
                if _under_any(abs_src, skip):
                    continue
                if not abs_src.is_file():
                    continue

                layers, assets, unresolved = UsdUtils.ComputeAllDependencies(
                    str(abs_src)
                )
                if len(layers) <= 1 and not assets and not unresolved:
                    dest_name = _unique_name(abs_src.name, used)
                    try:
                        shutil.copy(abs_src, layer_dir / dest_name)
                    except OSError:
                        logger.warning('Could not localise %s', abs_src, exc_info=True)
                        continue
                    used.add(dest_name)
                    result.rewritten[raw] = dest_name
                    continue

                folder = _unique_name(abs_src.stem, used_external)
                target = external_root / folder
                try:
                    root_copy = _copy_closure(
                        abs_src, [layer.realPath for layer in layers],
                        [str(a) for a in assets], target,
                    )
                except OSError:
                    logger.warning('Could not localise %s', abs_src, exc_info=True)
                    shutil.rmtree(target, ignore_errors=True)
                    continue
                used_external.add(folder)
                result.rewritten[raw] = (
                    f'{EXTERNAL_DIR}/{folder}/'
                    + root_copy.relative_to(target).as_posix()
                )
                for missing in unresolved:
                    missing = str(missing)
                    if _is_layer_path(missing):
                        result.missing_layers.append(missing)
                    else:
                        result.missing_assets.append(missing)

    if result.rewritten:
        UsdUtils.ModifyAssetPaths(
            layer, lambda p: result.rewritten.get(p, p)
        )
        layer.Save()
        logger.info(
            'Localised %d external reference/payload file(s) into %s',
            len(result.rewritten), layer_dir,
        )
    return result


def _tile_glob(path: Path) -> list[Path]:
    pattern = _TILE_TOKEN.sub('*', path.name)
    try:
        return sorted(p for p in path.parent.glob(pattern) if p.is_file())
    except OSError:
        return []


def localize_external_assets(
    layer_path, project_roots=(), skip_roots=(),
) -> LocalizeResult:
    """Make the layer's own asset paths (textures, HDRIs) survive the move.

    For each asset-valued path in the layer that is not a composition arc
    (arcs belong to ``localize_external_arcs`` and the escaping-path guard):

    - inside the layer's folder: left alone;
    - under ``skip_roots`` (versioned caches): left alone;
    - under ``project_roots`` (shared project storage): pinned absolute, so
      a path the ROP made relative to the temp folder still resolves after
      the copy;
    - anywhere else, when the file exists: copied into ``EXTERNAL_DIR`` and
      rewritten to the copy. A ``<UDIM>``/``<UVTILE>`` path copies every
      tile it names;
    - missing: left alone and reported.
    """
    from pxr import Sdf, UsdUtils

    result = LocalizeResult()
    layer_path = Path(layer_path)
    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if layer is None:
        return result
    layer_dir = layer_path.parent
    layer_root = layer_dir.resolve()
    projects = _resolve_roots(project_roots)
    skip = _resolve_roots(skip_roots)
    arcs = composition_arc_paths(layer)
    external_root = layer_dir / EXTERNAL_DIR
    try:
        used = {p.name for p in external_root.iterdir()}
    except OSError:
        used = set()
    copied: dict = {}  # resolved source -> rewritten path

    def localize(raw: str) -> str:
        if not raw or raw in arcs or _looks_like_uri(raw):
            return raw
        if raw in result.rewritten:
            return result.rewritten[raw]
        src = Path(raw)
        abs_src = src if src.is_absolute() else layer_dir / src
        resolved = _resolved(abs_src)
        if resolved is None or resolved.is_relative_to(layer_root):
            return raw
        if any(resolved.is_relative_to(root) for root in skip):
            return raw
        tiled = bool(_TILE_TOKEN.search(abs_src.name))
        if any(resolved.is_relative_to(root) for root in projects):
            pinned = resolved.as_posix()
            if pinned != raw:
                result.rewritten[raw] = pinned
            return pinned
        files = _tile_glob(resolved) if tiled else (
            [resolved] if resolved.is_file() else []
        )
        if not files:
            result.missing_assets.append(raw)
            return raw
        key = resolved.as_posix()
        if key not in copied:
            name = _unique_name(resolved.name, used)
            external_root.mkdir(exist_ok=True)
            try:
                if tiled:
                    # Same prefix/suffix around the token, so the copied
                    # tiles still match the rewritten pattern.
                    head, tail = _TILE_TOKEN.split(resolved.name, 1)
                    new_head, new_tail = _TILE_TOKEN.split(name, 1)
                    for tile in files:
                        token = tile.name[len(head):len(tile.name) - len(tail)]
                        shutil.copy(tile, external_root / f'{new_head}{token}{new_tail}')
                else:
                    shutil.copy(files[0], external_root / name)
            except OSError:
                logger.warning('Could not localise %s', resolved, exc_info=True)
                return raw
            used.add(name)
            copied[key] = f'{EXTERNAL_DIR}/{name}'
        result.rewritten[raw] = copied[key]
        return copied[key]

    UsdUtils.ModifyAssetPaths(layer, localize)
    if result.rewritten:
        layer.Save()
        logger.info(
            'Localised %d external asset path(s) in %s',
            len(result.rewritten), layer_path,
        )
    return result
