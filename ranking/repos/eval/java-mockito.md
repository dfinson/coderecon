# mockito/mockito

| Field | Value |
|-------|-------|
| **URL** | https://github.com/mockito/mockito |
| **License** | MIT |
| **Language** | Java |
| **Scale** | Large |
| **Category** | Mocking framework |
| **Set** | eval |
| **Commit** | `a231205b240e7884a63bf0f63440012867a4da21` |

## Why this repo

- **Deep internals**: Bytecode generation (ByteBuddy), invocation interception, argument matching, stubbing, verification
- **Multi-layer architecture**: Public API → internal handler → invocation → stubbing → creation (proxy generation)
- **Complex state machine**: Mock lifecycle spanning creation, stubbing, invocation recording, verification, and cleanup

## Structure overview

```
mockito-core/src/main/java/org/mockito/
├── Mockito.java              # Main entry point: mock(), when(), verify(), spy()
├── BDDMockito.java           # BDD-style given/then API
├── ArgumentMatchers.java     # Built-in matchers: any(), eq(), argThat()
├── ArgumentCaptor.java       # Capture arguments for assertion
├── MockedStatic.java         # Static method mocking (scoped)
├── MockedConstruction.java   # Constructor mocking (scoped)
├── InOrder.java              # Ordered verification interface
├── MockSettings.java         # Mock configuration: name, default answer, serializable
├── internal/
│   ├── handler/              # MockHandlerImpl: invocation dispatch, answer resolution
│   ├── stubbing/             # InvocationContainerImpl, StubbedInvocationMatcher, answers/
│   ├── verification/         # Times, AtLeast, AtMost, InOrderWrapper, checkers/
│   ├── invocation/           # InterceptedInvocation, InvocationMatcher, InvocationsFinder
│   ├── creation/             # ByteBuddy mock generation, proxy, settings
│   ├── matchers/             # MatcherApplicationStrategy, argument matching internals
│   ├── configuration/        # Injection (field/constructor/property), plugins
│   ├── framework/            # DefaultMockitoFramework, lifecycle
│   ├── session/              # MockitoSession implementation
│   ├── debugging/            # Invocation logging, warning generation
│   ├── exceptions/           # Stack trace cleaning, error formatting
│   ├── listeners/            # Invocation listeners, verification listeners
│   ├── junit/                # JUnit 4/5 rule/extension integration
│   ├── progress/             # Verification progress tracking
│   └── util/                 # Reflection, collections, IO, concurrent helpers
├── stubbing/                 # Answer, OngoingStubbing interfaces
├── verification/             # VerificationMode, VerificationAfterDelay
├── listeners/                # InvocationListener, VerificationStartedListener
├── plugins/                  # MockMaker, StackTraceCleanerProvider SPI
├── quality/                  # Strictness enum
└── session/                  # MockitoSession interface
```

## Scale indicators

- ~481 Java source files in mockito-core/src/main
- ~44K lines of code
- ByteBuddy bytecode generation for mock proxies
- SPI plugin system for mock makers and stack trace cleaners

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `InOrder` verification not detecting extra invocations between verified calls

When using `inOrder.verify(mock).methodA()` followed by `inOrder.verify(mock).methodB()`, extra invocations of unrelated methods between A and B are silently ignored. `InOrderWrapper` only checks the relative ordering of verified calls but does not report unverified interleaved invocations when using strict mode.

### N2: Add `ArgumentCaptor.reset()` to clear accumulated captured values

`ArgumentCaptor` provides no way to reset captured state between reuses in the same test. When the same `ArgumentCaptor` instance is passed to multiple `verify()` calls, `getAllValues()` accumulates values from every matching invocation across all verifications. Neither `ArgumentCaptor` nor the underlying `CapturingMatcher` exposes a `reset()` or `clear()` method, forcing developers to create a new captor instance for each independent verification. Add `ArgumentCaptor.reset()` delegating to a new `CapturingMatcher.clear()` method so captors can be reused without recreation.

### N3: Fix `description()` verification mode not working with `inOrder.verify()`

