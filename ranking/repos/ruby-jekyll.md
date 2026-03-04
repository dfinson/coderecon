# jekyll/jekyll

| Field | Value |
|-------|-------|
| **URL** | https://github.com/jekyll/jekyll |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Medium (multi-module project) |
| **Category** | Static site generator |

## Why this repo

- **Multi-module with clear boundaries**: Markdown/Liquid template rendering,
  content pipeline (readers, generators, converters, renderers), plugin system,
  development server, incremental build, configuration management — each a
  distinct subsystem requiring navigation between them.
- **Well-structured**: Source under `lib/jekyll/` with clear per-concern files
  and directories. The content pipeline has explicit phases (read → generate →
  render → write) reflected in the code structure.
- **Rich history**: 12K+ commits, 15+ years of development. One of the most
  widely used static site generators. Varied PR patterns from bug fixes to
  plugin system changes.
- **Permissive**: MIT license.

## Structure overview

```
lib/jekyll/
├── site.rb              # Core Site object (orchestrates pipeline)
├── page.rb              # Page content type
├── document.rb          # Collection document type
├── collection.rb        # Content collections
├── configuration.rb     # Config loading and defaults
├── readers/             # Content readers
│   ├── data_reader.rb   # _data/ directory reader
│   ├── page_reader.rb   # Page file reader
│   └── post_reader.rb   # Blog post reader
├── generators/          # Content generators
│   └── pagination.rb    # Pagination generator
├── converters/          # Format converters
│   ├── markdown.rb      # Markdown → HTML
│   └── identity.rb      # Pass-through
├── renderers/           # Template renderers
├── tags/                # Liquid template tags
├── filters.rb           # Liquid template filters
├── commands/            # CLI commands (build, serve, new)
├── server/              # Development server
└── plugin_manager.rb    # Plugin loading and lifecycle
```

## Scale indicators

- ~100 Ruby source files
- ~20K lines of code
- 2-3 levels of module nesting
- Clear pipeline phase boundaries
