# ReactiveX/RxSwift

| Field | Value |
|-------|-------|
| **URL** | https://github.com/ReactiveX/RxSwift |
| **License** | MIT |
| **Language** | Swift |
| **Scale** | Large |
| **Category** | Reactive extensions for Swift |
| **Set** | eval |
| **Commit** | `132aea4f236ccadc51590b38af0357a331d51fa2` |

## Why this repo

- **Multi-subsystem**: Core observables, operators, subjects, schedulers, disposables, traits (Single/Maybe/Completable/Infallible), RxCocoa UI bindings, RxBlocking, RxRelay, RxTest
- **Well-structured**: Clear separation between RxSwift/, RxCocoa/, RxBlocking/, RxRelay/, RxTest/, Platform/
- **Rich history**: 24K+ stars, mature reactive programming library with 70+ operators

## Structure overview

```
RxSwift/
├── Observables/         # 70+ operators: Map, Filter, Merge, CombineLatest, Throttle, etc.
├── Subjects/            # PublishSubject, BehaviorSubject, ReplaySubject, AsyncSubject
├── Schedulers/          # MainScheduler, SerialDispatchQueue, ConcurrentDispatchQueue, etc.
├── Disposables/         # DisposeBag, CompositeDisposable, SerialDisposable, etc.
├── Concurrency/         # AsyncLock, Lock, SynchronizedOnType
├── Traits/              # PrimitiveSequence (Single, Maybe, Completable), Infallible
├── Extensions/          # Bag+Rx
RxCocoa/
├── Common/              # DelegateProxy, Binder, Observable+Bind
├── Foundation/          # URLSession+Rx, NotificationCenter+Rx, KVO
├── iOS/                 # UIKit reactive extensions
├── Traits/              # ControlEvent, ControlProperty, Driver, Signal
RxBlocking/              # Synchronous blocking operators
RxRelay/                 # PublishRelay, BehaviorRelay, ReplayRelay
RxTest/                  # TestScheduler, HotObservable, ColdObservable, TestableObserver
Platform/                # AtomicInt, RecursiveLock, data structures (Bag, Queue, PriorityQueue)
```

## Scale indicators

- ~1,000 Swift source files
- ~170K lines of code
- 70+ observable operators with scheduler-aware implementations
- Multiple subsystems: core, UI bindings, testing, blocking, relay

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Document `throttle` timer-reset semantics and add `Tips.md` section

The `ThrottleSink` in `Observables/Throttle.swift` resets its trailing-edge timer every time a new element arrives within the active throttle window (each new `next` event replaces the in-flight `cancellable: SerialDisposable` with a fresh timer). This behavior — where a rapid burst repeatedly pushes back the trailing emission — is undocumented in the operator's doc comment. Update the `throttle` function's documentation in `Observables/Throttle.swift` to explain the timer-reset semantics, and add a 'Throttling and Debouncing' section to `Documentation/Tips.md` with examples contrasting `throttle(latest: false)` (leading-edge only), `throttle(latest: true)` (leading + trailing with window reset), and `debounce` (trailing-edge with emission only after silence).

### N2: Add `bufferSize` and `currentElementCount` properties to `ReplaySubject`

The `ReplaySubject` in `Subjects/ReplaySubject.swift` does not expose the configured buffer size or current element count publicly. The `bufferSize` is stored privately in `ReplayMany` and inaccessible from the `ReplaySubject` base class reference returned by `create(bufferSize:)`. Add a `bufferSize: Int?` computed property to `ReplaySubject<Element>` (returning `nil` for the unbounded `ReplayAll` created by `createUnbounded()`) and a `currentElementCount: Int` property returning the number of elements currently held in the replay buffer, both accessed under the existing `RecursiveLock`.

### N3: Add `count` and `isEmpty` inspection properties to `DisposeBag`

The `DisposeBag` in `Disposables/DisposeBag.swift` exposes no public API to inspect how many disposables are currently tracked. This makes it impossible to verify in unit tests that subscriptions were correctly added and not yet disposed. Add a thread-safe `count: Int` computed property and an `isEmpty: Bool` computed property to `DisposeBag`, both protected by the existing `SpinLock` that guards `disposables` and `isDisposed`.

