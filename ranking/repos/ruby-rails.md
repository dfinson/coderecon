# rails/rails

| Field | Value |
|-------|-------|
| **URL** | https://github.com/rails/rails |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Large (multi-team project) |
| **Category** | Full-stack web framework |

## Why this repo

- **No single developer knows it all**: Active Record (ORM, migrations,
  validations), Action Pack (routing, controllers, rendering), Action View
  (templates, helpers, partials), Action Mailer, Action Cable (WebSockets),
  Active Job (background jobs), Active Storage (file uploads), Action Text
  (rich text), Active Support (core extensions) — each a substantial,
  independently complex framework.
- **Well-structured**: Each framework is its own gem directory with independent
  `lib/`, tests, and gemspec. Clear separation — you can use Active Record
  without Action Pack. Internal structure within each gem follows consistent
  patterns.
- **Rich history**: 90K+ commits, 20+ years of development, 5K+ contributors.
  The definitive Ruby project. Every type of PR pattern imaginable.
- **Permissive**: MIT license.

## Structure overview

```
.
├── activerecord/            # ORM framework
│   └── lib/active_record/
│       ├── base.rb          # Model base class
│       ├── relation/        # Query builder (chainable scopes)
│       ├── connection_adapters/ # Database adapters (PG, MySQL, SQLite)
│       ├── migration/       # Schema migrations
│       ├── associations/    # has_many, belongs_to, etc.
│       └── validations/     # Model validations
├── actionpack/              # Controllers and routing
│   └── lib/action_controller/
│       ├── base.rb          # Controller base class
│       ├── metal/           # Routing, rendering, params
│       └── ...
├── actionview/              # View rendering
│   └── lib/action_view/
│       ├── template/        # Template loading, compilation
│       ├── helpers/         # Built-in view helpers
│       └── renderer.rb      # Rendering pipeline
├── activesupport/           # Core extensions and utilities
├── actionmailer/            # Email sending
├── actioncable/             # WebSockets
├── activejob/               # Background job framework
├── activestorage/           # File upload management
├── actiontext/              # Rich text content
└── actionmailbox/           # Inbound email processing
```

## Scale indicators

- ~4,000 Ruby source files
- ~400K+ lines of code
- Deep module hierarchies (4-5 levels)
- Cross-cutting Active Support used everywhere
