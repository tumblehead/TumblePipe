"""Exception types shared across layers.

Kept in ``util`` so the deepest code can raise them without importing the
Qt-backed ui package - a farm worker running an export headlessly must be
able to raise the same signals the interactive executor catches.
"""


class TaskSkipped(Exception):
    """Raised by a task callback that has nothing to do, not something wrong.

    The process executor marks the task SKIPPED with this message as its
    reason and carries on with the remaining tasks - unlike a real failure,
    which aborts the rest of the group. Use it for conditions that make a
    single node a no-op (e.g. an export node whose stage input is
    disconnected), never for a condition that would publish a wrong or
    empty result.
    """
