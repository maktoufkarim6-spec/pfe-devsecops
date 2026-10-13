"""python -m agent              : démarre l'agent
   python -m agent journal [N]  : affiche les N dernières décisions
   python -m agent tester       : teste la chaîne SSH vers chaque nœud (action « status », sans effet)
   python -m agent reprendre    : lève une auto-suspension (après analyse des échecs)"""
import logging
import sys
import threading
import time

from . import __version__
from .config import Config
from .executor import SshExecutor
from .guards import Guards
from .journal import Journal
from .metrics import Metrics
from .prom import Context, PromClient
from .server import make_handler, serve
from .worker import Agent


def read_token(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def show_journal(cfg, n):
    rows = Journal(cfg.data_dir).tail(n)
    if not rows:
        print("Journal vide : aucune décision pour l'instant.")
    for r in rows:
        extra = f" -> {r['verification']}" if r["type"] == "verification" else f" : {r['detail']}"
        print(f"{r['horodatage']}  [{r['mode']}] {r['noeud']:<10} {r['alerte']:<18} {r['action']:<28} {r['decision']}{extra}")


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("agent")
    cfg = Config.from_env()
    cmd = argv[1] if len(argv) > 1 else "run"

    if cmd == "journal":
        return show_journal(cfg, int(argv[2]) if len(argv) > 2 else 20)
    if cmd == "reprendre":
        Guards(cfg).resume()
        print("Suspension levée (prise en compte au prochain redémarrage de l'agent : docker restart mon-remediation).")
        return
    executor = SshExecutor(cfg)
    if cmd == "tester":
        for node in cfg.nodes:
            res = executor.execute(node, "status")
            print(f"{node.name:<12} {'OK ' if res.code == 0 else 'ÉCHEC'} {res.output}")
        return

    token = read_token(cfg.token_file)
    if not token:
        log.error("Jeton du webhook absent (%s) : les notifications seront toutes refusées", cfg.token_file)
    metrics = Metrics()
    metrics.set("pfe_agent_info", 1, version=__version__, mode=cfg.mode)
    agent = Agent(cfg, executor, Context(PromClient(cfg.prom_url)), Guards(cfg), Journal(cfg.data_dir), metrics)
    worker = threading.Thread(target=agent.run_forever, name="travailleur", daemon=True)
    worker.start()

    def healthy():
        beat = metrics.get("pfe_agent_worker_heartbeat_timestamp_seconds")
        return worker.is_alive() and (beat == 0 or time.time() - beat < 120 + cfg.ssh_timeout * 2)

    log.info("Agent de remédiation %s démarré en mode %s, nœuds : %s, nettoyage disque : %s",
             __version__, cfg.mode.upper(), ", ".join(n.name for n in cfg.nodes),
             "autorisé" if cfg.disk_cleanup else "désactivé")
    serve(cfg.listen_port, make_handler(agent, token, metrics, healthy)).serve_forever()


if __name__ == "__main__":
    main(sys.argv)
