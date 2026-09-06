"""
Shared protocol and result type for all EdgeDash agents.

Every agent — real or mock — must satisfy the Agent protocol so the
Orchestrator can treat them uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from edgedash.config import Config


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    agent: str
    status: str          # "ok" | "failed"
    records_touched: int
    notes: str = ""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Agent(Protocol):
    """Anything that has a name and a run() method is a valid agent."""

    name: str

    def run(
        self,
        config: Config,
        db_path: str,
        stop_conditions: dict[str, Any] | None = None,
    ) -> AgentResult:
        """
        Execute the agent's goal.

        Args:
            config:          project configuration.
            db_path:         path to the database.
            stop_conditions: limits set by the Orchestrator, e.g.
                             {"max_items": 25, "max_seconds": 300}.
                             Agents must respect these limits.
                             Defaults to {} when called without the argument.
        """
        ...
