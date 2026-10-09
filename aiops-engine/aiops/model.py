"""Modèle d'anomalie : z-score robuste directionnel + Isolation Forest, persistance et champion/challenger."""
import os
import tempfile
import time

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from .mathlib import MAD_CONSTANT, c_n, cyclic_hour, robust_center_scale

SCHEMA_VERSION = 5
_SIGN = {"up": 1, "down": -1, "both": 0}


class AnomalyModel:
    def __init__(self, feature_names, seasonal=False, min_effects=None, directions=None, z_high=3.5,
                 n_estimators=200, max_samples=256, percentile=99.5, hysteresis_ratio=0.5,
                 relevance_norm=1.0, augment_ratio=0.5, augment_copies=4, random_state=42):
        k = len(feature_names)
        self.feature_names = list(feature_names)
        self.min_effects = np.zeros(k) if min_effects is None else np.asarray(min_effects, float)
        self.directions = ["both"] * k if directions is None else list(directions)
        self.sign = np.array([_SIGN[d] for d in self.directions])
        self.seasonal = bool(seasonal)
        self.z_high = z_high
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.percentile = percentile
        self.hysteresis_ratio = hysteresis_ratio
        self.relevance_norm = relevance_norm
        self.augment_ratio = augment_ratio
        self.augment_copies = augment_copies
        self.random_state = random_state
        self.schema_version = SCHEMA_VERSION
        self.version = 0
        self.trained_at = 0.0
        self.n_samples = 0
        self.span_hours = 0.0
        self.regime_start = 0.0      # l'apprentissage ignore les données antérieures
        self.if_suspended = False    # IF désactivé après un changement de régime, jusqu'au ré-entraînement

    # ----- entrée de l'Isolation Forest -----
    def _if_transform(self, X):
        """Même garde-fou que le z-score : une baisse sans danger est ramenée à la médiane d'apprentissage."""
        dev = np.atleast_2d(np.asarray(X, dtype=float)) - self.if_median
        dev = np.where(self.sign > 0, np.maximum(dev, 0), dev)
        dev = np.where(self.sign < 0, np.minimum(dev, 0), dev)
        return self.if_median + dev

    def _design(self, X, ts):
        X = self._if_transform(X)
        if not self.seasonal:
            return X
        s, c = cyclic_hour(np.atleast_1d(ts))
        return np.column_stack([X, s, c])

    # ----- apprentissage -----
    def fit(self, X, ts):
        X = np.asarray(X, dtype=float)
        ts = np.asarray(ts, dtype=float)
        self.median, self.mad_raw = robust_center_scale(X)
        # Plancher de significativité : z > z_high  =>  |x - médiane| > min_effect
        self.mad = np.maximum(self.mad_raw, MAD_CONSTANT * self.min_effects / self.z_high)
        self.if_median = self.median.copy()
        # Augmentation par bruit d'effet minimal : l'IF apprend qu'un écart inférieur à
        # augment_ratio x effet minimal est NORMAL (sinon il isole des dérives insignifiantes).
        rng = np.random.default_rng(self.random_state)
        base = self._if_transform(X)
        noise = [rng.uniform(-self.augment_ratio, self.augment_ratio, base.shape) * self.min_effects
                 for _ in range(self.augment_copies)]
        A = np.vstack([base + e for e in noise]) if self.augment_copies else base
        A_ts = np.tile(ts, self.augment_copies) if self.augment_copies else ts
        self.psi_ = min(self.max_samples, len(A))
        self.forest = IsolationForest(n_estimators=self.n_estimators, max_samples=self.psi_,
                                      contamination="auto", random_state=self.random_state)
        design_A = np.column_stack([A, *cyclic_hour(A_ts)]) if self.seasonal else A
        self.forest.fit(design_A)
        aug_scores = -self.forest.score_samples(design_A)
        self.threshold = float(np.percentile(aug_scores, self.percentile))   # seuil sur le nuage toléré
        center = float(np.median(aug_scores))
        self.low_threshold = center + self.hysteresis_ratio * (self.threshold - center)
        scores = self.score(X, ts)                                            # référence PSI : points réels
        self.train_scores = scores if len(scores) <= 5000 else rng.choice(scores, 5000, replace=False)
        self.trained_at = time.time()
        self.n_samples = len(X)
        self.span_hours = float((ts.max() - ts.min()) / 3600.0) if len(ts) else 0.0
        return self

    @property
    def cn(self):
        return c_n(self.psi_)

    # ----- écarts -----
    def deviation(self, x):
        """Écart signé, annulé dans le sens sans danger (ex. disque qui se vide)."""
        dev = np.atleast_2d(np.asarray(x, dtype=float)) - self.median
        dev = np.where(self.sign > 0, np.maximum(dev, 0), dev)
        return np.where(self.sign < 0, np.minimum(dev, 0), dev)

    def zscores(self, x):
        """Z-score robuste signé brut (pour l'affichage)."""
        return MAD_CONSTANT * (np.asarray(x, dtype=float) - self.median) / self.mad

    def directional_z(self, x):
        """Z-score robuste limité au sens dangereux (pour la détection)."""
        return MAD_CONSTANT * self.deviation(x) / self.mad

    def combined_effect(self, x):
        """E = sqrt( sum_i (écart_directionnel_i / effet_min_i)^2 ) ; E = 1 : un effet minimal complet."""
        scale = np.where(self.min_effects > 0, self.min_effects, np.inf)
        return np.sqrt(np.sum((self.deviation(x) / scale) ** 2, axis=1))

    def is_relevant(self, x):
        return self.combined_effect(x) >= self.relevance_norm

    def explain(self, x):
        z = self.directional_z(x)[0]
        i = int(np.argmax(np.abs(z)))
        return self.feature_names[i], float(z[i])

    # ----- inférence -----
    def score(self, X, ts):
        """s(x, n) de Liu et al. : proche de 1 = anomalie, < 0.5 = normal."""
        X = np.atleast_2d(X)
        ts = np.broadcast_to(np.atleast_1d(np.asarray(ts, dtype=float)), (len(X),))
        return -self.forest.score_samples(self._design(X, ts))

    def flags(self, X, ts):
        """Points anormaux selon le modèle (IF pertinent OU z-score directionnel)."""
        z_flag = np.max(np.abs(self.directional_z(X)), axis=1) > self.z_high
        if self.if_suspended:
            return z_flag
        return z_flag | ((self.score(X, ts) > self.threshold) & self.is_relevant(X))

    def false_positive_rate(self, X, ts):
        return float(np.mean(self.flags(X, ts))) if len(X) else 0.0

    # ----- changement de régime -----
    def rebaseline(self, X_recent, regime_start):
        """Nouvelle référence de niveau (médiane du plateau). L'IF, appris sur l'ancien régime,
        est suspendu jusqu'au prochain ré-entraînement."""
        self.median = np.median(np.asarray(X_recent, dtype=float), axis=0)
        self.regime_start = float(regime_start)
        self.if_suspended = True

    # ----- persistance (écriture atomique) -----
    def save(self, path):
        directory = os.path.dirname(path) or "."
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        os.close(fd)
        try:
            joblib.dump(self, tmp)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    @staticmethod
    def load(path):
        model = joblib.load(path)
        if getattr(model, "schema_version", None) != SCHEMA_VERSION:
            raise ValueError("version de schéma incompatible")
        return model


