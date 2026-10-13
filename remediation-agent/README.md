# remediation-agent

Agent d'auto-réparation du parc : il reçoit les alertes d'Alertmanager, choisit une action corrective sûre
d'après le diagnostic du moteur AIOps, l'exécute sur le bon nœud, vérifie son effet et journalise tout.

```
Moteur AIOps (détecte, diagnostique) ──► Prometheus ──► Alertmanager ──► agent ──► SSH bridé ──► pfe-runbook (nœud)
                                                                           │
                                                   journal + métriques ◄───┘──► vérification 5 min après
```

## Politique (liste fermée)

| Alerte | Action | Condition |
|---|---|---|
| AnomalieAIOps, variable `cpu_server` ou `ram_server_mb` | `rollout-restart server` (redémarrage progressif, sans coupure) | nœud applicatif, hors build |
| AnomalieAIOps, variable `cpu_host` ou `ram_host` ; RamElevee | `stop-sonarqube` | SonarQube actif hors build |
| CollecteurArrete (cAdvisor), NoeudInjoignable (node-exporter) | `restart-container <collecteur>` | — |
| MoteurAIOpsArrete | `restart-container mon-aiops-engine` | serveur de supervision |
| DisquePlein, DisquePleinPrevu | `clean-disk` (images Docker inutilisées, journaux > 14 j) | **désactivé par défaut** (`remediation_disk_cleanup`) |
| Toute autre alerte, ou anomalie sur une autre variable | aucune : escalade humaine (e-mail d'Alertmanager) | — |

## Garde-fous

- **Mode conseil par défaut** : l'agent simule, journalise et vérifie si l'alerte disparaît seule ; `actif` exécute.
- **Liste fermée des deux côtés** : l'agent refuse toute action hors liste ; sur le nœud, la clé SSH est bridée
  (commande forcée `pfe-runbook`, adresse source imposée, `restrict`), `sudo` n'autorise que ce script,
  qui revérifie l'action, l'argument et le contexte (build en cours, conteneur autorisé).
- **Pause** de 30 min entre deux actions identiques, **quota** de 3 actions par heure, **2 tentatives** au plus par
  incident puis escalade, **aucune action pendant une maintenance** (build Jenkins, sauvegarde).
- **Abstention** si Prometheus ne répond pas (maintenance invérifiable) ou si le diagnostic AIOps manque.
- **Disjoncteur** : 3 échecs consécutifs suspendent l'agent (alerte critique) jusqu'à intervention humaine.
- État des garde-fous conservé sur disque ; un état illisible suspend l'agent par sécurité.
- Webhook protégé par jeton, aucun port publié, conteneur non-root en lecture seule, 128 Mo de RAM.
- Double traçabilité : journal de l'agent (`/data/actions.csv`) et journal système de chaque nœud (`journalctl -t pfe-runbook`).

## Utilisation (sur le serveur de supervision)

```bash
docker exec mon-remediation python -m agent journal 30   # 30 dernières décisions
docker exec mon-remediation python -m agent tester       # chaîne SSH vers chaque nœud (action « status », sans effet)
docker exec mon-remediation python -m agent reprendre && docker restart mon-remediation   # après une suspension
```

Passer en mode actif : `remediation_mode: actif` dans `infra/ansible/group_vars/all.yml`, puis relancer `deploy.sh`.

## Métriques

`pfe_agent_mode`, `pfe_agent_suspended`, `pfe_agent_decisions_total{alertname,node,action,decision}`,
`pfe_agent_actions_total{node,action,outcome}`, `pfe_agent_verifications_total{action,result}`,
`pfe_agent_node_reachable{node}`, `pfe_agent_last_action_timestamp_seconds`.

## Tests

`python -m pytest tests` : 45 tests (politique, garde-fous, travailleur, webhook, commande SSH). Ils s'exécutent
pendant `docker build` : un échec empêche l'image d'exister. Bibliothèque standard seulement (aucune dépendance).
