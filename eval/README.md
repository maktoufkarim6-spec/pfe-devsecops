# Évaluation par injection de pannes réelles

Objectif : mesurer, sur les vrais serveurs, ce que valent la détection (moteur AIOps et seuils fixes) et la
remédiation automatique (agent). Les résultats de chaque campagne sont dans `resultats/`.

## Protocole

1. **État calme exigé avant chaque panne** : modèle AIOps prêt et hors période de chauffe, aucune maintenance
   annoncée, aucune anomalie ni alerte en cours sur le nœud, hors créneaux des sauvegardes (2 h 20–3 h 05 et
   4 h 20–4 h 55 UTC), et au moins 35 min depuis une panne appelant la même action (pause de 30 min de l'agent).
2. **Injection** d'une panne réelle (liste ci-dessous) par SSH, horodatée avec l'horloge de Prometheus.
3. **Observation toutes les 15 s** : premier instant où chaque détecteur signale la panne, variable désignée par
   l'IA, disparition de la panne (réparation par l'agent).
4. **Annulation systématique** par le script à la fin de la durée prévue (ou en cas d'erreur) : l'état final est
   l'état initial. Puis attente du retour au calme (15 min au plus) et **période calme de 8 min**, qui sert aussi
   à compter les fausses alertes.
5. **Analyse** (`analyse.py`) à partir des séries Prometheus et du journal de l'agent.

| Panne | Comment | Détection attendue | Action attendue de l'agent |
|---|---|---|---|
| cAdvisor arrêté | `docker stop cadvisor` | alerte CollecteurArrete | redémarrer cadvisor |
| node-exporter arrêté | `docker stop node-exporter` | alerte NoeudInjoignable | redémarrer node-exporter |
| Moteur AIOps arrêté | `docker stop mon-aiops-engine` | alerte MoteurAIOpsArrete | redémarrer le moteur |
| SonarQube hors build | `docker start sonarqube` (+2 Go) | IA (RAM ou CPU du serveur) | arrêter SonarQube |
| Surcharge CPU | conteneur de calcul sur 3 cœurs sur 4 | IA (cpu_host), seuil CPU > 80 % | arrêter SonarQube (déjà arrêté : rien à faire) |
| Pression mémoire | 2 Go en mémoire (tmpfs) | IA (ram_host) | idem |
| Boucle CPU dans l'API | processus `node` en boucle dans le pod | IA (cpu_server) | redémarrage progressif de l'API |
| Fuite mémoire dans l'API | +150 Mo dans le pod | IA (ram_server_mb) | redémarrage progressif de l'API |
| Disque | fichier de 6 Go | IA (disk_host) | aucune (nettoyage désactivé) : escalade |
| Pod client à 0 | `kubectl scale --replicas=0` | IA (app_containers), remise en place par Argo CD | aucune |

## Indicateurs

- **Rappel** d'un détecteur : pannes détectées / pannes où il est attendu.
- **Précision** du moteur : épisodes d'anomalie liés à une panne / tous les épisodes (les autres sont des faux
  positifs), avec le **taux de fausses alertes par heure**.
- **Délai de détection** : depuis l'injection (médiane, min, max), comparé aux seuils fixes.
- **Diagnostic** : la variable désignée par l'IA est-elle la bonne ?
- **Agent** : décision correcte (action choisie = action attendue), réparation effective, délai de rétablissement
  (début de la panne → panne disparue), vérification 5 min après.

## Limites

- Peu d'injections par type : les pourcentages sont indicatifs, pas statistiquement robustes.
- Le modèle de la première campagne n'est appris que sur quelques heures, de nuit, sans calibration : refaire la
  campagne après 24 h calmes et `aiops.calibrate` pour comparer avant et après.
- Pannes simples et isolées : pas de pannes simultanées ni de dégradations lentes (sauf le disque).

## Relancer

```bash
python eval/campagne.py                       # campagne complète (depuis le PC, clé SSH vers les deux VPS)
python eval/campagne.py --seulement cpu_api   # une seule panne
python eval/analyse.py                        # tableau de résultats -> resultats/RESULTATS.md
```
