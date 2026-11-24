"""The Clean Up Project window's overview of a listing, as plain data.

The window shows a listing (a :class:`core.Plan`) as a tree: a row per
category (what it found, what goes, what that frees), opened to why
(the reasons, grouped, with counts and sizes), what goes biggest first, and
what the category could not read. Everything here is derived from the plan
alone and changes nothing in it: the window draws :func:`rows` and
:func:`warnings` as they come, so what it shows is what these functions
say, and they are tested without Qt.

A category's notes are of two kinds. "Read 12 latest workfile(s) ..." says
what was read; every other note says something is *not* protected, or could
not be asked (``.hiplc`` workfiles, Deadline), and is a warning: shown in
the warning colour above the tree as well as in its category. A note of a
kind this module does not know is a warning too, so a new one is never
buried.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from tumblepipe.cleanup.core import CategoryPlan, Plan, human

#: A note that only says what was read starts so.
INFO_PREFIX = 'Read '

#: How a row is drawn.
TONE_NORMAL = 'normal'
TONE_HEADING = 'heading'     # a category's own row
TONE_DIM = 'dim'             # what stays, and what was read
TONE_WARNING = 'warning'
TONE_ERROR = 'error'

#: Groups listed under "Biggest first" before the rest are summed up.
DETAIL = 200
#: Errors listed under a category before the rest are counted.
ERROR_DETAIL = 50

COLUMNS = ('', 'Found', 'To delete', 'Frees')


def is_warning(note: str) -> bool:
    """Whether a category's note warns, rather than says what was read."""
    return not note.startswith(INFO_PREFIX)


def plural(count: int, noun: str) -> str:
    return f'{count:,} {noun}{"" if count == 1 else "s"}'


@dataclass(frozen=True)
class Reason:
    """The entries of a category that go (or stay) for one reason."""
    prune: bool
    reason: str
    count: int
    size: int


@dataclass(frozen=True)
class Group:
    """What goes of one group (an entity, a department folder)."""
    name: str
    count: int
    size: int


@dataclass(frozen=True)
class CategoryOverview:
    key: str
    title: str
    noun: str
    found: int
    to_delete: int
    #: bytes the deletions free
    size: int
    #: bytes of what stays
    kept_size: int
    #: deleting reasons first, then keeping ones; each biggest first
    reasons: tuple[Reason, ...]
    #: what goes, by group, biggest first
    groups: tuple[Group, ...]
    #: what was read
    notes: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def kept(self) -> int:
        return self.found - self.to_delete


@dataclass(frozen=True)
class Overview:
    categories: tuple[CategoryOverview, ...]

    @property
    def found(self) -> int:
        return sum(category.found for category in self.categories)

    @property
    def to_delete(self) -> int:
        return sum(category.to_delete for category in self.categories)

    @property
    def size(self) -> int:
        return sum(category.size for category in self.categories)


def category_overview(category: CategoryPlan) -> CategoryOverview:
    by_reason: dict[tuple[bool, str], list[int]] = defaultdict(lambda: [0, 0])
    by_group: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for entry in category.entries:
        tally = by_reason[(entry.prune, entry.reason)]
        tally[0] += 1
        tally[1] += entry.size
        if entry.prune:
            tally = by_group[entry.group]
            tally[0] += 1
            tally[1] += entry.size
    reasons = sorted(
        (Reason(prune, reason, count, size) for (prune, reason), (count, size) in by_reason.items()),
        key=lambda r: (not r.prune, -r.size, -r.count, r.reason),
    )
    groups = sorted(
        (Group(name, count, size) for name, (count, size) in by_group.items()),
        key=lambda g: (-g.size, -g.count, g.name),
    )
    prunable = category.prunable
    size = sum(entry.size for entry in prunable)
    return CategoryOverview(
        key=category.key, title=category.title, noun=category.noun,
        found=len(category.entries), to_delete=len(prunable), size=size,
        kept_size=sum(entry.size for entry in category.entries) - size,
        reasons=tuple(reasons), groups=tuple(groups),
        notes=tuple(note for note in category.notes if not is_warning(note)),
        warnings=tuple(note for note in category.notes if is_warning(note)),
        errors=tuple(category.errors),
    )


def overview(plan: Plan) -> Overview:
    return Overview(tuple(category_overview(category) for category in plan.categories))


# ── what the window shows ─────────────────────────────────


def headline(view: Overview) -> str:
    return f'{human(view.size)} to free' if view.to_delete else 'Nothing to delete'


def subline(view: Overview) -> str:
    count = len(view.categories)
    looked = f'{view.found:,} looked at in {count} {"category" if count == 1 else "categories"}'
    if not view.to_delete:
        return f'{looked}: everything stays.'
    return f'{plural(view.to_delete, "item")} to delete, of {looked}. Nothing is deleted until you confirm.'


def delete_label(view: Overview | None) -> str:
    """The Delete button's text."""
    if view is None or not view.to_delete:
        return 'Delete…'
    return f'Delete {view.to_delete} item(s), {human(view.size)}…'


def warnings(view: Overview) -> tuple[tuple[str, str], ...]:
    """Every warning, ``(category title, text)``, in the order listed:
    shown above the tree, where none can be missed."""
    return tuple((category.title, text) for category in view.categories for text in category.warnings)


def confirm_lines(view: Overview) -> tuple[str, ...]:
    """What the confirmation lists: one line per category that deletes."""
    return tuple(
        f'{category.title}: {category.to_delete} {category.noun}(s), {human(category.size)}'
        for category in view.categories if category.to_delete
    )


