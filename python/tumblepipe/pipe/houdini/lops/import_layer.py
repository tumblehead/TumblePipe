import logging
from pathlib import Path

import hou

logger = logging.getLogger(__name__)

from tumblepipe.api import path_str, api
from tumblepipe.util.uri import Uri
from tumblepipe.util.io import load_json
from tumblepipe.config.variants import list_variants
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.util import uri_to_prim_path
from tumblepipe.pipe.houdini.lops import import_asset
import tumblepipe.pipe.context as ctx
from tumblepipe.pipe.paths import (
    list_version_paths,
    get_workfile_context,
    latest_export_path,
    get_export_uri,
    latest_shared_export_path
)


def _valid_version_path(path: Path) -> bool:
    context_path = path / 'context.json'
    return context_path.exists()

def _metadata_update_script(
    entity_uri: Uri,
    department_name: str,
    version_name: str,
    assets: list[dict]
) -> str:
    """
    Generate Python script to update asset metadata inputs on scene prims.

    For each asset in the imported layer, adds the current department/version
    to the asset's inputs array (if not already present).
    """
    def _indent(lines):
        return [f"    {line}" for line in lines]

    header = [
        'import hou',
        '',
        'from tumblepipe.pipe.houdini import util',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        '',
        'def update(root):',
    ]

    content = []
    for asset_info in assets:
        asset_uri_str = asset_info['asset']
        asset_uri = Uri.parse_unsafe(asset_uri_str)
        prim_path = uri_to_prim_path(asset_uri)

        instance_name = asset_uri.segments[-1] if asset_uri.segments else ''
        content.extend([
            f"# Asset: {asset_uri_str}",
            f"prim = root.GetPrimAtPath('{prim_path}')",
            "if prim.IsValid():",
            "    metadata = util.get_metadata(prim)",
            "    if metadata is None:",
            f"        metadata = {{'uri': '{asset_uri_str}', 'instance': '{instance_name}', 'inputs': []}}",
            "    util.add_metadata_input(metadata, {",
            f"        'uri': '{str(entity_uri)}',",
            f"        'department': '{department_name}',",
            f"        'version': '{version_name}',",
            "    })",
            "    util.set_metadata(prim, metadata)",
            "",
        ])

    footer = [
        'update(root)',
        '',
    ]

    script = header + _indent(content) + footer
    return '\n'.join(script)

def _inline_marker_script(assets: list[dict]) -> str:
    """
    Generate the metadata script for 'inline' import mode.

    Marks each asset prim from the imported layer as 'inlined' instead of
    updating its pipeline metadata: the layer content is deliberately baked
    into the export, so its assets must not be scraped and re-referenced,
    and the publish guards must not read the missing metadata as an
    accidental drop. Targets only the assets recorded in the imported
    layer's context.json — assets flowing through from upstream nodes are
    untouched.
    """
    script_lines = [
        'import hou',
        '',
        'from tumblepipe.pipe.houdini import util',
        '',
        'node = hou.pwd()',
        'stage = node.editableStage()',
        'root = stage.GetPseudoRoot()',
        '',
    ]

    for asset_info in assets:
        asset_uri = Uri.parse_unsafe(asset_info['asset'])
        prim_path = uri_to_prim_path(asset_uri)
        script_lines.extend([
            f"prim = root.GetPrimAtPath('{prim_path}')",
            'if prim.IsValid():',
            '    util.mark_inlined(prim)',
            '',
        ])

    return '\n'.join(script_lines)

