from pathlib import Path

import hou

from tumblehead.api import default_client
from tumblehead.pipe.paths import (
    get_workfile_context
)
from tumblehead.util.uri import Uri
import tumblehead.pipe.houdini.nodes as ns

api = default_client()

DEFAULTS_URI = Uri.parse_unsafe('defaults:/houdini/lops/create_model')

class CreateModel(ns.Node):
    def __init__(self, native):
        super().__init__(native)

    def list_asset_uris(self) -> list[Uri]:
        asset_entities = api.config.list_entities(
            filter = Uri.parse_unsafe('entity:/assets'),
            closure = True
        )
        return [entity.uri for entity in asset_entities]

    def get_entity_source(self):
        parm = self.parm('entity_source')
        if parm is None: return 'from_settings'
        return parm.eval()

    def set_entity_source(self, entity_source):
        valid_sources = ['from_context', 'from_settings']
        if entity_source not in valid_sources: return
        self.parm('entity_source').set(entity_source)

    def get_asset_uri(self) -> Uri | None:
        entity_source = self.get_entity_source()
        match entity_source:
            case 'from_context':
                file_path = Path(hou.hipFile.path())
                context = get_workfile_context(file_path)
                if context is None: return None
                # Verify it's an asset entity
                if context.entity_uri.segments[0] != 'assets': return None
                return context.entity_uri
            case 'from_settings':
                asset_uris = self.list_asset_uris()
                if len(asset_uris) == 0: return None
                asset_uri_raw = self.parm('asset').eval()
                if len(asset_uri_raw) == 0: return asset_uris[0]
                asset_uri = Uri.parse_unsafe(asset_uri_raw)
                if asset_uri not in asset_uris: return None
                return asset_uri
            case _:
                raise AssertionError(f'Unknown entity source: {entity_source}')

    def set_asset_uri(self, asset_uri: Uri):
        asset_uris = self.list_asset_uris()
        if asset_uri not in asset_uris: return
        self.parm('asset').set(str(asset_uri))

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

    # If no valid asset context, switch to from_settings
    if context is None or context.entity_uri.segments[0] != 'assets':
        node.parm('entity_source').set('from_settings')
        return

    # Set the default values from context
    node.set_asset_uri(context.entity_uri)

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
