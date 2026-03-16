# ruby-grape/grape

| Field | Value |
|-------|-------|
| **URL** | https://github.com/ruby-grape/grape |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Medium |
| **Category** | REST API framework |
| **Set** | eval |
| **Commit** | `f2fc392dcae0c1e1fbf4196fa3f51a024e7f22db` |

## Why this repo

- **Full REST framework**: Routing, parameter validation, content negotiation, versioning, authentication, error handling
- **Rich DSL**: Declarative API definition with `desc`, `params`, `get`/`post` blocks, namespace scoping, and middleware stacking
- **Extensible validation**: Custom validators, type coercion, contract-based validation with dry-types integration

## Structure overview

```
lib/grape/
├── api.rb                    # Main API class and class-level DSL
├── api/instance.rb           # API instance evaluation
├── endpoint.rb               # Request processing, filter execution, validation
├── router.rb                 # Route compilation, matching, and dispatch
├── router/                   # Route, pattern, greedy_route, base_route
├── dsl/                      # DSL modules: routing, callbacks, helpers, parameters
│   ├── routing.rb            # version, mount, namespace, route methods
│   ├── parameters.rb         # requires, optional, group parameter DSL
│   ├── inside_route.rb       # body, status, header, error!, redirect
│   ├── callbacks.rb          # before, after, before_validation hooks
│   ├── helpers.rb            # Helper module mounting
│   └── request_response.rb   # content_type, formatter, error handling
├── middleware/               # Rack middleware: error, formatter, filter, globals
├── validations/              # Parameter validation framework
│   ├── params_scope.rb       # Nested parameter scope with coercion
│   ├── types/                # Coercers: primitive, array, JSON, dry-type, custom
│   └── validators/           # Built-in validators: presence, regexp, length, etc.
├── exceptions/               # Exception classes for validation, headers, methods
├── formatter/                # Output formatters: JSON, XML, TXT, serializable_hash
├── error_formatter/          # Error response formatters
├── parser/                   # Input parsers: JSON, XML
├── params_builder/           # Parameter hash builders
├── presenters/               # Presenter base class
└── util/                     # Cache, inheritable settings, media type, header
```

## Scale indicators

- ~288 Ruby source files
- ~35K lines of code
- Rack-based middleware architecture
- Extensive parameter validation and type coercion

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `Router#rotation` not preserving `Allow` header across mounted APIs

In `lib/grape/router.rb`, the `rotation` method iterates candidate routes for a given path. When multiple mounted APIs define routes for the same path with different HTTP methods, the `Allow` header in the 405 response only includes methods from the last mounted API, dropping methods from earlier mounts. Document the correct multi-mount pattern in `CHANGELOG.md`.

### N2: Fix `params_scope` not validating `mutually_exclusive` across nested groups

In `lib/grape/validations/params_scope.rb`, the `mutually_exclusive` validator checks that at most one of the listed parameters is present. When the mutually exclusive parameters are declared inside different nested `group` blocks, the cross-group validation is silently skipped because each group creates its own scope.

### N3: Add `length` validator type-safety check for non-collection parameters

The `LengthValidator` in `lib/grape/validations/validators/length_validator.rb` validates length using duck-typing (`param.respond_to?(:length)`). When applied to a non-collection type such as `Integer`, `Float`, or `Symbol`, the validator silently skips validation (returns early) rather than raising a clear configuration error. Developers who mistakenly write `requires :count, type: Integer, length: { max: 10 }` get no feedback that the constraint is ignored. Add a type-check in `LengthValidator#initialize` that raises `ArgumentError` when the declared coerce type is not one of `String`, `Array`, or `Hash`, matching the pattern already used by other validators in the same directory.

### N4: Fix `Endpoint#run` losing the computed response when `after` callbacks raise

In `lib/grape/endpoint.rb`, the `run` method calls `run_filters afters, :after` after `response_object = execute` computes the response. If an `after` callback raises an unhandled exception, it propagates before `[status, header, response_object]` is returned, bypassing Grape's error-handling middleware and producing a raw 500 error instead of a formatted Grape error response. Wrap `after`-filter execution in a rescue block that re-raises via `error!` so the response is formatted consistently with other runtime errors.

### N5: Add `params_builder` option for `Hashie::Mash` with symbolized keys

