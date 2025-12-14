from tempfile import TemporaryDirectory
from pathlib import Path
import datetime as dt

import hou

from tumblehead.api import (
    fix_path,
    path_str,
    get_user_name,
    default_client
)
from tumblehead.util.uri import Uri
from tumblehead.util.io import (
    load_json,
    store_json
)
from tumblehead.config.timeline import FrameRange, get_frame_range
from tumblehead.config.department import list_departments
from tumblehead.config.variants import list_variants
from tumblehead.config.farm import list_pools
import tumblehead.pipe.houdini.nodes as ns
import tumblehead.pipe.context as ctx
from tumblehead.pipe.context import get_aov_names_from_context
from tumblehead.pipe.houdini import util
from tumblehead.pipe.paths import (
    latest_export_path,
    load_entity_context
)

api = default_client()


def _entity_from_context_json():
    # Path to current workfile
    file_path = Path(hou.hipFile.path())
    if not file_path.exists(): return None

    # Look for context.json in the workfile directory
    context_json_path = file_path.parent / "context.json"
    if not context_json_path.exists(): return None

    # Load context using shared helper
    context = load_entity_context(context_json_path)
    if context is None: return None

    # Verify it's a shot entity
    if context.entity_uri.purpose not in ('entity', 'groups'): return None
    if len(context.entity_uri.segments) < 1: return None
    if context.entity_uri.segments[0] != 'shots': return None

    return context

def _get_output(node, index):
    connections = node.outputConnectors()
    if len(connections) == 0: return []
    if len(connections) <= index: return []
    return [
        connection.outputNode()
        for connection in connections[index]
    ]

def _get_preview_node(node):
    preview_nodes = _get_output(node, 1)
    if len(preview_nodes) == 0: return None
    return preview_nodes[0]

def _ensure_preview_node(node):
    preview_node = _get_preview_node(node)
    if preview_node is not None: return preview_node
    preview_node = node.parent().createNode('null', f'{node.name()}_preview')
    preview_node.setInput(0, node, 1)
    node.parent().layoutChildren(items = [node, preview_node])
    return preview_node

