# sinatra/sinatra

| Field | Value |
|-------|-------|
| **URL** | https://github.com/sinatra/sinatra |
| **License** | MIT |
| **Language** | Ruby |
| **Scale** | Medium |
| **Category** | Web DSL / micro-framework |
| **Set** | eval |
| **Commit** | `f891dd2b6f4911e356600efe6c3b82af97d262c6` |

## Why this repo

- **Monorepo with three gems**: Core sinatra, sinatra-contrib (extensions), rack-protection (security middleware)
- **Dense DSL core**: Routing, templates, filters, error handlers, helpers, settings, and middleware all defined in a single 2100-line `base.rb`
- **Rich middleware layer**: 15+ rack-protection strategies (CSRF, session hijacking, IP spoofing, XSS, etc.)

## Structure overview

```
sinatra/
├── lib/sinatra/
│   ├── base.rb              # Core: Request, Response, Helpers, Templates, Base, Application
│   ├── main.rb              # Top-level DSL (delegates to Sinatra::Application)
│   ├── indifferent_hash.rb  # Hash subclass with indifferent access
│   ├── show_exceptions.rb   # Debug exception page
│   ├── middleware/logger.rb  # Rack-compatible logger middleware
│   └── version.rb
├── rack-protection/lib/rack/protection/
│   ├── base.rb              # Base protection middleware
│   ├── authenticity_token.rb # CSRF token validation
│   ├── content_security_policy.rb
│   ├── cookie_tossing.rb
│   ├── escaped_params.rb
│   ├── form_token.rb
│   ├── frame_options.rb
│   ├── host_authorization.rb
│   ├── http_origin.rb
│   ├── ip_spoofing.rb
│   ├── json_csrf.rb
│   ├── path_traversal.rb
│   ├── referrer_policy.rb
│   ├── remote_referrer.rb / remote_token.rb
│   ├── session_hijacking.rb
│   ├── strict_transport.rb
│   └── xss_header.rb
└── sinatra-contrib/lib/sinatra/
    ├── namespace.rb          # URL namespace scoping
    ├── reloader.rb           # Code reloading in development
    ├── streaming.rb          # EventSource / streaming responses
    ├── json.rb               # JSON response helper
    ├── respond_with.rb       # Content negotiation
    ├── cookies.rb            # Cookie jar helper
    ├── content_for.rb        # Template content blocks
    ├── config_file.rb        # YAML config loader
    ├── capture.rb            # Template capture helper
    ├── multi_route.rb        # Multiple route patterns
    ├── required_params.rb    # Parameter validation
    ├── custom_logger.rb      # Logger injection
    ├── link_header.rb        # Link header helper
    ├── webdav.rb             # WebDAV verb support
    └── extension.rb          # Extension registration DSL
```

## Scale indicators

- ~150 Ruby source files across all three gems
- ~7 600 lines of library code
- 2 100-line core file (`base.rb`) with DSL, routing, templates, filters, and settings
- 15+ independent rack-protection middleware strategies
- 20+ sinatra-contrib extensions

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `IndifferentHash#filter` not returning an `IndifferentHash`

In `lib/sinatra/indifferent_hash.rb`, the class overrides `select` to return a `Sinatra::IndifferentHash`, but does not override `filter` (the Ruby 2.6+ alias for `select`). Because `filter` is a C-level method that bypasses the Ruby-level `select` override, calling `params.filter { |k, v| v }` returns a plain `Hash`. Downstream code that calls `params.filter { ... }.key?(:foo)` with symbol keys silently fails because the returned hash is no longer indifferent. The fix is to define `filter` as an explicit alias for the overridden `select` method in `IndifferentHash`.

### N2: Fix `Request#preferred_type` not respecting `q=0` rejection in `Accept` headers

In `base.rb`, `preferred_type` loops over `accept` entries and delegates matching to `MimeTypeEntry#accepts?`, which never checks the `q` value. As a result, `Accept: text/html;q=0` (an explicit client rejection) still causes `preferred_type("text/html")` to return `"text/html"` instead of treating it as unacceptable. The fix should filter or skip `AcceptEntry` instances with `q == 0.0` inside `preferred_type` before attempting type matching, so clients can explicitly reject content types.

### N3: Fix `Response#finish` raising `NoMethodError` when body contains non-String elements

In `base.rb`, `Response#finish` calculates `content-length` with `body.map(&:bytesize).reduce(0, :+)`. If any element in the body array does not respond to `bytesize` (e.g., an Integer, Symbol, or other non-String object set via `body [some_object]`), a `NoMethodError` is raised instead of a meaningful error. The calculation should guard against non-String elements by using `body.reduce(0) { |sum, s| sum + s.to_s.bytesize }` or a similar safe accumulation, and the `calculate_content_length?` helper in `base.rb` should remain the single gate for this logic.

