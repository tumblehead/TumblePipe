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


# The entity picker's button, the same on every entity-aware HDA (see
# tests/test_entity_picker_buttons.py): one "Browse..." text button opening
# the picker. Before it, `entity_select`, the jade icon button of 1.104.0,
# stays as an invisible carrier: saved scenes hold the button, and Houdini
# warns about every parm a scene holds that the HDA no longer has.
ENTITY_BROWSE_HELP = (
    'Open the entity picker to search for and pick the entity this node addresses.'
)


def _entity_parms(module):
    return f"""        parm {{
            name    "entity_select"
            label   "Shot"
            type    button
            invisible
            joinnext
            default {{ "0" }}
            {_callback(module, 'select_entity')}
        }}
        parm {{
            name    "entity_browse"
            label   "Browse..."
            type    button
            joinnext
            default {{ "0" }}
            help    "{ENTITY_BROWSE_HELP}"
            parmtag {{ "script_callback" "import tumblepipe.pipe.houdini.cops.{module} as th_node; th_node.select_entity(kwargs)" }}
            parmtag {{ "script_callback_language" "python" }}
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


# th::import_render's outputs (#67, Remy's mockup of 2026-11-12): a
# beauty output per channel (rgba, rgba2 .. rgba8; a digital asset's outputs
# are fixed by its type), the AOV cables, then the scene's. Kept equal to
# cops.import_render.OUTPUTS by tests/test_import_render_outputs.py.
IMPORT_RENDER_CHANNEL_OUTPUTS = 8
IMPORT_RENDER_OUTPUTS = (
    [('rgba', 'rgba', 'RGBA')]
    + [(f'rgba{i}', f'rgba {i}', 'RGBA') for i in range(2, IMPORT_RENDER_CHANNEL_OUTPUTS + 1)]
    + [('lpe', 'lpe', 'Cable'), ('aov', 'aov', 'Cable'), ('matte', 'matte', 'Cable'),
       ('cryptomaterials', 'cryptomaterials', 'Cable'),
       ('cryptoprimitives', 'cryptoprimitives', 'Cable'),
       ('geometry', 'geometry', 'Geometry'), ('camera', 'camera', 'Metadata')]
)
_IMPORT_RENDER_OUTPUT_LINES = '\n'.join(
    f'    output\t{name}\t"{label}"' for name, label, _ in IMPORT_RENDER_OUTPUTS
)
_IMPORT_RENDER_SIGNATURE = ' '.join(kind for _, _, kind in IMPORT_RENDER_OUTPUTS)

IMPORT_RENDER_DS = f"""# Dialog script for th::import_render::1.0

