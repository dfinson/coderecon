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

---

## Tasks

8 tasks (3 narrow, 3 medium, 2 wide) for the Go terminal UI framework.

## Narrow

### N1: Fix cursor blink timer leaking on program exit

When a program using a blinking cursor exits, the blink timer goroutine
continues running briefly after the program's `Quit` command is processed.
This causes occasional panics when the timer tries to send on a closed
channel. Fix the shutdown sequence to cancel the blink timer before
closing the program's message channel.

### N2: Add `WindowSizeMsg` debouncing

When the terminal is resized by dragging, the program receives a flood
of `WindowSizeMsg` messages that cause expensive re-renders. Add
configurable debouncing to window size events so the program only
receives the final size after the resize gesture completes. Default
debounce interval should be 50ms.

### N3: Fix mouse click coordinates wrong with alt screen scrollback

When using the alt screen buffer with mouse support enabled, mouse click
coordinates are reported relative to the visible viewport but the program
interprets them relative to the scrollback buffer origin. Fix the mouse
coordinate translation to account for the alt screen offset.

## Medium

### M1: Implement focus management for composite models

Add a focus management system for applications that compose multiple
interactive sub-models (e.g., a form with multiple text inputs). Provide
`FocusNext`, `FocusPrev` commands and a `Focusable` interface that
models can implement. Tab and Shift-Tab should navigate between focusable
sub-models. Track which sub-model has focus and only forward key events
to the focused model.

### M2: Add built-in animation support

Implement an animation framework that allows smooth transitions between
visual states. Add an `Animate(from, to, duration, easing)` command that
emits interpolated `TickMsg` values over time. Include common easing
functions (linear, ease-in, ease-out, ease-in-out). Support concurrent
animations with independent timers. The animation system should be
composable with existing `tea.Cmd` patterns.

### M3: Implement terminal capability detection

Add automatic detection of terminal capabilities (true color support,
Unicode width, mouse protocol, alt screen, bracketed paste) at program
startup. Surface the detected capabilities through a `TerminalInfo`
message sent before `Init`. Use the capabilities to automatically
select the best mouse protocol and color mode instead of requiring
manual configuration.

## Wide

### W1: Add accessible screen reader output

Implement a screen reader compatibility layer that runs alongside the
visual renderer. Maintain a logical model of the UI content and emit
OS-specific accessibility announcements when the content changes.
Support NVDA/JAWS on Windows via MSAA, VoiceOver on macOS via
NSAccessibility, and Orca on Linux via AT-SPI. Add `aria-live`
equivalent semantics for dynamic content regions.

### W2: Implement a layout engine inspired by CSS Flexbox

Add a layout system that handles the spatial arrangement of sub-models
within a container. Support flex direction (row/column), wrapping,
alignment (start/center/end/space-between), and flex-grow/shrink
properties. The layout engine should run during the View phase, taking
the terminal width/height and producing positioned render regions
for each child. Integrate with the existing Lipgloss styling.

### N4: Fix `tea.Quit` not flushing pending output before exit

When a model returns `tea.Quit`, any pending `View()` output in the
renderer buffer is discarded. The last frame before exit is never
displayed. Fix the shutdown sequence to flush the renderer buffer
before closing the program.

### N5: Fix alt screen restoration leaving terminal in raw mode

When the program panics while the alt screen is active, the terminal
is left in raw mode because the cleanup handler doesn't run. Add a
panic recovery handler that restores the terminal state before
re-panicking.

### N6: Add `tea.Tick` with custom ID for cancellable timers

The current `tea.Tick` returns a `Cmd` with no way to cancel it. Add
`tea.TickWithID(duration, id)` that returns a cancellable tick. Add
`tea.CancelTick(id)` to cancel a pending tick by ID.

### N7: Fix pasted text containing escape sequences interpreted as key events

When bracketed paste mode is enabled and the pasted text contains
ANSI escape sequences (e.g., colored text from a terminal), the paste
handler interprets them as key events instead of literal characters.
Fix the paste handler to treat all characters within the bracketed
paste markers as literal text.

### N8: Add `tea.Println` for output outside the TUI rendering area

There's no way to print non-TUI output (debug messages, log lines)
without corrupting the rendered view. Add `tea.Println(msg)` that
outputs text above the TUI render area, scrolling the output region
while keeping the TUI fixed at the bottom.

### N9: Fix `tea.EnterAltScreen` not working when called from `Init()`

When a model returns `tea.EnterAltScreen` from `Init()`, the alt
screen is not entered because the renderer hasn't started yet. The
command is processed before the terminal is ready. Fix the startup
sequence to defer alt screen commands until after renderer initialization.

### N10: Fix mouse wheel events reporting wrong direction on macOS Terminal.app

On macOS Terminal.app, mouse wheel up/down events are reported with
inverted direction compared to iTerm2 and other terminals. The mouse
event parser uses the wrong bit for direction detection in the SGR
mouse protocol. Fix the mouse parser to handle Terminal.app's encoding.

