"""Project Browser Package

This package contains the refactored project browser components split into logical modules.
"""

# For backwards compatibility, import the main components
from .main import ProjectBrowser

def create():
    """Create and return a ProjectBrowser widget.
    
    This function maintains compatibility with the original project_browser.py module.
    """
    widget = ProjectBrowser()
    return widget

# Maintain backward compatibility
__all__ = ['ProjectBrowser', 'create']