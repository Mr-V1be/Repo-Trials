# syntax=docker/dockerfile:1.7
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS runtime

ARG REPOTRIALS_VERSION=0.1.0
LABEL org.opencontainers.image.title="RepoTrials" \
      org.opencontainers.image.description="Private, reproducible coding-agent evaluations from Git history" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/PozziTiv4ik/Repo-Trials" \
      org.opencontainers.image.version="${REPOTRIALS_VERSION}"

RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 repotrials

WORKDIR /opt/repotrials
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

USER repotrials
WORKDIR /workspace
ENTRYPOINT ["repotrials"]
CMD ["--help"]
