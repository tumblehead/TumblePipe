from pathlib import Path

import hou

from tumblehead.api import default_client
from tumblehead.pipe.paths import (
    get_workfile_context
)
from tumblehead.util.uri import Uri
from tumblehead.pipe.houdini.util import uri_to_metadata_prim_path
import tumblehead.pipe.houdini.nodes as ns

api = default_client()


def _metadata_script(asset_uri: Uri) -> str:
    """Generate Python script for creating asset metadata prim."""
    metadata_prim_path = uri_to_metadata_prim_path(asset_uri)
    entity_name = asset_uri.segments[-1]

    script = f'''import hou

from tumblehead.pipe.houdini import util

node = hou.pwd()
stage = node.editableStage()

# Create metadata prim
metadata_path = "{metadata_prim_path}"
prim = stage.DefinePrim(metadata_path, "Scope")

# Set metadata
metadata = {{
    'uri': '{str(asset_uri)}',
    'instance': '{entity_name}',
    'inputs': []
}}
util.set_metadata(prim, metadata)
'''
    return script


class CreateModel(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[str]:
        asset_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )
        uris = [entity.uri for entity in asset_entities]
        return ['from_context'] + [str(uri) for uri in uris]

    def get_asset_uri(self) -> Uri | None:
        asset_uri_raw = self.parm('asset').eval()
        if asset_uri_raw == 'from_context':
            file_path = Path(hou.hipFile.path())
            context = get_workfile_context(file_path)
            if context is None: return None
            # Verify it's an asset entity
            if context.entity_uri.segments[0] != 'assets': return None
            return context.entity_uri
        # From settings
        asset_uris = self.list_asset_uris()
        if len(asset_uris) <= 1: return None  # Only 'from_context' means no real URIs
        if len(asset_uri_raw) == 0: return Uri.parse_unsafe(asset_uris[1])  # Skip 'from_context'
        if asset_uri_raw not in asset_uris: return None  # Compare strings
        return Uri.parse_unsafe(asset_uri_raw)

    def set_asset_uri(self, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if str(asset_uri) not in asset_uris: return  # Compare strings
        self.parm('asset').set(str(asset_uri))

    def get_metadata_content(self) -> str:
        """Get metadata script content."""
        asset_uri = self.get_asset_uri()
        if asset_uri is None:
            return ''
        return _metadata_script(asset_uri)

    def _update_labels(self):
        """Update label parameters to show resolved values when 'from_context' is selected."""
        asset_raw = self.parm('asset').eval()
        if asset_raw == 'from_context':
            asset_uri = self.get_asset_uri()
            self.parm('asset_label').set(str(asset_uri) if asset_uri else '')
        else:
            self.parm('asset_label').set('')

    def execute(self):
        """Execute node - generate and set metadata script."""
        self._update_labels()
        script = self.get_metadata_content()
        self.parm('metadata_python').set(script)


def create(scene, name):
    node_type = ns.find_node_type('create_model', 'Lop')
    assert node_type is not None, 'Could not find create_model node type'
    native = scene.node(name)
    if native is not None: return CreateModel(native)
    return CreateModel(scene.createNode(node_type.name(), name))

def set_style(raw_node):
    raw_node.setColor(ns.COLOR_NODE_DEFAULT)
    raw_node.setUserData('nodeshape', ns.SHAPE_NODE_IMPORT)

def on_created(raw_node):

    # Set node style
    set_style(raw_node)

    # Context
    raw_node_type = raw_node.type()
    if raw_node_type is None: return
    node_type = ns.find_node_type('create_model', 'Lop')
    if node_type is None: return
    if raw_node_type != node_type: return
    node = CreateModel(raw_node)

    # Parse scene file path and check for valid asset context
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)

    # If no valid asset context, set first available asset
    if context is None or context.entity_uri.segments[0] != 'assets':
        asset_uris = node.list_asset_uris()
        if len(asset_uris) > 1:  # Skip 'from_context'
            node.set_asset_uri(Uri.parse_unsafe(asset_uris[1]))
        return

    # Set the default values from context
    node.set_asset_uri(context.entity_uri)

def execute():
    """Execute node from HDA callback."""
    raw_node = hou.pwd()
    node = CreateModel(raw_node)
    node.execute()

