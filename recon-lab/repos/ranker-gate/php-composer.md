# composer/composer

| Field | Value |
|-------|-------|
| **URL** | https://github.com/composer/composer |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Medium (multi-module project) |
| **Category** | Dependency manager |
| **Set** | ranker-gate |
| **Commit** | `f8a87966f77267f041949ca9785df6992ec4a128` |

## Why this repo

- **Multi-module with clear boundaries**: Dependency resolution (SAT solver),
  package repository sources (Packagist, VCS, path), autoload generation,
  installer plugins, lock file management, script execution, platform
  requirements — each a distinct subsystem requiring navigation between them.
- **Well-structured**: Source under `src/Composer/` with clear namespace
  split. Dependency solver, repository layer, installer, downloader, and
  autoload generator are separated by concern.
- **Rich history**: 10K+ commits, foundational to the PHP ecosystem. Every
  PHP project uses it. PRs cover dependency resolution edge cases, repository
  source handling, and performance improvements.
- **Permissive**: MIT license.

## Structure overview

```
src/Composer/
├── Composer.php              # Main Composer class
├── Factory.php               # Factory for creating Composer instances
├── Command/                  # CLI commands (require, install, update, etc.)
├── DependencyResolver/       # SAT-based dependency resolution
│   ├── Solver.php            # Constraint solver
│   ├── Pool.php              # Package pool
│   ├── Request.php           # Resolution request
│   └── Rule.php              # Solver rules
├── Repository/               # Package sources
│   ├── ComposerRepository.php    # Packagist/Composer repos
│   ├── VcsRepository.php         # Git/SVN/Hg repos
│   ├── PathRepository.php        # Local path repos
│   └── RepositoryManager.php     # Repository orchestration
├── Installer/                # Package installation
├── Downloader/               # Package downloading (zip, git, etc.)
├── Autoload/                 # Autoload file generation
│   ├── AutoloadGenerator.php
│   └── ClassMapGenerator.php
├── Package/                  # Package model
├── Plugin/                   # Plugin system
└── Script/                   # Script execution
```

## Scale indicators

- ~200 PHP source files
- ~50K lines of code
- 2-3 levels of namespace nesting
- Clear subsystem boundaries

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `composer update --minimal-changes` removing unrelated packages

When running `composer update --minimal-changes pkg/specific`, unrelated
packages that have newer versions available are sometimes downgraded
to their minimum required version. The solver treats the `--minimal-changes`
flag as "minimize all changes" rather than "update only the specified
packages." Fix the solver to lock non-targeted packages at their
current versions.

### N2: Add `--sort` option to `composer show` command

The `composer show` command lists packages but provides no sorting
options. Add `--sort=name` (default, current behavior), `--sort=size`
(by installed size), and `--sort=date` (by last update date) options.
Include a `--reverse` flag for descending order.

### N3: Fix `SvnDriver::execute` not masking credentials in error output

When `SvnDriver` executes SVN commands with `--username` and
`--password` flags and the command fails, the error message logged
via `$io->writeError()` includes the password in cleartext. A user
running Composer in CI with verbose output inadvertently exposes
repository credentials in build logs. Mask the password value in
the error output before logging, consistent with how `Svn::cleanEnv`
already handles environment variables. Update
`doc/05-repositories.md` to document the credential masking behavior
for SVN repositories and add a note about safe CI logging practices.

### N4: Fix `BumpCommand` not skipping packages with branch-alias constraints

When running `composer bump`, packages whose constraint is
`dev-main` or contains a branch alias (e.g., `dev-main as 2.0.0`)
are processed by the bumper, which produces invalid constraints
like `^dev-main`. The command should detect dev/branch-alias
constraints and skip them with an informational message instead of
writing a broken constraint to `composer.json`.

### N5: Fix `AutoloadGenerator` producing duplicate classmap entries

When a package defines both a `classmap` and a `psr-4` entry that overlap
on the same directory, `AutoloadGenerator` emits duplicate entries in the
generated `autoload_classmap.php`. The class scanner in `ClassMapGenerator`
does not deduplicate against PSR-4 discovered classes. Fix the generator
to detect overlapping autoload rules and emit each class exactly once,
preferring the PSR-4 entry.

