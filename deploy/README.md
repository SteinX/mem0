# Self-hosted release stack

This template preserves the Core, Sidecar, Dashboard and MCP routing used by the
self-hosted stack. It targets Core `v2.2.1-steinx.1`, Sidecar and Dashboard
`v0.3.11`, and MCP `0.1.5`. The Sidecar Dashboard build uses this Core release as
its baseline and applies its checked-in overlay.

Copy `.env.example` to a private `.env`, replace every credential placeholder,
and set the host bind paths to existing data directories. Compose bind paths
belong to the Docker daemon host, while build contexts belong to the machine
running Compose. The PostgreSQL initialization script lives at
`server/init-db.sh` in this repository. An existing deployment keeps its saved
model configuration, including its embedding URL and vector dimensions; a fresh
deployment must configure these through the API before writing memories.

The `agent-shared` network is external and must already exist. Review exposed
ports and use a TLS reverse proxy for remote clients. Build the ingress locally
from its pinned Nginx image, then use the published application images:

```sh
docker compose --env-file .env -f docker-compose.unraid.yaml config --quiet
docker compose --env-file .env -f docker-compose.unraid.yaml build mem0-ingress
docker compose --env-file .env -f docker-compose.unraid.yaml up -d
```

For an existing stack, take database and configuration backups first, retain the
previous image digests, and upgrade one service at a time. Core runs Alembic on
startup; Sidecar migrates its database to `0010_add_recovery_scan_index`. The
MCP image and Compose service use SIGINT for shutdown. Nginx resolves container
names again after replacements and strips external caller-context headers.

The `tools` profile includes the authenticated ingress canary. Its credential
must be a dedicated client key; operator and legacy rollback keys stay private.
PostgreSQL/pgvector major-version or extension rollback requires database
restoration and reconciliation of writes made after the backup.

The container templates do not alter an existing embedding service. The tested
embedding stack uses TEI `1.9.4` with BGE-M3 revision
`5617a9f61b028005a4858fdac845db406aefb181` and 1024 dimensions; PostgreSQL uses
17.11 with pgvector 0.8.6. Set `MEM0_POSTGRES_IMAGE` to a verified immutable
image reference before upgrading an existing database.

## Publishing the server image

In `SteinX/mem0`, publishing a GitHub Release with a version tag such as
`v2.2.1-steinx.1` automatically runs `Publish Mem0 Server Image`. It builds
the exact tagged commit and publishes `ghcr.io/steinx/mem0:<tag>` and a
`sha-<short-commit>` tag. Stable releases also update `latest`; prereleases
do not. Saving a draft or pushing a Git tag alone does not publish an image.
The upstream package Release Router remains restricted to `mem0ai/mem0`.

To retry publication without recreating a Release, run the workflow manually
from `main` and supply the existing Git tag:

```sh
gh workflow run publish-server-image.yml --repo SteinX/mem0 --ref main \
  -f image_tag=v2.2.1-steinx.1 -f push_latest=false
```

Set `push_latest=true` only when intentionally promoting a manual build.
The workflow checks the tag and release commit before obtaining GHCR credentials.
