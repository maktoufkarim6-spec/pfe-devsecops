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

L'installation d'un parc vide de zéro (nœud de contrôle : k3s, Argo CD, Jenkins, SonarQube, Vault ; puis
supervision) est l'étape suivante. Aujourd'hui, le nœud de contrôle et la supervision existants sont
installés ; tout nouveau serveur vide les rejoint comme nœud applicatif.
