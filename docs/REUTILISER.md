# Réutiliser la plateforme pour une autre application

L'application « cobaye » (React + Nest.js + PostgreSQL) a servi à construire et à évaluer la plateforme.
Tout ce qui l'entoure est générique : chaîne CI/CD, GitOps, supervision, moteur AIOps, agent de
remédiation, sauvegardes, page d'accueil et campagne d'évaluation. Voici ce qu'il faut changer, et où.

## Ce qui est couvert

| Situation | Effort |
|---|---|
| Application web conteneurisée, k3s sur VPS Linux (Debian), base PostgreSQL | Remplir le bloc de variables et l'en-tête du `Jenkinsfile` |
| Autre langage ou autre structure de code (pas de `server/` et `client/`) | Adapter les étapes *Tests* et *Build* du `Jenkinsfile` |
| Plusieurs composants (microservices) | Liste `app_restartable` ; l'IA suit finement l'API principale et la base, les autres composants sont comptés |
| Autre base (MySQL, MongoDB…) | Adapter la partie base de `pfe-backup` et du test de restauration (écrite pour `pg_dump`) |
| Plusieurs VPS applicatifs dans le même cluster k3s | Une ligne par VPS dans l'inventaire (section « Ajouter un VPS ») |
| Kubernetes managé (EKS, GKE, AKS), plusieurs clusters, serveurs non Linux, applications non conteneurisées | Non prévu : scripts fondés sur `k3s kubectl` et sur un accès SSH aux serveurs |
| Grande échelle | Non prévu : un seul Prometheus, sans haute disponibilité ; sauvegardes centralisées sur le serveur de supervision |

## 1. Les serveurs : `infra/ansible/inventory.ini`

```ini
[monitoring]
supervision ansible_host=<IP du serveur de supervision> ansible_connection=local node_name=monitoring node_app=false

[nodes]
app-1 ansible_host=<IP du serveur applicatif> node_name=app-1 node_app=true
```

Un nœud en plus = une ligne de plus dans `[nodes]`. `node_app=true` pour un nœud qui héberge l'application
(k3s) : il reçoit le collecteur des pods et ses variables applicatives sont surveillées par l'IA.

### Ajouter un VPS applicatif (nœud k3s supplémentaire)

1. Sur le nouveau VPS (Debian), rejoindre le cluster existant comme **agent k3s**. Le jeton se lit sur le nœud
   de contrôle : `sudo cat /var/lib/rancher/k3s/server/node-token`.
   ```bash
   curl -sfL https://get.k3s.io | K3S_URL=https://<IP du nœud de contrôle>:6443 K3S_TOKEN=<jeton> sh -
   ```
   Le port 6443 du nœud de contrôle doit être joignable depuis le nouveau VPS.
2. Dans `inventory.ini`, ajouter une ligne dans `[nodes]` :
   `app-2 ansible_host=<IP> node_name=app-2 node_app=true` (sans `k3s_server`, réservé au nœud de contrôle).
3. Relancer `deploy.sh` : collecteurs, pare-feu, compte de l'agent et sauvegardes sont installés sur le nouveau
   VPS ; Prometheus, le moteur AIOps (un modèle de plus), l'agent et la page d'accueil le prennent en compte.
   Ansible utilise le même mot de passe SSH pour tous les serveurs : même mot de passe sur le nouveau VPS, ou
   passer Ansible en connexion par clé.

