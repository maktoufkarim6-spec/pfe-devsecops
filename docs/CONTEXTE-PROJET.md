# Contexte du projet (reprise par une nouvelle session)

Document de passation : état exact du projet au 13 octobre 2026. À lire en entier avant d'agir.

## Le projet

PFE DevSecOps + AIOps + gestion de parc : déployer une application multi-tiers sur Kubernetes de façon
sécurisée et automatisée. Base d'un futur SaaS de l'encadrant ; approche « application cobaye »
(React + Nest.js + PostgreSQL) pour construire la chaîne avant de brancher la vraie application.

- Dépôt : https://github.com/maktoufkarim6-spec/pfe-devsecops (branche `main`, publique)
- Images : Docker Hub `kariimm557/app-cobaye-server` et `kariimm557/app-cobaye-client`

## Serveurs

| Rôle | Adresse | Contenu |
|---|---|---|
| Application (nœud `app-1`) | 57.129.142.176 | Debian 13, 4 vCPU, 7,6 Go RAM. Jenkins (8080), SonarQube (9000, arrêté hors builds), sonar-db, Vault (8200, mode dev), k3s avec l'application et Argo CD (NodePort 32252), ancienne supervision mono-serveur (Prometheus 9090, Grafana 3000, Alertmanager 9093, Netdata 19999, node-exporter 9100, cAdvisor 8081, cAdvisor-k3s 8082, aiops-engine 127.0.0.1:8010) |
| Supervision (nœud `monitoring`) | 54.37.13.100 | Debian 13, 3,7 Go RAM. Supervision centralisée (Prometheus, Alertmanager, Grafana 3000, moteur AIOps, agent de remédiation). Ancienne installation (Jenkins, k3s) arrêtée, rien supprimé |

Utilisateur SSH : `debian` (mot de passe demandé au propriétaire, jamais écrit dans un fichier ni dans Git).
La RAM du VPS applicatif est juste : ne pas laisser SonarQube tourner hors build.

## Les 5 objectifs

| # | Objectif | État |
|---|---|---|
| 1 | CI/CD (Jenkins, webhook GitHub, Docker Hub) | Fait et validé de bout en bout (build #16 : push → Jenkins → Docker Hub → commit GitOps → Argo CD) |
| 2 | Sécurité (SonarQube, Trivy, Vault) | Fait ; SonarQube et Trivy bloquants |
| 3 | GitOps (Argo CD, auto-sync, self-heal, prune) | Fait |
| 4 | Supervision + AIOps | Supervision multi-nœuds déployée ; agent d'auto-réparation écrit (mode conseil), **à déployer** |
| 5 | Parc serveurs (Rundeck + Ansible) | Sauvegardes croisées en Ansible écrites, **à déployer** ; Rundeck pas commencé |

## Ce qui est dans le dépôt

- `Jenkinsfile` : Checkout → fenêtre de maintenance AIOps → tests serveur + couverture → SonarQube
  (Quality Gate bloquant, diagnostic détaillé dans le journal en cas d'échec) → builds → Trivy
  (CRITICAL bloquant, exceptions datées dans `.trivyignore`) → push Docker Hub → mise à jour GitOps
  (écrit le tag `<build>-<commit>` dans `k8s/`, commit `[skip ci]`, Argo CD déploie). Détails : `docs/CI-CD.md`.
- `server/` : API Nest.js, 27 tests unitaires (environ 93 % des lignes), image non-root.
- `client/` : React servi par nginx-unprivileged (port 8080).
- `k8s/` : Deployments durcis (non-root, lecture seule, limites), Secret Kubernetes
  `app-cobaye-secrets` (déjà créé sur le VPS par `scripts/preparer-vps.sh`, copie dans Vault),
  PostgreSQL sur volume persistant.
- `aiops-engine/` : moteur AIOps v3.1.0 (z-score robuste MAD + Isolation Forest + prévisions Theil-Sen),
  multi-nœuds via `AIOPS_TARGETS`, 56 tests exécutés pendant `docker build`. Voir `aiops-engine/README.md`.
- `infra/` : supervision multi-nœuds en Ansible + `deploy.sh`. Voir `infra/README.md`.

## Où on en est exactement (à reprendre dans cet ordre)

Fait le 13 octobre 2026 : faille critique `proxy-addr` corrigée, pipeline vert (#16), credential Jenkins `github-token`
en place, supervision multi-nœuds déployée sur 54.37.13.100, ancienne supervision du VPS applicatif arrêtée
(`retirer-ancienne-supervision.yml`), clé SSH du PC du propriétaire installée sur les deux VPS.

1. **Déployer l'agent de remédiation et les sauvegardes** : relancer `infra/deploy.sh` sur le serveur de supervision.
   Puis : `docker exec mon-remediation python -m agent tester` (chaîne SSH vers chaque nœud), une sauvegarde
   immédiate sur chaque nœud (`sudo systemctl start pfe-backup`) et un test (`sudo pfe-restore tester`).
2. **Démonstration** : provoquer une panne (ex. `docker stop cadvisor` sur le VPS applicatif) et vérifier dans le
   journal de l'agent qu'il la détecte et propose (mode conseil) puis exécute (mode actif) la réparation.
3. **Mode actif** de l'agent après quelques jours de journal en mode conseil jugé correct (`remediation_mode: actif`).
4. **Alertes par e-mail** (Gmail plutôt que Telegram) : relancer `deploy.sh` avec l'adresse et un mot de passe
   d'application Gmail. Les actions réelles de l'agent sont alors aussi notifiées par e-mail (`ActionAutomatique`).
5. Ensuite : calibration du moteur après 24 h calmes
   (`docker exec mon-aiops-engine python -m aiops.calibrate /data/detections-app-1.csv`), injection de
   pannes réelles pour le tableau d'évaluation (précision, rappel, délai, et taux de réparation automatique de l'agent),
   Rundeck pour l'objectif 5.

## Règles de travail

- Ne jamais écrire de mot de passe, token ou secret dans Git (dépôt public) ni dans un fichier non protégé.
- Avant toute opération lourde sur le VPS applicatif (build manuel, nettoyage Docker), annoncer une
  maintenance pour éviter les fausses alertes AIOps : écrire `pfe_maintenance_until_seconds <fin>` dans
  `/var/lib/node_exporter/textfile/maintenance.prom` (Jenkins le fait automatiquement).
- Ne rien supprimer sur un serveur sans accord explicite (données, volumes, conteneurs).
- Le propriétaire a choisi de garder ses mots de passe actuels : à citer comme limite dans le rapport.
- Le propriétaire n'est pas développeur expert : expliquer simplement, en français, avec des commandes
  à copier-coller une par une, en précisant toujours si elles se lancent sur le PC ou sur quel VPS.
