# App-vNext/Polly

| Field | Value |
|-------|-------|
| **URL** | https://github.com/App-vNext/Polly |
| **License** | BSD-3-Clause |
| **Language** | C# |
| **Scale** | Medium (multi-module project) |
| **Category** | Resilience framework |
| **Set** | Cutoff |
| **Commit** | `6da1b7945ca95c55ffa2f12006c7f0dcecc9eba4` |

## Why this repo

- **Composable strategy architecture**: V8+ redesigned around
  `ResiliencePipeline` and composable strategies — each strategy
  (Retry, CircuitBreaker, Timeout, Hedging, RateLimiter, Fallback)
  is a self-contained module with options, internal state, and
  telemetry integration. Tasks require navigating across strategy
  boundaries, the pipeline builder, and the telemetry/registry layers.
- **Well-structured**: Source under `src/Polly.Core/` with clear
  per-strategy directories (`Retry/`, `CircuitBreaker/`, `Timeout/`,
  `RateLimiter/`, `Hedging/`, `Fallback/`), shared infrastructure
  in `Telemetry/` and `Registry/`, and the pipeline builder as the
  composition root.
- **Rich history**: 13K+ stars, widely adopted in .NET microservice
  architectures. Issues span correctness under concurrency, strategy
  composition edge cases, and telemetry fidelity.
- **Permissive**: BSD-3-Clause license.

## Structure overview

```
src/Polly.Core/
├── ResiliencePipeline.cs              # Main entry — Execute, ExecuteAsync
├── ResiliencePipeline.TResult.cs      # Generic result pipeline
├── ResiliencePipelineBuilder.cs       # Composition builder
├── ResiliencePipelineBuilder.TResult.cs
├── ResilienceContext.cs               # Per-execution context (state, cancellation)
├── ResilienceStrategyOptions.cs       # Base options for all strategies
├── Outcome.cs                        # Result-or-exception wrapper
├── Retry/
│   ├── RetryStrategyOptions.cs        # MaxRetryAttempts, Delay, BackoffType
│   ├── RetryResilienceStrategy.cs     # Core retry logic
│   ├── RetryConstants.cs              # Default values
│   ├── RetryHelper.cs                 # Delay calculation (exponential, jitter)
│   └── OnRetryArguments.cs            # Event arguments
├── CircuitBreaker/
│   ├── CircuitBreakerStrategyOptions.cs
│   ├── CircuitBreakerResilienceStrategy.cs
│   ├── CircuitStateController.cs      # State machine (Closed→Open→HalfOpen)
│   ├── AdvancedCircuitBreakerOptions.cs
│   ├── BrokenCircuitException.cs
│   └── CircuitBreakerManualControl.cs
├── Timeout/
│   ├── TimeoutStrategyOptions.cs
│   ├── TimeoutResilienceStrategy.cs
│   └── TimeoutRejectedException.cs
├── RateLimiter/
│   ├── RateLimiterStrategyOptions.cs
│   ├── RateLimiterResilienceStrategy.cs
│   └── RateLimiterRejectedException.cs
├── Hedging/
│   ├── HedgingStrategyOptions.cs
│   ├── HedgingResilienceStrategy.cs
│   ├── HedgingHandler.cs
│   └── HedgingActionGeneratorArguments.cs
├── Fallback/
│   ├── FallbackStrategyOptions.cs
│   ├── FallbackResilienceStrategy.cs
│   └── FallbackHandler.cs
├── Telemetry/
│   ├── TelemetryUtil.cs
│   ├── ResilienceTelemetryDiagnosticSource.cs
│   ├── TelemetryOptions.cs
│   └── ExecutionAttemptArguments.cs
├── Registry/
│   ├── ResiliencePipelineRegistry.cs
│   ├── ResiliencePipelineProvider.cs
│   └── RegistryOptions.cs
└── Utils/
    ├── TimeProvider.cs
    ├── CancellationTokenSourcePool.cs
    └── SystemClock.cs
```

## Scale indicators

- ~80–100 C# source files under src/Polly.Core/
- ~15K lines of code
- 2–3 levels of namespace nesting
- Clear per-strategy module boundaries

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix RetryHelper jitter calculation producing negative delays

