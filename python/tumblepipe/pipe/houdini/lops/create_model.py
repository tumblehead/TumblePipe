import hou

from tumblepipe.util.uri import Uri
from tumblepipe.pipe.houdini.prim_queries import uri_to_prim_path
import tumblepipe.pipe.houdini.nodes as ns
from tumblepipe.pipe.houdini.entity_node import EntityNode

def _metadata_script(asset_uri: Uri) -> str:
    """Generate Python script for setting asset metadata on the scene prim."""
    prim_path = uri_to_prim_path(asset_uri)
    entity_name = asset_uri.segments[-1]

    script = f'''import hou

from tumblepipe.pipe.houdini import prim_metadata

node = hou.pwd()
stage = node.editableStage()
root = stage.GetPseudoRoot()

# Set metadata on scene prim
prim = root.GetPrimAtPath("{prim_path}")
if prim.IsValid():
    metadata = {{
        'uri': '{str(asset_uri)}',
        'instance': '{entity_name}',
        'inputs': []
    }}
    prim_metadata.set_metadata(prim, metadata)
'''
    return script

class CreateModel(EntityNode):

    # One asset's model. The parm is spelled 'asset' in saved scenes; an
    # asset Multi's workfile records the group, which has no model.
    ENTITY_PARM = 'asset'
    ENTITY_CONTEXTS = ('assets',)

    def get_metadata_content(self) -> str:
        """Get metadata script content."""
        asset_uri = self.get_entity_uri()
        if asset_uri is None:
            return ''
        return _metadata_script(asset_uri)

    def _update_labels(self):
        self.parm('asset_label').set(self._entity_label())

    def _initialize(self):
        """Refresh the labels. The 'asset' parm keeps its 'from_context'
        default: baking the workfile's URI in here would pin the node to
        whichever asset it was born in, so a copied scene (or a renamed
        entity) would keep publishing to the old asset."""
        self._update_labels()

    def execute(self):
        """Execute node - generate and set metadata script."""
        self._update_labels()
        script = self.get_metadata_content()
        self.parm('metadata_python').set(script)

def create(scene, name):
    return ns.create_node(scene, name, CreateModel, 'create_model')

def on_created(raw_node):
    ns.set_node_style(raw_node, ns.SHAPE_NODE_IMPORT)
    CreateModel(raw_node)._initialize()
    fill_materials(raw_node)

def execute(kwargs):
    """Execute node from HDA callback."""
    raw_node = kwargs['node']
    node = CreateModel(raw_node)
    node.execute()

def _get_materials(geo, outputnode):
    """The SOP material paths on ``geo``: per primitive, or one on the detail."""
    if geo.findPrimAttrib("shop_materialpath") is not None:
        return [
            x for x in sorted(set(geo.primStringAttribValues("shop_materialpath")))
            if x and outputnode.node(x)
        ]
    if geo.findGlobalAttrib("shop_materialpath") is not None:
        return [geo.stringAttribValue("shop_materialpath")]
    return []

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
                material = prop.Get(hou.frame())
                # If the materialpath is an array attribute of some kind,
                # just take the first value. All the values should be the
                # same, generally.
                if hasattr(material, '__iter__') and not isinstance(material, str):
                    material = material[0]
                if material not in matprims:
                    matprims[material] = []
                matprims[material].append(primpath)
            
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
            node.parm("matpath" + istr).set('`ifs(ch("enable_pathprefix"), chs("pathprefix"), "")`/mtl/%s' % matshortname)
            if material in matprims:
                node.parm("geopath" + istr).set(" ".join(matprims[material]))
