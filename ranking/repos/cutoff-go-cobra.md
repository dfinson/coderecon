# spf13/cobra

| Field | Value |
|-------|-------|
| **URL** | https://github.com/spf13/cobra |
| **License** | Apache-2.0 |
| **Language** | Go |
| **Scale** | Medium |
| **Category** | CLI framework |
| **Set** | Cutoff |

## Why this repo

- **Well-structured**: Clean single-package layout with clear per-concern
  files — the `Command` struct and lifecycle (`command.go`), argument
  validators (`args.go`), shell completion for bash/zsh/fish/PowerShell
  (`completions.go`, `*_completions.go`), flag groups (`flag_groups.go`),
  and a standalone CLI generator tool (`cobra/cmd/`). Each concern is
  isolated in its own file with minimal cross-coupling.
- **Rich history**: 38K+ stars, 2.8K+ commits. One of the most popular
  Go libraries; powers kubectl, Hugo, gh, and hundreds of other CLIs.
  PRs cover completion edge cases, flag handling, help formatting, and
  argument validation.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
cobra/
├── cobra.go               # Package-level helpers, EnablePrefixMatching, MousetrapHelpText
├── command.go             # Command struct — Execute, ExecuteC, RunE, flags, help, usage
├── args.go                # Argument validators — NoArgs, ExactArgs, MinimumNArgs, etc
├── completions.go         # Core completion engine — ValidArgsFunction, RegisterFlagCompletionFunc
├── bash_completions.go    # Legacy bash completion script generation
├── zsh_completions.go     # Zsh completion script generation
├── fish_completions.go    # Fish completion script generation
├── powershell_completions.go # PowerShell completion script generation
├── active_help.go         # ActiveHelp — dynamic help messages during completion
├── flag_groups.go         # MarkFlagsRequiredTogether, MarkFlagsMutuallyExclusive, MarkFlagsOneRequired
├── command_notwin.go      # Unix-specific command helpers
├── command_win.go         # Windows mousetrap integration
├── doc/
│   ├── man_docs.go        # Man page generation from command tree
│   ├── md_docs.go         # Markdown documentation generation
│   ├── rest_docs.go       # reStructuredText documentation generation
│   ├── yaml_docs.go       # YAML-structured docs generation
│   └── util.go            # Doc generation utilities
├── cobra/
│   └── cmd/
│       ├── root.go        # cobra CLI generator — root command
│       ├── init.go        # cobra init — scaffold a new CLI project
│       ├── add.go         # cobra add — add a new subcommand
│       └── helpers.go     # Project scaffolding utilities
└── site/
    └── content/           # Documentation site (Hugo-based)
        ├── user_guide.md  # User guide — commands, flags, args, completions
        └── ...
