from pathlib import Path

import hou

from tumblehead.api import default_client
from tumblehead.pipe.paths import (
    get_workfile_context,
    AssetContext
)
import tumblehead.pipe.houdini.nodes as ns

api = default_client()

class CreateModel(ns.Node):
    def __init__(self, native):
        super().__init__(native)
    
    def list_category_names(self):
        return api.config.list_category_names()

    def list_asset_names(self):
        category_name = self.get_category_name()
        if category_name is None: return []
        return api.config.list_asset_names(category_name)

    def get_category_name(self):
        category_names = self.list_category_names()
        if len(category_names) == 0: return None
        category_name = self.parm('category').eval()
        if len(category_name) == 0: return category_names[0]
        if category_name not in category_names: return None
        return category_name

    def get_asset_name(self):
        asset_names = self.list_asset_names()
        if len(asset_names) == 0: return None
        asset_name = self.parm('asset').eval()
        if len(asset_name) == 0: return asset_names[0]
        if asset_name not in asset_names: return None
        return asset_name

    def set_category_name(self, category_name):
        category_names = self.list_category_names()
        if category_name not in category_names: return
        self.parm('category').set(category_name)

    def set_asset_name(self, asset_name):
        asset_names = self.list_asset_names()
        if asset_name not in asset_names: return
        self.parm('asset').set(asset_name)

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

    # Parse scene file path
    file_path = Path(hou.hipFile.path())
    context = get_workfile_context(file_path)
    if context is None: return
    
    # Set the default values
    match context:
        case AssetContext(
            department_name,
            category_name,
            asset_name,
            version_name
            ):
            node.set_category_name(category_name)
            node.set_asset_name(asset_name)

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
        inputnode = node.node("input")
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
