import hou

def load():

    # Set the desktop
    tumblehead_desktop = hou.ui.desktop('Tumblehead')
    tumblehead_desktop.setAsCurrent()

load()