### N4: Fix `Rack::Protection::AuthenticityToken` not rotating tokens on session regeneration

In `rack-protection/lib/rack/protection/authenticity_token.rb`, the CSRF token is derived from `session[:csrf]` (stored under `options[:key]`). When the session ID is regenerated (e.g., after login), the old session hash is copied to the new session, so the CSRF token remains valid across the regeneration boundary. Applications have no public API to invalidate the old token. The fix is to add a `self.rotate_token!(session, options = {})` class method that deletes the master CSRF key from the session hash (forcing a new token to be generated on the next request), and to call this helper from an `after_call` hook or expose it for app-level use. Changes are confined to `authenticity_token.rb`.

### N5: Fix `Rack::Protection::HostAuthorization` not normalising port numbers in permitted hosts

In `rack-protection/lib/rack/protection/host_authorization.rb`, `HostAuthorization#initialize` stores permitted host strings verbatim (e.g., `"example.com:80"`) in `@permitted_hosts`, while `accepts?` compares using `extract_host` which strips any port via `PORT_REGEXP`. This means `permitted_hosts: ["example.com:80"]` never matches a request with `Host: example.com` (default port omitted), because `"example.com"` is not included in `@permitted_hosts`. The fix should strip port numbers from string entries during `initialize` so the stored values always match the port-stripped comparison values.

### N6: Fix `Rack::Protection::PathTraversal` not decoding percent-encoded sequences before checking

In `rack-protection/lib/rack/protection/path_traversal.rb`, the middleware checks for `..` in the request path. Attackers can bypass this by double-encoding (`%252e%252e`). The middleware should decode the path fully before checking for traversal sequences.

### N7: Fix `Base.process_route` silently dropping route captures named `:ignore`

