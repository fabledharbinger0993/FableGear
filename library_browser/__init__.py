"""
library_browser — Three-view Record Room library browser.

Provides three distinct library views for different perspectives on the
music collection: Rekordbox View, Local View, and Integrated View.

Key Features:
- Rekordbox View: Database-centric view with file health indicators
- Local View: Filesystem-centric view with flexible sorting
- Integrated View: Three-column comparison and sync interface
- Efficient scanning and caching for large libraries
- Real-time filtering and search
- Integration with existing pyrekordbox database access

Example Usage:
    from library_browser import LibraryBrowser, ViewMode
    
    browser = LibraryBrowser()
    
    # Switch to Rekordbox View
    browser.set_view_mode(ViewMode.REKORDBOX)
    rekordbox_tracks = browser.get_tracks()
    
    # Switch to Local View
    browser.set_view_mode(ViewMode.LOCAL)
    local_files = browser.get_files()
    
    # Switch to Integrated View
    browser.set_view_mode(ViewMode.INTEGRATED)
    integrated_data = browser.get_integrated_view()
"""

from .core import LibraryBrowser, ViewMode, TrackData, FileData
from .rekordbox_view import RekordboxView
from .local_view import LocalView
from .integrated_view import IntegratedView
from .scanner import LibraryScanner
from .cache import ViewCache

__all__ = [
    "LibraryBrowser",
    "ViewMode",
    "TrackData",
    "FileData",
    "RekordboxView",
    "LocalView",
    "IntegratedView",
    "LibraryScanner",
    "ViewCache",
]
