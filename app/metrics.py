"""Compteurs Prometheus métier (exposés sur /metrics avec les métriques HTTP)."""
from prometheus_client import Counter

SNAPSHOTS_COLLECTED = Counter(
    "watcher_snapshots_collected_total", "Relevés de prix stockés", ["source"]
)
ANOMALIES_DETECTED = Counter(
    "watcher_anomalies_detected_total", "Anomalies candidates détectées (étage 1)", ["type"]
)
ALERTS_EMAIL = Counter(
    "watcher_alerts_email_total", "Alertes traitées par statut email", ["status"]
)
COLLECTION_RUNS = Counter(
    "watcher_collection_runs_total", "Passes de collecte Travelpayouts", ["status"]
)