`Description` (returned by `times(1).description("msg")` or `Mockito.description("msg")`) implements `VerificationMode` but not `VerificationInOrderMode`. When `inOrder.verify(mock, times(1).description("msg"))` is called, `InOrderImpl.verify()` checks `mode instanceof VerificationInOrderMode` and throws `MockitoException: "Description is not implemented to work with InOrder"`. Fix `Description` to also implement `VerificationInOrderMode` by delegating `verifyInOrder()` to its wrapped mode's `verifyInOrder()` and catching `AssertionError` to prepend the custom description message.

### N4: Fix `ScopedMockImpl.close()` marking mock as closed before disabling interceptor

`ScopedMockImpl.close()` sets `closed = true` before calling `control.disable()`. If `control.disable()` throws a `MockitoException` (e.g., the interceptor was already removed due to a prior failure or concurrent access), the mock is permanently marked as closed even though its class interceptor may still be registered. Any subsequent attempt to re-register the same class as a static or construction mock then fails with "static mocking is already registered in the current thread", with no apparent cause. Fix by calling `control.disable()` first, then setting `closed = true` only after `disable()` succeeds.

### N5: Fix `doNothing().when(spy).method()` not working for final methods on spies

When creating a spy of a class with a final method, `doNothing().when(spy).finalMethod()` still calls the real method because ByteBuddy's subclass mock maker cannot override final methods. The error message should explicitly state that final methods cannot be stubbed on spies created via subclass.

### N6: Add `Mockito.clearAllMocks()` to reset all mocks in the current thread

`Mockito.reset(mock)` requires a reference to each mock. Add a `clearAllMocks()` method that resets all mocks created in the current thread/session: clear stubbings, invocation records, and verification state. Track mocks via `MockitoFramework`.

### N7: Fix `Reporter.formatUnncessaryStubbingException()` not providing "did you mean?" hints for argument mismatches

When `STRICT_STUBS` reports unused stubbings via `UnnecessaryStubbingException`, the message lists each unused stubbing with its location (from `u.getLocation()`). However, it does not show whether there were actual invocations of the same method with different arguments — information that would help identify mismatched stubs. The `potentialStubbingProblem()` reporter already performs this cross-check. Extend `Reporter.formatUnncessaryStubbingException()` to scan the mock's recorded invocations for calls to the same method name with different arguments and append them as "actual invocations that may have been intended" in the exception message.

### N8: Fix `ArgumentMatchers.argThat` causing `NullPointerException` for primitive parameters

`argThat(matcher)` unconditionally returns `null`. When the method parameter is a primitive type (e.g., `int`), the `null` is unboxed and throws `NullPointerException` before the test assertion runs. Other matchers such as `any(Class)` already avoid this by returning `Primitives.defaultValue(type)`. Fix `ArgumentMatchers.argThat(ArgumentMatcher<T>)` to return `Primitives.defaultValue()` based on the matcher's type instead of returning `null`, mirroring the approach used by `anyInt()`, `any(Class)`, and similar matchers in `ArgumentMatchers`.

### N9: Add `Answers.RETURNS_EMPTY` explicit enum constant for empty-value default answer

There is no `Answers` enum constant that explicitly selects `ReturnsEmptyValues` behavior. `RETURNS_DEFAULTS` uses `GloballyConfiguredAnswer`, which delegates to whatever is globally configured (default: `ReturnsEmptyValues`). When the global default is changed, any mock using `RETURNS_DEFAULTS` silently changes behavior. There is no way to use `@Mock(answer = ...)` or `withSettings().defaultAnswer(...)` to explicitly request the empty-values behavior (returning empty collections, `Optional.empty()`, `Stream.empty()`, `0`/`false` for primitives, etc.) without depending on global configuration. Add `RETURNS_EMPTY` to the `Answers` enum wrapping a `ReturnsEmptyValues` instance.

### N10: Fix `verifyNoInteractions` ignoring `Mockito.ignoreStubs()` marks