`RetryHelper.DecorrelatedJitterBackoffV2()` calculates the next delay
as `formulaIntrinsicValue = next - prev`, where `prev` is the
accumulated state from the previous attempt. When the exponential
formula produces a `next` value smaller than `prev` (which can occur
at high attempt numbers before the infinity guard kicks in), the tick
count goes negative. In release builds, the `Debug.Assert(ticks >= 0)`
is suppressed and `TimeSpan.FromTicks(ticks)` returns a negative
`TimeSpan`. Fix `RetryHelper.cs` to clamp `ticks` to zero before
constructing the return value, ensuring `TimeSpan.FromTicks` is never
called with a negative argument.

### N2: Fix CircuitBreakerManualControl.IsolateAsync not respecting cancellation

`CircuitBreakerManualControl.IsolateAsync()` transitions the circuit
to `Isolated` state but does not check the `CancellationToken` from
the `ResilienceContext`. If the token is already cancelled, the
isolation proceeds silently instead of throwing
`OperationCanceledException`. Fix to check cancellation before state
transition.

### N3: Fix TimeoutResilienceStrategy cleanup not protected by finally block

`TimeoutResilienceStrategy.ExecuteCore` acquires a
`CancellationTokenSource` from `_cancellationTokenSourcePool` and
registers a `CancellationTokenRegistration` for cleanup. However,
the cleanup sequence — `registration.Dispose()` followed by
`_cancellationTokenSourcePool.Return(cancellationSource)` — executes
outside any `finally` block. If an unexpected exception escapes the
cleanup sequence, the token source is leaked from the pool. Fix the
strategy to wrap both `registration.Dispose()` and
`_cancellationTokenSourcePool.Return(cancellationSource)` in a
`finally` block in `TimeoutResilienceStrategy.cs`, guaranteeing
cleanup under all execution paths.

### N4: Fix HedgingResilienceStrategy secondary contexts ignoring custom ResilienceContextPool

`TaskExecution<T>` initializes its cached secondary-action context
directly from `ResilienceContextPool.Shared` (field initializer:
`private readonly ResilienceContext _cachedContext = ResilienceContextPool.Shared.Get()`).
When a caller sets a custom `ResilienceContextPool` on
`ResiliencePipelineBuilderBase.ContextPool`, that pool is used for
primary execution contexts but is never consulted for the secondary
action contexts created by the hedging strategy. Fix by threading the
configured pool through `HedgingResilienceStrategy.cs` →
`HedgingController.cs` → `TaskExecution.cs`, replacing the hardcoded
`ResilienceContextPool.Shared.Get()` with the builder-supplied pool
(defaulting to `ResilienceContextPool.Shared` when none is set).

### N5: Fix RetryDelayGeneratorArguments not including previous attempt execution duration

`RetryDelayGeneratorArguments` provides the attempt number and outcome
to custom delay generators but does not include the execution duration
of the previous attempt. Delay generators that want to implement
latency-adaptive backoff (e.g., increasing delay when the upstream
is slow) must track timing externally. Fix
`RetryDelayGeneratorArguments.cs` to add a `Duration` property and
populate it from the execution time measured in
`RetryResilienceStrategy.cs`, matching the `Duration` already
available on `OnRetryArguments`.

### N6: Fix FallbackResilienceStrategy losing original exception when fallback itself fails

When `FallbackResilienceStrategy` invokes the fallback action and the
action itself throws an exception, the catch block returns
`Outcome.FromException<T>(e)` where `e` is only the fallback's
exception. The original exception that triggered the fallback (stored
in `outcome.Exception`) is silently discarded, making root-cause
analysis impossible. Fix `FallbackResilienceStrategy.cs` to set the
original exception as the `InnerException` of the fallback exception
when the fallback action fails, preserving the full exception chain
for callers and logging infrastructure.

### N7: Fix RetryResilienceStrategy not capping DelayGenerator results to MaxDelay

