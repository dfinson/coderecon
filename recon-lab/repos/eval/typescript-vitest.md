# vitest-dev/vitest

| Field | Value |
|-------|-------|
| **URL** | https://github.com/vitest-dev/vitest |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Large |
| **Category** | Test framework |
| **Set** | eval |
| **Commit** | `b0b83e21424a9e9330c07b5f1d5ed82e01aa3f29` |

## Why this repo

- **Multi-package monorepo**: Core runner, snapshot engine, expect/assertions, mocker, coverage providers, browser testing, UI dashboard, spy utilities
- **Well-structured**: Clear package separation under `packages/` with distinct concerns per package
- **Rich history**: 10K+ commits, Vite-native test framework with growing adoption

## Structure overview

```
packages/
├── vitest/src/              # Core test framework
│   ├── node/                # Node-side: core orchestrator, config, CLI, pools, reporters, watcher
│   ├── runtime/             # Test runtime: module runner, workers, VM isolation
│   ├── integrations/        # chai, mock, snapshot, coverage, environment adapters
│   ├── api/                 # WebSocket API for UI communication
│   ├── typecheck/           # Type-checking mode
│   └── utils/               # Shared utilities
├── runner/src/              # Test runner: suite collection, hook execution, fixture injection
├── expect/src/              # Assertion library: jest-expect, asymmetric matchers, custom matchers
├── snapshot/src/            # Snapshot testing: inline snapshots, file snapshots, serialization
├── spy/src/                 # Spy/stub utilities (tinyspy wrapper)
├── mocker/src/              # Module mocking: auto-mocker, mock registry
├── coverage-v8/src/         # V8-based code coverage provider
├── coverage-istanbul/src/   # Istanbul-based code coverage provider
├── browser/src/             # Browser test environment orchestration
├── browser-preview/src/     # Browser preview UI for test execution
├── ui/                      # Dashboard UI (Vue-based)
├── utils/src/               # Shared utilities: error processing, diff, serialization
├── web-worker/src/          # Web Worker test support
└── ws-client/src/           # WebSocket client for IDE integrations
```

## Scale indicators

- ~1900 TypeScript source files across packages
- ~80K lines of source code
- Multiple test execution pools (threads, forks, VM isolation)
- Coverage, snapshot, browser, and mocking subsystems

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `test.each` not preserving test context when used with fixtures

When `test.each(cases)` is combined with fixture injection via `test.extend`, the fixtures are not injected into the parameterized test function. The `suite.ts` in `packages/runner/src/` wraps each `each` handler as `() => handler(...items)`, discarding the fixture context parameter, while `test.for` correctly passes it via `(ctx) => handler(item, ctx)`. The fix propagates fixture definitions through `each` wrappers in `packages/runner/src/suite.ts`.

### N2: Fix snapshot update mode not clearing obsolete snapshots from inline snapshot files

When running with `--update`, removed inline snapshots in test files leave stale `toMatchInlineSnapshot()` calls with old values. The `SnapshotState` class in `packages/snapshot/src/port/state.ts` tracks unused keys for file-based snapshots via `_uncheckedKeys` and clears them on update, but maintains no equivalent tracking for inline snapshots stored in `_inlineSnapshots`. The fix adds unchecked-inline-snapshot tracking to `SnapshotState` so stale inline snapshot calls are removed during `--update` runs.

### N3: Add `--fail-on-flaky` CLI flag to mark flaky tests as failures

When a test passes on retry after initially failing, it's reported as passed. Add a CLI flag that treats retried-then-passed tests as failures. Changes touch `packages/vitest/src/node/cli/` for the flag and `packages/runner/src/run.ts` for retry result handling.

### N4: Fix `vi.useFakeTimers()` not affecting `setTimeout` inside `Promise.then` chains

When fake timers are active, `setTimeout` calls inside resolved promise `.then()` callbacks use real timers because the microtask runs after the fake timer context is restored. The timer installation in `packages/vitest/src/integrations/vi.ts` doesn't intercept microtask-scheduled timers.

### N5: Fix coverage report showing 0% for files that only export types

When a TypeScript file exports only type definitions, V8 coverage reports it as 0% covered even though there's no executable code. The coverage provider in `packages/coverage-v8/src/provider.ts` should exclude type-only files from the coverage report.

### N6: Fix `expect.soft()` throwing when called inside `beforeEach`/`afterEach` hooks

When `expect.soft(val).toBe(x)` is placed inside a `beforeEach` or `afterEach` hook, it throws `'expect.soft() can only be used inside a test'` because the `vitest-test` flag is not set in the assertion context during hook execution. The failures should instead be collected and associated with the corresponding test. Changes touch `packages/expect/src/utils.ts` to resolve the active test from runner state when the assertion flag is absent, and `packages/runner/src/run.ts` to expose the hook's parent test reference to the assertion layer.

