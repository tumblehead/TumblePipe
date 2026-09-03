"""Per-entity settings resolution for the Submit Jobs dialog.

Pure functions — no Qt, no ``hou``, and no config reads. Everything here
takes an already-resolved properties dict (the caller fetches those inside
one ``config.coherent()`` scope) and returns plain data, so the whole
resolution policy is testable headlessly. See
``tests/test_submit_jobs_resolve.py``.

Why this module exists
----------------------

``SubmitJobsDialog`` used to seed every field from the *first* checked
entity and then send one shared ``settings`` dict for the whole batch. That
made a multi-shot submit render every shot over the primary shot's frame
range — silently, because ``batch_submit`` filled a missing frame key with
a hardcoded 1001-1100. Playblast never had the bug: ``batch_submit``
ignores the form for playblast frames and asks config per shot.

So a submission field has three possible sources, in priority order:

1. an **exception** — an explicit per-entity override,
2. a **pinned** form field — an explicit choice meant for the whole batch,
3. the **entity's own properties** — the per-entity default.

A field the artist never touches stays on (3), which is the safe default
and the opposite of what the dialog used to do. :func:`resolve_settings`
is that priority order; :func:`field_agreement` is what lets the form show
``⟨per entity⟩`` when the checked entities disagree.

Batch fields vs entity fields
-----------------------------

A :class:`Field` with ``prop=None`` has no per-entity source — Range mode,
Standalone, Copy to edit and the channel list are choices *about the
submission*, not properties *of the entity*. They always come from the
form and can never read ``⟨per entity⟩``.

``variants`` (the frozen wire key for what the UI calls channels) is
deliberately a batch field even though entities do carry a ``variants``
property. The channel menu lists the union over the batch and submits
exactly what is checked; a channel a given entity does not define still
reaches ``submit_entity_batch`` and still raises ``BatchSubmitError`` for
that entity. That visible failure is the contract — a union pick that
doesn't apply must not silently degrade to ``default``, and per-entity
resolution must not quietly turn it into a skip. :func:`entity_warnings`
surfaces it *before* the submit instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

# Sentinel: this field has no fallback constant. When it resolves to
# neither an exception, a pinned form value, nor an entity property, the
# key is left OUT of the settings dict entirely, and
# ``batch_submit.submit_entity_batch`` raises. This is what stops a shot
# with no configured frame range from quietly rendering 1001-1100.
REQUIRED = object()

# Sentinel: the checked entities do not agree on this field's value, so the
# form shows a placeholder rather than any one entity's number.
MIXED = object()

# Every entity has this channel implicitly. Mirrors
# ``tumblepipe.config.channels.DEFAULT_CHANNEL``; not imported, because
# that module pulls in ``tumblepipe.api`` at import time and this one stays
# free of pipeline imports so the tests can load it directly.
DEFAULT_CHANNEL = 'default'


@dataclass(frozen=True)
class Field:
    """One submission setting.

    Args:
        key: The key ``batch_submit.submit_entity_batch`` reads. Frozen
            wire tokens — renaming one changes the farm contract.
        section: ``'publish'`` | ``'render'`` | ``'playblast'``. The field
            is resolved only when its section is enabled.
        label: Human label, used by the pre-flight table's header.
        prop: Dotted path into the entity's resolved properties, or
            ``None`` for a batch field (see the module docstring).
        default: Fallback when the entity carries no value. :data:`REQUIRED`
            means "omit the key instead of guessing".
        kind: Coercion applied to whatever source wins — ``'int'``,
            ``'bool'``, ``'str'`` or ``'list'``.
        preflight: Show this field as a column in the pre-flight table.
            Only the ones that meaningfully vary per entity are worth a
            column; the rest are visible in the form.
    """

    key: str
    section: str
    label: str
    prop: str | None = None
    default: Any = None
    kind: str = 'str'
    preflight: bool = False


# The field table. Property paths and defaults match what the dialog and
# ``batch_submit`` used before per-entity resolution, so an entity that
# defines everything resolves to exactly the old values — the behaviour
# change is confined to entities that *differ* from the primary.
FIELDS: tuple[Field, ...] = (
    # ── Publish ───────────────────────────────────────────
    Field('pub_department', 'publish', 'Department',
          prop='submission.publish.department', kind='str', preflight=True),
    Field('pub_pool', 'publish', 'Pool',
          prop='farm.default_pool', default='general', kind='str'),
    Field('pub_priority', 'publish', 'Priority',
          prop='farm.priority', default=50, kind='int'),

    # ── Render ────────────────────────────────────────────
    Field('render_department', 'render', 'Department',
          prop='submission.render.department', kind='str', preflight=True),
    # Batch fields: choices about the submission, not the entity.
    Field('variants', 'render', 'Channels',
          prop=None, default=(DEFAULT_CHANNEL,), kind='list', preflight=True),
    Field('render_mode', 'render', 'Range', prop=None, default='full',
          kind='str'),
    Field('standalone', 'render', 'Standalone', prop=None, default=False,
          kind='bool'),
    Field('copy_to_edit', 'render', 'Copy to edit', prop=None, default=False,
          kind='bool'),
    # Entity fields. The four frame keys are REQUIRED: this is the fix.
    Field('first_frame', 'render', 'First', prop='frame_start',
          default=REQUIRED, kind='int', preflight=True),
    Field('last_frame', 'render', 'Last', prop='frame_end',
          default=REQUIRED, kind='int', preflight=True),
    Field('pre_roll', 'render', 'Pre-roll', prop='roll_start', default=0,
          kind='int'),
    Field('post_roll', 'render', 'Post-roll', prop='roll_end', default=0,
          kind='int'),
    Field('render_pool', 'render', 'Pool', prop='farm.default_pool',
          default='general', kind='str', preflight=True),
    Field('render_priority', 'render', 'Priority', prop='farm.priority',
          default=50, kind='int', preflight=True),
    Field('tile_count', 'render', 'Tiles', prop='farm.tile_count', default=4,
          kind='int'),
    Field('batch_size', 'render', 'Batch', prop='farm.batch_size', default=10,
          kind='int'),
    Field('samples', 'render', 'Samples', prop='render.pathtracedsamples',
          default=64, kind='int'),
    Field('denoise', 'render', 'Denoise', prop='render.enabledenoising',
          default=True, kind='bool'),
    Field('mblur', 'render', 'Motion blur', prop='render.enablemblur',
          default=True, kind='bool'),
    Field('dof', 'render', 'DOF', prop='render.enabledof', default=True,
          kind='bool'),

    # ── Playblast (shots only) ────────────────────────────
    # The playblast cut mirrors the render cut, so it reads the same
    # property. Its *frames* are not here at all: batch_submit derives them
    # per shot from config, which is the behaviour this module generalises.
    Field('pb_department', 'playblast', 'Department',
          prop='submission.render.department', kind='str', preflight=True),
    Field('pb_pool', 'playblast', 'Pool', prop='farm.default_pool',
          default='general', kind='str'),
    Field('pb_priority', 'playblast', 'Priority', prop='farm.priority',
          default=50, kind='int'),
    # Assembled into the 'pb_res' pair by _finish(); these two keys never
    # reach batch_submit.
    Field('pb_res_x', 'playblast', 'Width', prop='playblast.res_x',
          default=1280, kind='int'),
    Field('pb_res_y', 'playblast', 'Height', prop='playblast.res_y',
          default=720, kind='int'),
)

FIELDS_BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}

SECTIONS = ('publish', 'render', 'playblast')


def fields_for(sections: Sequence[str]) -> list[Field]:
    """Every field belonging to one of ``sections``, in table order."""
    wanted = set(sections)
    return [f for f in FIELDS if f.section in wanted]


def entity_fields(sections: Sequence[str] = SECTIONS) -> list[Field]:
    """Fields with a per-entity source — the ones that can read MIXED."""
    return [f for f in fields_for(sections) if f.prop is not None]


# ── value plumbing ────────────────────────────────────────


def nested(properties: dict, dotted: str, default: Any = None) -> Any:
    """Read ``a.b.c`` out of a resolved properties dict.

    Total: a missing key, or a non-dict where a dict was expected, yields
    ``default`` rather than raising.
    """
    cur: Any = properties
    for part in dotted.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _as_int(value: Any, fallback: Any) -> Any:
    # bool is an int subclass; letting True through as 1 would silently
    # turn a mis-typed config value into a frame number.
    if isinstance(value, bool):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


_FALSE_WORDS = frozenset({'false', 'f', 'no', 'n', 'off', '0', ''})


def _as_bool(value: Any, fallback: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        # Config holds real JSON booleans, but a hand-edited db file can
        # hold "false", and bool("false") is True.
        return value.strip().lower() not in _FALSE_WORDS
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


def _as_list(value: Any, fallback: Any) -> Any:
    if isinstance(value, str):
        return [value] if value.strip() else list(fallback or [])
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return list(fallback or [])


def coerce(value: Any, kind: str, fallback: Any = None) -> Any:
    """Force ``value`` into ``kind``, falling back on a bad value."""
    if value is REQUIRED or value is MIXED:
        return value
    if kind == 'int':
        return _as_int(value, fallback)
    if kind == 'bool':
        return _as_bool(value, fallback)
    if kind == 'list':
        return _as_list(value, fallback)
    if value is None:
        return fallback
    return str(value)


def entity_value(
    field: Field,
    properties: dict,
    fallbacks: dict | None = None,
) -> Any:
    """This entity's own value for ``field``, ignoring form and exceptions.

    Falls back to ``fallbacks[field.key]`` (context-derived defaults the
    caller looks up — e.g. the first renderable department, which this pure
    module can't know), then to ``field.default``. Returns :data:`REQUIRED`
    when the field has no fallback constant and the entity carries no value.
    """
    raw = nested(properties, field.prop) if field.prop else None
    if raw is not None:
        value = coerce(raw, field.kind, field.default)
        # A bad stored value coerces to the default; if there isn't one,
        # treat it as absent rather than shipping REQUIRED downstream.
        if value is not REQUIRED:
            return value
    if fallbacks and fallbacks.get(field.key) is not None:
        return coerce(fallbacks[field.key], field.kind, field.default)
    if field.default is REQUIRED:
        return REQUIRED
    return coerce(field.default, field.kind, field.default)


def resolve_settings(
    properties: dict,
    *,
    sections: Sequence[str],
    form: dict,
    pinned: Sequence[str] = (),
    exception: dict | None = None,
    fallbacks: dict | None = None,
) -> dict:
    """The ``settings`` dict ``submit_entity_batch`` gets for one entity.

    Args:
        properties: That entity's resolved config properties.
        sections: Which of ``publish`` / ``render`` / ``playblast`` are on.
        form: Every current form value, keyed by field key.
        pinned: Field keys the artist explicitly set — these apply to the
            whole batch. A field absent from ``pinned`` resolves per entity.
        exception: Per-entity overrides for this entity, highest priority.
        fallbacks: Context-derived defaults (first renderable department,
            …) used when the entity carries no value of its own.

    Returns:
        A settings dict carrying the three section booleans plus one key per
        resolved field. A :data:`REQUIRED` field with no value anywhere is
        **omitted**, so the farm side fails loudly instead of guessing.
    """
    on = set(sections)
    out: dict[str, Any] = {name: (name in on) for name in SECTIONS}
    pins = set(pinned)

    for field in fields_for(sections):
        if exception and field.key in exception:
            value = coerce(
                exception[field.key], field.kind, field.default,
            )
        elif field.prop is None or field.key in pins:
            # Batch field, or one the artist pinned: the form speaks for
            # every entity. A form value that is itself MIXED means the
            # widget is showing the placeholder and was never pinned, so
            # fall through to the entity.
            value = form.get(field.key, field.default)
            if value is MIXED:
                value = entity_value(field, properties, fallbacks)
            else:
                value = coerce(value, field.kind, field.default)
        else:
            value = entity_value(field, properties, fallbacks)

        if value is REQUIRED:
            continue  # omit — batch_submit raises rather than guessing
        out[field.key] = value

    return _finish(out, sections)


def _finish(settings: dict, sections: Sequence[str]) -> dict:
    """Assemble composite keys and drop the internal ones.

    ``pb_res`` is a ``[width, height]`` pair on the wire but two separate
    entity properties, so it is resolved as two fields and joined here.
    """
    if 'playblast' in set(sections):
        width = settings.pop('pb_res_x', None)
        height = settings.pop('pb_res_y', None)
        if width is not None and height is not None:
            settings['pb_res'] = [width, height]
    else:
        settings.pop('pb_res_x', None)
        settings.pop('pb_res_y', None)
    return settings


# ── form seeding ──────────────────────────────────────────


def field_agreement(
    field: Field,
    properties_list: Sequence[dict],
    fallbacks: dict | None = None,
) -> Any:
    """The value every entity agrees on, or :data:`MIXED`.

    This is what decides whether a form widget shows a real value or the
    dimmed ``⟨per entity⟩`` placeholder. An empty batch agrees vacuously and
    yields the field's default, so a dialog with nothing checked still shows
    a sane form.
    """
    if field.prop is None:
        # Batch fields have no per-entity source, so they always agree.
        return coerce(field.default, field.kind, field.default)
    values = [
        entity_value(field, properties or {}, fallbacks)
        for properties in properties_list
    ]
    if not values:
        return entity_value(field, {}, fallbacks)
    first = values[0]
    for value in values[1:]:
        if value != first:
            return MIXED
    return first


def seed_form(
    properties_list: Sequence[dict],
    *,
    sections: Sequence[str] = SECTIONS,
    fallbacks: dict | None = None,
) -> dict:
    """Agreement value (or :data:`MIXED`) for every field in ``sections``.

    The dialog renders MIXED as ``⟨per entity⟩``; everything else shows the
    shared value. With a single entity checked nothing can disagree, so the
    form looks exactly as it did before per-entity resolution — which
    matters, because that is the Render quick action's path.
    """
    return {
        field.key: field_agreement(field, properties_list, fallbacks)
        for field in fields_for(sections)
    }


# ── pre-flight ────────────────────────────────────────────


def preflight_fields(sections: Sequence[str]) -> list[Field]:
    """Columns worth showing in the pre-flight table."""
    return [f for f in fields_for(sections) if f.preflight]


def varying_fields(
    properties_list: Sequence[dict],
    *,
    sections: Sequence[str],
    pinned: Sequence[str] = (),
    fallbacks: dict | None = None,
) -> list[Field]:
    """Pre-flight columns whose value actually differs across the batch.

    A pinned field is the same for everyone by definition, so it is not a
    column; showing only what varies is what makes the table readable
    instead of a wall of repeats. Falls back to every pre-flight column when
    a single entity is checked, so the table still says something.
    """
    pins = set(pinned)
    columns = preflight_fields(sections)
    if len(properties_list) < 2:
        return columns
    return [
        field for field in columns
        if field.key not in pins
        and field_agreement(field, properties_list, fallbacks) is MIXED
    ]


#: The two stored spellings of the channel list. ``variants`` is what writers
#: emit today; ``channels`` is where the pipeline is going. Readers take
#: either, so data and readers can cross over independently.
#:
#: Deliberately a local copy of ``tumblepipe.config.channels`` rather than an
#: import: this module is pure by contract (properties in, settings out, no
#: config reads) and its tests boot without the project harness. The copy is
#: pinned against the shared implementation by a cross-check property in
#: ``tests/test_channel_spelling.py``, so it cannot drift.
CHANNEL_LIST_KEY = 'variants'
CHANNEL_LIST_KEY_ALT = 'channels'


def read_channel_list(data: dict, where: str) -> list:
    """The channel list under either spelling; raises when the two disagree."""
    old = data.get(CHANNEL_LIST_KEY)
    new = data.get(CHANNEL_LIST_KEY_ALT)
    if old is not None and new is not None and list(old) != list(new):
        raise ValueError(
            f'{where} carries both {CHANNEL_LIST_KEY!r}={old!r} and '
            f'{CHANNEL_LIST_KEY_ALT!r}={new!r}, which name different channels. '
            f'They are two spellings of one key and must agree; fix whichever '
            f'is wrong.'
        )
    return list(new if new is not None else (old or []))


def channel_names(properties: dict) -> list[str]:
    """Channels this entity defines, ``default`` first.

    ``variants`` is the spelling writers emit today; ``channels`` is also read.
    """
    raw = read_channel_list(properties, 'entity properties')
    if isinstance(raw, str):
        raw = [raw]
    names = [DEFAULT_CHANNEL]
    for value in raw:
        name = str(value).strip()
        if name and name not in names:
            names.append(name)
    return names


def channel_union(properties_list: Sequence[dict]) -> list[str]:
    """Every channel defined across the batch, ``default`` first.

    What the menu *offers*: a channel only the second checked shot defines
    still has to be selectable, which is what typing into the old csv field
    allowed.
    """
    names = [DEFAULT_CHANNEL]
    for properties in properties_list:
        for name in channel_names(properties or {}):
            if name not in names:
                names.append(name)
    return names


def channel_intersection(properties_list: Sequence[dict]) -> list[str]:
    """Channels *every* entity in the batch defines, ``default`` first.

    What the menu opens *checked*. For a single entity this is exactly its
    own channel list, which is what the old csv field pre-filled — so the
    Render quick action is unchanged. For a batch it is the largest set that
    renders on all of them, which matters because paleindia shots carry
    6-13 channels and barely overlap: seeding the union there would check
    twenty-odd channels and warn on every single entity.

    Never empty: ``channel_names`` gives every entity ``default``.
    """
    names = [DEFAULT_CHANNEL]
    if not properties_list:
        return names
    shared = set(channel_names(properties_list[0] or {}))
    for properties in properties_list[1:]:
        shared &= set(channel_names(properties or {}))
    # Ordered by the union so the menu and the seed agree on order.
    return [n for n in channel_union(properties_list) if n in shared]


def entity_warnings(
    properties: dict,
    resolved: dict,
    *,
    departments: Sequence[str] | None = None,
) -> list[str]:
    """Per-entity problems worth flagging *before* the submit loop fires.

    Every one of these currently surfaces only as a ``BatchSubmitError`` in
    the summary box — after the loop has already submitted every entity
    ahead of it in the batch.

    Args:
        properties: The entity's resolved properties.
        resolved: What :func:`resolve_settings` produced for it.
        departments: Departments assigned to this entity
            (``config.department.get_entity_departments``), or ``None`` to
            skip that check.
    """
    warnings: list[str] = []

    if resolved.get('render'):
        if 'first_frame' not in resolved or 'last_frame' not in resolved:
            warnings.append("no frame range configured")
        defined = set(channel_names(properties))
        missing = [
            name for name in read_channel_list(resolved, 'submission settings')
            if name not in defined
        ]
        if missing:
            plural = 's' if len(missing) > 1 else ''
            # Elide: a union pick across a wide batch can name twenty, and
            # this lands in a table cell.
            shown = ', '.join(missing[:4])
            if len(missing) > 4:
                shown += f", +{len(missing) - 4} more"
            warnings.append(f"channel{plural} not defined here: {shown}")
        warnings.extend(
            _department_warning(resolved.get('render_department'), departments)
        )

    if resolved.get('publish'):
        warnings.extend(
            _department_warning(resolved.get('pub_department'), departments)
        )

    if resolved.get('playblast'):
        warnings.extend(
            _department_warning(resolved.get('pb_department'), departments)
        )

    return warnings


def _department_warning(
    name: Any,
    departments: Sequence[str] | None,
) -> list[str]:
    """Flag a department this entity has not been assigned.

    Only a warning, never a block: the department pool is still what the
    staged build and ``render_stage`` read, so an unassigned department is
    unusual rather than impossible.
    """
    if departments is None or not name:
        return []
    if name in set(departments):
        return []
    return [f"department '{name}' is not assigned to this entity"]
