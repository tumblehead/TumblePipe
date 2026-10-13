import sys
import tumblepipe.vram  # noqa: F401

# This checkout's sources (the bridge execs this file, so there is no __file__).
SRC = r"C:\Users\tumblehead\Documents\workspace\TumblePipe\python\tumblepipe\vram"
for name in ("gpumem", "sizes", "report", "images", "process", "usd_scan",
             "cop_scan", "collect", "ui"):
    full = f"tumblepipe.vram.{name}"
    __import__(full)
    mod = sys.modules[full]
    path = f"{SRC}\\{name}.py"
    code = compile(open(path, encoding="utf-8").read(), path, "exec")
    exec(code, mod.__dict__)

from tumblepipe.vram import ui
if ui._window is not None:
    try:
        ui._window.close()
        ui._window.deleteLater()
    except RuntimeError:
        pass
    ui._window = None
print("reloaded")