### N4: Add `buffer(timeSpan:count:scheduler:)` operator to `Infallible`

The `Infallible` trait in `Traits/Infallible/Infallible+Operators.swift` is missing a `buffer(timeSpan:count:scheduler:)` operator that collects elements into arrays and emits them on time boundaries or when the count threshold is reached. Unlike `Observable.buffer`, an `Infallible` version should return `Infallible<[Element]>` since collecting elements into an array cannot introduce errors. Implement the operator by bridging to `Observable.buffer` and wrapping the result in `Infallible`, following the pattern already used by `debounce` and `throttle` in the same file.

### N5: Add `advance(by:)` relative time-advancement method to `HistoricalScheduler`

The `HistoricalScheduler` in `Schedulers/HistoricalScheduler.swift` inherits `advanceTo(_ virtualTime: VirtualTime)` from `VirtualTimeScheduler` but provides no convenience method to advance the clock by a relative `TimeInterval` offset. Users who want to move time forward by, say, 5 seconds must manually compute `clock + 5` themselves. Add an `func advance(by interval: TimeInterval)` method to `HistoricalScheduler` that computes the new target time from the current `clock` and delegates to the inherited `advanceTo(_:)`, including guard logic matching `sleep(_:)` to prevent advancing into the past.

### N6: Add boundary-observable `window(boundary:)` variant to `Observables/Window.swift`

The `window` operator in `Observables/Window.swift` only provides the combined time-and-count boundary variant (`window(timeSpan:count:scheduler:)`). Add a `window(boundary:)` overload on `ObservableType` that accepts a secondary boundary observable and opens a new inner window on each boundary emission, completing the previous window. Implement a `WindowBoundarySink` following the structural pattern of the existing `WindowTimeCountSink` in the same file.

### N7: Add `Maybe.from(_ optional:)` factory for Swift `Optional` wrapping

The `Maybe` trait in `Traits/PrimitiveSequence/Maybe.swift` lacks a static `from(_ optional: Element?)` factory method that converts a Swift `Optional` to a `Maybe`: `.success(value)` for `.some(value)` and `.empty()` for `.none`. This forces users to write boilerplate `guard let` + `Maybe.just` / `Maybe.empty()` at every call site. Add `static func from(_ optional: Element?) -> Maybe<Element>` as an extension on `PrimitiveSequence` constrained to `Trait == MaybeTrait`, in `Traits/PrimitiveSequence/Maybe.swift`.

### N8: Add `sampleFirst` operator to `Observables/Sample.swift`

The `sample(_:)` operator in `Observables/Sample.swift` always emits the *latest* element produced since the last sampler tick. There is no variant for emitting the *first* element observed in each sampling window. Add a `sampleFirst(_ sampler:)` operator on `ObservableType` that emits the first new element since the most recent tick and discards subsequent elements until the next tick. Implement it via a new `SampleFirstSequenceSink` in `Observables/Sample.swift` with a `hasEmitted: Bool` flag that is set on the first `next` event and cleared when the sampler fires.

### N9: Unify thread-local storage mechanism in `CurrentThreadScheduler`

The `CurrentThreadScheduler` in `Schedulers/CurrentThreadScheduler.swift` uses two different thread-local storage mechanisms within the same class: raw `pthread_key_t`/`pthread_getspecific` for `isScheduleRequired`, but Foundation's `Thread.getThreadLocalStorageValueForKey` (via `Platform.Darwin.swift` / `Platform.Linux.swift`) for the scheduler `queue`. This creates an unnecessary cross-layer dependency and potential inconsistency on non-Foundation platforms. Add a second `pthread_key_t` for the scheduler queue alongside the existing `isScheduleRequiredKey` and rewrite the `queue` getter/setter to use `pthread_getspecific`/`pthread_setspecific` directly, removing the `getThreadLocalStorageValueForKey` call from `CurrentThreadScheduler`.

### N10: Add `materialize` operator for `Completable` trait

