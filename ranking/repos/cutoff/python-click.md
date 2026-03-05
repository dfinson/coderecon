# pallets/click

| Field | Value |
|-------|-------|
| **URL** | https://github.com/pallets/click |
| **License** | BSD-3-Clause |
| **Language** | Python |
| **Scale** | Medium |
| **Category** | CLI framework |
| **Set** | Cutoff |

## Why this repo

- **Well-structured**: Clean single-package layout under `click/` with
  clear per-concern modules — command/group hierarchy (`core.py`),
  parameter type system (`types.py`), decorator API (`decorators.py`),
  option parsing (`parser.py`), help formatting (`formatting.py`),
  shell completion (`shell_completion.py`), and test harness
  (`testing.py`). One developer can follow the full call chain from
  decorator to parsed result.
- **Rich history**: 2K+ commits, 15K+ stars. One of the most widely
  used Python CLI libraries. PRs cover parsing edge cases, completion
  improvements, and type system extensions.
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
click/
├── __init__.py            # Public API re-exports
├── core.py                # Command, Group, MultiCommand, Context, BaseCommand
├── types.py               # Type system — INT, FLOAT, STRING, Path, Choice, etc.
├── decorators.py          # @command, @option, @argument, @group, @pass_context
├── parser.py              # Option/argument tokenizer and parser
├── formatting.py          # HelpFormatter for --help output
├── utils.py               # echo, get_terminal_size, launch, open_file
├── exceptions.py          # ClickException, BadParameter, MissingParameter, Abort
├── testing.py             # CliRunner for invoking commands in tests
├── shell_completion.py    # Bash/Zsh/Fish completion generation
├── globals.py             # Thread-local context stack
├── termui.py              # Terminal UI helpers — progress bars, prompts, colors
└── _compat.py             # Python version compatibility shims
```

## Scale indicators

- ~15 Python source files
- ~10K lines of code
- Flat structure (1 level, single package)
- Minimal dependencies (only `colorama` on Windows)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix Choice type not respecting case_sensitive=False in error messages

When a `Choice` type is configured with `case_sensitive=False`, passing
an invalid value produces an error message that lists the original-case
choices, making it unclear that the match is case-insensitive. The error
message should indicate that matching is case-insensitive, and the listed
choices should reflect the normalized comparison. The fix is localized
to the `Choice.fail()` method in the type system module.

### N2: Fix Context.color being ignored when invoking subcommands

When a parent `Context` sets `color=False` to suppress ANSI output, child
contexts created by `Group.invoke()` do not inherit this setting. The
`Context.__init__` method defaults `color` to `None` instead of
inheriting from the parent. Fix the context initialization to propagate
the `color` flag through the context chain.

### N3: Fix echo() silently swallowing UnicodeEncodeError on redirected stdout

When stdout is redirected to a file with a narrow encoding (e.g.,
ASCII), `echo()` silently swallows `UnicodeEncodeError` instead of
raising a clear error or using a replacement strategy. Fix `echo()` in
the utils module to apply `errors='replace'` or `errors='backslashreplace'`
when writing to a non-UTF-8 stream rather than silently dropping output.

### N4: Fix @option with multiple=True and default=None raising TypeError

Declaring `@option('--tag', multiple=True, default=None)` raises a
`TypeError` during parameter processing because the `multiple` code path
assumes the default is always a tuple. Fix the `Option.type_cast_value()`
method to normalize `None` defaults to an empty tuple when `multiple=True`.

### N5: Fix Path type not expanding ~ in file arguments

The `Path` type validates existence and permissions but does not expand
`~` or `~user` prefixes before validation. A user passing `--config
~/myfile.conf` gets a "does not exist" error because the literal `~`
path is checked. Fix the `Path.convert()` method to call
`os.path.expanduser()` before validation.

### N6: Fix HelpFormatter indentation breaking with very long option names

When an option has a very long metavar (e.g., `--output-format FORMAT_STRING`),
the `HelpFormatter.write_dl()` method overflows the column width and
produces misaligned help text. Fix the definition-list writer to wrap
the help text onto the next line when the term exceeds the column width,
matching the behavior of `argparse`.

### N7: Fix IntRange not showing allowed range in error messages

When a value falls outside an `IntRange(min=1, max=100)`, the error
message says the value is "not in the range" but does not display the
actual allowed bounds. Fix the `IntRange.fail()` method to include the
`min` and `max` values in the error message so users know the valid range.

### N8: Fix shell completion not escaping special characters in values

When completing values that contain spaces or shell metacharacters (e.g.,
file paths with spaces), the completion output does not escape them,
causing the shell to misinterpret the completions. Fix the completion
formatting in the shell-completion module to properly quote or escape
values for each supported shell (bash, zsh, fish).

### N9: Fix testing.CliRunner not capturing stderr separately when mix_stderr=False

When `CliRunner` is created with `mix_stderr=False`, the `result.stderr`
attribute is supposed to contain only stderr output, but it also
captures some stdout content due to incorrect stream wiring in the
isolation context manager. Fix the stream separation logic in the
testing module.

### N10: Fix @argument with nargs=-1 not allowing empty input when required=False

An argument with `nargs=-1` and `required=False` still raises a
`MissingParameter` error when no values are provided because the
argument processing does not check the `required` flag for variadic
arguments. Fix the argument resolution in the parser to allow an empty
tuple when the argument is optional.

## Medium

### M1: Implement parameter groups for mutually exclusive options

Add support for declaring groups of options that are mutually exclusive:
if one is set, the others must not be. Requires a `mutually_exclusive()`
constraint that validates after all parameters are resolved, integrates
with the help formatter to display the exclusion relationship, and
produces a clear error message naming the conflicting options. Touches
the parameter processing in the core module, help generation in the
formatter, and validation in the decorators.

### M2: Add typed parameter overloads for @option and @argument

Introduce type-safe overloads for the `@option` and `@argument`
decorators that use `ParamSpec` and `Concatenate` to correctly type
the decorated function's signature. Currently mypy/pyright cannot infer
the types of parameters added by click decorators. Requires changes
to the decorators module, the type system exports, and the public API
type stubs.

### M3: Implement lazy subcommand loading for large CLI applications

Add a `LazyGroup` variant that accepts subcommands as import strings
(e.g., `"myapp.commands.deploy:cli"`) and only imports them when
invoked. Requires changes to the `Group.get_command()` and
`Group.list_commands()` methods, integration with shell completion so
lazy commands appear in completion results, and error handling for
import failures with clear diagnostics.

### M4: Add structured output support for help text

Implement a `--help-format` option that outputs help text as JSON or
YAML instead of plain text. The structured output should include the
command name, parameters with types and defaults, subcommand list, and
description. Requires a new formatter backend alongside `HelpFormatter`,
integration with `Command.format_help()`, and registration of the
format option as a special eager parameter.

### M5: Implement environment variable prefix support for nested command groups

Add support for automatically mapping environment variables to options
using a hierarchical prefix derived from the command path. For example,
a `--port` option on `myapp server start` would read from
`MYAPP_SERVER_START_PORT`. Requires changes to context creation to
track the command path, option resolution to construct the env var
name, and help text generation to display the expected variable name.

### M6: Add retry and timeout decorators for commands that call external services

Implement `@click.retry(max_attempts=3, backoff=1.0)` and
`@click.timeout(seconds=30)` decorators that wrap command callbacks.
Retry should catch configurable exception types and support exponential
backoff. Timeout should use signal-based or thread-based interruption.
Both should integrate with click's exception handling to produce
user-friendly error messages.

### M7: Implement parameter value callbacks with dependency injection

Add a `@option` parameter `inject=True` that resolves the option's
value by calling a provider function with the current `Context` and
other already-resolved parameter values. Requires changes to parameter
resolution order in the core module, cycle detection for dependent
parameters, and integration with the type system for validation of
injected values.

### M8: Add rich help formatting with terminal colors and styles

Implement a `RichHelpFormatter` that replaces `HelpFormatter` and uses
ANSI styles to colorize help output: bold command names, dim metavars,
colored section headings, and syntax-highlighted default values. Support
style customization via a theme dictionary. Requires a new formatter
class, integration with `Context.formatter_class`, and graceful
degradation when color is disabled.

### M9: Implement command aliases with completion support

Add support for command aliases in `Group`: `@cli.command(aliases=['rm', 'del'])`. Aliases should work in invocation, help text
(listed alongside the primary name), and shell completion. Requires
changes to `Group.get_command()` for alias resolution,
`Group.format_commands()` for display, and the completion module for
alias enumeration.

### M10: Add configuration file support as a parameter source

Implement `@click.config_option('--config', format='toml')` that reads
parameter values from a configuration file. Support TOML, JSON, and INI
formats. Config values should have lower precedence than command-line
arguments but higher than defaults. Requires a config file reader, a
custom parameter source integrated into `Context.params` resolution, and
error reporting for malformed config files.

## Wide

### W1: Implement a plugin system for dynamically discovered commands

Add a plugin architecture that discovers and loads commands from entry
points (`click.plugins` group), namespace packages, or a plugin
directory. Support plugin metadata (version, author, description),
dependency ordering between plugins, conflict detection when multiple
plugins provide the same command name, and a `plugin list` built-in
command that displays discovered plugins. Changes span the group/multi-
command hierarchy, context initialization, entry-point scanning, and
shell completion for plugin-provided commands.

### W2: Add async command support throughout the framework

Implement first-class support for `async def` command callbacks.
`@click.command` should detect async functions and run them in an event
loop. `Context.invoke()` should support awaiting async callbacks.
`CliRunner` should handle async commands transparently. Progress bars
and prompts should work within async contexts. Changes span the
command invocation path in `core.py`, the decorator layer, the testing
module, and the terminal UI helpers.

### W3: Implement a declarative command configuration system

Add a YAML/TOML-based command definition format that generates click
command trees without Python code. Support parameter definitions with
types, defaults, and validation; subcommand hierarchies; help text;
and callback references to Python functions. Include a validator that
checks configuration files for errors. Changes span the core module
(a new command builder), the type system (type resolution from strings),
the decorator layer (configuration loading), and a new configuration
parser module.

### W4: Implement comprehensive parameter validation framework

Add a validation system that supports cross-parameter constraints:
`requires` (option A needs option B), `conflicts` (mutually exclusive),
`at_least_one` (one of a set must be provided), and `depends_on` (value
of one option constrains another). Validations should run after all
parameters are resolved, produce grouped error messages listing all
violations, and integrate with help text to display constraints. Changes
span the core module, parameter processing, help formatting, error
handling, and the testing module.

### W5: Add internationalization support for help text and error messages

Implement i18n across click's user-facing output. All error messages,
help text labels ("Usage:", "Options:", "Commands:"), and built-in
strings should be translatable via gettext or a custom catalog. Support
user-provided translations for parameter help text and command
descriptions. Include locale detection from environment variables.
Changes span error messages in exceptions, help formatting, the type
system error paths, the core module's built-in strings, and the
completion module's output.

### W6: Implement a middleware/hook system for command lifecycle

Add pre/post hooks for the command lifecycle: `before_parse`,
`after_parse`, `before_invoke`, `after_invoke`, `on_error`, and
`on_close`. Hooks should support ordering via priority, async hooks,
and access to the context. Support both decorator-based and
programmatic hook registration. Changes span context management, the
command invocation pipeline, the group dispatching logic, error
handling, and the decorator API.

### W7: Implement interactive command wizards

Add `@click.wizard` that presents an interactive step-by-step interface
for complex commands. When invoked without required arguments, the
wizard prompts for each parameter with type-specific UI: text input
for strings, selection menus for `Choice` types, file browser for
`Path` types, and confirmation for flags. Support back-navigation,
validation at each step, and a summary screen before execution. Changes
span the core command invocation, parameter types, the terminal UI
module, the prompt system, and the testing module for wizard simulation.

### W8: Add comprehensive shell completion rewrite

Rewrite the shell completion system to support dynamic completions
based on context: complete option values by calling type-specific
completers (file path completion for `Path`, value enumeration for
`Choice`), complete based on previously supplied options, and support
completion for chained commands. Add completion testing utilities to
`CliRunner`. Changes span the completion module, all parameter types,
the parser, the core command classes, and the testing module.

### W9: Implement a command-line application testing framework

Extend `CliRunner` into a full testing framework: snapshot testing for
help text output, structured result objects with parsed stdout/stderr,
filesystem fixtures for `Path` parameters, environment variable
fixtures, mock input streams for interactive prompts, and assertion
helpers for exit codes and output patterns. Add a pytest plugin that
provides click-specific fixtures. Changes span the testing module,
a new pytest plugin module, the CLI runner isolation logic, and
integration with the exception hierarchy.

### W10: Add automatic man page and documentation generation

Implement `click.docs.generate_man(cli)` that produces roff-formatted
man pages from a click command tree, and `click.docs.generate_rst(cli)`
for Sphinx documentation. Support full command hierarchies with
cross-references between subcommands, parameter tables with types and
defaults, environment variable documentation, and exit code
documentation. Changes span a new documentation generator module,
the help formatter for structured extraction, the core module for
metadata access, and the type system for type name rendering.
