"""Render-settings override script shared by the hython-side stage/export tasks.

The stage, cloud_stage and export tasks all inject the same Python-node
script into the staged scene: it re-reads the render settings JSON at cook
time and applies its ``overrides`` onto the ``/Render/rendersettings`` prim.
"""

from pathlib import Path

from tumblepipe.api import path_str


def get_render_settings_script(render_settings_path: Path) -> str:
    _render_settings_path = path_str(render_settings_path)
    return (
        'from pathlib import Path\n'
        'import json\n'
        'import hou\n'
        '\n'
        'def _load_json(path):\n'
        '    if not path.exists(): return None\n'
        '    with path.open("r") as file:\n'
        '        return json.load(file)\n'
        '\n'
        'def _edit_render_settings():\n'
        '    \n'
        '    # Get context\n'
        '    node = hou.pwd()\n'
        '    stage = node.editableStage()\n'
        '    root = stage.GetPseudoRoot()\n'
        '    \n'
        '    # Load render settings\n'
        f'    render_settings_path = Path("{_render_settings_path}")\n'
        '    render_settings_data = _load_json(render_settings_path)\n'
        '    if render_settings_data is None: return\n'
        '    if "overrides" not in render_settings_data: return\n'
        '    \n'
        '    # Get render settings prim\n'
        '    render_settings_prim = root.GetPrimAtPath(\n'
        '        "/Render/rendersettings"\n'
        '    )\n'
        '    if not render_settings_prim.IsValid(): return\n'
        '    \n'
        '    # Edit the render settings\n'
        '    overrides = render_settings_data["overrides"]\n'
        '    for property, value in overrides.items():\n'
        '        attribute = render_settings_prim.GetAttribute(property)\n'
        '        if not attribute.IsValid(): continue\n'
        '        attribute.Set(value)\n'
        '\n'
        '_edit_render_settings()\n'
    )
