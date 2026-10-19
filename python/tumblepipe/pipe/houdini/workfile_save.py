"""Save the loaded scene as the next workfile version.

The Houdini half of the workfile commit: ``pipe.paths`` allocates the
version, ``pipe.context`` writes its lineage, and this saves the hip in
between. Kept out of ``pipe.context`` so the core stays importable, and
testable, without a Houdini session.
"""

import logging
from pathlib import Path

import hou

from tumblepipe.pipe.context import (
    save_context,
    save_entity_context,
    workfile_context,
)
from tumblepipe.pipe.paths import (
    Context,
    release_reserved_version,
    reserve_next_hip_file_path,
)
from tumblepipe.util.uri import Uri

logger = logging.getLogger(__name__)


def save_hip_file(next_path: Path) -> Path:
    """``hou.hipFile.save`` into ``next_path`` and return where the hip landed.

    Houdini does not honour a requested extension the session's license is not
    allowed to write: an Education or Apprentice session asked to save
    ``foo.hip`` silently writes ``foo.hipnc`` instead. Every workfile commit
    must therefore record the extension from the file Houdini *reports having
    saved*, not from the path it was asked for — a sidecar built from the
    request points ``get_hip_file_path`` at a ``.hip`` that never existed, and
    the version can never be reopened from the browser (Rates, TumblePipe
    1.44.2 on Houdini Education).

    The predicted path (``session_nc_type``) is still worth getting right —
    it names the file the artist sees — but this is the safety net for the
    license categories the prediction does not know about. A rewrite is
    logged at warning level so the mismatch is visible, not silently absorbed.

    Raises RuntimeError when Houdini reports a file other than the one asked
    for (beyond its extension): the version would be recorded against a file
    that is not where the scene went.
    """
    next_path = Path(next_path)
    hou.hipFile.save(str(next_path))
    saved = Path(hou.hipFile.path())
    if saved.stem != next_path.stem:
        raise RuntimeError(
            f"asked Houdini to save {next_path}, but it reports the scene "
            f"as {saved}"
        )
    if saved.suffix != next_path.suffix:
        logger.warning(
            f"Houdini saved {next_path.name} as {saved.name} (license-driven "
            f"extension rewrite); recording the extension actually on disk"
        )
        return next_path.with_suffix(saved.suffix)
    return next_path


def commit_next_workfile(
    entity_uri: Uri,
    department_name: str,
    prev_context: Context = None,
    nc_type: str = None,
    note: str = "",
) -> Path:
    """Reserve, save the loaded hip into, and record the next workfile version.

    The single implementation every workfile-save path shares, so version
    allocation and the three-step commit are done one correct way:

    1. atomically reserve the next version (:func:`reserve_next_hip_file_path`),
       so two concurrent saves never pick the same number;
    2. ``hou.hipFile.save`` the loaded scene into it (:func:`save_hip_file`,
       so the recorded extension is the one Houdini actually wrote);
    3. write the ``_context`` lineage entry, then the ``context.json`` pointer
       **last** — so a crash always leaves the pointer at or below a
       fully-committed version, never ahead of a missing one.

    Rollback is deliberately narrow: if the *hip save* fails the reservation is
    released (the number is reusable). If the hip saved but the bookkeeping
    afterward fails, the file is **kept** — the artist's work is real — and the
    reader reconciliation plus ``verify_context_chain`` recover the pointer and
    chain rather than discarding a genuine save.

    ``prev_context`` records provenance; pass the loaded scene's context on an
    incremental save, or None (a fresh create / farm publish) to let
    :func:`save_context` derive the predecessor from disk.

    ``note`` is the artist's free-text "what changed" for this version, shown
    in the asset browser's Note column. Collect it *before* calling this — the
    version is reserved on the first line, so prompting from in here would hold
    a reservation open for as long as a dialog sits on screen, and a cancel
    would have to unwind a commit already in flight. Returns the saved path.
    """
    next_path = reserve_next_hip_file_path(entity_uri, department_name, nc_type=nc_type)
    try:
        next_path = save_hip_file(next_path)
    except BaseException:
        release_reserved_version(next_path)
        raise

    next_context = workfile_context(entity_uri, department_name, next_path)
    save_context(
        next_path.parent, prev_context, next_context,
        houdini_version=hou.applicationVersionString(),
        file_extension=next_path.suffix.lstrip("."),
        note=note,
    )
    save_entity_context(next_path.parent, next_context)
    return next_path
