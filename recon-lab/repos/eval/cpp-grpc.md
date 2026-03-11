# grpc/grpc

| Field | Value |
|-------|-------|
| **URL** | https://github.com/grpc/grpc |
| **License** | Apache-2.0 |
| **Language** | C++ |
| **Scale** | Very large |
| **Category** | gRPC C++ implementation |
| **Set** | eval |
| **Commit** | `a18875d1df6a3fdac2cd7339a4fdb6aff29db89e` |

## Why this repo

- **Multi-subsystem**: Core C library, C++ client/server, load balancing policies, name resolution, transport (HTTP/2, in-process), security/TLS, xDS, channelz, event engine, compression, resource quota
- **Well-structured**: Clear separation between src/core/ (C core), src/cpp/ (C++ wrappers), include/grpcpp/ (public API), src/compiler/ (protoc plugin)
- **Rich history**: 42K+ stars, production RPC framework with extensive subsystem depth

## Structure overview

```
include/grpcpp/              # Public C++ API headers
├── channel.h, server.h, server_builder.h
├── support/                 # client_callback, async_stream, sync_stream
├── security/                # credentials, TLS, auth
├── ext/                     # channelz, ORCA, OTel, admin
├── test/                    # mock_stream, test peers
src/cpp/
├── client/                  # Channel, credentials, interceptors
├── server/                  # ServerBuilder, server impl, health check, ORCA
├── common/                  # Completion queue, channel arguments
src/core/
├── call/                    # Call infrastructure
├── client_channel/          # Client channel filter, subchannel management
├── load_balancing/          # round_robin, pick_first, ring_hash, weighted_round_robin, priority, xds, rls, grpclb
├── resolver/                # DNS, fake, sockaddr, xds, google_c2p
├── transport/               # HTTP/2, in-process transport
├── credentials/             # Call and transport credentials
├── lib/
│   ├── event_engine/        # Platform event loop abstraction
│   ├── resource_quota/      # Memory and thread quotas
│   ├── compression/         # Message compression
│   ├── channel/             # Channel stack
│   ├── promise/             # Promise-based call API
│   └── transport/           # Transport internals
├── filter/                  # Auth filters
├── handshaker/              # TLS/HTTP handshake
├── tsi/                     # Transport security interface
├── xds/                     # xDS protocol support
├── channelz/                # Channel diagnostics
├── telemetry/               # Telemetry and stats
└── server/                  # Server core
```

## Scale indicators

- ~2,200 C/C++ source files in src/
- ~525K lines of code
- 8+ load balancing policies
- Multiple transport, resolver, and credential backends

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `weighted_round_robin` scheduler not updating weights when backend metric reports arrive late

In `load_balancing/weighted_round_robin/static_stride_scheduler.cc`, the `StaticStrideScheduler` builds a pick sequence from endpoint weights at construction time. When out-of-band backend metrics arrive via `oob_backend_metric.cc` and update weights, the scheduler continues using stale weights until the next full rebuild, causing suboptimal load distribution.

### N2: Fix `ServerBuilder::AddListeningPort` not returning error details on bind failure

In `src/cpp/server/server_builder.cc`, when `AddListeningPort` fails to bind (e.g., port already in use), it sets the output port to 0 but the error reason is discarded. The underlying core error from `grpc_server_add_http2_port` contains the OS-level failure message but it is not propagated to the caller.

### N3: Add `grpc.max_connection_idle_ms` channel argument validation

The channel argument `grpc.max_connection_idle_ms` is parsed via `GetDurationFromIntMillis(GRPC_ARG_MAX_CONNECTION_IDLE_MS)` in `src/core/ext/filters/channel_idle/legacy_channel_idle_filter.cc`. Negative integer values are accepted silently, which produces a negative `Duration` that will never satisfy idle-time comparisons against `Duration::Infinity()`, causing the connection to never idle. Add validation in `legacy_channel_idle_filter.cc` to clamp negative values to zero and emit a log warning. Update `doc/keepalive.md` with validated parameter ranges and default values.

### N4: Fix `ring_hash` policy ring-slot instability when endpoint address order changes

