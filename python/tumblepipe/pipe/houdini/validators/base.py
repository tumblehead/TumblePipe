"""Base classes for USD stage validation."""

import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class ValidationSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    message: str
    prim_path: str | None = None
    severity: ValidationSeverity = ValidationSeverity.ERROR
    suggestion: str | None = None


@dataclass
class ValidationResult:
    passed: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    def add_error(
        self,
        message: str,
        prim_path: str | None = None,
        suggestion: str | None = None,
    ):
        self.issues.append(
            ValidationIssue(message, prim_path, ValidationSeverity.ERROR, suggestion)
        )
        self.passed = False

    def add_warning(
        self,
        message: str,
        prim_path: str | None = None,
        suggestion: str | None = None,
    ):
        self.issues.append(
            ValidationIssue(message, prim_path, ValidationSeverity.WARNING, suggestion)
        )

    def merge(self, other: 'ValidationResult'):
        self.issues.extend(other.issues)
        if not other.passed:
            self.passed = False

    def grouped_issues(
        self,
    ) -> list[tuple['ValidationSeverity', str, list[str], str | None]]:
        """Group issues by (severity, message), preserving first-seen order.

        Returns a list of (severity, message, prim_paths, suggestion) tuples.
        `prim_paths` is the list of every prim_path that produced this
        (severity, message) pair (in original order); single-prim issues yield a
        one-element list and issues with no prim_path yield an empty list.
        `suggestion` is taken from the first issue in the group (validators
        should attach the same suggestion to every issue with the same message).

        Use this to render summaries like "Mesh missing rest positions — 14 prims"
        with expandable details, instead of one line per affected prim.
        """
        groups: dict[tuple['ValidationSeverity', str], list[str]] = {}
        suggestions: dict[tuple['ValidationSeverity', str], str | None] = {}
        order: list[tuple['ValidationSeverity', str]] = []
        for issue in self.issues:
            key = (issue.severity, issue.message)
            if key not in groups:
                groups[key] = []
                suggestions[key] = issue.suggestion
                order.append(key)
            if issue.prim_path:
                groups[key].append(issue.prim_path)
        return [
            (sev, msg, groups[(sev, msg)], suggestions[(sev, msg)])
            for (sev, msg) in order
        ]

    def format_message(self) -> str:
        if not self.issues:
            return "Validation passed"
        lines = []
        for severity, message, prim_paths, suggestion in self.grouped_issues():
            prefix = "ERROR" if severity == ValidationSeverity.ERROR else "WARNING"
            if len(prim_paths) <= 1:
                # Single occurrence — show inline, original format
                path = prim_paths[0] if prim_paths else None
                if path:
                    lines.append(f"[{prefix}] {path}: {message}")
                else:
                    lines.append(f"[{prefix}] {message}")
            else:
                # Multiple occurrences — summary + indented paths
                lines.append(f"[{prefix}] {message} ({len(prim_paths)} prims)")
                for path in prim_paths:
                    lines.append(f"    - {path}")
            if suggestion:
                lines.append(f"    Suggestion: {suggestion}")
        return "\n".join(lines)


class StageValidatorRegistry:
    """Registry for stage validators.

    Allows registration of custom validators and running them on USD stages.
    """

    def __init__(self):
        self._validators: dict[str, Callable] = {}

    def register(self, name: str, validator: Callable):
        """Register a validator function.

        Args:
            name: Unique identifier for the validator
            validator: Function that takes a USD prim (stage root) and returns ValidationResult
        """
        self._validators[name] = validator

    def unregister(self, name: str):
        """Unregister a validator by name."""
        self._validators.pop(name, None)

    def list_validators(self) -> list[str]:
        """List all registered validator names."""
        return list(self._validators.keys())

    def run(
        self,
        root,
        validators: list[str] | None = None,
        context: dict | None = None
    ) -> ValidationResult:
        """Run validators on a stage root.

        Args:
            root: USD stage pseudo root prim
            validators: Optional list of validator names to run. If None, runs all.
            context: Optional context dict passed to validators that accept it.
                     May contain 'entity_uri' for asset structure validation.

        Returns:
            Combined ValidationResult from all validators
        """
        result = ValidationResult()
        names = validators if validators else list(self._validators.keys())
        for name in names:
            if name in self._validators:
                validator = self._validators[name]
                # Check if validator accepts context parameter
                sig = inspect.signature(validator)
                if 'context' in sig.parameters:
                    validator_result = validator(root, context=context)
                else:
                    validator_result = validator(root)
                result.merge(validator_result)
        return result
