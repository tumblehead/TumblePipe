# TumblePipe's Houdini startup hook: see tumblepipe.startup.bootstrap.
#
# Houdini runs every package's pythonX.Ylibs/pythonrc.py in one shared
# __main__, so this file binds no names and makes one call; the logic lives in
# the module. tumblepipe is importable through PYTHONPATH, which
# hpm.toml [runtime] points at the package's python/.

__import__("tumblepipe.startup").startup.bootstrap()
