# Parc de serveurs : découverte et ajout de serveurs

La plateforme sait **trouver les serveurs vides** parmi les adresses que vous déclarez, et **transformer un
serveur vide en nœud applicatif** : il rejoint le cluster k3s, puis reçoit supervision, modèle IA, agent de
réparation et sauvegardes. Elle ne touche jamais un serveur sans votre confirmation.

```
infra/parc.yml (adresses autorisées)
      │  toutes les heures, et à la demande : bash deploy.sh --decouvrir
      ▼
Découverte (LECTURE SEULE) : port SSH -> clé du parc -> sonde -> classement
      │  vide · supervision · controle · applicatif · occupe · incompatible · cle_refusee · inaccessible
      ▼
Grafana « Parc » + alerte ServeurVideDetecte
      │  bash deploy.sh --ajouter <adresse>   (confirmation, empreinte SSH affichée)
      ▼
bash deploy.sh : le serveur rejoint k3s (même version que le cluster), puis il est équipé comme les autres
```

## 1. Déclarer les adresses : `infra/parc.yml`

```yaml
plages_autorisees:
  - 57.129.142.176/32
  - 54.37.13.100/32
  - 51.38.0.10/32        # nouveau VPS
```

Seules ces adresses sont contactées. Chez un hébergeur comme OVH, les autres appartiennent à d'autres clients,
et les sonder serait interdit. Les garde-fous : IPv4 seulement, aucun réseau plus large qu'un /22, et 1 024
adresses au plus. Un réseau privé (vRack OVH) se déclare comme une plage, par exemple `10.0.0.0/24`.

## 2. Découvrir : `bash deploy.sh --decouvrir`

Sur le serveur de supervision. Exemple réel, au 16/10/2026 :

```
ADRESSE          INVENTAIRE   ÉTAT          DÉTAIL
57.129.142.176   app-1        controle      nœud de contrôle k3s v1.36.4+k3s1
54.37.13.100     supervision  supervision   supervision de la plateforme (mon-prometheus)
```

| État | Signification | Suite |
|---|---|---|
| `vide` | Debian 12 ou 13, au moins 1,8 Go de RAM et 10 Go libres, sudo sans mot de passe, aucun conteneur, aucun service à part SSH | **proposé comme nœud** |
| `supervision`, `controle`, `applicatif` | Déjà dans le parc | — |
| `occupe` | Autre chose y tourne (conteneurs, ports en écoute) | **jamais touché** |
| `incompatible` | Système, RAM, disque ou sudo | — |
| `cle_refusee` | Port SSH ouvert, mais la clé du parc n'est pas encore posée | `--ajouter`, puis `--cles` |
| `inaccessible` | Port SSH fermé | — |

La sonde (`infra/outils/sonde_serveur.sh`) ne fait que lire : nom, système, CPU, RAM, disque, Docker et ses
conteneurs, k3s, ports en écoute. Elle n'installe, ne modifie et ne démarre rien. La même découverte tourne
**toutes les heures** (minuterie `pfe-parc`, en lecture seule elle aussi). Son résultat apparaît sur la page
d'accueil Grafana (section « Parc ») et déclenche l'alerte **ServeurVideDetecte**.

## 3. Ajouter un serveur vide

Prérequis pour un VPS neuf : Debian 12 ou 13, compte `debian` avec sudo sans mot de passe (c'est le cas des VPS
OVH), et son adresse ajoutée à `plages_autorisees` (commit et push).

Sur le serveur de supervision :

```bash
bash deploy.sh --ajouter 51.38.0.10
```

La commande affiche l'état, la ligne d'inventaire proposée (`app-2 ansible_host=51.38.0.10 …`) et
**l'empreinte SSH** du serveur. Comparez cette empreinte avec celle de l'espace client de l'hébergeur avant de
répondre `oui`. La ligne est ajoutée à `~/.pfe-parc/inventaire.ini`, un inventaire propre au serveur de
supervision et hors Git, lu par `deploy.sh` en plus de celui du dépôt.

Ensuite :

```bash
bash deploy.sh --cles        # seulement pour un VPS neuf : pose la clé du parc (mot de passe demandé une fois)
bash deploy.sh               # le serveur rejoint le cluster puis est équipé
```

Pendant `deploy.sh`, le rôle `k3s_noeud` :
1. **rejoue la sonde** juste avant d'installer quoi que ce soit, et refuse un serveur qui n'est plus vide ;
2. lit la **version exacte de k3s du cluster** et son jeton d'entrée sur le nœud de contrôle (jeton jamais
   affiché) ;
3. vérifie que le port 6443 du nœud de contrôle est joignable ;
4. installe l'agent k3s avec le script officiel **figé sur cette version** ;
5. attend que le nœud soit « Ready ».

