# gabime/spdlog

| Field | Value |
|-------|-------|
| **URL** | https://github.com/gabime/spdlog |
| **License** | MIT |
| **Language** | C++ |
| **Scale** | Medium (header-only with modular sinks and details) |
| **Category** | Logging library |
| **Set** | Cutoff |
| **Commit** | `0f7562a0f9273cfc71fddc6ae52ebff7a490fa04` |

## Why this repo

- **De facto C++ logging standard**: The most popular C++ logging
  library with 24K+ stars, combining a clean header-only API with
  high performance via lock-free queues and fmt-based formatting.
  Navigation requires understanding sink hierarchies, async thread
  pools, and compile-time log-level stripping.
- **Well-structured**: Despite being header-only, the source is
  organized into `sinks/` (~20 sink implementations), `details/`
  (internal machinery for OS abstraction, threading, formatting),
  and `cfg/` (runtime configuration loading) — each with clear
  responsibilities and consistent patterns.
- **Rich feature surface**: Supports synchronous and async logging,
  pattern-based formatting, rotating/daily file sinks, colored
  console output, syslog/systemd/Windows Event Log integration,
  custom sinks via CRTP base classes, backtrace ring buffers,
  and runtime configuration via environment variables or files.
- **Permissive**: MIT license.

## Structure overview

```
include/spdlog/
├── spdlog.h                          # Main public API (default logger shortcuts)
├── logger.h                          # logger class (log, flush, set_level, sinks)
├── async_logger.h                    # Async logger subclass (thread pool based)
├── common.h                          # Enums (level, pattern_time), macros, typedefs
├── tweakme.h                         # Compile-time configuration knobs
├── version.h                         # Version macros
├── formatter.h                       # formatter interface (abstract base)
├── pattern_formatter.h               # Pattern-based formatter (%Y-%m-%d %H:%M:%S, etc.)
├── async.h                           # Async helpers (init/create thread pool)
├── stopwatch.h                       # Elapsed-time stopwatch utility
├── cfg/
│   ├── argv.h                        # Config from command-line arguments
│   ├── env.h                         # Config from environment variables (SPDLOG_LEVEL)
│   ├── helpers.h                     # Parsing helpers for level strings
│   └── log_levels.h                  # Level map container
├── details/
│   ├── registry.h                    # Global logger registry (singleton)
│   ├── registry-inl.h               # Registry inline implementation
│   ├── thread_pool.h                 # Thread pool for async logging
│   ├── thread_pool-inl.h            # Thread pool inline implementation
│   ├── log_msg.h                     # log_msg struct (payload passed to sinks)
│   ├── log_msg_buffer.h             # Owning version of log_msg
│   ├── log_msg-inl.h                # log_msg inline implementation
│   ├── os.h                          # OS abstraction (file ops, time, console)
│   ├── os-inl.h                      # OS inline implementation
│   ├── fmt_helper.h                  # fmt formatting utilities
│   ├── backtracer.h                  # Backtrace ring buffer for dump_backtrace()
│   ├── circular_q.h                  # Lock-free circular queue
│   ├── mpmc_blocking_q.h            # Multi-producer multi-consumer blocking queue
│   ├── periodic_worker.h            # Periodic flush worker thread
│   ├── null_mutex.h                  # No-op mutex for single-threaded sinks
│   ├── synchronous_factory.h        # Factory for synchronous logger creation
│   ├── file_helper.h                 # File open/close/write helpers
│   ├── file_helper-inl.h            # File helper inline implementation
│   ├── tcp_client.h                  # TCP socket client for tcp_sink
│   ├── tcp_client-inl.h             # TCP client inline implementation
│   ├── udp_client.h                  # UDP socket client for udp_sink
│   └── windows_include.h            # Windows header management
├── sinks/
│   ├── base_sink.h                   # CRTP base class for all sinks
│   ├── sink.h                        # Abstract sink interface
│   ├── basic_file_sink.h            # Simple file sink
│   ├── rotating_file_sink.h         # Size-based rotating file sink
│   ├── daily_file_sink.h            # Time-based daily rotating file sink
│   ├── hourly_file_sink.h           # Hourly rotating file sink
│   ├── stdout_color_sinks.h         # Colored stdout/stderr sinks
│   ├── stdout_sinks.h               # Plain stdout/stderr sinks
│   ├── ansicolor_sink.h             # ANSI color formatting sink
│   ├── wincolor_sink.h              # Windows console color sink
│   ├── null_sink.h                   # Discard-all sink (benchmarking)
│   ├── ostream_sink.h               # std::ostream-backed sink
│   ├── dist_sink.h                   # Fan-out distribution sink
│   ├── dup_filter_sink.h            # Duplicate message filter sink
│   ├── ringbuffer_sink.h            # In-memory ring buffer sink
│   ├── callback_sink.h              # User callback sink
│   ├── syslog_sink.h                # POSIX syslog sink
│   ├── systemd_sink.h               # systemd journal sink
│   ├── tcp_sink.h                    # TCP socket sink
│   ├── udp_sink.h                    # UDP socket sink
│   ├── mongo_sink.h                  # MongoDB sink
│   ├── qt_sinks.h                    # Qt signal-based sink
│   └── msvc_sink.h                   # MSVC OutputDebugString sink
└── fwd.h                             # Forward declarations
```