### N6: Fix `PathRepository` symlink mode ignoring nested composer.json

When using a path repository with `"symlink": true` pointing at a directory
that contains subdirectories with their own `composer.json` files,
`PathRepository` only reads the top-level `composer.json` and silently
ignores nested packages. Fix `PathRepository` to correctly scope its
package discovery to only the target directory without descending into
subdirectories that are separate packages.

### N7: Fix `StatusCommand` not detecting uncommitted changes in source-installed packages

`composer status` checks for local modifications in source-installed
packages by running `git status` in each package directory. However,
it does not detect untracked files (new files added but not committed).
A developer who adds a new file inside `vendor/pkg/src/` gets no
warning before running `composer update`, which overwrites the vendor
directory. Include untracked files in the status output using
`git status --porcelain`.

### N8: Fix `GitExcludeFilter` ignoring subdirectory `.gitattributes` export-ignore rules

When `composer archive` creates a zip or tar archive, `ArchivableFilesFinder`
applies `GitExcludeFilter` to honour `export-ignore` directives from
`.gitattributes`. However, `GitExcludeFilter` only reads the `.gitattributes`
file at the root of the source directory (`$sourcePath.'/.gitattributes'`); it
never descends into subdirectories. When a package defines `export-ignore`
patterns in `.gitattributes` files inside subdirectories — which is valid git
behaviour — those rules are silently ignored and the matched files are included
in the archive. Fix `GitExcludeFilter` to also process `.gitattributes` files
found in subdirectories of the source path, accumulating their `export-ignore`
rules alongside those from the root file.

### N9: Fix `SearchCommand` returning duplicate results from multiple repositories

`composer search` queries all configured repositories via
`CompositeRepository::search()`, which uses `array_merge` to concatenate
results without deduplication. When the same package is mirrored in both a
private Packagist repository and the public one, it appears multiple times in
the output — once per repository that returned it. Fix `SearchCommand` to
deduplicate search results by package name, keeping the entry from the
highest-priority repository (first-declared in `composer.json`) when
duplicates exist.

### N10: Fix `ValidateCommand` not checking for conflicting `autoload` and `autoload-dev` paths

`composer validate` checks JSON schema correctness but does not
detect when `autoload` and `autoload-dev` PSR-4 entries map
overlapping namespace prefixes to different directories, which
causes unpredictable class loading depending on whether dev
dependencies are installed. Add a check that warns when both
sections define the same namespace prefix with different paths.

## Medium

### M1: Implement explicit download-verification controls with improved error reporting

`FileDownloader` already verifies SHA-1 checksums from the `dist.shasum`
field when the repository provides them, but there is no way for users to
control or observe this behaviour. Add a `--verify-downloads` CLI flag to
the `install` and `update` commands and a `config.verify-downloads`
configuration option (default: `true`) to explicitly enable or disable
checksum verification. When verification fails, emit a clear error message
showing the expected and actual SHA-1 hash before deleting the corrupted
file, then retry the download once before permanently failing. Support
disabling verification for local/path repositories where no checksum is
available. Modify `FileDownloader`, `ZipDownloader`, and `TarDownloader`
to call the verification step after download but before extraction, and
to respect the new configuration flag.

### M2: Add package size reporting to `ShowCommand` and post-install summary

After installation, users have no way to see how much disk space each
package consumes in `vendor/`. Add a `--sizes` flag to
`composer show` that calculates and displays the installed size
of each package by walking its vendor directory. Show sizes in
human-readable format (KB/MB). Add a post-install summary line
showing total `vendor/` size and the top 5 largest packages.
Support `--format=json` output of size data. Implement size
calculation in a new method on `Filesystem` and integrate it
into `ShowCommand` and `InstallCommand`'s output.

### M3: Implement workspace/monorepo support

Add `composer.json` workspace support for monorepos containing multiple
packages. A root workspace config lists member packages by directory.
Cross-references between workspace packages use local paths
automatically. `composer install` in the root resolves dependencies
for all workspace members together, deduplicating shared dependencies.
Add `--workspace-filter=pkg/*` to operate on a subset.

