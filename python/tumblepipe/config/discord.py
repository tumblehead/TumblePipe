from tumblepipe.api import api
from tumblepipe.util.uri import Uri

DISCORD_URI = Uri.parse_unsafe('config:/discord')

def get_token() -> str | None:
    """Get the Discord bot token, or None when the project has not set one.

    A project created from the template carries an empty ``token`` string, so
    a blank value means "not configured" just as much as a missing key does.
    """
    properties = api.config.get_properties(DISCORD_URI)
    if properties is None: return None
    token = properties.get('token')
    if not isinstance(token, str): return None
    token = token.strip()
    if len(token) == 0: return None
    return token

def _children(kind: str) -> dict:
    discord_data = api.config.root('config') or {}
    discord_children = discord_data.get('children', {}).get('discord', {}).get('children', {})
    return discord_children.get(kind, {}).get('children', {})

def _lookup(kind: str, name: str) -> dict | None:
    """Properties of the ``kind`` entry named ``name``, matched ignoring case.

    The keys are typed by hand in the config editor and stored verbatim, so a
    channel may be stored as ``Renders`` while callers ask for ``renders``
    (or the other way round). Lowercasing the query alone made every
    mixed-case key listed by ``list_channels`` unreachable. An exact match
    wins; otherwise the stored spelling that matches case-insensitively.
    A name that cannot be a config key at all (a user name with a space)
    is simply not found.
    """
    stored = _children(kind)
    key = name if name in stored else next(
        (existing for existing in stored if existing.casefold() == name.casefold()),
        None
    )
    if key is None: return None
    try:
        uri = DISCORD_URI / kind / key
    except ValueError:
        return None
    return api.config.get_properties(uri)

def get_user_discord_id(username: str) -> int | None:
    """Get the Discord user ID for a given username."""
    properties = _lookup('users', username)
    if properties is None: return None
    return properties.get('discord_id')

def get_channel_id(channel_name: str) -> int | None:
    """Get the Discord channel ID for a given channel name."""
    properties = _lookup('channels', channel_name)
    if properties is None: return None
    return properties.get('channel_id')

def get_channel_for_department(department: str) -> str | None:
    """Get the channel name for a given department."""
    properties = _lookup('departments', department)
    if properties is None: return None
    return properties.get('channel')

def list_users() -> list[str]:
    """List all registered Discord usernames."""
    return list(_children('users').keys())

def list_channels() -> list[str]:
    """List all registered Discord channel names."""
    return list(_children('channels').keys())

def is_configured() -> bool:
    """Whether this project has a usable Discord setup at all.

    The project template ships an empty discord block -- no token, no users,
    no channels -- so a project nobody has wired up answers False here and
    callers can skip posting instead of failing.
    """
    if get_token() is None: return False
    if len(list_channels()) == 0: return False
    return True
