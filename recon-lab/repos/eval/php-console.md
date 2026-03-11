# symfony/console

| Field | Value |
|-------|-------|
| **URL** | https://github.com/symfony/console |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Medium-large |
| **Category** | CLI framework / console component |
| **Set** | eval |
| **Commit** | `fd6352fd1484a5d8c3a5f72b90f3b39daf350f6b` |

## Why this repo

- **Full CLI framework**: Application lifecycle, command registration, input parsing, output formatting, shell completion, signal handling, interactive questions, and testing utilities
- **Well-structured**: Clean separation into Command/, Input/, Output/, Helper/, Formatter/, Completion/, Descriptor/, Event/, Tester/, Style/, Question/, and more
- **Rich feature surface**: ProgressBar, Table, TreeHelper, SymfonyStyle, argument resolvers, invokable commands, section output, color modes, shell completion for bash/zsh/fish

## Structure overview

```
console/
├── Application.php              # Application lifecycle, command routing, signal handling
├── Command/
│   ├── Command.php              # Base command with configure/execute lifecycle
│   ├── InvokableCommand.php     # Closure-based command with argument resolution
│   ├── LazyCommand.php          # Deferred command instantiation
│   ├── TraceableCommand.php     # Command instrumentation
│   ├── CompleteCommand.php      # Shell completion command
│   ├── LockableTrait.php        # Command locking via filesystem
│   └── SignalableCommandInterface.php
├── Input/
│   ├── ArgvInput.php            # CLI argument parser
│   ├── ArrayInput.php           # Programmatic input
│   ├── StringInput.php          # String-parsed input
│   ├── InputDefinition.php      # Argument/option schema
│   ├── InputArgument.php        # Positional argument definition
│   ├── InputOption.php          # Option (flag/value) definition
│   └── Input.php                # Abstract input base
├── Output/
│   ├── ConsoleOutput.php        # stdout/stderr output
│   ├── ConsoleSectionOutput.php # Rewritable output sections
│   ├── StreamOutput.php         # Stream-based output
│   ├── BufferedOutput.php       # In-memory output buffer
│   └── Output.php               # Abstract output base
├── Formatter/
│   ├── OutputFormatter.php      # Tag-based style formatting (<info>, <error>)
│   ├── OutputFormatterStyle.php # Foreground/background/options style
│   └── OutputFormatterStyleStack.php
├── Helper/
│   ├── Table.php                # Table rendering with multiple styles
│   ├── ProgressBar.php          # Progress bar with format placeholders
│   ├── ProgressIndicator.php    # Spinner indicator
│   ├── QuestionHelper.php       # Interactive question prompts
│   ├── TreeHelper.php           # Tree structure rendering
│   ├── ProcessHelper.php        # External process execution
│   ├── FormatterHelper.php      # Text formatting utilities
│   ├── OutputWrapper.php        # Word-wrapping output
│   └── DebugFormatterHelper.php
├── Style/
│   ├── SymfonyStyle.php         # Opinionated output style (title, section, table, etc.)
│   └── OutputStyle.php          # Base output style
├── Completion/                  # Shell completion (bash, zsh, fish)
├── Question/                    # Question, ChoiceQuestion, ConfirmationQuestion, FileQuestion
├── Descriptor/                  # JSON, XML, Markdown, ReStructuredText, Text descriptors
├── Event/                       # ConsoleCommandEvent, ConsoleErrorEvent, ConsoleSignalEvent
├── Tester/                      # CommandTester, ApplicationTester, assertions
├── ArgumentResolver/            # Value resolvers for InvokableCommand
├── Attribute/                   # PHP 8 attributes (AsCommand, Argument, Option, etc.)
├── SignalRegistry/              # OS signal registration and dispatch
├── DependencyInjection/        # Compiler passes for Symfony DI
├── Messenger/                   # Integration with Symfony Messenger
└── Terminal.php                 # Terminal dimensions detection
```

