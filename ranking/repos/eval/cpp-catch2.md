# catchorg/Catch2

| Field | Value |
|-------|-------|
| **URL** | https://github.com/catchorg/Catch2 |
| **License** | BSL-1.0 |
| **Language** | C++ |
| **Scale** | Medium |
| **Category** | C++ test framework |
| **Set** | eval |
| **Commit** | `0ad9824bc644fbc4c0c1226340a04f0ded7919de` |

## Why this repo

- **Multi-subsystem**: Test case registry, assertion macros/decomposer, matchers, generators, benchmarking, reporters (console/JUnit/JSON/TAP/XML), CLI parsing, output capture, sharding
- **Well-structured**: Clear separation between internal/, matchers/, reporters/, generators/, benchmark/, interfaces/
- **Rich history**: 19K+ stars, widely used C++ test framework with header-only heritage and modular v3 architecture

## Structure overview

```
src/catch2/
├── internal/            # Core engine: registry, runner, decomposer, CLI, text formatting
│   ├── catch_run_context.*        # Test case execution
│   ├── catch_test_case_registry_impl.*  # Test registration
│   ├── catch_decomposer.*         # Expression decomposition for assertions
│   ├── catch_section.*            # SECTION nesting
│   ├── catch_test_case_tracker.*  # Section/test tracking
│   ├── catch_xmlwriter.*          # XML output
│   ├── catch_jsonwriter.*         # JSON output
│   ├── catch_textflow.*           # Line-wrapping text formatter
│   ├── catch_sharding.*           # Test sharding
│   ├── catch_output_redirect.*    # stdout/stderr capture
│   └── catch_clara.*              # CLI argument parser
├── matchers/            # Matcher framework: string, float, container, predicate, range
├── reporters/           # Console, JUnit, JSON, TAP, SonarQube, compact, automake
├── generators/          # Data generators for property-based testing
├── benchmark/           # Micro-benchmarking engine with statistical analysis
├── interfaces/          # Abstract interfaces: reporter, listener, config, registry
```

## Scale indicators

- ~410 C++ source files (headers + implementations)
- ~74K lines of code
- Multiple reporter backends and assertion types
- Cross-cutting section tracking and test case registry

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `SECTION` nesting depth not reported in console reporter on failure

When an assertion fails inside a deeply nested `SECTION` in `internal/catch_section.cpp`, the console reporter in `reporters/catch_reporter_console.cpp` only prints the innermost section name. The full section path (parent > child > grandchild) should be displayed to help locate the failure.

### N2: Fix `Approx` matcher not handling subnormal floating-point values correctly

In `matchers/catch_matchers_floating_point.cpp`, the `WithinAbsMatcher` compares using absolute epsilon but does not account for subnormal float values where the default epsilon is larger than the values themselves, causing false positives for comparisons near zero.

### N3: Add `CATCH_CONFIG_DISABLE_BENCHMARKS` compile-time flag

The benchmark subsystem in `benchmark/catch_benchmark.hpp` is always compiled even when not used. Add a preprocessor flag in `internal/catch_compiler_capabilities.hpp` that disables benchmark compilation, reducing binary size for projects that only need test assertions.

### N4: Fix JSON reporter not escaping Unicode control characters

In `internal/catch_jsonwriter.cpp`, the `needsEscape` function handles only the six named escape sequences (`\"`, `\\`, `\b`, `\f`, `\n`, `\r`, `\t`) but does not escape the remaining Unicode control characters (U+0000–U+001F) required by the JSON specification. Characters such as U+0001 (SOH) through U+0008 (BS), U+000B (VT), U+000E–U+001F embedded in test names or captured output produce invalid JSON. Extend `needsEscape` and `makeEscapeStringRef` to emit `\uXXXX` sequences for all unhandled control characters. Update `docs/reporters.md` with JSON reporter output format specification.

### N5: Fix `StringRef` comparison not being `constexpr` when possible

In `internal/catch_stringref.hpp`, the `operator==` for `StringRef` performs a length check followed by `std::memcmp`, but neither is marked `constexpr`. Since C++17 allows `constexpr` string operations, the comparison should be `constexpr` for compile-time test name deduplication.

### N6: Add wildcard negation support in test spec tag expressions

