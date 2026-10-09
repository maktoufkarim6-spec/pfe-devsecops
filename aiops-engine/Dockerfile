# ---------- base : dépendances de production ----------
FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# ---------- test : le build échoue si un test ou le backtest échoue ----------
FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY aiops ./aiops
COPY tests ./tests
RUN python -m pytest -q -p no:cacheprovider tests && touch /tests-passed

# ---------- runtime : image finale sans outils de test ----------
FROM base AS runtime
COPY --from=test /tests-passed /tests-passed
COPY aiops ./aiops
RUN useradd --system --uid 10001 --no-create-home aiops && mkdir /data && chown 10001:10001 /data
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=4)"
CMD ["python", "-m", "aiops"]
