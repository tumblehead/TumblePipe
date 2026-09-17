"""Verify import_shot's "Exclude Asset Departments" against a live project.

The parm existed until the Dec 2025 rewrite (legacy ``asset_departments``,
applied as Configure Stage mute *patterns* — which USD never honoured: a
mute path is an exact layer identifier, ``*`` is literal). The rewrite
dropped it without replacement, so an artist had no way to leave, say, a
character's lookdev out of a shot.

This drives the compiled HDA end to end: import a staged shot, exclude an
asset department, and ask the composed stage whether that department's
layers are still used.

Needs a licensed hython, the tumbleResolver plugin and project data:

    PXR_PLUGINPATH_NAME=<repo>/resolver/houdini22/tumbleResolver/resources \\
    PATH=<repo>/resolver/houdini22/tumbleResolver/lib:$PATH \\
    VERIFY_PROJECT=W:/projects/paleindia \\
    VERIFY_SHOT=entity:/shots/000/sh020_Clash \\
    hython scripts/verify_import_shot_asset_exclude.py

TH_* are set here, before tumblepipe is imported: Houdini packages loaded
at startup can point them at another install.

Checks:
  1. With nothing excluded, the shot composes asset layers of the
     department (otherwise the run proves nothing) and nothing is muted.
  2. Excluding it mutes exactly those layers — every asset one, no shot
     one — and none of them is used by the composed stage.
  3. The excluded department's prims (materials, for lookdev) are gone.
  4. The node comment reports what was muted.
  5. Clearing the exclusion removes the mute node and restores the stage.
  6. The getter/setter pair round-trips and rejects unknown names.
"""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECT = os.environ.get('VERIFY_PROJECT', 'W:/projects/paleindia')
SHOT = os.environ.get('VERIFY_SHOT', 'entity:/shots/000/sh020_Clash')
DEPARTMENT = os.environ.get('VERIFY_DEPARTMENT', 'lookdev')

os.environ.update(
    TH_PROJECT_PATH=PROJECT,
    TH_CONFIG_PATH=f'{PROJECT}/_config',
    TH_EXPORT_PATH=f'{PROJECT}/export',
    TH_PIPELINE_PATH=str(REPO),
)
sys.path.insert(0, str(REPO / 'python'))

import hou  # noqa: E402

from tumblepipe.pipe.usd import parse_entity_sublayer_uri  # noqa: E402
from tumblepipe.pipe.houdini.lops import import_shot  # noqa: E402
from tumblepipe.pipe.houdini.util import uri_to_prim_path  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402

FAILURES = []


def check(label, ok, detail=''):
    tag = 'PASS' if ok else 'FAIL'
    print(f'[{tag}] {label}' + (f'  ({detail})' if detail else ''))
    if not ok:
        FAILURES.append(label)


def install_hda():
    path = str(REPO / 'otls' / 'lop_th.import_shot.1.0.hda')
    hou.hda.installFile(path)
    for definition in hou.hda.definitionsInFile(path):
        definition.setIsPreferred(True)


def department_layers(stage, scope):
    """Used layer ids for DEPARTMENT, split by entity kind."""
    ids = []
    for layer in stage.GetUsedLayers():
        ref = parse_entity_sublayer_uri(layer.identifier)
        if ref is None or ref.department != DEPARTMENT:
            continue
        if ref.base.startswith(f'entity:/{scope}/'):
            ids.append(layer.identifier)
    return sorted(ids)


def mtl_prims(stage, layer_ids):
    """Prims under the ``mtl`` scope of the assets owning ``layer_ids``.

    Lookdev owns an asset's ``<asset>/mtl``. Scoped to those assets' roots
    and matched as a whole segment: shot departments author ``mtl`` scopes
    of their own (light's karmafogbox), and shader networks are full of
    names like ``kma_pyrosmokecolor1`` that a substring test counts.
    """
    roots = tuple(
        uri_to_prim_path(Uri.parse_unsafe(parse_entity_sublayer_uri(i).base))
        + '/mtl'
        for i in layer_ids
    )
    return sum(
        1 for prim in stage.Traverse()
        if any(prim.GetPath().pathString == root
               or prim.GetPath().pathString.startswith(root + '/')
               for root in roots)
    )


def main():
    install_hda()
    raw = hou.node('/stage').createNode('th::import_shot::1.0', 'verify_shot')
    node = import_shot.ImportShot(raw)
    check('HDA carries the asset_departments parm',
          raw.parm('asset_departments') is not None)
    if FAILURES:
        return

    raw.parm('entity').set(SHOT)
    raw.parm('department').set('none')
    raw.parm('version').set(os.environ.get('VERIFY_VERSION', 'latest'))

    # 1. Baseline
    node.execute()
    stage = raw.stage()
    asset_ids = department_layers(stage, 'assets')
    shot_ids = department_layers(stage, 'shots')
    baseline_mtl = mtl_prims(stage, asset_ids)
    check(f'baseline composes asset {DEPARTMENT} layers', bool(asset_ids),
          f'{len(asset_ids)} layers, {baseline_mtl} mtl prims')
    check('baseline mutes nothing', not stage.GetMutedLayers())
    check('baseline has no mute node',
          raw.node('duplicates/mute_asset_departments') is None)

    # 2. Exclude
    node.set_exclude_asset_department_names([DEPARTMENT])
    node.execute()
    stage = raw.stage()
    mute_node = raw.node('duplicates/mute_asset_departments')
    check('mute node created', mute_node is not None)
    muted = sorted(mute_node.parm('mutepaths').eval().split()) if mute_node else []
    check(f'mutes exactly the asset {DEPARTMENT} layers', muted == asset_ids,
          f'{len(muted)} muted vs {len(asset_ids)} composed')
    check('no shot layer muted', not set(muted) & set(shot_ids))
    check(f'no asset {DEPARTMENT} layer still used',
          department_layers(stage, 'assets') == [],
          ', '.join(department_layers(stage, 'assets')))
    check('shot layers untouched', department_layers(stage, 'shots') == shot_ids)

    # 3. Prims
    # Assets exported before the ``mtl`` convention have nothing to count.
    if DEPARTMENT == 'lookdev' and baseline_mtl == 0:
        print('[SKIP] asset material scopes gone  (no mtl scopes to begin with)')
    elif DEPARTMENT == 'lookdev':
        excluded_mtl = mtl_prims(stage, asset_ids)
        check('asset material scopes gone', excluded_mtl == 0,
              f'{baseline_mtl} -> {excluded_mtl}')

    # 4. Comment
    comment = raw.comment()
    check('comment reports the mute',
          f'Muted asset {DEPARTMENT}: {len(asset_ids)} layer' in comment,
          comment.replace('\n', ' | '))

    # 5. Clear
    node.set_exclude_asset_department_names([])
    node.execute()
    stage = raw.stage()
    check('mute node removed',
          raw.node('duplicates/mute_asset_departments') is None)
    check('stage restored', department_layers(stage, 'assets') == asset_ids
          and not stage.GetMutedLayers())
    check('comment no longer mentions a mute', 'Muted' not in raw.comment())

    # 6. Getter/setter
    node.set_exclude_asset_department_names([DEPARTMENT, 'no_such_dept'])
    check('setter keeps known names only',
          node.get_exclude_asset_department_names() == [DEPARTMENT],
          raw.parm('asset_departments').eval())


main()
print()
if FAILURES:
    print(f'{len(FAILURES)} FAILED')
    sys.exit(1)
print('ALL PASSED')
