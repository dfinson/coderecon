# xunit/xunit

| Field | Value |
|-------|-------|
| **URL** | https://github.com/xunit/xunit |
| **License** | Apache-2.0 |
| **Language** | C# |
| **Scale** | Large |
| **Category** | Test framework |
| **Set** | eval |
| **Commit** | `63aad206c62c2db373a9420486aa8ebc1a3daad9` |

## Why this repo

- **Multi-project solution**: Core framework (`xunit.v3.core`), assertions (`xunit.v3.assert`), runner infrastructure (`xunit.v3.runner.common`, `.utility`, `.console`, `.msbuild`), common abstractions (`xunit.v3.common`)
- **Deep pipeline architecture**: Discovery → filtering → ordering → execution → reporting, with extensibility at every stage
- **Backward compatibility**: Supports v1, v2, and v3 test assemblies through adapter layers and Microsoft Testing Platform integration

## Structure overview

```
src/
├── xunit.v3.core/               # Test framework core
│   ├── Runners/                 # Assembly/collection/class/method/test runners
│   ├── Framework/               # Discoverers, front controller, test framework
│   ├── Attributes/              # BeforeAfterTest, DataAttribute, MemberData
│   ├── ObjectModel/             # XunitTest, XunitTestCase, XunitTestAssembly
│   ├── Messages/                # Message types for pipeline communication
│   ├── Internal/                # MessageBus, TestContext, runner helpers
│   └── Abstractions/            # Interfaces for attributes, object model, runners
├── xunit.v3.assert/             # Assert methods (Equal, Contains, Throws, etc.)
├── xunit.v3.common/             # Shared types, serialization, utilities
│   ├── Options/                 # ExplicitOption, ParallelAlgorithm, TestMethodDisplay
│   ├── Serialization/           # JSON serialization for test messages
│   └── Utility/                 # DisposalTracker, UniqueIDGenerator, TypeHelper
├── xunit.v3.runner.common/      # Shared runner infrastructure
│   ├── Filtering/               # XunitFilters, query filter parser, trait filters
│   ├── Sinks/                   # ExecutionSink, TestDiscoverySink, event sinks
│   ├── Reporters/               # Registered runner reporters
│   ├── Transforms/              # Output transforms (XML, JSON, etc.)
│   └── Configuration/           # JSON config reader
├── xunit.v3.runner.console/     # Console runner executable
├── xunit.v3.runner.msbuild/     # MSBuild task runner
├── xunit.v3.runner.utility/     # Runner utility library
│   ├── SimpleRunner/            # Simplified AssemblyRunner API
│   ├── Frameworks/              # v1/v2/v3 framework adapters
│   └── Configuration/           # Configuration readers
└── xunit.v3.mtp-v1/v2/         # Microsoft Testing Platform adapters
```

## Scale indicators

- ~1025 C# source files
- ~142K lines of code
- Multi-framework backward compatibility (v1, v2, v3)
- Extensive runner pipeline with pluggable components

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `XunitTestAssemblyRunnerBase` not short-circuiting to serial execution when `MaxParallelThreads` is set to 1

When `MaxParallelThreads` is configured to 1 in the JSON config, `XunitTestAssemblyRunnerBase.RunTestCollections()` still dispatches all test collections as concurrently-started tasks rather than taking the fully serial path (which is only used when `DisableParallelization` is `true`). `XunitTestAssemblyRunnerBaseContext.SetupParallelism()` creates a `SemaphoreSlim(initialCount: 1)` to throttle execution, but all collection tasks are still launched simultaneously before competing for the semaphore. This wastes thread-pool resources and causes all collection fixtures to be initialized in parallel even though the user intends single-threaded execution. Fix `RunTestCollections` in `XunitTestAssemblyRunnerBase` to take the serial base path when `MaxParallelThreads == 1`, matching the behavior of `DisableParallelization = true`.

### N2: Fix `CommandLineParserBase` incorrectly rejecting trait values that contain `=`

In `src/xunit.v3.runner.common/Parsers/CommandLineParserBase.cs`, the `OnTrait` and `OnTraitMinus` methods parse the `-trait` and `-trait-` command-line arguments by calling `option.Value.Split('=')` without a count limit. If the trait value contains an `=` character (e.g., `-trait "Status=result=pass"`), the split produces more than two pieces and the methods throw `"incorrect argument format for -trait"` instead of treating everything after the first `=` as the value. Fix both methods to use `Split('=', 2)` so that values containing `=` are handled correctly.

### N3: Add `TestMethodDisplay.ClassAndMethodWithParameters` option