```

## Scale indicators

- ~20 Go source files (library core)
- ~12K lines of code
- Flat structure (single package + doc/ subpackage + cobra/ CLI tool)
- Minimal dependencies (only `pflag` for POSIX flag parsing)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Command.Find not respecting EnablePrefixMatching for deeply nested subcommands

When `EnablePrefixMatching` is set to `true`, prefix matching works for
top-level subcommands but fails for commands nested more than two levels
deep. The `Command.Find()` method in `command.go` does not propagate
prefix matching through the recursive `innerFind` traversal. Fix the
traversal to apply prefix comparison at every level of the command tree.

### N2: Fix ExactArgs validator not producing a clear error when too many arguments are given

When `ExactArgs(2)` is used and 5 arguments are supplied, the error
message says "accepts 2 arg(s), received 5" without listing the extra
arguments. Fix the `ExactArgs` validator in `args.go` to include the
unexpected argument values in the error message, making debugging easier
for end users.

### N3: Fix fish completion not escaping descriptions containing single quotes

When a completion value has a description containing a single quote
(e.g., "don't use this"), the fish completion script produces a syntax
error because the description is inserted unescaped into the `complete`
command. Fix the fish completion writer in `fish_completions.go` to
escape single quotes in descriptions.

### N4: Fix MarkFlagsMutuallyExclusive not checking persistent flags from parent commands

`MarkFlagsMutuallyExclusive("format", "json")` only checks flags defined
on the current command, not persistent flags inherited from a parent
command. When one flag is local and the other is persistent, the
mutual exclusion check is silently skipped. Fix the flag group validation
in `flag_groups.go` to resolve flags from the full flag set including
persistent flags.

### N5: Fix ActiveHelp output including trailing whitespace that breaks shell parsing

`ActiveHelp` messages emitted during completion end with trailing
whitespace or newline characters that cause some shells to display
garbled output. Fix the `activeHelpMarker` formatting in `active_help.go`
to trim trailing whitespace before encoding the help message into the
completion output.

### N6: Fix Command.UsageString panicking when called before Execute

Calling `cmd.UsageString()` before `cmd.Execute()` panics with a nil
pointer dereference because `cmd.ctx` is not initialized until
execution begins. Fix `UsageString()` in `command.go` to create a
default context if one does not exist yet, matching the behavior of
`HelpString()`.

### N7: Fix man page doc generator not rendering Deprecated field

When a command has its `Deprecated` field set, the man page generator
in `doc/man_docs.go` omits the deprecation notice entirely. The
`GenManTree` function should include a "DEPRECATED" section in the
generated man page with the deprecation message.

### N8: Fix bash completion v2 not handling flag values that start with a dash

When a flag value legitimately starts with `-` (e.g., `--offset -10`),
the v2 bash completion script interprets it as another flag and offers
flag completions instead of leaving it as a value. Fix the bash
completion logic in `completions.go` to detect when a flag is expecting
a value and suppress flag-name completions.

### N9: Fix Command.DebugFlags outputting to stdout instead of stderr

`Command.DebugFlags()` writes flag debug information to `os.Stdout`,
which pollutes program output and breaks piping. Change the output
target to `cmd.ErrOrStderr()` in `command.go` so debug output goes
to stderr, consistent with other diagnostic methods.

### N10: Fix yaml_docs.go not including the Aliases field in generated YAML

The YAML doc generator produces structured command documentation but
omits the `Aliases` field, making it impossible to discover command
aliases from generated docs. Fix `GenYamlCustom` in `doc/yaml_docs.go`
to include an `aliases` key listing all command aliases.

## Medium

### M1: Implement ordered subcommand groups in help output

Add support for organizing subcommands into named groups that appear
as sections in the help output (e.g., "Management Commands",
"Build Commands"). Requires a `Command.AddGroup()` method, a `Group`
field on `Command` to associate it with a group, changes to
`Command.UsageString()` for grouped display, and integration with
shell completions to preserve group ordering in completion results.

### M2: Add flag value completion for custom types

Implement a `RegisterFlagCompletionFunc` variant that supports
type-aware completion: enum-like types return their valid values,
file-path flags use filesystem completion, and duration flags suggest
common formats. Requires changes to the completion engine in
`completions.go`, per-type completion providers, integration with
`pflag.Value` interface detection, and updates to all shell-specific
completion scripts to pass the type hint through.

### M3: Implement persistent pre-run hooks with error short-circuiting

Add `PersistentPreRunE` chaining so that when a parent command and
a child command both define `PersistentPreRunE`, both run in order
(parent first) instead of the child silently overriding the parent.
Requires changes to the command execution pipeline in `command.go`,
a chain-builder for hooks, proper error propagation to stop execution
if any hook fails, and test coverage for multi-level nesting.

### M4: Add context.Context integration for command lifecycle

Integrate Go's `context.Context` into the command lifecycle: allow
setting a base context on the root command, propagate it through
`PersistentPreRun` / `RunE` / `PostRunE`, and support cancellation
(e.g., on SIGINT). Requires changes to `Command.ExecuteC()` for context
creation, signal handling setup, context passing through hooks, and
updates to the `cobra` CLI generator templates to include context-aware
scaffolding.

### M5: Implement command suggestions with Levenshtein distance and aliases

Enhance the "unknown command" error to suggest the closest matching
commands using Levenshtein distance, considering both primary names
and aliases. Show up to 3 suggestions sorted by distance. Requires a
distance function, integration with `Command.Find()` for candidate
collection, formatting changes to the error message in `command.go`,
and respect for `DisableSuggestions` and `SuggestionsMinimumDistance`
settings.

### M6: Add shell completion testing utilities

Implement a `CompletionTestRunner` that programmatically invokes the
completion engine and returns structured results (completions with
descriptions and directives). Support testing flag-value completions,
positional argument completions, and ActiveHelp messages. Requires
a new test-helper API, internal refactoring of the completion path
to separate output formatting from completion logic, and example
tests demonstrating usage.

### M7: Implement automatic environment variable binding for flags

Add `Command.AutomaticEnvBindings(prefix string)` that automatically
binds each flag to an environment variable derived from its name
(`PREFIX_FLAG_NAME`). Support nested command prefixes (e.g.,
`APP_SERVER_PORT` for `app server --port`). Requires flag traversal
logic, env-var lookup during flag defaulting in `command.go`, help text
updates to show the variable name, and interaction with viper-based
config if present.

### M8: Add markdown table-of-contents generation for multi-level command trees

Extend `doc/md_docs.go` to generate an index page with a table of
contents for deep command hierarchies. The TOC should be a nested list
matching the command tree structure with links to per-command pages.
Requires recursive command tree walking, link generation relative to
output directory structure, front-matter support for static site
generators, and handling of hidden/deprecated commands.

### M9: Implement flag value validation hooks

Add a `RegisterFlagValidationFunc(flagName, func(val string) error)`
that runs after flag parsing but before `PreRunE`. Validations should
run for all flags with registered validators, collect all errors, and
report them as a grouped message. Requires a validation registry on
`Command`, integration into the execution pipeline in `command.go`,
clear error formatting, and interaction with required/mutually-exclusive
flag groups.

### M10: Add support for command-level exit codes

Implement a mechanism for commands to specify distinct exit codes via
a `ExitCode` field on a returned error or a `CommandError` type. The
root `Execute()` method should translate these into `os.Exit()` calls.
Support a configurable exit-code map for common error types (usage
error, runtime error, permission denied). Requires a custom error
type, exit handling in `command.go`, integration with `PersistentPostRun`
for cleanup, and documentation updates.

## Wide

### W1: Implement a plugin system for dynamically discovered subcommands

Add a plugin architecture that discovers subcommands from: (a) binaries
on `$PATH` matching a naming convention (e.g., `app-plugin-*`), (b) a
plugin directory, and (c) Go plugin `.so` files. Support plugin metadata
(version, description), conflict detection when multiple plugins provide
the same command, and a built-in `plugin list` command. Changes span
command resolution in `command.go`, a new plugin discovery module,
completion support for plugin-provided commands, help formatting for
plugin sections, and the CLI generator for plugin scaffolding.

### W2: Add comprehensive shell completion rewrite with dynamic descriptions

Rewrite the completion system to support per-value descriptions,
grouped completions (separating subcommands from aliases from flags),
custom sorting, and filtering of already-used flags. Unify the four
shell-specific generators behind a common `CompletionProvider`
interface that each shell implements. Changes span `completions.go`,
all four shell-specific files, `active_help.go`, the command traversal
logic, and the flag group system for exclusion-aware completion.

### W3: Implement declarative command configuration from YAML/TOML

Add a configuration-driven command definition system that generates
cobra command trees from YAML or TOML files without Go code. Support
subcommand hierarchies, flag definitions with types and defaults,
argument validators, help text, and callback references to Go functions
via reflection or registry. Include a validation tool that checks config
files for errors. Changes span the core `Command` builder, a new config
parser module, flag type resolution, the CLI generator (`cobra/cmd/`),
and documentation.

### W4: Implement a middleware/interceptor system for the command pipeline

Add a middleware layer that wraps the command execution pipeline with
composable interceptors: logging, metrics, tracing, error recovery,
and timeout enforcement. Middleware should have access to the `Command`,
flags, args, and context. Support ordering via priority and
per-command middleware stacks. Changes span the execution pipeline in
`command.go`, a new middleware registry, context propagation, error
handling, the hook system (`PreRun`/`PostRun`), and the CLI generator
for middleware-aware scaffolding.

### W5: Add internationalization support for help text and error messages

Implement i18n across cobra's user-facing output. All error messages,
help text labels ("Usage:", "Available Commands:", "Flags:"), suggestion
messages, completion instructions, and ActiveHelp output should be
translatable via Go's `x/text/message` package. Support user-provided
translations for command descriptions and flag usage strings. Include
locale detection from `$LANG`/`$LC_ALL`. Changes span `command.go`,
`args.go`, `completions.go`, `active_help.go`, `flag_groups.go`, the
doc generators, and the CLI generator templates.

### W6: Implement interactive command wizards with prompt-driven input

Add a `Command.Interactive` mode that, when invoked without required
flags, presents an interactive step-by-step prompt for each parameter.
Support text input for strings, selection menus for enum-like flags,
file-path browsing for path flags, confirmation prompts for boolean
flags, and back-navigation. Show a summary before execution. Changes
span the command execution pipeline, flag type inspection, a new
interactive UI module, the testing framework for wizard simulation,
and integration with completion for wizard-assisted input.

### W7: Add end-to-end testing framework for CLI applications

Implement a `CLITestHarness` that compiles and runs a cobra CLI in a
subprocess, captures stdout/stderr/exit-code, and provides assertion
helpers. Support snapshot testing for help output, table-driven
test generation, environment variable fixtures, temporary filesystem
setup, and stdin injection for interactive commands. Include a test
generator in the `cobra` CLI tool. Changes span a new testing package,
the CLI generator for test scaffolding, doc generators for snapshot
baselines, and example tests for common patterns.

### W8: Implement hierarchical configuration with viper deep integration

Build a first-class viper integration layer that automatically binds
cobra flags to viper keys, supports config-file discovery per
subcommand level, merges config from multiple sources (flags > env >
config file > defaults) with clear precedence, and exposes a unified
`GetConfig()` API on `Command`. Changes span flag binding in
`command.go`, a new config integration module, the CLI generator for
config-aware scaffolding, help text showing config sources, and
environment variable documentation in the doc generators.

### W9: Implement command versioning and deprecation lifecycle

Add a versioning and deprecation system: commands can declare `Since`,
`DeprecatedSince`, and `RemovedIn` version fields. At runtime, compare
against the application version to emit warnings for deprecated commands
and hard errors for removed ones. Generate migration guides listing
deprecated commands and their replacements. Changes span command
metadata in `command.go`, execution-time version checking, help and
doc formatting, the CLI generator for versioned scaffolding, and a
new migration-guide generator in `doc/`.

### W10: Add OpenTelemetry tracing and metrics across the command lifecycle

Instrument the full command lifecycle with OpenTelemetry: trace spans
for command resolution, flag parsing, hook execution, and command
invocation. Emit metrics for execution duration, error counts, and
flag usage frequency. Support configurable exporters (stdout, OTLP,
Jaeger). Changes span the execution pipeline in `command.go`, context
propagation, a new telemetry module, the hook system, error handling,
CLI generator templates for instrumented scaffolding, and optional
dependency management.
