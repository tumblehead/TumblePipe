from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import get_user_name, path_str, default_client
from tumblehead.util.io import store_json
from tumblehead.util.uri import Uri
from tumblehead.config.department import list_departments
from tumblehead.config.groups import is_group_uri, get_group
from tumblehead.config.timeline import FrameRange, get_frame_range
from tumblehead.config.shots import list_render_layers
from tumblehead.pipe.houdini import util
import tumblehead.pipe.houdini.nodes as ns
from tumblehead.pipe.paths import (
    latest_render_layer_export_path,
    next_render_layer_export_file_path,
    get_workfile_context
)

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/export_render_layer')

def _sort_aov_names(aov_names):
    beauty = None
    lpes = []
    mattes = []
    mses = []
    other = []
    for aov_name in aov_names:
        name = aov_name.lower()
        if name == 'beauty': beauty = aov_name; continue
        if name.endswith('_mse'): mses.append(aov_name); continue
        if name.startswith('beauty_'): lpes.append(aov_name); continue
        if name.startswith('objid_'): mattes.append(aov_name); continue
        if name.startswith('holdout_'): mattes.append(aov_name); continue
        other.append(aov_name)
    other.sort()
    result = [] if beauty is None else [beauty]
    result += lpes
    result += mattes
    result += mses
    result += other
    return result

