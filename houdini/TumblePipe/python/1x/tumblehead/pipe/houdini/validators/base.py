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


@dataclass
class ValidationResult:
    passed: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    def add_error(self, message: str, prim_path: str | None = None):
        self.issues.append(ValidationIssue(message, prim_path, ValidationSeverity.ERROR))
        self.passed = False

    def add_warning(self, message: str, prim_path: str | None = None):
        self.issues.append(ValidationIssue(message, prim_path, ValidationSeverity.WARNING))

    def merge(self, other: 'ValidationResult'):
        self.issues.extend(other.issues)
        if not other.passed:
            self.passed = False

    def format_message(self) -> str:
        if not self.issues:
            return "Validation passed"
        lines = []
        for issue in self.issues:
            prefix = "ERROR" if issue.severity == ValidationSeverity.ERROR else "WARNING"
            if issue.prim_path:
                lines.append(f"[{prefix}] {issue.prim_path}: {issue.message}")
            else:
                lines.append(f"[{prefix}] {issue.message}")
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
