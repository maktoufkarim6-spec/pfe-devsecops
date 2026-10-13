# Résultats de la campagne d'injection de pannes

Campagne du 13/10/2026 de 01:20 à 07:47 UTC (6.4 h),
14 injections réelles sur les VPS (script `eval/campagne.py`, données brutes `injections.jsonl`).
Le moteur AIOps n'était **pas encore calibré** (modèle appris sur quelques heures d'historique) : ces chiffres sont
une base de départ, à refaire après la calibration (`aiops.calibrate`).

## Détection

| Détecteur | Rappel (pannes détectées) | Délai médian | Délai min–max |
|---|---|---|---|
| Moteur AIOps (z-score ou Isolation Forest) | 91 % (10/11) | 62 s | 50–75 s |
| z-score robuste seul | 91 % (10/11) | 62 s | 50–75 s |
| Isolation Forest seul | 0 % (0/11) | — | — |
| Alerte AnomalieAIOps (après 1 min) | 91 % (10/11) | 132 s | 120–145 s |
| Seuils fixes (CPU > 80 %, RAM > 85 %, disque > 85 %) | 0 % (0/11) | — | — |
| Alertes de disponibilité (collecteur, nœud, moteur muets) | 100 % (3/3) | 145 s | 135–155 s |

- **Précision du moteur AIOps** (épisodes d'anomalie liés à une panne réelle) : 92 % (12/13).
  Faux positifs : 1 en 6.4 h, soit 0.16 par heure — app-1 03:56–03:58.
- **Diagnostic** : la variable désignée par l'IA est la bonne dans 100 % (10/10) des détections.

## Agent de remédiation

- Décisions correctes (action choisie = action attendue) : 100 % (14/14).
- Pannes réparables par l'agent effectivement réparées par lui : 89 % (8/9).
- Délai de rétablissement (début de la panne → panne disparue) : médiane 161 s.
- Réaction de l'agent (alerte déclenchée → panne disparue) : médiane 18 s.

## Détail des injections

| # | Panne | Heure (UTC) | Détections (délai) | Variable IA | Action attendue | Décisions de l'agent | Correct | Réparée en | Vérification 5 min après |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Arrêt du collecteur cAdvisor | 01:21 | alerte 135 s | — | restart-container cadvisor | restart-container cadvisor (executee) | oui | 143 s | — |
| 2 | SonarQube démarré hors build (+2 Go de RAM) | 01:32 | ia 60 s / zscore 60 s / alerte_ia 130 s | cpu_host | stop-sonarqube | stop-sonarqube (executee), aucune (aucune_action) | oui | 137 s | persistant |
| 3 | Boucle CPU dans le pod de l'API | 02:04 | ia 75 s / zscore 75 s / alerte_ia 145 s | cpu_server | rollout-restart server | rollout-restart server (executee), aucune (aucune_action) | oui | 164 s | persistant |
| 4 | Fichier de 6 Go sur le disque | 03:06 | ia 65 s / zscore 65 s / alerte_ia 135 s | disk_host | aucune | aucune (aucune_action) | oui | — | — |
| 5 | Surcharge CPU du serveur (3 cœurs sur 4) | 04:09 | ia 75 s / zscore 75 s / alerte_ia 145 s | cpu_host | stop-sonarqube | stop-sonarqube (executee) | oui | — | — |
| 6 | Fuite mémoire dans le pod de l'API (+150 Mo) | 04:56 | ia 55 s / zscore 55 s / alerte_ia 125 s | ram_server_mb | rollout-restart server | rollout-restart server (executee), aucune (aucune_action), aucune (aucune_action) | oui | 160 s | persistant |
| 7 | Arrêt de node-exporter (nœud muet) | 05:14 | alerte 145 s / ia 220 s / zscore 220 s | — | restart-container node-exporter | restart-container node-exporter (executee) | oui | 162 s | — |
| 8 | Pression mémoire du serveur (+2 Go) | 05:25 | ia 50 s / zscore 50 s / alerte_ia 120 s | ram_host | stop-sonarqube | stop-sonarqube (pause) | oui | — | — |
| 9 | Pod client supprimé (mise à 0, Argo CD doit le remettre) | 05:45 |  | — | aucune | aucune décision | oui | 32 s | — |
| 10 | Boucle CPU dans le pod de l'API | 05:54 | ia 70 s / zscore 70 s / alerte_ia 140 s | cpu_server | rollout-restart server | rollout-restart server (quota) | oui | — | — |
| 11 | SonarQube démarré hors build (+2 Go de RAM) | 06:21 | ia 75 s / zscore 75 s / alerte_ia 145 s | cpu_host | stop-sonarqube | stop-sonarqube (executee) | oui | 160 s | — |
| 12 | Fuite mémoire dans le pod de l'API (+150 Mo) | 06:53 | ia 50 s / zscore 50 s / alerte_ia 120 s | ram_server_mb | rollout-restart server | rollout-restart server (executee), aucune (aucune_action) | oui | 161 s | persistant |
| 13 | Surcharge CPU du serveur (3 cœurs sur 4) | 07:12 | ia 60 s / zscore 60 s / alerte_ia 130 s | cpu_host | stop-sonarqube | stop-sonarqube (executee), aucune (aucune_action) | oui | — | — |
| 14 | Arrêt du moteur AIOps | 07:34 | alerte 155 s | — | restart-container mon-aiops-engine | restart-container mon-aiops-engine (executee), aucune (aucune_action) | oui | 187 s | — |

## Lecture

- Rappel : part des pannes vues par le détecteur, pendant la panne. Délai : depuis l'injection.
- Les pannes de collecteurs (cAdvisor, node-exporter, moteur) sont vues par les alertes de disponibilité, pas par
  le modèle (le modèle ne voit justement plus les données) : elles sont exclues du rappel de l'IA.
- Pour les surcharges du serveur (CPU, RAM), l'action attendue est l'arrêt de SonarQube ; il était déjà arrêté :
  l'agent répond « rien à faire », ce qui est correct mais ne répare pas (escalade humaine).
