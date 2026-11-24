"""Old export versions: ``tumblepipe.prune_exports``, as a cleanup category."""

from __future__ import annotations

from pathlib import Path

from tumblepipe import prune_exports
from tumblepipe.cleanup import hip
from tumblepipe.cleanup.core import Category, CategoryPlan, Entry, Settings


def plan(roots, inventory, settings: Settings, *, workfiles: hip.Workfiles) -> CategoryPlan:
    export_plan = prune_exports.make_plan(
        roots, settings.keep_exports, settings.context, workfiles=workfiles, inventory=inventory,
    )
    out = CategoryPlan('exports', 'Old export versions', 'version', payload=export_plan)
    for verdict in export_plan.verdicts:
        version = verdict.version
        out.entries.append(Entry(
            export_plan.export_path.joinpath(*version.rel.split('/')),
            version.rel, '/'.join(version.series.split('/')[:3]),
            version.size, verdict.prune, verdict.reason,
        ))
    out.notes.append(f'Read {export_plan.hips_read} latest workfile(s) for pinned versions.')
    if export_plan.hips_unreadable:
        out.notes.append(
            f'{len(export_plan.hips_unreadable)} latest workfile(s) are .hiplc/.hipnc in a form that '
            'cannot be read: versions only they load are not kept for them.'
        )
    out.errors = list(export_plan.errors)
    return out


def apply(category_plan: CategoryPlan, log_path: Path) -> tuple[int, int, list[str]]:
    return prune_exports.apply(category_plan.payload, log_path)


CATEGORY = Category(
    'exports', 'Old export versions',
    'export versions older than the newest few, that no staged build or latest workfile loads',
    ('export',), plan, apply,
)
