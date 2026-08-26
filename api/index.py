"""
Vercel Python entry — path-safe re-export of the Flask app.

Primary deploy path is root main.py (Flask framework preset).
This module remains for /api* compatibility and explicit WSGI export.
"""
from __future__ import annotations

import os
import sys

# Ensure project root is on sys.path when Vercel loads api/index.py as a function
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from main import app  # noqa: E402  — WSGI app Vercel binds to

# Explicit aliases some runtimes look for
application = app