## Scale indicators

- ~160 PHP source files (excluding tests)
- ~22K lines of library code
- ~190 test files
- Multiple output backends (console, stream, buffer, section, null)
- Shell completion for bash, zsh, and fish
- 8 descriptor formats (text, json, xml, markdown, rst)
- Full argument resolver pipeline with 10+ value resolvers

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `OutputFormatterStyleStack` not merging inherited styles from parent stack entries

In `Formatter/OutputFormatterStyleStack.php`, the `getCurrent()` method returns only the top-most active style, discarding visual properties from all parent styles lower in the stack. When `<fg=red><bold>text</bold></fg=red>` is processed, text inside `<bold>` displays only as bold with no foreground color, because `getCurrent()` returns only the bold style instead of a combination of all stacked styles. The method should compute a merged effective style that accumulates properties (foreground, background, options) from all stacked entries in order, so inner styles inherit and augment the outer ones.

### N2: Fix `Terminal` static dimension cache not invalidated on window resize

In `Terminal.php`, the static properties `self::$width` and `self::$height` are populated once by `initDimensions()` and never refreshed. The instance method `getWidth()` falls back to `self::$width ?: 80` after the first call, so `ProgressBar::display()` always uses the cached value even after a terminal resize. Add a `static resetDimensions(): void` method to `Terminal` that sets both static properties to `null` so the next `getWidth()` / `getHeight()` call re-queries the terminal, and call it from `ProgressBar::display()` on a configurable resize-check interval stored in `ProgressBar`. Document the resize behavior in `README.md`.

### N3: Fix `ArgvInput` ignoring configured default for `VALUE_OPTIONAL` options provided without a value

In `Input/ArgvInput.php`, when an `InputOption` with mode `VALUE_OPTIONAL` and a non-null default is passed on the command line as `--name` (without a trailing `=value`), `addLongOption()` stores `null` in `$this->options`. Because `Input::getOption()` returns the stored `null` when the key exists—bypassing the definition default—the option's configured default value is never used. The `addLongOption()` method should assign the option's defined default value when `$value` is `null` and the option is `VALUE_OPTIONAL`, so that `--name` without an explicit value behaves identically to omitting the option entirely.

### N4: Fix `ConsoleSectionOutput` line-count miscalculation for content containing tab characters

In `Output/ConsoleSectionOutput.php`, the `addContent()` method delegates to `getDisplayLength()`, which calls `str_replace("\t", '        ', $text)` to expand tab characters as a fixed 8-space sequence regardless of the tab's column position. Real terminal tab stops advance to the next column that is a multiple of 8 from the tab's actual position, so a tab at column 3 advances only 5 spaces, not 8. This makes the computed line count exceed the actual rendered line count when section content contains tab characters, causing `popStreamContentUntilCurrentSection()` to erase too many lines and leave ghost text from previous renders above the section.

### N5: Fix `ChoiceQuestion` multiselect validator accepting duplicate comma-separated values

In `Question/ChoiceQuestion.php`, the default validator returned by `getDefaultValidator()` does not detect duplicate entries in a multiselect comma-separated response. When a user enters `foo,foo` for a `ChoiceQuestion` with `multiselect: true`, the validator silently returns `['foo', 'foo']` as a valid result instead of throwing `InvalidArgumentException`. The validator should track processed choices in a set and throw when any value appears more than once, matching the contract that each selected choice represents a distinct selection.

### N6: Improve `TreeHelper` cycle detection to render a placeholder instead of throwing

In `Helper/TreeHelper.php`, when the renderer encounters a node that has already been visited during the current traversal, it throws `\LogicException("Cycle detected at node: …")`. Applications that use `TreeHelper` for data structures that may legitimately contain back-references (e.g., dependency graphs loaded from external sources) must wrap every `render()` call in a try-catch, making normal control flow awkward. Replace the throw with a configurable placeholder text (default `[circular reference]`) written via `$this->output->writeln()` for the repeated node, and continue rendering the rest of the tree. Expose a `setCircularReferencePlaceholder(string $text): static` method and update `README.md` with a cycle-handling example.