### N7: Fix `toMatchObject` not handling `Error` instances correctly in deep comparisons

When `expect(obj).toMatchObject({ err: new TypeError('message') })` compares objects containing `Error` fields, the comparison uses strict reference equality instead of property-based matching. The `isObjectWithKeys` function in `packages/expect/src/jest-utils.ts` explicitly excludes `Error` instances via `!isError(a)`, causing `subsetEquality` to return `undefined` for Error values and fall back to `equals()`, which requires the same Error instance. The fix adds `Error` handling to `subsetEquality` in `packages/expect/src/jest-utils.ts` to compare by `message`, `name`, and `cause` properties.

### N8: Fix test file watcher not detecting changes in symlinked dependencies

When test files import from symlinked local packages, changes to those packages don't trigger re-runs. The watcher in `packages/vitest/src/node/watcher.ts` resolves symlinks but doesn't add the resolved paths to the watch list.

### N9: Add `onTestRetry` hook that runs before each retry attempt

Tests that use retry need a way to reset state or log diagnostics specifically between retry attempts, distinct from `onTestFailed` (which runs on any failure, including the final one) and `onTestFinished` (which runs after the test is fully done). Add `onTestRetry(callback)` to the test context in `packages/runner/src/context.ts` that fires before each retry attempt, with execution added to the retry loop in `packages/runner/src/run.ts`.

### N10: Fix `vi.mock()` hoisting not working with dynamic import expressions

When `vi.mock('./module')` is used alongside `const mod = await import('./module')`, the mock factory isn't applied because the hoisting transform in `packages/vitest/src/node/plugins/` doesn't intercept dynamic `import()` calls following `vi.mock()`.

### N11: Update CLI documentation for the `--fail-on-flaky` flag

The `--fail-on-flaky` CLI flag is undocumented. Add usage examples and behavior description to `docs/guide/cli.md`. Update `docs/guide/filtering.md` with flaky test handling guidance. Add a configuration reference entry in `docs/config/retry.md` covering the interaction between retry settings and flaky detection.

## Medium

### M1: Implement `watchExclude` patterns to prevent heavy tests from re-running in watch mode

In watch mode, every file change that touches the dependency graph of a test file triggers a re-run. Add a `watchExclude` glob-pattern list that prevents matched test files from being scheduled for re-run even when their imported modules change, enabling developers to exclude slow integration tests from automatic re-runs. Changes span `packages/vitest/src/node/watcher.ts` for exclusion logic during change detection, `packages/vitest/src/node/config/resolveConfig.ts` for pattern normalization, `packages/vitest/src/node/types/config.ts` for the new option type, `packages/vitest/src/node/cli/cli-config.ts` for the `--watch-exclude` CLI flag, `packages/vitest/src/node/specifications.ts` for spec filtering, and `packages/vitest/src/node/core.ts` for integration with the watch loop. Update `docs/config/watchexclude.md` and `docs/guide/filtering.md` with documentation and usage examples.

### M2: Add per-suite concurrency limit for `describe.concurrent()`

Currently `describe.concurrent()` shares the global `maxConcurrency` limit across all concurrent suites and tests. Implement an optional `concurrency` parameter on `describe.concurrent({ concurrency: N }, () => {...})` that scopes parallelism to just the tests within that specific suite, independent of the global limit. Changes span `packages/runner/src/types/tasks.ts` for the suite-level concurrency option, `packages/runner/src/suite.ts` for parsing the option and wiring up the scoped limiter, `packages/runner/src/run.ts` for creating and applying a suite-scoped `ConcurrencyLimiter` when the option is set, `packages/runner/src/utils/limit-concurrency.ts` for any required extensions, `packages/runner/src/fixture.ts` for fixture isolation within bounded concurrency, and `packages/vitest/src/node/config/resolveConfig.ts` for validation.

### M3: Implement snapshot testing with custom serializers per test file

Add the ability to register custom snapshot serializers scoped to a test file via `vi.snapshotSerializer(serializer)`. Changes span `packages/snapshot/src/client.ts` for per-file serializer storage, `packages/snapshot/src/port/state.ts` for state tracking, and `packages/vitest/src/integrations/snapshot/` for the API.

### M4: Add test dependency declaration and ordering

Implement `test.dependsOn('other test name')` that ensures a test only runs after its dependency passes. Detect circular dependencies and skip dependents when a dependency fails. Changes span `packages/runner/src/collect.ts` for dependency graph, `packages/runner/src/run.ts` for ordered execution, and `packages/vitest/src/node/sequencers/`.

