"""Build the comp HDAs (th::import_render, th::light_mix, th::export_comp,
th::import_scene) from code and expand them into otls/.

    hython scripts/make_comp_hdas.py

The nodes' interfaces and contents are written here, so a change is a
diff of this file plus the regenerated otls/ sources (see otls/README.md).
Each node is built as a COP subnet, turned into a digital asset in a temp
.hda, given its DialogScript and sections, and expanded with ``hotl -t``
into ``otls/cop_th.<name>.1.0/``, replacing what was there. The Python the
nodes run lives in ``python/tumblepipe/pipe/houdini/cops/<name>.py``.

Run it with this checkout's python/ on PYTHONPATH, and no
HOUDINI_USER_PREF_DIR override: a scratch prefs dir named with __HVER__ makes
every COP cook fail with "invalid string position" (22.0.368 and .429).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import hou

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'python'))
OTLS = REPO / 'otls'
HOTL = Path(os.environ['HFS']) / 'bin' / ('hotl.exe' if os.name == 'nt' else 'hotl')

ICONS = {
    'import_render': '$TH_PIPELINE_PATH/resources/ImportAssetLayer.png',
    'light_mix': '$TH_PIPELINE_PATH/resources/BuildComp.png',
    'export_comp': '$TH_PIPELINE_PATH/resources/BuildComp.png',
    'import_scene': '$TH_PIPELINE_PATH/resources/ImportAssetLayer.png',
}

TOOLS_SHELF = """<?xml version="1.0" encoding="UTF-8"?>
<shelfDocument>
  <!-- This file contains definitions of shelves, toolbars, and tools.
 It should not be hand-edited when it is being used by the application.
 Note, that two definitions of the same element are not allowed in
 a single file. -->

  <tool name="$HDA_DEFAULT_TOOL" label="$HDA_LABEL" icon="$HDA_ICON">
    <toolMenuContext name="viewer">
      <contextNetType>COP</contextNetType>
    </toolMenuContext>
    <toolMenuContext name="network">
      <contextOpType>$HDA_TABLE_AND_NAME</contextOpType>
    </toolMenuContext>
    <toolSubmenu>_TumblePipe/pipeline</toolSubmenu>
    <script scriptType="python"><![CDATA[import coptoolutils

coptoolutils.genericTool(kwargs, '$HDA_NAME')]]></script>
  </tool>
</shelfDocument>
"""


def _callback(module, function):
    return (
        f'parmtag {{ "script_callback" "import tumblepipe.pipe.houdini.cops.{module} '
        f'as th_node; th_node.{function}(kwargs)" }}\n'
        '        parmtag { "script_callback_language" "python" }'
    )


def _menu(module, cls, method):
    return f"""menu {{
                [ "from tumblepipe.pipe.houdini.cops import {module}" ]
                [ "from tumblepipe.pipe.houdini import menus" ]
                [ "" ]
                [ "node = {module}.{cls}(hou.pwd())" ]
                [ "return menus.list_to_menu(node.{method}())" ]
                language python
            }}"""


def _entity_parms(module):
    return f"""        parm {{
            name    "entity_select"
            label   "Shot"
            type    button
            joinnext
            default {{ "0" }}
            parmtag {{ "button_icon" "hicon:/SVGIcons.index?BUTTONS_tree_full.svg" }}
            {_callback(module, 'select_entity')}
        }}
        parm {{
            name    "entity"
            label   "Shot"
            type    string
            invisible
            joinnext
            default {{ "from_context" }}
            parmtag {{ "script_callback_language" "python" }}
        }}
        parm {{
            name    "entity_label"
            label   "entity_label"
            type    label
            nolabel
            default {{ "from_context: none" }}
        }}"""


IMPORT_RENDER_DS = f"""# Dialog script for th::import_render::1.0