### N7: Fix `CompletionInput::bind()` accessing out-of-bounds token index when cursor is at position 0

In `Completion/CompletionInput.php`, the `bind()` method unconditionally reads `$this->tokens[$this->currentIndex - 1]` as the "previous token" when deciding whether completion type is `TYPE_OPTION_VALUE`. When `$currentIndex` is `0`, this evaluates to `$this->tokens[-1]`, which is `null` for a standard 0-indexed PHP array. The subsequent `'-' === $previousToken[0]` string offset access on `null` triggers a deprecation warning in PHP 8.0 and a `TypeError` in PHP 8.1+. The method should guard the previous-token lookup with `$this->currentIndex > 0` before accessing the array index.

### N8: Fix `AnsiColorMode` hex color parser silently accepting non-hexadecimal characters

In `Output/AnsiColorMode.php`, `convertFromHexToAnsiColorCode()` strips the `#` prefix and validates only the string length (3 or 6 characters), but does not verify that the remaining characters are valid hexadecimal digits. PHP's `hexdec()` silently returns `0` for strings containing non-hex characters such as `#zzzzzz`, so `Color('#zzzzzz')` silently produces black `(0, 0, 0)` instead of throwing `InvalidArgumentException`. The method should validate that the stripped string matches `/^[0-9a-fA-F]+$/` and throw `InvalidArgumentException` for strings that contain non-hexadecimal characters.

### N9: Fix `OutputWrapper` wrapping at code-point count rather than display column width

In `Helper/OutputWrapper.php`, the `wrap()` method uses `preg_replace()` with the `/u` (Unicode) flag, which counts wrap width in Unicode code points rather than visual terminal columns. East-Asian full-width characters (CJK ideographs, fullwidth forms) each occupy two display columns but count as a single code point. A line wrapped at 80 code points that contains double-width characters may visually span up to 160 terminal columns, causing overflow. The `wrap()` method should measure display width using `mb_strwidth()` (or a helper function that accounts for zero-width and double-width characters) instead of raw code-point count when determining line break positions.

### N10: Fix `Cursor::getCurrentPosition()` writing terminal query to the input stream instead of output

In `Cursor.php`, the `getCurrentPosition()` method writes the ANSI Device Status Report query (`\033[6n`) to `$this->input` (the input stream, defaulting to `STDIN`) via `@fwrite($this->input, "\033[6n")` rather than to `$this->output`. On systems where standard input is piped or redirected (e.g., `echo '' | php app.php`), the query is never delivered to the terminal, and the subsequent `fread($this->input, 1024)` blocks indefinitely waiting for a CPR response that will never arrive. The query should be sent through `$this->output->write("\033[6n")` so it travels via the same path as all other cursor escape sequences, with the CPR response still read back from `$this->input`.

### N11: Fix `CHANGELOG.md` unreleased section not following Keep a Changelog format

The `CHANGELOG.md` uses a freeform format for unreleased changes instead of the Keep a Changelog specification (Added/Changed/Deprecated/Removed/Fixed/Security categories). Restructure existing `CHANGELOG.md` entries into the standardized format, update `.github/PULL_REQUEST_TEMPLATE.md` to require a changelog category selection, and add a changelog validation step referencing `composer.json` version metadata.

## Medium

### M1: Add table column alignment and numeric formatting

Extend `Helper/Table.php` to support per-column alignment (`left`, `right`, `center`) and numeric formatting (thousands separator, decimal places). Requires changes to `Table` (column-level alignment logic), `TableStyle` (alignment configuration), `TableCell` (formatted value rendering), and `TableCellStyle` (numeric format options).

### M2: Implement command grouping with collapsible sections in list output

