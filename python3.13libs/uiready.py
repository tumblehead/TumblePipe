# TumblePipe's UI-ready hook: see tumblepipe.startup.select_desktop.
#
# Like pythonrc.py, this runs in Houdini's shared __main__, so it binds no
# names and makes one call.

__import__("tumblepipe.startup").startup.select_desktop()