class ExportRenderLayer(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_shot_uris(self) -> list[Uri]:
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        return list(shot_entities)

    def list_department_names(self):
        shot_departments = list_departments('shots')
        if len(shot_departments) == 0: return []
        shot_department_names = [dept.name for dept in shot_departments]
        default_values = api.config.get_properties(DEFAULTS_URI)
        return [
            department_name
            for department_name in default_values['departments']
            if department_name in shot_department_names
        ]

    def list_render_layer_names(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return []
        return list_render_layers(shot_uri)
    
    def get_entity_source(self):
        return self.parm('entity_source').eval()

    def get_shot_uri(self) -> Uri | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
                if context is None: return None
                return context.entity_uri
            case 'from_settings':
                shot_uris = self.list_shot_uris()
                if len(shot_uris) == 0: return None
                shot_uri_raw = self.parm('shot').eval()
                if len(shot_uri_raw) == 0: return shot_uris[0]
                shot_uri = Uri.parse_unsafe(shot_uri_raw)
                if shot_uri not in shot_uris: return None
                return shot_uri
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')

    def get_department_name(self) -> str | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
                if context is None: return None
                return context.department_name
            case 'from_settings':
                department_names = self.list_department_names()
                if len(department_names) == 0: return None
                department_name = self.parm('department').eval()
                if len(department_name) == 0: return department_names[0]
                if department_name not in department_names: return None
                return department_name
            case _:
                raise AssertionError(f'Unknown entity source token: {entity_source}')
    
    def get_render_layer_name(self):
        render_layer_names = self.list_render_layer_names()
        if len(render_layer_names) == 0: return None
        render_layer_name = self.parm('layer').eval()
        if len(render_layer_name) == 0: return render_layer_names[0]
        if render_layer_name not in render_layer_names: return None
        return render_layer_name
    
    def get_frame_range_source(self):
        return self.parm('frame_range').eval()
    
    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_context':
                shot_uri = self.get_shot_uri()
                if shot_uri is None: return None
                frame_range = get_frame_range(shot_uri)
                return frame_range, 1
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                ), self.parm('frame_settingsz').eval()
            case _:
                assert False, f'Unknown frame range token: {frame_range_source}'

    def set_entity_source(self, entity_source):
        valid_sources = ['from_context', 'from_settings']
        if entity_source not in valid_sources: return
        self.parm('entity_source').set(entity_source)

    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if shot_uri not in shot_uris: return
        self.parm('shot').set(str(shot_uri))
    
    def set_department_name(self, department_name):
        department_names = self.list_department_names()
        if department_name not in department_names: return
        self.parm('department').set(department_name)
    
    def set_layer_name(self, layer_name):
        layer_names = self.list_render_layer_names()
        if layer_name not in layer_names: return
        self.parm('layer').set(layer_name)

    def _do_export_to_shot(self, stage_node, shot_uri: Uri, department_name,
                            render_layer_name, frame_range, frame_step):
        """Core export logic for a single shot

        Args:
            stage_node: Stage input node
            shot_uri: Shot URI
            department_name: Department name
            render_layer_name: Render layer name
            frame_range: Frame range for this shot
            frame_step: Frame step size

        Returns:
            Tuple of (version_name, timestamp, user_name)
        """
        render_range = frame_range.full_range()
        timestamp = dt.datetime.now()
        user_name = get_user_name()

        # Scrape stage for aovs
        aov_names = list()
        root = stage_node.stage().GetPseudoRoot()
        for aov_path in util.list_render_vars(root):
            aov_names.append(aov_path.rsplit('/', 1)[-1].lower())

        # Get export file path
        file_path = next_render_layer_export_file_path(
            shot_uri,
            department_name,
            render_layer_name
        )
        version_path = file_path.parent
        version_name = version_path.name

        # Export layer
        self.parm('export_lopoutput').set(path_str(file_path))
        self.parm('export_f1').set(render_range.first_frame)
        self.parm('export_f2').set(render_range.last_frame)
        self.parm('export_f3').set(frame_step)
        self.parm('export_execute').pressButton()

        # Store context
        context_path = version_path / 'context.json'
        context = dict(
            inputs = [],
            outputs = [dict(
                entity = str(shot_uri),
                department = department_name,
                render_layer = render_layer_name,
                version = version_name,
                timestamp = timestamp.isoformat(),
                user = user_name,
                parameters = {
                    'aov_names': _sort_aov_names(aov_names)
                }
            )]
        )
        store_json(context_path, context)

        return version_name, timestamp, user_name

    def execute(self):
        """Main export method - handles both individual shots and group exports"""

        # Nodes
        native = self.native()
        stage_node = native.node('IN_stage')

        # Parameters
        shot_uri = self.get_shot_uri()
        department_name = self.get_department_name()
        render_layer_name = self.get_render_layer_name()
        frame_range_result = self.get_frame_range()

        # Check parameters
        if shot_uri is None: return
        if department_name is None: return
        if render_layer_name is None: return
        if frame_range_result is None: return

        frame_range, frame_step = frame_range_result

        # Check if we're exporting to a group
        if is_group_uri(shot_uri):
            # Group export - split to member shots
            group = get_group(shot_uri)
            if group is None:
                hou.ui.displayMessage(
                    f"Group not found: {shot_uri}",
                    severity=hou.severityType.Error
                )
                return

            # Export to each member shot
            exported_members = []
            last_timestamp = None
            last_user_name = None

            for member_uri in group.members:
                try:
                    # Get member's original frame range
                    member_frame_range = get_frame_range(member_uri)
                    if member_frame_range is None:
                        continue

                    # Set Houdini timeline to member's timeline for correct frame cooking
                    util.set_frame_range(member_frame_range)

                    # Export this member
                    version_name, timestamp, user_name = self._do_export_to_shot(
                        stage_node,
                        member_uri,
                        department_name,
                        render_layer_name,
                        member_frame_range,
                        frame_step
                    )

                    # Track for comment
                    last_timestamp = timestamp
                    last_user_name = user_name

                    # Add to exported members list for display
                    exported_members.append(f"{member_uri} ({version_name})")
                except Exception as e:
                    # Skip failed members but log the error
                    print(f"Failed to export member {member_uri}: {e}")
                    pass

            # Update node comment
            if last_timestamp and last_user_name:
                native.setComment(
                    f'group export: {shot_uri.segments[-1]}\n'
                    f'{last_timestamp.strftime("%Y-%m-%d %H:%M:%S")}\n'
                    f'by {last_user_name}\n'
                    f'members: {", ".join(exported_members)}'
                )
                native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

        else:
            # Individual shot export
            version_name, timestamp, user_name = self._do_export_to_shot(
                stage_node,
                shot_uri,
                department_name,
                render_layer_name,
                frame_range,
                frame_step
            )

            # Update node comment
            native.setComment(
                'last export: '
                f'{version_name} \n'
                f'{timestamp.strftime("%Y-%m-%d %H:%M:%S")} \n'
                f'by {user_name}'
            )
            native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

    def open_location(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return
        department_name = self.get_department_name()
        if department_name is None: return
        render_layer_name = self.get_render_layer_name()
        if render_layer_name is None: return

        # Find latest version
        export_path = latest_render_layer_export_path(
            shot_uri,
            department_name,
            render_layer_name
        )
        if export_path is None: return
        if not export_path.exists(): return
        hou.ui.showInFileBrowser(f'{path_str(export_path)}')

def create(scene, name):
    node_type = ns.find_node_type('export_render_layer', 'Lop')
    assert node_type is not None, 'Could not find export_render_layer node type'
    native = scene.node(name)
    if native is not None: return ExportRenderLayer(native)
    return ExportRenderLayer(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_EXPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Check if workfile context exists
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is not None: return  # Context exists → keep 'from_context'

    # No context → change entity source to settings
    node = ExportRenderLayer(raw_node)
    node.set_entity_source('from_settings')

def execute():
    raw_node = hou.pwd()
    node = ExportRenderLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ExportRenderLayer(raw_node)
    node.open_location()