The `Completable` extensions in `Traits/PrimitiveSequence/Completable.swift` lack a `materialize()` operator that converts `.completed` and `.error` events into `Observable<CompletableEvent>`. Since `Completable` has `Element == Never`, calling `asObservable().materialize()` yields `Observable<Event<Never>>`, which is not the same as `Observable<CompletableEvent>`. Add a specific `func materialize() -> Observable<CompletableEvent>` extension on `PrimitiveSequence` where `Trait == CompletableTrait` that maps `.completed` to `CompletableEvent.completed` and `.error(_:)` to `CompletableEvent.error(_:)`.

### N11: Fix `Documentation/GettingStarted.md` code samples using deprecated Swift 2 syntax

The `Documentation/GettingStarted.md` file contains code examples that use obsolete Swift 2/Objective-C bridging APIs: `containsString(_:)` (replaced by `contains(_:)` in Swift 3), bare `just(...)` global functions (now `Observable.just(...)`), and `catchError { }` (renamed to `catch { }` in RxSwift 6). Update all code samples in `Documentation/GettingStarted.md` to use current RxSwift 6 and Swift 5.9+ syntax, and add a version-specific callout in `Documentation/SwiftConcurrency.md` noting that `async`/`await` bridging requires iOS 13+ / macOS 10.15+.

## Medium

### M1: Add `merge()`, `reduce()`, and `toArray()` operators to `Infallible`

The `Infallible` trait in `Traits/Infallible/` is missing three operators: a `merge()` instance method on `InfallibleType where Element: InfallibleType` (for flattening a nested `Infallible<Infallible<Element>>` into `Infallible<Element>`), `reduce(_:accumulator:)`, and `toArray()` returning `Infallible<[Element]>`. These force `Infallible` users to call `asObservable()` and manually wrap results. Implement the operators in a new `Infallible+Operators+Gathering.swift` file following the delegation-and-wrap pattern used throughout `Infallible+Operators.swift`, adapting from `Observable.merge`, `Observable.reduce`, and `Observable.toArray` respectively.

### M2: Add retry with exponential backoff operator

Implement a `retry(maxAttempts:delay:multiplier:scheduler:)` operator that retries a failed observable sequence with configurable exponential backoff. The operator should integrate with `RetryWhen` logic in `Observables/RetryWhen.swift` and schedulers for delay timing without blocking threads. Update `README.md` with retry operator documentation and add error handling guidance.

### M3: Implement `TestScheduler` cold observable auto-disposal tracking

Extend `RxTest/Schedulers/TestScheduler.swift` to track all subscriptions created by `createColdObservable` and provide an assertion method `assertAllDisposed(by:)` that verifies all subscriptions are disposed by a specific virtual time. This requires extending `TestableObservable` and `Subscription` tracking.

### M4: Add collection-based and multi-source `withLatestFrom` overloads to `Infallible`

The `Infallible` trait in `Traits/Infallible/Infallible+Operators.swift` only provides single-source `withLatestFrom` overloads. It lacks 2-, 3-, and 4-source arity overloads (with and without `resultSelector`) and a collection-based `withLatestFrom<Collection: Swift.Collection>(_:resultSelector:)` variant. Implement the arity overloads as static functions in a new `Infallible+WithLatestFrom+arity.swift` file and the collection overload in `Infallible+WithLatestFrom+Collection.swift`, following the patterns established by `Infallible+CombineLatest+arity.swift` and `Infallible+CombineLatest+Collection.swift`.

### M5: Implement `prefetch` operator for observable sequences

Add a `prefetch(count:)` operator that eagerly subscribes and buffers up to `count` elements before the downstream subscribes. This requires a new file in `Observables/` with a custom `Producer` subclass, buffer management with proper back-pressure, and scheduler-aware element delivery.

### M6: Add debug name propagation through operator chains

Extend the `debug()` operator in `Observables/Debug.swift` to support propagating debug identifiers through chained operators. Each operator in the chain should inherit and append its operator name to a debug path (e.g., `"source > map > filter > throttle"`), with the path accessible in `Rx.resources` for leak debugging.

### M7: Implement `RxBlocking` timeout with partial results

Extend `RxBlocking/BlockingObservable+Operators.swift` to add `toArray(timeout:partial:)` that returns elements collected before the timeout instead of throwing. This requires modifying `BlockingObservable`'s `RunLoopLock` integration to support partial result collection and adding new error types for timeout-with-partial-data.

