"""Write a plan as zip parts with a manifest; verify and restore archives.

**Format.** One or more ordinary ZIP64 files, so a recipient opens them with
what their OS already has. Files keep their layout under each root's prefix
(``HideAndReek/…``, ``HideAndReek_render/…``, see ``roots``). Formats that
are already compressed (USD crate, EXR, ``.bgeo.sc``, VDB, movies, images)
are stored; text and ``.hip`` are deflated.

With ``part_size`` the archive is split into independent, individually
valid zips (``<name>.part001.zip`` …): a file never spans two parts, so any
part opens on its own.

**Manifest.** ``ARCHIVE/manifest.json`` in the last part, and the same file
beside the parts as ``<name>.manifest.json``: what the archive is (profile,
when, by whom, from where), the tool versions the project was made with, the
prefix of every root, every file with its size, mtime and SHA-256 and the
part holding it, a summary of what was left out and why, and the derived
outputs a rebuild could make again (``derived``). ``ARCHIVE/README.txt``
says the same for a person.

Nothing is written to the project. ``restore`` leaves the manifest in the
restored project as ``_archive/<name>.manifest.json``. ``create`` writes to ``*.partial`` files
and renames them only when the whole archive is done, so a cancelled or
failed run never leaves something that looks like an archive.
"""

from dataclasses import dataclass, field
from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import platform
import zipfile

from tumblepipe.archive.inventory import Item, Kind
from tumblepipe.archive.longpath import long_path
from tumblepipe.archive.plan import Plan, Reason
from tumblepipe.pipe import provenance

FORMAT = 'tumblepipe-archive'
FORMAT_VERSION = 1
MANIFEST_NAME = 'ARCHIVE/manifest.json'
README_NAME = 'ARCHIVE/README.txt'
MANIFEST_SUFFIX = '.manifest.json'

_CHUNK = 1 << 20
_ZIP64_LIMIT = (1 << 31) - 1
_STORED_SUFFIXES = (
    '.usdc', '.usdz', '.exr', '.bgeo.sc', '.vdb', '.sc', '.gz', '.zst', '.zip',
    '.7z', '.rar', '.mp4', '.mov', '.mkv', '.avi', '.mp3', '.wav', '.jpg',
    '.jpeg', '.png', '.webp', '.tif', '.tiff', '.rat', '.tx', '.abc', '.drp',
)
_USD_CRATE_MAGIC = b'PXR-USDC'


class ArchiveError(RuntimeError):
    """The archive cannot be written, read, verified or restored."""


class Cancelled(ArchiveError):
    """The caller's ``cancel`` callback asked to stop."""


@dataclass
class Progress:
    files_done: int = 0
    files_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    current: str = ''


@dataclass
class VerifyResult:
    checked: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


# ── names and parts ────────────────────────────────────────────────────────


def archive_name(item: Item, plan: Plan) -> str:
    """Where ``item`` goes inside the archive."""
    root = plan.inventory.roots.get(item.root)
    return f'{root.prefix}/{item.rel}'


def part_paths(out_path: Path, count: int) -> list[Path]:
    """The part files of an archive written to ``out_path``."""
    out_path = Path(out_path)
    if count <= 1:
        return [out_path]
    stem = out_path.name[:-len('.zip')] if out_path.name.endswith('.zip') else out_path.name
    return [out_path.with_name(f'{stem}.part{index:03d}.zip') for index in range(1, count + 1)]


def manifest_path(out_path: Path) -> Path:
    out_path = Path(out_path)
    stem = out_path.name[:-len('.zip')] if out_path.name.endswith('.zip') else out_path.name
    return out_path.with_name(stem + MANIFEST_SUFFIX)


def _compress_type(path: Path, name: str) -> int:
    lower = name.lower()
    if lower.endswith(_STORED_SUFFIXES):
        return zipfile.ZIP_STORED
    if lower.endswith('.usd'):
        try:
            with path.open('rb') as handle:
                if handle.read(len(_USD_CRATE_MAGIC)) == _USD_CRATE_MAGIC:
                    return zipfile.ZIP_STORED
        except OSError:
            pass
    return zipfile.ZIP_DEFLATED


def _zip_time(mtime: float) -> tuple:
    stamp = dt.datetime.fromtimestamp(mtime)
    if stamp.year < 1980:
        stamp = dt.datetime(1980, 1, 1)
    return stamp.timetuple()[:6]


