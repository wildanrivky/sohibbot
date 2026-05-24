"""Cadence routines (R17 M3): daily standup + weekly review.

Codifies the autonomous-GM rhythm (blueprint 14.1). Each routine returns
a string digest the channels layer can deliver; routines never act on
their own — anything actionable goes through the initiative/decision path.
"""
from __future__ import annotations

from el_solver.routines.cadence import daily_standup, weekly_review

__all__ = ["daily_standup", "weekly_review"]