## Scale indicators

- ~60 header files under `include/spdlog/`
- ~15K lines of C++ header-only code
- ~20 sink types following CRTP `base_sink` pattern
- Zero required dependencies (optional fmt, header-only)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix rotating_file_sink not handling max_files=0 correctly

The `rotating_file_sink` in `sinks/rotating_file_sink.h` accepts
`max_files=0` in its constructor without validation. When rotation
triggers, the sink attempts to rename files with a modular arithmetic
expression that produces incorrect filenames when `max_files` is zero.
Fix the constructor to reject `max_files=0` with an
`spdlog_ex` exception. Also update `README.md` to document the
valid range for `max_files` in the rotating file sink usage example,
and add a note to the API section clarifying the minimum value.

### N2: Fix pattern_formatter not refreshing compiled formatters after add_flag()

`pattern_formatter` in `pattern_formatter.h` maintains two parallel
data structures: `custom_handlers_` (a map from flag char to handler
used as the source during pattern compilation) and `formatters_` (the
compiled vector of flag formatters used at format time). When
`add_flag()` is called after `set_pattern()` has already compiled the
pattern, it updates `custom_handlers_` but leaves `formatters_` stale.
Any flag character already present in the compiled pattern continues to
use the old handler clone stored in `formatters_` instead of the new
one. Fix `add_flag()` in `pattern_formatter.h` to call
`compile_pattern_(pattern_)` after updating `custom_handlers_` whenever
a pattern has already been set, so that `formatters_` is rebuilt to
reflect the new handler.

### N3: Fix daily_file_sink calculating next rotation time incorrectly across DST

The `daily_file_sink` in `sinks/daily_file_sink.h` computes the next
rotation time by adding 24 hours to the current rotation point. When
a DST transition occurs, this causes the rotation to fire one hour
early or late. Fix `next_rotation_tp_()` to compute the target
wall-clock time directly using `std::mktime` rather than adding a
fixed duration.

### N4: Fix log_msg_buffer not capturing MDC context for cross-thread consumption

`log_msg_buffer` in `details/log_msg_buffer-inl.h` copies the
`logger_name` and `payload` string_views into an owned buffer, and
copies value-type fields (`level`, `time`, `thread_id`) via the base
`log_msg` copy. However, it does not capture mapped diagnostic context
(MDC) data from `mdc.h`, which is stored in thread-local storage.
When `log_msg_buffer` is consumed on the async logging thread pool
(via `details/thread_pool.h`), the MDC entries from the originating
thread are inaccessible because the worker thread has its own
thread-local storage. Fix `log_msg_buffer` to snapshot the current
thread's MDC data at construction time and make it available when
the buffer is processed on a different thread.

### N5: Fix periodic_worker not catching exceptions from callback function

The `periodic_worker` in `details/periodic_worker.h` runs the
user-provided callback on a background timer thread without any
exception handling. If the callback throws (e.g., during a flush
operation that encounters an I/O error), the exception propagates
uncaught out of the thread, invoking `std::terminate()` and crashing
the process. Fix the worker loop in `periodic_worker.h` to wrap the
`callback_fun()` invocation in a try/catch block that suppresses the
exception and continues the periodic loop.

### N6: Fix backtracer double-pushing messages when normal logging is enabled

