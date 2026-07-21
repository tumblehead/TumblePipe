#!/usr/bin/env python
"""Audit (and optionally clean) a project's playblast tree against the current
pathing convention.

Current convention (see tumblepipe.pipe.paths.render.get_*playblast_path):

    <root>/shots/<seq>/<shot>/<dept>/vNNNN.mp4              # non-layered
    <root>/shots/<seq>/<shot>/<dept>/<layer>/vNNNN.mp4     # layered (denoise/render/composite)

  seq/shot  -> from the project's entity.json (the authority)
  dept      -> a renderable shot department or a render department
  layer     -> one of the shot's variants, or 'default'/'slapcomp'
  version   -> a file named vNNNN.mp4 (v + exactly 4 digits)

Everything else is off-convention. The dominant legacy form is the old
version-as-directory layout (<...>/vNNNN/render.mp4) from a previous pipeline,
plus old per-variant trees keyed on the variant instead of the department.

Dry-run by default. Deletion is opt-in and per-category.

    hython audit_playblast_paths.py <config_dir> <playblast_root>
    hython audit_playblast_paths.py <config_dir> <playblast_root> \
        --delete --categories legacy_version_dir,legacy_variant_tree,bad_filename,bad_version_name
"""

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

VERSION_RE = re.compile(r'^v[0-9]{4}$')          # exactly v + 4 digits (paleindia)
VERSION_ISH_RE = re.compile(r'^v[0-9]+$')         # v + any digits (to spot bad ones)


def load_config(config_dir):
    cfg = Path(config_dir) / 'db'
    entity = json.loads((cfg / 'entity.json').read_text())
    depts = json.loads((cfg / 'departments.json').read_text())

    shots_node = entity['children']['shots']['children']
    seqs = {}
    variants = {}  # (seq, shot) -> set(variant names)
    for seq, sd in shots_node.items():
        shot_names = list(sd.get('children', {}).keys())
        seqs[seq] = set(shot_names)
        for shot, shd in sd['children'].items():
            vs = set(shd.get('properties', {}).get('variants', []))
            variants[(seq, shot)] = vs

    valid_depts = set(depts['children']['shots']['children'].keys())
    valid_depts |= set(depts['children']['render']['children'].keys())

    return seqs, variants, valid_depts


def dir_stats(path):
    """(file_count, total_bytes) under path (path may be a file)."""
    p = Path(path)
    if p.is_file():
        try:
            return 1, p.stat().st_size
        except OSError:
            return 1, 0
    n, total = 0, 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            n += 1
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return n, total


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f}{unit}'
        n /= 1024
    return f'{n:.1f}PB'


class Finding:
    __slots__ = ('path', 'category', 'note', 'files', 'bytes')

    def __init__(self, path, category, note):
        self.path = path
        self.category = category
        self.note = note
        self.files, self.bytes = dir_stats(path)


def classify(root, seqs, variants, valid_depts):
    """Return (findings, ok_count). Each finding is a removal unit: the
    shallowest off-convention path that contains no on-convention siblings."""
    root = Path(root)
    findings = []
    ok = [0]

    def child_kind(name):
        if VERSION_RE.match(name):
            return 'version'
        if VERSION_ISH_RE.match(name):
            return 'bad_version'
        return 'name'

    # ---- root: expect a single 'shots' dir ----
    for entry in sorted(root.iterdir()):
        if entry.name != 'shots':
            findings.append(Finding(entry, 'unknown_root',
                                    f'unexpected top-level entry (expected only shots/)'))
            continue
        # ---- shots: children are sequences ----
        for seq_dir in sorted(entry.iterdir()):
            if not seq_dir.is_dir() or seq_dir.name not in seqs:
                findings.append(Finding(seq_dir, 'unknown_seq',
                                        f'{seq_dir.name!r} is not a sequence in entity.json'))
                continue
            seq = seq_dir.name
            # ---- sequence: children are shots ----
            for shot_dir in sorted(seq_dir.iterdir()):
                if not shot_dir.is_dir() or shot_dir.name not in seqs[seq]:
                    findings.append(Finding(shot_dir, 'unknown_shot',
                                            f'{seq}/{shot_dir.name!r} is not a shot in entity.json'))
                    continue
                shot = shot_dir.name
                layers = variants.get((seq, shot), set()) | {'default', 'slapcomp'}
                _classify_shot(shot_dir, valid_depts, layers, findings, ok, child_kind)
    return findings, ok[0]


def _classify_shot(shot_dir, valid_depts, layers, findings, ok, child_kind):
    # role = shot: children should be department dirs
    for child in sorted(shot_dir.iterdir()):
        if child.is_dir() and child.name in valid_depts:
            _classify_dept(child, layers, findings, ok, child_kind)
            continue
        kind = child_kind(child.name)
        if kind == 'version':
            findings.append(Finding(child, 'legacy_version_dir',
                                    'old version-as-directory form (no department)'))
        elif kind == 'bad_version':
            findings.append(Finding(child, 'bad_version_name',
                                    f'{child.name!r} is not a valid version (need vNNNN)'))
        elif child.name in layers:
            findings.append(Finding(child, 'legacy_variant_tree',
                                    f'old per-variant tree keyed on {child.name!r} instead of a department'))
        else:
            findings.append(Finding(child, 'unknown_dept',
                                    f'{child.name!r} is not a valid department'))