class ImportLayer(EntityNode):

    def __init__(self, native):
        super().__init__(native)

    def list_department_names(self) -> list[str]:
        entity_type = self.get_entity_type()
        if entity_type is None:
            return ['from_context']
        context_name = 'assets' if entity_type == 'asset' else 'shots'
        names = [
            d.name for d in self.scoped_departments(context_name)
            if d.publishable
        ]
        return ['from_context'] + names

    def list_version_names(self) -> list[str]:
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return ['current']
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        if department_name is None:
            return ['current']

        # Get export path with variant
        export_uri = get_export_uri(entity_uri, variant_name, department_name)
        export_path = api.storage.resolve(export_uri)
        version_paths = list(filter(
            _valid_version_path,
            list_version_paths(export_path)
        ))
        version_names = [vp.name for vp in version_paths]

        # Add 'current' option at the beginning (resolves to highest numbered version)
        return ['current'] + version_names

    def get_department_name(self) -> str | None:
        department_name_raw = self.parm('department').eval()
        # Handle 'from_context' special value
        if department_name_raw == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None:
                return None
            return context.department_name
        # From settings
        department_names = self.list_department_names()
        if len(department_names) == 0:
            return None
        if len(department_name_raw) == 0:
            return department_names[0]
        if department_name_raw not in department_names:
            return None
        return department_name_raw

    def get_version_name(self) -> str | None:
        version_names = self.list_version_names()
        if len(version_names) == 0:
            return None
        version_name = self.parm('version').eval()
        if len(version_name) == 0:
            return version_names[-1]
        if version_name == 'current':
            return version_names[-1]
        if version_name not in version_names:
            return None
        return version_name

    def set_variant_name(self, variant_name: str):
        """Set variant name."""
        self.parm('variant').set(variant_name)
        self._update_labels()

    def set_version_name(self, version_name: str):
        version_names = self.list_version_names()
        if version_name not in version_names:
            return
        self.parm('version').set(version_name)
        self._update_labels()

    def _update_labels(self):
        """Update label parameters to show current entity selection."""
        entity_raw = self.parm('entity').eval()
        if entity_raw == 'from_context':
            entity_uri = self.get_entity_uri()
            if entity_uri:
                self.parm('entity_label').set(f'from_context: {entity_uri}')
            else:
                self.parm('entity_label').set('from_context: none')
        else:
            # Specific entity URI selected
            self.parm('entity_label').set(entity_raw)

    def _initialize(self):
        """Refresh the labels. The 'entity' parm keeps its 'from_context'
        default even when the context can't be read: falling back to the
        first entity in the project silently imports the wrong one, where an
        unresolved 'from_context' visibly imports nothing."""
        self._update_labels()

    def execute(self):
        self._update_labels()
        return self._import_layer()

    def _import_layer(self):
        """Unified import method for both assets and shots."""
        native = self.native()
        entity_uri = self.get_entity_uri()
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        version_name = self.get_version_name()

        def _bypass(reason: str):
            self.parm('import_filepath1').set('')
            self.parm('import_filepath2').set('')
            self.parm('import_enable1').set(0)
            self.parm('import_enable2').set(0)
            self.parm('bypass_input').set(0)
            ns.set_node_comment(native, f"Bypassed: {reason}")
            native.bypass(True)

        if entity_uri is None:
            logger.debug("Import bypassed: No entity selected")
            _bypass("No entity selected")
            return
        if department_name is None:
            logger.debug(f"Import bypassed: No department selected for {entity_uri}")
            _bypass("No department selected")
            return
        if version_name is None:
            logger.debug(f"Import bypassed: No version selected for {entity_uri}/{department_name}")
            _bypass("No version selected")
            return

        # The guards above are the only bypass sources, and they are sticky:
        # nothing else ever cleared the flag, so a node bypassed once (e.g. by
        # switching Department to one this entity has no export for) stayed
        # dead after switching back to a valid selection, despite a resolved
        # version and filepath. Clear it here, symmetrically with _bypass().
        native.bypass(False)

        logger.info(f"Importing layer: uri={entity_uri}, dept={department_name}, variant={variant_name}, version={version_name}")

        from tumblepipe import resolver as _resolver

        # Resolve URIs in Python and feed the inner sublayer LOP a plain
        # filesystem path. The URI form does not survive Houdini's geometry-parm
        # + chs() pipeline cleanly. Nested entity:// URIs inside the loaded
        # layer continue to be resolved at USD load time.

        # Shared layer (index 1) - only if entity has multiple variants.
        shared_resolved = ''
        shared_exists = False
        if len(list_variants(entity_uri)) > 1:
            shared_path = latest_shared_export_path(entity_uri, department_name)
            if shared_path is not None:
                shared_version = shared_path.name
                shared_uri = f"{entity_uri}?dept={department_name}&variant=_shared&version={shared_version}"
                resolved = _resolver.try_resolve_entity_uri(shared_uri)
                if resolved and Path(resolved).exists():
                    shared_resolved = resolved
                    shared_exists = True
                    logger.debug(f"Found shared layer: {resolved}")

        self.parm('import_filepath1').set(shared_resolved)
        self.parm('import_enable1').set(1 if shared_exists else 0)

        # Variant layer (index 2)
        variant_uri = f"{entity_uri}?dept={department_name}&variant={variant_name}&version={version_name}"
        resolved_variant = _resolver.try_resolve_entity_uri(variant_uri)
        variant_exists = bool(resolved_variant) and Path(resolved_variant).exists()
        self.parm('import_filepath2').set(resolved_variant if variant_exists else '')
        self.parm('import_enable2').set(1 if variant_exists else 0)

        # The paths above are resolved in Python, but nested entity:// URIs
        # inside the loaded layers resolve at USD compose time — and an
        # already-composed stage never re-resolves them on its own. Ask the
        # resolver to notify stages so a re-import also floats the nested
        # references (batched to one notice inside deferred_refresh()).
        _resolver.refresh_context()

        if not variant_exists:
            logger.warning(f"Variant layer file not found: {variant_uri}")

        # Resolve version_path for context.json lookup below
        export_uri = get_export_uri(entity_uri, variant_name, department_name) / version_name
        version_path = api.storage.resolve(export_uri)

        # Enable bypass if either layer exists
        self.parm('bypass_input').set(1 if (shared_exists or variant_exists) else 0)

        if not shared_exists and not variant_exists:
            logger.warning(f"No layer files found for import: uri={entity_uri}, dept={department_name}, version={version_name}")

        # Update version label
        self.parm('version_label').set(version_name)

        # Generate metadata update script from context.json. Build the script
        # from scratch each run: if the new import has no context.json (or no
        # assets), a stale script would otherwise re-run against the wrong
        # entity's prim paths.
        script_parts = []
        context_path = version_path / 'context.json'
        context_data = None
        layer_info = None
        if context_path.exists():
            context_data = load_json(context_path)
            layer_info = ctx.find_output(
                context_data,
                uri=str(entity_uri),
                department=department_name
            )
            if layer_info is not None:
                assets = layer_info.get('parameters', {}).get('assets', [])
                if assets:
                    # Inline mode swaps the metadata update for a marker on
                    # each asset prim so the export bakes the layer's assets
                    # in instead of re-referencing them.
                    if self.get_import_mode() == 'inline':
                        script_parts.append(_inline_marker_script(assets))
                    else:
                        script_parts.append(_metadata_update_script(
                            entity_uri, department_name, version_name, assets
                        ))
        root_script = self._root_metadata_script(entity_uri)
        if root_script is not None:
            script_parts.append(root_script)
        self.parm('metadata_python').set('\n'.join(script_parts))

        # Set success comment with import metadata
        if layer_info is not None:
            timestamp = layer_info.get('timestamp', '')
            user = layer_info.get('user', '')
            if timestamp and user:
                ns.set_node_comment(native, f"Imported: {version_name}\n{timestamp}\nby {user}")
            else:
                ns.set_node_comment(native, f"Imported: {version_name}")
        else:
            ns.set_node_comment(native, f"Imported: {version_name}")

        logger.info(f"Import completed: uri={entity_uri}, dept={department_name}, version={version_name}")

    def _root_metadata_script(self, entity_uri: Uri) -> str | None:
        """Metadata script for the imported entity's own root prim.

        The context.json scripts above only refresh assets recorded INSIDE
        the imported layer, so importing another asset's department layer
        (e.g. Arena's model into the SET workfile) left the imported prim
        itself untagged — it silently dropped out of the export scrape.
        Tag foreign asset roots the same way import_asset does. Self-imports
        (pulling this workfile's own entity, e.g. model into lookdev) stay
        untagged: the exporting entity's root is never tracked by its own
        export. Shot layers are never root-tagged — tracking metadata is an
        asset concept.
        """
        if len(entity_uri.segments) == 0 or entity_uri.segments[0] != 'assets':
            return None
        shot_uri = None
        shot_department = None
        workfile_context = get_workfile_context(Path(hou.hipFile.path()))
        if workfile_context is not None:
            if str(workfile_context.entity_uri) == str(entity_uri):
                return None
            if str(workfile_context.entity_uri).startswith('entity:/shots/'):
                shot_uri = workfile_context.entity_uri
                shot_department = workfile_context.department_name
        if self.get_import_mode() == 'inline':
            return import_asset._inline_metadata_script(entity_uri)
        return import_asset._metadata_script(
            entity_uri,
            variant_name=self.get_variant_name(),
            shot_uri=shot_uri,
            shot_department=shot_department
        )

    def open_location(self):
        entity_uri = self.get_entity_uri()
        if entity_uri is None:
            return
        variant_name = self.get_variant_name()
        department_name = self.get_department_name()
        if department_name is None:
            return

        export_path = latest_export_path(entity_uri, variant_name, department_name)
        if export_path is None:
            return
        if not export_path.exists():
            return
        hou.ui.showInFileBrowser(path_str(export_path))