{{
    name	th::import_render::1.0
    script	th::import_render::1.0
    label	"th import render"

    output	rgba	rgba
    output	aovs	aovs
    output	beauties	beauties
    signature	default	Default	{{ }}	{{ RGBA Cable Cable }}

    help {{
	"A shot's finished render: the beauty (rgba), a cable of the other AOVs the comp reads (aovs) and a cable of each channel's beauty (beauties). Channel * reads every channel: rgba is them laid over each other, Channel Outputs keeps one output per channel beside the node."
    }}

    groupsimple {{
        name    "selection"
        label   "Render"

{_entity_parms('import_render')}
        parm {{
            name    "channel"
            label   "Channel"
            type    string
            default {{ "default" }}
            help    "The channel to read; All channels (*) reads every channel of the shot in this one node."
            menu {{
                [ "from tumblepipe.pipe.houdini.cops import import_render" ]
                [ "" ]
                [ "return import_render.ImportRender(hou.pwd()).channel_menu()" ]
                language python
            }}
            {_callback('import_render', 'changed')}
        }}
        parm {{
            name    "department"
            label   "Department"
            type    string
            default {{ "best" }}
            help    "best: the highest render department below composite with a complete version (denoise, else render), unless the denoise was made from an older render than the newest one."
            {_menu('import_render', 'ImportRender', 'list_department_names')}
            {_callback('import_render', 'changed')}
        }}
        parm {{
            name    "version"
            label   "Version"
            type    string
            default {{ "latest" }}
            help    "latest: the newest complete version. Pinning a version needs a concrete department."
            {_menu('import_render', 'ImportRender', 'list_version_names')}
            {_callback('import_render', 'changed')}
        }}
        parm {{
            name    "version_label"
            label   "Reads"
            type    label
            default {{ "" }}
        }}
        parm {{
            name    "resolved"
            label   "Resolved"
            type    string
            invisible
            default {{ "" }}
        }}
        parm {{
            name    "fields"
            label   "Fields"
            type    string
            invisible
            default {{ "" }}
        }}
        parm {{
            name    "build_key"
            label   "Build Key"
            type    string
            invisible
            default {{ "" }}
        }}
        parm {{
            name    "pins"
            label   "Pins"
            type    string
            invisible
            default {{ "" }}
            help    "Set by the composite worker: an all-channels import's frozen version per channel."
        }}
    }}

    groupsimple {{
        name    "outputs_group"
        label   "Outputs"

        parm {{
            name    "beauty_outputs"
            label   "Channel Outputs"
            type    toggle
            default {{ "1" }}
            help    "All channels: keep a Cable Unpack beside the node with one output per channel's beauty, kept in step with the channels read."
            {_callback('import_render', 'unpack_changed')}
        }}
        parm {{
            name    "unpack_outputs"
            label   "AOV Outputs"
            type    toggle
            default {{ "0" }}
            help    "Keep a Cable Unpack beside the node with one output per AOV in the cable, kept in step with it."
            {_callback('import_render', 'unpack_changed')}
        }}
    }}

    groupsimple {{
        name    "actions"
        label   "Actions"

        parm {{
            name    "execute"
            label   "Update"
            type    button
            joinnext
            default {{ "0" }}
            {_callback('import_render', 'execute')}
        }}
        parm {{
            name    "open_location"
            label   "Open Location"
            type    button
            joinnext
            default {{ "0" }}
            {_callback('import_render', 'open_location')}
        }}
        parm {{
            name    "render"
            label   "Render"
            type    button
            default {{ "0" }}
            help    "Open Farm Submit with this shot's Render ticked and this import's channel checked (all channels for *); a concrete Department is the render's Up to."
            {_callback('import_render', 'render')}
        }}
    }}

}}
"""


LIGHT_MIX_DS = f"""# Dialog script for th::light_mix::1.0

