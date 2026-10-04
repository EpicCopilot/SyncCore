# SyncCore

**SyncCore** is a Python/PySide6 desktop application that keeps a local SQLite representation of an AniList anime list synchronized with the upstream AniList GraphQL API.

The project is intentionally built as a single maintainable application rather than a collection of services. Its engineering focus is reliable synchronization: authentication, extraction, normalization, reconciliation, idempotent persistence, transactional updates, background execution, testing, and observability.

## Architecture

```text
                         AniList GraphQL API
                                  |
                                  v
                         +------------------+
                         |  AniListClient   |
                         +--------+---------+
                                  |
                                  v
                         +------------------+
                         |   SyncService    |
                         +--------+---------+
                                  |
                                  v
                         +------------------+
                         | Reconciliation   |
                         | / Data Mapping   |
                         +--------+---------+
                                  |
                                  v
                         +------------------+
                         | AnimeRepository  |
                         +--------+---------+
                                  |
                                  v
                              SQLite
                                  ^
                                  |
                         +--------+---------+
                         |     PySide6      |
                         |       UI         |
                         +------------------+
                                  ^
                                  |
                              QThread

OAuth2 authentication and configuration are cross-cutting concerns.
```

## Engineering features

- OAuth2 Authorization Code authentication with persistent local token storage
- AniList GraphQL integration with explicit request timeouts
- Bounded retries for transient API/network failures
- Response normalization and status/progress reconciliation
- Idempotent SQLite UPSERTs using AniList IDs as external keys
- Full upstream reconciliation: stale AniList-linked records are removed after a successful fetch
- Manual/local records (`anilist_id IS NULL`) are preserved during reconciliation
- Atomic synchronization: current records and stale-record cleanup commit as one transaction
- SQLite WAL mode, busy timeout, foreign-key enforcement, constraints, and indexes
- Thread-safe repository usage through operation-scoped database connections
- Background synchronization with QThread and Qt signals
- Rotating application logs without credential/token logging
- Schema migration support for the legacy `episodes` model and SQLite `user_version`
- Automated tests for models, persistence, reconciliation, idempotency, transaction safety, and thread safety
- GitHub Actions CI that runs the test suite on pushes and pull requests

## Synchronization model

SyncCore treats AniList as the source of truth for AniList-linked records.

```text
Fetch current AniList list
          |
          v
Validate + normalize
          |
          v
Map status/progress
          |
          v
Atomic database transaction
     /              \
    v                v
 UPSERT current    Remove linked rows
 records           missing upstream
     \              /
      v            v
       Commit together
```

A manual record is represented by `anilist_id = NULL` and is not removed by the AniList reconciliation process.

If the API request fails, the transaction is never started, so a failed network request cannot cause stale records to be deleted.

## Data model

The main `anime` table contains:

- `id` — local database identifier
- `anilist_id` — unique upstream identifier; NULL for manual records
- `name`
- `seasons`
- `total_episodes` — nullable when AniList has no known total
- `watched_episodes`
- `status`
- `rating`
- `thumbnail`
- `updated_at`

Unknown totals are stored as `NULL`, not as a magic value such as `999`.

## Authentication

Create an AniList OAuth application and configure the redirect URI to match the value registered with AniList exactly. Put the credentials in `.env` based on `.env.example`.

Tokens are stored locally under `data/token.json` and are excluded from Git. OAuth callback state is validated before the authorization code is accepted. The UI also provides a manual reconnect flow for revoked/invalid sessions. Never commit `.env`, OAuth credentials, access tokens, or a database containing personal account data.

If credentials or tokens from an old local copy were ever exposed, rotate/revoke them before publishing the repository.

## Setup

### Requirements

- Python 3.11+
- A configured AniList OAuth application

### Windows

```text
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, configure the AniList credentials, then run:

```text
python -m anime_tracker.main
```

### Tests

```text
pytest -q
```

The current release includes 20 automated tests covering persistence, migration, reconciliation, synchronization, transaction safety, validation, and thread-safe repository usage.

The Add Anime dialog retrieves AniList cover art asynchronously for a non-blocking preview. The cover URL is stored as an internal source reference rather than exposed as a manual input field; this keeps the UI focused on the data the user actually controls.

## Project structure

```text
SyncCore/
├── .github/workflows/ci.yml
├── anime_tracker/
│   ├── anilist_client.py
│   ├── auth.py
│   ├── config.py
│   ├── exceptions.py
│   ├── main.py
│   ├── models.py
│   ├── reconciliation.py
│   ├── repository.py
│   ├── sync_service.py
│   ├── ui.py
│   └── worker.py
├── tests/
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
└── README.md
```

## Production-quality scope

For this application, production quality means reliable behavior for a local desktop synchronization client: clear boundaries, safe persistence, transactional reconciliation, failure handling, background execution, logging, automated verification, configuration hygiene, and reproducible CI.

The project deliberately does not introduce PostgreSQL, Redis, Kafka, Kubernetes, or microservices merely for technology keywords. Those would be appropriate only if SyncCore's requirements changed into a multi-user/cloud service.
