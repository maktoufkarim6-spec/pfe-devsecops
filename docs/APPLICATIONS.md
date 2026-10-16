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

## Créer une application en libre-service (formulaire)

Sans écrire de fichier ni toucher au dépôt : page d'accueil Grafana → **📦 Construire et déployer une autre
application** → Jenkins, puis :

| Champ | Exemple | Obligatoire |
|---|---|---|
| `APPLICATION` | `boutique` (minuscules, chiffres, tirets) | oui |
| `DEPOT` | `https://github.com/compte/boutique.git` (public, avec un Dockerfile) | oui pour une nouvelle application |
| `BRANCHE`, `DOSSIER`, `DOCKERFILE` | `main`, `.`, `Dockerfile` | non (valeurs par défaut) |
| `PORT` | `8080` | non : lu dans le Dockerfile (`EXPOSE`) |
| `SANTE` | `/health` | non : test du port |
| `DOMAINE` | `boutique.monsite.com` | non : `<nom>.<ip>.sslip.io` |
| `BASE` | coché | non : PostgreSQL, variables `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` fournies |
| `SONAR` | `bloquant` | non |

Ensuite :
1. La plateforme **écrit `applications/<nom>/values.yaml`** et le contrôle comme les autres.
2. Elle clone, teste, analyse, construit et scanne l'application.
3. Elle **lit dans l'image le port et l'utilisateur**. Une image qui tourne en root est **refusée**, avec le
   message « ajouter USER 1000 dans le Dockerfile ».
4. Elle publie l'image, puis **commite le dossier de l'application seulement si tout a réussi**.
5. Argo CD la déploie.
6. En moins de 5 minutes, la **resynchronisation automatique** (`pfe-sync`, sur le serveur de supervision)
   rejoue le déploiement. L'application reçoit alors sa sonde web, son modèle IA, sa place dans la liste de
   l'agent, la sauvegarde de sa base et ses secrets, **sans relancer `deploy.sh`**.

`pfe-sync` ne se déclenche que si la liste des applications ou leurs réglages changent : un nouveau build (ligne
`image:`) ne déclenche rien. Il reprend les options du dernier `bash deploy.sh` (par exemple
`-e remediation_mode=actif`) et ne tourne jamais en même temps qu'un `deploy.sh` lancé à la main (verrou). En
cas d'échec, il retente toutes les 5 minutes, et l'alerte `SynchronisationEchouee` part au bout de 15 minutes.
Son journal : `~/.pfe-parc/sync.log`.

## Suivre les déploiements dans Grafana

Grafana a deux pages :

- **PFE - Accueil**, l'essentiel en quelques secondes :
  - un bandeau « Tout fonctionne », ou le nombre de points à regarder ;
  - les boutons Déployer et Ajouter une application ;
  - un tableau avec une ligne par application : état, version en ligne, dernier build, IA ;
  - quatre voyants : serveurs, services, sauvegardes, IA.
- **PFE - Détails**, tout le détail technique : liste des alertes en cours, applications, déploiements,
  services, serveurs, parc, agent, sauvegardes et évaluation. Le lien « Détails techniques » est en haut de
  l'accueil. Les deux pages sont générées par `python infra/outils/generer_accueil.py`.

La section **Déploiements** de la page Détails montre, pour toutes les applications :

| Tuile | Ce qu'elle montre | D'où ça vient |
|---|---|---|
| Version en ligne | l'image qui tourne vraiment pour chaque composant ; vert si toutes les copies sont prêtes, orange si une partie seulement | `pfe-etat-cluster`, chaque minute sur le nœud de contrôle |
| Argo CD | Synced (cluster identique au dépôt) et Healthy (tout démarre) ; bleu pendant une synchronisation | `pfe-etat-cluster` |
| Dernier build Jenkins | réussi, instable (Quality Gate non bloquante) ou échec, avec la version construite ; un clic ouvre le job | fin de chaque build (`post`) des deux pipelines |
| Synchronisation auto | état de `pfe-sync` et date de sa dernière resynchronisation | `pfe-sync` |