def _classify_dept(dept_dir, layers, findings, ok, child_kind):
    # role = dept: valid children are vNNNN.mp4 files or layer subdirs
    for child in sorted(dept_dir.iterdir()):
        if child.is_file():
            if child.suffix == '.mp4' and VERSION_RE.match(child.stem):
                ok[0] += 1
            elif child.suffix == '.mp4':
                findings.append(Finding(child, 'bad_filename',
                                        f'mp4 not named vNNNN.mp4 ({child.name!r})'))
            else:
                findings.append(Finding(child, 'stray_file',
                                        f'unexpected file {child.name!r}'))
            continue
        # directory under a department
        if child.name in layers:
            _classify_layer(child, findings, ok)
        elif VERSION_RE.match(child.name):
            findings.append(Finding(child, 'legacy_version_dir',
                                    'old version-as-directory form under a department'))
        elif VERSION_ISH_RE.match(child.name):
            findings.append(Finding(child, 'bad_version_name',
                                    f'{child.name!r} is not a valid version'))
        else:
            findings.append(Finding(child, 'unknown_layer',
                                    f'{child.name!r} is not a variant/layer of this shot'))


def _classify_layer(layer_dir, findings, ok):
    # role = layer: valid children are vNNNN.mp4 files only
    for child in sorted(layer_dir.iterdir()):
        if child.is_file() and child.suffix == '.mp4' and VERSION_RE.match(child.stem):
            ok[0] += 1
        elif child.is_dir() and VERSION_RE.match(child.name):
            findings.append(Finding(child, 'legacy_version_dir',
                                    'old version-as-directory form under a layer'))
        else:
            cat = 'bad_filename' if child.is_file() else 'unknown_layer'
            findings.append(Finding(child, cat, f'unexpected {child.name!r} under a layer'))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('config_dir', help='project _config dir (has db/entity.json)')
    ap.add_argument('playblast_root', help='the playblast/ root to audit')
    ap.add_argument('--delete', action='store_true',
                    help='actually delete (default is dry-run)')
    ap.add_argument('--categories', default='',
                    help='comma-separated categories to delete (with --delete)')
    args = ap.parse_args()

    root = Path(args.playblast_root)
    if not root.is_dir():
        print(f'ERROR: playblast root not found: {root}', file=sys.stderr)
        return 2

    seqs, variants, valid_depts = load_config(args.config_dir)
    findings, ok = classify(root, seqs, variants, valid_depts)

    # Group by category
    by_cat = {}
    for f in findings:
        by_cat.setdefault(f.category, []).append(f)

    print('=' * 78)
    print(f'PLAYBLAST PATH AUDIT  ({root})')
    print('=' * 78)
    print(f'On-convention playblast files : {ok}')
    print(f'Off-convention removal units  : {len(findings)}')
    print(f'Valid departments             : {", ".join(sorted(valid_depts))}')
    print()

    order = ['legacy_version_dir', 'legacy_variant_tree', 'bad_version_name',
             'bad_filename', 'stray_file', 'unknown_layer', 'unknown_dept',
             'unknown_shot', 'unknown_seq', 'unknown_root']
    cats = [c for c in order if c in by_cat] + [c for c in by_cat if c not in order]

    total_files = total_bytes = 0
    for cat in cats:
        items = by_cat[cat]
        cf = sum(i.files for i in items)
        cb = sum(i.bytes for i in items)
        total_files += cf
        total_bytes += cb
        print(f'--- {cat}  ({len(items)} units, {cf} files, {human(cb)}) ---')
        print(f'    {items[0].note}')
        for i in items:
            rel = i.path.relative_to(root)
            print(f'      {rel}   [{i.files}f, {human(i.bytes)}]')
        print()

    print('=' * 78)
    print(f'TOTAL off-convention: {len(findings)} units, '
          f'{total_files} files, {human(total_bytes)}')
    print('=' * 78)

    if not args.delete:
        print('\nDRY-RUN. Nothing deleted. Re-run with '
              '--delete --categories <cat,cat,...> to remove.')
        return 0

    wanted = {c.strip() for c in args.categories.split(',') if c.strip()}
    if not wanted:
        print('\n--delete given but --categories is empty; nothing to do.',
              file=sys.stderr)
        return 2

    print(f'\nDELETING categories: {", ".join(sorted(wanted))}')
    removed = 0
    for f in findings:
        if f.category not in wanted:
            continue
        try:
            if f.path.is_dir():
                shutil.rmtree(f.path)
            else:
                f.path.unlink()
            removed += 1
            print(f'  removed {f.path.relative_to(root)}')
        except OSError as exc:
            print(f'  FAILED  {f.path.relative_to(root)}: {exc}', file=sys.stderr)
    print(f'\nDone. Removed {removed} unit(s).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
