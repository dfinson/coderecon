# go-gitea/gitea

| Field | Value |
|-------|-------|
| **URL** | https://github.com/go-gitea/gitea |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Large (multi-team project) |
| **Category** | Self-hosted Git service |

## Why this repo

- **No single developer knows it all**: Git operations, issue tracker, pull
  requests, code review, CI/CD (Actions), package registry, wiki, OAuth/SSO,
  API (REST + GraphQL-like), webhooks, notifications, admin dashboard, LFS —
  each a deep subsystem with its own models, services, and routes.
- **Well-structured**: Clean Go package layout with `models/`, `services/`,
  `routers/`, `modules/` separation. Models further split by domain
  (issues, repo, user, packages, actions, etc.).
- **Rich history**: 15K+ commits, highly active PR flow, Hacktoberfest
  participation. Feature-rich with varied development patterns.
- **Permissive**: MIT license.

## Structure overview

```
.
├── models/              # Database models and queries
│   ├── issues/          # Issues, labels, milestones, comments
│   ├── repo/            # Repository metadata, stars, forks
│   ├── user/            # User accounts, orgs, teams
│   ├── packages/        # Package registry
│   ├── actions/         # CI/CD actions
│   └── git/             # Git-specific models
├── services/            # Business logic layer
│   ├── repository/      # Repo creation, deletion, transfer
│   ├── issue/           # Issue lifecycle
│   ├── pull/            # PR merge, review, conflicts
│   ├── auth/            # Authentication sources
│   └── webhook/         # Webhook delivery
├── routers/             # HTTP route handlers
│   ├── api/             # REST API v1
│   ├── web/             # Web UI routes
│   └── private/         # Internal API
├── modules/             # Shared infrastructure
│   ├── git/             # Git command interface
│   ├── markup/          # Markdown, org-mode rendering
│   ├── indexer/         # Search indexing (bleve/elasticsearch)
│   └── notification/    # Notification delivery
└── cmd/                 # CLI commands
```

## Scale indicators

- ~1,500 Go source files
- ~300K+ lines of code
- Deep module hierarchies (3-5 levels)
- Cross-cutting auth, notification, indexing concerns