class SubmitRender(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_shot_uris(self) -> list[str]:
        shot_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/shots'),
            closure = True
        )
        uris = [entity.uri for entity in shot_entities]
        return ['from_context'] + [str(uri) for uri in uris]

    def list_shot_department_names(self) -> list[str]:
        names = [d.name for d in list_departments('shots') if d.renderable]
        return ['from_context'] + names

    def list_variant_names(self):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return []
        return list_variants(shot_uri)
    
    def list_render_department_names(self):
        return [d.name for d in list_departments('render') if d.renderable]

    def list_pool_names(self):
        return [pool.name for pool in list_pools()]
    
    def list_aov_names(self, variant_name):
        shot_uri = self.get_shot_uri()
        if shot_uri is None: return []
        department_name = self.get_shot_department_name()

        # Try to get from layer export context
        export_path = latest_export_path(shot_uri, variant_name, department_name)
        if export_path is not None:
            context_path = export_path / 'context.json'
            context_data = load_json(context_path)
            aov_names = get_aov_names_from_context(context_data, variant_name)
            if aov_names:
                return aov_names

        # Fallback: read from root layer context
        root_context_path = api.storage.resolve(Uri.parse_unsafe('config:/usd/context.json'))
        root_context = load_json(root_context_path)
        aov_names = get_aov_names_from_context(root_context)
        if aov_names:
            return aov_names

        # Final fallback: get from USD stage directly
        stage_node = self.native().node('import_shot')
        if stage_node is not None:
            root = stage_node.stage().GetPseudoRoot()
            return [
                aov_path.rsplit('/', 1)[-1].lower()
                for aov_path in util.list_render_vars(root)
            ]

        return []
    
    def get_shot_uri(self) -> Uri | None:
        shot_uri_raw = self.parm('shot').eval()
        if shot_uri_raw == 'from_context':
            context = _entity_from_context_json()
            if context is not None:
                return context.entity_uri
            # Fall back to first available shot if context not found
            shot_uris = self.list_shot_uris()
            if len(shot_uris) <= 1: return None  # Only 'from_context' means no real URIs
            return Uri.parse_unsafe(shot_uris[1])  # Skip 'from_context'
        # From settings
        shot_uris = self.list_shot_uris()
        if len(shot_uris) <= 1: return None  # Only 'from_context' means no real URIs
        if len(shot_uri_raw) == 0: return Uri.parse_unsafe(shot_uris[1])  # Skip 'from_context'
        if shot_uri_raw not in shot_uris: return None  # Compare strings
        return Uri.parse_unsafe(shot_uri_raw)

    def get_shot_department_name(self):
        shot_department_name = self.parm('shot_department').eval()
        if shot_department_name == 'from_context':
            context = _entity_from_context_json()
            if context is not None:
                return context.department_name
            # Fall back to first available department if context not found
            shot_department_names = self.list_shot_department_names()
            if len(shot_department_names) <= 1: return None  # Only 'from_context' means no real names
            return shot_department_names[1]  # Skip 'from_context'
        # From settings
        shot_department_names = self.list_shot_department_names()
        if len(shot_department_names) <= 1: return None  # Only 'from_context' means no real names
        if len(shot_department_name) == 0: return shot_department_names[1]  # Skip 'from_context'
        if shot_department_name not in shot_department_names: return None
        return shot_department_name

    def get_variant_name(self):
        variant_names = self.list_variant_names()
        if len(variant_names) == 0: return None
        variant_name = self.parm('variant').eval()
        if len(variant_name) == 0: return variant_names[0]
        if variant_name == 'all': return 'all'
        if variant_name not in variant_names: return None
        return variant_name

    def get_variant_names(self):
        variant_name = self.get_variant_name()
        if variant_name is None: return []
        if variant_name != 'all': return [variant_name]
        return self.list_variant_names()

    def get_render_department_name(self):
        render_department_names = self.list_render_department_names()
        if len(render_department_names) == 0: return None
        render_department_name = self.parm('render_department').eval()
        if len(render_department_name) == 0: return render_department_names[0]
        if render_department_name not in render_department_names: return None
        return render_department_name

    def get_frame_range_source(self):
        return self.parm('frame_range').eval()
    
    def get_frame_range(self):
        frame_range_source = self.get_frame_range_source()
        match frame_range_source:
            case 'from_config':
                shot_uri = self.get_shot_uri()
                if shot_uri is None: return None
                frame_range = get_frame_range(shot_uri)
                return frame_range
            case 'from_settings':
                return FrameRange(
                    self.parm('frame_settingsx').eval(),
                    self.parm('frame_settingsy').eval(),
                    self.parm('roll_settingsx').eval(),
                    self.parm('roll_settingsy').eval()
                )
            case _:
                assert False, f'Unknown frame range token: {frame_range_source}'
    
    def get_pool_name(self):
        pool_names = self.list_pool_names()
        if len(pool_names) == 0: return None
        pool_name = self.parm('pool').eval()
        if pool_name not in pool_names: return pool_names[0]
        return pool_name
    
    def get_priority_full(self):
        return self.parm('full_priority').eval()

    def get_priority_partial(self):
        return self.parm('partial_priority').eval()

    def get_step_size(self):
        return self.parm('stepsize').eval()
    
    def get_batch_size(self):
        return self.parm('batchsize').eval()
    
    def get_samples(self):
        render_settings = self.parm('render_settings').eval()
        match render_settings:
            case 'from_export':
                stage = self.native().node('preview').stage()
                root = stage.GetPseudoRoot()
                render_settings_prim = root.GetPrimAtPath(
                    '/Render/rendersettings'
                )
                if not render_settings_prim.IsValid(): return None
                samples_attribute = render_settings_prim.GetAttribute(
                    'karma:global:pathtracedsamples'
                )
                if not samples_attribute.IsValid(): return None
                return samples_attribute.Get()
            case 'from_settings':
                return self.parm('samples').eval()
            case _: assert False, f'Unknown render settings token: {render_settings}'
        
    def get_tile_count(self):
        return int(self.parm('tile_count').eval())
    
    def get_submit_partial(self):
        return self.parm('submit_partial').eval()
    
    def get_submit_full(self):
        return self.parm('submit_full').eval()
    
    def get_partial_frames(self):
        return (
            self.parm('specific_framesx').eval(),
            self.parm('specific_framesy').eval(),
            self.parm('specific_framesz').eval()
        )
    
    def get_partial_denoise(self):
        return bool(self.parm('partial_denoise_task').eval())

    def get_full_denoise(self):
        return bool(self.parm('full_denoise_task').eval())

    def _update_labels(self):
        """Update label parameters to show resolved values when 'from_context' is selected."""
        shot_raw = self.parm('shot').eval()
        if shot_raw == 'from_context':
            shot_uri = self.get_shot_uri()
            self.parm('shot_label').set(str(shot_uri) if shot_uri else '')
        else:
            self.parm('shot_label').set('')

        shot_department_raw = self.parm('shot_department').eval()
        if shot_department_raw == 'from_context':
            shot_department_name = self.get_shot_department_name()
            self.parm('shot_department_label').set(shot_department_name if shot_department_name else '')
        else:
            self.parm('shot_department_label').set('')

    def _initialize(self):
        """Initialize node with defaults from workfile context and update labels."""
        # If no context, set first available shot
        entity = _entity_from_context_json()
        if entity is None:
            shot_uris = self.list_shot_uris()
            if len(shot_uris) > 1:  # Skip 'from_context'
                self.set_shot_uri(Uri.parse_unsafe(shot_uris[1]))

        # Update labels to show resolved values
        self._update_labels()

    def set_shot_uri(self, shot_uri: Uri):
        shot_uris = self.list_shot_uris()
        if str(shot_uri) not in shot_uris: return  # Compare strings
        self.parm('shot').set(str(shot_uri))

    def set_shot_department_name(self, shot_department_name):
        shot_department_names = self.list_shot_department_names()
        if shot_department_name not in shot_department_names: return
        self.parm('shot_department').set(shot_department_name)
    
    def set_variant_name(self, variant_name):
        variant_names = self.list_variant_names()
        if variant_name not in variant_names: return
        self.parm('variant').set(variant_name)
    
    def set_render_department_name(self, render_department_name):
        render_department_names = self.list_render_department_names()
        if render_department_name not in render_department_names: return
        self.parm('render_department').set(render_department_name)
    
    def set_frame_range_source(self, frame_range_source):
        valid_sources = ['from_config', 'from_settings']
        if frame_range_source not in valid_sources: return
        self.parm('frame_range_source').set(frame_range_source)
    
    def set_frame_range(self, frame_range):
        self.parm('frame_settingsx').set(frame_range.start_frame)
        self.parm('frame_settingsy').set(frame_range.end_frame)
        self.parm('roll_settingsx').set(frame_range.start_roll)
        self.parm('roll_settingsy').set(frame_range.end_roll)
    
    def set_pool_name(self, pool_name):
        pool_names = self.list_pool_names()
        if pool_name not in pool_names: return
        self.parm('pool').set(pool_name)
    
    def set_priority_full(self, priority):
        assert priority >= 0 and priority <= 100, 'Invalid priority'
        self.parm('full_priority').set(priority)

    def set_priority_partial(self, priority):
        assert priority >= 0 and priority <= 100, 'Invalid priority'
        self.parm('partial_priority').set(priority)

    def set_batch_size(self, batch_size):
        assert batch_size >= 1, 'Invalid batch size'
        self.parm('batchsize').set(batch_size)
    
    def set_submit_partial(self, submit_partial):
        self.parm('submit_partial').set(submit_partial)
    
    def set_submit_full(self, submit_full):
        self.parm('submit_full').set(submit_full)
    
    def build_preview(self):

        # Parameters
        shot_uri = self.get_shot_uri()
        variant_name = self.get_variant_name()
        if variant_name == 'all':
            variant_names = self.list_variant_names()
            variant_name = variant_names[0]

        # Set the preview parameters using URI path
        self.parm('preview_shot').set(str(shot_uri))
        self.parm('preview_variant').set(variant_name)

        # Create the preview null node
        preview_node = _ensure_preview_node(self.native())
        preview_node.setSelected(True, clear_all_selected=True)
        preview_node.setDisplayFlag(True)

        # Execute the preview
        self.native().node('preview').parm('build').pressButton()

    def submit_farm(self):

        # Import the job module
        from tumblehead.farm.jobs.houdini.stage_render import (
            job as stage_render_job
        )

        # Submit the job
        return self._submit(stage_render_job)
    
    def submit_cloud(self):

        # Import the job module
        from tumblehead.farm.jobs.houdini.cloud_stage_render import (
            job as cloud_stage_render_job
        )

        # Submit the job
        return self._submit(cloud_stage_render_job)

    def _submit(self, stage_render_job):

        # Get shot information
        shot_uri = self.get_shot_uri()
        shot_department_name = self.get_shot_department_name()

        # Get variant names to process
        variant_names = self.get_variant_names()
        if len(variant_names) == 0:
            raise ValueError("No variants to submit")

        # Prepare common settings
        user_name = get_user_name()
        pool_name = self.get_pool_name()
        full_priority = self.get_priority_full()
        partial_priority = self.get_priority_partial()
        render_department_name = self.get_render_department_name()
        samples = self.get_samples()
        tile_count = self.get_tile_count()
        frame_range = self.get_frame_range()
        render_range = frame_range.full_range()
        step_size = self.get_step_size()
        batch_size = self.get_batch_size()
        timestamp = dt.datetime.now()

        # Collect AOVs for all variants
        all_aov_names = []
        for variant_name in variant_names:
            layer_aov_names = self.list_aov_names(variant_name)
            all_aov_names.extend(layer_aov_names)

        # Remove duplicates while preserving order
        seen = set()
        aov_names = []
        for aov in all_aov_names:
            if aov in seen: continue
            seen.add(aov)
            aov_names.append(aov)

        # Prepare tasks
        tasks = dict()

        # Add the stage task
        tasks['stage'] = dict(
            priority = full_priority,
            channel_name = 'exports'
        )

        # Maybe add partial render task
        if self.get_submit_partial():
            denoise = self.get_partial_denoise()
            tasks['partial_render'] = dict(
                priority = partial_priority,
                denoise = denoise,
                channel_name = 'previews'
            )

        # Maybe add full render task
        if self.get_submit_full():
            denoise = self.get_full_denoise()
            tasks['full_render'] = dict(
                priority = full_priority,
                denoise = denoise,
                channel_name = 'renders'
            )

        # Open temporary directory
        root_temp_path = fix_path(api.storage.resolve(Uri.parse_unsafe('temp:/')))
        root_temp_path.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=path_str(root_temp_path)) as temp_dir:
            temp_path = Path(temp_dir)

            # Save the render settings
            render_settings_path = temp_path / 'render_settings.json'
            relative_render_settings_path = (
                render_settings_path
                .relative_to(temp_path)
            )
            store_json(render_settings_path, dict(
                variant_names = variant_names,
                aov_names = aov_names,
                overrides = {
                    'karma:global:pathtracedsamples': samples
                }
            ))

            # Submit the job
            stage_render_job.submit(dict(
                entity = dict(
                    uri = str(shot_uri),
                    department = shot_department_name
                ),
                settings = dict(
                    user_name = user_name,
                    purpose = 'render',
                    pool_name = pool_name,
                    variant_names = variant_names,
                    render_department_name = render_department_name,
                    render_settings_path = path_str(
                        relative_render_settings_path
                    ),
                    tile_count = tile_count,
                    first_frame = render_range.first_frame,
                    last_frame = render_range.last_frame,
                    step_size = step_size,
                    batch_size = batch_size,
                ),
                tasks = tasks
            ), {
                render_settings_path: relative_render_settings_path
            })

        # Update node comment
        native = self.native()
        variants_text = ', '.join(variant_names)
        new_comment = (
            f'{user_name} submitted:\n'
            f'{variants_text}\n'
            f'{samples} samples\n'
            f'{timestamp.strftime("%Y-%m-%d %H:%M")}\n'
        )
        native.setComment(new_comment)
        native.setGenericFlag(hou.nodeFlag.DisplayComment, True)

        # Show success message
        tasks_text = []
        if 'stage' in tasks:
            tasks_text.append('Stage')
        if 'partial_render' in tasks:
            tasks_text.append('Partial Render')
        if 'full_render' in tasks:
            tasks_text.append('Full Render')

        hou.ui.displayMessage(
            f"Render job submitted to farm\n\n"
            f"Shot: {shot_uri}\n"
            f"Department: {shot_department_name}\n"
            f"Variants: {variants_text}\n"
            f"Samples: {samples}\n"
            f"Tasks: {', '.join(tasks_text)}",
            title="Render Submitted"
        )
    
def create(scene, name):
    node_type = ns.find_node_type('submit_render', 'Lop')
    assert node_type is not None, 'Could not find submit_render node type'
    native = scene.node(name)
    if native is not None: return SubmitRender(native)
    return SubmitRender(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_DEFAULT)

def on_created(raw_node):
    # Set node style
    set_style(raw_node)

    node = SubmitRender(raw_node)
    node._initialize()

def build_preview():
    raw_node = hou.pwd()
    node = SubmitRender(raw_node)
    node.build_preview()

def submit_farm():
    raw_node = hou.pwd()
    node = SubmitRender(raw_node)
    node.submit_farm()