The `logger` class in `logger-inl.h` pushes messages to the backtrace
ring buffer in two separate places: once inside `sink_it_()` (via the
`should_backtrace()` check at the bottom of that method) and once
directly in `log_it_()` (via the unconditional `tracer_.push_back`
call when `traceback_enabled` is true). When a message passes the
logger-level check (`log_enabled=true`), both code paths execute,
causing the message to consume two slots in the `circular_q` inside
`details/backtracer.h`. This halves the effective backtrace capacity
for normally-logged messages. Fix `logger::sink_it_()` in `logger.h`
by removing the `should_backtrace()` / `tracer_.push_back()` call from
it, leaving `log_it_()` as the sole place that enqueues messages into
the backtrace.

### N7: Fix dup_filter_sink comparing only payload without level

The `dup_filter_sink` in `sinks/dup_filter_sink.h` suppresses
consecutive duplicate messages by comparing only the message payload.
Two messages with identical text but different log levels (e.g.,
one warning and one error) are incorrectly collapsed into one. Fix
the duplicate detection to also compare the log level.

### N8: Fix file_helper::open not setting file permissions on POSIX

`file_helper::open()` in `details/file_helper.h` uses `std::fopen`
to create log files, inheriting the process umask. There is no way
to specify file permissions for newly created log files. Add an
optional `mode_t` parameter to `open()` that calls `fchmod` after
file creation on POSIX platforms to set the desired permissions.

### N9: Fix thread_pool worker not calling on_thread_stop when on_thread_start throws

The `thread_pool` constructor in `details/thread_pool-inl.h` spawns
worker threads using a lambda that calls `on_thread_start()`, then
`worker_loop_()`, then `on_thread_stop()` in sequence. If
`on_thread_start()` throws an exception, the thread function unwinds
immediately without ever invoking `on_thread_stop()`. The
`on_thread_stop` callback is a user-supplied cleanup hook (e.g., for
deregistering the thread from a profiler or runtime), and it must be
called exactly once per corresponding `on_thread_start` call. Fix the
worker lambda in `details/thread_pool-inl.h` to call `on_thread_stop()`
in a RAII guard or try/catch so it is invoked even when
`on_thread_start()` throws.

### N10: Fix cfg::helpers not supporting quoted logger names with special characters

The `load_levels()` helper in `cfg/helpers-inl.h` splits the level
spec string on commas and equals signs using simple `std::getline`
and `find` operations. Logger names containing commas or equals signs
(e.g., `"app.db=query"`) cannot be specified because the parser
splits on these characters without supporting quoting or escaping.
Fix the parsing in `cfg/helpers-inl.h` to support quoted logger
names (e.g., `"app.db=query"=debug`) by detecting and handling
double-quoted strings in `extract_kv_` and `extract_key_vals_`.

## Medium

### M1: Add structured JSON logging formatter

Implement a `json_formatter` class alongside `pattern_formatter` in
`pattern_formatter.h` that emits log messages as single-line JSON
objects with fields for timestamp, level, logger name, thread ID,
source location, and message. The formatter must implement the
`formatter` interface in `formatter.h`, integrate with the
`logger` class in `logger.h` for per-logger formatter selection,
and support user-configurable field inclusion/exclusion. Also update
`cmake/spdlog.pc.in` to add the JSON formatter as a noted feature
of the library, and update `README.md` to add a "JSON Logging"
section with a usage example showing the `json_formatter`
configuration.

### M2: Implement log sampling sink with rate limiting

Create a `sampling_sink` in `sinks/` that wraps another sink and
applies configurable rate limiting: N messages per time window,
with support for per-level rate limits. The sink must integrate
with the `base_sink` CRTP pattern in `sinks/base_sink.h`, use
the `circular_q` from `details/circular_q.h` for token bucket
tracking, and be selectable through `logger.h` sink composition.

### M3: Add runtime pattern reconfiguration via registry

Extend the `registry` in `details/registry.h` with a new
`set_pattern(logger_name, pattern)` method that changes the
formatting pattern of a specific logger at runtime. The change
must propagate through the logger's sinks in `logger.h`, handle
the async logger case in `async_logger.h` by signaling the
thread pool in `details/thread_pool.h` to swap formatters
without losing messages, and update the `cfg/` layer to support
reloading patterns from environment variables.