In `load_balancing/ring_hash/ring_hash.cc`, the `Ring` constructor derives the hash key for each endpoint from `endpoint.addresses().front()` (the first element of the address vector) when `GRPC_ARG_RING_HASH_ENDPOINT_HASH_KEY` is not set. The `endpoint_map_` in `UpdateLocked` is keyed by `EndpointAddressSet`, which stores addresses as a sorted set for order-independent comparison. When a resolver returns the same endpoint addresses in a different vector order between resolutions (e.g., DNS may reorder A/AAAA records), the `EndpointAddressSet` key still matches—so the existing `RingHashEndpoint` is reused—but the hash key passed to ring construction changes because `.front()` now returns a different address. This causes the endpoint to land on different ring slots after each re-resolution that reorders addresses, making consistent-hash routing unstable. Fix by computing the hash key from the lexicographically smallest address in the set rather than `.front()` of the vector.

### N5: Fix `CompletionQueue::AsyncNext` not respecting `gpr_timespec` deadline on Windows

In `src/core/lib/surface/completion_queue.cc`, the `cq_next` function converts the `gpr_timespec` deadline to an internal `grpc_core::Timestamp` via `Timestamp::FromTimespecRoundUp`, which rounds sub-millisecond deadlines up to the nearest millisecond. On Windows, the underlying event loop (`grpc_pollset_work`) uses `WaitForSingleObjectEx` with millisecond granularity. For deadlines between 0 and 1 ms, the rounded-up value causes `AsyncNext` to block for up to 1 ms even when the caller intended an immediate poll, and for already-elapsed deadlines close to zero the rounding may produce a nonzero wait. Fix the Windows event loop path to treat any deadline that rounds to 0 ms (i.e., already-elapsed or sub-millisecond) as an immediate zero-wait rather than applying the rounded-up value.

### N6: Fix `ClientCallbackUnary` not invoking `OnReadInitialMetadataDone` before `OnDone` in the simple callback form

In `include/grpcpp/support/client_callback.h`, the simple non-reactor callback form (`CallbackUnaryCall` / `CallbackUnaryCallImpl`) bundles all ops—send message, receive initial metadata, receive message, receive status—into a single batch with one completion tag. The `on_completion` function receives only a `grpc::Status` and is invoked when all ops complete. There is no separate notification when initial metadata arrives, so callers cannot inspect `context->GetServerInitialMetadata()` before the full RPC finishes. By contrast, `ClientCallbackUnaryImpl` (the reactor form) correctly invokes `reactor_->OnReadInitialMetadataDone` via a dedicated `start_tag_` callback. Fix `CallbackUnaryCallImpl` to split the initial-metadata receive into a separate first batch and invoke a user-supplied `on_initial_metadata` callback (defaulting to a no-op) before the response batch, consistent with the reactor implementation.

### N7: Fix DNS resolver delaying address results while waiting for SRV query to complete

In `resolver/dns/c_ares/dns_resolver_ares.cc`, the `OnResolvedLocked` method waits for all sub-queries (hostname/A/AAAA, SRV, TXT) to complete before reporting any result. When `GRPC_ARG_DNS_ENABLE_SRV_QUERIES` is set and the SRV query fails (e.g., NXDOMAIN or SERVFAIL) while the hostname query has already returned addresses, the resolver withholds the available addresses until the SRV failure propagates, causing unnecessary delay in address delivery. There is already a `TODO` comment in the code acknowledging this limitation. Fix `OnResolvedLocked` to report successfully-resolved hostname addresses immediately when the hostname query succeeds, without waiting for outstanding SRV or TXT queries that have no bearing on the address list.

### N8: Fix `fake_resolver` crashing when re-resolution is triggered without a response generator

In `resolver/fake/fake_resolver.cc`, `FakeResolver::RequestReresolutionLocked` unconditionally asserts `GRPC_CHECK(response_generator_ != nullptr)`. A `FakeResolver` can legitimately be constructed without a `FakeResolverResponseGenerator` when a test only needs a static address list and does not intend to re-resolve. If the gRPC channel framework internally triggers re-resolution (e.g., after a subchannel connectivity failure), the hard assertion fires and terminates the test process. Replace the `GRPC_CHECK` with a null guard so that re-resolution requests are silently ignored when no generator is configured, matching the graceful behavior of other resolver implementations.

### N9: Add compression algorithm name to channelz socket trace events