### M4: Add `--format=markdown` and semver-level column to `outdated` command

The `outdated` command already supports `--major-only`, `--minor-only`, and
`--patch-only` filter flags and displays color-coded update indicators in
the default text output, but it has no `--format=markdown` option for
generating output suitable for pasting into pull request descriptions.
Add `--format=markdown` to `OutdatedCommand` and the underlying
`ShowCommand` display logic, producing a GitHub-Flavored Markdown table
with columns for package name, current version, latest version, and semver
level (major/minor/patch). The semver level column should use text labels
since Markdown cannot render colors. Update `CHANGELOG.md` with the new
format option and add documentation for `--format=markdown` to
`doc/03-cli.md`.

### M5: Add configurable retry and mirror fallback to Downloader

The download layer currently fails after a single HTTP timeout or 5xx
error. Implement configurable retry logic in the Downloader subsystem:
exponential backoff with jitter (default 3 retries), automatic fallback
to alternative mirror URLs listed in the repository metadata, and a
circuit-breaker that skips a mirror after repeated failures within a
time window. Add `--retry-count` and `--mirror-timeout` CLI options and
corresponding `config` keys.

### M6: Implement `composer diff` for comparing installed vs locked versions

Add a `composer diff` command that compares the currently installed
packages (from `vendor/composer/installed.json`) against the lock
file (`composer.lock`). Show packages that are installed but not in
the lock file, packages in the lock file but not installed, and
packages where the installed version differs from the locked
version. Support `--format=json` for CI integration. Add a
`--changelog` flag that fetches and displays changelog URLs from
repository metadata for each changed package. Integrate with
`Locker`, `RepositoryManager`, and the installed repository.

### M7: Add dependency tree depth limiting to the solver

Deeply nested dependency trees slow resolution and increase the
risk of version conflicts. Add a `config.max-depth` option
(default: unlimited) that causes the solver to emit a warning
when the dependency graph exceeds the configured depth, and a
`--max-depth=N` CLI option for `update` and `require` that
makes the solver reject solutions deeper than N levels. Report
the deepest dependency path found during resolution. Implement
depth tracking in the `Solver` by augmenting rule generation
in `RuleSetGenerator` and adding depth attributes to `Pool`
entries. Display the deepest path in the `DependencyResolver`
output when verbosity is enabled.

### M8: Implement autoload generation caching

The `AutoloadGenerator` rebuilds classmap and PSR-4 autoload files from
scratch on every `composer dump-autoload`. Implement a cache layer that
stores the previously generated autoload state and only regenerates
entries for packages whose installed version or autoload configuration
changed since the last dump. Add a cache manifest file alongside the
generated autoloader. Support `--force` to bypass the cache. Measure
and log time savings for large dependency trees.

### M9: Add `composer bin` for managing vendor binary symlinks

Vendor binaries in `vendor/bin/` are created as symlinks or proxy
scripts during installation, but there is no command to inspect or
manage them. Add a `composer bin` command with subcommands:
`composer bin list` shows all available binaries with their
providing package and resolved path, `composer bin path <name>`
prints the absolute path to a binary, and `composer bin check`
verifies that all expected symlinks exist and point to valid
targets. Integrate with `InstallationManager` to read the binary
registry and `Filesystem` to validate symlink targets.

### M10: Add require-dev path differentiation to `depends --tree` output

The `depends --tree` command recursively walks the dependency graph upward
from a target package to the root, displaying a nested tree of each path
with version constraints at each node. However, all paths are displayed
identically regardless of whether they pass through a `require-dev` entry:
there is no visual distinction between production and dev-only dependency
paths. Add a `[dev]` prefix marker (and apply a distinct color in terminal
output) to every tree node that is reachable only via a `require-dev`
dependency link. Add a `--no-dev` flag to `DependsCommand` that excludes
all require-dev dependency paths from the tree output entirely. Update
`BaseDependencyCommand` to propagate the require-dev state when
traversing dependency links during graph construction and integrate with
the `Locker`'s installed repository for graph traversal.

## Wide

### W1: Add plugin sandboxing for security

