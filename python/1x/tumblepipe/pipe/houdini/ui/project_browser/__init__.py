"""Project Browser Package

This package contains the refactored project browser components split into logical modules.
"""

def create():
    """Create and return a ProjectBrowser widget.

    This function maintains compatibility with the original project_browser.py module.
    """
    from .main import ProjectBrowser
    widget = ProjectBrowser()
    return widget

def __getattr__(name):
    """Lazy import for ProjectBrowser to avoid Qt dependency in headless mode."""
    if name == "ProjectBrowser":
        from .main import ProjectBrowser
        return ProjectBrowser
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Maintain backward compatibility
__all__ = ['ProjectBrowser', 'create']