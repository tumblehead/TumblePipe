"""th::lpe_tags: name light groups for light-path-expression AOVs."""

from tumblepipe.pipe.houdini import prim_queries


def generate(kwargs):
    """Fill the tag rows from how the lights in the stage are named.

    Only ever a starting point: an LPE tag is a lighting decision, and
    plenty of setups group lights whose names say nothing about the
    grouping. Rows written here are meant to be edited.
    """
    node = kwargs['node']
    root = node.stage().GetPseudoRoot()
    proposals = prim_queries.propose_lpe_tags(root)
    node.parm("tags").set(len(proposals))
    for i, (tag_name, prim_paths) in enumerate(proposals):
        node.parm(f"lpetag{i}").set(tag_name)
        node.parm(f"primpattern{i}").set(" ".join(prim_paths))
