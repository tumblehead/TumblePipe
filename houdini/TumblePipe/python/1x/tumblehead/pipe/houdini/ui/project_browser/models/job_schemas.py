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
class SectionDefinition:
    """Defines a collapsible section in the job submission spreadsheet."""
    key: str                               # Internal identifier ('publish', 'render')
    label: str                             # Display header ('Publish', 'Render')
    enabled_by_default: bool = False       # Section enabled by default
    collapsible: bool = True               # Can be collapsed
    collapsed_by_default: bool = False     # Initially collapsed


@dataclass
class ColumnDefinition:
    """Defines a single column's behavior and appearance."""
    key: str                          # Internal identifier (e.g., 'samples')
    label: str                        # Display header (e.g., 'Samples')
    column_type: ColumnType           # Editor type
    default_value: Any                # Default value when not overridden

    # Section assignment
    section_key: Optional[str] = None      # Which section this column belongs to

    # Entity property mapping (for loading defaults from entity config)
    property_path: Optional[str] = None    # e.g., 'render.pathtracedsamples', 'farm.tile_count'

    # Type-specific options
    min_value: Optional[float] = None      # For INTEGER/FLOAT
    max_value: Optional[float] = None      # For INTEGER/FLOAT
    step: Optional[float] = None           # For INTEGER/FLOAT spinbox step
    choices: Optional[list[str]] = None    # For COMBO: list of valid options
    choices_func: Optional[Callable[[], list[str]]] = None  # Dynamic choices
    choices_from: Optional[str] = None     # Property path to get choices from (e.g., 'farm.pools')
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
    """Defines sections and columns for job submission."""
    job_type: str                          # Schema identifier
    display_name: str                      # Display name
    sections: list[SectionDefinition] = field(default_factory=list)
    columns: list[ColumnDefinition] = field(default_factory=list)

    def get_column_by_key(self, key: str) -> Optional[ColumnDefinition]:
        """Get a column definition by its key."""
        for col in self.columns:
            if col.key == key:
                return col
        return None

    def get_section_by_key(self, key: str) -> Optional[SectionDefinition]:
        """Get a section definition by its key."""
        for section in self.sections:
            if section.key == key:
                return section
        return None

    def get_columns_for_section(self, section_key: str) -> list[ColumnDefinition]:
        """Get all columns belonging to a section."""
        return [col for col in self.columns if col.section_key == section_key]

    def get_section_column_range(self, section_key: str) -> tuple[int, int]:
        """Get the column index range for a section (start, end exclusive).

        Returns indices relative to schema columns (not including Entity column).
        """
        start = None
        end = None
        for i, col in enumerate(self.columns):
            if col.section_key == section_key:
                if start is None:
                    start = i
                end = i + 1
        if start is None:
            return (0, 0)
        return (start, end)


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
    depts = list_departments('shots')
    if not depts:
        raise RuntimeError("No departments configured for 'shots' context")
    return [d.name for d in depts]


def _get_assets_department_choices() -> list[str]:
    """Get department names for assets context."""
    depts = list_departments('assets')
    if not depts:
        raise RuntimeError("No departments configured for 'assets' context")
    return [d.name for d in depts]


def _get_default_department() -> str:
    """Get the default department (last in shots list, typically 'light' or render dept)."""
    depts = list_departments('shots')
    if not depts:
        raise RuntimeError("No departments configured for 'shots' context")
    # Return the last renderable department (typically 'light' or 'render')
    renderable = [d.name for d in depts if d.renderable]
    if renderable:
        return renderable[-1]
    return depts[-1].name


def _get_publishable_department_choices() -> list[str]:
    """Get department names that support publishing (for shots context)."""
    depts = list_departments('shots')
    if not depts:
        raise RuntimeError("No departments configured for 'shots' context")
    publishable = [d.name for d in depts if d.publishable]
    if not publishable:
        raise RuntimeError("No publishable departments configured for 'shots' context")
    return publishable