The `HashieParams` builder in `lib/grape/params_builder/hashie_mash.rb` creates `Hashie::Mash` instances with string keys. Add a `symbolized_hashie_mash` builder variant that creates `Hashie::Mash` instances where keys are symbols, matching the behavior of `params_builder: :hash` but with Mash's method-access convenience.

### N6: Fix `PrimitiveCoercer` not handling `BigDecimal` string coercion correctly

In `lib/grape/validations/types/primitive_coercer.rb`, coercing a string like `"1.23e-4"` to `BigDecimal` uses `BigDecimal()` which works, but coercing `"NaN"` or `"Infinity"` also succeeds and creates a non-finite BigDecimal. The coercer should reject non-finite values as invalid input.

### N7: Fix `version` matcher not working with `path` versioning and `mount`

In `lib/grape/dsl/routing.rb`, when an API uses `version 'v1', using: :path` and is mounted inside another API that also uses path versioning, the version prefix is duplicated in the URL (e.g., `/v1/v1/resource`). The `prepare_version` method in `endpoint.rb` doesn't check for already-present version prefixes from the parent mount.

### N8: Fix `RegexpValidator` error message not identifying the failing array element index

The `RegexpValidator` in `lib/grape/validations/validators/regexp_validator.rb` validates array parameters element-by-element via `Array.wrap(params[attr_name]).all? { |param| param.nil? || scrub(param.to_s).match?(value) }`. When an element fails the regexp check the validator raises a single `Grape::Exceptions::Validation` that reports only the top-level parameter name (e.g., `items`), giving no indication of which index failed. Modify `validate_param!` so that when the parameter is an `Array`, it iterates with an index and reports the failing element as `items[0]`, `items[1]`, etc., matching the convention used by other array-aware validators in the codebase.

### N9: Fix `content_type` override in `dsl/request_response.rb` not taking effect for error responses

When a custom `content_type` is declared via the DSL (e.g., `content_type :json, 'application/vnd.api+json'`), successful responses use the custom type. However, error responses generated by `error!` or validation failures revert to the default `application/json` because the error formatter resolves content type independently of the DSL override.

### N10: Add `before_validation` callback access to declared parameter types

The `before_validation` callback in `lib/grape/dsl/callbacks.rb` fires before parameter validation. Inside this callback, there's no API to inspect the declared parameter types and requirements for the current endpoint. Add a `declared_params_info` method to the endpoint context that returns parameter names, types, and validation rules.

### N11: Fix `UPGRADING.md` not covering breaking parameter validation changes

The `UPGRADING.md` file documents API-level breaking changes for major version upgrades but omits breaking changes in the parameter validation subsystem (e.g., `params_scope` coercion behavior changes, `mutually_exclusive` semantics). Add a "Validation Changes" section to `UPGRADING.md` for each major version, cross-reference the corresponding `CHANGELOG.md` entries, and update `CONTRIBUTING.md` with a requirement to update `UPGRADING.md` for any validation-related breaking changes.

## Medium

### M1: Implement request rate limiting middleware

Add a `Grape::Middleware::RateLimiter` that limits requests per client based on configurable strategies (IP, API key, or custom identifier). Support fixed-window and sliding-window algorithms with in-memory and Redis-backed stores. Add DSL methods `throttle` and `throttle_by` for per-endpoint rate limit configuration. Return `429 Too Many Requests` with `Retry-After` header when exceeded.

### M2: Add OpenAPI 3.1 specification generation

Implement automatic OpenAPI 3.1 specification generation from Grape API definitions. Extract route definitions, parameter validations, response types, and descriptions. Support `$ref` components for reusable schemas, security schemes from authentication blocks, and server definitions from mount points. Add a `GET /openapi.json` endpoint. Changes span DSL metadata extraction, a schema builder, and spec rendering. Update `README.md` with OpenAPI generation usage and add documentation to `CONTRIBUTING.md` for maintaining schema accuracy.

### M3: Implement typed response declaration and validation

Add `returns` DSL method that declares expected response schemas: `returns Code: 200, Schema: UserEntity, Description: 'Success'`. Validate that endpoint return values match the declared schema in development/test mode. Add response type information to route metadata for documentation generation. Changes span `dsl/request_response.rb`, `endpoint.rb`, and add a response validation module.

### M4: Add request/response compression middleware

Implement `Grape::Middleware::Compression` that supports gzip, deflate, and brotli compression. Handle `Accept-Encoding` negotiation, compress responses above a configurable size threshold, and decompress request bodies with `Content-Encoding` header. Add `compress` DSL option for per-endpoint control. Changes span middleware, formatter, parser, and DSL modules.

