"""Houdini-embedded asset paths in an exported stage, made into files beside it.

A LOP network can point an asset attribute at a file embedded in an HDA
(``opdef:/th::Lop/lookdev_studio::2.0?environment_texture.exr``) or in an
operator library (``oplib:``). Houdini resolves those; husk on the farm does
not, so a farm turntable rendered with no HDRI and no backdrop fabric ("failed
to load file", QA 2026-11-10). Before an exported stage leaves Houdini, each
such path is read once (``read_bytes``, i.e. ``hou.readBinaryFile``), written
under ``<root>/embedded/`` and the attribute pointed at the copy, relative to
its layer, so the export folder carries everything it needs.
"""

from hashlib import sha1
from pathlib import Path
import os
import re

SCHEMES = ('opdef:', 'oplib:')
FOLDER = 'embedded'
LAYER_SUFFIXES = ('.usd', '.usda', '.usdc')


def is_embedded(path) -> bool:
    return isinstance(path, str) and path.startswith(SCHEMES)


def embedded_file_name(path: str) -> str:
    """A file name for ``path``: its section name, made safe, after a short
    hash of the whole path (two HDAs may embed a like-named section)."""
    section = path.rsplit('?', 1)[-1] if '?' in path else path.rsplit('/', 1)[-1]
    section = re.sub(r'[^A-Za-z0-9._-]+', '_', section).strip('._') or 'file'
    digest = sha1(path.encode('utf-8')).hexdigest()[:8]
    return f'{digest}_{section}'


def _relative(target: Path, layer_dir: Path) -> str:
    rel = os.path.relpath(target, layer_dir).replace('\\', '/')
    return rel if rel.startswith('.') else f'./{rel}'


def localize_embedded_assets(root: Path, read_bytes) -> tuple[dict[str, str], dict[str, str]]:
    """Rewrite every embedded asset path in the layers under ``root``.

    Returns ``(written, failed)``: ``written`` maps each embedded path to
    its file (relative to ``root``); ``failed`` maps the paths ``read_bytes``
    could not read to why, which stay as they were.
    """
    from pxr import Sdf

    root = Path(root)
    written: dict[str, str] = {}
    failed: dict[str, str] = {}

    def localize(path: str) -> str | None:
        """The file for ``path`` (absolute), or None when it cannot be read."""
        if path in failed:
            return None
        if path not in written:
            try:
                data = read_bytes(path)
            except Exception as exc:  # noqa: BLE001 - reported, the path stays
                failed[path] = str(exc) or type(exc).__name__
                return None
            if data is None:
                failed[path] = 'nothing read'
                return None
            target = root / FOLDER / embedded_file_name(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bytes(data))
            written[path] = target.relative_to(root).as_posix()
        return str(root / written[path])

    def rewrite(value, layer_dir: Path):
        """``value`` with its embedded paths rewritten, or None if unchanged."""
        if isinstance(value, Sdf.AssetPath):
            if not is_embedded(value.path):
                return None
            target = localize(value.path)
            return None if target is None else Sdf.AssetPath(_relative(Path(target), layer_dir))
        if isinstance(value, Sdf.AssetPathArray):
            changed = False
            items = []
            for item in value:
                new = rewrite(item, layer_dir)
                changed |= new is not None
                items.append(new if new is not None else item)
            return Sdf.AssetPathArray(items) if changed else None
        return None

    layer_paths = sorted(
        p for p in root.rglob('*')
        if p.is_file() and p.suffix.lower() in LAYER_SUFFIXES
        and FOLDER not in p.relative_to(root).parts[:1]
    )
    for layer_path in layer_paths:
        layer = Sdf.Layer.FindOrOpen(str(layer_path))
        if layer is None:
            continue
        layer_dir = layer_path.parent
        edits = []

        def visit(spec_path, layer=layer, layer_dir=layer_dir, edits=edits):
            if not spec_path.IsPropertyPath():
                return
            spec = layer.GetAttributeAtPath(spec_path)
            if spec is None:
                return
            if spec.HasDefaultValue():
                new = rewrite(spec.default, layer_dir)
                if new is not None:
                    edits.append((spec_path, None, new))
            for time in layer.ListTimeSamplesForPath(spec_path) or ():
                new = rewrite(layer.QueryTimeSample(spec_path, time), layer_dir)
                if new is not None:
                    edits.append((spec_path, time, new))

        layer.Traverse(Sdf.Path.absoluteRootPath, visit)
        if not edits:
            continue
        for spec_path, time, value in edits:
            if time is None:
                layer.GetAttributeAtPath(spec_path).default = value
            else:
                layer.SetTimeSample(spec_path, time, value)
        layer.Save()
    return written, failed
