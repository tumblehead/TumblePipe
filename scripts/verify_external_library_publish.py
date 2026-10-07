"""Acceptance: library files dropped into a shot survive layout -> light -> render.

    hython scripts/verify_external_library_publish.py

Run under a project hython with the local source (TumbleTrove Desktop
run_hython with use_dev_overrides=true). PUBLISHES into the project: it
creates the shot entity:/shots/verify_seq/verify_library, exports layout and
light, builds the staged stage after each, then removes the shot and its
exports again. Needs one published asset-free project; any will do.

The report (an external user): a chair dragged in from an asset-library
folder outside the project, through a plain Reference LOP, reached the next
department as empty Xforms, and a dome light's HDRI went missing. Two
causes, both silent:

- the export copied the referenced library wrapper into the version folder
  but not the geometry layer it references, so the inner arc dangled;
- the USD ROP wrote the dome light's texture relative to the temp export
  folder, which points nowhere once the layer is copied into its version.

Checks, composed through import_shot in a render workfile:
  1. the library asset still has its geometry child;
  2. the dome light's texture resolves to a file inside the published light
     version folder.
"""

import shutil
import sys
import tempfile
from pathlib import Path

import hou
from pxr import Sdf
from tumblepipe.api import api
from tumblepipe.pipe.houdini.lops import export_layer, import_shot
from tumblepipe.pipe.houdini.ui import task_factory
from tumblepipe.util.uri import Uri

SEQ = Uri.parse_unsafe('entity:/shots/verify_seq')
SHOT = Uri.parse_unsafe('entity:/shots/verify_seq/verify_library')
FAILURES = []


def check(label, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f'  ({detail})' if detail else ''))
    if not ok:
        FAILURES.append(label)


def write_library(root: Path) -> tuple[Path, Path]:
    """A polyhaven-shaped download: wrapper -> geometry layer -> texture."""
    asset = root / 'polyhaven' / 'Barrel_02'
    (asset / 'textures').mkdir(parents=True)
    (asset / 'textures' / 'diff.png').write_bytes(b'png')
    (asset / 'geo.usda').write_text(
        '#usda 1.0\n'
        'def Xform "Barrel_02"\n{\n'
        '    def Mesh "mesh" {}\n'
        '    def Shader "tex"\n    {\n'
        '        asset inputs:file = @./textures/diff.png@\n'
        '    }\n}\n'
    )
    wrapper = asset / 'Barrel_02_yup.usda'
    wrapper.write_text(
        '#usda 1.0\n(defaultPrim = "Barrel_02")\n'
        'def Xform "Barrel_02"\n{\n'
        '    def Xform "geo" (prepend references = @./geo.usda@</Barrel_02>)\n'
        '    {\n    }\n}\n'
    )
    hdri = root / 'hdri' / 'aft_lounge.exr'
    hdri.parent.mkdir()
    hdri.write_bytes(b'exr')
    return wrapper, hdri


def export(stage_net, node, department):
    node_out = export_layer.create(stage_net, f'export_{department}')
    node_out.native().setInput(0, node)
    node_out.set_entity_uri(SHOT)
    node_out.set_department_name(department)
    version = node_out._export_local()
    task_factory._execute_build_local(SHOT, 'default', 1001, 1001)
    return version


def import_into(department):
    hou.hipFile.clear(suppress_save_prompt=True)
    stage_net = hou.node('/stage')
    node = import_shot.create(stage_net, 'import_shot')
    node.set_shot_uri(SHOT)
    node.set_department_name(department)
    node.execute()
    return stage_net, node.native()


def main():
    library = Path(tempfile.mkdtemp(prefix='verify_library_'))
    created_seq = api.config.get_properties(SEQ) is None
    created = api.config.get_properties(SHOT) is None
    try:
        wrapper, hdri = write_library(library)
        if created_seq:
            api.config.add_entity(SEQ, {})
        if created:
            api.config.add_entity(SHOT, {'frame_start': 1001, 'frame_end': 1001})

        hou.hipFile.clear(suppress_save_prompt=True)
        stage_net = hou.node('/stage')
        ref = stage_net.createNode('reference::2.0', 'bar_chair')
        ref.parm('primpath1').set('/bar_chair')
        ref.parm('filepath1').set(wrapper.as_posix())
        export(stage_net, ref, 'layout')

        stage_net, imported = import_into('light')
        dome = stage_net.createNode('domelight::3.0', 'aft_lounge')
        dome.setInput(0, imported)
        dome.parm('primpath').set('/lights/dome')
        dome.parm('xn__inputstexturefile_r3ah').set(hdri.as_posix())
        light_version = export(stage_net, dome, 'light')

        # The library is gone once published: nothing may still read it.
        shutil.rmtree(library)

        _stage_net, imported = import_into('render')
        stage = imported.stage()
        mesh = stage.GetPrimAtPath('/bar_chair/geo/mesh')
        check('library asset keeps its geometry downstream', bool(mesh))
        dome_prim = stage.GetPrimAtPath('/lights/dome')
        texture = dome_prim.GetAttribute('inputs:texture:file').Get() if dome_prim else None
        resolved = Path(texture.resolvedPath) if isinstance(texture, Sdf.AssetPath) and texture.resolvedPath else None
        check(
            'dome light texture resolves inside the light version folder',
            resolved is not None and resolved.exists() and light_version in resolved.parts,
            f'{texture} -> {resolved}',
        )
    finally:
        shutil.rmtree(library, ignore_errors=True)
        if created:
            api.config.remove_entity(SHOT)
            export_root = Path(api.storage.resolve(Uri.parse_unsafe('export:/shots/verify_seq/verify_library')))
            shutil.rmtree(export_root, ignore_errors=True)
        if created_seq:
            api.config.remove_entity(SEQ)
            shutil.rmtree(
                Path(api.storage.resolve(Uri.parse_unsafe('export:/shots/verify_seq'))),
                ignore_errors=True,
            )

    print('RESULT:', 'FAILED ' + ', '.join(FAILURES) if FAILURES else 'ALL CHECKS PASSED')
    return 1 if FAILURES else 0


if __name__ == '__main__':
    sys.exit(main())
