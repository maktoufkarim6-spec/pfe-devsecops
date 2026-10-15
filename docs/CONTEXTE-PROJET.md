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

## Réutilisation

L'application cobaye est remplaçable : tout ce qui la concerne est regroupé en tête de
`infra/ansible/group_vars/all.yml` et du `Jenkinsfile` (guide : `docs/REUTILISER.md`).

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
  multi-nœuds via `AIOPS_TARGETS`, profil d'application configurable, 57 tests exécutés pendant `docker build`. Voir `aiops-engine/README.md`.
- `infra/` : supervision multi-nœuds en Ansible + `deploy.sh`. Voir `infra/README.md`.

## Où on en est exactement (à reprendre dans cet ordre)

Fait les 12 et 13 octobre 2026 : pipeline vert (#16), supervision multi-nœuds, agent de remédiation et
sauvegardes croisées déployés (sauvegarde, copie, vérification et test de restauration réussis des deux côtés),
campagne de 14 pannes réelles (résultats : `eval/resultats/`), interface Grafana « Agent et sauvegardes »,
plateforme rendue réutilisable (`docs/REUTILISER.md`).

État particulier à connaître :
- L'agent tourne en **mode actif** (conteneur recréé à la main pour la campagne). Un `deploy.sh` sans option le
  remet en mode conseil ; pour garder l'actif : `bash deploy.sh -e remediation_mode=actif`.
- Le code de l'agent dans le dépôt est plus récent que l'image déployée (une baisse n'est plus une surcharge,
  action sans effet hors quota, déploiement de l'API configurable) : relancer `deploy.sh` pour l'appliquer.

1. **Relancer `deploy.sh -e remediation_mode=actif`** : nouvelle version de l'agent, page d'accueil Grafana,
   testeur de services, profil d'application.
2. **Sécurité SSH** : des milliers de tentatives de connexion par mot de passe par jour sur les VPS. Écrit
   et testé en local (`infra/ansible/cles-ssh.yml`, procédure `docs/CLES-SSH.md`) : **à lancer** par le
   propriétaire (`deploy.sh --cles`, vérification depuis le PC, puis `deploy.sh --fermer-mots-de-passe`).
3. **Alertes par e-mail** (Gmail) : relancer `deploy.sh` avec l'adresse et un mot de passe d'application Gmail.
4. **Calibration** du moteur après 24 h calmes, puis refaire la campagne (`eval/campagne.py`) pour comparer.
5. Objectif 5 : Rundeck (ou justification de l'orchestration Ansible + minuteries systemd).
6. **Plusieurs applications** (phase 1, écrite et testée en local, `docs/APPLICATIONS.md`) : un dossier
   `applications/<nom>/` par application, déployé par Argo CD (ApplicationSet) avec le chart commun
   `plateforme/chart`, dans un namespace isolé. Application de test : `demo-podinfo`. **À mettre en service**
   avec `deploy.sh`. Phases suivantes : pipeline Jenkins commun, supervision et sauvegardes par application,
   découverte des serveurs vides, installation d'un parc vide.

## Règles de travail

- Ne jamais écrire de mot de passe, token ou secret dans Git (dépôt public) ni dans un fichier non protégé.
- Avant toute opération lourde sur le VPS applicatif (build manuel, nettoyage Docker), annoncer une
  maintenance pour éviter les fausses alertes AIOps : écrire `pfe_maintenance_until_seconds <fin>` dans
  `/var/lib/node_exporter/textfile/maintenance.prom` (Jenkins le fait automatiquement).
- Ne rien supprimer sur un serveur sans accord explicite (données, volumes, conteneurs).
- Le propriétaire a choisi de garder ses mots de passe actuels : à citer comme limite dans le rapport.
- Le propriétaire n'est pas développeur expert : expliquer simplement, en français, avec des commandes
  à copier-coller une par une, en précisant toujours si elles se lancent sur le PC ou sur quel VPS.