The test spec parser in `internal/catch_test_spec_parser.cpp` supports `[tag]` inclusion and `~[tag]` exclusion, but does not support negated wildcard patterns like `~[slow*]` to exclude all tags matching a prefix. The `TestSpec::TagPattern` in `catch_test_spec.hpp` performs an exact `std::find` for a tag literal and does not use `WildcardPattern`, so a pattern such as `[slow*]` or `~[slow*]` will only match a test whose tag is literally `"slow*"`. Extend `TagPattern::matches` in `catch_test_spec.cpp` to use `WildcardPattern` (from `internal/catch_wildcard_pattern.hpp`) when the tag string contains `*` characters, enabling wildcard inclusion and exclusion.

### N7: Fix `TextFlow::Column` width calculation for multi-byte UTF-8 characters

In `internal/catch_textflow.cpp`, `AnsiSkippingString::preprocessString` and its iterator advance one byte at a time, counting each byte as one visible character via `m_size`. Multi-byte UTF-8 sequences (e.g., two-byte Latin Extended or three-byte CJK characters) are therefore counted as 2–4 visible characters each, causing `Column::calcLength` to calculate an inflated line width and wrap lines too early when test names or section names contain non-ASCII Unicode text. Fix the byte-counting in `preprocessString` and the `advance` method to skip UTF-8 continuation bytes (0x80–0xBF) so that `m_size` and `lineLength` reflect codepoint count rather than byte count.

### N8: Fix `GENERATE(random(...))` for `long double` not deterministic across platforms

In `generators/catch_generators_random.cpp`, the `long double` specialization of `RandomFloatingGenerator` uses `std::uniform_real_distribution<long double>` to sample values, but the C++ standard does not guarantee identical output sequences across standard library implementations (e.g., libstdc++ vs libc++). All other floating-point types use the portable `Catch::uniform_floating_point_distribution`. Replace the `std::uniform_real_distribution<long double>` in `RandomFloatingGenerator<long double>::PImpl` with `Catch::uniform_floating_point_distribution<long double>` so that `GENERATE(random(x, y))` for `long double` produces identical sequences on all platforms with the same `--rng-seed`.

### N9: Add elapsed time display to TAP reporter output

The TAP reporter in `reporters/catch_reporter_tap.cpp` outputs `ok`/`not ok` lines but does not include execution time for individual test cases. Add `# time=<ms>` directives after each test line, following the TAP 13 specification for timing metadata.

### N10: Fix missing CLI validation that `--shard-index` is less than `--shard-count`

In `internal/catch_commandline.cpp`, the `setShardIndex` handler validates only that the value is a non-negative integer but does not check that it is less than the configured shard count. When a user passes `--shard-index N` where `N >= --shard-count`, the `createShard` function in `internal/catch_sharding.hpp` is called with `shardIndex >= shardCount`, triggering `assert(shardCount > shardIndex)` in debug builds and undefined behaviour in release builds. Add a post-parse validation step (after both options are parsed) that returns a `runtimeError` when `shardIndex >= shardCount`.

### N11: Fix `docs/release-notes.md` not documenting matcher API breaking changes

The `docs/release-notes.md` file does not flag breaking changes in the matcher API (e.g., `WithinAbsMatcher` constructor signature changes, removed matcher aliases) across major versions. Add a "Breaking Changes" section per release in `docs/release-notes.md`, update `docs/matchers.md` with migration notes for deprecated matcher APIs, and cross-reference from `docs/migrate-v2-to-v3.md`.

## Medium

### M1: Implement parametrized test case registration

Add `TEST_CASE_P(name, generator)` macro that registers a test case once but runs it for each value produced by a generator. This requires modifying `internal/catch_test_case_registry_impl.cpp` to support parameterized registration, adding parameter display in test names, and ensuring each parameter run is reported independently in all reporters.

### M2: Add `REQUIRE_THROWS_MATCHES` with exception matcher chaining

Implement matcher composition for exception assertions: `REQUIRE_THROWS_MATCHES(expr, ExType, HasMessage("x") && HasCode(42))`. Requires extending the exception matchers in `matchers/catch_matchers_exception.hpp`, adding `HasCode` matcher, and modifying the decomposer in `internal/catch_decomposer.hpp` to support matcher conjunction on exception types.

### M3: Implement test dependency declarations with ordering

Add `TEST_CASE("b", "[depends:a]")` syntax that declares test "b" depends on test "a" completing successfully. Requires modifying the tag parser in `internal/catch_test_spec_parser.cpp`, the test case registry ordering in `internal/catch_test_case_registry_impl.cpp`, and the run context in `internal/catch_run_context.cpp` to skip dependents when their prerequisite fails.

### M4: Add structured key-value output to benchmark results

