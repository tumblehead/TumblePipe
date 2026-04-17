import hou

DESKTOP_NAME = "TumblePipe"


def _set_default_desktop():
    desktops = {d.name(): d for d in hou.ui.desktops()}
    desktop = desktops.get(DESKTOP_NAME)
    if desktop is None:
        return
    if hou.ui.curDesktop().name() == DESKTOP_NAME:
        return
    desktop.setAsCurrent()


_set_default_desktop()
