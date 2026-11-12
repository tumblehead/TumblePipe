"""The up-front validation pass of a publish run (#56): pure logic.

A Multi publish used to validate shot by shot as the run reached each one,
so a failure on shot 1 of 4 stopped the run behind a modal and the later
shots' issues only showed once the earlier ones were answered. Now, before
anything exports, the process dialog runs every ticked validation against
the stage as it is, and shows one dialog grouping the issues by shot (or
asset) and department, with a checkbox per shot: Continue Anyway runs the
ticked shots, Cancel Export runs nothing.

The validations still run in place during the run, because a downstream
department's validation may see what an upstream export of the same run
wrote. An issue the artist already saw and accepted up front does not ask
again there; only an issue the run itself brought in does.

No Qt and no ``hou`` here: the grouping, which issues were accepted, which
issues are new and which shots to skip are functions of plain data, tested
in tests/test_validation_preflight.py. An issue is anything with
``severity``, ``message`` and ``prim_path`` attributes (the validators'
``ValidationIssue``); a task is anything with the ProcessTask attributes
``collect_findings`` reads.
"""

from collections.abc import Iterable
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

# What identifies an issue across the up-front pass and the run:
# (severity, message, prim path). The suggestion is advice about the
# issue, not part of it.
IssueKey = tuple[str, str, str | None]

# (entity, department, issue key): one issue of one validation, accepted.
Accepted = frozenset[tuple[str, str, IssueKey]]

CONTINUE = 'continue'
CANCEL = 'cancel'


def issue_key(issue) -> IssueKey:
    """The identity of ``issue``: its severity, message and prim path."""
    severity = getattr(issue.severity, 'value', issue.severity)
    return (str(severity), issue.message, issue.prim_path)


@dataclass(frozen=True)
class Finding:
    """What one validation task of the run would ask the artist about.

    ``entity`` is the entity URI as a string (the shot or asset the task
    publishes), ``issues`` the issues its prompt would show.
    """
    entity: str
    department: str
    issues: tuple


# entity -> [(department, issues)], both in first-seen order
Grouped = list[tuple[str, list[tuple[str, list]]]]


def group_findings(findings: Iterable[Finding]) -> Grouped:
    """Group the findings by entity, then department, in first-seen order.

    Two validations of the same entity and department (one per channel)
    merge into one department entry, an issue both report showing once.
    Findings without issues, and entities left with none, are dropped:
    the dialog lists only what needs an answer.
    """
    entities: dict[str, dict[str, list]] = {}
    seen: dict[tuple[str, str], set[IssueKey]] = {}
    for finding in findings:
        if not finding.issues:
            continue
        departments = entities.setdefault(finding.entity, {})
        issues = departments.setdefault(finding.department, [])
        keys = seen.setdefault((finding.entity, finding.department), set())
        for issue in finding.issues:
            key = issue_key(issue)
            if key in keys:
                continue
            keys.add(key)
            issues.append(issue)
    return [
        (entity, list(departments.items()))
        for entity, departments in entities.items()
    ]


def issue_count(grouped: Grouped) -> int:
    """How many issues ``grouped`` lists, over every entity and department."""
    return sum(
        len(issues)
        for _, departments in grouped
        for _, issues in departments
    )


def accepted_issues(grouped: Grouped, kept: Iterable[str]) -> Accepted:
    """The issues accepted by Continue Anyway with the ``kept`` entities
    ticked: every issue of a kept entity, under its entity and department.

    A skipped entity accepts nothing; it does not run.
    """
    kept = set(kept)
    return frozenset(
        (entity, department, issue_key(issue))
        for entity, departments in grouped
        if entity in kept
        for department, issues in departments
        for issue in issues
    )


def skipped_entities(grouped: Grouped, kept: Iterable[str]) -> list[str]:
    """The entities of ``grouped`` the artist unticked, in dialog order."""
    kept = set(kept)
    return [entity for entity, _ in grouped if entity not in kept]


@dataclass(frozen=True)
class Outcome:
    """What the up-front dialog decided.

    ``proceed`` False: Cancel Export, nothing runs. Otherwise the run
    disables every task of the ``skip`` entities and the validations in
    place pass the ``accepted`` issues without asking again.
    """
    proceed: bool
    skip: tuple[str, ...] = ()
    accepted: Accepted = frozenset()


def outcome(choice: str, grouped: Grouped, kept: Iterable[str]) -> Outcome:
    """The run's course from the artist's answer to the up-front dialog."""
    if choice != CONTINUE:
        return Outcome(proceed=False)
    kept = set(kept)
    return Outcome(
        proceed=True,
        skip=tuple(skipped_entities(grouped, kept)),
        accepted=accepted_issues(grouped, kept),
    )


def unseen_issues(
    issues: Iterable, accepted: Accepted, entity: str, department: str,
) -> list:
    """The ``issues`` of an in-place validation not accepted up front for
    the same entity and department, in order: what the run brought in."""
    return [
        issue for issue in issues
        if (entity, department, issue_key(issue)) not in accepted
    ]


def has_error(issues: Iterable) -> bool:
    """Whether any of ``issues`` is an error, not a warning."""
    return any(issue_key(issue)[0] == 'error' for issue in issues)


def asks(issues: list, ask_on_warnings: bool) -> bool:
    """Whether an in-place validation left with ``issues`` (the unseen
    ones) shows its prompt: the stage validation asks on an error only, as
    it always has; the rig validation asks on a warning too."""
    return bool(issues) if ask_on_warnings else has_error(issues)


def _runs_in_mode(task, is_local: bool) -> bool:
    return (task.execute_local if is_local else task.execute_farm) is not None


def validation_tasks(tasks: Iterable, is_local: bool) -> list:
    """The validation tasks a run of ``tasks`` would run: ticked, of type
    ``validate``, able to say what they would ask (``collect_issues``) and
    runnable in the run's mode. Children of a ticked group count too."""
    found = []
    for task in tasks:
        for candidate in (task, *(task.children or ())):
            if candidate is not task and not task.enabled:
                continue
            if (candidate.enabled and candidate.task_type == 'validate'
                    and getattr(candidate, 'collect_issues', None) is not None
                    and _runs_in_mode(candidate, is_local)):
                found.append(candidate)
    return found


def collect_findings(tasks: Iterable, is_local: bool) -> list[Finding]:
    """Run every validation of the run up front and return what each would
    ask about. A validation that raises is logged and left out here: it runs
    again in place, where it fails its task as before."""
    findings = []
    for task in validation_tasks(tasks, is_local):
        try:
            issues = task.collect_issues()
        except Exception:
            logger.exception(
                'Up-front validation %r raised; it runs again in place',
                task.description,
            )
            continue
        if issues:
            findings.append(Finding(str(task.uri), task.department, tuple(issues)))
    return findings
