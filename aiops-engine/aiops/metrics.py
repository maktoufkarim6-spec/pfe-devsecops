"""Métriques exposées au format Prometheus.

Chaque métrique porte l'étiquette node="<nœud supervisé>" : un moteur, plusieurs nœuds.
Le moteur utilise for_node(nom), qui pose cette étiquette automatiquement.
"""
from types import SimpleNamespace

from prometheus_client import Counter, Gauge

N = ["node"]
L = ["node", "feature"]
R = ["node", "resource"]

MODEL_READY = Gauge("aiops_model_ready", "1 si un modèle est chargé", N)
MODEL_VERSION = Gauge("aiops_model_version", "Version du modèle champion", N)
MODEL_TRAINED = Gauge("aiops_model_trained_timestamp", "Horodatage d'entraînement du champion", N)
MODEL_SEASONAL = Gauge("aiops_model_seasonal", "1 si la saisonnalité horaire est utilisée", N)
TRAIN_SAMPLES = Gauge("aiops_training_samples", "Échantillons d'apprentissage du champion", N)
IF_CN = Gauge("aiops_iforest_cn", "Facteur de normalisation c(psi)", N)
CHALLENGER = Counter("aiops_challenger_decisions", "Décisions champion/challenger", ["node", "decision"])

IF_SCORE = Gauge("aiops_iforest_score", "Score Isolation Forest brut s(x,n)", N)
IF_SMOOTH = Gauge("aiops_iforest_score_smoothed", "Score Isolation Forest lissé (EWMA)", N)
IF_HIGH = Gauge("aiops_iforest_threshold_high", "Seuil haut (ouverture)", N)
IF_LOW = Gauge("aiops_iforest_threshold_low", "Seuil bas (fermeture)", N)
IF_ANOM = Gauge("aiops_iforest_anomaly", "1 si le détecteur Isolation Forest est ouvert et pertinent", N)
WARMUP = Gauge("aiops_warmup", "1 pendant la période de chauffe (pas d'ouverture d'alerte)", N)
MAINTENANCE = Gauge("aiops_maintenance", "1 si une fenêtre de maintenance est active", N)
TUNED = Gauge("aiops_tuned", "1 si des paramètres calibrés (tuning.json) sont appliqués", N)
IF_SUSPENDED = Gauge("aiops_iforest_suspended", "1 si l'IF est suspendu après un changement de régime", N)
REGIMES = Counter("aiops_regime_changes", "Changements de régime acceptés", N)
IF_INTERNAL = Gauge("aiops_iforest_detector_internal", "1 si le détecteur IF interne est ouvert (avant porte)", N)
EFFECT = Gauge("aiops_combined_effect", "Effet combiné E (1 = un effet minimal complet)", N)
RELEVANT = Gauge("aiops_relevance_gate", "1 si un écart significatif existe (porte de pertinence)", N)

Z = Gauge("aiops_zscore", "Z-score robuste par variable", L)
Z_MAX = Gauge("aiops_zscore_max", "max |z| brut", N)
Z_SMOOTH = Gauge("aiops_zscore_max_smoothed", "max |z| lissé (EWMA)", N)
Z_ANOM = Gauge("aiops_zscore_anomaly", "1 si le détecteur z-score est ouvert", N)

ACTIVE = Gauge("aiops_anomaly_active", "1 si une anomalie est en cours (IF ou z-score)", N)
TOP = Gauge("aiops_top_feature", "1 pour la variable la plus déviante", L)
TOP_Z = Gauge("aiops_top_feature_zscore", "Z-score de la variable la plus déviante", N)
EVENTS = Counter("aiops_anomaly_events", "Anomalies ouvertes", ["node", "detector"])

VALUE = Gauge("aiops_feature_value", "Valeur courante", L)
QUALITY = Gauge("aiops_data_quality", "1 si les données sont complètes, fraîches et plausibles", N)
PSI = Gauge("aiops_psi", "Population Stability Index des scores récents", N)
DRIFT = Gauge("aiops_drift_detected", "1 si PSI > seuil", N)

F_HOURS = Gauge("aiops_forecast_hours_to_saturation", "Heures avant saturation (Theil-Sen)", R)
F_SLOPE = Gauge("aiops_forecast_slope_per_hour", "Pente en points de % par heure", R)
F_R2 = Gauge("aiops_forecast_r2", "R² de la tendance", R)
F_RELIABLE = Gauge("aiops_forecast_reliable", "1 si R² >= seuil et ressource croissante", R)

CYCLE = Gauge("aiops_cycle_duration_seconds", "Durée du dernier cycle", N)
LAST_SCORE = Gauge("aiops_last_score_timestamp", "Horodatage de la dernière évaluation réussie", N)
ERRORS = Counter("aiops_errors", "Erreurs pendant les cycles", ["node", "stage"])


class _Bound:
    """Une métrique dont l'étiquette node est déjà posée."""

    def __init__(self, metric, node):
        self._metric, self._node = metric, node

    def labels(self, **kw):
        return self._metric.labels(node=self._node, **kw)

    def set(self, value):
        self._metric.labels(node=self._node).set(value)

    def inc(self, amount=1):
        self._metric.labels(node=self._node).inc(amount)


def for_node(node):
    """Toutes les métriques du module, liées au nœud donné (m.ACTIVE.set(1), m.Z.labels(feature=...))."""
    metrics = {k: v for k, v in globals().items() if isinstance(v, (Gauge, Counter))}
    return SimpleNamespace(**{k: _Bound(v, node) for k, v in metrics.items()})
