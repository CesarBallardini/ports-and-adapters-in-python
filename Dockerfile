# Base image for the devcontainer: the toolchain, not the project.
#
# The project source is bind-mounted by the devcontainer and installed by
# `make install` on create, so the image deliberately does not COPY the source
# -- which also avoids needing the git history that uv-dynamic-versioning reads
# to derive the version.
FROM python:3.14-slim

# uv comes from its own published image so the version here, the one in
# .github/actions/install-uv and the one developers run locally stay in step.
COPY --from=ghcr.io/astral-sh/uv:0.11.10 /uv /uvx /usr/local/bin/

# git and make for the workflow itself; nodejs and golang so the pre-commit
# hooks written in those languages (markdown-link-check, gitleaks) do not have
# to bootstrap a toolchain on every fresh container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      golang-go \
      make \
      nodejs \
      npm \
 && rm -rf /var/lib/apt/lists/*

# Copy rather than hardlink: the workspace is a bind mount, on a different
# filesystem from the image layers.
ENV UV_LINK_MODE=copy

WORKDIR /workspace