Le reste de `deploy.sh` l'équipe ensuite comme les autres serveurs : étiquette `pfe.noeud`, collecteurs, modèle
IA, compte bridé de l'agent, sauvegardes. Le nouveau nœud devient choisissable dans le champ **NOEUDS** de
Jenkins et dans `noeuds:` d'une application.

Pour rendre l'ajout définitif dans le dépôt : copier la ligne de `~/.pfe-parc/inventaire.ini` dans
`infra/ansible/inventory.ini`, puis la retirer du fichier local.

## Réseau entre les nœuds

k3s utilise, entre les nœuds, le port TCP 6443 (API, vers le nœud de contrôle), le port UDP 8472 (réseau des
pods, flannel VXLAN) et le port TCP 10250 (kubelet). Les VPS OVH n'ont pas de pare-feu par défaut. Si un
pare-feu est ajouté, ces ports doivent rester ouverts **entre les nœuds**.

## Vérifications faites

- **Découverte, 22 tests unitaires** : classement des 12 situations, refus des plages trop larges, de l'IPv6
  et d'un utilisateur piégé, limite de 1 024 adresses, noms des nouveaux nœuds, métriques, exploration simulée
  (vide, clé refusée, réponse illisible, port fermé).
- **Sonde réelle** exécutée localement et relue par le classement.
- **Découverte réelle sur les deux serveurs**, en lecture seule : app-1 classé `controle`, la supervision
  classée `supervision`, aucun faux « vide ».
- **Rôle `k3s_noeud`** : refus d'un serveur incompatible **avant toute action**.
- Règles d'alerte du parc : tests unitaires promtool. Syntaxe de `site.yml` avec ansible-core 2.18.
  `--ajouter` : lecture du rapport (vide, occupé, clé refusée, adresse hors plages).
- **Non vérifié** : l'entrée réelle d'un VPS neuf dans le cluster, faute de serveur vide pour l'essai.

## Parc entièrement neuf

Pour installer la plateforme **de zéro** sur des serveurs vides (un nouveau client, un nouvel environnement),
il faut au moins 2 VPS Debian 12 ou 13 : un pour la supervision et un pour le nœud de contrôle. Les rôles sont
attribués automatiquement :

