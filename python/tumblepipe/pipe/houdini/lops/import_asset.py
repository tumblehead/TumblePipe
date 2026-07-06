import os
import re
from pathlib import Path

import hou

from tumblepipe.api import api
from tumblepipe.util.uri import Uri
from tumblepipe.util.io import load_json
from tumblepipe.config.variants import list_variants
from tumblepipe.util import result
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode
from tumblepipe.pipe.houdini.util import uri_to_prim_path
from tumblepipe.pipe.paths import (
    get_workfile_context,
    current_staged_path,
    list_version_paths
)


def _staged_context_assets(staged_file_path: Path) -> list[dict]:
    """Tracked sub-assets recorded next to a staged file.

    The staged build's context.json lists every asset tracked in the
    department layers it composed ({asset, instances, variant, inputs}).
    Old staged builds without a sidecar yield an empty list.
    """
    context_data = load_json(staged_file_path.parent / 'context.json')
    if context_data is None:
        return []
    return context_data.get('parameters', {}).get('assets', [])

def _subasset_script_lines(
    subassets: list[dict],
    shot_uri: Uri | None = None,
    shot_department: str | None = None,
    inline: bool = False
) -> list[str]:
    """Script lines that re-establish each tracked sub-asset on import.

    Exported layers carry no pipeline metadata (the layerbreak strips
    everything the set workfile authored, including the Duplicate LOP's
    defs and reference arcs), so the importing side must both tag every
    sub-asset root and re-define multi-instance duplicates — otherwise
    the duplicates compose as typeless overs (invisible in the session,
    while the render-stage flatten regenerates them) and the untagged
    roots trip the export drop-guard. Instance prims mirror the
    Duplicate LOP's output and the render stage's instance definitions:
    copies named {base}0..{base}N-1 referencing the base prim, base
    deactivated. No transform is authored — placement comes from the
    set layer's overs on the instance prims.

    Numbered duplicates AT or beyond the tracked count are deactivated:
    the import node's persistent layer (which holds these re-established
    defs) is localized into department exports as a sidecar, so a layer
    exported while an inflated count was live carries the phantom defs
    forever — composition would resurrect them on every import no matter
    what the corrected count says (the paleindia six-towers relapse).
    For a count of 1 the base prim is the instance, so every numbered
    sibling is stale and the base is re-activated.
    """
    lines = []
    for asset_info in subassets:
        asset_uri_raw = asset_info.get('asset')
        if not asset_uri_raw:
            continue
        try:
            sub_uri = Uri.parse_unsafe(asset_uri_raw)
            base_path = uri_to_prim_path(sub_uri)
        except ValueError:
            continue
        base_name = sub_uri.segments[-1]
        instances = asset_info.get('instances', 1)
        variant = asset_info.get('variant', 'default')
        inputs = list(asset_info.get('inputs', []))
        if shot_uri is not None and shot_department is not None:
            shot_entry = {
                'uri': str(shot_uri),
                'department': shot_department,
                'version': 'initial'
            }
            if shot_entry not in inputs:
                inputs.append(shot_entry)

        # Deactivate stale numbered duplicates at/beyond the tracked
        # count (see docstring). Runs before the wanted instances are
        # (re)defined so a later legitimate def downstream still wins.
        stale_threshold = instances if instances > 1 else 0
        stale_cleanup = [
            '    for sibling in list(base.GetParent().GetChildren()):',
            '        sibling_name = sibling.GetName()',
            f'        if not sibling_name.startswith("{base_name}"):',
            '            continue',
            f'        suffix = sibling_name[{len(base_name)}:]',
            '        if not suffix.isdigit():',
            '            continue',
            f'        if int(suffix) >= {stale_threshold}:',
            '            sibling.SetActive(False)',
        ]

        lines += [
            f'# Sub-asset: {asset_uri_raw}',
            f'base = root.GetPrimAtPath("{base_path}")',
            'if base.IsValid():',
        ]
        lines += stale_cleanup
        if instances <= 1:
            lines.append('    base.SetActive(True)')
            if inline:
                lines.append('    util.mark_inlined(base)')
            else:
                lines += [
                    '    util.set_metadata(base, {',
                    f"        'uri': '{asset_uri_raw}',",
                    f"        'instance': '{base_name}',",
                    f"        'variant': '{variant}',",
                    f"        'inputs': {inputs!r}",
                    '    })',
                ]
        else:
            parent_path = base_path.rsplit('/', 1)[0]
            lines.append('    base.SetActive(False)')
            for index in range(instances):
                instance_prim_path = f'{parent_path}/{base_name}{index}'
                instance_name = api.naming.get_instance_name(
                    base_name, index
                )
                lines += [
                    f'    inst = stage.DefinePrim("{instance_prim_path}")',
                    '    inst.SetActive(True)',
                    f'    inst.GetReferences().AddInternalReference("{base_path}")',
                ]
                if inline:
                    lines.append('    util.mark_inlined(inst)')
                else:
                    lines += [
                        '    util.set_metadata(inst, {',
                        f"        'uri': '{asset_uri_raw}',",
                        f"        'instance': '{instance_name}',",
                        f"        'variant': '{variant}',",
                        f"        'inputs': {inputs!r}",
                        '    })',
                    ]
        lines.append('')
    return lines