def is_safe_name(name: str) -> bool:
    """A name that stays inside the folder it is extracted to."""
    if not name or name.startswith(('/', '\\')) or '\\' in name:
        return False
    # Split the raw name: PurePosixPath would quietly drop '' and '.' parts.
    parts = name.split('/')
    if ':' in parts[0]:
        return False
    return all(part not in ('', '.', '..') for part in parts)


# ── manifest ───────────────────────────────────────────────────────────────


def _derived_entries(plan: Plan) -> list[dict]:
    """Left-out outputs a rebuild could make again, one per version."""
    entries: dict[str, dict] = {}
    contexts = plan.inventory.cache_contexts
    for decision in plan.excluded():
        item = decision.item
        if item.unit is None:
            continue
        key = f'{item.root}:{item.unit}'
        if key in entries:
            continue
        if item.kind == Kind.CACHE and decision.reason in (Reason.CACHE, Reason.CACHE_OLD):
            context = contexts.get(key, {})
            entries[key] = dict(
                kind='cache', root=item.root, unit=item.unit,
                version=item.version, context=context,
            )
        elif item.kind == Kind.RENDER and decision.reason == Reason.RENDER:
            entries[key] = dict(
                kind='render', root=item.root, unit=item.unit, version=item.version,
            )
    # Renders: only the latest version of each series is worth re-making.
    latest_render: dict[str, int] = {}
    for entry in entries.values():
        if entry['kind'] == 'render':
            series = entry['unit'].rsplit('/', 1)[0]
            latest_render[series] = max(latest_render.get(series, 0), entry['version'])
    return [
        entry for entry in entries.values()
        if entry['kind'] != 'render'
        or entry['version'] == latest_render[entry['unit'].rsplit('/', 1)[0]]
    ]


def _external_references(plan: Plan) -> dict:
    """What ``archive.references`` found, for the manifest."""
    report = plan.references
    if report is None:
        return {}
    return dict(
        outside={path: sorted(files) for path, files in sorted(report.outside.items())},
        excluded={path: sorted(files) for path, files in sorted(report.excluded.items())},
        absolute_inside=sorted(report.absolute_inside),
    )