`RetryResilienceStrategy.ExecuteCore` applies `MaxDelay` only to the
algorithm-computed delay produced by `RetryHelper.GetRetryDelay`. When
`RetryStrategyOptions.DelayGenerator` is also set and returns a valid
`TimeSpan`, the returned value replaces the computed delay without
being subject to the `MaxDelay` cap. A custom `DelayGenerator`
returning 60 seconds when `MaxDelay` is 5 seconds will proceed
uncapped. Fix `RetryResilienceStrategy.cs` to apply the `MaxDelay`
cap to the delay returned by `DelayGenerator` (using the same
`Math.Min` / `TimeSpan` comparison logic already present in
`RetryHelper.GetRetryDelay`).

### N8: Fix ResiliencePipelineBuilderBase.CreateComponent not validating factory return value

`ResiliencePipelineBuilderBase.CreateComponent` calls the
user-supplied strategy factory (`entry.Factory(context)`) and
immediately assigns `strategy.Options = entry.Options` without
checking whether the factory returned `null`. If the factory returns
`null`, the assignment throws a `NullReferenceException` deep inside
`BuildPipelineComponent` with no indication of which factory is at
fault. Fix `ResiliencePipelineBuilderBase.cs` to validate the factory
return value immediately after the call and throw a descriptive
`InvalidOperationException` if it is `null`, making the error
actionable at the point of failure.

### N9: Fix OnCircuitOpenedArguments not including the failure rate that triggered the transition

`OnCircuitOpenedArguments` carries the break duration, outcome, and
manual flag but does not include the failure rate or failure count from
the health metrics sampling window that caused the circuit to open.
Subscribers to the `OnOpened` callback cannot assess the severity of
the failure pattern or verify which threshold was crossed. Fix
`OnCircuitOpenedArguments.cs` to add `FailureRate` and `FailureCount`
properties, and populate them from the health metrics in
`CircuitStateController.cs` when the circuit transitions to `Open`.

### N10: Fix ResilienceTelemetrySource missing a dedicated StrategyType field

`ResilienceTelemetrySource` exposes `StrategyName` (the
user-configurable `ResilienceStrategyOptions.Name`) but provides no
separate field for the immutable strategy type identifier (e.g.
`"Retry"`, `"CircuitBreaker"`, `"Timeout"`). When users override the
default `Name` with a custom value, the strategy type is lost from
telemetry events and listeners have no way to filter or aggregate
events by strategy type independently of the assigned name. Fix
`ResilienceTelemetrySource.cs` to add a `StrategyType` property, and
update `ResiliencePipelineBuilderBase.CreateComponent` to populate it
from a new `ResilienceStrategyOptions.StrategyType` property that each
built-in strategy sets to its well-known type name constant (e.g.
`RetryConstants.DefaultName`).

### N11: Update stryker mutation testing configuration and CI linting workflow

The `eng/stryker-config.json` targets `net10.0` and ignores `block`
and `statement` mutations, but does not exclude the `Simmy/` chaos
engineering directory which has intentionally non-deterministic
behaviour that produces false-positive mutation survivors. Add
`"Simmy"` to the `ignore-methods` list and add
`"src/Polly.Core/Simmy/**"` to a new `mutate` exclusion pattern.
Also update `.github/workflows/lint.yml` to run `dotnet format
--verify-no-changes` on pull requests, and update the existing
`.markdownlint.json` to add a heading-style rule enforcing consistent
ATX heading format across `docs/` markdown files.

## Medium

### M1: Add progressive delay backoff type to RetryHelper

Implement `DelayBackoffType.Progressive` that starts with a configured
initial delay, then advances by a fixed additive increment per attempt
(e.g., 100ms → 300ms → 500ms → 700ms). Unlike `Linear` which uses
multiples of the base delay, `Progressive` uses an absolute step. The
algorithm needs an `Increment` property on `RetryStrategyOptions`.
Changes span `DelayBackoffType.cs` for the new enum value,
`RetryHelper.cs` for the calculation, `RetryStrategyOptions.cs` for
the new `Increment` property, `RetryConstants.cs` for defaults, and
`RetryResilienceStrategy.cs` for wiring the increment into delay
generation. Also add a `docs/strategies/retry-progressive.md`
documentation page explaining the progressive backoff algorithm with
timing diagrams, and update `docs/toc.yml` to include the new page
under the Resilience strategies section.

### M2: Implement circuit breaker manual health reset with cold-start protection

