# Chaîne CI/CD — fonctionnement et procédure

## Vue d'ensemble

```
git push main ──► Jenkins (CI) ─────────────────────────────────────────────► Argo CD (CD) ──► k3s
                  Checkout → SonarQube (Quality Gate) → Build → Trivy → Push
                  → commit "[skip ci]" du nouveau tag dans k8s/ ──────────────┘
```

| Contrôle | Comportement |
|---|---|
| SonarQube | Démarré par le pipeline s'il est arrêté, puis ré-arrêté. Reçoit la couverture des tests. **Bloquant** si le Quality Gate échoue ; les conditions en échec, hotspots et problèmes du code nouveau sont alors affichés dans le journal Jenkins. |
| Trivy | **Bloquant** sur toute faille CRITIQUE corrigeable non listée dans `.trivyignore`. Les HIGH sont affichées sans bloquer. |
| Tag d'image | `<n° de build>-<commit court>` (ex. `8-76ada39`), écrit dans `k8s/` : chaque déploiement est un commit traçable. |
| Anti-boucle | Le commit `[skip ci]` de Jenkins ne relance pas la CI (build marqué NOT_BUILT). |
| Concurrence | Un seul build à la fois ; arrêt automatique après 30 min. |
| Secrets | Plus aucun secret dans Git : les pods lisent le Secret Kubernetes `app-cobaye-secrets` (copie dans Vault `secret/app-cobaye-k8s`). |
| AIOps | Le pipeline annonce 30 min de maintenance au moteur AIOps (métrique `pfe_maintenance_until_seconds` via node-exporter, voir `infra/README.md`). |
| Tests | 27 tests unitaires de l'API (authentification, mots de passe, droits sur les tâches, contrôleurs), environ 93 % des lignes couvertes, exécutés dans l'étape *Tests serveur et couverture* : **bloquants**. |
| Pods | Sondes de disponibilité et de vie ; limites CPU, mémoire et disque ; aucun conteneur root (utilisateurs 1000, 101 et 999), système de fichiers en lecture seule, capabilities retirées, pas de jeton de compte de service. |
| Images | Copies explicites (pas de `COPY . .`) ; client servi par nginx non-root sur le port 8080. |
| Données | PostgreSQL sur un volume persistant (`postgres-data`, provisionneur local-path de k3s) : les données survivent aux redémarrages. |

Jenkins n'a **aucun accès au cluster** : il n'écrit que dans Git.

## Mise en place (une seule fois, dans cet ordre)

1. **Secret Kubernetes et inventaire des failles** : sur le VPS, `bash scripts/preparer-vps.sh`.
   Les nouvelles failles critiques listées sont ajoutées à `.trivyignore` avec une date d'expiration (90 jours).
2. **Credential GitHub dans Jenkins** :
   - GitHub → Settings → Developer settings → Fine-grained tokens → *Generate new token* :
     dépôt `pfe-devsecops` uniquement, permission **Contents : Read and write**, expiration 90 jours.
   - Jenkins → Administrer Jenkins → Credentials → *Add Credentials* :
     type **Username with password**, ID **`github-token`**, username = compte GitHub, password = le token.
3. **Relancer un build** dans Jenkins (*Lancer un build*) : il publie les images et déclenche le déploiement.

Si le Secret Kubernetes manque, les nouveaux pods restent en attente et **les anciens continuent de tourner**
(mise à jour progressive) : l'application n'est pas interrompue.

## Revenir à une version précédente

```bash
git revert <commit "[skip ci] Deploiement des images X">   # puis git push
```
Argo CD redéploie la version précédente ; la CI ne se relance pas pour ce commit.

## Ajouter une exception Trivy

Ligne dans `.trivyignore` : `CVE-AAAA-NNNN exp:AAAA-MM-JJ   # paquet - justification`.
À l'expiration, la faille bloque à nouveau le pipeline.

## Limites connues

- Tests unitaires seulement : pas encore de tests d'intégration contre une vraie base dans le pipeline.
- Vault tourne en mode développement (données en mémoire) : le Secret Kubernetes est la copie opérationnelle.
- Jenkins utilise le Docker de l'hôte : un agent de build isolé serait préférable en production.
- Le volume PostgreSQL est local au VPS : pas de réplication, et les sauvegardes restent à mettre en place (objectif 5, Rundeck).