In `src/core/channelz/`, socket trace events record message send/receive but do not include which compression algorithm (gzip, deflate, none) was used for each message. Add the algorithm name to the trace event data in the channelz socket node.

### N10: Fix `pick_first` happy-eyeballs interleaving preferring whichever address family appears first in resolver output

In `load_balancing/pick_first/pick_first.cc`, the happy-eyeballs address interleaving logic builds `address_family_order` by recording the first occurrence of each address family encountered while iterating the flattened endpoint list. The scheme that appears first in the resolver output becomes the first scheme tried during connection, regardless of address family type. When the resolver returns IPv4 addresses before IPv6 (common for DNS A-then-AAAA ordering), IPv4 is tried first, contrary to RFC-8305's recommendation to prefer IPv6. Fix the `UpdateLocked` interleaving logic to reorder `address_family_order` so that the IPv6 family (scheme `"ipv6"` or equivalent) is placed first if present, consistent with RFC-8305 section 4 which mandates preferring IPv6 for Happy Eyeballs.

### N11: Add RST_STREAM error code mapping to `doc/PROTOCOL-WEB.md`

The `doc/PROTOCOL-WEB.md` specification document describes the gRPC-Web protocol but does not document how HTTP/2 RST_STREAM error codes map to gRPC-Web status codes when a stream is abruptly terminated. While `doc/PROTOCOL-HTTP2.md` already contains a RST_STREAM-to-gRPC-status mapping table (e.g., REFUSED_STREAM→UNAVAILABLE, CANCEL→CANCELLED), gRPC-Web has additional constraints because it is typically proxied over HTTP/1.1 connections where RST_STREAM semantics are translated by the proxy. Add a section to `doc/PROTOCOL-WEB.md` documenting RST_STREAM error code handling for gRPC-Web, cross-referencing `doc/PROTOCOL-HTTP2.md` for the base mapping and `doc/statuscodes.md` for gRPC status code definitions, and clarifying how a gRPC-Web proxy should translate RST_STREAM errors for gRPC-Web clients that use HTTP/1.1 connections.

## Medium

### M1: Implement per-call memory tracking and limits

Add per-call memory accounting that tracks allocations for messages, metadata, and serialization buffers. Enforce a configurable per-call memory limit (`grpc.max_call_memory_bytes`) that returns `RESOURCE_EXHAUSTED` when exceeded. Requires changes in `src/core/call/`, `src/core/lib/resource_quota/resource_quota.h`, and the call filter chain to integrate with the existing resource quota system.

### M2: Add client-side request hedging support for unary RPCs

Implement hedging as specified in the gRPC retry design: send multiple copies of a unary RPC simultaneously and use the first successful response. Requires modifying `src/core/client_channel/` for hedging policy parsing from service config, `src/core/call/` for managing parallel call attempts, and `src/cpp/client/` for exposing hedging configuration in `ChannelArguments`.

### M3: Implement graceful server drain with configurable deadline

Add `Server::DrainConnections(deadline)` that sends GOAWAY to all connected clients, waits for in-flight RPCs to complete up to the deadline, then force-closes remaining connections. Requires changes in `src/cpp/server/server_cc.cc`, `src/core/server/`, and the HTTP/2 transport layer in `src/core/ext/transport/` for GOAWAY frame handling.

### M4: Add server-side interceptor support for metadata modification

The server interceptor API in `include/grpcpp/support/server_interceptor.h` allows observing calls but does not support modifying response metadata after handlers execute. Add `ModifyTrailingMetadata` hook that allows interceptors to add or modify trailing metadata before it is sent. Requires changes to the server interceptor interface, method handler implementation in `src/cpp/server/`, and the call filter chain.

### M5: Implement load balancing policy hot-swap without connection disruption

Add support for changing the active load balancing policy at runtime (e.g., from `round_robin` to `weighted_round_robin`) without dropping existing connections. Requires modifying `load_balancing/child_policy_handler.cc`, the subchannel pool in `src/core/client_channel/`, and ensuring in-flight RPCs on existing subchannels complete before the old policy is destroyed.

### M6: Add structured logging with trace context propagation

