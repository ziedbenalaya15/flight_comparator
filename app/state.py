"""État en mémoire des jobs (suffisant pour l'affichage dashboard en Phase 1)."""
from datetime import datetime


class JobState:
    def __init__(self) -> None:
        self.running: bool = False
        self.last_run_at: datetime | None = None
        self.last_status: str | None = None  # ok | error
        self.last_detail: str | None = None
        self.last_snapshot_count: int = 0
        self.last_trigger: str | None = None  # scheduled | manual


collection_state = JobState()
