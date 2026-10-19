"""The composition arcs a USD layer declares, and whether they survive publish.

A layer is exported into a temp folder, copied into its version folder and
composed from there on every machine. ``arc_paths`` lists the files it
composes (sublayers, references and payloads, including those authored
inside variants); ``find_escaping_layer_paths`` and
``find_dangling_layer_paths`` classify them for the export guard.

The walk needs USD (``pxr``); the classification is pure path logic.
"""

import re
from pathlib import Path
from typing import Iterable, Iterator, Union


class LayerOpenError(RuntimeError):
    """A layer to be analysed does not exist."""


def looks_like_uri(asset_path: str) -> bool:
    """True if ``asset_path`` carries a URI scheme the resolver handles
    (``entity:/``, ``op:/`` …) rather than a bare filesystem path.

    A scheme is two or more leading characters before ``:`` — this
    deliberately excludes single-letter Windows drive prefixes (``C:``)
    so absolute paths are treated as filesystem paths, not URIs.
    """
    return bool(re.match(r'^[A-Za-z][A-Za-z0-9+.\-]+:', asset_path))


def open_layer(layer_path: Union[Path, str]):
    """The ``Sdf.Layer`` at ``layer_path``.

    Raises ``LayerOpenError`` when there is none, and lets USD's own error
    through when the file does not parse.
    """
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(str(layer_path))
    if layer is None:
        raise LayerOpenError(f"no USD layer at {layer_path}")
    return layer


def prim_specs(layer) -> Iterator:
    """Every prim spec in ``layer``, those inside variants included."""
    stack = list(layer.rootPrims)
    while stack:
        prim = stack.pop()
        yield prim
        stack.extend(prim.nameChildren.values())
        for variant_set in prim.variantSets.values():
            for variant in variant_set.variants.values():
                stack.append(variant.primSpec)


def reference_paths(layer) -> Iterator[str]:
    """Asset paths of every reference and payload in ``layer``.

    ``GetAddedOrExplicitItems`` covers explicit, prepended and appended
    items alike. Internal references (no asset path) are skipped.
    """
    for prim in prim_specs(layer):
        for arc_list in (prim.referenceList, prim.payloadList):
            for item in arc_list.GetAddedOrExplicitItems():
                if item.assetPath:
                    yield str(item.assetPath)


def arc_paths(layer) -> list[str]:
    """Asset paths of every sublayer, reference and payload in ``layer``,
    in authored order, each once.

    Shallow: the arcs this layer declares, not those of the layers it
    points at.
    """
    paths = [str(p) for p in layer.subLayerPaths]
    paths.extend(reference_paths(layer))
    return list(dict.fromkeys(p for p in paths if p))


def _resolver_locates(asset_path: str) -> bool:
    """True if the active Ar resolver locates ``asset_path`` on its own.

    Bare relative arcs are USD *search paths*: the resolver looks them
    up in its search roots rather than next to the referencing layer.
    Houdini resolves ``houdini/usd/...`` against ``$HFS`` — Quick
    Surface Material authors its material library that way — so such an
    arc resolves on every machine with the same Houdini install and is
    portable without travelling in the layer folder.
    """
    from pxr import Ar

    return bool(str(Ar.GetResolver().Resolve(asset_path)))