Implement a security sandbox for Composer plugins. Plugins currently
have full access to the filesystem and network. Add a permission system
where plugins declare required capabilities (filesystem read/write paths,
network hosts, command execution) and the user approves them on first
use. Store approved permissions in a lock file. Block undeclared
operations at runtime. Add `composer plugin:audit` to review plugin
permissions.

### W2: Implement dependency resolution visualization and debugging

Add rich dependency resolution diagnostics: visual dependency tree
(`composer why-tree pkg/name`), conflict explanation with the full
resolution trace (`composer why-not pkg/name:2.0` with step-by-step
solver reasoning), resolution time profiling (which constraints took
the longest to satisfy), and a browser-based interactive dependency
graph that shows version constraints, conflicts, and resolution paths.

### W3: Implement lock file conflict auto-resolution for merge workflows

When merging branches that both modified `composer.lock`, Git leaves
conflict markers that require manual resolution. Implement a `composer
lock:resolve` command that parses the conflicted lock file, extracts both
sides' package lists, re-resolves the combined dependency set against
the merged `composer.json`, and writes a clean lock file. Handle cases
where both branches added conflicting versions of the same package,
where one branch removed a package the other updated, and where platform
requirements diverged. Integrate with the solver to detect unresolvable
conflicts and report them clearly.

### W4: Add dependency license compliance checking

Implement a `composer license:check` command that validates all installed
packages against a configurable license policy. Define allowed and
forbidden license identifiers in `composer.json` under an `extra` key.
Walk the full dependency tree including transitive dependencies, extract
SPDX license identifiers from each package's metadata, flag unknown or
ambiguous licenses, support SPDX compound expressions (AND, OR, WITH),
and generate a compliance report in text, JSON, and HTML formats. Add a
`--fail-on=deny` flag for CI pipelines that exits non-zero on policy
violations.

### W5: Implement repository priority and constraint routing

Add a repository priority system where each configured repository has
a numeric priority and optional package pattern filters. When resolving
dependencies, the `RepositoryManager` should prefer higher-priority
repositories, only falling back to lower-priority ones if a package
is not found or the version constraint cannot be satisfied. Support
per-package repository pinning in `composer.json` to force a specific
package to come from a specific repository. Handle version conflicts
across repositories by reporting which repository provided each
candidate version, and integrate priority into the solver's rule
generation in `DependencyResolver`.

### W6: Add `composer ci` meta-command for continuous integration workflows

Implement a `composer ci` command that orchestrates a full CI pipeline:
validate `composer.json` schema, verify `composer.lock` is up-to-date,
install dependencies with `--no-interaction`, run `composer audit` for
security advisories, execute configured test scripts, generate a
classmap-authoritative autoloader, and produce a summary report. Each
step should be independently skippable via `--skip=audit,test`. Support
`--fail-fast` to stop on first failure. Emit structured JSON output
suitable for CI systems. Add timing information for each step and an
overall pass/fail status code.

### W7: Implement cross-version dependency testing

Add a `composer test:matrix` command that tests the project against
multiple dependency version combinations. Parse each dependency's
version constraint, enumerate the lowest, highest, and a configurable
set of intermediate versions, generate a test matrix, and run the
configured test script against each combination. Parallelize test runs
up to a configurable concurrency limit. Use the solver to verify each
combination is installable before testing. Report a compatibility matrix
showing which version combinations pass or fail, including the specific
failure output for failed combinations.

### W8: Implement a plugin capability and lifecycle system

Redesign the plugin system to support a formal capability model and
lifecycle hooks. Define a `PluginCapability` interface that plugins
implement to declare the events they handle (pre/post install, resolve,
download, autoload, script execution). Add plugin ordering and priority
to control execution sequence when multiple plugins handle the same
event. Implement a plugin lifecycle with `activate`, `deactivate`, and
`uninstall` phases so plugins can perform cleanup. Add a `composer
plugin:list` command showing installed plugins, their capabilities,
and execution order. Support lazy plugin loading so plugins are only
instantiated when their declared events fire.

### W9: Implement offline mode with pre-populated cache