#: The categories a workfile makes again: an export, render or cache by
#: exporting, rendering or caching again, a farm job folder by the next
#: submit. What the cleanup removes is a risk to no one, and the words say
#: so plainly: no "dangerous", no "can't be undone".
REMAKEABLE = ('exports', 'renders', 'caches', 'jobs')
#: How the confirmation names a category no workfile makes again.
NOT_REMAKEABLE_NOUN = {'backups': 'Houdini backups', 'deleted': 'deleted entities'}


def confirm_note(categories) -> str:
    """The confirmation's sentence for the categories about to be purged:
    how many items go, and which of them a workfile can make again."""
    deleting = [c for c in categories if c.to_delete]
    count = sum(c.to_delete for c in deleting)
    head = f'{count:,} old item{"" if count == 1 else "s"} will be removed from disk.'
    others = [c for c in deleting if c.key not in REMAKEABLE]
    if not deleting or len(others) == len(deleting):
        return head
    if not others:
        return f'{head} Each can be made again from its workfile.'
    names = ' and '.join(NOT_REMAKEABLE_NOUN.get(c.key, c.title) for c in others)
    return f'{head} All but the {names} can be made again from their workfiles.'


@dataclass(frozen=True)
class Row:
    """One row of the tree: its cells (:data:`COLUMNS`), how it is drawn,
    a tooltip, its children and whether it starts open."""
    cells: tuple[str, str, str, str]
    tone: str = TONE_NORMAL
    tip: str = ''
    children: tuple[Row, ...] = ()
    expanded: bool = False


def _reason_row(category: CategoryOverview, reason: Reason) -> Row:
    if reason.prune:
        return Row((reason.reason, f'{reason.count:,}', f'{reason.count:,}', human(reason.size)),
                   tip=f'{plural(reason.count, category.noun)} go: {reason.reason}')
    return Row((reason.reason, f'{reason.count:,}', 'kept', ''), TONE_DIM,
               tip=f'{plural(reason.count, category.noun)} stay ({human(reason.size)}): {reason.reason}')


def biggest(groups: tuple[Group, ...], detail: int = DETAIL) -> tuple[tuple[Group, ...], Group | None]:
    """The first ``detail`` groups, and the rest summed up (None when none
    are left over); the rest's name says how many groups it holds."""
    shown, rest = groups[:detail], groups[detail:]
    if not rest:
        return shown, None
    return shown, Group(f'({len(rest):,} more)', sum(g.count for g in rest), sum(g.size for g in rest))


def category_row(category: CategoryOverview, *, detail: int = DETAIL) -> Row:
    children: list[Row] = [Row((text, '', '', ''), TONE_WARNING, tip=text) for text in category.warnings]
    if category.reasons:
        children.append(Row(
            ('Why', '', '', ''), TONE_DIM,
            tip='What goes and what stays, by reason',
            children=tuple(_reason_row(category, reason) for reason in category.reasons),
            expanded=True,
        ))
    if category.groups:
        shown, rest = biggest(category.groups, detail)
        group_rows = [Row((g.name, '', f'{g.count:,}', human(g.size)), tip=g.name) for g in shown]
        if rest is not None:
            group_rows.append(Row((rest.name, '', f'{rest.count:,}', human(rest.size)), TONE_DIM))
        children.append(Row(
            ('To delete, biggest first', '', '', ''), TONE_DIM,
            tip='What goes, by entity or folder, the biggest first',
            children=tuple(group_rows),
        ))
    children += [Row((text, '', '', ''), TONE_DIM, tip=text) for text in category.notes]
    if category.errors:
        shown_errors = [Row((error, '', '', ''), TONE_ERROR, tip=error) for error in category.errors[:ERROR_DETAIL]]
        left = len(category.errors) - ERROR_DETAIL
        if left > 0:
            shown_errors.append(Row((f'({left:,} more)', '', '', ''), TONE_ERROR))
        children.append(Row(
            (f'Could not be read: {len(category.errors):,}', '', '', ''), TONE_ERROR,
            tip='What could not be read is kept', children=tuple(shown_errors),
        ))
    found = plural(category.found, category.noun)
    return Row(
        (category.title, found, f'{category.to_delete:,}', human(category.size) if category.to_delete else '—'),
        TONE_WARNING if category.warnings else TONE_HEADING,
        tip=f'{found}: {category.to_delete:,} to delete ({human(category.size)}), '
            f'{category.kept:,} kept ({human(category.kept_size)})',
        children=tuple(children),
        # A category whose listing warns opens, so the warning is in view.
        expanded=bool(category.warnings),
    )


def rows(view: Overview, *, detail: int = DETAIL) -> tuple[Row, ...]:
    """The tree: one row per category, in the order listed."""
    return tuple(category_row(category, detail=detail) for category in view.categories)


def deleted_rows(plan: Plan, results: dict[str, tuple[int, int, list[str]]]) -> tuple[Row, ...]:
    """What a delete did, one row per category it touched; its errors under it."""
    out = []
    for category in plan.categories:
        removed, removed_bytes, errors = results.get(category.key, (0, 0, []))
        if not removed and not errors:
            continue
        out.append(Row(
            (category.title, '', f'{removed:,} deleted', human(removed_bytes)),
            TONE_ERROR if errors else TONE_HEADING,
            children=tuple(Row((error, '', '', ''), TONE_ERROR, tip=error) for error in errors),
            expanded=bool(errors),
        ))
    return tuple(out)