def find_dangling_layer_paths(
    asset_paths: Iterable[str], layer_dir: Union[Path, str],
) -> list[str]:
    """Return composition asset paths that won't resolve at load time.

    Flags only filesystem paths whose target does not exist — the
    unambiguous "this layer composes geometry from a file that isn't
    there, so consumers import nothing" case (e.g. a payload left
    pointing at a machine-local scratch file):

    - URI-scheme arcs (``entity:/``, ``op:/`` …) are left to the
      resolver and never flagged.
    - Absolute paths are checked directly.
    - Relative paths are resolved against ``layer_dir`` (the layer's own
      directory). A bare relative path absent from ``layer_dir`` is
      additionally offered to the Ar resolver (search-path semantics —
      e.g. Houdini-shipped assets under ``$HFS``) before being flagged;
      ``./``/``../``-anchored paths are not, since USD anchors those to
      the layer itself.

    Returns the offending paths, in input order, each once.
    """
    dangling = []
    for p in dict.fromkeys(asset_paths):
        if not p or looks_like_uri(p):
            continue
        path = Path(p)
        if path.is_absolute():
            exists = path.exists()
        else:
            exists = (Path(layer_dir) / path).exists()
            if not exists and not p.startswith(('./', '../')):
                exists = _resolver_locates(p)
        if not exists:
            dangling.append(p)
    return dangling


def _is_workfile_cache(resolved: Path, storage_roots: list[Path]) -> bool:
    """True if ``resolved`` is a versioned cache under a workfile directory.

    Caches always land in a ``cache/`` directory inside a workfile's
    workspace on shared ``project:``/``proxy:`` storage
    (``<...>/<entity>/<dept>/cache/<name>/<version>/...``), immutable per
    version and visible on every machine — so such an arc publishes by
    reference safely no matter how it was loaded (a ``th::cache`` node, a
    raw ``sublayer``/reference of the ``.bgeo.sc`` files, a file SOP …).
    The anchor is a ``cache`` path component *below* a storage root: the
    H22 ``$HIP/usd/`` hollow-layer default lands under ``usd/`` (not
    ``cache/``) and stays flagged, and a machine-local scratch path is
    not under a storage root at all.
    """
    for root in storage_roots:
        if not resolved.is_relative_to(root):
            continue
        if any(part.lower() == 'cache' for part in resolved.relative_to(root).parts):
            return True
    return False


def find_escaping_layer_paths(
    asset_paths: Iterable[str], layer_dir: Union[Path, str],
    allowed_roots: Iterable[Union[Path, str]] = (),
    cache_storage_roots: Iterable[Union[Path, str]] = (),
) -> list[str]:
    """Return filesystem composition paths that resolve outside ``layer_dir``.

    A published layer travels as a folder (temp export dir → version
    folder → other machines), so every filesystem arc must stay inside
    the layer's own directory to survive the move. Existence is
    deliberately irrelevant: the classic offender — a layer save path
    left at H22's ``$HIP/usd/$OS.usd`` default — exists at export time
    and only dangles (or silently goes stale) after publish, which is
    exactly when ``find_dangling_layer_paths`` can no longer help.

    - URI-scheme arcs (``entity:/``, ``op:/`` …) are the resolver's
      business and never flagged.
    - Absolute paths are inherently non-portable and always flagged —
      even one pointing inside the export dir dies when the folder is
      copied to the version location — unless they resolve under one of
      ``allowed_roots`` (explicit versioned-cache locations) or land in a
      ``cache/`` directory below one of ``cache_storage_roots`` (the
      ``project:``/``proxy:`` storage roots), i.e. a versioned cache next
      to some workfile, which publishes by reference. Relative arcs are
      never exempt; they re-anchor when the layer folder moves.
    - Relative paths are flagged when they climb out of ``layer_dir``.

    Returns the offending paths, in input order, each once.
    """
    layer_root = Path(layer_dir).resolve()
    roots = [Path(root).resolve() for root in allowed_roots]
    storage_roots = [Path(root).resolve() for root in cache_storage_roots]
    escaping = []
    for p in dict.fromkeys(asset_paths):
        if not p or looks_like_uri(p):
            continue
        path = Path(p)
        if path.is_absolute():
            resolved = path.resolve()
            escapes = (
                not any(resolved.is_relative_to(root) for root in roots)
                and not _is_workfile_cache(resolved, storage_roots)
            )
        else:
            escapes = not (layer_root / path).resolve().is_relative_to(layer_root)
        if escapes:
            escaping.append(p)
    return escaping