Add `CircuitBreakerManualControl.ResetHealthAsync()` that transitions
the circuit from `Open` to `Closed` but enters a cold-start period
where the `FailureRatio` threshold is temporarily raised (e.g., doubled)
for the first `SamplingDuration` window to avoid immediately re-opening
from residual errors. Requires changes to
`CircuitBreakerManualControl.cs` for the new method,
`CircuitStateController.cs` for the cold-start state tracking,
`AdvancedCircuitBehavior.cs` for threshold adjustment during cold-start,
and `CircuitBreakerStrategyOptions.cs` for a configurable cold-start
ratio multiplier.

### M3: Add ResiliencePipeline execution result metadata

Implement `ResilienceExecutionResult<T>` that wraps the outcome with
execution metadata: total duration, attempt count, strategies invoked,
circuit state consulted, and whether a fallback was used. Add
`ResiliencePipeline.ExecuteWithResult<T>()`. Changes span
`ResiliencePipeline.cs`, `ResiliencePipeline.TResult.cs`, new result
metadata classes, and each strategy for metadata Population.

### M4: Implement dynamic strategy reconfiguration at runtime

Add `ResiliencePipelineRegistry.Reconfigure<T>()` that updates a
named pipeline's strategy options without rebuilding the pipeline.
Strategy instances must support hot-swapping their options via an
`IReconfigurable<TOptions>` interface. Changes span `Registry/` for
the reconfiguration API, `ResilienceStrategyOptions.cs` for the
interface, and each strategy class (`Retry`, `CircuitBreaker`,
`Timeout`) for reconfiguration support.

### M5: Add hedging action result selection strategy

Extend `HedgingStrategyOptions` with a `ResultSelector` delegate that
chooses the best result from completed hedging actions instead of
always taking the first successful one. Support scoring functions and
quorum-based selection. Changes span `HedgingStrategyOptions.cs`,
`HedgingResilienceStrategy.cs` for parallel result collection,
`HedgingHandler.cs` for selector invocation, and
`HedgingActionGeneratorArguments.cs` for context.

### M6: Implement outcome event notification system for pipeline observability

Add `ResiliencePipelineBuilder.OnOutcome(Action<OutcomeEventArgs>)`
that registers a callback invoked after each strategy in the pipeline
produces an outcome, carrying the strategy name, outcome
(result or exception), duration, and attempt metadata. Support
multiple subscribers with ordering guarantees. Changes span
`ResiliencePipelineBuilder.cs` for the registration API, new
`OutcomeEventArgs.cs` and `OutcomeEventNotifier.cs` classes,
`ResilienceStrategy.cs` for notifier injection, each strategy class
(`Retry`, `Timeout`, `Fallback`) for emitting events, and
`Telemetry/TelemetryUtil.cs` for bridging with existing telemetry.

### M7: Add per-strategy timeout support to ResiliencePipeline

Implement a pipeline-level overall timeout that wraps all strategies,
independent of the per-operation `TimeoutResilienceStrategy`. Add
`ResiliencePipelineBuilder.WithPipelineTimeout(TimeSpan)`. Changes span
`ResiliencePipelineBuilder.cs` for the configuration,
`ResiliencePipeline.cs` for the wrapping logic,
`ResilienceContext.cs` for remaining-time tracking, and
`CancellationTokenSourcePool.cs` for linked-source management.

### M8: Implement outcome predicate composition for strategies

Add fluent predicate builders:
`RetryStrategyOptions.HandleResult<T>(Func<T, bool>)`,
`.Handle<TException>()`, `.OrResult()`, `.OrHandle()` that compose
into a single `ShouldHandle` predicate. Replace the current raw
delegate assignment. Changes span `ResilienceStrategyOptions.cs` for
the builder base, each strategy's options class for the typed
overloads, and each strategy's core class for predicate evaluation.

### M9: Add circuit breaker state change notifications via IObservable

Implement `CircuitBreakerManualControl.StateChanges` as an
`IObservable<CircuitState>` that emits on every state transition.
Support `IAsyncObserver` for async subscribers. Changes span
`CircuitBreakerManualControl.cs`, `CircuitStateController.cs` for
event firing, `CircuitBreakerResilienceStrategy.cs` for wiring, and
new observable infrastructure in `Utils/`.

### M10: Implement strategy execution weight for load balancing