The `TestMethodDisplay` enum in `xunit.v3.common/Options/` supports `ClassAndMethod` and `Method`. Add a `ClassAndMethodWithParameters` option that includes the serialized parameter values in the display name for `[Theory]` tests, making parameterized test names more distinctive in output.

### N4: Fix `UniqueIDGenerator.ForTestMethod` not including method parameter types, causing ID collisions for overloaded methods

In `xunit.v3.common/Utility/UniqueIDGenerator.cs`, `ForTestMethod` accepts only the parent class unique ID and the method name; it does not incorporate parameter type information. When two test methods share the same name but differ only in parameter types (overloads discoverable via reflection, such as when a custom discoverer enumerates all overloads of a base class method), they receive identical `ForTestMethod` IDs. Because `ForTestCase` uses the `ForTestMethod` ID as its parent, any downstream test cases for these overloads with identical arguments would also collide. Fix `ForTestMethod` to accept an optional `IReadOnlyList<string>? parameterTypes` parameter and include it in the hash computation.

### N5: Fix `ExecutionSink` delaying long-running test warnings by up to one extra interval for tests that start mid-period

In `src/xunit.v3.runner.common/Sinks/ExecutionSink.cs`, `ThreadWorker` rate-limits `SendLongRunningMessage()` to at most once per `LongRunningTestTime` by checking `now - lastTestActivity >= options.LongRunningTestTime`. A test that starts just after a timer fire will not satisfy the per-test `(now - startTime) >= LongRunningTestTime` condition at the NEXT fire (still short by the start offset), so it is not reported until the fire AFTER that — meaning the warning is delayed by up to `2 × LongRunningTestTime` from when the test actually started. Fix `ThreadWorker` so that the next check is scheduled at `min(testStartTime + LongRunningTestTime)` across all currently-running tests, ensuring each test case is detected within one timer-tick of its individual threshold.

### N6: Add `[RetryFact]` attribute for automatically retrying flaky tests

Add a `RetryFactAttribute` that retries a failing test up to a configurable number of times before reporting failure. Implement a corresponding `RetryFactDiscoverer` in the Framework directory and a `RetryTestCaseRunner` that wraps `XunitTestCaseRunner` with retry logic.

### N7: Fix `ConfigReader_Json` silently ignoring unknown configuration keys

In `src/xunit.v3.runner.common/Configuration/ConfigReader_Json.cs`, unknown keys in the JSON configuration file are silently ignored. A typo like `"maxParallelThread"` (missing 's') produces no warning, leading to confusing behavior where the setting appears ineffective. Update `README.md` with a comprehensive list of valid configuration keys and their expected types.

### N8: Fix `MemberDataAttributeBase` throwing `NullReferenceException` instead of a diagnostic error when `MemberType` points to an inaccessible type

In `xunit.v3.core/Attributes/MemberDataAttributeBase.cs`, when the `MemberType` parameter specifies a type that is not accessible from the test assembly (e.g., an `internal` type in a different assembly), `GetPropertyAccessor`, `GetFieldAccessor`, and `GetMethodAccessor` silently return `null` because reflection returns `null` for the inaccessible member. This causes `GetData()` to fall through all three accessors and throw a `DataAttributeException` whose message references the test class rather than the inaccessible `MemberType`, making the root cause hard to diagnose. Fix `GetData` to detect when `MemberType` is non-null but no accessor is found, and throw a `DataAttributeException` that names the specified `MemberType` and `MemberName` explicitly.

### N9: Fix `DisposalTracker.Clear()` silently dropping tracked objects without disposing them and throwing on post-disposal calls

In `xunit.v3.common/Utility/DisposalTracker.cs`, `Clear()` removes all tracked objects from the internal `Stack<object>` without calling `Dispose()` or `DisposeAsync()` on any of them, silently leaking resources. Additionally, calling `Clear()` after `DisposeAsync()` has already run throws `ObjectDisposedException` even though clearing an already-disposed tracker is a harmless no-op. Fix `DisposalTracker` by adding a `DisposeAllAsync()` method that disposes all tracked objects in LIFO order (identical to `DisposeAsync()`) and then clears the stack, and update `Clear()` to be a no-op (rather than throwing) when called after the tracker has been disposed.

### N10: Add test attachment size limit to `ExecutionSinkOptions`

The `ExecutionSinkOptions` in `src/xunit.v3.runner.common/Sinks/` allows configuring various execution behaviors but has no limit on test attachment sizes. Add a `MaxAttachmentSizeBytes` option that truncates or rejects attachments exceeding the configured limit, preventing memory exhaustion from large test output attachments.

### N11: Fix `BUILDING.md` missing instructions for building on ARM64 Linux and macOS