Add comprehensive offline mode support to Composer. When online,
`composer cache:prepare` downloads and caches all metadata and archives
for the current dependency tree plus a configurable depth of version
alternatives. When offline, Composer falls back to the local cache for
all repository metadata, package downloads, and VCS operations. Track
cache freshness per-package and warn when cached data exceeds a
configurable age. Support exporting and importing the cache as a
portable archive for air-gapped environments. Modify the
`RepositoryManager`, `Downloader`, and `Factory` to transparently
check cache availability before network access.

### W10: Add structured logging and telemetry across all subsystems

Implement a structured logging framework that replaces ad-hoc output
across all Composer subsystems. Define log channels for resolver,
installer, downloader, autoloader, plugin, and repository operations.
Each log entry includes a timestamp, channel, severity, structured
context data, and a correlation ID that ties together all operations
within a single command invocation. Support configurable log sinks
(stderr, file, JSON-lines) via `composer.json` config. Add
`--log-level` and `--log-file` CLI options. Implement performance
telemetry that records timing for dependency resolution, downloads,
and autoload generation, and optionally reports anonymized telemetry
to a configurable endpoint.

### N11: Fix inconsistent formatting in `CHANGELOG.md`

The `CHANGELOG.md` has two formatting inconsistencies. First, the oldest
entry (version `1.0.0-alpha1` near the bottom of the file) uses the header
`### 1.0.0-alpha1 - 2012-03-01` — missing the brackets around the version
number that every other entry uses (`### [version]`). Second, older 1.x
entries use `### [version] - YYYY-MM-DD` (hyphen separator after the
closing bracket), while all 2.x entries use `### [version] YYYY-MM-DD`
(no hyphen). Standardise all version headers to the modern `### [version]
YYYY-MM-DD` format: add brackets to the `1.0.0-alpha1` entry and remove
the hyphen separators from all 1.x entries. Add the missing
`[1.0.0-alpha1]` link-reference definition at the bottom of the file to
match every other version.

### M11: Expand contributor tooling and add plugin development documentation

The `phpunit.xml.dist` configures coverage via the PHPUnit 9 `<coverage>`
element but does not enable branch coverage reporting, and has no dedicated
test group for integration tests (only `slow` and `legacy` groups are
defined). The `composer.json` `scripts` section has `test`, `phpstan`, and
`compile` entries but no `lint` or `cs-fix` shortcuts for contributors. Add
an `integration` test group to `phpunit.xml.dist` and configure the
`<coverage>` element with branch coverage enabled and a Clover XML report
output. Add `lint` (for JSON/schema validation via `composer validate`)
and `cs-fix` (invoking `php-cs-fixer fix`) script entries to `composer.json`
and their descriptions to `scripts-descriptions`. Add a new
`doc/dev/plugin-development.md` guide covering the existing `PluginInterface`
lifecycle hooks (`activate`, `deactivate`, `uninstall`), capability
declaration patterns, event subscription via `EventSubscriberInterface`,
and strategies for testing plugins.

### W11: Modernize project tooling and contributor documentation

Migrate `.php-cs-fixer.php` rules to enforce PHP 8.x coding standards
(strict types declarations, match expression formatting, named argument
alignment, and nullsafe operator usage). Update `phpunit.xml.dist` from
the PHPUnit 9 schema (`xsi:noNamespaceSchemaLocation` targeting version
9.3 with a `<coverage><include>` element) to the PHPUnit 10+ schema
(update the `xsi:noNamespaceSchemaLocation` URL and replace
`<coverage><include>` with the `<source>` element format). Expand the
existing `.github/CONTRIBUTING.md` with detailed sections covering the
SAT solver debugging workflow, repository driver testing procedures, and
development environment setup for new contributors. Refresh `README.md`
with updated installation instructions for PHP 8.x and current CI badges.
Update `doc/00-intro.md` and `doc/01-basic-usage.md` to reflect Composer
2.x defaults and behavior changes. Add enforcement procedures to
`CODE_OF_CONDUCT.md`. Add `lint` and `cs-fix` entries to the `scripts`
section of `composer.json` for contributor convenience (the section
already contains `test`, `phpstan`, and `compile`).
