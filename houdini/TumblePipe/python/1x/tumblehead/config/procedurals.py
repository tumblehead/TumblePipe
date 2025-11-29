from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

def list_procedural_names(shot_uri: Uri, asset_uri: Uri) -> list[str]:
    """Return procedural node names for an asset in a shot.

    URI Structure: procedurals:/shots/{shot_segments}/{asset_segments_without_assets}

    Example:
        shot_uri: entity:/shots/010/010
        asset_uri: entity:/assets/CHAR/BigGuy
        procedural_uri: procedurals:/shots/010/010/CHAR/BigGuy

    Args:
        shot_uri: The shot entity URI
        asset_uri: The asset entity URI

    Returns:
        List of procedural node type names
    """
    shot_segments = shot_uri.segments  # ['shots', '010', '010']
    asset_segments = asset_uri.segments[1:]  # ['CHAR', 'BigGuy'] - skip 'assets'
    procedural_path = '/'.join(shot_segments + asset_segments)
    procedural_uri = Uri.parse_unsafe(f'procedurals:/{procedural_path}')

    properties = api.config.get_properties(procedural_uri)
    if properties is None:
        return []
    return properties.get('procedurals', [])