In `base.rb`, `process_route` calls `params.delete('ignore')` unconditionally before merging Mustermann params into `@params`. This means any route that uses `:ignore` as a named parameter (e.g., `get '/users/:ignore' do ... end`) will have that parameter silently deleted: `params[:ignore]` will always be `nil`. The fix should remove the hardcoded delete and instead handle the internal `ignore` sentinel differently (e.g., store it under a namespace-prefixed key, or use Mustermann's `ignore` option rather than post-processing).

### N8: Fix `ShowExceptions` HTML template not escaping `env['SCRIPT_NAME']`

In `lib/sinatra/show_exceptions.rb`, the `TEMPLATE` ERB template uses `<%=h ... %>` (which calls `Rack::Utils.escape_html`) for nearly every interpolated value — including `exception.message`, frame filenames, and request data. However, the `src` attribute of the error image tag interpolates `env['SCRIPT_NAME']` without the `h` escaping helper: `<img src="<%= env['SCRIPT_NAME'] %>/__sinatra__/500.png" ...>`. A malicious or misconfigured proxy can inject arbitrary values into `SCRIPT_NAME`, making this an XSS vector in development mode. The fix is to apply `<%=h env['SCRIPT_NAME'] %>` consistently with the rest of the template.

### N9: Fix `Sinatra::Cookies` helper not setting `SameSite` attribute

In `sinatra-contrib/lib/sinatra/cookies.rb`, cookies set via the `cookies` helper do not include the `SameSite` attribute. Modern browsers default to `Lax`, but the helper should explicitly set `SameSite=Lax` (or a configurable value) to match the application's CSRF protection expectations from rack-protection.

### N10: Fix `before` filter halt not preventing `after` filters from running

In `base.rb`, when a `before` filter calls `halt`, the `after` filters in `dispatch!` still execute because the `catch(:halt)` in `invoke` unwinds to `call!` which then runs `after_filter!`. After-filters should be skipped when the response was halted from a before-filter.

### N11: Fix `CHANGELOG.md` not attributing security fixes to CVE identifiers

The `CHANGELOG.md` lists security fixes as regular bug fixes without linking to CVE identifiers or the `SECURITY.md` advisory process. Add CVE cross-references to existing security-related changelog entries, establish a changelog format convention in `CONTRIBUTING.md`, and update `RELEASING.md` with a security release checklist that includes CVE attribution and `VERSION` file bump procedures.

## Medium

### M1: Add generic request-header condition helper for route matching

Sinatra's `compile!` method already processes route options via `options.each_pair { |option, args| send(option, *args) }`, and built-in helpers (`user_agent`, `provides`, `host_name`) work as route conditions today. However, there is no generic `request_header` helper for matching arbitrary HTTP headers not covered by the built-in conditions. Add a `request_header(name, pattern)` condition method to the DSL (e.g., `get '/api', request_header(:x_api_version, /v2/) do ... end`) that registers a condition proc checking `request.env["HTTP_#{name.upcase}"]` against the given pattern. Changes span `Base` (new `request_header` condition method), `compile!` (no changes needed — it already invokes option methods), and documentation in `README.md`.

### M2: Implement rate limiting middleware for rack-protection

Add `Rack::Protection::RateLimiter` that throttles requests per IP using a sliding-window algorithm. Store counters in Rack session or a configurable backend. Changes span a new `rate_limiter.rb` in `rack-protection/lib/rack/protection/`, `protection.rb` registration, and configuration in `base.rb` default middleware.

### M3: Add streaming Server-Sent Events with heartbeat and reconnection

Extend `sinatra-contrib/lib/sinatra/streaming.rb` to support SSE with `event:`, `id:`, and `retry:` fields. Add automatic heartbeat comments to keep connections alive and client reconnection via `Last-Event-ID` header. Changes span `streaming.rb` (new `EventStream` class), `Helpers` in `base.rb` (new `event_stream` method), and response header handling.

### M4: Implement route versioning through Accept header

Add `get '/users', version: 2 do ... end` that matches when the `Accept` header contains `application/vnd.app.v2+json`. Requires changes to `Base.route` (version metadata), `Base.route!` (accept-header version extraction), and `Helpers` (version accessor), and `respond_with.rb` for content negotiation integration.

### M5: Add request body validation with schema support

Implement `post '/items', schema: ItemSchema do ... end` that validates the parsed request body against a schema object before invoking the route block. Requires changes to `Base.route` (schema storage), `Base.process_route` (validation step), error handling integration in `Base.dispatch!`, and a new `sinatra/validation.rb` contrib extension.

### M6: Implement session encryption for rack-protection

Add `Rack::Protection::EncryptedSession` middleware that encrypts session data at rest using AES-256-GCM. Changes span a new `encrypted_session.rb` in `rack-protection/lib/rack/protection/`, key derivation from the app secret in `base.rb`, and integration with session middleware. Update `README.md` with session encryption configuration instructions and add a security hardening section to `SECURITY.md`.

### M7: Add template caching with dependency invalidation for the reloader

`sinatra-contrib/lib/sinatra/reloader.rb` reloads Ruby source files but does not invalidate cached templates that depend on modified helpers. Implement dependency tracking between templates and their helper modules. Changes span `reloader.rb` (file-dependency graph), `Templates` module in `base.rb` (cache invalidation hook), and `engine_tracking.rb`.

### M8: Implement multi-route error handling with typed exception matchers

Extend `error` blocks to support exception class patterns: `error DatabaseError, status: 503 do ... end` with priority ordering by exception class specificity. Requires changes to `Base.error` (store exception class → handler mapping), `Base.dispatch!` (match by `Exception#is_a?` hierarchy), and `Helpers#error` (status code integration).

### M9: Add namespace-scoped middleware support

Extend `sinatra-contrib/lib/sinatra/namespace.rb` to allow `namespace '/admin' do use AdminAuth; ... end` where middleware only applies to routes within that namespace. Requires changes to `namespace.rb` (middleware storage), `Base.route!` (conditional middleware insertion), and `Base.build` (middleware stack scoping).

### M10: Implement content security policy nonce injection

Extend `rack-protection/lib/rack/protection/content_security_policy.rb` to auto-generate a per-request nonce, inject it into the CSP `script-src` directive, and expose it via `env['rack.csp_nonce']` for use in templates. Changes span `content_security_policy.rb` (nonce generation and header rewriting), `Helpers` in `base.rb` (nonce accessor), and `show_exceptions.rb` (use nonce for inline scripts).

### M11: Modernize CI test matrix and monorepo tooling

Extend `.github/workflows/test.yml` to add a macOS platform to the test matrix (currently only `ubuntu-latest` is used). Update `.rubocop.yml` to replace the blanket `Exclude` of `rack-protection/**/*` and `sinatra-contrib/**/*` with per-gem `inherit_from` entries using gem-specific `.rubocop.yml` files in each gem directory. Move the existing `CODEOWNERS` file from `.github/workflows/CODEOWNERS` (an unrecognised path) to `.github/CODEOWNERS` so GitHub correctly enforces code ownership. Changes span `.github/workflows/test.yml`, `.rubocop.yml`, `.github/CODEOWNERS`, `rack-protection/.rubocop.yml`, and `sinatra-contrib/.rubocop.yml`.

## Wide

### W1: Implement WebSocket support with route DSL integration

Add `websocket '/chat' do |ws| ... end` to the routing DSL with full lifecycle callbacks (`on_open`, `on_message`, `on_close`). Requires changes to `base.rb` (new route verb, upgrade detection in `route!`), a new `sinatra/websocket.rb` extension, rack hijacking integration, `streaming.rb` adaptation for bi-directional IO, and `namespace.rb` support.

### W2: Add background job processing framework

Implement `perform_later :send_email, user_id: 1` with an in-process thread pool and optional Redis-backed persistence. Changes span a new `sinatra/jobs.rb` contrib extension (job registration, queue, worker pool), `base.rb` (helper method, settings), `reloader.rb` (job class reloading), error handling integration in `dispatch!`, and a `sinatra/jobs/dashboard.rb` status route.

### W3: Implement modular application composition with mount DSL

Add `mount AdminApp, at: '/admin'` that mounts sub-applications with isolated middleware stacks and settings inheritance. Changes span `base.rb` (mount registration, routing delegation in `route!`, settings cascade), `main.rb` (top-level mount support), `namespace.rb` (interaction with mounted apps), rack-protection (per-mount security policies), and `reloader.rb` (sub-app reloading).

### W4: Add GraphQL endpoint support with schema DSL

Implement `graphql '/graphql', schema: AppSchema do ... end` integrating schema definition, query execution, and subscription support. Changes span a new `sinatra/graphql.rb` contrib extension (schema DSL, execution), `base.rb` routing (GraphQL verb handling), `streaming.rb` (subscription streaming), `respond_with.rb` (GraphQL content type), and error handling integration.

### W5: Implement request tracing with OpenTelemetry integration

Add distributed tracing across the request lifecycle: middleware → filters → route execution → template rendering → streaming. Changes span `base.rb` (span creation in `call!`, `route!`, `render`), rack-protection middlewares (trace context propagation), `streaming.rb` (span lifecycle for long-running streams), `namespace.rb` (scoped span naming), and a new `sinatra/tracing.rb` contrib extension.

### W6: Add comprehensive test helpers framework

Implement `Sinatra::TestHelpers` with mock session management, request factory methods, response assertions, streaming test support, and time-travel for cookie expiration testing. Changes span a new `test_helpers.rb` redesign in sinatra-contrib (request builder, assertion helpers), `base.rb` (test mode hooks), `cookies.rb` (test accessors), `streaming.rb` (synchronous test mode), and rack-protection (test bypass mode).

### W7: Implement hot code reloading with zero-downtime for production

Extend `sinatra-contrib/lib/sinatra/reloader.rb` to support production-grade reloading: fork-and-replace worker model, connection draining, middleware stack rebuild, template cache invalidation, and route table swap. Changes span `reloader.rb` (worker management), `base.rb` (atomic route table swap, middleware rebuild), `main.rb` (signal handling), rack-protection (session migration), and `streaming.rb` (connection handoff).

### W8: Add internationalization (i18n) framework

Implement `t(:greeting, name: user.name)` with locale detection from Accept-Language, URL prefix, and session. Changes span a new `sinatra/i18n.rb` contrib extension (translation loading, locale resolution, pluralization), `base.rb` (locale-aware routing, helper registration), `respond_with.rb` (locale-based content negotiation), `config_file.rb` (translation file loading), and template rendering integration.

### W9: Implement API documentation generation from route metadata

Add `desc 'List users'; param :page, Integer, default: 1` annotations that generate OpenAPI specs. Changes span `base.rb` (route metadata storage in `route`, `desc` DSL method, `param` DSL method), a new `sinatra/api_doc.rb` contrib extension (OpenAPI schema generation), `respond_with.rb` (documentation endpoint), `namespace.rb` (namespace-level metadata), and `json.rb` (schema serialization).

### W10: Add multi-tenant support with per-tenant configuration isolation

Implement `tenant { |req| req.host.split('.').first }` with isolated settings, middleware stacks, sessions, and CSRF tokens per tenant. Changes span `base.rb` (tenant resolution in `call!`, settings isolation, middleware scoping), `main.rb` (top-level tenant DSL), rack-protection (per-tenant CSRF tokens in `authenticity_token.rb`, per-tenant sessions in `session_hijacking.rb`), `cookies.rb` (tenant-scoped cookie jar), and `namespace.rb` (tenant-aware namespaces).

### W11: Create unified contributor documentation and maintenance guides

Consolidate and overhaul the project's extensive non-code documentation: unify `CONTRIBUTING.md`, `MAINTENANCE.md`, and `RELEASING.md` into a coherent contributor handbook with cross-references; update `AUTHORS.md` with a contribution attribution process; modernize `CODE_OF_CONDUCT.md` to the latest Contributor Covenant; add development setup instructions to `CONTRIBUTING.md` covering all three gems; update `CHANGELOG.md` format to follow Keep a Changelog with per-gem sections for `sinatra`, `sinatra-contrib`, and `rack-protection`; document the security advisory process in `SECURITY.md` with responsible disclosure timeline; and update `README.md` with badges, monorepo structure overview, and links to all documentation files. Changes span `CONTRIBUTING.md`, `MAINTENANCE.md`, `RELEASING.md`, `AUTHORS.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `SECURITY.md`, `README.md`, and `VERSION`.
