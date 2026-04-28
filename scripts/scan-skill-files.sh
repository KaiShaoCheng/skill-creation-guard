#!/usr/bin/env bash
set -euo pipefail
PLUGIN_DIR="${PLUGIN_DIR:-$HOME/.hermes/profiles/kaishao-admin/plugins/skill-creation-guard}"
cd "$PLUGIN_DIR"
exec /root/.hermes/hermes-agent/venv/bin/python skill_file_audit.py
