"""Nodes that hold one asset import per multiparm row.

th::import_assets and th::import_rigs each list rows of (entity, channel,
version, instances) under a count parm, and read them the same way: the
assets a row may pick, the row's asset, its channels and versions, its
labels, and every row at once. :class:`AssetRows` is that model; a node
says which count parm holds its rows, which of the project's assets it
offers, where a row's versions are published and what 'latest' loads.

A row's asset is always explicit: an empty row is unset (never the first
asset), and a row naming an asset the project lacks, or one another row
already imports, raises EntityNodeError.
"""

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tumblepipe.api import api
from tumblepipe.config.channels import list_channels
from tumblepipe.pipe.houdini.entity_node import EntityNodeError
from tumblepipe.pipe.paths import list_version_paths
from tumblepipe.util.uri import Uri
import tumblepipe.pipe.houdini.nodes as ns


@dataclass(frozen=True)
class _Rows:
    """The project's assets the node offers, and what every row names."""
    offered: list[Uri]
    offered_set: frozenset[Uri]
    row_uris: dict[int, Uri]
    imported: Counter


class AssetRows(ns.Node):
    #: The multiparm count parm holding the rows.
    COUNT_PARM: str
    #: What a row's Version menu offers before the published versions.
    VERSION_TOKENS: tuple[str, ...]

    _rows_snapshot: _Rows | None = None

    def _error(self, message: str) -> EntityNodeError:
        return EntityNodeError(f'{self.path()}: {message}')

    # What the node imports from

    def _offers(self, asset_uri: Uri) -> bool:
        """Whether a row may import ``asset_uri``, one of the project's
        listed assets."""
        raise NotImplementedError

    def _version_folder(self, asset_uri: Uri, channel_name: str) -> Path:
        """The folder holding the versions a row of ``asset_uri`` in
        ``channel_name`` picks from."""
        raise NotImplementedError

    def _latest_version(self, asset_uri: Uri, channel_name: str) -> str | None:
        """The version 'latest' loads; None while there is none."""
        raise NotImplementedError

    # Reading the rows

    @contextmanager
    def _rows_read_once(self):
        """Read the project's assets and the rows' assets once for the
        length of the block, instead of once per row read.

        An Import reads every row's asset about five times (asset,
        channel, version, labels), and each read lists the project's
        assets and parses every other row: O(rows^2) per read. A 300-row
        import_assets refresh took 28 s (the nightly's workfile_imports
        benchmark). Nothing in the block may change a row's entity parm.
        A nested block reads through the outer one's snapshot.
        """
        if self._rows_snapshot is not None:
            yield
            return
        self._rows_snapshot = self._read_rows()
        try:
            yield
        finally:
            self._rows_snapshot = None

    def _read_rows(self) -> _Rows:
        if self._rows_snapshot is not None:
            return self._rows_snapshot
        with api.config.coherent():
            offered = [
                uri for uri in api.config.list_entity_uris(
                    filter=Uri.parse('entity:/assets'), closure=True,
                )
                if self._offers(uri)
            ]
        offered_set = frozenset(offered)
        row_uris = {}
        for index in range(1, self.parm(self.COUNT_PARM).eval() + 1):
            raw = self.parm(f'entity{index}').eval()
            if len(raw) == 0: continue
            row_uris[index] = Uri.parse(raw)
        imported = Counter(uri for uri in row_uris.values() if uri in offered_set)
        return _Rows(offered, offered_set, row_uris, imported)

    # A row's asset

    def list_entity_uris(self, index: int) -> list[Uri]:
        """The assets a row may pick: all the node offers but those another
        row already imports."""
        rows = self._read_rows()
        own = rows.row_uris.get(index)
        taken = {
            uri for uri, count in rows.imported.items()
            if count - (1 if uri == own else 0) > 0
        }
        return [uri for uri in rows.offered if uri not in taken]

    def get_entity_uri(self, index: int) -> Uri | None:
        """The row's asset; None for a row with no asset picked yet.
        Read-only: the channel/version menus call this."""
        rows = self._read_rows()
        asset_uri = rows.row_uris.get(index)
        if asset_uri is None: return None
        if asset_uri not in rows.offered_set:
            raise self._error(
                f'row {index}: {asset_uri} is not an asset in the project. '
                "Pick one with the row's Browse... button."
            )
        if rows.imported[asset_uri] > 1:
            raise self._error(
                f'row {index}: {asset_uri} is already imported by another '
                'row. Remove one of the rows, or raise its Instances.'
            )
        return asset_uri

    def require_entity_uri(self, index: int) -> Uri:
        """The row's asset, for the import, which cannot skip a row."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            raise self._error(
                f"row {index} has no asset. Pick one with the row's Browse... "
                'button, or remove the row.'
            )
        return asset_uri

    def set_entity_uri(self, index: int, asset_uri: Uri):
        if asset_uri not in self.list_entity_uris(index):
            raise self._error(
                f'row {index}: {asset_uri} is not an asset in the project, '
                'or another row already imports it'
            )
        self.parm(f'entity{index}').set(str(asset_uri))

    # Instances

    def get_instances(self, index: int) -> int:
        return self.parm(f'instances{index}').eval()

    def set_instances(self, index: int, instances: int):
        self.parm(f'instances{index}').set(instances)

    # Channel

    def list_channel_names(self, index: int) -> list[str]:
        """The row's asset's channels, 'default' first; none while unset."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return []
        return list_channels(asset_uri)

    def get_channel_name(self, index: int) -> str:
        """The row's Channel, which must be a channel of the row's asset."""
        asset_uri = self.require_entity_uri(index)
        channel_names = list_channels(asset_uri)
        channel_name = self.parm(f'channel{index}').eval()
        if channel_name not in channel_names:
            raise self._error(
                f"row {index}: channel '{channel_name}' is not a channel of "
                f"{asset_uri} (listed: {', '.join(channel_names)})"
            )
        return channel_name

    def set_channel_name(self, index: int, channel_name: str):
        self.parm(f'channel{index}').set(channel_name)

    # Version

    def list_version_names(self, index: int) -> list[str]:
        """The row's Version menu: the tokens, then the published versions
        of the row's asset in its channel, oldest first."""
        asset_uri = self.get_entity_uri(index)
        if asset_uri is None:
            return list(self.VERSION_TOKENS)
        folder = self._version_folder(asset_uri, self.get_channel_name(index))
        return [*self.VERSION_TOKENS, *(path.name for path in list_version_paths(folder))]

    def get_version_name(self, index: int) -> str:
        """The row's version: one of the tokens or a version name."""
        version_name = self.parm(f'version{index}').eval()
        if len(version_name) == 0:
            raise self._error(
                f"row {index}: version is empty. Pick a version, or 'latest'."
            )
        return version_name

    def set_version_name(self, index: int, version_name: str):
        self.parm(f'version{index}').set(version_name)

    # Every row

    def get_row_imports(self) -> list[tuple[Uri, str, str, int]]:
        """(asset_uri, channel, version, instances) for every row. Every row
        must name its asset; see require_entity_uri."""
        return [
            (
                self.require_entity_uri(index),
                self.get_channel_name(index),
                self.get_version_name(index),
                self.get_instances(index),
            )
            for index in range(1, self.parm(self.COUNT_PARM).eval() + 1)
        ]

    def _update_labels(self, index: int):
        """The row's labels: its asset, and the version it loads ('latest'
        named as the version it is now)."""
        self.parm(f'entity_label{index}').set(
            self.parm(f'entity{index}').eval() or 'none'
        )
        version_name = self.get_version_name(index)
        if version_name == 'latest':
            asset_uri = self.get_entity_uri(index)
            if asset_uri is not None:
                version_name = (
                    self._latest_version(asset_uri, self.get_channel_name(index))
                    or version_name
                )
        self.parm(f'version_label{index}').set(version_name)