### M8: Add `BehaviorRelay` snapshot and diff support

Extend `RxRelay/BehaviorRelay.swift` with `snapshot()` returning the current value and subscription count, and `changes()` returning an `Observable` that emits `(oldValue, newValue)` tuples. Requires adding value comparison infrastructure and thread-safe old value tracking within the relay.

### M9: Add cross-trait `flatMap` variants to `Maybe`

The `Maybe` trait in `Traits/PrimitiveSequence/Maybe.swift` only exposes `flatMap(_:) -> Maybe<Result>`, but `Single` already provides `flatMapMaybe`, `flatMapCompletable`, and `flatMap`. `Maybe` is missing `flatMapSingle<Result>(_ selector:) -> Single<Result>` (for selectors that guarantee a value) and `flatMapCompletable(_ selector:) -> Completable` (for side-effecting selectors). Add both methods to `Traits/PrimitiveSequence/Maybe.swift` mirroring the `Single` implementations, add `flatMapMaybe<Result>(_ selector:) -> Maybe<Result>` to `Completable` (complementing its existing `andThen` variants) in `Traits/PrimitiveSequence/Completable.swift`, and add corresponding test cases to `Sources/AllTestz/MaybeTest.swift`.

### M10: Add `groupBy` operator support for `Driver` trait

The `Driver` trait in `RxCocoa/Traits/` supports most operators but lacks `groupBy`. Implement `Driver.groupBy(keySelector:)` that returns `Driver<GroupedObservable<Key, Element>>`, ensuring all emissions happen on the main scheduler and errors are replaced with the `onErrorJustReturn` recovery mechanism.

### M11: Improve SwiftLint and formatting configuration

Update `.swiftlint.yml` with module-specific rules for RxSwift/, RxCocoa/, RxBlocking/, RxRelay/, and RxTest/ directories. Configure `.swiftformat` with per-directory formatting rules matching the existing code style. Update `.jazzy.yml` with complete module documentation generation settings and custom theme. Add a `Makefile` target for documentation generation and linting. Configure `mise.toml` with development tool versions. Update `CONTRIBUTING.md` with code style guidelines referencing the linting configuration. Changes span `.swiftlint.yml`, `.swiftformat`, `.jazzy.yml`, `Makefile`, `mise.toml`, `CONTRIBUTING.md`, and `README.md`.

## Wide

### W1: Implement structured concurrency bridge for async/await

Add comprehensive `async`/`await` bridging beyond the existing `Observable+Concurrency.swift`. Implement a typed `AsyncObservableSequence<Element>` struct conforming to `AsyncSequence` to replace the anonymous `AsyncThrowingStream` returned by the `values` property, providing a stable named public type. Add `Subject.send(from asyncSequence: some AsyncSequence)` on `SubjectType` that feeds an `AsyncSequence` into a subject via a managed `Task`, automatically cancelling or completing the task when the subject disposes. Add `DisposeBag.add(task:)` that stores a Swift `Task` and cancels it on bag deinit, completing lifecycle integration. Changes span `RxSwift/Concurrency/`, `RxSwift/Subjects/`, and `RxSwift/Disposables/`.

### W2: Implement reactive caching layer with expiration

Add a `CachedObservable` type that wraps a source observable with configurable TTL, max-size LRU caching, and cache invalidation. Support cache key extraction via `KeyPath`, shared cache across multiple subscribers, cache prewarming, and disk persistence. Changes span new files in RxSwift/Observables/, extensions to `ObservableType`, new data structures in Platform/, and integration with schedulers for TTL expiration.

### W3: Add comprehensive operator fusion optimization

Implement operator fusion that detects and optimizes common operator chain patterns: `map.map` → single `map`, `filter.filter` → single `filter`, `observeOn.observeOn` → last `observeOn`, and `share().share()` → single `share()`. Requires modifying `Producer` in `Observables/Producer.swift`, adding a fusion protocol to `Sink`, and updating `Map`, `Filter`, `ObserveOn`, and `ShareReplayScope` to participate in fusion.

