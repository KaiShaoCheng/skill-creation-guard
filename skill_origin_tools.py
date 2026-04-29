"""Utility helpers for retrieving Hermes hub metadata that explains skill origins."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def read_lock_entry_for_skill(skill_name: str) -> Optional[Dict[str, Any]]:
    """Return a Hermes hub lock entry for *skill_name*, or None when unavailable."""
    try:
        from tools.skills_hub import HubLockFile
        lock = HubLockFile()
        return lock.get_installed(skill_name)
    except Exception:
        logger.debug("Unable to read Hermes hub lock for skill origin audit", exc_info=True)
        return None
