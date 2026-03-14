"""
conftest.py — ensure the project root is on sys.path so that
`from main import app` and `from core.xxx import ...` work correctly
when pytest is invoked from any working directory.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