### M4: Implement text input component with history

Add a reusable `textinput.Model` with command history (up/down arrow
to navigate previous inputs), history persistence to disk, history
search (Ctrl-R fuzzy search), and max history length configuration.
Support multi-line input with Shift-Enter for newlines.

### M5: Add table component with sorting and filtering

Implement a `table.Model` component for rendering tabular data with
column headers, sortable columns (click header to sort), filterable
rows (type to filter), scrollable body, resizable columns, and
customizable cell rendering. Support keyboard navigation between cells.

### M6: Implement progress bar with ETA estimation

Add a `progress.Model` component that shows a progress bar with
percentage, elapsed time, ETA (estimated time of arrival), and
throughput rate. Support configurable bar characters, color gradients,
and indeterminate mode (pulsing animation for unknown total).

### M7: Add file browser component

Implement a `filebrowser.Model` for navigating the filesystem.
Show directories and files with icons/indicators, support directory
traversal (Enter to open, Backspace to go up), file type filtering,
hidden file toggling, and filepath output on selection. Support
both single file and multi-file selection modes.

### M8: Implement modal dialog system

Add a modal overlay system where a `modal.Model` renders on top of
the underlying view. Support configurable positioning (center, top,
bottom), backdrop dimming, focus trapping (key events only go to the
modal), stacked modals, and dismiss on Escape. The modal should work
with any inner `tea.Model`.

### M9: Add form component with validation

Implement a `form.Model` that composes multiple input fields (text,
select, checkbox, date) into a form with tab navigation between fields,
per-field validation with inline error messages, submit/cancel actions,
and dirty-state tracking. Support required fields and custom validators.

### M10: Implement split-pane layout with drag-to-resize

Add a `split.Model` that divides the terminal into two panes
(horizontal or vertical) with a draggable divider. Support minimum
pane sizes, keyboard-based resize (Alt+arrows), collapsed pane state,
and nested splits. Each pane renders an independent `tea.Model`.

### W3: Implement remote TUI serving over SSH

Add the ability to serve a Bubbletea application over SSH using the
Wish library integration. An SSH server hosts the TUI, and each SSH
connection gets its own program instance. Support per-session state,
shared state between sessions, terminal capability negotiation per
client, and graceful disconnection handling. Changes span the program
runner, renderer, input handling, and add an SSH server module.

### W4: Add application state persistence and restore

Implement state snapshot and restore for Bubbletea applications. On
exit, serialize the model state to disk. On restart, restore from the
saved state. Support configurable state serialization (JSON, msgpack),
selective state persistence (mark which fields to persist via tags),
and state migration between versions. Changes span the model
interface, program lifecycle, and add a persistence module.

### W5: Implement headless testing framework

Add a testing framework for Bubbletea applications that runs programs
without a real terminal. Provide `teatest.New(model)` that creates a
headless program, `Send(msg)` for injecting messages, `WaitFor(condition)`
for assertion, and `Output()` for capturing rendered frames. Support
simulating key input sequences, mouse events, and window resize.
Changes span the program runner, renderer, and add a test module.

### W6: Add theme system with runtime switching

Implement a theming system that decouples visual styling from model
logic. Define themes as structured style collections (colors, borders,
padding). Support runtime theme switching (light/dark mode toggle),
theme inheritance (base theme + overrides), and theme loading from
TOML/YAML config files. Integrate with Lipgloss for style application.
Changes span all components, rendering, and add a theme module.

### W7: Implement multi-window TUI framework

Add support for running multiple independent TUI windows within a
single program. Each window has its own model, update loop, and render
area. Windows can be tiled (non-overlapping layout) or floating
(overlapping with z-order). Support window creation, destruction,
focus switching, and inter-window messaging. This requires a window
manager, layout coordinator, and input routing layer.

### W8: Add internationalization support

Implement i18n for Bubbletea components. Support locale-aware string
formatting (dates, numbers, currencies), right-to-left text rendering
for Arabic/Hebrew, locale-based keyboard shortcuts, and translation
loading from message files. All built-in components (spinner, progress,
text input) should respect the active locale. Changes span the
renderer, all built-in components, and add a locale module.

### W9: Implement terminal multiplexer integration

Add integration with tmux/screen that allows Bubbletea programs to
manage terminal multiplexer sessions. Create and switch between tmux
panes from within the TUI, run commands in multiplexer panes, capture
pane output, and display multiplexer status. Support both tmux and
screen protocols. Changes span the program runner, add a multiplexer
abstraction, and a command execution module.

### W10: Add plugin system for extensible components

Implement a plugin architecture that allows third-party Go packages to
register custom components, key bindings, and commands with a
Bubbletea application. Plugins declare their models, init commands,
and key maps through a registration interface. Support plugin lifecycle
management, configuration, and sandboxed message handling. Changes span
the program runner, key handling, model composition, and add a plugin
registry.