`NoInteractions.verify()` calls `data.getAllInvocations().isEmpty()` to check for any invocations. It does not filter out invocations marked via `Mockito.ignoreStubs(mock)`. When `ignoreStubs(mock)` marks stub-matched invocations as `isIgnoredForVerification`, `verifyNoMoreInteractions(mock)` correctly passes (because `NoMoreInteractions` uses `findFirstUnverified()` which treats `isIgnoredForVerification` as verified). However, `verifyNoInteractions(mock)` still fails for those same invocations, making `ignoreStubs()` ineffective with `verifyNoInteractions`. Fix `NoInteractions.verify()` to filter out invocations where `isIgnoredForVerification()` returns `true`, consistent with how `NoMoreInteractions` handles them.

### N11: Fix `doc/release-notes/official.md` not documenting deprecated API removals across major versions

The `doc/release-notes/official.md` file lists new features per release but omits entries for APIs removed after deprecation (e.g., removed `Matchers` class, legacy `MockitoJUnitRunner`). Add a "Removed" section per major version in `doc/release-notes/official.md`, cross-reference the original deprecation notices, and update `.github/ISSUE_TEMPLATE.md` with a migration assistance request template for users affected by removals.

## Medium

### M1: Add `@Captor` support for JUnit 5 test method parameters

`@Captor` annotation is only processed on fields by `MockitoAnnotations.openMocks()`. JUnit 5 tests using `@ExtendWith(MockitoExtension.class)` cannot inject `ArgumentCaptor` instances as test method parameters. Add `ArgumentCaptor` parameter resolution to `MockitoExtension` via a `CapturingParameterResolver` that creates typed captors from each parameter's generic type using `ArgumentCaptor.forClass()`. Changes span `MockitoExtension`, `ArgumentCaptor`, and the JUnit 5 parameter resolution integration in `org.mockito.junit.jupiter`.

### M2: Add `verify(mock, timeout(n).thenStayStable(d))` combined timeout+stability verification

`verify(mock, timeout(500))` succeeds as soon as the expected count is reached, and `after(500)` waits the full duration to ensure the count is not exceeded. There is no single mode that: (1) polls until the expected count is reached within a timeout, and (2) then waits an additional stabilization period to confirm no further calls happen. Add `VerificationWithTimeout.thenStayStable(long stabilizationMillis)` that delegates to a new `StableVerificationOverTime` wrapping `VerificationOverTimeImpl`. Changes span `VerificationOverTimeImpl`, `VerificationModeFactory`, `VerificationWithTimeout`, and the verification checker pipeline.

### M3: Implement deep stubs for generic return types

`RETURNS_DEEP_STUBS` fails when the stubbed method returns a generic type like `List<Foo>` because the generic type information is erased at runtime. Use the method's generic signature to resolve the actual type parameter and create an appropriately typed deep stub. Changes span `ReturnsDeepStubs`, `GenericMetadataSupport`, and mock creation settings.

### M4: Add `BDDMockito.willCapture(Class<T>)` for fluent BDD-style argument capture

`BDDMockito.given()` has no built-in way to create captors inline during stub setup. Users must declare `ArgumentCaptor` fields separately and then use them in `given(mock.method(captor.capture()))`. Add `BDDMockito.willCapture(Class<T> clazz)` that returns a `BDDArgumentCaptor<T>` combining `ArgumentCaptor<T>` with a `BDDOngoingStubbing`-compatible matcher, enabling inline capture in `given()` and `then().should()` chains without pre-declaring a captor. Changes span `BDDMockito`, a new `BDDArgumentCaptor` class, `BDDOngoingStubbing`, and the `ArgumentCaptor`/`CapturingMatcher` integration.

### M5: Implement mock serialization with Kryo support

Mocks configured with `withSettings().serializable()` use Java's built-in serialization, which is slow and fails for classes without no-arg constructors. Add Kryo serialization support via `withSettings().serializable(SerializableMode.KRYO)`. Changes span `MockSettings`, mock creation in ByteBuddy, and serialization handling in `InvocationContainerImpl`. Add the Kryo dependency to `build.gradle.kts` and update `README.md` with serialization mode documentation.

### M6: Add invocation listener with filtering support