Add `#[AsCommand(group: 'database')]` support so `list` command displays commands in collapsible groups. Changes span `Attribute/AsCommand.php` (group property), `Command/Command.php` (group accessor), `Application.php` (group collection during `all()`), `Descriptor/TextDescriptor.php` (grouped rendering), and `Command/ListCommand.php` (group filter option). Update `composer.json` autoload configuration for any new namespace additions.

### M3: Add progress bar multi-bar support for parallel operations

Implement `ProgressBar::createMulti($output, $count)` that renders multiple progress bars simultaneously using `ConsoleSectionOutput`. Changes span `Helper/ProgressBar.php` (multi-bar coordinator, section-based rendering), `Output/ConsoleSectionOutput.php` (multi-section updates), `Style/SymfonyStyle.php` (multi-progress factory), and `Helper/ProgressIndicator.php` (multi-indicator support).

### M4: Extend `UidValueResolver` to support array-mode UID arguments and options

The existing `ArgumentResolver/ValueResolver/UidValueResolver.php` resolves a single `AbstractUid` instance from a command argument or option but does not handle the case where the argument or option is declared with `IS_ARRAY` mode. When an `#[Argument]` or `#[Option]` has a UID type name and array mode, multiple string values may be provided that each need to be converted to a UID. Changes span `UidValueResolver.php` (add array branches to `resolveArgument()` and `resolveOption()` that iterate values and return an array of UIDs), `Attribute/Argument.php` (expose array-mode flag for UID detection), `Attribute/Option.php` (corresponding option handling), `ArgumentResolver/ArgumentResolver.php` (ensure UID resolver runs before the generic array resolver to avoid type conflicts), and `Command/InvokableCommand.php` (propagate `IS_ARRAY` flag when building the UID input definition entry).

### M5: Add per-command timeout via `#[AsCommand(timeout:)]` integrated with the existing alarm infrastructure

`Application` already implements `setAlarmInterval()`, `scheduleAlarm()`, and `SIGALRM` dispatch via `ConsoleAlarmEvent`, but there is no way to declare a per-command timeout declaratively. Add a `timeout` constructor parameter to `Attribute/AsCommand.php` and a `getTimeout(): ?int` accessor to `Command/Command.php`. In `Application::doRunCommand()`, read the command's timeout and call `$this->setAlarmInterval($timeout)` before execution and `$this->setAlarmInterval(null)` in the finally block. Extend `EventListener/ErrorListener.php` to cancel any pending alarm when a console error event is dispatched. Changes span `Attribute/AsCommand.php`, `Command/Command.php`, `Application.php` (`doRunCommand`), and `EventListener/ErrorListener.php`.

### M6: Implement output format auto-detection for CI environments

Add automatic format switching when running in CI (GitHub Actions, GitLab CI, Jenkins). When detected, use `GithubActionReporter` for error/warning annotations and disable interactive features. Changes span `Application.php` (CI detection), `CI/GithubActionReporter.php` (annotation formatting), `Output/ConsoleOutput.php` (decorator mode selection), `Style/SymfonyStyle.php` (disable interactive prompts), and `Helper/QuestionHelper.php` (CI fallback to defaults).

### M7: Add interactive file browser for `FileQuestion`

Extend `Question/FileQuestion.php` with an interactive directory-browsing mode using `Cursor` for navigation. Changes span `FileQuestion.php` (browse mode, directory listing), `Helper/QuestionHelper.php` (special handling for file browse), `Cursor.php` (keyboard navigation helpers), `Input/StreamableInputInterface.php` (raw key detection), and `Style/SymfonyStyle.php` (file chooser method).

### M8: Implement command dependency declaration and execution ordering

Add `#[AsCommand(requires: ['db:migrate'])]` that ensures prerequisite commands run before the current command. Changes span `Attribute/AsCommand.php` (requires property), `Application.php` (dependency graph resolution, topological sort), `Command/Command.php` (dependency accessor), `Exception/` (circular dependency exception), and `Tester/CommandTester.php` (dependency stubbing).

