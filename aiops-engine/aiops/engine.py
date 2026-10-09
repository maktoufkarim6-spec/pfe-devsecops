"""Boucle principale : qualité des données -> scoring -> décision -> cycle de vie du modèle."""
import csv
import json
import logging
import math
import os
import time
from collections import deque

import numpy as np

from . import metrics as M
from .config import Config
from .detector import HysteresisDetector
from .features import Target
from .mathlib import hours_to_saturation, psi
from .model import AnomalyModel, train_champion_challenger

log = logging.getLogger("aiops")


class _NodeLog(logging.LoggerAdapter):
    """Préfixe chaque ligne de journal par le nœud concerné : [app-1] ANOMALIE OUVERTE ..."""

    def process(self, msg, kwargs):
        return f"[{self.extra['node']}] {msg}", kwargs


class Engine:
    """Moteur de détection d'UN nœud. Plusieurs nœuds = plusieurs instances (voir __main__)."""

    def __init__(self, cfg: Config, prom, clock=time.time, target: Target = None):
        self.cfg, self.prom, self.clock = cfg, prom, clock
        self.target = target or Target()
        self.m = M.for_node(self.target.name)
        self.log = _NodeLog(log, {"node": self.target.name})
        self.model_path = self._path("model", "joblib")
        # Fenêtre de maintenance : fichier global (tous les nœuds) ou propre au nœud
        self.maintenance_paths = [os.path.join(cfg.data_dir, "maintenance")]
        if not self.target.legacy:
            self.maintenance_paths.append(os.path.join(cfg.data_dir, f"maintenance-{self.target.name}"))
        self._apply_tuning()
        self.model = None
        self.if_det = None
        self.z_det = HysteresisDetector(cfg.z_high, cfg.z_low, cfg.vote_k, cfg.vote_n, cfg.ewma_lambda)
        self.recent_scores = deque(maxlen=cfg.psi_window)
        self.recent_X = deque(maxlen=cfg.regime_window)
        self.relevance = deque(maxlen=cfg.vote_n)
        self.if_effective = False
        self.drift = False
        self.active = False
        self.event = None
        self.cycles = 0
        self._maintenance_expired_logged = False
        self.last_train_attempt = -math.inf
        self.last_forecast = -math.inf
        self._load_model()

    def _apply_tuning(self):
        """Applique les paramètres calibrés (aiops.calibrate --apply) s'ils existent."""
        path = os.path.join(self.cfg.data_dir, "tuning.json")
        self.m.TUNED.set(0)
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                t = json.load(f)
            for key in ("z_high", "z_low", "ewma_lambda", "vote_k", "vote_n"):
                if key in t:
                    setattr(self.cfg, key, type(getattr(self.cfg, key))(t[key]))
            self.z_det = HysteresisDetector(self.cfg.z_high, self.cfg.z_low,
                                            self.cfg.vote_k, self.cfg.vote_n, self.cfg.ewma_lambda)
            self.relevance = deque(maxlen=self.cfg.vote_n)
            self.m.TUNED.set(1)
            self.log.info("Paramètres calibrés appliqués : %s", t)
        except (OSError, ValueError, KeyError) as exc:
            self.m.ERRORS.labels(stage="tuning").inc()
            self.log.warning("tuning.json ignoré : %s", exc)

    def _path(self, stem, ext):
        """Fichiers propres au nœud ; noms historiques en mode mono-serveur."""
        name = f"{stem}.{ext}" if self.target.legacy else f"{stem}-{self.target.name}.{ext}"
        return os.path.join(self.cfg.data_dir, name)

    def maintenance_active(self, now):
        """Fenêtre de maintenance : fichier témoin (global ou du nœud) de moins de maintenance_max_minutes."""
        ages = []
        for path in self.maintenance_paths:
            try:
                ages.append(now - os.path.getmtime(path))
            except OSError:
                pass
        if not ages:
            self._maintenance_expired_logged = False
            return False
        age = min(ages)
        if age <= self.cfg.maintenance_max_minutes * 60:
            self._maintenance_expired_logged = False
            return True
        if not self._maintenance_expired_logged:
            self.log.info("Fenêtre de maintenance expirée (fichier vieux de %.0f min)", age / 60)
            self._maintenance_expired_logged = True
        return False

    # ------------------------------------------------------------ modèle
    def _load_model(self):
        if not os.path.exists(self.model_path):
            return
        try:
            model = AnomalyModel.load(self.model_path)
            if (model.feature_names != self.target.names
                    or list(model.min_effects) != list(self.target.min_effects)
                    or model.directions != self.target.directions):
                self.log.warning("Modèle sur disque incompatible avec la configuration, ignoré")
                return
            self._install(model)
            self.log.info("Modèle v%d rechargé depuis le disque (n=%d)", model.version, model.n_samples)
        except Exception as exc:
            self.m.ERRORS.labels(stage="load").inc()
            self.log.warning("Impossible de recharger le modèle : %s", exc)

    def _install(self, model):
        self.model = model
        if self.if_det is None:
            self.if_det = HysteresisDetector(model.threshold, model.low_threshold,
                                             self.cfg.vote_k, self.cfg.vote_n, self.cfg.ewma_lambda)
        else:
            self.if_det.set_thresholds(model.threshold, model.low_threshold)
            self.if_det.reset()
        self.cycles = 0  # chauffe après chaque (ré)installation de modèle
        self.recent_scores.clear()
        self.drift = model.if_suspended
        self.m.MODEL_READY.set(1)
        self.m.MODEL_VERSION.set(model.version)
        self.m.MODEL_TRAINED.set(model.trained_at)
        self.m.MODEL_SEASONAL.set(int(model.seasonal))
        self.m.TRAIN_SAMPLES.set(model.n_samples)
        self.m.IF_CN.set(model.cn)
        self.m.IF_HIGH.set(model.threshold)
        self.m.IF_LOW.set(model.low_threshold)
        self.m.IF_SUSPENDED.set(int(model.if_suspended))
        self.m.DRIFT.set(int(self.drift))

    @property
    def incident(self):
        return self.active  # état effectif : un détecteur interne bloqué ne doit pas geler l'apprentissage

    def should_train(self, now):
        if self.model is None:
            return now - self.last_train_attempt >= self.cfg.train_retry_seconds
        if self.incident:
            return False
        if self.model.if_suspended:  # après un changement de régime : réessayer souvent
            return now - self.last_train_attempt >= self.cfg.train_retry_seconds
        if now - self.last_train_attempt < self.cfg.min_retrain_gap_minutes * 60:
            return False
        return self.drift or now - self.model.trained_at >= self.cfg.retrain_hours * 3600

    def fetch_training_data(self, now):
        start = now - self.cfg.train_hours * 3600
        if self.model is not None:
            start = max(start, self.model.regime_start)
        series = [self.prom.range(f.expr, start, now, self.cfg.train_step) for f in self.target.features]
        if not all(series):
            return np.empty((0, len(self.target.names))), np.empty(0)
        common = sorted(set.intersection(*(set(s) for s in series)))
        X = np.array([[s[t] for s in series] for t in common], dtype=float)
        ts = np.array(common, dtype=float)
        lo = np.array([f.lo for f in self.target.features])
        hi = np.array([f.hi for f in self.target.features])
        ok = np.all(np.isfinite(X), axis=1) & np.all((X >= lo) & (X <= hi), axis=1)
        if self.target.app:
            ok &= X[:, self.target.pods] > 0  # instants sans collecte applicative
        return X[ok], ts[ok]

    def train(self, now):
        self.last_train_attempt = now
        X, ts = self.fetch_training_data(now)
        if len(X) < self.cfg.min_samples:
            self.log.warning("Données insuffisantes pour l'apprentissage (%d < %d)", len(X), self.cfg.min_samples)
            return False
        challenger, info = train_champion_challenger(
            X, ts, self.target.names, champion=self.model, seasonal_min_span_hours=self.cfg.seasonal_min_span_hours,
            fpr_tolerance=self.cfg.fpr_tolerance, z_high=self.cfg.z_high, percentile=self.cfg.if_percentile,
            min_effects=self.target.min_effects, directions=self.target.directions)
        self.log.info("Challenger : %s", info)
        if not info["accepted"]:
            self.m.CHALLENGER.labels(decision="rejected").inc()
            self.model.trained_at = now
            self.drift = False
            self.m.DRIFT.set(0)
            return False
        self.m.CHALLENGER.labels(decision="accepted").inc()
        challenger.save(self.model_path)
        self._install(challenger)
        self.log.info("Modèle v%d en service : n=%d, psi=%d, c(psi)=%.3f, seuils=%.4f/%.4f, saisonnier=%s",
                 challenger.version, challenger.n_samples, challenger.psi_, challenger.cn,
                 challenger.threshold, challenger.low_threshold, challenger.seasonal)
        for i, name in enumerate(self.target.names):
            self.log.info("  %-16s médiane=%10.3f  MAD=%8.3f  MAD_eff=%8.3f  effet_min=%g  sens=%s", name,
                     challenger.median[i], challenger.mad_raw[i], challenger.mad[i],
                     challenger.min_effects[i], challenger.directions[i])
        return True

    # ------------------------------------------------------------ données temps réel
    def collect(self):
        x = np.array([self.prom.instant(f.expr) for f in self.target.features], dtype=float)
        for f, value in zip(self.target.features, x):
            if not math.isfinite(value):
                return x, f"valeur manquante : {f.name}"
            if not f.lo <= value <= f.hi:
                return x, f"valeur implausible : {f.name}={value:.3f}"
        for source, expr in self.target.freshness.items():
            age = self.prom.instant(expr)
            if not math.isfinite(age) or age > self.cfg.max_staleness_seconds:
                return x, f"données périmées : {source} ({age:.0f} s)"
        return x, None

    # ------------------------------------------------------------ changement de régime
    def regime_check(self, now):
        """Un écart devient un nouveau régime s'il est DURABLE, STABLE et NON CRITIQUE."""
        if not self.active or now - self.event["start"] < self.cfg.regime_minutes * 60:
            return None
        if len(self.recent_X) < self.recent_X.maxlen:
            return "fenêtre incomplète"
        X = np.array(self.recent_X)
        spread = np.quantile(X, 0.95, axis=0) - np.quantile(X, 0.05, axis=0)
        effects = np.array(self.target.min_effects)
        unstable = [n for n, sp, e in zip(self.target.names, spread, effects) if e > 0 and sp > e]
        if unstable:
            return f"pas un plateau : {', '.join(unstable)}"
        critical = [n for n, v, c in zip(self.target.names, X[-1], self.target.critical) if v >= c]
        if critical:
            return f"niveau critique : {', '.join(critical)}"
        if self.target.app and X[-1, self.target.pods] < self.model.median[self.target.pods]:
            return "conteneurs perdus"
        start = self.event["start"]
        self.model.rebaseline(X, regime_start=start)
        self.model.save(self.model_path)
        self._write_event(now, kind="changement_de_regime")
        self.log.warning("CHANGEMENT DE RÉGIME accepté après %.0f min (%s) : nouvelle référence, IF suspendu "
                    "jusqu'au ré-entraînement", (now - start) / 60, self.event["feature"])
        self.m.REGIMES.inc()
        self.m.IF_SUSPENDED.set(1)
        self.z_det.reset()
        self.if_det.reset()
        self.relevance.clear()
        self.active = False
        self.if_effective = False
        self.event = None
        self.drift = True
        self.m.DRIFT.set(1)
        return "accepté"

    # ------------------------------------------------------------ évaluation
    def score(self, now):
        x, problem = self.collect()
        if problem:
            self.m.QUALITY.set(0)
            self.log.warning("Abstention (%s)", problem)
            return None
        self.m.QUALITY.set(1)
        self.recent_X.append(x)
        self.cycles += 1
        warmup = self.cycles <= self.cfg.warmup_cycles
        maintenance = self.maintenance_active(now)
        self.m.WARMUP.set(int(warmup))
        self.m.MAINTENANCE.set(int(maintenance))

        m = self.model
        s = float(m.score(x, now)[0])
        z = m.zscores(x)
        zdir = m.directional_z(x)[0]
        z_max = float(np.max(np.abs(zdir)))
        top_name, top_z = m.explain(x)
        effect = float(m.combined_effect(x)[0])

        if_state = self.if_det.update(s)
        z_state = self.z_det.update(z_max)
        self.relevance.append(effect >= m.relevance_norm)
        relevant = sum(self.relevance) >= self.cfg.vote_k      # même vote k-sur-n que les détecteurs
        if_effective = if_state.active and relevant and not m.if_suspended

        was_active = self.active
        wants_active = if_effective or z_state.active
        if wants_active and not was_active and (warmup or maintenance):
            reason = "démarrage" if warmup else "maintenance"
            self.log.info("Ouverture d'alerte supprimée (%s) : %s (z=%+.2f)", reason, top_name, top_z)
            wants_active = False
            if_effective = False
        self.active = wants_active
        if if_effective and not self.if_effective:
            self.m.EVENTS.labels(detector="iforest").inc()
        if z_state.opened:
            self.m.EVENTS.labels(detector="zscore").inc()
        self.if_effective = if_effective

        if self.active and not was_active:
            self.event = {"start": now, "feature": top_name, "z": top_z,
                          "detectors": [d for d, on in (("iforest", if_effective), ("zscore", z_state.active)) if on]}
            self.log.warning("ANOMALIE OUVERTE : %s (z=%+.2f), détecteurs=%s", top_name, top_z, self.event["detectors"])
        elif was_active and not self.active:
            self._write_event(now)
            self.log.warning("ANOMALIE FERMÉE après %.0f s", now - self.event["start"])
            self.event = None

        regime = self.regime_check(now)

        self.recent_scores.append(s)
        if len(self.recent_scores) == self.recent_scores.maxlen and not m.if_suspended:
            value = psi(m.train_scores, np.array(self.recent_scores))
            self.m.PSI.set(value)
            self.drift = value > self.cfg.psi_threshold
            self.m.DRIFT.set(int(self.drift))

        for i, name in enumerate(self.target.names):
            self.m.VALUE.labels(feature=name).set(x[i])
            self.m.Z.labels(feature=name).set(z[i])
            self.m.TOP.labels(feature=name).set(int(name == top_name))
        self.m.TOP_Z.set(top_z)
        self.m.IF_SCORE.set(s)
        self.m.IF_SMOOTH.set(if_state.smoothed)
        self.m.IF_ANOM.set(int(self.if_effective))
        self.m.IF_INTERNAL.set(int(if_state.active))
        self.m.RELEVANT.set(int(relevant))
        self.m.EFFECT.set(effect)
        self.m.Z_MAX.set(z_max)
        self.m.Z_SMOOTH.set(z_state.smoothed)
        self.m.Z_ANOM.set(int(self.z_det.active))
        self.m.ACTIVE.set(int(self.active))
        self.m.LAST_SCORE.set(now)

        self._write_csv(self._path("detections", "csv"),
                        ["epoch", "iforest_score", "iforest_smoothed", "iforest_active", "zscore_max",
                         "zscore_smoothed", "zscore_active", "anomaly_active", "top_feature", "top_z"] + self.target.names,
                        [f"{now:.0f}", f"{s:.5f}", f"{if_state.smoothed:.5f}", int(self.if_effective),
                         f"{z_max:.4f}", f"{z_state.smoothed:.4f}", int(self.z_det.active), int(self.active),
                         top_name, f"{top_z:.3f}"] + [f"{v:.4f}" for v in x])
        return {"x": x, "s": s, "z_max": z_max, "active": self.active, "top": top_name, "effect": effect,
                "regime": regime}

    # ------------------------------------------------------------ prévisions
    def forecast(self, now):
        self.last_forecast = now
        for resource, feature in (("disk", "disk_host"), ("ram", "ram_host")):
            expr = next(f.expr for f in self.target.features if f.name == feature)
            data = self.prom.range(expr, now - self.cfg.forecast_hours * 3600, now, 60)
            if len(data) < 30:
                continue
            keys = sorted(data)
            t = (np.array(keys, dtype=float) - keys[0]) / 3600.0
            y = np.array([data[k] for k in keys], dtype=float)
            hours, beta, r2, reliable = hours_to_saturation(t, y, 100.0, self.cfg.forecast_min_r2)
            self.m.F_HOURS.labels(resource=resource).set(hours)
            self.m.F_SLOPE.labels(resource=resource).set(beta)
            self.m.F_R2.labels(resource=resource).set(r2)
            self.m.F_RELIABLE.labels(resource=resource).set(int(reliable))

    # ------------------------------------------------------------ journaux
    def _write_event(self, now, kind="anomalie"):
        e = self.event
        detectors = "+".join(e["detectors"]) if kind == "anomalie" else kind
        self._write_csv(self._path("events", "csv"), ["start_epoch", "end_epoch", "duration_s", "top_feature", "top_z", "detectors"],
                        [f"{e['start']:.0f}", f"{now:.0f}", f"{now - e['start']:.0f}", e["feature"],
                         f"{e['z']:.2f}", detectors])

    def _write_csv(self, path, header, row):
        filename = os.path.basename(path)
        try:
            if os.path.exists(path) and os.path.getsize(path) > self.cfg.csv_max_bytes:
                os.replace(path, path + ".1")
            new = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(header)
                w.writerow(row)
        except OSError as exc:
            self.m.ERRORS.labels(stage="csv").inc()
            self.log.warning("Écriture %s impossible : %s", filename, exc)

    # ------------------------------------------------------------ cycle
    def run_once(self):
        start = self.clock()
        result = None
        for stage, action in (("train", self._maybe_train), ("score", self._maybe_score),
                              ("forecast", self._maybe_forecast)):
            try:
                out = action(start)
                if stage == "score":
                    result = out
            except Exception as exc:
                self.m.ERRORS.labels(stage=stage).inc()
                self.log.exception("Erreur (%s) : %s", stage, exc)
        self.m.CYCLE.set(self.clock() - start)
        return result

    def _maybe_train(self, now):
        if self.should_train(now):
            self.train(now)

    def _maybe_score(self, now):
        return self.score(now) if self.model is not None else None

    def _maybe_forecast(self, now):
        if now - self.last_forecast >= 60:
            self.forecast(now)
