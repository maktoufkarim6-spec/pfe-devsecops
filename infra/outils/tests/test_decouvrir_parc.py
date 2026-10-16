"""Tests de la découverte du parc (python -m pytest infra/outils/tests)."""
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decouvrir_parc as d  # noqa: E402

CONF = {"systemes": ["debian 12", "debian 13"], "ram_min_mo": 1800, "disque_libre_min_go": 10,
        "port_ssh": 22, "utilisateur_ssh": "debian"}
VIDE = {"nom_hote": "vps-neuf", "systeme": "debian 13", "cpu": 2, "ram_mo": 3800, "disque_libre_go": 35, "sudo": True,
        "docker": False, "conteneurs": "", "k3s_version": "", "k3s_serveur": False, "k3s_agent": False,
        "pfe_runbook": False, "services_ecoute": "22 53"}


def avec(**kw):
    return {**VIDE, **kw}


@pytest.mark.parametrize("sonde, etat", [
    (VIDE, "vide"),
    (avec(conteneurs="mon-prometheus mon-grafana"), "supervision"),
    (avec(k3s_serveur=True, k3s_version="v1.36.4+k3s1", conteneurs="jenkins"), "controle"),
    (avec(k3s_agent=True), "applicatif"),
    (avec(systeme="ubuntu 24.04"), "incompatible"),
    (avec(ram_mo=1000), "incompatible"),
    (avec(disque_libre_go=4), "incompatible"),
    (avec(sudo=False), "incompatible"),
    (avec(docker=True, conteneurs="wordpress"), "occupe"),            # un site d'un client : jamais touché
    (avec(services_ecoute="22 53 80 3306"), "occupe"),               # services hors conteneurs
    (avec(pfe_runbook=True), "occupe"),
    (avec(docker=True, conteneurs=""), "vide"),                       # Docker installé mais rien ne tourne
])
def test_classement(sonde, etat):
    assert d.classer(sonde, CONF)[0] == etat


def test_raison_lisible():
    assert "3306" in d.classer(avec(services_ecoute="22 3306"), CONF)[1]
    assert "1000 Mo" in d.classer(avec(ram_mo=1000), CONF)[1]


def ecrire(tmp_path, texte):
    p = tmp_path / "parc.yml"
    p.write_text(texte, encoding="utf-8")
    return str(p)


def test_conf_valide(tmp_path):
    c = d.lire_conf(ecrire(tmp_path, "plages_autorisees: [10.0.0.0/30, 1.2.3.4/32]\n"))
    assert d.adresses(c["_reseaux"]) == ["10.0.0.1", "10.0.0.2", "1.2.3.4"]
    assert c["utilisateur_ssh"] == "debian" and c["port_ssh"] == 22


@pytest.mark.parametrize("texte", [
    "plages_autorisees: []\n",                       # rien à explorer
    "plages_autorisees: [10.0.0.0/16]\n",            # balayage trop large
    "plages_autorisees: ['::1/128']\n",              # IPv6 non prévu
    "plages_autorisees: [pas-une-ip]\n",
    "plages_autorisees: [1.2.3.4/32]\nutilisateur_ssh: 'root; rm'\n",
])
def test_conf_refusee(tmp_path, texte):
    with pytest.raises(ValueError):
        c = d.lire_conf(ecrire(tmp_path, texte))
        d.adresses(c["_reseaux"])


def test_jamais_plus_de_1024_adresses(tmp_path):
    c = d.lire_conf(ecrire(tmp_path, "plages_autorisees: [10.0.0.0/22, 10.0.4.0/22]\n"))
    with pytest.raises(ValueError):
        d.adresses(c["_reseaux"])


def test_inventaire_et_noms(tmp_path):
    inv = tmp_path / "inventory.ini"
    inv.write_text("[nodes]\napp-1 ansible_host=1.1.1.1 node_name=app-1\n# app-9 ansible_host=9.9.9.9\n", encoding="utf-8")
    connus = d.inventaire_connu(str(inv), str(tmp_path / "absent.ini"))
    assert connus == {"1.1.1.1": "app-1"}
    res = [{"adresse": "1.1.1.1", "inventaire": "app-1", "etat": "controle"},
           {"adresse": "2.2.2.2", "inventaire": "", "etat": "vide"},
           {"adresse": "3.3.3.3", "inventaire": "", "etat": "occupe"},
           {"adresse": "4.4.4.4", "inventaire": "", "etat": "vide"}]
    assert d.propositions(res, connus) == [
        "app-2 ansible_host=2.2.2.2 node_name=app-2 node_app=true",
        "app-3 ansible_host=4.4.4.4 node_name=app-3 node_app=true"]   # seuls les vides, noms suivants libres
    m = d.metriques(res)
    assert 'pfe_parc_serveur{adresse="3.3.3.3",etat="occupe",inventaire=""} 1' in m
    assert "pfe_parc_serveurs_vides 2" in m


def test_exploration_complete_simulee(tmp_path):
    c = d.lire_conf(ecrire(tmp_path, "plages_autorisees: [10.0.0.1/32, 10.0.0.2/32, 10.0.0.3/32, 10.0.0.4/32]\n"))
    c.update({k: v for k, v in CONF.items() if k not in c})
    reponses = {"10.0.0.1": (0, json.dumps(VIDE), ""),
                "10.0.0.2": (255, "", "debian@10.0.0.2: Permission denied (publickey)."),
                "10.0.0.3": (0, "pas du json", "")}

    def executer(cmd, **kw):
        ip = cmd[-2].split("@")[1]
        assert "BatchMode=yes" in cmd and "PasswordAuthentication=no" in cmd and cmd[-1] == "bash -s"
        assert b"LECTURE SEULE" in kw["input"]            # c'est bien la sonde qui est envoyée
        rc, out, err = reponses[ip]
        return types.SimpleNamespace(returncode=rc, stdout=out.encode(), stderr=err.encode())

    res = d.explorer(c, "/cle", str(tmp_path / "kh"), {}, executer=executer, ouvert=lambda ip, port: ip != "10.0.0.4")
    assert [(r["adresse"], r["etat"]) for r in res] == [
        ("10.0.0.1", "vide"), ("10.0.0.2", "cle_refusee"), ("10.0.0.3", "erreur"), ("10.0.0.4", "inaccessible")]
