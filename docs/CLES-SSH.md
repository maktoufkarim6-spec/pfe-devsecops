# Connexion par clé SSH sur tout le parc

Objectif : plus aucun mot de passe SSH. Les milliers de tentatives par mot de passe reçues chaque jour
deviennent inutiles, et Ansible joint tous les serveurs sans rien demander (base de l'automatisation du parc).

Deux sortes de clés sont autorisées sur le compte `debian` de chaque serveur :

| Clé | Où est la partie privée | Sert à |
|---|---|---|
| Clé du parc `pfe_parc` | serveur de supervision, `~/.ssh/pfe_parc` (créée automatiquement) | Ansible, `deploy.sh` |
| Clé de votre PC | votre PC, `C:\Users\<vous>\.ssh\id_ed25519` | vous connecter depuis le PC |

Les clés privées ne quittent jamais leur machine et ne sont jamais dans Git.

## Procédure (une seule fois, dans cet ordre)

### 1. Sur votre PC (PowerShell) : créer la clé du PC

```powershell
ssh-keygen -t ed25519
```
Appuyer sur Entrée aux questions (une phrase de passe est possible, elle protège la clé si le PC est volé).
Afficher la clé **publique**, à copier à l'étape 2 :
```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub
```

### 2. Sur le serveur de supervision : poser les clés

```bash
ssh debian@54.37.13.100
```
```bash
curl -fsSL https://raw.githubusercontent.com/maktoufkarim6-spec/pfe-devsecops/main/infra/deploy.sh -o deploy.sh
```
```bash
bash deploy.sh --cles
```
Coller la clé publique du PC quand elle est demandée, répondre `oui`, puis donner le mot de passe SSH
(c'est la dernière fois). Les mots de passe restent acceptés à ce stade : rien ne peut être bloqué.

### 3. Sur votre PC : vérifier, serveur par serveur

Dans une **nouvelle** fenêtre PowerShell :
```powershell
ssh debian@54.37.13.100
```
```powershell
ssh debian@57.129.142.176
```
Chaque connexion doit s'ouvrir **sans demander de mot de passe**. Si un mot de passe est demandé, s'arrêter
là et ne pas faire l'étape 4.

### 4. Sur le serveur de supervision : refuser les mots de passe

```bash
bash deploy.sh --fermer-mots-de-passe
```
Taper `FERMER`. Garder la session ouverte jusqu'à la fin. Le résultat indique pour chaque serveur
« connexion par clé seulement » ou « mots de passe CONSERVÉS » (avec la raison).

Ensuite, `bash deploy.sh` ne demande plus de mot de passe SSH.

## Garde-fous

- **Deux temps** : les clés sont posées et vérifiées avant toute fermeture.
- **Aucune fermeture** s'il n'y a pas de clé de PC enregistrée (sinon vous perdriez l'accès depuis le PC).
- **Aucune fermeture** sur un serveur où la clé du parc n'est pas acceptée (vérifiée par une vraie connexion).
- **Configuration sshd validée** (`sshd -t`) avant rechargement ; en cas d'erreur, retour automatique à
  l'ancienne configuration. Rechargement sans couper les sessions ouvertes.
- **Rien n'est retiré** : les clés déjà présentes sur les serveurs sont conservées.
- Connexion `root` par mot de passe refusée ; `root` par clé reste possible (`PermitRootLogin prohibit-password`).

## Ajouter un PC (ou un collègue)

Relancer `bash deploy.sh --cles` et coller la nouvelle clé publique. Les clés enregistrées sont dans
`~/.pfe-secrets/cles-admin.pub` sur le serveur de supervision.

## Ajouter un serveur au parc

Sur un VPS neuf, la clé du parc n'est pas encore installée : `deploy.sh` le voit et redemande le mot de
passe SSH une fois. Lancer `bash deploy.sh --cles` (pose la clé sur le nouveau serveur), puis
`bash deploy.sh --fermer-mots-de-passe`.

## En cas de problème

**Accès SSH perdu** : console KVM d'OVH (Espace client → VPS → *KVM*), se connecter avec le mot de passe
du compte, puis :
```bash
sudo rm /etc/ssh/sshd_config.d/00-pfe-parc.conf && sudo systemctl reload ssh
```
Les mots de passe SSH sont de nouveau acceptés.

**Clé du PC perdue ou PC changé** : depuis un serveur encore accessible ou par la console KVM, ajouter la
nouvelle clé publique dans `~/.ssh/authorized_keys` du compte `debian`.
