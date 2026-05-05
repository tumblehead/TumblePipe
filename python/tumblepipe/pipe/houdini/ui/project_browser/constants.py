AUTO_SETTINGS_DEFAULT = dict(
    Asset=dict(Save=False, Refresh=True, RebuildNodes=False),
    Shot=dict(Save=False, Refresh=True, RebuildNodes=False),
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
    RebuildNodes = "RebuildNodes"


class FrameRangeMode:
    Padded = "Padded"
    Full = "Full"