### M4: Implement file sink with compression support

Create a `compressed_file_sink` in `sinks/` that writes log
output using gzip compression. The sink must follow the
`base_sink` CRTP pattern, integrate with `details/file_helper.h`
for file management, support rotation compatible with
`rotating_file_sink.h` semantics, and implement a flush strategy
that finalizes the gzip stream on flush calls without closing the
file.

### M5: Add conditional logging with lazy message evaluation

Implement a `log_if(condition, level, msg)` method on the `logger`
class in `logger.h` that evaluates the format string and arguments
only if both the condition is true and the level is active. Add
corresponding free-function wrappers in `spdlog.h` for the default
logger, and add `SPDLOG_LOGGER_INFO_IF`, `SPDLOG_LOGGER_WARN_IF` and
similar convenience macros in `common.h` (following the existing macro
patterns). Update `async_logger.h` to override `log_if` so that the
condition is evaluated on the calling thread before any async dispatch,
ensuring no `log_msg` allocation occurs when the condition is false.

### M6: Implement scoped context logging with RAII push/pop semantics

The existing `mdc.h` provides a flat `put`/`remove` API for
thread-local key-value context that is rendered via the `%&` pattern
flag in `pattern_formatter.h`. However, there is no RAII helper that
automatically restores the previous value when a scope exits, making
nested context scopes error-prone. Add a `spdlog::scoped_context` RAII
class in a new `include/spdlog/scoped_context.h` that saves the current
value of a key on construction, calls `mdc::put(key, value)`, and
restores the original value (or removes the key if it was absent) on
destruction. Update `log_msg_buffer.h` to snapshot the current thread's
MDC map at construction time so that async loggers (via
`details/thread_pool.h`) replay the originating thread's context rather
than the worker thread's empty context. Update `pattern_formatter.h` to
read the MDC snapshot from the `log_msg_buffer` when available instead
of calling `mdc::get_context()` directly at format time.

### M7: Add hot-reload support for configuration files

Extend the `cfg/` module with a `file_watcher` that monitors a
TOML or INI configuration file and applies changes at runtime.
On change, update logger levels via `cfg/log_levels.h`, update
formatting patterns via the registry in `details/registry.h`, and
add or remove sinks from loggers in `logger.h`. Use the
`periodic_worker` from `details/periodic_worker.h` for polling.

### M8: Implement correlation ID propagation across loggers

Add support for an `spdlog::correlation_id` that is attached to
a logging scope and automatically included in all log messages
across multiple loggers. The ID must be stored in thread-local
storage, carried through `details/log_msg.h`, serialized by
`pattern_formatter.h` via a new `%j` flag (not yet used by any
existing formatter), preserved when messages cross the async
boundary in `details/thread_pool.h` by snapshotting the ID into
`log_msg_buffer.h`, and configurable via the `cfg/` layer.

### M9: Add sink filtering by log level range

Implement a `level_filter_sink` in `sinks/` that wraps another
sink and only forwards messages within a configurable level range
(e.g., only warnings and errors). The filter must compose with
`dist_sink.h` for fan-out routing, integrate with `logger.h` for
per-sink level overrides, and support runtime reconfiguration
through the `registry` in `details/registry.h`.

### M10: Implement log message enrichment with system metadata

Add an `enrichment_sink` in `sinks/` that decorates log messages
with system metadata (hostname, PID, thread name, executable path)
before forwarding to a wrapped sink. The metadata collection must
use OS abstractions from `details/os.h`, the enrichment must be
visible to `pattern_formatter.h` via new format flags, and the
sink must correctly handle the async case where metadata should
be captured at log time rather than sink time.

## Wide

### W1: Implement distributed logging with log aggregation protocol

Build a log aggregation system with a `collector_sink` that
serializes log messages into a compact binary protocol and sends
them to a remote collector over TCP. Implement a corresponding
collector server that deserializes and routes messages to local
sinks. Changes span a new `sinks/collector_sink.h` for the client
sink, `details/tcp_client.h` for protocol framing,
`details/log_msg.h` for binary serialization of log messages,
the `registry` in `details/registry.h` for collector registration,
`pattern_formatter.h` for rendering collector metadata, and
`cfg/` for collector endpoint configuration.

### W2: Add OpenTelemetry-compatible log exporter

