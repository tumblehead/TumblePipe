def deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts. Override values take precedence.

    For nested dicts, merge recursively. For other types, override replaces base.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
