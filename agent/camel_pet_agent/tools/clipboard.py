"""Clipboard tool — returns the current OS clipboard text.

Opt-in from the UI: the server only exposes this tool to the agent when
the user has flipped the switch in settings.
"""
from __future__ import annotations


def get_clipboard() -> str:
    """Read the user's current clipboard text and return it.

    Returns the clipboard contents as a string. If the clipboard is
    empty or the platform has no clipboard available, returns an empty
    string.
    """
    try:
        import pyperclip
    except ImportError:
        return "(clipboard unavailable: pyperclip not installed)"
    try:
        return pyperclip.paste() or ""
    except Exception as e:  # pyperclip raises PyperclipException
        return f"(clipboard error: {e})"