The `BUILDING.md` file documents build prerequisites for x64 Windows and Linux but omits ARM64-specific instructions. .NET SDK selection via `global.json` may resolve to an incompatible architecture. Add ARM64 build sections to `BUILDING.md`, document `global.json` rollForward policy for multi-architecture builds, and update `.github/ISSUE_TEMPLATE.md` with a build environment diagnosis template.

## Medium

### M1: Implement structured test output capture with named sections

Currently, `TestOutputHelper` in `xunit.v3.core/Framework/` captures all test output as a single string. Implement named output sections (e.g., `output.Section("Setup")`, `output.Section("Assertion")`) that partition captured output into labeled segments. Update the message pipeline, `TestOutput` message, and console/MSBuild reporters to display sectioned output. Add documentation for the new API in `docfx/` configuration.

### M2: Add test dependency declaration and execution ordering

Implement a `[DependsOn(typeof(OtherTest))]` attribute that declares execution dependencies between test classes. Add dependency resolution in `XunitTestCollectionRunner` that topologically sorts test classes, detects cycles, and skips dependent tests when prerequisites fail. Update `DefaultTestCaseOrderer` to respect dependency order.

### M3: Implement parallel test execution with resource locking

Add a `[RequiresResource("database")]` attribute and a resource lock manager in the runner pipeline. Test collections requiring the same resource are serialized, while tests needing different resources run in parallel. Implement the lock manager in `xunit.v3.core/Internal/`, update `TestCollectionRunner`, and integrate with the parallel scheduler.

### M4: Add real-time test execution progress API for IDE integration

Implement a communication channel in `InProcessFrontController` that streams test execution events to an external process via named pipes or Unix domain sockets. Add a `TestProgressReporter` sink that serializes `MessageSinkMessage` events in real-time. Update the message bus to support external subscribers alongside internal sinks.

### M5: Implement test data snapshot assertions

Add `Assert.MatchesSnapshot<T>(T actual, string snapshotName)` that compares objects against stored JSON snapshots. On first run, create the snapshot file. On subsequent runs, diff against the stored snapshot and report structural differences. Add snapshot update mode via configuration. Changes span `xunit.v3.assert/`, `xunit.v3.common/` for serialization helpers, and configuration.

### M6: Add test execution profiling with per-phase timing

Implement timing breakdown for each test execution phase: fixture construction, `BeforeAfterTest.Before`, test method execution, `BeforeAfterTest.After`, and fixture disposal. Add a `TestExecutionProfile` to the `TestPassed`/`TestFailed` messages. Update `XunitTestRunner` and `XunitTestCaseRunner` to capture phase timings, and update reporters to display profiling data.

### M7: Implement dedicated `[AsyncMemberData]` attribute and discoverer for async data generation

`MemberDataAttributeBase` in `xunit.v3.core/Attributes/` already handles `IAsyncEnumerable<object?>` return values inside `GetDataAsync()`, but there is no dedicated `[AsyncMemberData]` attribute that makes async data sources a first-class concept. Add an `AsyncMemberDataAttribute` that explicitly signals async data generation to the discoverer and test runner, implement a corresponding `AsyncMemberDataDiscoverer` in the Framework directory, and update `XunitDelayEnumeratedTheoryTestCase` to surface async enumeration errors distinctly from sync errors. Support configurable cancellation timeouts for async data generation via an attribute property.

### M8: Add test categorization and selective execution by category groups

Implement a category group system where traits can be grouped (e.g., `"Integration" = ["Database", "API", "Queue"]`). Add category group definitions to the JSON config, update `XunitFilters` and `QueryFilterParser` to resolve category groups, and implement group-aware filtering in the runner pipeline.

### M9: Implement flaky test detection and quarantine

Add automatic flaky test detection that tracks test result history across runs using a JSON database file. When a test alternates between pass/fail across N runs, mark it as flaky. Add a `[Quarantine]` auto-applied trait and a configuration option to skip or warn on quarantined tests. Changes span `ExecutionSink`, configuration, and filtering.

### M10: Add test matrix support for combinatorial `[Theory]` data

Implement `[MatrixData]` attribute that generates test cases from the Cartesian product of multiple data sources. `[MatrixData(nameof(Browsers), nameof(Resolutions))]` generates all combinations. Add `MatrixDataDiscoverer`, integrate with `TheoryDiscoverer`, and support filtering specific matrix combinations.

### M11: Improve CI pipeline with signed build validation and version management

