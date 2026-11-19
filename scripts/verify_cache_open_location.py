"""Audit: th::cache's Open Location button, on the compiled HDAs.

    hython scripts/verify_cache_open_location.py

Remy, 2026-11-18: "Can we get an 'Open location' button on the th_cache
node please". On a real ``th::cache`` SOP and LOP in the template project,
with the OS file browser stood in for (``util.desktop.open_in_file_browser``
is replaced, so nothing opens on the desktop):

- both HDAs have the button, labelled **Open Location**, in the Action row
  after **Load** (which joins it), calling ``cache.open_location``;
- a node that has cached nothing refuses, saying the folder that is not
  there yet and to press Cache, and opens nothing;
- after **Cache** writes a version, the button (pressed as the artist
  presses it) opens that version's folder, which holds what was written;
- a Version that is not on disk refuses, naming it;
- with no Version picked it opens the cache's folder (its versions).

Needs Houdini with the HDAs compiled and on HOUDINI_OTLSCAN_PATH and this
checkout's python/ on PYTHONPATH (the preflight ``hython`` stage sets
both, and TH_PROJECT_PATH at a copy of scripts/project_template). Exits 1
on any failure; nothing outside the temp project is written.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))

import hou  # noqa: E402

from tumblepipe.api import api, path_str  # noqa: E402
from tumblepipe.util import desktop  # noqa: E402
from tumblepipe.util.uri import Uri  # noqa: E402
from tumblepipe.pipe.paths import Context, get_workspace_relpath  # noqa: E402
from tumblepipe.pipe.houdini import entity_node  # noqa: E402
import tumblepipe.pipe.houdini.cache as cache_mod  # noqa: E402
import tumblepipe.pipe.houdini.nodes as ns  # noqa: E402

_failures = []
opened: list[Path] = []
desktop.open_in_file_browser = lambda path: opened.append(Path(path))


def check(label, ok, detail=''):
    print(f"{'ok   ' if ok else 'FAIL '} {label}", flush=True)
    if not ok:
        if detail: print(f'        {detail}')
        _failures.append(label)


def refusal(node) -> str:
    try:
        node.open_location()
    except ns.NodeError as error:
        return str(error)
    return ''


def check_interface(native, label):
    parm = native.parm('open_location')
    check(f'{label}: the node has an Open Location button',
          parm is not None and parm.parmTemplate().type() == hou.parmTemplateType.Button
          and parm.parmTemplate().label() == 'Open Location')
    if parm is None: return
    group = native.type().definition().parmTemplateGroup()
    action = group.find('action')
    names = [template.name() for template in action.parmTemplates()]
    check(f'{label}: it is in the Action row, after Cache and Load',
          names == ['cache', 'load', 'open_location'], str(names))
    check(f'{label}: Cache and Load join it on one row',
          group.find('cache').joinsWithNext() and group.find('load').joinsWithNext()
          and not group.find('open_location').joinsWithNext())
    check(f'{label}: it calls cache.open_location',
          'th_cache.open_location(kwargs)' in parm.parmTemplate().scriptCallback()
          and parm.parmTemplate().scriptCallbackLanguage() == hou.scriptLanguage.Python,
          parm.parmTemplate().scriptCallback())


def check_node(cls, source, label, uri, department):
    node = cache_mod.create(source.parent(), f'{label}_cache', cls)
    native = node.native()
    native.setInput(0, source)
    check_interface(native, label)
    name = native.name()
    root = cls.resolve_location('project', get_workspace_relpath(uri, department))

    del opened[:]
    said = refusal(node)
    check(f'{label}: nothing cached yet refuses, saying where and what to press',
          not opened and 'nothing on disk yet' in said and 'press Cache' in said
          and path_str(root / name) in said, said)

    node.cache()
    version = native.parm('version').eval()
    folder = root / name / version
    check(f'{label}: Cache wrote a first version', version == 'v0001' and folder.is_dir(),
          f'{version} {folder}')
    del opened[:]
    native.parm('open_location').pressButton()
    check(f'{label}: the button opens the version\'s folder', opened == [folder], str(opened))
    written = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
    check(f'{label}: that folder holds what Cache wrote',
          any(n.startswith('v0001.') for n in written) and 'context.json' in written, str(written))

    del opened[:]
    native.parm('version').set('v0009')
    said = refusal(node)
    check(f'{label}: a Version that is not on disk refuses, naming it',
          not opened and 'v0009' in said and 'not on disk' in said
          and path_str(root / name / 'v0009') in said, said)

    native.parm('version').set('')
    del opened[:]
    got = node.open_location()
    check(f'{label}: with no Version picked it opens the cache\'s folder',
          opened == [root / name] and got == root / name, str(opened))


def main():
    uri = Uri.parse('entity:/assets/CHAR/Bean')
    api.config.add_entity(uri, {})
    hou.hipFile.clear(suppress_save_prompt=True)
    entity_node.scene_context = lambda: Context(uri, 'model')

    geo = hou.node('/obj').createNode('geo', 'geo1')
    check_node(cache_mod.SopCache, geo.createNode('box'), 'sop', uri, 'model')
    check_node(cache_mod.LopCache, hou.node('/stage').createNode('cube'), 'lop', uri, 'model')

    print()
    print(f'{len(_failures)} failure(s)' if _failures else 'all checks passed')
    return 1 if _failures else 0


if __name__ == '__main__':
    hou.exit(main())
