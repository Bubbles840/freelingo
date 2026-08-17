#!/bin/sh
# Fork: push locally-built images to GHCR so the server can pull instead of
# building from source. Requires: docker login ghcr.io -u Bubbles840 (PAT
# with write:packages as the password).
set -e
docker push ghcr.io/bubbles840/freelingo-backend:fork
docker push ghcr.io/bubbles840/freelingo-frontend:fork
echo "Pushed. On the server: docker compose -f /opt/yams/docker-compose.custom.yaml pull freelingo-backend freelingo-frontend && docker compose -f /opt/yams/docker-compose.custom.yaml up -d freelingo-backend freelingo-frontend"