Add `ResilienceStrategyOptions.Weight` property used by the pipeline
to distribute load across multiple instances of the same strategy
type. Support weighted round-robin and least-connections strategies.
Changes span `ResilienceStrategyOptions.cs` for the weight property,
`ResiliencePipelineBuilder.cs` for multi-instance registration,
`ResiliencePipeline.cs` for the selection algorithm, and telemetry
for weight-based metrics.

### M11: Update CI build workflow and DocFX documentation configuration

The `.github/workflows/build.yml` matrix runs on `windows-latest`,
`macos-latest`, and `ubuntu-latest` but does not include a
documentation build step; the separate `gh-pages.yml` builds docs only
on push to `main` or `release/*`. Add a `docs` job to
`.github/workflows/build.yml` that runs `docfx build docs/docfx.json`
to validate the generated API docs on every PR. Also update
`docs/toc.yml` to add "Extensions" and "Testing" navigation entries
pointing to the existing `src/Polly.Extensions` and `src/Polly.Testing`
projects, add `docs/extensibility/custom-strategies.md` with a guide
on creating custom resilience strategies (the `docs/extensibility/`
directory exists but lacks this page), and verify that
`docs/strategies/toc.yml` references the strategies index correctly.

## Wide

### W1: Implement distributed circuit breaker with shared state

Extend `CircuitBreakerResilienceStrategy` to support distributed state
via a pluggable `ICircuitBreakerStateStore` interface with
implementations for Redis and SQL Server. Shared state must handle
concurrent transitions across processes, lease-based half-open probes,
and eventual consistency. Changes span `CircuitBreaker/` for the state
store abstraction, `CircuitStateController.cs` for distributed locking,
`Registry/` for distributed pipeline registration, `Telemetry/` for
cross-process event correlation, and new persistence infrastructure.

### W2: Add OpenTelemetry-native instrumentation throughout the pipeline

Replace `DiagnosticSource`-based telemetry with native OpenTelemetry
`Activity` and `Meter` instrumentation. Emit spans for each strategy
execution with attributes (attempt number, circuit state, delay,
outcome), counters for retries/circuit-breaks/timeouts/rate-limits,
and histograms for execution duration. Changes span every strategy
class, `Telemetry/` for the OTel integration, `ResiliencePipeline.cs`
for span lifecycle, `ResilienceContext.cs` for baggage propagation,
and `Registry/` for meter management.

### W3: Implement adaptive concurrency limiter strategy