`MockSettings.invocationListeners()` notifies for every invocation. Add filtering so listeners can specify which methods they care about via method name patterns or annotations. Changes span `InvocationNotifierHandler`, `MockSettings`, `InvocationListener` interface, and the listener notification pipeline.

### M7: Implement ordered verification across multiple mocks with gap detection

Enhance `InOrder` to support a `strict()` mode where any invocation on any of the in-order mocks that was not explicitly verified causes a failure. Currently, `InOrder` only checks relative ordering. Changes span `InOrderImpl`, `InOrderWrapper`, `InOrderContextImpl`, and verification checkers.

### M8: Fix `ConstructorInjection.SimpleArgumentResolver` ignoring constructor parameter names when resolving duplicate-type mocks

`@InjectMocks` already performs constructor injection via `ConstructorInjection`, but `SimpleArgumentResolver.resolveTypeInstances()` matches mocks to constructor parameters by type only. When a constructor has two parameters of the same type (e.g., `public Service(String firstName, String lastName)`), the resolver returns the first mock of that type for every parameter of that type, potentially injecting the wrong mock. Fix by using `Constructor.getParameters()` and `Parameter.getName()` to match parameters by both type and name, and update `PropertyAndSetterInjection` to use the same name-based disambiguation. Changes span `ConstructorInjection.SimpleArgumentResolver`, `PropertyAndSetterInjection`, and the `MockInjectionStrategy` base class.

### M9: Implement stubbing inheritance for spy hierarchies

When a spy of a subclass is created, stubs declared on a parent class spy are not inherited. Implement stubbing inheritance so that `when(parentSpy.method()).thenReturn(x)` is visible when verifying `childSpy.method()`. Changes span `InvocationContainerImpl`, `MockHandlerImpl`, and mock creation settings.

### M10: Add `MockedConstruction.contextFor(T mock)` to retrieve construction context after initialization

`MockedConstruction.constructed()` returns all mocked instances in creation order, and the `MockInitializer` receives each mock with its `Context` at construction time. However, once the initializer returns there is no way to look up the `Context` (containing `constructor()` and `arguments()`) for a specific constructed mock. Add `MockedConstruction.contextFor(T mock)` that returns the `Context` associated with a given mock instance. Changes span the `MockedConstruction` interface, `MockedConstructionImpl`, the `InlineConstructionMockControl` in `creation/bytebuddy/`, and the construction callback mechanism.

### M11: Improve `build.gradle.kts` for strict cross-compilation and reproducible builds

The `build.gradle.kts` does not configure Java toolchains or explicit release targets, meaning the build inherits the JDK version used to run Gradle, potentially creating class files incompatible with the declared minimum Java version. Configure `java.toolchain.languageVersion`, add `options.release.set(11)` to `JavaCompile` tasks for strict cross-compilation, set `isReproducibleFileOrder = true` and `isPreserveFileTimestamps = false` on archive tasks for reproducible builds, and add `org.gradle.configuration-cache=true` and `org.gradle.parallel=true` to `gradle.properties`. Update `.github/CONTRIBUTING.md` with explicit JDK setup instructions and add Gradle toolchain documentation.

## Wide

### W1: Implement `InstrumentationMockMaker` using Java Instrumentation API

Add a `MockMaker` plugin that uses `java.lang.instrument.Instrumentation.redefineClasses()` to transform target class bytecode at the JVM level, enabling mocking of final classes, static methods, and constructors without ByteBuddy's inline advice approach. Unlike the existing `ProxyMockMaker` (interfaces only, in `creation/proxy/`) and `InlineByteBuddyMockMaker` (ByteBuddy-based bytecode generation), `InstrumentationMockMaker` would operate via the standard Java Instrumentation API and a Java agent, enabling mocking in environments where ByteBuddy is unavailable. Changes span a new `creation/instrumentation/` package, `MockMaker` SPI registration, `MockHandlerImpl` dispatch updates, `InvocationContainerImpl`, and `MockMakers` constants.

### W2: Add comprehensive static analysis for common Mockito mistakes