{{
    name	th::import_render::1.0
    script	th::import_render::1.0
    label	"th import render"

{_IMPORT_RENDER_OUTPUT_LINES}
    signature	default	Default	{{ }}	{{ {_IMPORT_RENDER_SIGNATURE} }}

    help {{
	"A shot's finished render, top to bottom: each channel's beauty (rgba; with Channel * every channel on an output of its own, rgba to rgba 8, named on Reads), cables of the light groups (lpe, a light mix's), the other AOVs (aov), the mattes, ramps and holdouts (matte) and each cryptomatte's rank planes, whole, for a Cryptomatte COP's crypto1 (cryptomaterials, cryptoprimitives), then the staged geometry (geometry) and the shot's render camera (camera) for a Rasterize Geometry."
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
            name    "time_offset_mode"
            label   "Time Offset"
            type    string
            default {{ "from_multi" }}
            help    "Moves the shot's renders to where the shot sits in this scene: frame F reads the render's frame F minus the offset. From Multi: the shot's place on a Multi's shared timeline (no offset anywhere else). The farm always comps the shot's own frames."
            menu {{
                "from_multi"    "From Multi"
                "none"          "None"
                "manual"        "Manual"
            }}
            {_callback('import_render', 'changed')}
        }}
        parm {{
            name    "time_offset"
            label   "Offset Frames"
            type    integer
            default {{ "0" }}
            disablewhen "{{ time_offset_mode != manual }}"
            range   {{ -1000 1000 }}
            {_callback('import_render', 'changed')}
        }}
        parm {{
            name    "applied_offset"
            label   "Applied Offset"
            type    integer
            invisible
            default {{ "0" }}
            help    "The offset the File COPs read (frame F reads the render's F minus it); kept by the node."
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
        parm {{
            name    "outputs_layout"
            label   "Outputs Layout"
            type    integer
            invisible
            default {{ "0" }}
            help    "Which outputs the node was made with: 0 before #67 gave each channel an output and split the AOVs into lpe, aov and matte (its wires move on load), 1 since; set at creation."
        }}
        parm {{
            name    "channel_outputs"
            label   "Channel Outputs"
            type    string
            invisible
            default {{ "" }}
            help    "The channel on each of rgba .. rgba 8 (- where none), kept so a wire stays on its channel."
        }}
    }}

    groupcollapsible {{
        name    "cache_group"
        label   "Cache"

        parm {{
            name    "cache"
            label   "Cache"
            type    toggle
            default {{ "1" }}
            help    "Keeps each frame read in memory, so playing it again does not read the network; the cache empties itself when the read changes. Caches the beauties (rgba .. rgba 8). Memory grows with every frame played, up to Max Frames per read."
        }}
        parm {{
            name    "cache_aovs"
            label   "Cache AOVs Too"
            type    toggle
            default {{ "0" }}
            disablewhen "{{ cache == 0 }}"
            help    "Caches lpe, aov and matte as well: every AOV of every frame, far more memory than the beauty alone."
        }}
        parm {{
            name    "cache_frames"
            label   "Max Frames"
            type    integer
            default {{ "250" }}
            range   {{ 1! 2000 }}
            help    "How many frames each cached read keeps."
        }}
        parm {{
            name    "clear_cache"
            label   "Clear Cache"
            type    button
            default {{ "0" }}
            help    "Empties the cache: the next play reads every frame from disk again."
            {_callback('import_render', 'clear_cache')}
        }}
    }}

    groupcollapsible {{
        name    "scene_group"
        label   "Scene"

        parm {{
            name    "scene_department"
            label   "Department"
            type    string
            default {{ "from_context" }}
            help    "The staged shot the geometry and camera outputs read (th::import_scene inside the node); its shot and channel are this node's (default for All channels)."
            menu {{
                [ "opmenu -l -a scene/lopnet/import_shot department" ]
            }}
            {_callback('import_render', 'scene_changed')}
        }}
        parm {{
            name    "scene_version"
            label   "Version"
            type    string
            default {{ "latest" }}
            menu {{
                [ "opmenu -l -a scene/lopnet/import_shot version" ]
            }}
            {_callback('import_render', 'scene_changed')}
        }}
        parm {{
            name    "scene_label"
            label   "Reads"
            type    label
            default {{ "not imported: press Update" }}
            help    "Update imports the scene; a refresh does only while geometry or camera is wired. geometry and camera stay empty until it has imported."
        }}
        parm {{
            name    "scene_ready"
            label   "Scene Ready"
            type    toggle
            invisible
            default {{ "0" }}
        }}
        parm {{
            name    "camera_ready"
            label   "Camera Ready"
            type    toggle
            invisible
            default {{ "0" }}
        }}
        parm {{
            name    "camera_prim"
            label   "Camera"
            type    string
            default {{ "/cameras/render_camera" }}
            {_callback('import_render', 'scene_changed')}
        }}
        parm {{
            name    "primpattern"
            label   "Primitives"
            type    string
            default {{ "/*" }}
            help    "The prims the geometry output imports; anything that is not geometry (cameras, lights) adds nothing."
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
        name    "outputs_group"
        label   "Outputs"

        parm {{
            name    "beauty_outputs"
            label   "Channel Outputs"
            type    toggle
            invisible
            default {{ "1" }}
            help    "Retired in 1.104.0, when #67 gave every channel its own beauty output; kept invisible so scenes saved with 1.103 load without a Skipping unrecognized parameter warning."
        }}
        parm {{
            name    "unpack_outputs"
            label   "AOV Outputs"
            type    toggle
            default {{ "0" }}
            help    "Keep a Cable Unpack beside the node for each AOV cable, with one output per AOV in the cable, kept in step with it."
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
            help    "Resolve the render again and import the scene."
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
    input	lpe	lpe
    output	rgba	rgba
    signature	default	Default	{{ RGBA Cable }}	{{ RGBA }}

    help {{
	"A channel's light groups mixed back into its beauty: the graded groups plus Rest (the beauty minus the groups). Inputs: the import's rgba beauty (wired for you on sync) and its lpe cable (the light groups), in the order of the import's outputs; a cable wired from the import's aov moves to lpe on sync."
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
    output	comp	comp
    signature	default	Default	{{ RGBA }}	{{ RGBA }}

    help {{
	"The end of a comp. Submit renders every export_comp in the workfile as one composite version per shot. Its input passes through, so the node shows what it exports."
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
            joinnext
            default {{ "0" }}
            help    "Also render the first, middle and last frames (or the List beside it) as a quick task before the full range."
            {_callback('export_comp', 'refresh')}
        }}
        parm {{
            name    "preview_frame_list"
            label   "List"
            type    string
            default {{ "" }}
            disablewhen "{{ preview_frames == 0 }}"
            help    "The Preview Frames List: frames to render first as the preview, space separated, in this order (key poses, say). Empty: the first, middle and last frame. A frame outside the comp's frames or off the Step is dropped, with a warning."
            {_callback('export_comp', 'refresh')}
        }}
        parm {{
            name    "frame_range"
            label   "Frame Range"
            type    string
            default {{ "from_context" }}
            menu {{
                [ "from tumblepipe.pipe.houdini import frame_range" ]
                [ "from tumblepipe.pipe.houdini.cops import export_comp" ]
                [ "" ]
                [ "return frame_range.menu(export_comp.ExportComp(hou.pwd()), (('from_context', 'From Config'), ('from_settings', 'From Settings')))" ]
                language python
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


# COP subnet output types of import_render's outputs, by signature kind.
SUBNET_TYPES = {'RGBA': 'vector4', 'Cable': 'cable', 'Geometry': 'geo', 'Metadata': 'metadata'}


def build_import_render(root):
    """The dive (File COPs and cable packs, rebuilt by Python; its outputs
    only ever appended to) and an embedded th::import_scene for geometry
    and camera, wired to the outputs by name. Left to right, as the dive
    and the comp template read: the dive and the scene, the scene's
    switches, the outputs."""
    from tumblepipe.pipe import comp
    from tumblepipe.pipe.houdini.cops import import_render as module
    _set_io(root, [], [(name, SUBNET_TYPES[kind]) for name, _, kind in IMPORT_RENDER_OUTPUTS])
    stride, row = comp.COLUMN_STRIDE, comp.ROW
    dive = root.createNode('subnet', module.DIVE)
    _set_io(dive, [], list(module.DIVE_OUTPUTS))
    dive.setColor(hou.Color((1.0, 0.0, 1.0)))
    dive.setPosition(hou.Vector2(0, 0))
    scene = root.createNode('th::import_scene::1.0', module.SCENE)
    # Its OnCreated ran an import here, with no shot: undo the bypass and
    # labels that left, so an instance starts as a fresh import_scene.
    shot = scene.node('lopnet/import_shot')
    shot.bypass(False)
    shot.setComment('')
    for label in ('version_label', 'entity_label'):
        if scene.parm(label) is not None:
            scene.parm(label).revertToDefaults()
    # The scene's shot and channel are the node's (All channels: default);
    # the rest is the Scene folder's.
    scene.parm('entity').setExpression('chs("../entity")', hou.exprLanguage.Hscript)
    scene.parm('channel').setExpression(
        'strreplace(chs("../channel"),"*","default")',
        hou.exprLanguage.Hscript,
    )
    for inner, outer in (('department', 'scene_department'), ('version', 'scene_version'),
                         ('camera_prim', 'camera_prim'), ('primpattern', 'primpattern'),
                         ('purpose', 'purpose')):
        scene.parm(inner).setExpression(f'chs("../{outer}")', hou.exprLanguage.Hscript)
    for name in ('timesample', 'staticimportframe'):
        scene.parm(name).setExpression(f'ch("../{name}")', hou.exprLanguage.Hscript)
    scene.setPosition(hou.Vector2(0, -2 * row))
    # The scene reaches its outputs only once it imported (camera: and its
    # camera cooks): Copernicus cooks every output of the subnet together,
    # so a camera that cannot cook failed the renders too. An unread
    # switch input is an empty output.
    switches = dict()
    for step, (name, output, ready) in enumerate((
            ('geometry', 0, module.SCENE_READY_PARM),
            ('camera', 1, module.CAMERA_READY_PARM))):
        switch = root.createNode('switch', f'{name}_out')
        switch.setInput(1, scene, output)
        switch.parm('input').setExpression(f'ch("../{ready}")', hou.exprLanguage.Hscript)
        switch.setPosition(hou.Vector2(stride, -(2 + step) * row))
        switches[name] = (switch, 0)
    outputs = root.node('outputs')
    outputs.setPosition(hou.Vector2(2 * stride, 0))
    if root.node('inputs') is not None:
        root.node('inputs').setPosition(hou.Vector2(-stride, 0))
    dive_index = {name: index for index, (name, _) in enumerate(module.DIVE_OUTPUTS)}
    sources = {name: (dive, dive_index[label]) for name, label in module.DIVE_SOURCES.items()}
    sources.update(switches)
    for index, name in enumerate(module.OUTPUTS):
        outputs.setInput(index, *sources[name])
    return [module.DIVE, module.SCENE, f'{module.SCENE}/lopnet/import_shot']


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
    # The input passes through, so the node shows what it exports in the
    # viewer and its thumbnail (Remy, 2026-11-11: it showed nothing).
    _set_io(root, [('comp', 'vector4')], [('comp', 'vector4')])
    root.node('outputs').setInput(0, root.node('inputs'), 0)
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


# (name, label, build, dialog script, min inputs, max inputs). import_scene
# comes before import_render, which embeds it: each built asset is
# installed in the session before the next is built.
NODES = [
    ('light_mix', 'th light mix', build_light_mix, LIGHT_MIX_DS, 1, 2),
    ('export_comp', 'th export comp', build_export_comp, EXPORT_COMP_DS, 1, 1),
    ('import_scene', 'th import scene', build_import_scene, IMPORT_SCENE_DS, 0, 0),
    ('import_render', 'th import render', build_import_render, IMPORT_RENDER_DS, 0, 0),
]

ON_LOADED = {
    'import_render': (
        'import tumblepipe.pipe.houdini.cops.import_render as th_node\n\n'
        "th_node.on_loaded(kwargs['node'])\n"
    ),
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
    for fallbacks in target.glob('*/Contents.dir/.OPfallbacks'):
        fallbacks.write_bytes(portable_fallbacks(fallbacks.read_bytes()))
    print(f'wrote {target}')


def portable_fallbacks(text: bytes) -> bytes:
    """An embedded asset's fallback library paths (``.OPfallbacks``: where
    a nested th:: type was found when the asset was built) as the package
    ships them, ``otls/<library>.hda``, not this machine's build or temp
    directories. Houdini's own libraries are left as they are."""
    lines = []
    for line in text.decode('utf-8').splitlines():
        type_name, _, path = line.partition(' ')
        if type_name.startswith('th::') and path:
            line = f'{type_name} otls/{Path(path).name}'
        if line not in lines:
            lines.append(line)
    return ''.join(f'{line}\n' for line in lines).encode('utf-8')


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        for name, label, build, dialog_script, min_inputs, max_inputs in NODES:
            hda_path = make(name, label, build, dialog_script, min_inputs, max_inputs, temp_dir)
            # The asset just built, with its DialogScript and sections, for
            # an asset built after it that embeds it (import_render's scene).
            hou.hda.installFile(str(hda_path), force_use_assets=True)
            expand(hda_path, name)


if __name__ == '__main__':
    main()
