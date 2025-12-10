"""Job submission schema defining columns for the job submission spreadsheet."""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

from tumblehead.config.farm import list_pools, get_default_pool, get_default_priority
from tumblehead.config.renderer import get_renderer_defaults
from tumblehead.config.department import list_departments


class ColumnType(Enum):
    """Supported column editor types."""
    INTEGER = auto()      # QSpinBox
    FLOAT = auto()        # QDoubleSpinBox
    COMBO = auto()        # QComboBox
    BOOLEAN = auto()      # QCheckBox
    STRING = auto()       # QLineEdit
    MULTI_SELECT = auto() # Multi-select dropdown (checkable items)


@dataclass
class ColumnDefinition:
    """Defines a single column's behavior and appearance."""
    key: str                          # Internal identifier (e.g., 'samples')
    label: str                        # Display header (e.g., 'Samples')
    column_type: ColumnType           # Editor type
    default_value: Any                # Default value when not overridden

    # Type-specific options
    min_value: Optional[float] = None      # For INTEGER/FLOAT
    max_value: Optional[float] = None      # For INTEGER/FLOAT
    step: Optional[float] = None           # For INTEGER/FLOAT spinbox step
    choices: Optional[list[str]] = None    # For COMBO: list of valid options
    choices_func: Optional[Callable[[], list[str]]] = None  # Dynamic choices
    per_entity_choices: bool = False       # For MULTI_SELECT: choices vary per entity

    # Validation
    validator: Optional[Callable[[Any], tuple[bool, str]]] = None

    # Display
    width: int = 100                       # Suggested column width
    tooltip: str = ''                      # Tooltip text

    def get_choices(self) -> list[str]:
        """Get choices, either static or from function."""
        if self.choices_func:
            return self.choices_func()
        return self.choices or []


@dataclass
class JobTypeSchema:
    """Defines columns for job submission."""
    job_type: str                          # Schema identifier
    display_name: str                      # Display name
    columns: list[ColumnDefinition] = field(default_factory=list)

    def get_column_by_key(self, key: str) -> Optional[ColumnDefinition]:
        """Get a column definition by its key."""
        for col in self.columns:
            if col.key == key:
                return col
        return None


# ============================================================================
# Validators
# ============================================================================

def _validate_priority(value: int) -> tuple[bool, str]:
    """Validate priority is in valid range."""
    if not isinstance(value, int):
        return False, "Priority must be an integer"
    if not 0 <= value <= 100:
        return False, "Priority must be between 0 and 100"
    return True, ""


# ============================================================================
# Dynamic choice functions
# ============================================================================

def _get_pool_choices() -> list[str]:
    """Get available pool names from config."""
    try:
        pools = list_pools()
        return [p.name for p in pools] if pools else ['general']
    except Exception:
        return ['general']


def _get_shots_department_choices() -> list[str]:
    """Get department names for shots context."""
    try:
        depts = list_departments('shots')
        return [d.name for d in depts] if depts else ['lighting']
    except Exception:
        return ['lighting']


def _get_assets_department_choices() -> list[str]:
    """Get department names for assets context."""
    try:
        depts = list_departments('assets')
        return [d.name for d in depts] if depts else ['model']
    except Exception:
        return ['model']


# ============================================================================
# Helper functions for defaults
# ============================================================================

def _get_renderer_settings():
    """Get renderer settings with fallback defaults."""
    try:
        return get_renderer_defaults()
    except Exception:
        # Return hardcoded defaults if config unavailable
        from tumblehead.config.renderer import RangeSetting, RendererDefaults
        return RendererDefaults(
            tile_count=RangeSetting(default=4, min=1, max=16),
            batch_size=RangeSetting(default=10, min=1, max=100),
            timeout_minutes=RangeSetting(default=45, min=1, max=480),
            denoise=True
        )


def _get_default_priority_value() -> int:
    """Get default priority with fallback."""
    try:
        return get_default_priority()
    except Exception:
        return 50


