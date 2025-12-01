from tumblehead.api import default_client
from tumblehead.util.uri import Uri

api = default_client()

DISCORD_URI = Uri.parse_unsafe('config:/discord')

def get_token() -> str | None:
    """Get the Discord bot token."""
    properties = api.config.get_properties(DISCORD_URI)
    if properties is None: return None
    return properties.get('token')

def get_user_discord_id(username: str) -> int | None:
    """Get the Discord user ID for a given username."""
    user_uri = DISCORD_URI / 'users' / username.lower()
    properties = api.config.get_properties(user_uri)
    if properties is None: return None
    return properties.get('discord_id')

def get_channel_id(channel_name: str) -> int | None:
    """Get the Discord channel ID for a given channel name."""
    channel_uri = DISCORD_URI / 'channels' / channel_name.lower()
    properties = api.config.get_properties(channel_uri)
    if properties is None: return None
    return properties.get('channel_id')

def get_channel_for_department(department: str) -> str | None:
    """Get the channel name for a given department."""
    department_uri = DISCORD_URI / 'departments' / department.lower()
    properties = api.config.get_properties(department_uri)
    if properties is None: return None
    return properties.get('channel')

def list_users() -> list[str]:
    """List all registered Discord usernames."""
    discord_data = api.config.cache.get('config', {})
    discord_children = discord_data.get('children', {}).get('discord', {}).get('children', {})
    users_children = discord_children.get('users', {}).get('children', {})
    return list(users_children.keys())

def list_channels() -> list[str]:
    """List all registered Discord channel names."""
    discord_data = api.config.cache.get('config', {})
    discord_children = discord_data.get('children', {}).get('discord', {}).get('children', {})
    channels_children = discord_children.get('channels', {}).get('children', {})
    return list(channels_children.keys())
