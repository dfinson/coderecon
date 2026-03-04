# django/django

| Field | Value |
|-------|-------|
| **URL** | https://github.com/django/django |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Large (multi-team project) |
| **Category** | Full-stack web framework |

## Why this repo

- **No single developer knows it all**: ORM, template engine, admin interface,
  auth system, forms framework, middleware, URL routing, migrations, cache
  framework, internationalization, test runner — each a substantial subsystem
  with deep module hierarchies.
- **Well-structured**: Clear top-level package split (`django/db/`, `django/template/`,
  `django/contrib/admin/`, `django/forms/`, etc.). Two decades of consistent
  architectural patterns.
- **Rich history**: 35K+ commits, 15K+ closed PRs, meticulous code review.
  One of the oldest and most actively maintained Python projects.
- **Permissive**: BSD-3-Clause.

## Structure overview

```
django/
├── db/                  # ORM (models, querysets, backends, migrations)
│   ├── models/          # Model layer, fields, managers
│   ├── backends/        # PostgreSQL, MySQL, SQLite, Oracle
│   └── migrations/      # Schema migration engine
├── template/            # Template engine (lexer, parser, loaders, tags)
├── contrib/
│   ├── admin/           # Admin interface (views, widgets, filters)
│   ├── auth/            # Authentication and authorization
│   └── ...              # contenttypes, sessions, staticfiles, etc.
├── forms/               # Form handling, validation, widgets
├── http/                # Request/response, cookies, multipart
├── middleware/           # Security, CSRF, sessions, cache
├── core/                # Management commands, mail, serializers
├── urls/                # URL routing and resolution
├── views/               # Generic views, decorators
└── utils/               # Functional utilities, encoding, dates
```

## Scale indicators

- ~2,500 Python source files
- ~350K+ lines of code
- Deep module hierarchies (3-5 levels)
- Cross-cutting concerns span many subsystems
