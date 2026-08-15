# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
ARG PYTHON_IMAGE=python:3.12-slim@sha256:dd29372629eeba2dd003fd9e9d35a5b8236c44727875a0364254b5127af88e65
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
    && groupadd --gid 10001 repotrials \
    && useradd --create-home --uid 10001 --gid 10001 repotrials \
    && install -d --owner=10001 --group=10001 /workspace

WORKDIR /opt/repotrials
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

ENV HOME=/home/repotrials

USER 10001:10001
WORKDIR /workspace
ENTRYPOINT ["repotrials"]
CMD ["--help"]