### M5: Implement automatic test retry with exponential backoff

Extend the retry mechanism to support `retry: { count: 3, delay: 1000, backoff: 'exponential' }`. Changes span `packages/runner/src/run.ts` for retry scheduling with delays, `packages/vitest/src/node/config/resolveConfig.ts` for config options, and type definitions.

### M6: Add module mock validation to detect unmocked imports

Implement `vi.mock('./module', { strict: true })` that throws when the test code accesses a property not explicitly defined in the mock factory. Changes span `packages/mocker/src/automocker.ts` for proxy-based strict mocking, `packages/mocker/src/registry.ts` for mock tracking, and `packages/vitest/src/integrations/mock/`.

### M7: Implement coverage diff reporting against a baseline

Add `--coverage-baseline coverage-baseline.json` that compares current coverage against a saved baseline and reports regressions. Changes span `packages/coverage-v8/src/provider.ts` and `packages/coverage-istanbul/src/` for baseline loading and comparison, and `packages/vitest/src/node/coverage.ts` for orchestration.

### M8: Implement maximum total run-duration enforcement with `maxRunDuration`

Add a `maxRunDuration` configuration option (in milliseconds) that causes the entire Vitest run to fail with a timeout error if the total wall-clock duration exceeds the limit. Unlike per-test `timeout`, this is a global ceiling for the complete test run, useful for CI pipelines with strict time budgets. Changes span `packages/runner/src/types/tasks.ts` for propagating deadline information, `packages/vitest/src/node/core.ts` for starting a run-level timer and aborting when exceeded, `packages/vitest/src/node/config/resolveConfig.ts` for config defaults, `packages/vitest/src/node/types/config.ts` for the option type, `packages/vitest/src/node/cli/cli-config.ts` for the `--max-run-duration` CLI flag, `packages/vitest/src/node/reporters/summary.ts` for displaying elapsed time against the budget, and `packages/vitest/src/node/reporters/json.ts` for including run duration in JSON output.

### M9: Implement per-test environment variable overrides via test options

Add an `env` option to individual tests and suites so that `process.env` variables are automatically set before the test runs and restored afterwards, without needing explicit `vi.stubEnv()` calls. Changes span `packages/runner/src/types/tasks.ts` for the `env` field on test options, `packages/runner/src/suite.ts` for parsing the option, `packages/runner/src/run.ts` for applying and restoring env overrides around each test, `packages/vitest/src/node/config/resolveConfig.ts` for global env defaults, `packages/vitest/src/node/types/config.ts` for the config type, and `packages/vitest/src/node/pools/` for propagating env maps across worker boundaries.

### M10: Add console output capture fixture for asserting on logged output

Implement a built-in `captureConsole` fixture that intercepts `console.log`, `console.warn`, and `console.error` output during a test and exposes the captured lines for assertion, without relying on `vi.spyOn`. Changes span `packages/runner/src/fixture.ts` for the capture fixture lifecycle, `packages/runner/src/context.ts` for context integration, `packages/runner/src/types/tasks.ts` for captured output types, `packages/runner/src/run.ts` for console interception and restoration around each test, `packages/vitest/src/integrations/vi.ts` for the public `vi.captureConsole()` API surface, and `packages/vitest/src/node/reporters/` for surfacing captured output in test failure reports.

### M11: Add contributor documentation and CI validation for test tagging

Update `CONTRIBUTING.md` with tag system development guidelines and architecture overview. Add advanced tag filtering examples to `docs/guide/test-tags.md` covering tag composition and exclusion patterns. Update tag-related configuration documentation in `docs/config/tags.md` with all available options. Add a CI validation job in `.github/workflows/ci.yml` that runs the tag-filtered test suite.

## Wide

### W1: Implement distributed test execution across multiple machines

Add a coordinator/worker architecture that splits test files across machines, collects results, and produces merged reports. Changes span `packages/vitest/src/node/core.ts` for coordination, `packages/vitest/src/node/pools/` for remote pool, `packages/runner/src/` for result serialization, `packages/vitest/src/node/reporters/` for merged output, `packages/vitest/src/api/` for network protocol, and add a distributed execution module.

### W2: Add performance regression testing support