### M5: Implement pagination helpers with multiple strategies

Add pagination support with cursor-based, offset-based, and keyset strategies. Implement `paginate` DSL method that adds pagination parameters, applies limits to results, and sets `Link` headers for navigation. Support configurable page size limits and default ordering. Changes span `dsl/parameters.rb`, `dsl/inside_route.rb`, and add a `pagination/` module.

### M6: Add structured logging with request lifecycle tracking

Implement structured JSON logging middleware that captures request start, parameter validation, endpoint execution, and response formatting as distinct log events with timing. Add a `request_id` that propagates through all log entries. Support correlation with external tracing headers. Changes span middleware, endpoint, router, and add a logging module.

### M7: Implement WebSocket endpoint support

Add WebSocket support via `websocket` route DSL method. Implement WebSocket handshake handling in the router, connection lifecycle management, message routing to handler blocks, and room/channel-based broadcasting. Support parameter validation on connection and per-message validation. Changes span routing DSL, router, endpoint, and add a `websocket/` module.

### M8: Add content versioning with deprecation support

Implement content-type-based API versioning (`Accept: application/vnd.myapp.v2+json`) with deprecation tracking. Add `deprecated` DSL method that marks versions/endpoints as deprecated with sunset dates. Return `Sunset` and `Deprecation` headers automatically. Track deprecated endpoint usage. Changes span `dsl/routing.rb`, middleware/versioner, formatter, and configuration.

### M9: Extend dry-validation contract integration with namespace inheritance and array error paths

The existing `contract` DSL (`lib/grape/dsl/validations.rb`), `ContractScope` (`lib/grape/validations/contract_scope.rb`), and `ContractScopeValidator` (`lib/grape/validations/validators/contract_scope_validator.rb`) support `Dry::Schema` and `Dry::Validation::Contract` as validation backends. Three gaps remain: (1) when a `contract` is declared inside a `namespace` or `mount` block, the `namespace_stackable[:contract_key_map]` is not merged with the parent scope's key map, silently dropping parent-scope key constraints; (2) `build_errors_from_messages` does not handle message paths with integer indices (array element errors), generating malformed param names such as `items[]` instead of `items[0]`; (3) combining a `contract` block with regular `requires`/`optional` declarations in the same endpoint silently drops the regular validations. Fix all three issues, add integration specs for each, and update `CHANGELOG.md`.

### M10: Add endpoint-level caching with conditional GET support

Implement response caching with `cache` DSL method supporting TTL, ETag, and Last-Modified strategies. Handle conditional GET (`If-None-Match`, `If-Modified-Since`) returning 304 responses. Support cache key customization and cache store backends (memory, Redis). Changes span `dsl/inside_route.rb`, middleware, and add a `caching/` module.

### M11: Modernize release and documentation infrastructure

Update the project's release and documentation files to match the current tooling: rewrite `RELEASING.md` so its release steps reference the automated edge release workflow in `.github/workflows/edge.yml` instead of the hand-crafted `rake release` instructions from version 0.6.0; update `CONTRIBUTING.md` to add a "Validation Breaking Changes" section that requires a `UPGRADING.md` entry for any change to parameter coercion or validation semantics; reformat `CHANGELOG.md` headers from version 3.0.0 onward to follow the [Keep a Changelog](https://keepachangelog.com/) specification (`## [version] - YYYY-MM-DD`, `### Added`, `### Fixed`, etc.); expand `SECURITY.md` with a disclosure timeline (initial response ≤ 7 days, patch release ≤ 30 days) and a supported-versions table; and update `README.md` with a quickstart code example and links to `CONTRIBUTING.md`, `UPGRADING.md`, and `SECURITY.md`. Changes span `RELEASING.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `SECURITY.md`, and `README.md`.

## Wide

### W1: Implement GraphQL endpoint integration alongside REST

Add GraphQL support that coexists with REST endpoints in the same Grape API. Implement a `graphql` DSL block that defines types, queries, mutations from Grape's route definitions. Share parameter validation, authentication, and middleware between REST and GraphQL paths. Changes span routing DSL, endpoint, middleware stack, resolver generation, and add a `graphql/` module.

### W2: Add real-time API analytics and monitoring dashboard