def _metadata_script(
    asset_uri: Uri,
    variant_name: str = 'default',
    shot_uri: Uri | None = None,
    shot_department: str | None = None,
    subassets: list[dict] | None = None
) -> str:
    """
    Generate Python script for setting asset metadata on the scene prim.

    If shot_uri and shot_department are provided, adds a shot department
    entry to the inputs array to track which department introduced this asset.
    The variant is recorded so the export scrape can carry it into the
    layer's context.json — staged builds re-reference each tracked asset's
    staged file by that variant.
    """
    prim_path = uri_to_prim_path(asset_uri)
    entity_name = asset_uri.segments[-1]

    # Build initial inputs list
    inputs_str = '[]'
    if shot_uri is not None and shot_department is not None:
        # Add shot department entry to track source
        inputs_str = f"[{{'uri': '{str(shot_uri)}', 'department': '{shot_department}', 'version': 'initial'}}]"

    script = f'''import hou

from tumblepipe.pipe.houdini import util

node = hou.pwd()
stage = node.editableStage()
root = stage.GetPseudoRoot()

# Set metadata on scene prim
prim = root.GetPrimAtPath("{prim_path}")
if prim.IsValid():
    metadata = {{
        'uri': '{str(asset_uri)}',
        'instance': '{entity_name}',
        'variant': '{variant_name}',
        'inputs': {inputs_str}
    }}
    util.set_metadata(prim, metadata)
'''
    subasset_lines = _subasset_script_lines(
        subassets or [], shot_uri, shot_department, inline=False
    )
    if subasset_lines:
        script += '\n' + '\n'.join(subasset_lines)
    return script

def _inline_metadata_script(
    asset_uri: Uri,
    subassets: list[dict] | None = None
) -> str:
    """
    Generate the metadata script for 'inline' import mode.

    Replaces the pipeline metadata with an 'inlined' marker: the asset is
    deliberately baked into the export (no layerbreak), so it must not be
    scraped and re-referenced, and the publish guards must not read the
    missing metadata as an accidental drop. Sub-asset roots (and their
    re-defined instance prims) get the same marker — they bake into the
    export together with the asset that tracks them. Targets only this
    node's prims — assets flowing through from upstream nodes are
    untouched.
    """
    prim_path = uri_to_prim_path(asset_uri)

    script = f'''import hou

from tumblepipe.pipe.houdini import util

node = hou.pwd()
stage = node.editableStage()
root = stage.GetPseudoRoot()

prim = root.GetPrimAtPath("{prim_path}")
if prim.IsValid():
    util.mark_inlined(prim)
'''
    subasset_lines = _subasset_script_lines(subassets or [], inline=True)
    if subasset_lines:
        script += '\n' + '\n'.join(subasset_lines)
    return script

def _expand_staged_layers(
    staged_path: Path,
    excluded: set[str],
    latest: bool,
    _visited: set | None = None
) -> list[str]:
    """Flatten a staged file into resolved leaf layer paths, dropping
    excluded departments.

    A staged file may sublayer OTHER assets' staged files — the tracked
    assets of a set-style asset. Those refs carry no dept= parameter, so a
    flat filter would load them whole and the excluded department would
    ride back in through every subasset. Recurse into them instead, so an
    exclusion applies to the entire nesting. Returns strongest-first order
    (matching the staged file's sublayer order); revisited staged files
    (cyclic or diamond nesting) are expanded once.
    """
    from tumblepipe import resolver

    if _visited is None:
        _visited = set()
    key = os.path.normcase(os.path.normpath(str(staged_path)))
    if key in _visited:
        return []
    _visited.add(key)

    layers = []
    for department, ref in _list_staged_layers(staged_path):
        if department in excluded:
            continue
        if ref.startswith('entity:'):
            uri = Uri.parse_unsafe(ref)
            # Strip version when in 'latest' mode so the resolver
            # picks the actual latest department export.
            if latest and 'version' in uri.query:
                stripped = dict(uri.query)
                del stripped['version']
                uri = Uri(uri.purpose, uri.segments, stripped)
            layer_resolved = resolver.try_resolve_entity_uri(str(uri))
        else:
            layer_resolved = os.path.normpath(str(staged_path.parent / ref))
        if not layer_resolved or not Path(layer_resolved).exists():
            continue
        if department is None and '_staged' in Path(layer_resolved).parts:
            layers.extend(_expand_staged_layers(
                Path(layer_resolved), excluded, latest, _visited
            ))
        else:
            layers.append(layer_resolved)
    return layers