def _get_materials(geo, outputnode):
    materials = []
    try:
        materials = [x for x in sorted(set(geo.primStringAttribValues("shop_materialpath"))) if x and outputnode.node(x)]
    except:
        try:
            materials = [geo.stringAttribValue("shop_materialpath")]
        except:
            pass
    return materials

def fill_materials(node):
    sopnet = node.node("sopnet")
    outputnode = sopnet.node("create").displayNode()
    sopnodes = [x for x in sopnet.children() if (not x.type().isManager()) and x.isDisplayFlagSet()]
    materials = []
    if sopnodes:
        geo = sopnodes[0].geometry()
        materials = _get_materials(geo, outputnode)
        if not materials:
            unpack = hou.sopNodeTypeCategory().nodeVerb("unpack")
            ngeo = hou.Geometry()
            unpack.execute(ngeo, [geo])
            materials = _get_materials(ngeo, outputnode)
    
    if materials:
        import loputils
        node.parm("materials").set(len(materials))
        node.node("input")
        lopnode = node.node("sopimport")
        pathprefix = node.evalParm("pathprefix")
        if not pathprefix.endswith("/"):
            pathprefix += "/"
        shoppath = "/shop_materialpath_" + node.path().replace("/", "_")
        
        prims = []
        if node.evalParm("asreference"):
            parentprim = node.evalParm("primpath")
            if not parentprim.endswith("/"):
                parentprim += "/"
            prims = loputils.globPrims(lopnode, "%s**" % parentprim)
        else:
            if node.evalParm("copycontents"):
                layer = lopnode.activeLayer()
                prims = [x for x in loputils.globPrims(lopnode, "/**") if layer.GetPrimAtPath(x.GetPrimPath().pathString)]
            else:
                sopnodepath = sopnodes[0].path()
                for prim in loputils.globPrims(lopnode, "/**"):
                    layer = prim.GetPrimStack()[0].layer
                    splits = layer.identifier.split(":")
                    if splits[0] == "op" and splits[1].split(".")[0] == sopnodepath:
                        prims.append(prim)
        matprims = {}
            
        for prim in prims:
            # Material path set on a "field" should be promoted onto the
            # parent Volume primitive.
            if prim.IsA("UsdVolFieldBase"):
                primpath = prim.GetPrimPath().GetParentPath().pathString
            else:
                primpath = prim.GetPrimPath().pathString
            
            prefix = None
            frompath = True
            if node.evalParm("enable_pathprefix") or primpath.startswith("/Geometry"):
                prefix = pathprefix if node.evalParm("enable_pathprefix") else "/Geometry/"

                if primpath.startswith(prefix):
                    frompath = False
                    reppath = 'chs("pathprefix")+"/' + primpath[len(prefix):] + '"'
                    primpath = '`ifs(ch("enable_pathprefix"), %s, "%s")`' % (reppath, "/Geometry/" + primpath[len(prefix):])

            prop = prim.GetProperty("primvars:shop_materialpath")
            if prop:
                try:
                    material = prop.Get(hou.frame())
                    # If the materialpath is an array attribute of some kind,
                    # just take the first value. All the values should be the
                    # same, generally.
                    if hasattr(material, '__iter__') and not isinstance(material, str):
                        material = material[0]
                    if material not in matprims:
                        matprims[material] = []
                    matprims[material].append(primpath)
                except:
                    pass
            
            elif prim.GetTypeName() == "GeomSubset":
                material = prim.GetCustomDataByKey('partitionValue')
                if material:
                    if material not in matprims:
                        matprims[material] = []
                    if frompath:
                        primpath = primpath.replace(shoppath, '/shop_materialpath_`strreplace(opfullpath("."), "/", "_")`')
                    else:
                        primpath = primpath.replace(shoppath, '/shop_materialpath_"+strreplace(opfullpath("."), "/", "_")+"')
                    matprims[material].append(primpath)

        matshortnames = []
        for x, material in enumerate(materials):
            matshortname = material.split("/")[-1]
            while matshortname in matshortnames:
                matshortname = hou.text.incrementNumberedString(matshortname)
            matshortnames.append(matshortname)
            istr = str(x + 1)
            node.parm("matnode" + istr).set(node.relativePathTo(outputnode.node(material)))
            node.parm("matpath" + istr).set('`ifs(ch("enable_pathprefix"), chs("pathprefix"), "")`/materials/%s' % matshortname)
            if material in matprims:
                node.parm("geopath" + istr).set(" ".join(matprims[material]))