Extend `benchmark/detail/catch_benchmark_stats.hpp` to support custom key-value metrics alongside timing data. Users should be able to call `BENCHMARK_METRIC("throughput_mbps", value)` within a benchmark, and all reporters should output these custom metrics alongside the standard mean/stddev/outliers.

### M5: Implement parallel section execution within a test case

Add `PARALLEL_SECTION("name")` that allows independent sections within a test case to execute concurrently using `std::thread`. Requires extending the section tracker in `internal/catch_test_case_tracker.cpp`, adding synchronization for assertion reporting in `internal/catch_run_context.cpp`, and ensuring reporters handle interleaved section results correctly.

### M6: Add user-defined properties API to JUnit reporter

The JUnit reporter in `reporters/catch_reporter_junit.cpp` already emits `<system-out>` / `<system-err>` per test case and a `<properties>` block with built-in fields (`random-seed`, `filters`). What is missing is a user-facing API that lets test authors attach arbitrary key-value metadata to a test case (e.g., `CATCH_TEST_PROPERTY("ticket", "PROJ-123")`), have those pairs stored as `<property>` elements inside the per-`<testsuite>` `<properties>` block, and be accessible to other reporters. Requires adding a property-recording API in `interfaces/` or `internal/catch_test_case_info.hpp`, propagating collected properties through `TestCaseStats`, wiring them into the JUnit XML writer, and exposing them in at least the JSON reporter output. Update `docs/reporters.md` with the new API and CI integration examples.

### M7: Implement generator shrinking for property-based testing

Add shrinking support to generators in `generators/catch_generators.hpp`: when a `GENERATE`-based test fails, automatically find a minimal failing input by shrinking the generated value. Requires adding a `Shrinkable<T>` wrapper, a shrink loop in the generator tracker, and integration with the console reporter to display both original and shrunk values.

### M8: Add test retry on failure with configurable attempt limit

Implement `--retry-failed <N>` CLI option that re-runs failed tests up to N times before declaring them as failures. Requires modifying the CLI parser in `internal/catch_commandline.cpp`, the run context loop in `internal/catch_run_context.cpp`, and all reporters to annotate retried tests with attempt numbers and only report the final status.

### M9: Implement custom reporter plugin loading at runtime

Add `--load-reporter <path>` CLI option to dynamically load reporter shared libraries. Requires adding a plugin loader in `internal/`, extending the reporter registry in `internal/catch_reporter_registry.cpp` to support dynamic registration, defining a stable C ABI for reporter plugins, and modifying the CLI parser.

### M10: Add source location tracking for matchers in failure messages

When a matcher assertion fails, the error message shows the expression but not where the matcher was defined. Extend `matchers/catch_matchers.hpp` to capture `std::source_location` at matcher construction, and modify the assertion handler in `internal/catch_assertion_handler.cpp` to include the matcher definition site in the failure output.

### M11: Improve CMake and build system configuration

Overhaul the build system configuration: update `CMakeLists.txt` with modern CMake 3.20+ `FetchContent` patterns and target-based dependency management; add `CMakePresets.json` presets for common build configurations (debug, release, sanitizers, coverage); configure `meson.build` to match CMake feature parity; update `Doxyfile` with project-specific settings for API documentation generation; add `codecov.yml` coverage thresholds and path exclusions; and update `appveyor.yml` Windows CI with MSVC and MinGW matrix. Changes span `CMakeLists.txt`, `CMakePresets.json`, `meson.build`, `meson_options.txt`, `Doxyfile`, `codecov.yml`, `appveyor.yml`, and `CMake/` directory.

## Wide

### W1: Implement snapshot testing framework

Add `CHECK_SNAPSHOT(name, value)` macro that compares serialized values against stored reference files, with `--update-snapshots` flag to regenerate. Requires a new `snapshots/` subsystem for file I/O and diffing, integration with the assertion handler, modifications to all reporters for snapshot diff display, a CLI option in `internal/catch_commandline.cpp`, and a snapshot serialization protocol supporting custom types.

### W2: Add test coverage integration and reporting

Implement built-in coverage tracking that records which `SECTION` and `TEST_CASE` blocks are executed per source file. Add `--coverage-report <path>` CLI option producing an HTML or JSON report. Requires compiler-assisted instrumentation hooks in `internal/catch_run_context.cpp`, a coverage data store, a new coverage reporter in `reporters/`, and integration with the test case registry for mapping tests to source locations.

### W3: Implement distributed test execution with result aggregation