`pfe-etat-cluster` ne fait que **lire** Kubernetes (`k3s kubectl get`) ; il n'ouvre aucun port. Alertes associées :
`ApplicationArgoCDDegradee` (application non Synced/Healthy depuis 15 min) et `EtatClusterMuet` (état non relu
depuis 10 min ; journal : `journalctl -u pfe-etat-cluster` sur le nœud de contrôle).

## Construire une application depuis son code source (pipeline commun)

Une application peut utiliser une image déjà publiée, ou être **construite depuis son code source** par le
job Jenkins `plateforme-applications` (`plateforme/Jenkinsfile.applications`). Ce job applique la même chaîne
que l'application cobaye.

```yaml
source:
  depot: https://github.com/compte/mon-appli.git   # https seulement, dépôt public
  branche: main
  sonar: bloquant          # bloquant (défaut) ou informatif (code d'un tiers : affiché sans bloquer)
composants:
  - nom: api
    image: docker.io/kariimm557/mon-appli-api:12-abc1234   # écrite par le pipeline à chaque build
    construire:
      contexte: api            # dossier du dépôt source (défaut .)
      dockerfile: Dockerfile   # relatif au contexte (défaut Dockerfile)
      image: kariimm557/mon-appli-api   # dépôt Docker Hub, sans tag (espace kariimm557 seulement)
      cible_tests: test        # étape du Dockerfile qui lance les tests (facultatif, recommandé)
    port: 5500
    utilisateur: 1000
```

| Étape | Bloquant |
|---|---|
| Contrôle de `values.yaml` (`plateforme/ci/construire.sh verifier`) : adresse https, branche, chemins sans `..`, image dans l'espace autorisé, sans tag | oui |
| Clone de la branche et tag `<build>-<commit>` | — |
| Tests : `docker build --target <cible_tests>` | oui |
| SonarQube, projet `app-<nom>` | selon `source.sonar` |
| Images, scan **Trivy** (faille CRITIQUE corrigeable ; exceptions : `.trivyignore` du dépôt source) | oui |
| Publication Docker Hub, puis écriture de la ligne `image:` (et d'elle seule) dans `applications/<nom>/values.yaml`, commit `[skip ci]` | — |
| Argo CD déploie la nouvelle version | — |

Si une étape échoue, rien n'est publié et la version en ligne reste en place. Toute la logique est dans
`plateforme/ci/construire.sh`, testable sans Jenkins. Le Jenkinsfile ne fait qu'enchaîner les étapes.

**Lancer :** page d'accueil Grafana, lien **📦 Construire et déployer une autre application**, puis le champ
**APPLICATION** (par exemple `demo-podinfo`) et **Build**.

**Création du job (une seule fois)** : Jenkins → *Nouvel élément* → nom `plateforme-applications` → type
*Pipeline* → *Pipeline script from SCM* → Git, dépôt `https://github.com/maktoufkarim6-spec/pfe-devsecops.git`,
branche `*/main`, *Script Path* `plateforme/Jenkinsfile.applications`. Pas de déclencheur : le job se lance à
la main ou depuis Grafana. Il reprend les credentials `dockerhub` et `github-token` et le serveur SonarQube du
pipeline de la cobaye. Le premier build peut échouer avant d'afficher le champ APPLICATION : Jenkins ne lit les
paramètres qu'à l'exécution. Il suffit de le relancer.

Limites : dépôts sources publics seulement (un dépôt privé demandera un credential de lecture) ; images
poussées dans l'espace Docker Hub `kariimm557` ; pas de déclenchement automatique quand le dépôt source change
(webhook à ajouter).

## Ce qui reste à faire (phases suivantes)

- Déclenchement automatique quand le dépôt source d'une application change (webhook) ; dépôts privés.
- **HTTPS** et vrais noms de domaine.

## Vérifications faites

**Pipeline commun** (38 tests, avec `yq` 4.44.3 réel, un vrai dépôt Git local servi en https, et `docker` et
`trivy` simulés) :
- chaîne complète sur une application à 2 composants construits et 1 image publique ;
- Trivy bloquant ;
- **seule la ligne `image:` change**, commentaires conservés, même avec un en-tête de commentaires ;
- mise à jour idempotente ;
- le chart accepte le résultat ;
- 17 refus : http, `..`, option déguisée en branche, contexte absolu, image d'un autre compte ou avec tag,
  étape de tests piégée, tag piégé, Dockerfile absent…


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
