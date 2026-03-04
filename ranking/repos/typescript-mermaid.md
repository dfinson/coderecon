# mermaid-js/mermaid

| Field | Value |
|-------|-------|
| **URL** | https://github.com/mermaid-js/mermaid |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Medium (multi-module project) |
| **Category** | Diagram generation engine |

## Why this repo

- **Multi-module with clear boundaries**: Each diagram type (flowchart,
  sequence, class, state, gantt, pie, ER, git, etc.) is its own subsystem
  with parser, renderer, and data model. Shared infrastructure for layout,
  theming, and configuration sits alongside.
- **Well-structured**: Source under `packages/mermaid/src/` with per-diagram
  directories. Clear separation between parsing (JISON/Langium grammars),
  data models, and D3-based rendering.
- **Rich history**: 4K+ commits, active development, widely used (75K+ stars).
  Regular feature additions (new diagram types) provide varied PR patterns.
- **Permissive**: MIT license.

## Structure overview

```
packages/mermaid/src/
├── diagrams/
│   ├── flowchart/       # Flowchart parser, renderer, styles
│   ├── sequence/        # Sequence diagram subsystem
│   ├── class/           # Class diagram subsystem
│   ├── state/           # State diagram subsystem
│   ├── gantt/           # Gantt chart subsystem
│   ├── pie/             # Pie chart subsystem
│   ├── er/              # ER diagram subsystem
│   ├── git/             # Git graph diagram
│   └── ...              # ~15 diagram types total
├── rendering/           # D3-based rendering infrastructure
├── config/              # Configuration and theming
├── utils/               # Shared utilities
├── mermaidAPI.ts        # Public API
└── mermaid.ts           # Entry point
```

## Scale indicators

- ~200 TypeScript source files
- ~50K lines of code
- Clear per-diagram subsystem boundaries
- Moderate depth (2-3 levels within each diagram type)