def _list_staged_layers(staged_file_path: Path) -> list[tuple[str | None, str]]:
    """Parse the staged .usda layer stack into (department, ref) pairs.

    The staged asset file sublayers one layer per exported department,
    strongest first, followed by the staged files of any tracked nested
    assets. The department is read from the entity URI's dept= query
    param, falling back to the directory layout
    (../{variant}/{department}/{version}/file.usd) for filesystem refs
    in old staged files. Refs with no recognizable department yield None
    (nested-asset staged refs always do — they carry no dept= param).
    """
    layers = []
    content = staged_file_path.read_text()
    for match in re.finditer(r'@([^@]+)@', content):
        ref = match.group(1)
        department = None
        if ref.startswith('entity:'):
            uri = Uri.parse_unsafe(ref)
            department = uri.query.get('dept')
        else:
            parts = [part for part in ref.split('/') if part not in ('..', '.')]
            if len(parts) >= 3:
                department = parts[-3]
        layers.append((department, ref))
    return layers

class ImportAsset(EntityNode):
    def __init__(self, native):
        super().__init__(native)

    def list_version_names(self) -> list[str]:
        """List available staged versions including 'latest' and 'current'."""
        asset_uri = self.get_entity_uri()
        if asset_uri is None:
            return ['latest', 'current']

        # Get staged directory for the selected variant
        # (_staged/<variant>/v####, mirroring import_shot)
        staged_uri = (
            Uri.parse_unsafe('export:/') /
            asset_uri.segments /
            '_staged' /
            self.get_variant_name()
        )
        staged_path = api.storage.resolve(staged_uri)

        # Get versioned directories (v0001, v0002, etc.)
        version_paths = list_version_paths(staged_path)
        version_names = [vp.name for vp in version_paths]

        # Add special options at the beginning
        return ['latest', 'current'] + version_names

    def list_variant_names(self) -> list[str]:
        """List available variant names for current entity."""
        asset_uri = self.get_entity_uri()
        if asset_uri is None:
            return ['default']
        variants = list_variants(asset_uri)
        if not variants:
            return ['default']
        # Ensure 'default' is always first
        if 'default' in variants:
            variants.remove('default')
            variants.insert(0, 'default')
        return variants

    def get_entity_uri(self) -> Uri | None:
        asset_uris = self.list_asset_uris()
        if len(asset_uris) == 0: return None
        asset_uri_raw = self.parm('entity').eval()
        if len(asset_uri_raw) == 0: return asset_uris[0]
        asset_uri = Uri.parse_unsafe(asset_uri_raw)
        if asset_uri not in asset_uris: return None
        return asset_uri

    def set_entity_uri(self, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm('entity').set(str(asset_uri))

    def _update_labels(self):
        """Update label parameters to show current entity selection."""
        entity_uri = self.get_entity_uri()
        if entity_uri:
            self.parm('entity_label').set(str(entity_uri))
        else:
            self.parm('entity_label').set('none')

        # Resolve version name (resolve 'latest' to actual version)
        version_name = self.get_version_name()
        if version_name == 'latest':
            version_names = self.list_version_names()
            actual_versions = [v for v in version_names if v not in ('latest', 'current')]
            if actual_versions:
                version_name = actual_versions[-1]
        self.parm('version_label').set(version_name)

    def execute(self):
        self._update_labels()
        native = self.native()
        asset_uri = self.get_entity_uri()
        if asset_uri is None:
            self.parm('import_filepath1').set('')
            ns.set_node_comment(native, "Bypassed: No asset selected")
            native.bypass(True)
            return result.Value(None)

        # Get variant and staged file path based on version selection
        variant_name = self.get_variant_name()
        version_name = self.get_version_name()

        from tumblepipe import resolver

        # Build the entity URI we want to resolve. 'latest' leaves the version
        # unpinned and turns on latest_mode so any nested entity:// URIs inside
        # the loaded layer also cascade. 'current' and specific versions bake
        # the version, freezing both this top-level resolve and nested ones.
        if version_name == 'latest':
            resolver.set_latest_mode(True)
            staged_uri = f"{asset_uri}?variant={variant_name}"
        else:
            resolver.set_latest_mode(False)
            if version_name == 'current':
                current_path = current_staged_path(asset_uri, variant_name)
                if current_path is None:
                    self.parm('import_filepath1').set('')
                    ns.set_node_comment(native, "Bypassed: No staged file found")
                    native.bypass(True)
                    return result.Value(None)
                pinned_version = current_path.name
            else:
                pinned_version = version_name
            staged_uri = f"{asset_uri}?variant={variant_name}&version={pinned_version}"

        # Invalidate cached nested resolutions so the new mode takes effect
        resolver.refresh_context()

        # Resolve via the entity resolver and feed the inner sublayer LOP a
        # plain filesystem path. The URI form does not survive Houdini's
        # geometry-parm + chs() pipeline cleanly, so we let the resolver do
        # its work here and stash the resolved path. Nested entity:// URIs
        # inside the loaded layer continue to go through the resolver at
        # USD load time.
        resolved = resolver.try_resolve_entity_uri(staged_uri)
        if not resolved or not Path(resolved).exists():
            self.parm('import_filepath1').set('')
            ns.set_node_comment(native, "Bypassed: No staged file found")
            native.bypass(True)
            return result.Value(None)

        # Apply department exclusion by loading the staged file's
        # per-department sublayers individually — the staged layer itself
        # composes every department, so it can only be loaded whole.
        # Without exclusions, load the staged file directly to keep its
        # header metadata (frame range, metersPerUnit, upAxis).
        excluded = set(self.get_exclude_department_names())
        layer_paths = [resolved]
        if excluded:
            # Flatten the staged file (recursing into tracked-asset staged
            # refs so the exclusion reaches nested assets too). The result
            # is strongest-first; the Sublayer LOP composes its last file
            # strongest, so load in reverse.
            flattened = _expand_staged_layers(
                Path(resolved), excluded, version_name == 'latest'
            )
            layer_paths = list(reversed(flattened))

        import_node = native.node('import')
        if not layer_paths:
            self.parm('import_filepath1').set('')
            import_node.parm('num_files').set(0)
            ns.set_node_comment(native, "Bypassed: All departments excluded")
            native.bypass(True)
            return result.Value(None)

        # filepath1 channels to the HDA-level parm; extra slots are set
        # directly on the (editable) internal sublayer node.
        self.parm('import_filepath1').set(layer_paths[0])
        import_node.parm('num_files').set(len(layer_paths))
        for index, layer_path in enumerate(layer_paths[1:], start=2):
            import_node.parm(f'filepath{index}').set(layer_path)
        native.bypass(False)

        # Update version label with resolved folder name
        resolved_version = Path(resolved).parent.name
        self.parm('version_label').set(resolved_version)

        # Get shot context if we're in a shot workfile
        shot_uri = None
        shot_department = None
        file_path = Path(hou.hipFile.path())
        workfile_context = get_workfile_context(file_path)
        if workfile_context is not None:
            if str(workfile_context.entity_uri).startswith('entity:/shots/'):
                shot_uri = workfile_context.entity_uri
                shot_department = workfile_context.department_name

        # Generate and set metadata script. Inline mode swaps the pipeline
        # metadata for a marker so the export bakes the asset in instead of
        # re-referencing it. The staged context.json's tracked sub-assets
        # are covered too: their roots re-tagged and their multi-instance
        # duplicates re-defined (see _subasset_script_lines).
        subassets = _staged_context_assets(Path(resolved))
        if self.get_import_mode() == 'inline':
            script = _inline_metadata_script(asset_uri, subassets)
        else:
            script = _metadata_script(
                asset_uri, variant_name, shot_uri, shot_department, subassets
            )
        self.parm('metadata_python').set(script)

        # Set success comment with import metadata
        ns.set_node_comment(native, f"Imported: {resolved_version}")

        return result.Value(None)

def create(scene, name):
    return ns.create_node(scene, name, ImportAsset, 'import_asset', force_valid_node_name=True)

def set_style(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):
    set_style(raw_node)
    node = ImportAsset(raw_node)
    # Set entity to first available
    asset_uris = node.list_asset_uris()
    if asset_uris:
        node.set_entity_uri(asset_uris[0])
    node._update_labels()

def execute():
    raw_node = hou.pwd()
    node = ImportAsset(raw_node)
    node.execute()

def select():
    """HDA button callback to open entity selector dialog."""
    from tumblepipe.pipe.houdini.ui.widgets import EntitySelectorDialog

    raw_node = hou.pwd()
    node = ImportAsset(raw_node)

    dialog = EntitySelectorDialog(
        api=api,
        entity_filter='assets',
        include_from_context=False,
        current_selection=node.parm('entity').eval(),
        title="Select Asset",
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