def _get_renderable_department_choices() -> list[str]:
    """Get department names that support rendering (for shots context)."""
    depts = list_departments('shots')
    if not depts:
        raise RuntimeError("No departments configured for 'shots' context")
    renderable = [d.name for d in depts if d.renderable]
    if not renderable:
        raise RuntimeError("No renderable departments configured for 'shots' context")
    return renderable


def _get_default_publishable_department() -> str:
    """Get the default publishable department (last publishable in list)."""
    publishable = _get_publishable_department_choices()
    return publishable[-1]


def _get_default_renderable_department() -> str:
    """Get the default renderable department (last renderable in list)."""
    renderable = _get_renderable_department_choices()
    return renderable[-1]


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
    """Create the unified submission schema with publish/render sections."""
    settings = _get_renderer_settings()

    return JobTypeSchema(
        job_type='submission',
        display_name='Job Submission',
        sections=[
            SectionDefinition(
                key='publish',
                label='Publish',
                enabled_by_default=False,
                collapsible=True,
                collapsed_by_default=False
            ),
            SectionDefinition(
                key='render',
                label='Render',
                enabled_by_default=False,
                collapsible=True,
                collapsed_by_default=False
            ),
        ],
        columns=[
            # ===== PUBLISH SECTION =====
            ColumnDefinition(
                key='pub_department',
                label='Department',
                column_type=ColumnType.COMBO,
                default_value=_get_default_publishable_department(),
                choices_func=_get_publishable_department_choices,
                section_key='publish',
                width=100,
                tooltip='Publish up to this department'
            ),
            ColumnDefinition(
                key='pub_pool',
                label='Pool',
                column_type=ColumnType.COMBO,
                default_value=_get_default_pool_name(),
                choices_func=_get_pool_choices,
                section_key='publish',
                width=80,
                tooltip='Farm pool for publish jobs'
            ),
            ColumnDefinition(
                key='pub_priority',
                label='Priority',
                column_type=ColumnType.INTEGER,
                default_value=_get_default_priority_value(),
                min_value=0,
                max_value=100,
                step=5,
                validator=_validate_priority,
                section_key='publish',
                width=60,
                tooltip='Priority for publish jobs (0-100)'
            ),

            # ===== RENDER SECTION =====
            ColumnDefinition(
                key='render_department',
                label='Department',
                column_type=ColumnType.COMBO,
                default_value=_get_default_renderable_department(),
                choices_func=_get_renderable_department_choices,
                section_key='render',
                width=100,
                tooltip='Department to render'
            ),
            ColumnDefinition(
                key='variants',
                label='Variants',
                column_type=ColumnType.MULTI_SELECT,
                default_value=[],  # Will be populated per-entity
                per_entity_choices=True,
                section_key='render',
                width=100,
                tooltip='Select variants to render'
            ),
            ColumnDefinition(
                key='render_pool',
                label='Pool',
                column_type=ColumnType.COMBO,
                default_value=_get_default_pool_name(),
                choices_func=_get_pool_choices,
                section_key='render',
                width=80,
                tooltip='Farm pool for render jobs'
            ),
            ColumnDefinition(
                key='render_priority',
                label='Priority',
                column_type=ColumnType.INTEGER,
                default_value=_get_default_priority_value(),
                min_value=0,
                max_value=100,
                step=5,
                validator=_validate_priority,
                section_key='render',
                width=60,
                tooltip='Priority for render jobs (0-100)'
            ),
            ColumnDefinition(
                key='tile_count',
                label='Tiles',
                column_type=ColumnType.INTEGER,
                default_value=settings.tile_count.default,
                min_value=settings.tile_count.min,
                max_value=settings.tile_count.max,
                step=1,
                section_key='render',
                width=45,
                tooltip='Number of tiles for parallel rendering'
            ),
            ColumnDefinition(
                key='pre_roll',
                label='Pre',
                column_type=ColumnType.INTEGER,
                default_value=0,
                min_value=0,
                max_value=999,
                section_key='render',
                width=40,
                tooltip='Pre-roll frames (motion blur handles)'
            ),
            ColumnDefinition(
                key='first_frame',
                label='First',
                column_type=ColumnType.INTEGER,
                default_value=1001,
                min_value=0,
                max_value=999999,
                section_key='render',
                width=50,
                tooltip='First frame of play range'
            ),
            ColumnDefinition(
                key='last_frame',
                label='Last',
                column_type=ColumnType.INTEGER,
                default_value=1100,
                min_value=0,
                max_value=999999,
                section_key='render',
                width=50,
                tooltip='Last frame of play range'
            ),
            ColumnDefinition(
                key='post_roll',
                label='Post',
                column_type=ColumnType.INTEGER,
                default_value=0,
                min_value=0,
                max_value=999,
                section_key='render',
                width=40,
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
                section_key='render',
                width=45,
                tooltip='Frames per batch'
            ),
            ColumnDefinition(
                key='denoise',
                label='Denoise',
                column_type=ColumnType.BOOLEAN,
                default_value=settings.denoise,
                section_key='render',
                width=55,
                tooltip='Enable denoising'
            ),
        ]
    )