def _config_version(plan: Plan) -> int | None:
    path = plan.inventory.roots.config_path / 'version.json'
    try:
        return int(json.loads(path.read_text(encoding='utf-8'))['version'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _readme(manifest: dict) -> str:
    tools = manifest['tools']
    lines = [
        f"TumblePipe archive of project '{manifest['project']['name']}'",
        '',
        f"Profile:   {manifest['profile']}",
        f"Created:   {manifest['created']} by {manifest['created_by'] or 'unknown'}",
        f"Files:     {len(manifest['files'])}",
        f"Parts:     {', '.join(manifest['parts'])}",
        '',
        'Made with:',
        f"  TumblePipe {', '.join(tools['tumblepipe_versions']) or 'not recorded (the workfiles predate it; use the archiving version)'}",
        f"  Houdini    {', '.join(tools['houdini_versions']) or 'unknown'}",
        f"  Archived by TumblePipe {tools['archiver']}",
        '',
        'To open the work: unpack every part into one folder, keeping the',
        'top-level folders side by side. Install the TumblePipe version above',
        '(TumbleTrove: tumblehead/tumblepipe) and point a project at the',
        f"'{manifest['project']['name']}' folder.",
        '',
        'Left out (re-made from the workfiles, or not pipeline data):',
    ]
    for reason, row in sorted(manifest['excluded'].items()):
        lines.append(f"  {row['files']:>7} files  {row['bytes']:>15,} bytes  {reason}")
    lines += [
        '',
        'ARCHIVE/manifest.json lists every file with its SHA-256.',
    ]
    return '\n'.join(lines) + '\n'


# ── create ─────────────────────────────────────────────────────────────────


def create(
    plan: Plan,
    out_path: Path,
    *,
    part_size: int | None = None,
    progress=None,
    cancel=None,
) -> dict:
    """Write ``plan`` to ``out_path`` (``.zip``); returns the manifest.

    ``progress(Progress)`` is called per file; ``cancel()`` returning true
    stops with :class:`Cancelled` and removes everything written.
    """
    out_path = Path(out_path)
    if not out_path.name.endswith('.zip'):
        raise ArchiveError(f'{out_path}: the archive name must end in .zip')
    roots = plan.inventory.roots
    if any(_is_within(out_path.parent.resolve(), root.path) for root in roots.roots):
        raise ArchiveError(
            f'{out_path} is inside the project; write the archive somewhere else'
        )
    items = sorted(plan.included(), key=lambda item: archive_name(item, plan))
    state = Progress(files_total=len(items), bytes_total=sum(i.size for i in items))

    written: list[Path] = []          # .partial files, in part order
    files: list[dict] = []
    houdini_versions: set[str] = set()
    tumblepipe_versions: set[str] = set()
    skipped: list[str] = []

    def open_part():
        index = len(written) + 1
        path = out_path.with_name(f'{out_path.name}.{index:03d}.partial')
        if path.exists():
            path.unlink()
        written.append(path)
        return zipfile.ZipFile(path, 'w', allowZip64=True)

    archive = open_part()
    part_bytes = 0
    part_files = 0
    try:
        for item in items:
            if cancel is not None and cancel():
                raise Cancelled('cancelled')
            name = archive_name(item, plan)
            source = long_path(item.path(roots))
            # By file count, not bytes: a part holding only empty files has 0
            # bytes, and the next large file must still start a new part.
            if part_size and part_files and part_bytes + item.size > part_size:
                archive.close()
                archive = open_part()
                part_bytes = 0
                part_files = 0
            state.current = name
            try:
                stat = source.stat()
                info = zipfile.ZipInfo(name, _zip_time(stat.st_mtime))
                info.compress_type = _compress_type(source, name)
                info.external_attr = 0o644 << 16
                digest = hashlib.sha256()
                size = 0
                capture = (
                    item.kind == Kind.WORKFILE_META and '/_context/' in f'/{item.rel}'
                )
                captured = bytearray()
                with source.open('rb') as reader, archive.open(
                    info, 'w', force_zip64=stat.st_size > _ZIP64_LIMIT
                ) as writer:
                    while chunk := reader.read(_CHUNK):
                        digest.update(chunk)
                        writer.write(chunk)
                        size += len(chunk)
                        if capture:
                            captured += chunk
                        state.bytes_done += len(chunk)
            except OSError as error:
                skipped.append(f'{name}: {error}')
                continue
            if capture:
                _collect_versions(captured, houdini_versions, tumblepipe_versions)
            files.append(dict(
                path=name, size=size, mtime=stat.st_mtime,
                sha256=digest.hexdigest(), kind=item.kind, part=len(written),
            ))
            part_bytes += size
            part_files += 1
            state.files_done += 1
            if progress is not None:
                progress(state)

        part_count = len(written)
        final_parts = part_paths(out_path, part_count)
        manifest = dict(
            format=FORMAT,
            format_version=FORMAT_VERSION,
            profile=plan.options.profile,
            options=dict(
                with_caches=plan.options.with_caches,
                with_renders=plan.options.with_renders,
            ),
            created=dt.datetime.now().isoformat(timespec='seconds'),
            created_by=os.environ.get('TH_USER') or os.environ.get('TT_USER_NAME'),
            host=platform.node(),
            project=dict(
                name=roots.name,
                path=roots.project_path.as_posix(),
                config=next(r.prefix for r in roots.roots if r.purpose == 'config'),
                config_version=_config_version(plan),
            ),
            roots={root.purpose: root.prefix for root in roots.roots},
            tools=dict(
                archiver=provenance.tumblepipe_version(),
                tumblepipe_versions=sorted(tumblepipe_versions),
                houdini_versions=sorted(houdini_versions),
            ),
            parts=[path.name for path in final_parts],
            files=files,
            skipped=skipped,
            excluded=plan.excluded_by_reason(),
            warnings=list(plan.warnings),
            external_references=_external_references(plan),
            derived=_derived_entries(plan),
        )
        text = json.dumps(manifest, indent=1)
        archive.writestr(README_NAME, _readme(manifest), compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr(MANIFEST_NAME, text, compress_type=zipfile.ZIP_DEFLATED)
        archive.close()
        for path in final_parts + [manifest_path(out_path)]:
            if path.exists():
                raise ArchiveError(f'{path} already exists')
        for partial, final in zip(written, final_parts):
            os.replace(partial, final)
        manifest_path(out_path).write_text(text, encoding='utf-8')
        return manifest
    except BaseException:
        archive.close()
        for path in written:
            try:
                path.unlink()
            except OSError:
                pass
        raise


def _collect_versions(data: bytes, houdini: set, tumblepipe: set) -> None:
    try:
        record = json.loads(bytes(data).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return
    if not isinstance(record, dict):
        return
    for key, target in (('houdini_version', houdini), ('tumblepipe_version', tumblepipe)):
        value = record.get(key)
        if isinstance(value, str) and value and value != provenance.UNKNOWN:
            target.add(value)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


# ── reading ────────────────────────────────────────────────────────────────


def load_manifest(archive_path: Path) -> tuple[dict, list[Path]]:
    """An archive's manifest and its part files, from any one of them.

    ``archive_path`` may be the ``.zip`` (or any ``.partNNN.zip``) or the
    ``.manifest.json`` beside it.
    """
    archive_path = Path(archive_path)
    directory = archive_path.parent
    name = archive_path.name
    manifest = None
    if name.endswith(MANIFEST_SUFFIX):
        manifest = json.loads(archive_path.read_text(encoding='utf-8'))
    else:
        base = name[:-len('.zip')] if name.endswith('.zip') else name
        if '.part' in base and base.rsplit('.part', 1)[1].isdigit():
            base = base.rsplit('.part', 1)[0]
        sidecar = directory / (base + MANIFEST_SUFFIX)
        if sidecar.is_file():
            manifest = json.loads(sidecar.read_text(encoding='utf-8'))
        else:
            # No sidecar: the manifest is in the last part.
            candidates = sorted(directory.glob(f'{base}.part*.zip')) or [directory / f'{base}.zip']
            with zipfile.ZipFile(candidates[-1]) as last:
                manifest = json.loads(last.read(MANIFEST_NAME).decode('utf-8'))
    if manifest.get('format') != FORMAT:
        raise ArchiveError(f'{archive_path} is not a TumblePipe archive')
    if manifest.get('format_version', 0) > FORMAT_VERSION:
        raise ArchiveError(
            f'{archive_path} is format v{manifest.get("format_version")}; '
            f'this TumblePipe reads up to v{FORMAT_VERSION}'
        )
    parts = [directory / part for part in manifest['parts']]
    missing = [str(part) for part in parts if not part.is_file()]
    if missing:
        raise ArchiveError(f'missing archive part(s): {", ".join(missing)}')
    return manifest, parts


def verify(archive_path: Path, *, progress=None, cancel=None) -> VerifyResult:
    """Re-read every file and check it against the manifest's hash."""
    manifest, parts = load_manifest(archive_path)
    result = VerifyResult()
    state = Progress(
        files_total=len(manifest['files']),
        bytes_total=sum(entry['size'] for entry in manifest['files']),
    )
    by_part: dict[int, list[dict]] = {}
    for entry in manifest['files']:
        by_part.setdefault(entry['part'], []).append(entry)
    for index, part in enumerate(parts, start=1):
        with zipfile.ZipFile(part) as archive:
            names = set(archive.namelist())
            for entry in by_part.get(index, ()):
                if cancel is not None and cancel():
                    raise Cancelled('cancelled')
                state.current = entry['path']
                if entry['path'] not in names:
                    result.problems.append(f'{entry["path"]}: missing from {part.name}')
                    continue
                digest = hashlib.sha256()
                try:
                    with archive.open(entry['path']) as reader:
                        while chunk := reader.read(_CHUNK):
                            digest.update(chunk)
                            state.bytes_done += len(chunk)
                except (zipfile.BadZipFile, OSError) as error:
                    result.problems.append(f'{entry["path"]}: {error}')
                    continue
                if digest.hexdigest() != entry['sha256']:
                    result.problems.append(f'{entry["path"]}: content does not match its hash')
                result.checked += 1
                state.files_done += 1
                if progress is not None:
                    progress(state)
    return result


def renamed_prefix(prefix: str, old: str, new: str) -> str:
    """``prefix`` for a project restored under the name ``new``.

    The project folder and everything in it follow the name, and so do the
    roots beside it that ``storage.json`` names after the project
    (``{project_name}_render`` …). Other prefixes are unchanged.
    """
    if prefix == old or prefix.startswith(old + '/'):
        return new + prefix[len(old):]
    if prefix.startswith(old + '_') and '/' not in prefix:
        return new + prefix[len(old):]
    return prefix


def restore_path(name: str, manifest: dict, dest: Path, new_name: str | None) -> Path:
    if not is_safe_name(name):
        raise ArchiveError(f'unsafe path in archive: {name!r}')
    if new_name:
        old = manifest['project']['name']
        prefixes = sorted(manifest['roots'].values(), key=len, reverse=True)
        for prefix in prefixes:
            if name == prefix or name.startswith(prefix + '/'):
                name = renamed_prefix(prefix, old, new_name) + name[len(prefix):]
                break
    return Path(dest).joinpath(*name.split('/'))


def restore(
    archive_path: Path,
    dest: Path,
    *,
    new_name: str | None = None,
    overwrite: bool = False,
    progress=None,
    cancel=None,
) -> dict:
    """Unpack an archive into ``dest``, checking every file's hash.

    Refuses before writing anything when a file would be overwritten (unless
    ``overwrite``). Each file is written beside its target and renamed into
    place only once its hash matched. Returns the manifest.
    """
    manifest, parts = load_manifest(archive_path)
    dest = Path(dest)
    if new_name is not None and not is_safe_name(new_name):
        raise ArchiveError(f'not a usable project name: {new_name!r}')
    targets = {
        entry['path']: restore_path(entry['path'], manifest, dest, new_name)
        for entry in manifest['files']
    }
    if not overwrite:
        clashes = [str(path) for path in targets.values() if long_path(path).exists()]
        if clashes:
            shown = ', '.join(clashes[:5]) + (' …' if len(clashes) > 5 else '')
            raise ArchiveError(f'{len(clashes)} file(s) already exist: {shown}')
    state = Progress(
        files_total=len(manifest['files']),
        bytes_total=sum(entry['size'] for entry in manifest['files']),
    )
    by_part: dict[int, list[dict]] = {}
    for entry in manifest['files']:
        by_part.setdefault(entry['part'], []).append(entry)
    try:
        for index, part in enumerate(parts, start=1):
            with zipfile.ZipFile(part) as archive:
                for entry in by_part.get(index, ()):
                    if cancel is not None and cancel():
                        raise Cancelled('cancelled')
                    target = long_path(targets[entry['path']])
                    state.current = entry['path']
                    target.parent.mkdir(parents=True, exist_ok=True)
                    partial = target.with_name(target.name + '.partial')
                    digest = hashlib.sha256()
                    with archive.open(entry['path']) as reader, partial.open('wb') as writer:
                        while chunk := reader.read(_CHUNK):
                            digest.update(chunk)
                            writer.write(chunk)
                            state.bytes_done += len(chunk)
                    if digest.hexdigest() != entry['sha256']:
                        partial.unlink()
                        raise ArchiveError(f'{entry["path"]}: content does not match its hash')
                    os.replace(partial, target)
                    os.utime(target, (entry['mtime'], entry['mtime']))
                    state.files_done += 1
                    if progress is not None:
                        progress(state)
    except Cancelled:
        raise
    except (OSError, zipfile.BadZipFile, ArchiveError) as error:
        raise ArchiveError(
            f'{error} — {state.files_done} of {state.files_total} file(s) were '
            'restored before this; run the restore again with --overwrite to '
            'finish it'
        ) from error
    keep_manifest(manifest, dest, new_name, Path(archive_path))
    return manifest


def keep_manifest(manifest: dict, dest: Path, new_name: str | None, archive_path: Path) -> Path:
    """Leave the manifest in the restored project's ``_archive`` folder.

    It is the record of where the project came from, and what
    ``archive.rebuild`` reads to re-make the caches the archive left out.
    """
    from tumblepipe.archive.rebuild import RESTORED_DIR
    project = Path(dest) / (new_name or manifest['project']['name'])
    folder = long_path(project / RESTORED_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    name = archive_path.name
    for suffix in (MANIFEST_SUFFIX, '.zip'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    if '.part' in name and name.rsplit('.part', 1)[1].isdigit():
        name = name.rsplit('.part', 1)[0]
    path = folder / f'{name}{MANIFEST_SUFFIX}'
    path.write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return path