### M9: Add shell completion for dynamic values from external data sources

Extend the completion system to support async value providers that fetch suggestions from databases or APIs. Changes span `Completion/CompletionSuggestions.php` (deferred suggestion provider), `Completion/Suggestion.php` (metadata for external sources), `Command/CompleteCommand.php` (async suggestion resolution), `Completion/Output/BashCompletionOutput.php` (cached suggestion rendering), and `Completion/CompletionInput.php` (provider context).

### M10: Implement colored diff output helper for text comparison

Add `Helper/DiffHelper.php` that renders side-by-side or unified diffs with ANSI coloring. Changes span a new `Helper/DiffHelper.php` (diff algorithm, formatting), `Style/SymfonyStyle.php` (diff rendering method), `Formatter/OutputFormatterStyle.php` (diff-specific styles: added, removed, changed), and `Helper/HelperSet.php` (diff helper registration).

### M11: Improve CI configuration and PR review process

Extend `.github/workflows/close-pull-request.yml` with a comprehensive CI pipeline: add PHP version matrix testing (8.1, 8.2, 8.3), integrate `phpunit.xml.dist` test execution, and add static analysis steps. Create `.github/PULL_REQUEST_TEMPLATE.md` with checklist items for tests, changelog, and documentation. Update `composer.json` with `scripts` section for development commands (test, lint, format). Add `.github/copilot-instructions.md` with project conventions for the `Descriptor/`, `Helper/`, and `Command/` directories. Changes span `.github/workflows/`, `.github/PULL_REQUEST_TEMPLATE.md`, `.github/copilot-instructions.md`, `composer.json`, and `phpunit.xml.dist`.

## Wide

### W1: Implement interactive TUI framework with widget system

Add a terminal UI mode with reusable widgets: text input, select list, checkbox group, data table with pagination, and modal dialogs. Changes span a new `Widget/` directory (base widget, text input, select, checkbox, table, modal), `Cursor.php` (viewport management), `Output/ConsoleSectionOutput.php` (widget region rendering), `Input/Input.php` (raw keyboard event processing), `Style/SymfonyStyle.php` (widget factory methods), and `Application.php` (TUI mode lifecycle).

### W2: Add command workflow engine with step-based execution

Implement `Workflow` class for defining multi-step command pipelines with conditional branching, parallel steps, and rollback. Changes span a new `Workflow/` directory (Workflow, Step, ParallelStep, ConditionalStep, WorkflowRunner), `Command/Command.php` (workflow execution mode), `Application.php` (workflow registration), `Output/ConsoleSectionOutput.php` (step progress rendering), `Event/` (workflow events), and `Tester/` (workflow testing utilities).

### W3: Implement remote command execution over SSH

Add `Application::connectRemote($host)` for executing commands on remote servers via SSH with output streaming back to the local terminal. Changes span `Application.php` (remote session management), `Command/Command.php` (remote execution flag), `Input/ArgvInput.php` (host prefix parsing), `Output/StreamOutput.php` (SSH channel output), `Helper/ProcessHelper.php` (SSH process adapter), and a new `Remote/` directory (SshSession, RemoteOutput, RemoteInput).

### W4: Add plugin system with command auto-discovery

Implement `Application::loadPlugins($path)` for auto-discovering and loading command bundles from filesystem paths or Composer packages. Changes span `Application.php` (plugin registry, command auto-discovery), `CommandLoader/` (PluginCommandLoader), `DependencyInjection/` (plugin compiler pass), `Attribute/AsCommand.php` (plugin metadata), `Descriptor/` (plugin listing in descriptors), and a new `Plugin/` directory (PluginInterface, PluginManager, PluginMetadata).

### W5: Implement comprehensive output theming system

