# Supervision multi-nœuds

> Réutiliser la plateforme pour une autre application : `docs/REUTILISER.md` (un seul bloc de variables).

```
        Serveur de supervision (54.37.13.100)
  Prometheus · Alertmanager · Grafana · moteur AIOps (un modèle par nœud)
                         │  lit les collecteurs (ports 9100, 8081, 8082)
         ┌───────────────┼─────────────────────┐
     monitoring        app-1                nouveau nœud
   (lui-même)      57.129.142.176           (une ligne dans l'inventaire)
   node-exporter   node-exporter, cAdvisor, cAdvisor-k3s
   cAdvisor        Jenkins, k3s (application)
```

Tout est décrit en Ansible (`ansible/`) : le parc est reproductible, et ajouter un nœud revient à
ajouter une ligne dans `ansible/inventory.ini` puis à relancer le déploiement.

## Déployer (sur le serveur de supervision)

```bash
curl -fsSL https://raw.githubusercontent.com/maktoufkarim6-spec/pfe-devsecops/main/infra/deploy.sh -o deploy.sh && bash deploy.sh
```

Le script installe Ansible dans un environnement isolé, affiche l'état du serveur, demande
(facultatif) l'adresse qui recevra les alertes par e-mail, puis lance le déploiement. Ansible demande
le mot de passe SSH puis celui de sudo ; rien n'est enregistré.

Ce qui est déployé :

| Où | Quoi |
|---|---|
| Serveur de supervision | Prometheus (15 jours d'historique), Alertmanager, Grafana avec le tableau de bord « PFE - Supervision multi-nœuds + AIOps » et un sélecteur de nœud, moteur AIOps v3 |
| Chaque nœud | node-exporter, cAdvisor, cAdvisor-k3s (nœud applicatif), pare-feu des collecteurs, compte bridé de l'agent, sauvegardes croisées |
| Serveur de supervision (suite) | Agent de remédiation `mon-remediation` (mode conseil au premier déploiement) |

## Sécurité

- **Seul Grafana est exposé** (port 3000, authentification). Prometheus, Alertmanager et le moteur
  n'écoutent que sur la machine (`127.0.0.1`) ; accès : `ssh -L 9090:localhost:9090 debian@54.37.13.100`.
- **Collecteurs des nœuds** : une table nftables dédiée (`pfe_exporters`) ne laisse passer que le
  serveur de supervision, la machine elle-même, les réseaux Docker et les pods k3s. Elle ne touche pas
  aux règles de Docker et de k3s.
- **Secrets** : mot de passe admin Grafana généré au premier déploiement (`~/.pfe-secrets/grafana-admin`
  sur le serveur de supervision), mot de passe SMTP lu au lancement ; fichiers en lecture seule pour
  leur seul service, jamais dans Git.
- Moteur AIOps : non-root, système de fichiers en lecture seule, capabilities retirées, mémoire limitée.

## Alertes

| Alerte | Déclenchement |
|---|---|
| NoeudInjoignable | collecteur du nœud muet depuis 2 min (masque les autres alertes du nœud) |
| CpuEleve / RamElevee / DisquePlein | seuils statiques par nœud |
| AnomalieAIOps | anomalie du moteur depuis 1 min, avec la variable responsable dans le message |
| DisquePleinPrevu | prévision Theil-Sen fiable (R² ≥ 0,7) de saturation sous 24 h |
| AIOpsAveugle / MoteurAIOpsArrete | le moteur ne voit plus les données ou ne répond plus |

E-mail (Gmail) : relancer `deploy.sh` et renseigner l'adresse ; il faut un **mot de passe d'application**
Google (compte avec validation en deux étapes).

## Fenêtres de maintenance

Un nœud annonce sa maintenance en écrivant, dans `/var/lib/node_exporter/textfile/maintenance.prom` :

```
pfe_maintenance_until_seconds <horodatage de fin>
```

Le pipeline Jenkins le fait automatiquement (30 min) pendant chaque build. Le moteur AIOps ignore une
annonce de plus de 30 min : une maintenance ne peut jamais devenir un silence permanent.

## Après le déploiement

1. Ouvrir Grafana, vérifier les deux nœuds dans le sélecteur et le panneau « Collecteurs joignables ».
2. Le moteur AIOps apprend chaque nœud dès qu'il dispose de 2 h d'historique.
3. Libérer la RAM du VPS applicatif en arrêtant l'ancienne supervision (rien n'est supprimé) :

```bash
cd ~/pfe-devsecops/infra/ansible && ~/.pfe-ansible/bin/ansible-playbook retirer-ancienne-supervision.yml --ask-pass --ask-become-pass
```

## Interface graphique

