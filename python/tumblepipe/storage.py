from pathlib import Path
import os
import tempfile

from tumblepipe.util.uri import Uri


def default_temp_path() -> Path:
    """Machine-local scratch root for a project's ``temp:/``.

    Everything that publishes stages here first and copies the finished
    result to its final home: an export writes the whole version payload
    (localized sidecars included) into a temp dir and then copies it into
    ``export/``, and a farm render writes its tiles here before the stitch.
    So this must not resolve onto the shared project drive - that turns one
    write into two over the network, and leaves a permanently empty
    ``<project>_temp`` beside the project, since the callers create the root
    eagerly and only the inner ``TemporaryDirectory`` ever cleans itself up.

    Nothing here outlives the operation that wrote it, so a machine-local
    path loses nothing. ``TH_TEMP`` overrides the root for a machine whose
    OS temp sits on a small system drive - a farm worker with a scratch
    disk of its own is the case that wants it.
    """
    from tumblepipe.api import get_project_name

    root = os.environ.get('TH_TEMP') or tempfile.gettempdir()
    return Path(root) / 'th_temp' / get_project_name()


class StorageConvention:

    def _normalize_input(self, uri: Uri) -> tuple[str, list[str]] | None:
        """Convert Uri to (purpose, segments) tuple."""
        if not isinstance(uri, Uri):
            raise TypeError(f"Expected Uri, got {type(uri).__name__}")
        if uri.is_wild():
            return None
        return uri.purpose, uri.segments

    def resolve(self, uri: Uri):
        raise NotImplementedError()