Implement an OpenTelemetry Logs data model adapter that maps spdlog
messages to the OTLP log record structure and exports them over TCP
using the existing `details/tcp_client.h` infrastructure. Build an
`otlp_sink` that batches log records in a compact OTLP-compatible
binary encoding (resource attributes, scope, severity, body,
trace context) and flushes them to a configurable collector endpoint.
Changes span a new `sinks/otlp_sink.h` for the exporter sink,
`details/log_msg.h` for trace context fields (trace_id, span_id as
optional string fields), `pattern_formatter.h` for trace context
rendering via new `%q` and `%Q` flags (trace_id and span_id),
`details/thread_pool.h` for batched async export using the existing
MPMC queue, `logger.h` for a `set_trace_context(trace_id, span_id)`
API that stores the IDs in thread-local storage, and `cfg/` for
collector endpoint and batch-size configuration.

### W3: Implement compile-time log format validation and optimization

Build a compile-time system that validates pattern format strings
at compile time and generates optimized formatter code. Use
`constexpr` parsing to decompose patterns in `pattern_formatter.h`
into a sequence of typed formatter objects, eliminate runtime
pattern parsing overhead, detect invalid flags at compile time,
and generate specialized fast paths for common patterns. Changes
span `pattern_formatter.h` for constexpr pattern decomposition,
`common.h` for compile-time string utilities, `formatter.h` for
optimized static formatter interface, `logger.h` for templated
log methods using static formatters, `spdlog.h` for macro updates,
and `details/fmt_helper.h` for constexpr format helpers.

### W4: Add multi-process safe file logging with advisory locking

Implement file sinks that are safe for multiple processes writing
to the same log file. Build a `shared_file_sink` with advisory
file locking, atomic append support, and stale lock recovery.
Changes span a new `sinks/shared_file_sink.h` for the locking
sink, `details/file_helper.h` for advisory lock operations and
atomic append mode, `details/os.h` for cross-platform file locking
primitives, `sinks/rotating_file_sink.h` for multi-process-safe
rotation coordination, `sinks/daily_file_sink.h` for coordinated
daily rotation, and `details/registry.h` for shared file handle
management across loggers. Also update `CMakeLists.txt` to add a
`SPDLOG_ENABLE_FILE_LOCKING` option that conditionally compiles the
advisory locking code, and update `.github/workflows/linux.yml`
to add a CI job testing multi-process file logging scenarios.

### W5: Implement hierarchical logger namespaces with inheritance

Build a hierarchical logger system where loggers form a tree based
on dot-separated names (e.g., `app.db.query` inherits from `app.db`).
Child loggers inherit parent levels, sinks, and formatters unless
overridden. Changes span `details/registry.h` for tree-structured
logger storage and lookup, `logger.h` for parent pointer and
inheritance resolution, `async_logger.h` for inherited thread pool
configuration, `sinks/base_sink.h` for inherited sink chains,
`pattern_formatter.h` for logger hierarchy rendering, and `cfg/`
for hierarchical level configuration syntax.

### W6: Add log pipeline with filter, transform, and route stages

Build a configurable log processing pipeline where messages flow
through filter, transform, and route stages before reaching sinks.
Implement a DSL for pipeline definitions in configuration files.
Changes span a new `pipeline/` directory with `filter_stage.h`,
`transform_stage.h`, and `route_stage.h`, modifications to
`logger.h` for pipeline attachment, `details/log_msg.h` for
mutable message context through stages, `details/thread_pool.h`
for async pipeline execution, `cfg/` for pipeline DSL parsing,
and `sinks/dist_sink.h` for pipeline-aware fan-out.

### W7: Implement structured logging with type-safe fields and indexing

Build a structured logging system where log messages carry typed
key-value fields alongside the text payload. Fields are
first-class objects available for programmatic access, filtering,
and indexing. Changes span `details/log_msg.h` for field storage,
`logger.h` for field-aware log API, `pattern_formatter.h` for
field rendering with format specifiers, `sinks/base_sink.h` for
field-aware sink interface, `sinks/basic_file_sink.h` for indexed
field output, `details/thread_pool.h` for field propagation across
async boundary, and `cfg/` for field schema configuration.

### W8: Add dynamic sink routing based on message content