Add `--distribute <worker-count>` mode that splits tests across child processes, executes them in parallel, and aggregates results. Requires a coordinator in `internal/` that uses the sharding logic from `catch_sharding.hpp`, IPC for result collection, modifications to all reporters for merged output, handling of crashed workers, and a unified exit code based on aggregated results.

### W4: Add mocking framework integrated with Catch2 assertions

Implement `MOCK_METHOD(Class, method, ReturnType(Args...))` that creates mock objects verified through Catch2's assertion system. Requires a mock registry parallel to the test registry, expectation recording and verification in `internal/`, automatic mock verification at section/test end via the tracker, integration with matchers for argument matching, and failure reporting through the existing decomposer.

### W5: Implement test execution profiling and bottleneck detection

Add `--profile` mode that tracks timing for each test case, section, assertion, and generator step. Produce a flame-graph-compatible output and a summary of slowest tests. Requires timing instrumentation in `internal/catch_run_context.cpp`, section tracker, generator tracker, a profiling data model, a new profile reporter in `reporters/`, and CLI integration.

### W6: Add data-driven testing with external data sources

Implement `TEST_CASE_DATA("name", "data.csv")` and `TEST_CASE_DATA("name", "data.json")` that load test parameters from external files at test registration time. Requires a new CSV parser and a new JSON reader module in `internal/` (distinct from the write-only `internal/catch_jsonwriter.cpp`), a data-driven test registrar that generates one virtual test case per data row in `internal/catch_test_case_registry_impl.cpp`, parameter injection into test functions via a typed accessor, per-row reporting in all reporters (`reporters/catch_reporter_console.cpp`, `catch_reporter_junit.cpp`, `catch_reporter_json.cpp`, `catch_reporter_xml.cpp`), and a `--data-dir` CLI option in `internal/catch_commandline.cpp` for base path configuration.

### W7: Implement fuzzing mode with corpus management

Add `FUZZ_TEST_CASE("name", Type)` that generates random inputs and tracks inputs that trigger new code paths or assertion failures. Requires a corpus manager for storing interesting inputs, mutation strategies in `generators/`, integration with sanitizers for crash detection, a fuzzing runner in `internal/`, progress reporting, and `--fuzz-duration` / `--fuzz-corpus-dir` CLI options.

### W8: Add hierarchical test organization with fixture inheritance

Implement `TEST_SUITE("suite")` blocks that group test cases with shared fixtures and nested sub-suites. Requires a suite registry parallel to test cases in `internal/`, fixture lifecycle management (setup/teardown per suite), modifications to all reporters for hierarchical output, tag inheritance from parent suites, and `--list-suites` CLI option.

### W9: Implement approval testing workflow

Add `APPROVE(value)` macro that on first run saves the output as approved baseline, and on subsequent runs compares against the approved file. Include `--approve` flag to accept current output, diff display in reporters, binary file support, and custom normalizers. Requires a new approval subsystem, file management, integration with the assertion handler, modifications to console/JSON/JUnit reporters for diff output, and CLI extensions.

### W10: Add cross-compilation test execution support

Implement `--crosscompile-run <command>` that executes tests on a remote target or emulator, collects results via structured output, and presents them locally. Requires splitting the runner into compile-side and execute-side components in `internal/`, a structured result protocol (extending JSON reporter), result deserialization and re-emission through local reporters, timeout handling for remote execution, and CLI integration for target configuration.

### W11: Overhaul Doxygen and Markdown documentation

Comprehensively restructure the project's documentation: regenerate Doxygen API documentation from updated `Doxyfile` with complete `src/catch2/` coverage; update `docs/tutorial.md` and `docs/test-cases-and-sections.md` with modern C++17/20 patterns; consolidate `docs/cmake-integration.md` with `CMakeLists.txt` examples for `FetchContent`, `find_package`, and submodule workflows; update `docs/command-line.md` with all current CLI options; add a `docs/ci-and-misc.md` section for `codecov.yml` and `appveyor.yml` CI integration; update `README.md` with feature matrix and quick-reference installation instructions; refresh `CODE_OF_CONDUCT.md` and `MAINTAINERS.md`; and add `SECURITY.md` vulnerability reporting process. Changes span `docs/` (tutorial.md, matchers.md, reporters.md, cmake-integration.md, command-line.md, ci-and-misc.md, release-notes.md, contributing.md), `Doxyfile`, `README.md`, `CODE_OF_CONDUCT.md`, `MAINTAINERS.md`, and `SECURITY.md`.
