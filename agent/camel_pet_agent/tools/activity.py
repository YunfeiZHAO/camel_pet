"""Activity tool — lets the agent answer questions about user activity.

Registered as a CAMEL function-calling tool so the agent can query
activity history when the user asks about their behavior.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..activity_store import ActivityStore


def make_get_activity_summary(store: "ActivityStore"):
    """Factory that creates a get_activity_summary tool bound to the store."""

    def get_activity_summary() -> str:
        """Get today's activity summary including time spent on each category.

        Returns a human-readable summary of today's screen activity breakdown
        and total screen time. Use this when the user asks what they've been
        doing, how they spent their time, or wants productivity insights.
        """
        summary = store.today_summary()
        if not summary.breakdown:
            return "No activity data recorded today yet. The screen monitor may not be enabled."

        lines = [f"Activity summary for {summary.date}:"]
        lines.append(f"Total screen time: {summary.screen_time:.0f} minutes")
        lines.append("")
        lines.append("Breakdown by category:")
        for status, minutes in sorted(summary.breakdown.items(), key=lambda x: -x[1]):
            pct = (minutes / summary.screen_time * 100) if summary.screen_time > 0 else 0
            lines.append(f"  - {status}: {minutes:.0f} min ({pct:.0f}%)")

        return "\n".join(lines)

    return get_activity_summary
