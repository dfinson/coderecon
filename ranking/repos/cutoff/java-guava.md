# google/guava

| Field | Value |
|-------|-------|
| **URL** | https://github.com/google/guava |
| **License** | Apache-2.0 |
| **Language** | Java |
| **Scale** | Large |
| **Category** | Core utility library |
| **Set** | Cutoff |
| **Commit** | `2fe9bd0d4faf1179cf981ed7800563d190817417` |

## Why this repo

- **Well-structured**: Organized under `guava/src/com/google/common/`
  with clear per-domain packages — `collect/` (immutable collections,
  multimaps, tables), `base/` (preconditions, strings, predicates),
  `cache/` (loading caches), `io/` (byte/char streams), `hash/`
  (hashing, Bloom filters), `util/concurrent/` (listenable futures, rate
  limiters), `primitives/`, `math/`, `net/`, and `eventbus/`. Each
  package has a focused API surface with clear internal layering.
- **Rich history**: 50K+ stars, 10K+ commits. One of the most widely
  used Java libraries. Issues cover correctness, performance, and API
  design across dozens of utility domains.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
guava/src/com/google/common/
├── collect/               # ImmutableList, ImmutableMap, ImmutableSet, Multimap,
│                          #   Table, BiMap, Multiset, Iterables, Lists, Sets, Maps
├── base/                  # Preconditions, Optional, Strings, Joiner, Splitter,
│                          #   Predicate, Function, Supplier, MoreObjects
├── cache/                 # CacheBuilder, LoadingCache, CacheLoader, CacheStats,
│                          #   LocalCache (internal), RemovalNotification
├── io/                    # ByteStreams, CharStreams, Files, Resources,
│                          #   ByteSource, ByteSink, CharSource, CharSink
├── hash/                  # Hashing, HashFunction, Hasher, HashCode, BloomFilter,
│                          #   Funnel, Funnels
├── util/concurrent/       # ListenableFuture, Futures, MoreExecutors,
│                          #   RateLimiter, Service, ServiceManager
├── primitives/            # Ints, Longs, Doubles, Bytes, Shorts, Floats, Chars,
│                          #   UnsignedInteger, UnsignedLong
├── math/                  # IntMath, LongMath, DoubleMath, BigIntegerMath,
│                          #   Stats, Quantiles
├── net/                   # InternetDomainName, InetAddresses, HostAndPort,
│                          #   MediaType, UrlEscapers, HttpHeaders
├── eventbus/              # EventBus, AsyncEventBus, Subscribe, DeadEvent,
│                          #   SubscriberExceptionHandler
├── graph/                 # Graph, ValueGraph, Network, MutableGraph,
│                          #   GraphBuilder, Traverser
├── escape/                # Escaper, CharEscaperBuilder, HtmlEscapers,
│                          #   XmlEscapers
├── reflect/               # TypeToken, Invokable, ClassPath, TypeResolver
├── annotations/           # Beta, VisibleForTesting, GwtCompatible
└── html/                  # HtmlEscapers (moved from escape/)
```

## Scale indicators

- ~800 Java source files under guava/src/
- ~300K lines of code
- Deep package hierarchy (3–5 levels)
- Zero external dependencies (JDK only)

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix ImmutableList.copyOf(Iterator) missing null-check on the iterator itself

`ImmutableList.copyOf(Iterator<? extends E> elements)` does not call
`checkNotNull(elements)` before using the iterator. Passing a `null`
iterator throws `NullPointerException` inside the method at
`elements.hasNext()` rather than at the call site. The sibling overload
`copyOf(Iterable)` already calls `checkNotNull(elements)` as its first
statement. Fix `copyOf(Iterator)` in `ImmutableList.java` to add
`checkNotNull(elements)` at the start of the method for a clear,
caller-pointing error.

### N2: Fix Joiner.appendTo(Appendable, Iterator) missing null-check for the parts iterator

The base `Joiner.appendTo(A appendable, Iterator<?> parts)` method does
not call `checkNotNull(parts)` before iterating. Passing a `null`
iterator throws `NullPointerException` at `parts.hasNext()` deep inside
the method with a confusing stack trace. The inner class returned by
`skipNulls()` already overrides this method and does call
`checkNotNull(parts, "parts")`. Fix the base `Joiner.appendTo(A,
Iterator)` method in `Joiner.java` to add `checkNotNull(parts)` as its
first statement, consistent with the `skipNulls()` variant.

### N3: Fix CacheBuilder.build(CacheLoader) not validating null loader at the call site

`CacheBuilder.build(CacheLoader<? super K1, V1> loader)` does not call
`checkNotNull(loader)` before delegating to `LocalLoadingCache`. Passing
a `null` loader causes a `NullPointerException` to originate inside
`LocalCache`'s constructor, exposing internal implementation details in
the stack trace rather than pointing to the user's `build()` call. Fix
`CacheBuilder.build(CacheLoader)` in `CacheBuilder.java` to add
`checkNotNull(loader, "loader")` before constructing the cache, so the
error is reported at the call site.

### N4: Fix Splitter.on(String) missing null-check on the separator

`Splitter.on(String separator)` validates that the separator is not
empty via `checkArgument(!separator.isEmpty())` but does not first call
`checkNotNull(separator)`. Passing `null` throws `NullPointerException`
at `separator.isEmpty()` inside `checkArgument`, producing a confusing
stack trace that does not clearly identify a null argument. The
`on(CharMatcher)` sibling already calls `checkNotNull(separatorMatcher)`
as its first statement. Fix `Splitter.on(String)` in `Splitter.java`
to add `checkNotNull(separator)` before the empty-string check.

### N5: Fix BloomFilter.writeTo not validating the OutputStream is non-null

`BloomFilter.writeTo(OutputStream out)` does not call
`checkNotNull(out)` before wrapping `out` in a `DataOutputStream`. If
`null` is passed, a `NullPointerException` is thrown on the first
`dout.writeByte()` call inside the method rather than at the call site.
The sibling `readFrom(InputStream, Funnel)` already calls
`checkNotNull(in, "InputStream")` as its first statement. Fix
`BloomFilter.writeTo()` in `BloomFilter.java` to add
`checkNotNull(out)` before creating the `DataOutputStream`.

### N6: Fix Strings.padStart and Strings.padEnd not validating negative minLength

Both `Strings.padStart(String, int, char)` and `Strings.padEnd(String,
int, char)` silently return the original string when `minLength` is
negative, masking caller bugs. Neither method throws or documents this
edge case, while the related `Strings.repeat(String, int)` does throw
`IllegalArgumentException` for negative `count`. Fix both `padStart()`
and `padEnd()` in `Strings.java` to throw `IllegalArgumentException`
when `minLength < 0`, making them consistent with `Strings.repeat()`.

### N7: Fix InternetDomainName.isValid not handling null input gracefully

`InternetDomainName.isValid(String name)` is documented as a safe
guard method, but passing `null` causes it to throw
`NullPointerException` instead of returning `false`. The method calls
`from(name)` which calls `checkNotNull(domain)`, throwing NPE. The
try/catch in `isValid()` only catches `IllegalArgumentException`, so
the NPE escapes to the caller. Fix `isValid()` in
`InternetDomainName.java` to return `false` for a `null` argument,
making it safe to use as a guard without a prior null check.

### N8: Fix RateLimiter.create(double, long, TimeUnit) not validating null TimeUnit

`RateLimiter.create(double permitsPerSecond, long warmupPeriod, TimeUnit
unit)` does not validate that `unit` is non-null. Passing a `null`
`TimeUnit` produces a `NullPointerException` inside
`SmoothWarmingUp`'s constructor at `timeUnit.toMicros(warmupPeriod)`
rather than at the caller's `create()` call site. Fix
`RateLimiter.create(double, long, TimeUnit)` in `RateLimiter.java` to
add `checkNotNull(unit)` before delegating, so the error is
reported at the call site with a clear message.

### N9: Fix TypeToken.getSupertype and getSubtype not validating null class argument

`TypeToken.getSupertype(Class<? super T> superclass)` and
`TypeToken.getSubtype(Class<?> subclass)` do not call `checkNotNull`
on their `Class` argument before use. Passing `null` causes a
`NullPointerException` inside `someRawTypeIsSubclassOf()` or the
type-variable branch, with a stack trace that exposes internal
implementation details rather than pointing to the caller. Fix both
`getSupertype()` and `getSubtype()` in `TypeToken.java` to add
`checkNotNull(superclass)` and `checkNotNull(subclass)` respectively
as their first statements.

### N10: Fix EventBus.post not validating that the event is non-null

`EventBus.post(Object event)` does not call `checkNotNull(event)`
before dispatching. Posting a `null` event causes a
`NullPointerException` inside `SubscriberRegistry.getSubscribers()` at
`event.getClass()`, making the stack trace point to internal code
rather than the caller's `post()` call. Fix `EventBus.post()` in
`EventBus.java` to add `checkNotNull(event)` at the start of the
method so the error is reported at the call site.

## Medium

### M1: Add Cache.policy() runtime introspection API

Add a `CachePolicy` value object and `Cache.policy()` method that
exposes the eviction configuration of a built cache at runtime:
maximum size, expire-after-write duration, expire-after-access
duration, refresh-after-write duration, and whether statistics
recording is enabled. Currently callers must track `CacheBuilder`
settings externally. Changes span `Cache.java` for the new interface
method, a new `CachePolicy.java` value object, `CacheBuilder.java` for
capturing policy metadata, and `LocalCache.java` for implementing the
`policy()` method from the internal segment state.

### M2: Implement CacheBuilder.writer for synchronous write-through hooks

Add a `CacheWriter<K, V>` interface with `write(K key, V value)` and
`delete(K key, V value, RemovalCause cause)` callbacks invoked
synchronously during cache mutations. `CacheBuilder.writer(CacheWriter)`
configures the writer. The writer's `delete()` is called on explicit
`invalidate()`, size-eviction, and expiration-eviction, while `write()`
is called on `put()` and `LoadingCache.get()` loads. Changes span a new
`CacheWriter.java` interface, `CacheBuilder.java` for configuration
and validation, `LocalCache.java` for invoking the writer hooks in
segment put and remove paths, and `RemovalCause.java` for cause
context.

### M3: Add topological sort to the graph package

Implement `Graphs.topologicalOrder(Graph<N>)` returning an
`ImmutableList<N>` of nodes in topological order for directed acyclic
graphs, throwing `IllegalArgumentException` on cyclic input (detected
via the existing `Graphs.hasCycle()` method). The implementation must
work with `Graph`, `ValueGraph`, and `Network` via the
`SuccessorsFunction` abstraction, using Kahn's algorithm for O(V+E)
performance. Changes span `Graphs.java` for the new public API
method, internal helper classes for in-degree tracking, and
`Traverser.java` for shared iteration infrastructure. Also update
the `README.md` to add a "Graph Algorithms" subsection under the
feature overview documenting the new `topologicalOrder()` API with
a usage example.

### M4: Add EventBus subscriber introspection and priority ordering

Implement `EventBus.getRegisteredSubscribers()` that returns a
structured view of all registered subscriber methods grouped by event
type. Add a `@SubscriberPriority(int)` annotation to control dispatch
order for multiple subscribers of the same event type (lower values
execute first). Changes span `EventBus.java` for the introspection
API, `SubscriberRegistry.java` for priority-sorted subscriber
maintenance, `Subscriber.java` for priority extraction from the
annotation, and a new `SubscriberPriority.java` annotation class in
the `eventbus/` package.

### M5: Add RateLimiter warmup introspection and reset support

Implement `RateLimiter.getAvailablePermits()` returning the estimated
number of permits currently available without blocking,
`RateLimiter.reset()` to clear accumulated permits and restart the
warmup period from cold state, and `RateLimiter.isWarmingUp()` to
query whether the limiter is still in its warmup phase. These methods
enable monitoring dashboards and operational tooling to inspect rate
limiter state at runtime. Changes span `RateLimiter.java` for the new
public API methods, `SmoothRateLimiter.java` for warmup state
introspection and reset logic, `SmoothRateLimiter.SmoothWarmingUp` for
warmup phase detection, and `SmoothRateLimiter.SmoothBursty` for
bursty reset behavior.

### M6: Add bounded-size strong interner with LRU eviction

Implement `Interners.newBoundedStrongInterner(int maxSize)` that
provides strong-reference interning with a configurable maximum number
of interned instances. When the bound is reached, the least-recently-
used interned value is evicted. The current `newStrongInterner()` grows
unboundedly and can cause memory exhaustion for large domains. Changes
span `Interners.java` for the new factory method, a new
`BoundedStrongInterner.java` implementation using an LRU eviction
data structure, and `MapMaker.java` for the underlying concurrent map
configuration.

### M7: Add ServiceManager health-check endpoint support

Extend `ServiceManager` to expose a structured health-check report:
`ServiceManager.healthReport()` returning service name, state, startup
duration, last failure cause, and uptime for each managed service. Add
`ServiceManager.awaitHealthy(Duration, HealthCriteria)` that accepts
custom health criteria. Changes touch `ServiceManager`, `Service`,
`AbstractService`, and the service state machine in
`util/concurrent/`.

### M8: Implement CharMatcher composition optimizations

Optimize `CharMatcher.or()`, `and()`, and `negate()` chains by
collapsing nested compositions into flat bitset-based matchers when
all operands are BMP characters. Add `CharMatcher.forPredicate()` for
interop with `java.util.function.IntPredicate`. Changes span
`CharMatcher`, its internal `FastMatcher` subclasses, and the
`base/` package's `Predicate` adapter.

### M9: Add weighted keys support to BloomFilter

Extend `BloomFilter` to support weighted insertions where high-priority
items receive more hash bits for lower false-positive rates. Add
`BloomFilter.create(Funnel, long, double, WeightFunction)` and update
`mightContain()` to respect the weight distribution. Changes span
`BloomFilter`, `BloomFilterStrategies`, the `Funnel` interface, and
the hash-function internals in `hash/`.

### M10: Add counting BloomFilter variant with element removal support

Implement `CountingBloomFilter<T>` backed by a counter array instead
of a bit array, supporting `remove(T)` in addition to `put(T)` and
`mightContain(T)`. Each hash position maintains a 4-bit counter that
increments on `put` and decrements on `remove`, with overflow
detection that saturates at the maximum counter value to prevent
wrap-around corruption. Include `CountingBloomFilter.create(Funnel,
long, double)` factory mirroring the standard `BloomFilter` API.
Changes span a new `CountingBloomFilter.java` class in `hash/`, a new
`CountingBloomFilterStrategies.java` for counter-based hash layouts,
`BloomFilterStrategies.java` for shared bit-manipulation
infrastructure, and the `Funnel` interface for shared funnel
compatibility.

## Wide

### W1: Implement a Guava-to-Java-8 migration module

Create a `com.google.common.migration` package that provides adapters
and automated migration helpers: `com.google.common.base.Optional` ↔
`java.util.Optional`, `Function`/`Predicate`/`Supplier` ↔ `java.util
.function` equivalents, `FluentIterable` → `Stream`, `ListenableFuture`
→ `CompletableFuture`, and `CacheBuilder` → `Caffeine` adapter. Include
static analysis annotations marking deprecated Guava APIs with their
Java 8+ replacements. Changes span `base/`, `collect/`, `cache/`,
`util/concurrent/`, and a new `migration/` package.

### W2: Add reactive streams support to util.concurrent

Implement `Publisher` and `Subscriber` adapters for
`ListenableFuture`, `Service`, and `ServiceManager`: stream of service
state transitions, future-to-publisher conversion, publisher-to-future
conversion, and backpressure handling. Add
`MoreExecutors.reactiveExecutor()` for scheduling. Changes span
`util/concurrent/`, new reactive adapter classes, `Service`,
`ServiceManager`, `ListenableFuture`, and `Futures`.

### W3: Implement persistent (immutable) collection variants

Add `PersistentList`, `PersistentMap`, `PersistentSet` backed by
hash-array mapped tries (HAMTs) offering O(log32 N) updates that
share structure with previous versions. Include `TransientList`/
`TransientMap` for batched mutations. Integrate with `ImmutableList
.toPersistent()` bridge methods. Changes span new classes in
`collect/`, integration with the existing immutable collection
hierarchy, and the iterator framework.

### W4: Implement comprehensive graph algorithm library

Extend the `graph/` package with shortest-path (Dijkstra, Bellman-Ford),
minimum spanning tree (Kruskal, Prim), topological sort, strongly
connected components (Tarjan), max-flow (Edmonds-Karp), and bipartite
matching. Each algorithm must work with `Graph`, `ValueGraph`, and
`Network` generically. Changes span `graph/`, new algorithm classes,
result types, and integration with `Traverser`.

### W5: Add serialization framework for Guava types

Implement a pluggable serialization framework so all Guava collection
types, `Optional`, `Table`, `RangeSet`, `Multimap`, `TypeToken`, and
graph types can be serialized to JSON, Protocol Buffers, or custom
formats. Add `GuavaSerializers.json()`, `GuavaSerializers.proto()`, and
a `SerializerModule` SPI. Changes span `collect/`, `base/`, `graph/`,
`hash/`, `reflect/`, and a new `serial/` package.

### W6: Implement a cache monitoring and management system

Add JMX and micrometer integration to `CacheBuilder`-built caches:
expose hit/miss/eviction/load metrics as MBeans and Micrometer meters,
add `Cache.policy()` for runtime inspection of eviction policies, and
implement `Cache.asMap().listeners()` for entry-level event
notification. Add a cache inspector that can dump cache contents and
statistics to structured output. Changes span `cache/`, `util/concurrent/`
for thread-pool metrics, and a new `cache/management/` sub-package.

### W7: Add defensive-copy and immutability-enforcement annotations

Implement `@Immutable`, `@DeepImmutable`, `@ReturnsCopy`, and
`@AcceptsOnly(immutable=true)` annotations with a compile-time
annotation processor that validates contracts. The processor should
detect mutable fields in `@Immutable` classes, missing defensive copies
on `@ReturnsCopy` methods, and mutable parameters on `@AcceptsOnly`
methods. Changes span `annotations/`, a new `annotations/processor/`
package, and retroactive annotation of core Guava types in `collect/`,
`base/`, and `cache/`.

### W8: Implement a type-safe heterogeneous container framework

Add `TypeSafeMap`, `TypeSafeMultimap`, and `TypeSafeTable` that use
`TypeToken` keys for type-safe heterogeneous storage, replacing the
`Class`-based pattern from Effective Java. Support bounded wildcards,
parameterized types, and serialization. Include `TypeSafeCache` backed
by `CacheBuilder`. Changes span `collect/`, `reflect/TypeToken`,
`cache/`, and new container classes.

### W9: Implement a functional data transformation pipeline

Add `Pipeline<I, O>` in `base/` that composes functions, predicates,
and side-effects into replayable, inspectable transformation chains.
Support branching (`Pipeline.branch(predicate, ifTrue, ifFalse)`),
error handling (`Pipeline.recover()`), async stages backed by
`ListenableFuture`, and metric instrumentation. Integrate with
`FluentIterable.transform()` and `Streams.pipeline()`. Changes span
`base/`, `collect/`, `util/concurrent/`, and `hash/` for content-based
deduplication stages.

### W10: Add multi-release JAR support with JDK-optimized implementations

Restructure Guava to produce a multi-release JAR with JDK 11, 17, and
21 optimized implementations: `VarHandle`-based `Striped` locks,
`MemorySegment`-backed `ByteSource`, virtual-thread-aware
`MoreExecutors`, `SequencedCollection`-backed immutable collections,
and `ScopedValue`-based context propagation replacing `ThreadLocal`
in `EventBus`. Changes span `util/concurrent/`, `io/`, `collect/`,
`eventbus/`, and build configuration including the parent `pom.xml`
for multi-release JAR plugin setup, the `guava/pom.xml` for
`maven-compiler-plugin` multi-release output configuration, and
`.github/workflows/ci.yml` for JDK 11/17/21 matrix testing.

### N11: Fix pom.xml maven-enforcer-plugin not requiring minimum Maven version

The parent `pom.xml` configures numerous Maven plugins but does not
use `maven-enforcer-plugin` to require a minimum Maven version.
Building with Maven 3.6.x silently produces incorrect multi-module
reactor ordering, while 3.8+ handles it correctly. Add a
`maven-enforcer-plugin` execution in the parent `pom.xml` with a
`requireMavenVersion` rule requiring `[3.8.0,)`. Also add a
`requireJavaVersion` rule matching the `surefire.toolchain.version`
property, and update `CONTRIBUTING.md` to document the minimum
Maven and JDK versions required for building.

### M11: Add Dependabot configuration and update parent pom.xml dependency management

The repository has a `.github/dependabot.yml` file but it only
monitors GitHub Actions versions, not Maven dependency updates.
Update `dependabot.yml` to add a `maven` package ecosystem entry
with weekly schedule, targeting the parent `pom.xml`. Add a
`dependabot` label configuration for auto-labeling PRs. Update the
parent `pom.xml` `<dependencyManagement>` section to pin all test
dependencies (`junit`, `truth`, `mockito`) to specific versions
rather than ranges, enabling Dependabot to propose version bumps.
Add a `.github/workflows/scorecard.yml` OpenSSF Scorecard workflow
for supply chain security monitoring. Update
`.github/pull_request_template.md` to include a checklist item for
dependency version justification.

### W11: Overhaul Maven build configuration and project documentation

Comprehensively update all non-code project files for modern build
practices. Restructure the parent `pom.xml` to extract shared
plugin configuration into `<pluginManagement>`, add
`maven-wrapper-validation` enforcement, and configure reproducible
builds via `project.build.outputTimestamp`. Update `guava-bom/pom.xml`
to include version-aligned entries for all published modules
(`guava`, `guava-testlib`, `guava-gwt`). Add a
`.mvn/maven-build-cache-config.xml` for local build caching. Update
`.github/workflows/ci.yml` to add a JDK compatibility matrix
(8, 11, 17, 21), add a `mvn verify -Pguava-bom` job for BOM
validation, and add dependency license scanning. Rewrite
`CONTRIBUTING.md` to add sections on the code review process, test
requirements (unit test for every public method), and the release
process. Update `README.md` to refresh the feature overview,
add migration examples from `java.util` to Guava equivalents, and
add a "Version Compatibility" matrix. Add `SECURITY.md` with
vulnerability reporting instructions.
