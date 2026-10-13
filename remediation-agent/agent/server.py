"""Point d'entrée HTTP : /alert (webhook Alertmanager, jeton obligatoire), /metrics, /healthz."""
import hmac
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("agent")
MAX_BODY = 1_000_000


def make_handler(agent, token, metrics, healthy):
    class Handler(BaseHTTPRequestHandler):
        server_version = "pfe-agent"

        def log_message(self, fmt, *args):   # journal HTTP silencieux (Prometheus interroge toutes les 15 s)
            pass

        def _send(self, code, body, ctype="text/plain; charset=utf-8"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/metrics":
                self._send(200, metrics.render(), "text/plain; version=0.0.4; charset=utf-8")
            elif self.path == "/healthz":
                ok = healthy()
                self._send(200 if ok else 503, "ok\n" if ok else "travailleur bloqué\n")
            else:
                self._send(404, "introuvable\n")

        def do_POST(self):
            if self.path != "/alert":
                return self._send(404, "introuvable\n")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                metrics.inc("pfe_agent_webhooks_total", status="invalide")
                self.close_connection = True
                return self._send(400, "requête invalide : taille invalide\n")
            # Corps lu avant toute réponse : un refus ne coupe jamais la connexion en pleine émission
            body = self.rfile.read(length)
            auth = self.headers.get("Authorization", "")
            if not token or not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
                metrics.inc("pfe_agent_webhooks_total", status="refuse")
                log.warning("Notification refusée : jeton absent ou invalide (%s)", self.client_address[0])
                return self._send(401, "jeton invalide\n")
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
                    raise ValueError("format Alertmanager attendu")
            except ValueError as exc:
                metrics.inc("pfe_agent_webhooks_total", status="invalide")
                return self._send(400, f"requête invalide : {exc}\n")
            n = agent.submit(payload)
            metrics.inc("pfe_agent_webhooks_total", status="accepte")
            self._send(202, f"{n} alerte(s) en file\n")

    return Handler


def serve(port, handler):
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    httpd.daemon_threads = True
    return httpd