{{
    name	th::light_mix::1.0
    script	th::light_mix::1.0
    label	"th light mix"

    input	rgba	rgba
    input	aovs	aovs
    output	rgba	rgba
    signature	default	Default	{{ RGBA Cable }}	{{ RGBA }}

    help {{
	"A channel's light groups mixed back into its beauty: the graded groups plus Rest (the beauty minus the groups). Inputs: the import's rgba beauty (wired for you on sync) and its aovs cable, in the order of the import's outputs."
    }}

    parm {{
        name    "mix_label"
        label   "Mix"
        type    label
        default {{ "no light groups: Rest is the whole beauty" }}
    }}
    parm {{
        name    "sync"
        label   "Sync Rows"
        type    button
        default {{ "0" }}
        help    "Add a row for every light group in the cable that has none. Existing rows are never changed."
        {_callback('light_mix', 'sync')}
    }}
    groupsimple {{
        name    "rest_group"
        label   "Rest (lights in no group)"

        parm {{
            name    "rest_mute"
            label   "Mute"
            type    toggle
            default {{ "0" }}
        }}
        parm {{
            name    "rest_exposure"
            label   "Exposure"
            type    float
            default {{ "0" }}
            range   {{ -5 5 }}
        }}
        parm {{
            name    "rest_tint"
            label   "Tint"
            type    color
            size    3
            default {{ "1" "1" "1" }}
            range   {{ 0 1 }}
        }}
    }}
    multiparm {{
        name    "groups"
        label    "Light Groups"
        default 0
        parmtag {{ "multistartoffset" "1" }}

        parm {{
            name    "group#"
            label   "Group"
            type    string
            default {{ "" }}
        }}
        parm {{
            name    "mute#"
            label   "Mute"
            type    toggle
            default {{ "0" }}
        }}
        parm {{
            name    "exposure#"
            label   "Exposure"
            type    float
            default {{ "0" }}
            range   {{ -5 5 }}
        }}
        parm {{
            name    "tint#"
            label   "Tint"
            type    color
            size    3
            default {{ "1" "1" "1" }}
            range   {{ 0 1 }}
        }}
    }}

}}
"""


EXPORT_COMP_DS = f"""# Dialog script for th::export_comp::1.0

{{
    name	th::export_comp::1.0
    script	th::export_comp::1.0
    label	"th export comp"

    input	comp	comp
    signature	default	Default	{{ RGBA }}	{{ }}

    help {{
	"The end of a comp. Submit renders every export_comp in the workfile as one composite version per shot."
    }}

    groupsimple {{
        name    "selection"
        label   "Output"

{_entity_parms('export_comp')}
        parm {{
            name    "layer"
            label   "Layer"
            type    string
            default {{ "comp" }}
            help    "comp: the merged comp, written as the version's cross-channel layer (composite/slapcomp). A channel: a per-channel output."
            {_menu('export_comp', 'ExportComp', 'list_layer_names')}
            {_callback('export_comp', 'refresh')}
        }}
        parm {{
            name    "warning"
            label   "Warning"
            type    label
            default {{ "" }}
            hidewhen "{{ warning == \\"\\" }}"
        }}
    }}

    groupsimple {{
        name    "jobs"
        label   "Jobs"

        parm {{
            name    "preview_frames"
            label   "Preview Frames"
            type    toggle
            default {{ "0" }}
            help    "Also render the first, middle and last frames as a quick task before the full range."
        }}
        parm {{
            name    "frame_range"
            label   "Frame Range"
            type    string
            default {{ "from_context" }}
            menu {{
                "from_context"  "From Config"
                "from_settings" "From Settings"
            }}
        }}
        parm {{
            name    "frame_settings"
            label   "Start/End-Frame"
            type    intvector2
            size    2
            default {{ "1001" "1100" }}
            disablewhen "{{ frame_range == from_context }}"
            range   {{ 1! 999999! }}
        }}
        parm {{
            name    "roll_settings"
            label   "Pre/Post-Roll"
            type    intvector2
            size    2
            default {{ "0" "0" }}
            disablewhen "{{ frame_range == from_context }}"
            range   {{ 0! 999999! }}
        }}
        parm {{
            name    "stepsize"
            label   "Step"
            type    integer
            default {{ "1" }}
            range   {{ 1! 10 }}
        }}
        parm {{
            name    "batchsize"
            label   "Batch Size"
            type    integer
            default {{ "5" }}
            range   {{ 1! 50 }}
        }}
        parm {{
            name    "copy_to_edit"
            label   "Copy to Edit"
            type    toggle
            default {{ "0" }}
        }}
    }}

    groupsimple {{
        name    "farm"
        label   "Farm"

        parm {{
            name    "pool"
            label   "Pool"
            type    string
            default {{ "" }}
            menu {{
                [ "from tumblepipe.pipe.houdini.cops import export_comp" ]
                [ "" ]
                [ "return export_comp.ExportComp(hou.pwd()).pool_menu()" ]
                language python
            }}
        }}
        parm {{
            name    "priority"
            label   "Priority"
            type    integer
            default {{ "50" }}
            range   {{ 0! 100! }}
        }}
    }}

    parm {{
        name    "render_output"
        label   "Render Output"
        type    file
        invisible
        default {{ "" }}
        help    "Set by the composite worker: where the render ROP writes."
    }}
    parm {{
        name    "render_range"
        label   "Render Range"
        type    intvector2
        size    2
        invisible
        default {{ "1001" "1001" }}
        help    "Set by the composite worker: the frames the render ROP writes."
    }}

    groupsimple {{
        name    "actions"
        label   "Actions"

        parm {{
            name    "submit"
            label   "Submit"
            type    button
            joinnext
            default {{ "0" }}
            {_callback('export_comp', 'submit_kwargs')}
        }}
        parm {{
            name    "open_location"
            label   "Open Location"
            type    button
            default {{ "0" }}
            {_callback('export_comp', 'open_location')}
        }}
    }}

}}
"""


IMPORT_SCENE_DS = f"""# Dialog script for th::import_scene::1.0