def get_submission_schema() -> JobTypeSchema:
    """Get the submission schema."""
    return create_submission_schema()


# ============================================================================
# Separate Publish/Render Schemas (for vertical sectioned layout)
# ============================================================================

def create_publish_schema() -> JobTypeSchema:
    """Create schema for the Publish section only.

    Loads columns from config database first, falling back to hardcoded defaults.
    """
    # Try loading from config database
    config_columns = load_submission_columns('publish')
    if config_columns is not None:
        return JobTypeSchema(
            job_type='publish',
            display_name='Publish',
            sections=[],
            columns=config_columns
        )

    # Fall back to hardcoded columns
    return JobTypeSchema(
        job_type='publish',
        display_name='Publish',
        sections=[],  # No sections needed - this is a single-purpose schema
        columns=[
            ColumnDefinition(
                key='department',
                label='Department',
                column_type=ColumnType.COMBO,
                default_value=_get_default_publishable_department(),
                choices_func=_get_publishable_department_choices,
                width=100,
                tooltip='Publish up to this department'
            ),
            ColumnDefinition(
                key='pool',
                label='Pool',
                column_type=ColumnType.COMBO,
                default_value=_get_default_pool_name(),
                property_path='farm.default_pool',
                choices_func=_get_pool_choices,
                choices_from='farm.pools',
                width=80,
                tooltip='Farm pool for publish jobs'
            ),
            ColumnDefinition(
                key='priority',
                label='Priority',
                column_type=ColumnType.INTEGER,
                default_value=_get_default_priority_value(),
                property_path='farm.priority',
                min_value=0,
                max_value=100,
                step=5,
                validator=_validate_priority,
                width=60,
                tooltip='Priority for publish jobs (0-100)'
            ),
        ]
    )