Implement structured JSON logging for gRPC internals that includes trace IDs and span IDs propagated from incoming `grpc-trace-bin` headers. Requires modifications to `src/core/telemetry/`, the logging infrastructure in `src/core/lib/debug/`, call metadata extraction in `src/core/call/`, and adding a structured log output mode to the C++ server and client wrappers. Update `doc/environment_variables.md` with structured logging configuration options.

### M7: Implement adaptive compression based on message characteristics

Add a `grpc.adaptive_compression` channel argument that selects compression algorithm per-message based on message size and content entropy. Small messages skip compression, large compressible messages use gzip, and already-compressed payloads use identity. Requires changes in `src/core/lib/compression/compression_internal.h`, the message compression filter, and per-call compression tracking.

### M8: Add connection pool management API for C++ client

Implement `ChannelPool` that manages multiple `grpc::Channel` instances to the same target with configurable pool size, connection affinity by call metadata, and automatic pool resizing based on load. Requires new files in `src/cpp/client/`, integration with `create_channel.cc`, and load metrics from the subchannel layer.

### M9: Implement health check aggregation across subchannel groups

Add aggregated health status reporting that combines health check results from all subchannels in a client channel into a single health state. Requires extending `src/cpp/server/health/`, modifying `load_balancing/health_check_client.cc`, and adding a health aggregation policy configurable per service name in the service config.

### M10: Add server-side rate limiting filter

Implement a `RateLimitingFilter` that limits the number of concurrent RPCs per service/method and per-client identity. Return `RESOURCE_EXHAUSTED` with retry-after metadata when limits are exceeded. Requires a new filter in `src/core/filter/`, token bucket implementation, integration with the server filter chain, and configuration via server builder options.

### M11: Improve CMake build configuration and dependency management

Overhaul `CMakeLists.txt` with modern CMake practices (current minimum is 3.16; update to at least 3.20 to unlock `FetchContent_MakeAvailable` improvements): add `FetchContent` integration for third-party dependencies as an alternative to `third_party/` submodules; update `cmake/` support modules with better find-package support for system-installed dependencies; update `requirements.txt` (which exists but lacks hash pinning) with hash verification for Python build dependencies; configure `build_handwritten.yaml` and `build_autogenerated.yaml` with clearer documentation; and update `BUILDING.md` with step-by-step instructions for all supported platforms. Changes span `CMakeLists.txt`, `cmake/`, `requirements.txt`, `build_handwritten.yaml`, `BUILDING.md`, and `MODULE.bazel`.

## Wide

### W1: Implement client-side load reporting and feedback loop

Add client-side load reporting that collects RPC latency, error rates, and backend utilization from response trailers, aggregates them locally, and feeds them back into load balancing decisions. Requires changes to `src/core/load_balancing/` (policy interface and weighted_round_robin), `src/core/client_channel/` (metric collection), `src/cpp/client/` (stats exposure), and `src/core/telemetry/` (metric aggregation with configurable windows).

### W2: Add end-to-end request tracing with W3C Trace Context propagation

Implement W3C Trace Context header (`traceparent`, `tracestate`) propagation through the entire gRPC call stack. Auto-generate trace and span IDs for untraced requests, propagate context through subchannel selection, load balancer picks, and server-side handling. Changes span `src/core/call/`, `src/core/filter/`, `src/core/client_channel/`, `src/cpp/server/`, `include/grpcpp/`, and `src/core/telemetry/`.

### W3: Implement multi-target client channel with failover

Add `MultiChannel` that connects to multiple target URIs simultaneously and fails over to the next target when the primary becomes unhealthy. Support priority-based failover, health-check-driven promotion, and automatic fail-back. Requires a new channel implementation in `src/cpp/client/`, a failover load balancing policy in `src/core/load_balancing/`, resolver integration in `src/core/resolver/`, and modifications to `include/grpcpp/` for the public API.

### W4: Add server reflection v2 with streaming schema updates

Extend the proto server reflection beyond the current `ServerReflection` service to support streaming schema change notifications when services are dynamically registered/unregistered. Requires modifications to `src/cpp/server/` (dynamic service registration), `src/cpp/ext/proto_server_reflection_plugin.cc`, a new streaming reflection service definition, integration with the server's service registry, and channelz for tracking service changes.

### W5: Implement connection migration for mobile clients