def train_champion_challenger(X, ts, feature_names, champion=None, seasonal_min_span_hours=36.0,
                             holdout_ratio=0.2, fpr_tolerance=0.005, **model_kwargs):
    """Entraîne un challenger et décide s'il remplace le champion.

    - Les points jugés anormaux par le champion sont retirés de l'apprentissage.
    - La saisonnalité n'est activée que si l'historique couvre assez d'heures.
    - Validation temporelle : apprentissage sur les 80 % anciens, test sur les 20 % récents.
    - Critère : FPR(challenger) <= FPR(champion) + tolérance.
    - Un champion obsolète (changement de régime) est remplacé sans comparaison.
    """
    X = np.asarray(X, dtype=float)
    ts = np.asarray(ts, dtype=float)
    order = np.argsort(ts)
    X, ts = X[order], ts[order]

    compatible = champion is not None and champion.feature_names == list(feature_names)
    obsolete = compatible and champion.if_suspended
    removed = 0
    if compatible:
        keep = ~champion.flags(X, ts)
        if keep.sum() >= 0.5 * len(X):
            removed = int((~keep).sum())
            X, ts = X[keep], ts[keep]

    span = float((ts[-1] - ts[0]) / 3600.0) if len(ts) > 1 else 0.0
    seasonal = span >= seasonal_min_span_hours
    cut = int(len(X) * (1 - holdout_ratio))
    challenger = AnomalyModel(feature_names, seasonal=seasonal, **model_kwargs).fit(X[:cut], ts[:cut])
    challenger_fpr = challenger.false_positive_rate(X[cut:], ts[cut:])
    champion_fpr = champion.false_positive_rate(X[cut:], ts[cut:]) if compatible else None

    accepted = (not compatible) or obsolete or challenger_fpr <= champion_fpr + fpr_tolerance
    challenger.version = (champion.version + 1) if compatible else 1
    if compatible:
        challenger.regime_start = champion.regime_start
    info = {
        "accepted": accepted, "obsolete_champion": obsolete, "seasonal": seasonal, "span_hours": span,
        "removed_as_anomalies": removed, "n_train": cut, "n_holdout": len(X) - cut,
        "challenger_fpr": challenger_fpr, "champion_fpr": champion_fpr,
    }
    return challenger, info
