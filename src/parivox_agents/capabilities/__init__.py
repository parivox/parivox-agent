"""The built-in Parivox behaviours a self-hosted agent can opt into.

Quiet mode and handoff live here. Translation needs no agent code: Parivox
runs it as a separate participant, which ``ParivoxRoom`` already ignores.
"""

from .handoff import Handoff
from .quiet_mode import QuietMode, is_quiet_request, match_wake_phrase

__all__ = ["Handoff", "QuietMode", "is_quiet_request", "match_wake_phrase"]