Implement a `routing_sink` that dispatches log messages to different
backend sinks based on configurable matching rules applied to the
message payload, level, logger name, and structured fields.
Support regex matching, glob patterns, and custom predicates.
Changes span a new `sinks/routing_sink.h` for the router,
`sinks/base_sink.h` for route-aware sink interface,
`details/log_msg.h` for routing metadata, `details/registry.h`
for route table management, `logger.h` for route override API,
`pattern_formatter.h` for route-hit logging, and `cfg/` for
routing rule configuration syntax.

### W9: Implement log replay and time-travel debugging system

Build a log capture and replay system that records all log messages
with precise timestamps into a binary journal file and supports
replaying them through arbitrary sink configurations at original
or modified speed. Changes span a new `sinks/journal_sink.h` for
binary capture, `details/log_msg.h` for nanosecond timestamp
precision, a new `replay/journal_reader.h` for binary parsing,
`replay/player.h` for timed playback, `logger.h` for replay
logger creation, `pattern_formatter.h` for replay timestamp
rendering, and `details/thread_pool.h` for time-simulated async
replay.

### W10: Add multi-tenant logging with namespace isolation and resource limits

Implement a multi-tenant logging system where separate tenants
get isolated logger namespaces, independent sink configurations,
and per-tenant resource limits (message rate, buffer size, file
quota). Changes span `details/registry.h` for tenant-scoped
registries, `logger.h` for tenant-aware logger construction,
`details/thread_pool.h` for per-tenant async queue partitioning
with backpressure, `sinks/rotating_file_sink.h` for tenant quota
enforcement, `sinks/base_sink.h` for tenant-scoped sink lifecycle,
`details/backtracer.h` for tenant-isolated backtrace buffers,
and `cfg/` for tenant configuration schema.

### N11: Update cmake pkg-config and CMake config templates for new features

The `cmake/spdlog.pc.in` and `cmake/spdlogConfig.cmake.in` templates
define the pkg-config and CMake package configuration but do not
expose optional feature flags (such as `SPDLOG_USE_STD_FORMAT` or
async logging support) to downstream consumers. Update
`cmake/spdlog.pc.in` to add `Cflags` entries for active compile
definitions (e.g., `-DSPDLOG_USE_STD_FORMAT` when enabled). Update
`cmake/spdlogConfig.cmake.in` to export these definitions as
interface compile definitions on the `spdlog::spdlog` target. Also
verify that `cmake/utils.cmake` correctly extracts the version from
`include/spdlog/version.h`.

### M11: Add CI coverage for new sink types and update build documentation

The `.github/workflows/` directory contains CI for Linux, macOS,
Windows, and Coverity scanning, but some sink types (syslog,
systemd, TCP/UDP) are only tested on specific platforms. Update
`.github/workflows/linux.yml` to add a job that builds with
`-DSPDLOG_BUILD_TESTS_HO=ON` and enables the syslog and systemd
sink tests. Update `.github/workflows/macos.yml` to add a job
testing the ostream and callback sinks. Update `README.md` to add
a "Sink Reference" section with a table listing all available sinks,
their header paths, and platform requirements. Also update
`appveyor.yml` to align the Windows CI matrix with the GitHub
Actions configuration and test the MSVC and wincolor sinks.

### W11: Overhaul build system and packaging across CMake, pkg-config, and CI

The build configuration spans `CMakeLists.txt`, `cmake/utils.cmake`,
`cmake/ide.cmake`, `cmake/spdlog.pc.in`, `cmake/spdlogConfig.cmake.in`,
`cmake/spdlogCPack.cmake`, `cmake/version.rc.in`, and `appveyor.yml`.
Audit and synchronise all of these: update `CMakeLists.txt` to add
a `SPDLOG_INSTALL_HEADERS` option for header-only embedding
scenarios and a `SPDLOG_SYSTEM_INCLUDES` option that marks the
include directory as `SYSTEM` to suppress warnings in downstream
builds. Update `cmake/spdlogCPack.cmake` to generate Debian and
RPM packages with correct dependencies. Update `cmake/version.rc.in`
to pull the version from the CMake project version. Add a
`.clang-tidy` configuration update to enable modernize-* checks
for the source files. Update `.github/workflows/linux.yml` to add
a packaging CI job that tests CPack generation. Update `README.md`
with an "Installation" section covering CMake, pkg-config, Conan,
and vcpkg installation methods.
