# Supervision multi-nœuds

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
| Chaque nœud | node-exporter, cAdvisor, cAdvisor-k3s (nœud applicatif), pare-feu des collecteurs |

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

## Vérifications effectuées avant livraison

- `ansible-lint` (profil production) : 0 remarque ; `--syntax-check` des deux playbooks.
- Modèles rendus avec l'inventaire réel ; YAML valide ; 8 règles d'alerte et 38 requêtes du tableau de
  bord validées par un analyseur PromQL ; règles nftables validées par `nft -c`.
- Versions d'images figées vérifiées contre les publications officielles ; `auth_password_file`
  vérifié dans le code source d'Alertmanager v0.27.0.
- Non vérifié ici (pas d'accès aux serveurs) : l'exécution réelle, qui se fait par `deploy.sh`.