def create_render_schema() -> JobTypeSchema:
    """Create schema for the Render section only.

    Loads columns from config database first, falling back to hardcoded defaults.
    """
    # Try loading from config database
    config_columns = load_submission_columns('render')
    if config_columns is not None:
        return JobTypeSchema(
            job_type='render',
            display_name='Render',
            sections=[],
            columns=config_columns
        )

    # Fall back to hardcoded columns
    settings = _get_renderer_settings()

    return JobTypeSchema(
        job_type='render',
        display_name='Render',
        sections=[],  # No sections needed - this is a single-purpose schema
        columns=[
            ColumnDefinition(
                key='department',
                label='Department',
                column_type=ColumnType.COMBO,
                default_value=_get_default_renderable_department(),
                choices_func=_get_renderable_department_choices,
                width=100,
                tooltip='Department to render'
            ),
            ColumnDefinition(
                key='variants',
                label='Variants',
                column_type=ColumnType.MULTI_SELECT,
                default_value=[],  # Will be populated per-entity
                per_entity_choices=True,
                width=100,
                tooltip='Select variants to render'
            ),
            ColumnDefinition(
                key='pool',
                label='Pool',
                column_type=ColumnType.COMBO,
                default_value=_get_default_pool_name(),
                property_path='farm.default_pool',
                choices_func=_get_pool_choices,
                choices_from='farm.pools',
                width=80,
                tooltip='Farm pool for render jobs'
            ),
            ColumnDefinition(
                key='priority',
                label='Priority',
                column_type=ColumnType.INTEGER,
                default_value=_get_default_priority_value(),
                property_path='farm.priority',
                min_value=0,
                max_value=100,
                step=5,
                validator=_validate_priority,
                width=60,
                tooltip='Priority for render jobs (0-100)'
            ),
            ColumnDefinition(
                key='tile_count',
                label='Tiles',
                column_type=ColumnType.INTEGER,
                default_value=settings.tile_count.default,
                property_path='farm.tile_count',
                min_value=settings.tile_count.min,
                max_value=settings.tile_count.max,
                step=1,
                width=45,
                tooltip='Number of tiles for parallel rendering'
            ),
            ColumnDefinition(
                key='pre_roll',
                label='Pre',
                column_type=ColumnType.INTEGER,
                default_value=0,
                min_value=0,
                max_value=999,
                width=40,
                tooltip='Pre-roll frames (motion blur handles)'
            ),
            ColumnDefinition(
                key='first_frame',
                label='First',
                column_type=ColumnType.INTEGER,
                default_value=1001,
                min_value=0,
                max_value=999999,
                width=50,
                tooltip='First frame of play range'
            ),
            ColumnDefinition(
                key='last_frame',
                label='Last',
                column_type=ColumnType.INTEGER,
                default_value=1100,
                min_value=0,
                max_value=999999,
                width=50,
                tooltip='Last frame of play range'
            ),
            ColumnDefinition(
                key='post_roll',
                label='Post',
                column_type=ColumnType.INTEGER,
                default_value=0,
                min_value=0,
                max_value=999,
                width=40,
                tooltip='Post-roll frames (motion blur handles)'
            ),
            ColumnDefinition(
                key='batch_size',
                label='Batch',
                column_type=ColumnType.INTEGER,
                default_value=settings.batch_size.default,
                property_path='farm.batch_size',
                min_value=settings.batch_size.min,
                max_value=settings.batch_size.max,
                step=5,
                width=45,
                tooltip='Frames per batch'
            ),
            ColumnDefinition(
                key='denoise',
                label='Denoise',
                column_type=ColumnType.BOOLEAN,
                default_value=settings.denoise,
                property_path='render.enabledenoising',
                width=55,
                tooltip='Enable denoising'
            ),
        ]
    )


# ============================================================================
# Config-Driven Column Loading
# ============================================================================

# Column type string to enum mapping
_COLUMN_TYPE_MAP = {
    'integer': ColumnType.INTEGER,
    'float': ColumnType.FLOAT,
    'combo': ColumnType.COMBO,
    'boolean': ColumnType.BOOLEAN,
    'string': ColumnType.STRING,
    'multi_select': ColumnType.MULTI_SELECT,
}

# Choice function mapping for config-defined columns
_CHOICES_FUNC_MAP = {
    'pools': _get_pool_choices,
    'publishable_departments': _get_publishable_department_choices,
    'renderable_departments': _get_renderable_department_choices,
    'shots_departments': _get_shots_department_choices,
    'assets_departments': _get_assets_department_choices,
}


def _column_from_dict(data: dict, section_key: Optional[str] = None) -> ColumnDefinition:
    """Create a ColumnDefinition from a JSON dict.

    Args:
        data: Dict with column configuration
        section_key: Optional section key to assign

    Returns:
        ColumnDefinition instance
    """
    # Parse type
    type_str = data.get('type', 'string').lower()
    column_type = _COLUMN_TYPE_MAP.get(type_str, ColumnType.STRING)

    # Parse default value
    default_value = data.get('default')

    # Handle choices function
    choices_func = None
    choices_func_name = data.get('choices_func')
    if choices_func_name:
        choices_func = _CHOICES_FUNC_MAP.get(choices_func_name)

    # Handle validator
    validator = None
    validator_name = data.get('validator')
    if validator_name == 'priority':
        validator = _validate_priority

    return ColumnDefinition(
        key=data['key'],
        label=data.get('label', data['key']),
        column_type=column_type,
        default_value=default_value,
        section_key=section_key,
        property_path=data.get('property_path'),
        min_value=data.get('min'),
        max_value=data.get('max'),
        step=data.get('step'),
        choices=data.get('choices'),
        choices_func=choices_func,
        choices_from=data.get('choices_from'),
        per_entity_choices=data.get('per_entity_choices', False),
        validator=validator,
        width=data.get('width', 100),
        tooltip=data.get('tooltip', ''),
    )


