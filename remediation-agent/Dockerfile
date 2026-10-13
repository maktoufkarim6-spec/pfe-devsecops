# ---------- base : Python (bibliothèque standard seulement) + client SSH ----------
FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# ---------- test : le build échoue si un test échoue ----------
FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY agent ./agent
COPY tests ./tests
RUN python -m pytest -q -p no:cacheprovider tests && touch /tests-passed

# ---------- runtime : image finale sans outils de test ----------
FROM base AS runtime
COPY --from=test /tests-passed /tests-passed
COPY agent ./agent
# Utilisateur nommé : le client SSH refuse de fonctionner pour un uid sans entrée dans /etc/passwd
RUN useradd --system --uid 10002 --home-dir /tmp --no-create-home pfe-agent && mkdir /data && chown 10002:10002 /data
USER 10002
ENV HOME=/tmp
EXPOSE 9095
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9095/healthz', timeout=4)"
CMD ["python", "-m", "agent"]
