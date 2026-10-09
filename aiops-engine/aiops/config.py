"""Configuration par variables d'environnement (valeurs par défaut documentées)."""
import os
from dataclasses import dataclass, fields


@dataclass
class Config:
    prom_url: str = "http://prometheus:9090"
    listen_port: int = 8000
    data_dir: str = "/data"
    score_interval: int = 15            # s entre deux évaluations
    train_hours: float = 72.0           # fenêtre d'historique pour l'apprentissage
    train_step: int = 30                # s entre deux échantillons d'apprentissage
    min_samples: int = 240              # 240 x 30 s = 2 h minimum
    retrain_hours: float = 6.0          # ré-entraînement périodique
    min_retrain_gap_minutes: float = 60 # délai minimal entre deux entraînements
    train_retry_seconds: int = 120      # nouvelle tentative si données insuffisantes
    seasonal_min_span_hours: float = 36 # saisonnalité activée au-delà
    if_percentile: float = 99.5         # seuil Isolation Forest (quantile d'apprentissage)
    z_high: float = 3.5                 # Iglewicz & Hoaglin
    z_low: float = 2.5                  # hystérésis
    vote_k: int = 3
    vote_n: int = 4
    ewma_lambda: float = 0.4
    fpr_tolerance: float = 0.005        # champion / challenger
    psi_window: int = 240               # 240 cycles = 1 h de scores récents
    psi_threshold: float = 0.25
    max_staleness_seconds: float = 120  # au-delà, les données sont jugées périmées
    forecast_hours: float = 6.0
    forecast_min_r2: float = 0.7
    csv_max_bytes: int = 50_000_000
    warmup_cycles: int = 20             # cycles sans ouverture d'alerte après un démarrage
    maintenance_max_minutes: float = 30 # durée de validité du fichier /data/maintenance
    regime_minutes: float = 30.0        # durée d'un écart avant d'envisager un changement de régime
    regime_window: int = 120            # cycles (30 min) servant à juger le plateau
    targets: str = ""                   # AIOPS_TARGETS : nœuds supervisés (JSON), vide = un seul serveur

    @classmethod
    def from_env(cls):
        values = {}
        for f in fields(cls):
            raw = os.getenv("AIOPS_TARGETS" if f.name == "targets" else f.name.upper())
            if raw is not None:
                values[f.name] = type(f.default)(raw)
        return cls(**values)
