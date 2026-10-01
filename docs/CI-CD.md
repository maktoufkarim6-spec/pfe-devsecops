# Chaîne CI/CD — fonctionnement et procédure

## Vue d'ensemble

```
git push main ──► Jenkins (CI) ─────────────────────────────────────────────► Argo CD (CD) ──► k3s
                  Checkout → SonarQube (Quality Gate) → Build → Trivy → Push
                  → commit "[skip ci]" du nouveau tag dans k8s/ ──────────────┘
```

| Contrôle | Comportement |
|---|---|
| SonarQube | Démarré par le pipeline s'il est arrêté, puis ré-arrêté. **Bloquant** si le Quality Gate échoue. |
| Trivy | **Bloquant** sur toute faille CRITIQUE corrigeable non listée dans `.trivyignore`. Les HIGH sont affichées sans bloquer. |
| Tag d'image | `<n° de build>-<commit court>` (ex. `8-76ada39`), écrit dans `k8s/` : chaque déploiement est un commit traçable. |
| Anti-boucle | Le commit `[skip ci]` de Jenkins ne relance pas la CI (build marqué NOT_BUILT). |
| Concurrence | Un seul build à la fois ; arrêt automatique après 30 min. |
| Secrets | Plus aucun secret dans Git : les pods lisent le Secret Kubernetes `app-cobaye-secrets` (copie dans Vault `secret/app-cobaye-k8s`). |
| AIOps | Le pipeline pose la fenêtre de maintenance du moteur AIOps pendant le build. |
| Pods | Sondes de disponibilité et de vie, requêtes et limites mémoire. |

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

- Les tests unitaires de l'application cobaye (squelette Nest.js) ne passent pas : ils ne sont donc pas
  exécutés par le pipeline. L'application réelle devra fournir des tests fonctionnels, ajoutés avant le build.
- Vault tourne en mode développement (données en mémoire) : le Secret Kubernetes est la copie opérationnelle.
- Jenkins utilise le Docker de l'hôte : un agent de build isolé serait préférable en production.
- PostgreSQL n'a pas de volume persistant : les données de démonstration sont recréées à chaque redémarrage du pod.