{{
    name	th::import_scene::1.0
    script	th::import_scene::1.0
    label	"th import scene"

    output	geometry	geometry
    output	camera	camera
    signature	default	Default	{{ }}	{{ Geometry Metadata }}

    help {{
	"The shot's staged scene as geometry and its render camera, for rasterizing in a comp (wire both into a Rasterize Geometry)."
    }}

    groupsimple {{
        name    "selection"
        label   "Scene"

{_entity_parms('import_scene')}
        parm {{
            name    "channel"
            label   "Channel"
            type    string
            default {{ "default" }}
            menu {{
                [ "opmenu -l -a lopnet/import_shot channel" ]
            }}
        }}
        parm {{
            name    "department"
            label   "Department"
            type    string
            default {{ "from_context" }}
            menu {{
                [ "opmenu -l -a lopnet/import_shot department" ]
            }}
        }}
        parm {{
            name    "version"
            label   "Version"
            type    string
            default {{ "latest" }}
            menu {{
                [ "opmenu -l -a lopnet/import_shot version" ]
            }}
        }}
        parm {{
            name    "version_label"
            label   "Reads"
            type    label
            default {{ "" }}
        }}
    }}

    groupsimple {{
        name    "geometry_group"
        label   "Geometry"

        parm {{
            name    "primpattern"
            label   "Primitives"
            type    string
            default {{ "/*" }}
            help    "The prims imported as geometry; anything that is not geometry (cameras, lights) adds nothing."
        }}
        parm {{
            name    "purpose"
            label   "Purpose"
            type    string
            default {{ "render" }}
            menu {{
                "render"    "Render"
                "proxy"     "Proxy"
                "guide"     "Guide"
            }}
        }}
        parm {{
            name    "timesample"
            label   "Time Sample"
            type    ordinal
            default {{ "1" }}
            help    "Animated imports the current frame and recooks every frame; Static imports one frame."
            menu {{
                "static"    "Static"
                "animated"  "Animated"
            }}
        }}
        parm {{
            name    "staticimportframe"
            label   "Static Frame"
            type    float
            default {{ "$FSTART" }}
            hidewhen "{{ timesample == 1 }}"
        }}
    }}

    groupsimple {{
        name    "camera_group"
        label   "Camera"

        parm {{
            name    "camera_prim"
            label   "Camera"
            type    string
            default {{ "/cameras/render_camera" }}
        }}
    }}

    groupsimple {{
        name    "actions"
        label   "Actions"

        parm {{
            name    "execute"
            label   "Import"
            type    button
            default {{ "0" }}
            {_callback('import_scene', 'execute')}
        }}
    }}

}}
"""


def _on_created(module):
    return (
        f'import tumblepipe.pipe.houdini.cops.{module} as th_node\n\n'
        "th_node.on_created(kwargs['node'])\n"
    )


def _set_io(subnet, inputs, outputs):
    """``inputs``/``outputs``: [(label, type token)] of a COP subnet."""
    subnet.parm('inputs').set(len(inputs))
    for index, (label, kind) in enumerate(inputs, start=1):
        subnet.parm(f'inputlabel{index}').set(label)
        subnet.parm(f'inputtype{index}').set(kind)
    subnet.parm('outputs').set(len(outputs))
    for index, (label, kind) in enumerate(outputs, start=1):
        subnet.parm(f'outputlabel{index}').set(label)
        subnet.parm(f'outputtype{index}').set(kind)


def build_import_render(root):
    io = [('rgba', 'vector4'), ('aovs', 'cable'), ('beauties', 'cable')]
    _set_io(root, [], io)
    dive = root.createNode('subnet', 'dive')
    _set_io(dive, [], io)
    dive.setColor(hou.Color((1.0, 0.0, 1.0)))
    outputs = root.node('outputs')
    for index in range(len(io)):
        outputs.setInput(index, dive, index)
    dive.setPosition(hou.Vector2(0, 1.5))
    return ['dive']


def build_light_mix(root):
    from tumblepipe.pipe.houdini.cops.light_mix import MIX_IO
    io = list(MIX_IO)
    _set_io(root, io, [('rgba', 'vector4')])
    mix = root.createNode('subnet', 'mix')
    _set_io(mix, io, [('rgba', 'vector4')])
    mix.setColor(hou.Color((1.0, 0.0, 1.0)))
    mix.setInput(0, root.node('inputs'), 0)
    mix.setInput(1, root.node('inputs'), 1)
    root.node('outputs').setInput(0, mix, 0)
    mix.setPosition(hou.Vector2(0, 1.5))
    # The default network: no groups, Rest is the beauty
    from tumblepipe.pipe.houdini.cops import light_mix

    class _Prototype(light_mix.LightMix):
        def parm(self, name):
            return _NullParm()

        def native(self):
            return _Unwired(root)

    _Prototype(root)._build([])
    return ['mix']


class _NullParm:
    def set(self, *_): pass
    def eval(self): return 0


class _Unwired:
    """The prototype light mix's node: its beauty input reads as wired, so
    the default network takes the beauty from the rgba input, not the cable
    (``_build`` reads an empty rgba input as "the cable carries it")."""
    def __init__(self, root):
        self._root = root

    def node(self, path):
        return self._root.node(path)

    def input(self, index):
        return self._root  # not None: wired


def build_export_comp(root):
    _set_io(root, [('comp', 'vector4')], [])
    render = root.createNode('rop_image', 'render')
    render.setInput(0, root.node('inputs'), 0)
    render.parm('trange').set('normal')
    render.parm('mkpath').set(1)
    # The worker sets the HDA's own parms; the ROP inside the locked asset
    # follows them (an inner parm of a locked asset cannot be set).
    render.parm('copoutput').setExpression('chs("../render_output")', hou.exprLanguage.Hscript)
    render.parm('f1').setExpression('ch("../render_rangex")', hou.exprLanguage.Hscript)
    render.parm('f2').setExpression('ch("../render_rangey")', hou.exprLanguage.Hscript)
    render.parm('f3').set(1)
    render.setPosition(hou.Vector2(0, 1.5))
    return []


def build_import_scene(root):
    """The staged shot (th::import_shot, payloads loaded) as unpacked
    polygons through a SOP Import, and its camera through a Camera Import."""
    _set_io(root, [], [('geometry', 'geo'), ('camera', 'metadata')])
    lopnet = root.createNode('lopnet', 'lopnet')
    shot = lopnet.createNode('th::import_shot::1.0', 'import_shot')
    for name in ('entity', 'channel', 'department', 'version'):
        shot.parm(name).setExpression(f'chs("../../{name}")', hou.exprLanguage.Hscript)
    shot.parm('loadpayloads').set(1)
    lopnet.setPosition(hou.Vector2(0, 3.0))

    geometry = root.createNode('sopimport', 'geometry')
    lopimport = geometry.createNode('lopimport::2.0', 'lopimport')
    lopimport.parm('loppath').set('../../lopnet/import_shot')
    for name in ('primpattern', 'purpose'):
        lopimport.parm(name).setExpression(f'chs("../../{name}")', hou.exprLanguage.Hscript)
    for name in ('timesample', 'staticimportframe'):
        lopimport.parm(name).setExpression(f'ch("../../{name}")', hou.exprLanguage.Hscript)
    unpack = geometry.createNode('unpackusd::2.0', 'unpackusd')
    unpack.parm('output').set(1)  # polygons
    unpack.setInput(0, lopimport, 0)
    out = geometry.node('output') or geometry.createNode('output', 'output')
    out.setInput(0, unpack, 0)
    geometry.setPosition(hou.Vector2(-1.5, 1.5))

    objnet = root.createNode('objnet', 'objnet')
    lopcam = objnet.createNode('lopimportcam', 'camera')
    lopcam.parm('loppath').set('../../lopnet/import_shot')
    lopcam.parm('primpath').setExpression('chs("../../camera_prim")', hou.exprLanguage.Hscript)
    # The render's width, not lopimportcam's 1280 (USD keeps the resolution
    # on RenderSettings, not the camera); resy stays width x camera aspect.
    from tumblepipe.pipe.houdini.render_camera import LOP_IMPORT_CAMERA_WIDTH_EXPR
    lopcam.parm('resx').setExpression(LOP_IMPORT_CAMERA_WIDTH_EXPR, hou.exprLanguage.Python)
    objnet.setPosition(hou.Vector2(1.5, 3.0))
    camera = root.createNode('cameraimport', 'camera')
    camera.parm('camera').set('../objnet/camera')
    camera.setPosition(hou.Vector2(1.5, 1.5))

    outputs = root.node('outputs')
    outputs.setInput(0, geometry, 0)
    outputs.setInput(1, camera, 0)
    return ['lopnet/import_shot']


# (name, label, build, dialog script, min inputs, max inputs)
NODES = [
    ('import_render', 'th import render', build_import_render, IMPORT_RENDER_DS, 0, 0),
    ('light_mix', 'th light mix', build_light_mix, LIGHT_MIX_DS, 1, 2),
    ('export_comp', 'th export comp', build_export_comp, EXPORT_COMP_DS, 1, 1),
    ('import_scene', 'th import scene', build_import_scene, IMPORT_SCENE_DS, 0, 0),
]

ON_LOADED = {
    'light_mix': (
        'import tumblepipe.pipe.houdini.cops.light_mix as th_node\n\n'
        "th_node.on_loaded(kwargs['node'])\n"
    ),
    'export_comp': (
        'import tumblepipe.pipe.houdini.cops.export_comp as th_node\n\n'
        "th_node.on_loaded(kwargs['node'])\n"
    ),
}


def make(name, label, build, dialog_script, min_inputs, max_inputs, temp_dir):
    type_name = f'th::{name}::1.0'
    copnet = hou.node('/img').createNode('copnet', f'build_{name}')
    root = copnet.createNode('subnet', name)
    editable = build(root)
    hda_path = Path(temp_dir) / f'cop_th.{name}.1.0.hda'
    node = root.createDigitalAsset(
        name = type_name,
        hda_file_name = str(hda_path),
        description = label,
        min_num_inputs = min_inputs,
        max_num_inputs = max_inputs,
        ignore_external_references = True,
    )
    definition = node.type().definition()
    definition.addSection('DialogScript', dialog_script)
    definition.addSection('OnCreated', _on_created(name))
    definition.setExtraFileOption('OnCreated/IsPython', True)
    if name in ON_LOADED:
        definition.addSection('OnLoaded', ON_LOADED[name])
        definition.setExtraFileOption('OnLoaded/IsPython', True)
    definition.addSection('Tools.shelf', TOOLS_SHELF)
    if editable:
        definition.addSection('EditableNodes', ' '.join(editable))
    definition.setIcon(ICONS[name])
    options = definition.options()
    options.setLockContents(True)
    options.setUnlockNewInstances(False)
    options.setSaveSpareParms(False)
    options.setCheckForExternalLinks(True)
    options.setMakeInitialParmsDefaults(True)
    options.setForbidOutsideParms(True)
    options.setCompressContents(True)
    definition.setOptions(options)
    definition.save(str(hda_path))
    copnet.destroy()
    return hda_path


def expand(hda_path, name):
    target = OTLS / f'cop_th.{name}.1.0'
    if target.exists():
        shutil.rmtree(target)
    subprocess.run([str(HOTL), '-t', str(target), str(hda_path)], check=True)
    # EditableNodes must have no trailing newline (otls/README.md)
    for editable in target.glob('*/EditableNodes'):
        editable.write_bytes(editable.read_bytes().rstrip(b'\r\n'))
    print(f'wrote {target}')


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        for name, label, build, dialog_script, min_inputs, max_inputs in NODES:
            hda_path = make(name, label, build, dialog_script, min_inputs, max_inputs, temp_dir)
            expand(hda_path, name)


if __name__ == '__main__':
    main()
