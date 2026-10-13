from agent.config import Config, Node
from agent.executor import Result

NODES = [Node("app-1", "10.0.0.2", app=True), Node("monitoring", "host.docker.internal", monitoring=True)]


def make_cfg(tmp_path, **kw):
    cfg = Config(data_dir=str(tmp_path), nodes=list(NODES), **kw)
    cfg.validate()
    return cfg


def alert(name, node="app-1", status="firing", fp="f1", starts="2026-10-13T10:00:00Z", **labels):
    lab = {"alertname": name, **labels}
    if node:
        lab["node"] = node
    return {"status": status, "labels": lab, "fingerprint": fp, "startsAt": starts}


class FakeExecutor:
    def __init__(self, code=0, output="ok"):
        self.calls = []
        self.code = code
        self.output = output

    def execute(self, node, action, arg=""):
        self.calls.append((node.name, action, arg))
        return Result(self.code, self.output, 0.1)


class FakeContext:
    def __init__(self, feature=None, maintenance=False, firing=False):
        self.feature = feature
        self.maintenance = maintenance
        self.firing = firing

    def top_feature(self, node):
        return self.feature

    def in_maintenance(self, node):
        return self.maintenance

    def still_firing(self, alertname, node):
        return self.firing


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s
