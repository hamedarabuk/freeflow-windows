"""
version.py — single source of truth for the FreeFlow version string.

Imported by updater.py and embedded into the PyInstaller build via freeflow.spec.
Bump this string before every release, then rebuild and update version.json on
the distribution server.
"""

__version__ = "2.1.2"
