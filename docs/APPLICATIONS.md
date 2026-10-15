# Plusieurs applications sur la plateforme

**Un dossier = une application.** Chaque dossier `applications/<nom>/` contient un fichier `values.yaml`
qui décrit l'application. La plateforme fait le reste :

- un **namespace Kubernetes isolé** (quotas, réseau fermé par défaut) ;
- des **secrets générés** ;
- un **déploiement Argo CD** (GitOps : auto-sync, self-heal) ;
- une **adresse web**.

L'application cobaye (`k8s/`, namespace `default`) n'est pas concernée : elle reste gérée comme avant,
avec ses données.

```
applications/
  demo-podinfo/values.yaml   →  namespace demo-podinfo  →  http://demo-podinfo.57-129-142-176.sslip.io/
  boutique/values.yaml       →  namespace boutique      →  http://boutique.57-129-142-176.sslip.io/
plateforme/chart/            →  modèle commun (Helm) utilisé par toutes les applications
```

## Mise en service (une seule fois)

Sur le serveur de supervision : `bash deploy.sh`. Le rôle `applications` installe l'ApplicationSet dans
Argo CD, puis crée les namespaces et les secrets. Si la définition ApplicationSet manque (cas connu :
`kubectl apply` côté client saute ce fichier de 1,4 Mo à l'installation d'Argo CD, et son contrôleur
redémarre alors en boucle), le rôle l'installe côté serveur, dans la version exacte d'Argo CD, puis relance
le contrôleur. Ensuite, chaque dossier ajouté dans `applications/` est
déployé automatiquement.

## Ajouter une application

1. Créer `applications/<nom>/values.yaml`. Le nom ne peut contenir que des minuscules, des chiffres et
   des tirets (3 à 32 caractères), et il devient le namespace. Modèle :
   ```yaml
   composants:
     - nom: web
       image: registre/mon-image:1.4.2     # tag figé obligatoire (latest refusé)
       port: 8080
       public: true                        # exposé sur l'adresse web
       sante: /healthz                     # sonde HTTP (vide : sonde TCP)
       utilisateur: 1000                   # UID non root de l'image (obligatoire)
   ```
2. Commit puis push sur `main`. Argo CD crée l'application en une à trois minutes.
3. **Seulement si l'application a une base ou des `secrets_generes`** : relancer `deploy.sh` sur le
   serveur de supervision, pour créer les secrets. Tant que ce n'est pas fait, les pods qui lisent ces
   secrets attendent, sans erreur pour les autres applications.

Retirer une application : supprimer son dossier. Argo CD arrête de la gérer **sans effacer** ce qu'elle a
créé (namespace, volume de la base). La suppression définitive se fait à la main, avec accord.

## Toutes les options (`values.yaml`)

| Clé | Rôle | Défaut |
|---|---|---|
| `domaine` | adresse web (le DNS doit pointer vers le nœud de contrôle) | `<nom>.<ip-avec-tirets>.sslip.io` |
| `noeuds` | nœuds où tourner (noms de `inventory.ini`) | n'importe lequel |
| `quotas.cpu`, `quotas.memoire`, `quotas.pods` | plafond de l'application entière | `1`, `1Gi`, `10` |
| `composants[].nom` | nom du composant (Deployment et Service) | obligatoire |
| `composants[].image` | image avec tag figé ou empreinte `@sha256:` | obligatoire |
| `composants[].port` | port du conteneur | obligatoire |
| `composants[].utilisateur` / `groupe` | UID et GID non root | obligatoire / = UID |
| `composants[].copies` | nombre de copies, réparties sur les nœuds | `1` |
| `composants[].public`, `chemin` | exposition sur l'adresse web | non, `/` |
| `composants[].sante` | chemin de la sonde HTTP | sonde TCP |
| `composants[].commande` | commande du conteneur | celle de l'image |
| `composants[].env` | variables (`NOM: valeur`) | — |
| `composants[].secrets` | variables lues dans `<nom>-secrets` (`NOM: clé`) | — |
| `composants[].ecriture` | dossiers inscriptibles (le reste est en lecture seule) | `[/tmp]` |
| `composants[].ressources` | `cpu`, `memoire` (réservés), `cpu_max`, `memoire_max` | 50m, 64Mi, 500m, 256Mi |
| `base.active`, `image`, `nom_base`, `taille` | PostgreSQL (Service `base`, port 5432) | non, `postgres:16`, `app`, `1Gi` |
| `secrets_generes` | clés aléatoires ajoutées à `<nom>-secrets` | — |

Base de données : le composant s'y connecte sur l'hôte `base`. Il lit l'utilisateur et le mot de passe
avec `secrets: {DB_USER: postgres-user, DB_PASSWORD: postgres-password}`.

## Sécurité appliquée à chaque application

- **Contrôles avant déploiement**, qui bloquent la synchronisation : nom valide et non réservé, au moins
  un composant, image à tag figé (pas de `latest`), port déclaré, utilisateur non root.
- **Conteneurs** : non root, système de fichiers en lecture seule, capabilities retirées, profil seccomp,
  pas de jeton Kubernetes, limites CPU, mémoire et disque.
- **Isolement** : `ResourceQuota`, qui empêche une application de priver les autres ; aucun `NodePort`
  ni `LoadBalancer` ; `NetworkPolicy` qui refuse tout trafic entrant, sauf celui de la même application et
  de Traefik vers les composants publics.
- **Secrets** : générés sur le nœud de contrôle (32 caractères aléatoires), jamais dans Git ni dans les
  journaux. Une clé existante n'est **jamais** régénérée.
