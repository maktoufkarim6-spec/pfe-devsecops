# aiops-engine

Moteur de détection d'anomalies du projet (z-score robuste + Isolation Forest, prévisions Theil-Sen),
lisant les métriques dans Prometheus et publiant ses résultats au format Prometheus.

## Multi-nœuds (v3)

Un modèle par nœud. Chaque nœud est identifié par l'étiquette Prometheus `node="<nom>"` posée sur
tous ses collecteurs. Liste des nœuds : variable `AIOPS_TARGETS`, par exemple

```
AIOPS_TARGETS=[{"node": "app-1", "app": true}, {"node": "monitoring", "app": false}]
```

- `app: true` : le nœud héberge l'application k3s (8 variables), sinon 4 variables système.
- Fichiers par nœud dans `/data` : `model-<nœud>.joblib`, `detections-<nœud>.csv`, `events-<nœud>.csv`.
- Fenêtre de maintenance : `/data/maintenance` (tous les nœuds) ou `/data/maintenance-<nœud>`.
- Toutes les métriques `aiops_*` portent l'étiquette `node`.
- Sans `AIOPS_TARGETS` : mode mono-serveur historique (requêtes et fichiers identiques à la v2.4).

## Tests

`python -m pytest tests` : 53 tests (mathématiques, détecteur, modèle, backtest, régimes,
opérations, multi-nœuds). Ils s'exécutent pendant `docker build` : un échec empêche l'image d'exister.
