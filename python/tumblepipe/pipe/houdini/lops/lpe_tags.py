"""th::lpe_tags: name light groups for light-path-expression AOVs."""


def generate_report(proposals, source_path: str) -> str:
    """What Generate LPEs from scene did, for the node's Status (#42).

    ``proposals`` is ``prim_queries.propose_lpe_tags``'s (tag, [prim paths])
    list; empty means the stage had no lights and the rows were left alone.
    """
    if not proposals:
        return f"No lights found in {source_path}'s stage; the tags are unchanged."
    tag_count = len(proposals)
    light_count = sum(len(prim_paths) for _, prim_paths in proposals)
    tags = 'tag' if tag_count == 1 else 'tags'
    lights = 'light' if light_count == 1 else 'lights'
    return f"Generated {tag_count} {tags} from {light_count} {lights}."


def generate(kwargs):
    """Fill the tag rows from how the lights in the stage are named.

    Only ever a starting point: an LPE tag is a lighting decision, and
    plenty of setups group lights whose names say nothing about the
    grouping. Rows written here are meant to be edited. Status says what it
    did (it used to stay empty).
    """
    from tumblepipe.pipe.houdini import prim_queries
    node = kwargs['node']
    # The input's stage: this node's own output carries the tags its rows
    # authored, which would feed the current rows back in.
    source = node.inputs()[0] if node.inputs() and node.inputs()[0] is not None else node
    stage = source.stage()
    proposals = prim_queries.propose_lpe_tags(stage.GetPseudoRoot()) if stage else []
    # Leave the rows as they are when nothing was found: clearing them
    # threw away the tags someone set up.
    if proposals:
        node.parm("tags").set(len(proposals))
        for i, (tag_name, prim_paths) in enumerate(proposals):
            node.parm(f"lpetag{i}").set(tag_name)
            node.parm(f"primpattern{i}").set(" ".join(prim_paths))
    node.parm("generate_report").set(generate_report(proposals, source.path()))