- **Données** : le volume de la base est conservé quand l'application est retirée.

## Supervision, IA, réparation et sauvegardes de chaque application

Tout est automatique à partir du dossier `applications/<nom>/`. Il suffit de relancer `deploy.sh` après
avoir ajouté ou retiré une application.

| Brique | Ce qu'elle fait pour chaque application |
|---|---|
| **Sonde web** (job Prometheus `applications`) | Teste l'adresse de l'application (`domaine`, ou sslip.io par défaut ; chemin `sonde`, ou celui du premier composant public) toutes les 15 s depuis le serveur de supervision |
| **IA** (moteur AIOps 3.2) | **Un modèle par application** (cible `appli-<nom>`), à côté des modèles par nœud. Variables : `cpu_<composant>`, `ram_<composant>_mb`, `ram_base_mb` (si base), `app_containers`, `web_ok` (disponibilité), `web_ms` (temps de réponse). Il apprend après 2 h d'historique. Il se tait pendant une maintenance annoncée sur n'importe quel nœud (build, déploiement), puisque les pods d'une application peuvent tourner partout. |
| **Alertes** | `AnomalieApplication` (IA, avec la variable en cause), `ApplicationHorsLigne` (adresse muette depuis 2 min, critique), `SauvegardeApplicationEchouee`. `AnomalieAIOps` ne concerne plus que les nœuds. |
| **Agent de réparation** (1.1) | Sur `AnomalieApplication`, et seulement si l'IA désigne la **hausse** du CPU ou de la RAM d'un composant déclaré : **redémarrage progressif de ce composant** (`rollout-app <application>/<composant>`), par le nœud de contrôle, sans coupure. Jamais la base, jamais à l'aveugle : site injoignable ou conteneur perdu, c'est Argo CD qui remet les pods, et l'agent escalade vers un humain. Liste fermée revérifiée sur le nœud (`pfe-runbook`). |
| **Sauvegardes** | Chaque nuit, `pg_dump` de la base de chaque application qui en a une, dans le jeu du nœud de contrôle (copié ensuite sur l'autre serveur). **Une base en échec n'empêche pas les autres** (statut par application). Chaque semaine, la base est **réellement restaurée** dans une base temporaire, et les comptages de lignes sont comparés. |
| **Restauration** | `sudo pfe-restore app <application> <jeu> --confirmer` sur le nœud de contrôle (une seule transaction : en cas d'erreur, la base reste intacte) |
| **Grafana** | Page d'accueil, section « Applications de la plateforme » : en ligne, détection IA, mémoire, âge de la dernière sauvegarde |

Sécurité : les valeurs de `values.yaml` qui finissent dans des scripts root ou dans la configuration de
Prometheus sont vérifiées avant tout usage, dans le catalogue Ansible : nom, composants, `base.nom_base`,
domaine et chemin de la sonde. Une valeur invalide arrête le déploiement.

## Ce qui reste à faire (phases suivantes)

- **Construction depuis le code source** : pipeline Jenkins commun qui construit, teste et scanne
  (SonarQube, Trivy), puis écrit le nouveau tag dans `applications/<nom>/values.yaml`. Aujourd'hui, une
  application utilise une image déjà publiée.
- **HTTPS** et vrais noms de domaine.

## Vérifications faites

**Supervision par application** (en local, mêmes versions qu'en production) :
- **Moteur AIOps, 71 tests** (Python 3.11, versions figées), dont 14 nouveaux. Ils vérifient : les
  variables d'une application ; le refus des cibles invalides ; une fuite mémoire désignée sur le bon
  composant ; un site injoignable détecté ; l'isolement application/nœud ; des fichiers séparés ; et le
  silence pendant une maintenance.
- **Agent, 70 tests**, dont 16 nouveaux. Ils vérifient : le redémarrage du seul composant désigné ;
  jamais de redémarrage de la base, ni sur une baisse, un site injoignable ou un composant inconnu ; le
  passage par le nœud de contrôle ; et le refus des arguments mal formés.
- **Rendu réel des modèles Ansible avec deux applications** (19 tests) : `bash -n` des scripts root ;
  liste fermée du runbook ; sauvegarde et restauration de la bonne base ; configuration Prometheus et
  règles validées par promtool 2.53.0 ; **tests unitaires des alertes**
  (`roles/monitoring/files/alert-rules.test.yml`) ; refus des valeurs dangereuses
  (`nom_base: 'x; rm -rf /'`, domaine avec guillemet).

**Plateforme multi-applications :**

- **Chart** (22 tests) : rendu Helm, validation Kubernetes 1.31 (kubeconform), et refus de chaque erreur
  (tag `latest`, image sans tag, root, port manquant, nom réservé ou invalide, namespace différent,
  composant `base`, adresse impossible).
- **Rôle Ansible** (14 tests, faux k3s) :
  - Argo CD sans ApplicationSet : rien n'est fait ;
  - secrets créés, puis identiques à la relance ;
  - nouvelle clé ajoutée seule ;
  - nom invalide : arrêt avant toute action ;
  - ApplicationSet conforme au schéma d'Argo CD.
- **Constaté sur le cluster réel (16/10/2026)** : Argo CD v3.5.2 sans la définition ApplicationSet,
  contrôleur en erreur depuis 43 jours (8 752 redémarrages). Le rôle corrige ce cas.
- **Non vérifié sur le cluster réel** :
  - comportement de la NetworkPolicy de k3s avec les sondes de santé : si les pods restent « non prêts »,
    c'est le premier point à regarder.
