import logging
import time

from prometheus_client import start_http_server

from . import __version__
from .config import Config
from .engine import Engine
from .features import parse_targets
from .prom import PromClient


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("aiops")
    cfg = Config.from_env()
    targets = parse_targets(cfg.targets)
    start_http_server(cfg.listen_port)
    log.info("aiops-engine %s démarré, Prometheus=%s, nœuds=%s", __version__, cfg.prom_url,
             ", ".join(f"{t.name}{' (application)' if t.app else ''}" for t in targets))
    prom = PromClient(cfg.prom_url)
    engines = [Engine(cfg, prom, target=t) for t in targets]
    while True:
        started = time.time()
        for engine in engines:
            engine.run_once()  # chaque moteur gère ses propres erreurs : un nœud en panne n'arrête pas les autres
        time.sleep(max(1.0, cfg.score_interval - (time.time() - started)))


if __name__ == "__main__":
    main()
