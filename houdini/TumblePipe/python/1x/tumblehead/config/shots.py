from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

def list_render_layers(shot_uri: Uri) -> list[str]:
    """Return render layer names for a shot entity.

    Args:
        shot_uri: The shot entity URI (e.g., entity:/shots/010/010)

    Returns:
        List of render layer names configured for this shot
    """
    properties = api.config.get_properties(shot_uri)
    if properties is None:
        return []
    return properties.get('render_layers', [])