Implement a compile-time annotation processor that detects common mistakes: stubbing without verification, verifying unstubbed methods, mismatched argument matchers, leaked `MockedStatic` instances. Changes span a new `processor/` module, annotation definitions, exception message improvements, and integration with the JUnit extension.

### W3: Implement mock recording and playback for integration tests

Add the ability to record all mock interactions to a file and replay them in subsequent test runs. Support JSON serialization of invocations and responses. Changes span `InvocationContainerImpl` (recording), `MockHandlerImpl` (playback mode), a new `recording/` module, session management, and serialization of argument matchers.

### W4: Add Kotlin coroutine support for suspend function mocking

Implement support for mocking Kotlin suspend functions. Handle `Continuation` parameters transparently, support `coEvery`/`coVerify` style API, and integrate with Kotlin coroutine test dispatchers. Changes span `MockHandlerImpl` (continuation handling), `InvocationMatcher` (argument filtering), `StubbedInvocationMatcher`, ByteBuddy generation, and a new `kotlin/` module.

### W5: Implement thread-safe mock framework for parallel test execution

Add thread-isolated stubbing and verification state so mocks can be safely shared across parallel test threads. Implement per-thread invocation recording, thread-safe `InvocationContainerImpl`, and parallel-safe `MockitoSession`. Changes span `InvocationContainerImpl`, `MockHandlerImpl`, `MockitoSession`, `InOrderImpl`, verification checkers, and stubbing state.

### W6: Add fluent assertion API for mock interactions

Implement AssertJ-style fluent assertions for mock verification: `assertThat(mock).wasCalledWith("arg").exactly(3).times().inOrder()`. Support chained assertions, soft assertions, and detailed diff output for argument mismatches. Changes span a new `assertions/` module, verification modes, argument matching, exception formatting, and integration with the stubbing API.

### W7: Implement configurable strictness profiles with custom rules

Add named strictness profiles beyond `LENIENT`/`WARN`/`STRICT_STUBS`. Allow custom rules: max mock count per test, required verification for all stubbings, forbidden answer types. Changes span `Strictness`, `MockitoSession`, `StrictnessSelector`, `UnusedStubbingReporting`, `MockitoFramework`, and configuration/plugins.

### W8: Add mock dependency graph visualization

Implement a dependency graph builder that tracks which mocks interact with which other mocks during test execution. Generate DOT/Mermaid diagrams showing invocation flow. Changes span `InvocationNotifierHandler` (tracking), `MockHandlerImpl` (cross-mock detection), `MockitoFramework` (graph building), session reporting, and a new `visualization/` module.

### W9: Implement automatic mock configuration from interfaces

Add `@AutoMock` annotation that generates mock configurations from interface contracts: return type defaults, parameter constraints, exception specifications. Support interface hierarchies and generic interfaces. Changes span a new annotation type, annotation processing, `MockSettings` generation, `Answers` customization, `MockHandlerImpl`, and ByteBuddy creation.

### W10: Add migration tool from EasyMock/PowerMock to Mockito

Implement a source-code transformation tool that migrates EasyMock `expect()`/`replay()` and PowerMock `@PrepareForTest` patterns to Mockito equivalents. Changes span a new `migration/` module, AST parsing utilities, pattern matching for EasyMock/PowerMock APIs, code generation for Mockito equivalents, and CLI tooling.

### W11: Create comprehensive contributor documentation and design doc system

Establish a structured project documentation system: expand `doc/design-docs/` with architecture decision records (ADRs) covering the mock creation pipeline, stubbing resolution, and ByteBuddy integration strategy; improve `.github/CONTRIBUTING.md` with explicit build setup, test execution, and debugging instructions; replace the single `.github/ISSUE_TEMPLATE.md` with a `.github/ISSUE_TEMPLATE/` directory containing separate templates for `bug_report.md`, `feature_request.md`, and `design_proposal.md`; expand `SECURITY.md` with a full responsible disclosure process beyond the current single-line Tidelift reference; update `README.md` with an architecture overview section and links to design docs; and update `.github/PULL_REQUEST_TEMPLATE.md` with a documentation checklist. Changes span `doc/design-docs/`, `.github/CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/`, `SECURITY.md`, and `README.md`.