Add configurable themes that control all visual aspects: colors, table styles, progress bar formats, tree styles, and question prompts. Changes span a new `Theme/` directory (Theme, ThemeLoader, ThemeCompiler), `Formatter/OutputFormatterStyle.php` (theme-aware style resolution), `Helper/Table.php` (themed table styles), `Helper/ProgressBar.php` (themed formats), `Helper/TreeHelper.php` (themed tree styles), `Style/SymfonyStyle.php` (theme integration), and `Application.php` (theme loading from config).

### W6: Add command profiling and performance dashboard

Implement execution profiling for all commands: timing, memory usage, I/O operations, signal counts, and sub-command calls. Changes span `Command/TraceableCommand.php` (profiling instrumentation), `Application.php` (global profiling toggle), `DataCollector/CommandDataCollector.php` (profile storage and aggregation), a new `Profile/` directory (Profiler, ProfileReport, ProfileFormatter), `Descriptor/` (profile output in all formats), and `Tester/` (profile assertion helpers).

### W7: Implement structured logging integration across all components

Add PSR-3 structured logging throughout the console lifecycle: command resolution, input parsing, signal handling, question prompts, and external process execution. Changes span `Application.php` (logger injection, command lifecycle logging), `Logger/ConsoleLogger.php` (structured log context), `Command/Command.php` (command-level log context), `Helper/ProcessHelper.php` (process logging), `Helper/QuestionHelper.php` (interaction logging), `SignalRegistry/SignalRegistry.php` (signal logging), and `Input/ArgvInput.php` (parse logging).

### W8: Add internationalization for all console output

Implement `Application::setLocale('fr')` with translated error messages, help text, progress bar labels, table headers, and question prompts. Changes span a new `Translation/` directory (Translator, MessageCatalogue, TranslationLoader), `Resources/translations/` (message files), `Application.php` (locale setting), `Command/Command.php` (translatable descriptions), `Helper/ProgressBar.php` (translatable format labels), `Helper/QuestionHelper.php` (translatable prompts), `Style/SymfonyStyle.php` (locale-aware formatting), and `Descriptor/TextDescriptor.php` (translated output).

### W9: Implement command versioning and deprecation framework

Add `#[AsCommand(since: '5.0', deprecatedAt: '6.0', replacedBy: 'new:command')]` with runtime deprecation warnings, migration guides, and compatibility shims. Changes span `Attribute/AsCommand.php` (version metadata), `Application.php` (deprecation checking, version-based command resolution), `Command/Command.php` (version accessors, migration helper), `Descriptor/` (version annotations in all formats), `Event/` (deprecation events), `Tester/` (deprecation assertion helpers), and `DependencyInjection/AddConsoleCommandPass.php` (compile-time deprecation checks).

### W10: Add distributed command execution with task queue integration

Implement `Application::dispatch('long:command', $args)` that serializes command invocations to a message queue (via Symfony Messenger) and processes them asynchronously with result collection. Changes span `Application.php` (dispatch mode, result collector), `Messenger/RunCommandMessage.php` (enhanced serialization), `Messenger/RunCommandMessageHandler.php` (async execution), `Command/Command.php` (async result reporting), `Output/BufferedOutput.php` (serializable output capture), `Tester/CommandTester.php` (async testing), and a new `Async/` directory (AsyncResult, ResultCollector, CommandSerializer).

### W11: Generate comprehensive descriptor-based documentation

Leverage the existing `Descriptor/` system (JSON, XML, Markdown, ReStructuredText, Text descriptors) to auto-generate project documentation: add a `GenerateDocsCommand` that uses `MarkdownDescriptor` and `ReStructuredTextDescriptor` to produce complete command reference documentation from registered commands; create `Resources/doc/` with generated and hand-written guides; update `README.md` with usage examples for all major features (ProgressBar, Table, TreeHelper, SymfonyStyle, shell completion); add `CHANGELOG.md` generation validation to CI; and document the full descriptor format in a developer guide. Changes span `Descriptor/`, `Resources/`, `README.md`, `CHANGELOG.md`, `composer.json` (scripts), and `.github/workflows/`.
