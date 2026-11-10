"""th::lpe_tags: name light groups for light-path-expression AOVs."""

from tumblepipe.pipe.houdini import prim_queries


def generate(kwargs):
    """Fill the tag rows from how the lights in the stage are named.

    Only ever a starting point: an LPE tag is a lighting decision, and
    plenty of setups group lights whose names say nothing about the
    grouping. Rows written here are meant to be edited.
    """
    import hou
    node = kwargs['node']
    # The input's stage: this node's own output carries the tags its rows
    # authored, which would feed the current rows back in.
    source = node.inputs()[0] if node.inputs() and node.inputs()[0] is not None else node
    stage = source.stage()
    proposals = prim_queries.propose_lpe_tags(stage.GetPseudoRoot()) if stage else []
    if not proposals:
        # Leave the rows as they are: clearing them threw away the tags
        # someone set up.
        message = f"No lights found in {source.path()}'s stage; the tags are unchanged."
        if hou.isUIAvailable():
            hou.ui.displayMessage(message, title='Generate LPEs from scene')
        else:
            print(message)
        return
    node.parm("tags").set(len(proposals))
    for i, (tag_name, prim_paths) in enumerate(proposals):
        node.parm(f"lpetag{i}").set(tag_name)
        node.parm(f"primpattern{i}").set(" ".join(prim_paths))