### W4: Implement distributed tracing for operator chains

Add tracing infrastructure that tracks element flow through operator chains: timestamp at each operator, processing duration, back-pressure metrics, and subscription lifecycle events. Requires adding a `Tracer` protocol, tracing hooks in `Producer.subscribe`, `Sink.forwardOn`, and `Disposable.dispose`, a `TracingScheduler` wrapper, and a reporting API. Changes span Observables/, Schedulers/, Disposables/, and new tracing module.

### W5: Add reactive state management framework

Implement `RxStore<State, Action>` as a Redux-like state container built on RxSwift: a `store.dispatch(action:)` that feeds through a `reduce(state:action:)` function, middleware support via observable transforms, `select(keyPath:)` for derived state streams with `distinctUntilChanged`, and time-travel debugging via `ReplaySubject`. Changes span new module files, integration with Subjects, operators, and RxCocoa for UI binding.

### W6: Implement comprehensive memory leak detection

Add a `LeakDetector` that tracks all RxSwift resource allocations and detects common leak patterns: retained `DisposeBag` in closures, missing disposal of subscriptions, circular references through `withLatestFrom`, and `share()` keeping upstream alive after all subscribers disconnect. Requires instrumenting `DisposeBag`, `Producer.subscribe`, `Sink`, `SubjectType`, and adding leak reporting to the `Resources` tracking system.

### W7: Add RxTest assertion DSL for complex sequence verification

Implement a fluent assertion API for `TestScheduler`: `expect(observer).toEmit([.next(200, "a"), .completed(300)])`, `expect(observable).toSubscribe(at: 200).andDispose(at: 500)`, `expect(hotObservable).toHaveSubscribers(count: 2, at: 300)`. Requires extending `TestableObserver`, `TestableObservable`, `Subscription`, adding custom assertion failure reporting, and integration with XCTest.

### W8: Implement cross-platform scheduler abstraction

Replace platform-specific scheduler implementations with a unified `SchedulerProvider` protocol supporting custom event loops. Implement providers for GCD (`DispatchQueue`), `OperationQueue`, Linux `epoll`, and a configurable thread pool. Requires refactoring `SerialDispatchQueueScheduler`, `ConcurrentDispatchQueueScheduler`, `MainScheduler`, Platform/ abstractions, and adding a provider registry.

### W9: Add backpressure support to observable sequences

Implement a backpressure mechanism for `Observable` sequences: `Flowable<Element>` type with `request(count:)` demand signaling, backpressure strategies (buffer, drop, latest, error), and integration with existing operators. Requires new protocol `FlowableType`, modifications to `Producer`/`Sink` for demand tracking, backpressure-aware versions of `Merge`, `FlatMap`, `Zip`, `CombineLatest`, and bridging operators between `Observable` and `Flowable`.

### W10: Implement reactive networking layer in RxCocoa

Extend `RxCocoa/Foundation/URLSession+Rx.swift` into a full reactive networking module: request retry with backoff, response caching with observable invalidation, request deduplication for identical in-flight requests, progress tracking as `Observable<Progress>`, multipart upload support, and automatic JSON decoding with `Codable`. Changes span RxCocoa/Foundation/, new networking files, integration with schedulers for retry timing, and RxSwift operators for deduplication logic.

### W11: Overhaul documentation site and playground examples

Comprehensively restructure the project's documentation: update all files in `Documentation/` (GettingStarted.md, Traits.md, Schedulers.md, Subjects.md, UnitTests.md, SwiftConcurrency.md, etc.) with current API examples and Swift 5.9+ syntax; refresh `Rx.playground/` examples to work with the latest Xcode; rebuild `docs/` Jazzy-generated API documentation from `.jazzy.yml`; update `README.md` with a feature comparison table, architecture overview, and migration guide from Combine; add structured changelog to `CONTRIBUTING.md`; update `CODE_OF_CONDUCT.md` to latest Contributor Covenant; and configure `Version.xcconfig` with documentation version tracking. Changes span `Documentation/`, `Rx.playground/`, `docs/`, `.jazzy.yml`, `README.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `LICENSE.md`, and `Version.xcconfig`.