Ce que fait la plateforme avec plusieurs nœuds :
- **Un modèle d'IA par nœud**, une ligne par nœud sur la page d'accueil, sélecteur de nœud dans Grafana.
- **Actions sur le cluster** (redémarrage progressif de l'API) : toujours exécutées par le nœud de contrôle
  (`k3s_server=true`), quel que soit le nœud où l'anomalie est vue. Actions locales (collecteurs) : sur le nœud
  concerné.
- **Sauvegardes** : chaque VPS applicatif envoie ses jeux au serveur de supervision ; la base est sauvegardée par
  le nœud de contrôle ; le serveur de supervision copie les siens sur le nœud de contrôle. Prévoir de la place
  sur le serveur de supervision.
- Jenkins, SonarQube, Argo CD et Vault restent sur le nœud de contrôle.

## 2. L'application : bloc en tête de `infra/ansible/group_vars/all.yml`

| Variable | Rôle | Cobaye |
|---|---|---|
| `app_name` | nom court (titres, journaux) | `app-cobaye` |
| `app_namespace` | espace de noms Kubernetes de l'application | `default` |
| `app_api` | déploiement et conteneur de l'API : CPU et RAM surveillés par l'IA, redémarrage progressif par l'agent | `server` |
| `app_restartable` | déploiements que l'agent a le droit de redémarrer | `[server, client]` |
| `app_db_deployment` | déploiement PostgreSQL sauvegardé chaque nuit (`""` = pas de base) | `postgres` |
| `app_db_name` | base sauvegardée (`pg_dump`) et restaurée chaque semaine pour test | `demo` |
| `app_ci_heavy_container` | outil de CI lourd que l'agent peut arrêter hors build (`""` = aucun) | `sonarqube` |
| `app_url_path` | page testée par la page d'accueil | `/` |
| `repo_web` | dépôt Git (clonage par Ansible, liens) | dépôt du PFE |
| `evaluation_url` | page de résultats de la campagne (`""` = aucune) | page des résultats |
| `services_web` | services déclarés à la main sur la page d'accueil (nom, adresse testée, lien). Les autres services exposés (ports Docker publics, NodePort et Ingress de k3s) sont **découverts automatiquement** au déploiement puis chaque minute | 10 services |

Ces variables sont transmises au moteur AIOps (`AIOPS_APP_NAMESPACE`, `AIOPS_APP_API`, `AIOPS_APP_DB`), à
l'agent (`AGENT_APP_API`), aux scripts des nœuds (`pfe-runbook`, `pfe-backup`, `pfe-restore`) et aux tableaux de
bord (marqueurs `__APP_NAMESPACE__`, `__APP_IP__`… remplacés au déploiement). Aucun code à modifier.

Contraintes : base PostgreSQL dont l'image fournit `pg_dump`, `pg_restore`, `psql` et la variable
`POSTGRES_USER` (image officielle) ; conteneurs de l'application avec `sh`, `ps` et `pkill` pour la campagne
d'évaluation.

## 3. La chaîne CI/CD : `Jenkinsfile`, `k8s/`, `scripts/preparer-vps.sh`

- `Jenkinsfile`, bloc `environment` en tête : `APP_NAME` (clé SonarQube), `IMAGE_SERVER`, `IMAGE_CLIENT`
  (images Docker Hub). Les étapes supposent une API dans `server/` (tests avec couverture lcov) et un client
  dans `client/` : adapter les étapes *Tests* et *Build* si la structure diffère. SonarQube, Trivy, push et
  mise à jour GitOps restent identiques.
- `k8s/` : les manifestes de la nouvelle application (garder les conventions de durcissement : non-root,
  lecture seule, limites, sondes). La mise à jour GitOps remplace l'étiquette des lignes `image:`.
- Argo CD : une Application qui pointe sur le dossier `k8s/` du dépôt (auto-sync, self-heal, prune).
- `scripts/preparer-vps.sh` : nom du Secret Kubernetes et de ses clés.
- Credentials Jenkins : `dockerhub`, `sonarqube-token`, `github-token` (voir `docs/CI-CD.md`).

## 4. Déployer

```bash
# Sur le serveur de supervision
curl -fsSL <repo_web>/raw/main/infra/deploy.sh -o deploy.sh && PFE_REPO=<repo_web>.git bash deploy.sh
```

L'agent démarre en mode conseil ; passer en actif après relecture de son journal :
`bash deploy.sh -e remediation_mode=actif`.

## 5. Évaluer (facultatif)

```bash
PFE_APP_IP=<IP app> PFE_MON_IP=<IP supervision> PFE_APP_NAMESPACE=<ns> PFE_APP_API=<api> \
PFE_APP_FRONT=<frontal> python eval/campagne.py
python eval/analyse.py && python eval/rapport_html.py
```

La panne « fuite mémoire dans l'API » est écrite pour une API Node.js (`node -e`) : l'adapter au langage de
la nouvelle API (les autres pannes sont indépendantes du langage).

## Ce qui reste propre au PFE

- Les seuils de l'IA (`aiops-engine/aiops/features.py`) sont ceux d'un petit VPS ; les recalibrer avec
  `aiops.calibrate` après 24 h de fonctionnement normal.
- La liste fermée des actions de l'agent (`pfe-runbook`) couvre les pannes de cette plateforme ; une nouvelle
  action se déclare des deux côtés (`remediation-agent/agent/policy.py` et `pfe-runbook.j2`), avec ses tests.