Extend `.github/workflows/ci-signed.yaml` with additional validation steps: verify NuGet package signature consistency, validate `version.json` against Git tags, and run deterministic build checks. Update `global.json` with SDK version pinning policy documentation, add a release checklist to `.github/PULL_REQUEST_TEMPLATE.md`, and configure `docfx/filterConfig.yml` to exclude internal APIs from public documentation. Changes span `.github/workflows/ci-signed.yaml`, `.github/workflows/ci-unsigned.yaml`, `version.json`, `global.json`, `.github/PULL_REQUEST_TEMPLATE.md`, and `docfx/`.

## Wide

### W1: Implement distributed test execution across multiple machines

Add a coordinator/worker architecture for distributing test execution across machines. The coordinator discovers tests, partitions them across workers, dispatches work via gRPC, collects results, and produces unified reports. Changes span runner infrastructure, message serialization, sink aggregation, and add `xunit.v3.runner.distributed/` project.

### W2: Add code coverage integration with per-test attribution

Implement coverage collection that attributes line coverage to individual tests. Integrate with System.Diagnostics.DiagnosticSource to track which lines execute during each test. Aggregate per-test coverage into a per-assembly report. Changes span the test runner pipeline, `XunitTestRunner`, message types, reporting transforms, and add a coverage collection module.

### W3: Implement test impact analysis based on code changes

Add a system that determines which tests to run based on source code changes. Build a dependency graph from test-to-source mappings (via coverage data or static analysis). Given a set of changed files, compute the affected test set. Changes span discovery, filtering, configuration, a dependency graph builder, and add a `TestImpactAnalysis` project.

### W4: Add visual regression testing support

Implement a visual testing framework that captures screenshots or rendered output, compares against baselines using perceptual diff, and reports visual regressions. Add `Assert.MatchesVisualBaseline()`, image comparison algorithms, baseline management, and diff report generation. Changes span `xunit.v3.assert/`, add image processing utilities, and update reporters for visual diff output.

### W5: Implement comprehensive test execution recording and replay

Add test execution recording that captures all external interactions (file I/O, network, time, randomness) during test runs. Enable replay mode that replays recorded interactions for deterministic offline test execution. Changes span the runner pipeline, add recording/replay infrastructure in `xunit.v3.core/`, framework hooks for intercepting side effects, and storage management.

### W6: Add multi-framework test aggregation runner

Implement a runner that discovers and executes tests from xUnit, NUnit, and MSTest assemblies in a unified pipeline. Add framework adapters for NUnit and MSTest that translate their test models into xUnit's message pipeline. Changes span `xunit.v3.runner.common/Frameworks/`, message translation, discovery, execution, and unified reporting.

### W7: Implement test environment provisioning and lifecycle management

Add infrastructure for provisioning test environments (Docker containers, databases, cloud resources) before test execution and tearing them down after. Define environments in JSON config, implement provisioning providers, health checking, and shared environment reuse across test collections. Changes span configuration, `xunit.v3.core/Framework/`, runner infrastructure, and add an environments module.

### W8: Add performance benchmarking integrated with test execution

Implement a `[Benchmark]` attribute and benchmarking infrastructure that measures execution time, memory allocations, and GC pressure for annotated test methods. Add statistical analysis (mean, median, percentiles, outlier detection), regression detection against baselines, and benchmark result reporting. Changes span attributes, runners, object model, sinks, and reporters.

### W9: Implement live test dashboard with WebSocket streaming

Add a web-based test execution dashboard that displays real-time test progress, results, and statistics via WebSocket streaming. Implement a `DashboardReporter` that serves an embedded web UI, streams execution events to connected browsers, and persists run history. Changes span reporters, sinks, add an embedded HTTP/WebSocket server, and frontend assets.

### W10: Add mutation testing integration for test suite quality assessment

Implement mutation testing that systematically modifies source code (mutants) and verifies tests catch the mutations. Add a mutation engine with operator library (arithmetic, conditional, null, void), mutant generation, selective test execution per mutant, and survival analysis reporting. Changes span the runner pipeline, add a mutation engine project, test mapping infrastructure, and report generation.

### W11: Create comprehensive docfx-based API documentation site

Build a complete API documentation website using the existing `docfx/` infrastructure: configure `docfx/docfx.json` to generate API reference from all `xunit.v3.*` projects' XML doc comments; add conceptual documentation pages covering test lifecycle, parallel execution, custom extensibility, and migration from v2; update `docfx/filterConfig.yml` to properly categorize public vs. internal APIs; add tutorial content to `BUILDING.md` for documentation contributions; update `README.md` with documentation site links and badges; and configure `.github/workflows/` to deploy documentation on release. Changes span `docfx/`, `BUILDING.md`, `README.md`, `.github/PULL_REQUEST_TEMPLATE.md`, and `.github/workflows/`.
