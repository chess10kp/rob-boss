"""Step machine: advance / retry / stuck-after-3-tries, held in a plain dict.

The dict is the whole state, so Backboard (or a JSON file) can persist it later by
swapping `to_dict` / `from_dict` without touching the logic.
"""
from __future__ import annotations

from track2.schema import Step, Verdict

MAX_TRIES = 3


class StepMachine:
    def __init__(self, steps: list[Step], max_tries: int = MAX_TRIES, state: dict | None = None):
        self.steps = steps
        self.max_tries = max_tries
        # status: active | stuck | complete
        self.state = state or {"current": 0, "tries": 0, "status": "active", "history": []}

    @classmethod
    def from_dict(cls, steps: list[Step], state: dict, max_tries: int = MAX_TRIES) -> "StepMachine":
        return cls(steps, max_tries, state)

    def to_dict(self) -> dict:
        return self.state

    @property
    def status(self) -> str:
        return self.state["status"]

    @property
    def current(self) -> Step | None:
        return None if self.status == "complete" else self.steps[self.state["current"]]

    def submit(self, verdict: Verdict) -> str:
        """Apply a critique verdict. Returns the new status: advanced | retry | stuck | complete."""
        if self.status != "active":
            raise RuntimeError(f"cannot submit while status is {self.status!r}")
        step = self.current
        self.state["history"].append(
            {"step": step.index, "verdict": verdict.verdict, "category": verdict.category,
             "adjustment": verdict.adjustment}
        )
        if verdict.verdict == "READY":
            return self._advance("advanced")
        self.state["tries"] += 1
        if self.state["tries"] >= self.max_tries:
            self.state["status"] = "stuck"
            return "stuck"
        return "retry"

    def skip(self) -> str:
        """Painter (or demo operator) moves on from a stuck step."""
        if self.status != "stuck":
            raise RuntimeError("skip is only valid when stuck")
        self.state["status"] = "active"
        return self._advance("advanced")

    def _advance(self, label: str) -> str:
        self.state["tries"] = 0
        if self.state["current"] + 1 >= len(self.steps):
            self.state["status"] = "complete"
            return "complete"
        self.state["current"] += 1
        return label