def create(scene, name):
    return ns.create_node(scene, name, ImportLayer, 'import_layer')

def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):
    # Set node style
    set_style(raw_node)

    # Validate node type
    raw_node_type = raw_node.type()
    if raw_node_type is None:
        return
    node_type = ns.find_node_type('import_layer', 'Lop')
    if node_type is None:
        return
    if raw_node_type != node_type:
        return

    node = ImportLayer(raw_node)
    node._initialize()

def execute():
    raw_node = hou.pwd()
    node = ImportLayer(raw_node)
    node.execute()

def open_location():
    raw_node = hou.pwd()
    node = ImportLayer(raw_node)
    node.open_location()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ImportLayer(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='both',
        include_from_context=True,
        current_selection=node.parm('entity').eval(),
        title="Select Entity",
        parent=hou.qt.mainWindow()
    )

    if dialog.exec_():
        selected_uri = dialog.get_selected_uri()
        if selected_uri:
            node.parm('entity').set(selected_uri)
            node.execute()


def output_modified_prims(raw_node) -> str:
    """Return the prim path this HDA wrote, for the output's modifiedprims."""
    entity = raw_node.parm('entity').eval()
    if not entity:
        return ''
    try:
        return uri_to_prim_path(Uri.parse_unsafe(entity))
    except ValueError:
        return ''