Implement performance metric capture and regression detection for browser tests: record Core Web Vitals (LCP, CLS, INP, TTFB) and custom timings, diff against stored baselines, apply configurable regression thresholds, and output visual trend reports. Changes span `packages/browser/src/` for performance metric capture commands, `packages/snapshot/src/` for numeric baseline storage and comparison, `packages/vitest/src/node/reporters/` for performance regression reports, `packages/expect/src/` for `toMatchPerformanceSnapshot()` matcher, `packages/ui/` for performance trend dashboard, `packages/vitest/src/node/config/` for threshold configuration per metric, `packages/vitest/src/node/coverage.ts` for performance data collection orchestration, and add metric serialization and diff utilities.

### W3: Implement test analytics and trend tracking

Add persistent test result storage with historical trend analysis: flaky test detection, duration trends, failure rate tracking, and test health dashboards. Changes span `packages/vitest/src/node/reporters/` for data collection, `packages/vitest/src/node/cache/` for storage, `packages/ui/` for dashboard views, `packages/vitest/src/node/core.ts` for aggregation, `packages/ws-client/src/` for live updates, and add analytics infrastructure. Add an analytics dashboard setup guide to `docs/guide/` and a CI workflow for metrics collection in `.github/workflows/ci.yml`.

### W4: Add contract testing framework for API boundary validation

Implement contract definition between provider and consumer test suites: schema validation, mock server generation, and contract verification. Changes span `packages/expect/src/` for contract matchers, `packages/runner/src/` for contract test type, `packages/vitest/src/node/` for contract resolution, `packages/mocker/src/` for mock server, `packages/vitest/src/node/reporters/` for contract reports, and add a contracts module.

### W5: Implement intelligent test prioritization with failure prediction

Add ML-based test ordering that predicts likely failures based on code changes, test history, and file proximity. Changes span `packages/vitest/src/node/sequencers/` for prediction-based sequencer, `packages/vitest/src/node/git.ts` for change analysis, `packages/vitest/src/node/cache/` for history storage, `packages/vitest/src/node/core.ts` for data collection, `packages/vitest/src/node/config/` for configuration, and add a prediction engine.

### W6: Add comprehensive test environment management

Implement declarative test environment configuration with automatic setup/teardown for Docker containers, databases, and external services. Changes span `packages/vitest/src/integrations/env/` for environment definitions, `packages/vitest/src/node/globalSetup.ts` for lifecycle, `packages/vitest/src/node/pools/` for environment isolation, `packages/runner/src/` for environment injection, `packages/vitest/src/node/config/` for declarations, and add environment provider modules.

### W7: Implement mutation testing to measure test effectiveness

Add a mutation testing mode that modifies source code and checks whether tests catch the mutations. Changes span `packages/vitest/src/node/core.ts` for mutation orchestration, `packages/vitest/src/node/vite.ts` for source mutation via Vite plugins, `packages/runner/src/` for mutation-aware execution, `packages/vitest/src/node/reporters/` for mutation reports, `packages/coverage-v8/src/` for correlation with coverage, and add a mutation engine.

### W8: Add IDE protocol server for deep editor integration

Implement a Language Server Protocol-like server for test discovery, execution, and debugging from editors. Changes span `packages/vitest/src/api/` for protocol definition, `packages/ws-client/src/` for client library, `packages/vitest/src/node/core.ts` for test lifecycle events, `packages/vitest/src/node/reporters/` for streaming results, `packages/runner/src/` for debug breakpoint support, and add protocol handler modules.

### W9: Implement snapshot migration framework for cross-version compatibility

Add tools to migrate snapshot files between serializer versions, handle format changes, and validate snapshot integrity. Changes span `packages/snapshot/src/client.ts` for versioned formats, `packages/snapshot/src/port/state.ts` for migration logic, `packages/snapshot/src/port/utils.ts` for format detection, `packages/vitest/src/node/cli/` for migration commands, `packages/snapshot/src/port/plugins.ts` for serializer versioning, and add migration utilities.

### W10: Add multi-project workspace test orchestration

Implement cross-project test execution for monorepos: shared configuration, project dependency ordering, cross-project coverage, and unified reporting. Changes span `packages/vitest/src/node/core.ts` for workspace coordination, `packages/vitest/src/node/projects/` for project resolution, `packages/vitest/src/node/config/` for workspace config, `packages/vitest/src/node/reporters/` for merged output, `packages/coverage-v8/src/` for cross-project coverage, and `packages/vitest/src/node/pools/` for project isolation.

### W11: Overhaul documentation and CI for distributed test execution

Add a distributed execution guide to `docs/guide/` covering coordinator/worker setup and deployment topologies. Add distributed configuration reference to `docs/config/`. Update `.github/workflows/ci.yml` with a multi-machine distributed test job. Update `CONTRIBUTING.md` with distributed development setup instructions. Update `pnpm-workspace.yaml` to include the distributed execution package and update `netlify.toml` for the new documentation pages.