Grafana (http://54.37.13.100:3000), dossier **PFE** :

- **PFE - Supervision multi-nœuds + AIOps** : métriques des serveurs et de l'application, détections du moteur AIOps.
- **PFE - Accueil** (page par défaut) : état des services déclarés et **des services découverts automatiquement**,
  serveurs, avis de l'IA, agent, sauvegardes, alertes, résultats de l'évaluation, liens vers chaque outil.
- **PFE - Agent de remédiation et sauvegardes** : mode de l'agent, réparations, échecs, décisions par alerte,
  dernières actions ; état des sauvegardes de chaque serveur (dernière sauvegarde, copie distante, taille, test
  de restauration), jeux reçus et leurs empreintes ; alertes en cours sur tout le parc.

## Agent de remédiation (auto-réparation)

Conteneur `mon-remediation` sur le serveur de supervision. Alertmanager lui transmet chaque alerte ; il choisit
une action dans une **liste fermée** d'après le diagnostic du moteur AIOps, l'exécute sur le nœud par SSH, vérifie
5 min après que l'alerte a disparu et journalise tout. Détails, politique et garde-fous : `remediation-agent/README.md`.

- **Démarre en mode conseil** (`remediation_mode: conseil`) : il simule et journalise sans rien toucher.
  Après relecture du journal (`docker exec mon-remediation python -m agent journal`), passer à `actif` et relancer `deploy.sh`.
- Sur chaque nœud : compte `pfe-agent` sans mot de passe, clé bridée (commande forcée, adresse source imposée),
  `sudo` limité au script `/usr/local/sbin/pfe-runbook`, qui revérifie chaque demande. Trace : `journalctl -t pfe-runbook`.
- Nettoyage disque automatique **désactivé** (`remediation_disk_cleanup: false`) tant que le propriétaire ne l'a pas accepté.
- Alertes sur l'agent lui-même : `ActionAutomatique` (e-mail à chaque action réelle), `ActionAutomatiqueSansEffet`,
  `AgentRemediationArrete`, `AgentRemediationSuspendu`, `AgentNoeudInaccessible`. L'agent n'agit jamais sur elles.

## Sauvegardes et restauration

Chaque nuit à 2 h 30 (`pfe-backup.timer`), chaque nœud sauvegarde ses données dans `/var/backups/pfe/local/<date>/`
puis copie le jeu sur l'autre serveur (supervision ↔ applicatif). Rattrapage automatique si le serveur était éteint.

| Nœud | Contenu d'un jeu |
|---|---|
| Supervision | Historique Prometheus (instantané cohérent), détections et incidents AIOps + modèles, journal de l'agent, base Grafana, Alertmanager, configuration (sans secrets) |
| Applicatif | Base PostgreSQL `demo` (`pg_dump`, contrôlée par `pg_restore --list`) et comptage des lignes de chaque table |

- **Rotation** : 7 jeux quotidiens + 1 par semaine sur 4 semaines, des deux côtés.
- **Copie protégée** : le compte de réception `pfe-backup` est en **écriture seule, sans suppression** (`rrsync -wo -no-del`),
  clé bridée à l'adresse de l'expéditeur. Un serveur compromis ne peut ni lire ni effacer ses sauvegardes chez l'autre.
- **Intégrité** : empreintes SHA-256 de chaque fichier ; le fichier `SHA256SUMS`, envoyé en dernier, marque un jeu complet.
  Chaque nuit à 4 h 30 (`pfe-backup-check.timer`), les jeux reçus sont vérifiés.
- **Test de restauration hebdomadaire réel** : la base est restaurée dans une base de test et ses comptages de lignes
  comparés à ceux de la sauvegarde ; l'instantané Prometheus est ouvert par `promtool`, la base Grafana vérifiée.
- **Supervision** : métriques `pfe_backup_*` (via node-exporter), panneaux Grafana, alertes `SauvegardeEchouee`,
  `SauvegardeEnRetard`, `CopieDistanteEnRetard`, `SauvegardeRecueCorrompue`, `TestRestaurationEchoue`.
- Pendant une sauvegarde, le nœud annonce 15 min de maintenance (pas de fausse anomalie, pas d'action de l'agent).

Commandes (sur le serveur concerné, en root) :

```bash
sudo systemctl start pfe-backup            # sauvegarde immédiate
sudo pfe-restore liste                     # jeux locaux et reçus
sudo pfe-restore tester                    # test de restauration complet maintenant (ne modifie rien)
sudo pfe-restore extraire <jeu> /root/restauration   # tout décompresser dans un dossier vide
sudo pfe-restore postgres <jeu> --confirmer          # REMPLACE la base demo (une seule transaction)
journalctl -u pfe-backup -u pfe-backup-check         # journaux
```

Remise en place de l'historique Prometheus (serveur de supervision) : `pfe-restore extraire <jeu> /root/r`, puis
`docker stop mon-prometheus`, remplacer le contenu du volume `pfe-prometheus-data` par `/root/r/prometheus/`
(propriétaire `65534`), `docker start mon-prometheus`. Même principe pour Grafana (`grafana.db`, propriétaire `472`).
Si le serveur entier est perdu : réinstaller avec `deploy.sh`, puis restaurer depuis les jeux copiés sur l'autre serveur
(`/var/backups/pfe/recu/<nœud>/`).

Limites : Jenkins (configuration et credentials) et Vault (mode dev, en mémoire) ne sont pas sauvegardés ; les deux
copies restent chez le même hébergeur.

## Vérifications effectuées avant livraison

- `ansible-lint` (profil production) : 0 remarque ; `--syntax-check` des deux playbooks.
- Modèles rendus avec l'inventaire réel ; YAML valide ; 8 règles d'alerte et 38 requêtes du tableau de
  bord validées par un analyseur PromQL ; règles nftables validées par `nft -c`.
- Versions d'images figées vérifiées contre les publications officielles ; `auth_password_file`
  vérifié dans le code source d'Alertmanager v0.27.0.
- Non vérifié ici (pas d'accès aux serveurs) : l'exécution réelle, qui se fait par `deploy.sh`.