Add `AdaptiveConcurrencyStrategy` that dynamically adjusts concurrency
limits based on latency percentiles (gradient algorithm inspired by
Netflix's adaptive concurrency library). Track in-flight requests,
p99 latency, and adjust limits using AIMD. Changes span a new
`AdaptiveConcurrency/` directory, integration with
`ResiliencePipelineBuilder.cs`, `ResilienceContext.cs` for timing,
`Telemetry/` for limit-change events, `RateLimiter/` for shared
rate-limiting infrastructure, and `Utils/` for percentile tracking.

### W4: Add resilience pipeline composition with dependency graph

Implement `ResiliencePipelineBuilder.AddDependency(pipeline)` that
models inter-pipeline dependencies (e.g., service A depends on service
B). Propagate circuit-breaker state across dependent pipelines — if
a downstream circuit opens, upstream pipelines preemptively shed load.
Changes span `ResiliencePipelineBuilder.cs`, `ResiliencePipeline.cs`
for dependency tracking, `CircuitBreaker/` for state propagation,
`Registry/` for dependency graph management, `Telemetry/` for
cascade visualization, and `Hedging/` for dependency-aware action
generation.

### W5: Implement policy-as-code with JSON/YAML configuration

Add `ResiliencePipelineBuilder.FromConfiguration(IConfiguration)` that
builds pipelines from structured configuration (JSON, YAML, or
`IConfiguration`). Support all strategy types, custom predicate
expressions, and hot-reload via `IOptionsMonitor`. Changes span a new
`Configuration/` directory, `ResiliencePipelineBuilder.cs` for the
configuration binding, each strategy's options class for serialization
attributes, `Registry/` for configuration-based pipeline registration,
and validation logic for configuration schema. Also update
`CHANGELOG.md` with a feature entry documenting the new configuration
support, add a `docs/advanced/configuration.md` guide with JSON and
YAML examples, update `package-readme.md` to highlight the
configuration-driven pipeline feature, update `mdsnippets.json` to
include the `Configuration/` directory in snippet extraction, and add
a `polly-configuration` group to `.github/dependabot.yml` for tracking
configuration library dependency updates.

### W6: Add request hedging with circuit-breaker-aware endpoint selection

Extend the hedging strategy to receive a list of endpoints and select
which ones to hedge against based on per-endpoint circuit breaker
state. Endpoints with open circuits are deprioritized. Add
`HedgingStrategyOptions.EndpointSelector` and per-endpoint health
tracking. Changes span `Hedging/` for the endpoint model and selector,
`CircuitBreaker/` for per-endpoint state, `Registry/` for endpoint
registration, `Telemetry/` for per-endpoint metrics, and
`ResiliencePipeline.cs` for endpoint-aware execution.

### W7: Implement resilience testing framework

Add `Polly.Testing` utilities: `ResiliencePipelineAssert` for verifying
pipeline composition (assert retry count, circuit threshold, timeout
value), `SimulatedClock` for deterministic time control,
`FaultInjectionContext` for scripted failure sequences, and
`TelemetryCollector` for asserting on emitted events. Changes span a
new `Testing/` directory, integration with `Telemetry/` for event
capture, `Utils/TimeProvider.cs` for clock injection, each strategy
for inspectable state, and `ResiliencePipeline.cs` for composition
introspection.

### W8: Add service mesh integration layer

Implement `Polly.Mesh` that bridges Polly resilience pipelines with
service mesh policies (Istio, Linkerd): sync circuit breaker state
with mesh-level outlier detection, respect mesh-injected retry budgets,
and propagate resilience headers (`x-retry-attempt`, `x-circuit-state`).
Changes span a new `Mesh/` integration layer, `ResilienceContext.cs`
for header propagation, `CircuitBreaker/` for external state sync,
`Retry/` for budget awareness, `Hedging/` for mesh endpoint discovery,
and `Telemetry/` for mesh-correlated spans.

### W9: Implement multi-tier resilience pipeline (inner/outer/cross-cutting)

Add `ResiliencePipelineBuilder.WrapWith()` composition that explicitly
models inner (per-call), outer (per-service), and cross-cutting
(global) resilience tiers. Each tier has its own telemetry scope and
can override or augment strategies from other tiers. Support
tier-aware circuit breaker aggregation. Changes span
`ResiliencePipelineBuilder.cs` for tier modeling,
`ResiliencePipeline.cs` for nested execution, every strategy for
tier-aware context, `Telemetry/` for per-tier metrics, and
`Registry/` for tiered pipeline management.

### W10: Add persistent execution log with replay capability

Implement `ResiliencePipeline.ExecuteWithLog<T>()` that persists every
execution attempt, outcome, delay, and strategy decision to a durable
log. Add `ExecutionLog.Replay()` that re-executes a logged sequence
for debugging. Support pluggable storage (file, database, event hub).
Changes span new `ExecutionLog/` infrastructure, integration with
`ResiliencePipeline.cs` and each strategy for event recording,
`ResilienceContext.cs` for log correlation, `Telemetry/` for log-based
metrics reconstruction, and `Registry/` for log configuration.

### W11: Overhaul project documentation and release management infrastructure

The repository has extensive documentation across `docs/`, `README.md`,
`CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, and
`package-readme.md`, but they reference different versions and have
inconsistent formatting. Consolidate release documentation by updating
`CHANGELOG.md` to follow Keep a Changelog format with `[Unreleased]`
and version-tagged sections (e.g., `## [8.6.6]` instead of
`## 8.6.6`), update `package-readme.md` to auto-include code snippets
via `mdsnippets.json` configuration, and add a
`docs/community/release-process.md` guide documenting the release
workflow. Update `.github/workflows/release.yml` to automatically
extract the latest `CHANGELOG.md` section as GitHub Release notes
(currently the workflow uses `generate_release_notes: true` which
auto-generates from PRs rather than from the curated changelog). Also
update `Polly.slnx` to include `docs/` as a solution folder for IDE
navigation (the `eng/` folder is already included but `docs/` is
absent).
