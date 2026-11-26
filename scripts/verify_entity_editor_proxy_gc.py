"""Headless verify: the Entity Editor's sheet proxies no longer crash the
cyclic garbage collector after their pane is closed.

Field crashes (Houdini 22.0.466, PySide 6.8.3; TumbleTrove crash signatures
7ff6ee10 and 516a3301, reports cr_297c4770a6d6b7d3, cr_07e88cd82b0f360c and
cr_f16885a53283e792 on TumblePipe 1.108 / 1.109 / 1.111) died in Shiboken
(``SbkObject_tp_clear`` -> ``Shiboken::Object::clearReferences``) while Python
was "Garbage-collecting". The collection itself started anywhere (TumbleRig's
tab-bar timer, a hip-load script); what it freed was this:

* ``_SheetPane`` / ``MultiPane`` made ``self.model = _SheetModel(self)``
  (the pane's child) and ``self.proxy = _Filter()`` (parentless, so owned
  by Python) and called ``proxy.setSourceModel(model)``; PySide then keeps a
  reference to the model on the proxy.
* Closing the editor deletes the pane, and the pane deletes the model. PySide
  frees the model's wrapper but leaves the pointer in the proxy's kept list.
  The proxy outlives the pane because the pane's Python wrapper is in a
  reference cycle; ``self.proxy`` is in the wrapper's ``__dict__``.
* The next full collection clears that cycle. The proxy's last reference
  goes, ``clearReferences`` decrements the freed model, and Houdini segfaults.

Each scenario runs in its own process, using the real ``_Filter`` /
``_MultiFilter`` from ``entity_editor`` / ``entity_editor_multis``:

* ``released`` (TumblePipe <= 1.111): parentless proxy made after the model.
  This one must crash, which shows the hazard is still in this PySide.
* ``fixed`` (this branch): the proxy is the pane's child and is made first.
  This one must survive.

The script also checks the source: both panes build their proxy as
``_Filter(self)`` / ``_MultiFilter(self)`` before the model.

Run under Houdini's interpreter:

    & "C:\\Program Files\\Side Effects Software\\Houdini 22.0.466\\bin\\hython.exe" \\
        scripts/verify_entity_editor_proxy_gc.py

Exit 0 = the fixed construction survives the collector; the source matches.
"""
import inspect
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: (module, pane, its filter proxy), modules under tumblepipe.asset_browser.
PANES = (
    ("entity_editor", "_SheetPane", "_Filter"),
    ("entity_editor_multis", "MultiPane", "_MultiFilter"),
)


def _import_editor(module: str):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "python"))
    try:
        import tumbletrove  # noqa: F401
    except ImportError:
        # Not on PYTHONPATH: the newest installed TumbleTrove package.
        found = sorted((Path.home() / ".hpm" / "packages").glob("tumbletrove@*/python"),
                       key=lambda p: tuple(int(x) for x in re.findall(r"\d+", p.parent.name)))
        if found:
            sys.path.append(str(found[-1]))
    import importlib
    return importlib.import_module(f"tumblepipe.asset_browser.{module}")


def _child(module: str, filter_name: str, fixed: bool) -> None:
    import gc

    import shiboken6
    from PySide6 import QtGui, QtWidgets

    ee = _import_editor(module)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    flt = getattr(ee, filter_name)

    window = QtWidgets.QDialog()
    pane = QtWidgets.QWidget(window)
    if fixed:
        pane.proxy = flt(pane)
        pane.model = QtGui.QStandardItemModel(0, 2, pane)
    else:
        pane.model = QtGui.QStandardItemModel(0, 2, pane)
        pane.proxy = flt()
    pane.proxy.setSourceModel(pane.model)
    pane.table = QtWidgets.QTableView(pane)
    pane.table.setModel(pane.proxy)
    pane.window_ = pane          # the pane's wrapper sits in a cycle
    window.show()
    app.processEvents()

    gc.disable()
    proxy = pane.proxy
    del pane
    # The editor closes (WA_DeleteOnClose): Qt deletes the pane and its children.
    shiboken6.delete(window)
    app.processEvents()
    # Read the proxy's kept references: a freed model crashes here every
    # time, where the collector crashes only once the memory was reused.
    for _ in range(200):
        shiboken6.dump(proxy)
    del proxy
    gc.collect()                 # frees the pane's cycle and the proxy with it
    print("survived", flush=True)


def _source_ok() -> list[str]:
    bad = []
    for module, pane_name, filter_name in PANES:
        src = inspect.getsource(getattr(_import_editor(module), pane_name))
        proxy = re.search(rf"self\.proxy = {filter_name}\(self\)", src)
        model = re.search(r"self\.model = _\w+Model\(self\)", src)
        if proxy is None:
            bad.append(f"{pane_name}: proxy is not {filter_name}(self)")
        elif model is None or model.start() < proxy.start():
            bad.append(f"{pane_name}: the model is made before the proxy")
    return bad


def main() -> int:
    if len(sys.argv) == 5 and sys.argv[1] == "--child":
        _child(sys.argv[2], sys.argv[3], sys.argv[4] == "1")
        return 0
    failures = 0
    for problem in _source_ok():
        print(f"FAIL source: {problem}")
        failures += 1
    for module, _, filter_name in PANES:
        results = {}
        for fixed in (False, True):
            proc = subprocess.run(
                [sys.executable, __file__, "--child", module, filter_name, "1" if fixed else "0"],
                check=False, capture_output=True, text=True, timeout=180,
            )
            results[fixed] = proc.returncode == 0 and "survived" in proc.stdout
        hazard = "dangles" if not results[False] else "no longer dangles (PySide fixed?)"
        ok = results[True]
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {filter_name:14s} released order: {hazard}; "
              f"fixed order: {'survives' if ok else 'CRASHES'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