| Serveur | Rôle | Installé |
|---|---|---|
| celui où l'on lance `deploy.sh` | **supervision** | Prometheus, Grafana, moteur IA, agent, sauvegardes |
| le serveur vide qui a **le plus de RAM** | **nœud de contrôle** | k3s serveur, Argo CD (et l'application cobaye), SonarQube et sa base, Vault, **Jenkins configuré par fichier** (identifiants, SonarQube, scanner, les 2 jobs) |
| les autres serveurs vides | **nœuds applicatifs** | agent k3s, collecteurs, sauvegardes |

Les versions installées sont celles du premier parc, relevées le 16/10/2026 et figées dans `group_vars/all.yml`
(`parc_*`) : k3s v1.36.4+k3s1, Argo CD v3.5.2, Jenkins 2.568.2 LTS, SonarQube 26.8 community, Vault 2.2.0.

### Étapes (sur le futur serveur de supervision)

```bash
curl -fsSL https://raw.githubusercontent.com/<compte>/<dépôt>/main/infra/deploy.sh -o deploy.sh
```
```bash
bash deploy.sh --nouveau-parc
```
1. Ce serveur se sonde lui-même : il doit être vide.
2. La découverte explore `plages_autorisees`. Si la clé du parc n'est pas encore posée sur les autres serveurs
   (VPS neufs), un **inventaire provisoire** est écrit. Lancer alors `bash deploy.sh --cles` (le mot de passe est
   demandé une seule fois), puis relancer `bash deploy.sh --nouveau-parc`.
3. Le **plan** s'affiche (rôle et RAM de chaque serveur ; les serveurs occupés sont ignorés) : répondre `oui`.
   Rien n'est encore installé.

```bash
bash deploy.sh
```
Pour le nœud de contrôle à installer, `deploy.sh` demande **une seule fois** le compte et le jeton Docker Hub,
ainsi que le compte et le jeton GitHub (droit *Contents : Read and write* sur le dépôt). Ces identifiants ne
sont pas affichés et ne sont écrits que sur le nœud de contrôle (`/var/lib/pfe-ci/secrets`, lisibles par
Jenkins seul), jamais dans Git.

Après l'installation :
- Jenkins : `http://<contrôle>:8080/`. Utilisateur `admin` ; mot de passe :
  `sudo cat /var/lib/pfe-ci/secrets/JENKINS_ADMIN_PASSWORD` sur le nœud de contrôle. Les jobs
  `pipeline-app-cobaye` et `plateforme-applications` sont déjà créés.
- Argo CD : `https://<contrôle>:32252/`. Utilisateur `admin` ; la commande qui affiche le mot de passe est
  donnée par `deploy.sh`.
- **Il reste une action dans GitHub** : un webhook `http://<contrôle>:8080/github-webhook/` (événement *push*),
  pour que chaque push lance le pipeline.

### Pour un autre compte (l'encadrant installe la plateforme chez lui)

`bash deploy.sh --nouveau-parc` demande d'abord : « Installer la plateforme pour VOTRE compte GitHub et Docker
Hub ? ». Répondre `oui`, puis donner :

| Demandé | Pour quoi faire |
|---|---|
| un **jeton GitHub classique** (github.com/settings/tokens), avec les droits `repo` et `admin:repo_hook` | créer sa copie du dépôt, y pousser, et créer le webhook vers son Jenkins |
| son **compte et un jeton Docker Hub** | publier ses images |
| les **adresses de ses serveurs**, celui-ci compris | les seules que la découverte explorera |

La plateforme fait alors tout seule :
1. **Elle copie le dépôt dans son compte** (fork, par l'API GitHub). Si la copie existe déjà, elle la réutilise.
2. **Elle adapte cette copie à lui** (`infra/outils/personnaliser.sh`) : son espace Docker Hub dans les deux
   pipelines, ses serveurs dans `infra/parc.yml`, l'adresse de son dépôt (Argo CD, Jenkins, liens). Elle envoie
   cette personnalisation dans sa copie, comme un commit.
3. Elle **découvre ses serveurs**, puis attribue les rôles (voir plus haut).
4. Au `bash deploy.sh` suivant, elle **installe tout depuis sa copie** : les identifiants ne sont pas
   redemandés, et le **webhook GitHub est créé automatiquement**. Aucune action manuelle dans GitHub.

Les applications tournent tout de suite avec les images publiques du premier parc. Leur premier build les
remplace par des images construites dans son compte Docker Hub. Les identifiants sont conservés dans
`~/.pfe-secrets/parc-neuf.env` sur son serveur de supervision, lisibles par ce compte seul, et jamais dans Git.
L'ancien dossier du dépôt est gardé (`~/pfe-devsecops.premier-parc-<date>`) : rien n'est supprimé.

### Garde-fous

- **Un nœud de contrôle existant n'est jamais touché** : si k3s y est déjà actif, toute la partie
  « nœud de contrôle » est sautée. C'est le cas du premier parc : son Jenkins installé à la main et son Argo CD
  restent tels quels.
- Le futur nœud de contrôle est **sondé juste avant l'installation**. S'il n'est pas vide, rien n'est installé,
  ni k3s, ni Argo CD, ni Jenkins.
- L'inventaire d'un parc neuf (`~/.pfe-parc/inventaire.ini`, marqué `# parc-neuf`) **remplace** celui du dépôt,
  qui décrit le premier parc : impossible de mélanger les deux parcs.
- Argo CD est installé côté serveur (`--server-side`) : la définition ApplicationSet est bien présente, ce qui
  évite le problème vécu sur le premier parc.
- Jenkins vient d'une **image construite par la plateforme** (`roles/ci/files/jenkins/Dockerfile`) : version
  figée, clients Docker et Trivy inclus (archive Trivy vérifiée par son empreinte), sans assistant ni réglage
  manuel. Le mot de passe admin de SonarQube est remplacé par un mot de passe aléatoire, et son jeton d'analyse
  est créé pour Jenkins. Tous les secrets générés sont conservés sur le nœud de contrôle.

### Vérifié / non vérifié

- **Vérifié en local** :
  - attribution des rôles (6 tests) : le plus de RAM devient le nœud de contrôle, les serveurs occupés et ce
    serveur lui-même ne sont jamais pris, et l'étape « clés à poser d'abord » est gérée ;
  - inventaire généré relu par ansible-core 2.18, avec contrôle, supervision et adresses bien résolus ;
  - `jenkins.yaml` valide : 3 identifiants, 2 jobs, aucun secret en clair, inscription fermée, lecture anonyme
    interdite ;
  - Application Argo CD de la cobaye conforme au schéma ;
  - **refus d'installer sur un nœud de contrôle non vide** ;
  - syntaxe de `site.yml` et de `deploy.sh`.
- **Non vérifié** : une installation réelle de bout en bout (il faut 2 VPS vides) ; la construction de l'image
  Jenkins et le démarrage de la Configuration as Code (pas de Docker disponible pour l'essai) ; les versions
  exactes des extensions Jenkins, choisies par `jenkins-plugin-cli` au moment de la construction.