def load_submission_columns(section: str) -> Optional[list[ColumnDefinition]]:
    """Load column definitions from config database.

    Args:
        section: 'publish' or 'render'

    Returns:
        List of ColumnDefinition objects, or None if config not found
    """
    try:
        from tumblehead.api import default_client
        from tumblehead.util.uri import Uri

        api = default_client()
        props = api.config.get_properties(Uri.parse_unsafe('config:/submission/columns'))

        if props is None:
            return None

        section_data = props.get(section, {})
        columns_data = section_data.get('columns', [])

        if not columns_data:
            return None

        return [_column_from_dict(col_data, section_key=section) for col_data in columns_data]

    except Exception:
        return None


def get_default_hidden_columns(section: str) -> set[str]:
    """Get default hidden columns from config database.

    Args:
        section: 'publish' or 'render'

    Returns:
        Set of column keys that should be hidden by default
    """
    try:
        from tumblehead.api import default_client
        from tumblehead.util.uri import Uri

        api = default_client()
        props = api.config.get_properties(Uri.parse_unsafe('config:/submission/columns'))

        if props is None:
            return set()

        section_data = props.get(section, {})
        default_hidden = section_data.get('default_hidden', [])

        return set(default_hidden)

    except Exception:
        return set()


def get_column_property_map(section: str) -> dict[str, str]:
    """Get a mapping of column keys to their property paths for a section.

    This is used by the dialog to know which entity properties to load
    for each column.

    Args:
        section: 'publish' or 'render'

    Returns:
        Dict mapping column key to property path (e.g., {'denoise': 'render.enabledenoising'})
    """
    # Try config-driven columns first
    columns = load_submission_columns(section)

    # Fall back to hardcoded schema
    if columns is None:
        if section == 'publish':
            schema = create_publish_schema()
        elif section == 'render':
            schema = create_render_schema()
        else:
            return {}
        columns = schema.columns

    return {
        col.key: col.property_path
        for col in columns
        if col.property_path is not None
    }


# ============================================================================
# Column Visibility Persistence
# ============================================================================

def _get_visibility_prefs_path():
    """Get path to column visibility preferences file."""
    from pathlib import Path
    home = Path.home()
    prefs_dir = home / '.tumblehead'
    prefs_dir.mkdir(parents=True, exist_ok=True)
    return prefs_dir / 'submission_column_visibility.json'


def load_column_visibility(section: str) -> set[str]:
    """Load hidden column keys from user preferences, falling back to config defaults.

    Args:
        section: 'publish' or 'render'

    Returns:
        Set of column keys that should be hidden
    """
    try:
        from tumblehead.util.io import load_json
        prefs_path = _get_visibility_prefs_path()
        if prefs_path.exists():
            prefs = load_json(prefs_path)
            if 'hidden' in prefs and section in prefs['hidden']:
                # User has saved preferences for this section
                return set(prefs['hidden'][section])

        # No user prefs - use default_hidden from config database
        return get_default_hidden_columns(section)

    except Exception:
        return get_default_hidden_columns(section)


def save_column_visibility(section: str, hidden_columns: set[str]):
    """Save hidden column keys to user preferences.

    Args:
        section: 'publish' or 'render'
        hidden_columns: Set of column keys to hide
    """
    try:
        from tumblehead.util.io import load_json, store_json
        prefs_path = _get_visibility_prefs_path()

        # Load existing prefs or create new
        if prefs_path.exists():
            prefs = load_json(prefs_path)
        else:
            prefs = {}

        # Update hidden columns for this section
        if 'hidden' not in prefs:
            prefs['hidden'] = {}
        prefs['hidden'][section] = list(hidden_columns)

        store_json(prefs_path, prefs)
    except Exception:
        pass  # Silently fail if we can't save prefs
