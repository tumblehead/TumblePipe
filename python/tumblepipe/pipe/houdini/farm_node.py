"""The farm settings of a th:: node that submits jobs, read one way.

A node with farm parms (pool, priority, batch size) mixes in ``FarmNode``
and names its parms through the class attributes — the names differ per
HDA ('pool' / 'export_pool' / 'farm_pool') and are frozen into saved scenes.

The Pool menu leads with an empty token labelled with the project's default
pool, so a node left on it visibly follows ``farm.default_pool``. A pool the
project does not list, or the default when the project sets none, raises:
a submission never lands in an arbitrary pool.
"""

from tumblepipe.config.farm import get_default_pool, list_pools

# The Pool menu token that follows the project's default pool.
PROJECT_DEFAULT_POOL = ''


class FarmNode:
    """Mixin for a th:: node that submits farm jobs. Needs ``parm()`` and
    ``_error()`` from the node class it is mixed into."""

    POOL_PARM: str = 'pool'
    PRIORITY_PARM: str = 'priority'
    BATCH_SIZE_PARM: str = 'batchsize'

    def list_pool_names(self) -> list[str]:
        return [pool.name for pool in list_pools()]

    def pool_menu(self) -> list[str]:
        """The Pool menu as Houdini's flat ``[token, label, ...]`` list."""
        default_pool = get_default_pool()
        menu = [
            PROJECT_DEFAULT_POOL,
            f'Project default ({default_pool or "none set"})',
        ]
        for pool_name in self.list_pool_names():
            menu += [pool_name, pool_name]
        return menu

    def get_pool_name(self) -> str:
        pool_name = self.parm(self.POOL_PARM).eval()
        if pool_name == PROJECT_DEFAULT_POOL:
            pool_name = get_default_pool()
            if pool_name is None:
                raise self._error(
                    'the Pool follows the project default, but the project '
                    'sets no default pool. Pick a pool on the node, or set '
                    'farm.default_pool in the project config.'
                )
        pool_names = self.list_pool_names()
        if pool_name not in pool_names:
            raise self._error(
                f"pool '{pool_name}' is not one of the project's pools "
                f"({', '.join(pool_names) or 'none'})"
            )
        return pool_name

    def set_pool_name(self, pool_name: str):
        if pool_name not in self.list_pool_names():
            raise self._error(f"pool '{pool_name}' is not a project pool")
        self.parm(self.POOL_PARM).set(pool_name)

    def get_priority(self) -> int:
        return self.parm(self.PRIORITY_PARM).eval()

    def set_priority(self, priority: int):
        self.parm(self.PRIORITY_PARM).set(priority)

    def get_batch_size(self) -> int:
        return self.parm(self.BATCH_SIZE_PARM).eval()