Add connection migration support that transparently reconnects RPCs to a new server when the client's network changes (WiFi → cellular). Preserve in-flight unary RPCs by retrying with the original deadline and metadata. Requires changes to `src/core/lib/event_engine/` (network change detection), `src/core/transport/` (connection state machine), `src/core/client_channel/` (subchannel migration), and `src/cpp/client/` (migration policy configuration).

### W6: Add comprehensive server admin and diagnostics API

Implement a gRPC admin service that provides runtime diagnostics: connection list with state and metadata, per-method RPC counters with latency histograms, active call inspection, and dynamic log level adjustment. Requires a new admin service in `src/cpp/server/admin/`, integration with channelz in `src/core/channelz/`, telemetry counters in `src/core/telemetry/`, the server core in `src/core/server/`, and public API in `include/grpcpp/ext/`.

### W7: Implement custom transport plugin API

Add a transport plugin interface that allows registering custom transport implementations (e.g., QUIC, Unix domain socket with SCM_RIGHTS). Define a stable transport API abstracting over `src/core/ext/transport/`, add a transport registry, modify the server and client channel to select transports dynamically, and update `ServerBuilder` and `ChannelArguments` for transport configuration. Changes span `src/core/transport/`, `src/core/ext/transport/`, `src/core/client_channel/`, `src/core/server/`, and `include/grpcpp/`.

### W8: Add deadline propagation and budget management across service meshes

Implement automatic deadline propagation from incoming RPCs to outgoing RPCs, with configurable deadline budget allocation per downstream call. Track deadline budget consumption and expose it via channelz. Requires modifications to `src/core/call/` (deadline context), `src/core/client_channel/` (budget allocation), `src/core/filter/` (deadline filter), `src/cpp/server/` (incoming deadline extraction), `src/core/channelz/` (budget metrics), and `include/grpcpp/` for configuration API.

### W9: Implement service mesh sidecar mode with traffic management

Add a sidecar proxy mode where a gRPC server acts as a transparent proxy with traffic management: request routing by header, traffic splitting by percentage, fault injection, and circuit breaking. Requires a proxy transport in `src/core/ext/transport/`, routing configuration via xDS in `src/core/xds/`, a circuit breaker in `src/core/load_balancing/`, fault injection filter in `src/core/filter/`, and CLI/config integration in `src/cpp/server/`.

### W10: Add comprehensive RPC replay and record infrastructure

Implement `--grpc-record <path>` and `--grpc-replay <path>` modes that capture and replay RPC traffic including metadata, messages, timing, and errors. Support request/response matching for test scenario creation. Requires a recording filter in `src/core/filter/`, a file format and serializer, a replay server in `src/cpp/server/`, a replay client in `src/cpp/client/`, timing simulation, and integration with the test utilities in `include/grpcpp/test/`.

### W11: Overhaul developer documentation and architecture guides

Comprehensively restructure the project's extensive documentation: update `doc/PROTOCOL-HTTP2.md` and `doc/PROTOCOL-WEB.md` with latest protocol version clarifications; consolidate `doc/load-balancing.md`, `doc/naming.md`, `doc/service_config.md`, and `doc/connectivity-semantics-and-api.md` into a coherent client architecture guide; rewrite `BUILDING.md` with platform-specific build instructions for Linux, macOS, Windows, and cross-compilation; update `CONTRIBUTING.md` and `CONTRIBUTING_STEPS.md` with development workflow and CI pipeline explanation; refresh `CONCEPTS.md` with current architecture diagrams; update `TROUBLESHOOTING.md` with common debugging scenarios; add `GOVERNANCE.md` decision-making process documentation; verify `MAINTAINERS.md` and `MANIFEST.md` accuracy; and update `SECURITY.md` vulnerability reporting process. Changes span `doc/` (PROTOCOL-HTTP2.md, load-balancing.md, service_config.md, keepalive.md, environment_variables.md, statuscodes.md, compression.md, versioning.md), `BUILDING.md`, `CONTRIBUTING.md`, `CONTRIBUTING_STEPS.md`, `CONCEPTS.md`, `TROUBLESHOOTING.md`, `GOVERNANCE.md`, `MAINTAINERS.md`, `MANIFEST.md`, `SECURITY.md`, `README.md`, and `CODE-OF-CONDUCT.md`.
