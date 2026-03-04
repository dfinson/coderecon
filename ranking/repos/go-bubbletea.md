# charmbracelet/bubbletea

| Field | Value |
|-------|-------|
| **URL** | https://github.com/charmbracelet/bubbletea |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Small (focused library) |
| **Category** | Terminal UI framework |

## Why this repo

- **Single-purpose**: Elm Architecture-inspired TUI framework for Go.
  Core loop (Init, Update, View) is simple and self-contained. One developer
  can hold the entire codebase in their head.
- **Well-structured**: Clean package layout with the core tea package plus
  supporting modules for input handling, rendering, and key mapping. No deep
  nesting.
- **Rich history**: 2K+ commits, active development, widely adopted (30K+
  stars). Part of the Charm ecosystem but standalone.
- **Permissive**: MIT license.

## Structure overview

```
.
├── tea.go               # Core Program, Model, Cmd types
├── commands.go          # Built-in commands (Quit, Batch, Sequence)
├── renderer.go          # Terminal renderer
├── screen.go            # Screen management (alt screen, mouse)
├── signals.go           # OS signal handling
├── key.go               # Key event types
├── mouse.go             # Mouse event types
├── input.go             # Input reading and parsing
├── cursor/              # Cursor model/component
├── standard_renderer.go # Standard rendering strategy
└── options.go           # Program options
```

## Scale indicators

- ~30 Go source files
- ~8K lines of code
- Flat package structure
- Minimal dependencies
