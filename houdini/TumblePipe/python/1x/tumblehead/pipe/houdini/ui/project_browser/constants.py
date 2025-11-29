AUTO_SETTINGS_DEFAULT = dict(
    Asset=dict(Save=False, Refresh=True),
    Shot=dict(Save=False, Refresh=True),
)


class Location:
    Workspace = "Workspace"
    Export = "Export"
    Texture = "Texture"


class Section:
    Asset = "Asset"
    Shot = "Shot"


class Action:
    Save = "Save"
    Refresh = "Refresh"


class FrameRangeMode:
    Padded = "Padded"
    Full = "Full"