Implement request analytics middleware that tracks endpoint hit counts, response times, error rates, and parameter patterns. Store metrics in a time-series format. Add a mountable dashboard API that serves aggregated analytics and historical trends. Changes span middleware, routing, endpoint lifecycle tracking, and add an `analytics/` module with storage, aggregation, and dashboard endpoints.

### W3: Implement automatic API client generation from endpoint definitions

Add a client code generator that produces typed Ruby, TypeScript, and Python API clients from Grape endpoint definitions. Extract route paths, HTTP methods, parameters, response types, and authentication requirements. Generate per-endpoint methods with parameter validation. Changes span DSL metadata extraction, route introspection, template rendering, and add a `codegen/` module.

### W4: Add multi-tenant API isolation with tenant-aware middleware

Implement tenant-aware request processing where each tenant gets isolated middleware stacks, rate limits, and configuration. Add `tenant` DSL for per-tenant endpoint customization, tenant resolution middleware, per-tenant error handling, and tenant-scoped helpers. Changes span middleware, routing, endpoint, DSL modules, configuration, and add a `tenancy/` module.

### W5: Implement API gateway features with upstream service proxying

Add API gateway capabilities: route requests to upstream services, aggregate responses from multiple services, apply circuit breakers, and transform request/response formats. Implement `proxy_to` DSL method, connection pooling for upstream services, response merging, and failure fallbacks. Changes span routing, endpoint, middleware, and add a `gateway/` module with proxy, circuit breaker, and aggregation components.

### W6: Add comprehensive API testing framework

Implement `Grape::Testing` with request factory helpers, response assertion matchers, endpoint isolation testing, middleware testing, parameter validation testing, and mock service dependencies. Support RSpec and Minitest integration. Changes span endpoint, middleware, validation, and add a `testing/` module with helpers, matchers, and fixtures.

### W7: Implement event-driven API with server-sent events and webhooks

Add SSE streaming support via `stream` DSL method with automatic client connection management. Implement a webhook system with subscription management, payload signing, delivery retries, and delivery status tracking. Changes span routing, endpoint, middleware, and add `streaming/` and `webhooks/` modules.

### W8: Add API versioning migration toolkit

Implement tools for migrating between API versions: request/response adapters that transform between version formats, automatic version compatibility testing, migration guide generation from endpoint diffs, and dual-version serving during migration periods. Changes span routing/versioning, middleware, endpoint, and add a `migration/` module.

### W9: Implement pluggable authentication framework with multiple strategies

Extend the existing `lib/grape/middleware/auth/` module (which currently supports only HTTP Basic and HTTP Digest via Rack strategies) to add a comprehensive pluggable auth framework. Add strategy classes for OAuth2 bearer tokens, JWT, API keys (header and query-string), and HMAC request signing under `lib/grape/middleware/auth/strategies/`. Add a unified DSL in `lib/grape/middleware/auth/dsl.rb` with `authenticate_with`, `require_scope`, and `rate_limit_by_auth_level` helpers. Implement token management and scope-based authorization modules. Add auth event logging via `ActiveSupport::Notifications`. Changes span `lib/grape/middleware/auth/dsl.rb`, `lib/grape/middleware/auth/strategies.rb`, `lib/grape/middleware/auth/base.rb`, new strategy files, and `lib/grape/endpoint.rb` for auth context access.

### W10: Add gRPC service generation from Grape API definitions

Implement gRPC service definitions generated from Grape endpoint declarations. Translate REST routes to gRPC methods, parameter schemas to protobuf messages, and response types to gRPC response messages. Support dual serving (REST + gRPC) from the same API definition. Changes span routing, endpoint, serialization, and add a `grpc/` module with protobuf generation and gRPC server integration.

### W11: Overhaul project documentation and release process

Restructure all project documentation: consolidate `UPGRADING.md` with version-specific migration checklists; update `RELEASING.md` with automated release workflow referencing `.github/workflows/edge.yml`; rewrite `CONTRIBUTING.md` with development setup using `docker-compose.yml`, RuboCop compliance guidelines from `.rubocop.yml`, and Danger PR review expectations from `Dangerfile`; update `CHANGELOG.md` format to follow Keep a Changelog specification; add security vulnerability reporting process to `SECURITY.md`; and update `README.md` with badges, quickstart guide, and links to all documentation files. Changes span `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `RELEASING.md`, `UPGRADING.md`, `SECURITY.md`, `grape.gemspec` (metadata links), and `.github/workflows/`.
