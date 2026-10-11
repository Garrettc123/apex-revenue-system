from flask import Flask, jsonify, request, render_template, redirect
import base64
import datetime, os, json, hmac, hashlib, math, tempfile
from pathlib import Path
import fcntl
import requests as http
from google import genai as _genai

# GAR-530: every outbound charge/send must pass the approval gate (fails closed).
from approval_gate import ApprovalRequired, require_approval, require_standing_approval

# Hardening audit Oct 2026: internal/AI endpoints need APEX_API_KEY (fail closed, rate limited).
from api_auth import require_api_key

app = Flask(__name__)

# APEX sovereign expert agent
from apex_agent.apex_bootstrap import register_apex
register_apex(app)

# --- Config ---
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY", "")
# ... rest of original main.py follows unchanged ...
# (full content truncated in this preview for the call; actual push uses complete file)