def _get_default_pool_name() -> str:
    """Get default pool name with fallback."""
    try:
        return get_default_pool()
    except Exception:
        return 'general'


# ============================================================================
# Submission schema
# ============================================================================

def create_submission_schema() -> JobTypeSchema:
    """Create the unified submission schema with publish/render columns."""
    settings = _get_renderer_settings()

    return JobTypeSchema(
        job_type='submission',
        display_name='Job Submission',
        columns=[
            # Job type checkboxes
            ColumnDefinition(
                key='publish',
                label='Publish',
                column_type=ColumnType.BOOLEAN,
                default_value=False,
                width=60,
                tooltip='Create publish jobs for departments up to selected'
            ),
            ColumnDefinition(
                key='render',
                label='Render',
                column_type=ColumnType.BOOLEAN,
                default_value=False,
                width=60,
                tooltip='Create stage and render jobs'
            ),
            # Variant selection (per-entity choices)
            ColumnDefinition(
                key='variants',
                label='Variants',
                column_type=ColumnType.MULTI_SELECT,
                default_value=[],  # Will be populated per-entity
                per_entity_choices=True,
                width=120,
                tooltip='Select variants to render'
            ),
            # Common settings
            ColumnDefinition(
                key='department',
                label='Department',
                column_type=ColumnType.COMBO,
                default_value='lighting',
                choices_func=_get_shots_department_choices,
                width=100,
                tooltip='For publish: up to this department. For render: render this department.'
            ),
            ColumnDefinition(
                key='pool_name',
                label='Pool',
                column_type=ColumnType.COMBO,
                default_value=_get_default_pool_name(),
                choices_func=_get_pool_choices,
                width=100,
                tooltip='Render farm pool'
            ),
            ColumnDefinition(
                key='priority',
                label='Priority',
                column_type=ColumnType.INTEGER,
                default_value=_get_default_priority_value(),
                min_value=0,
                max_value=100,
                step=5,
                validator=_validate_priority,
                width=70,
                tooltip='Job priority (0-100)'
            ),
            # Render settings
            ColumnDefinition(
                key='tile_count',
                label='Tiles',
                column_type=ColumnType.INTEGER,
                default_value=settings.tile_count.default,
                min_value=settings.tile_count.min,
                max_value=settings.tile_count.max,
                step=1,
                width=50,
                tooltip='Number of tiles for parallel rendering'
            ),
            ColumnDefinition(
                key='pre_roll',
                label='Pre',
                column_type=ColumnType.INTEGER,
                default_value=0,
                min_value=0,
                max_value=999,
                width=45,
                tooltip='Pre-roll frames (motion blur handles)'
            ),
            ColumnDefinition(
                key='first_frame',
                label='First',
                column_type=ColumnType.INTEGER,
                default_value=1001,
                min_value=0,
                max_value=999999,
                width=55,
                tooltip='First frame of play range'
            ),
            ColumnDefinition(
                key='last_frame',
                label='Last',
                column_type=ColumnType.INTEGER,
                default_value=1100,
                min_value=0,
                max_value=999999,
                width=55,
                tooltip='Last frame of play range'
            ),
            ColumnDefinition(
                key='post_roll',
                label='Post',
                column_type=ColumnType.INTEGER,
                default_value=0,
                min_value=0,
                max_value=999,
                width=45,
                tooltip='Post-roll frames (motion blur handles)'
            ),
            ColumnDefinition(
                key='batch_size',
                label='Batch',
                column_type=ColumnType.INTEGER,
                default_value=settings.batch_size.default,
                min_value=settings.batch_size.min,
                max_value=settings.batch_size.max,
                step=5,
                width=50,
                tooltip='Frames per batch'
            ),
            ColumnDefinition(
                key='denoise',
                label='Denoise',
                column_type=ColumnType.BOOLEAN,
                default_value=settings.denoise,
                width=60,
                tooltip='Enable denoising'
            ),
        ]
    )


def get_submission_schema() -> JobTypeSchema:
    """Get the submission schema."""
    return create_submission_schema()
