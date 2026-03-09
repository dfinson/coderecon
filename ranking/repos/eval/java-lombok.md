# projectlombok/lombok

| Field | Value |
|-------|-------|
| **URL** | https://github.com/projectlombok/lombok |
| **License** | MIT |
| **Language** | Java |
| **Scale** | Large |
| **Category** | Boilerplate reduction (annotation processor / compiler plugin) |
| **Set** | eval |
| **Commit** | `1edca70420ff29d68e3a7da0c3def499609dac84` |

## Why this repo

- **Dual compiler integration**: Separate handler implementations for javac and Eclipse/ECJ compiler pipelines
- **AST manipulation**: Direct Java AST construction (method generation, field insertion, import management)
- **Configuration system**: Cascading config files, annotation-level options, cross-cutting feature flags

## Structure overview

```
lombok/src/
├── core/lombok/
│   ├── *.java                    # Annotation definitions: @Getter, @Setter, @Builder, @Data, @Value, etc.
│   ├── ConfigurationKeys.java    # All lombok configuration keys
│   ├── core/
│   │   ├── AST.java              # Abstract syntax tree base
│   │   ├── LombokNode.java       # Node abstraction over compiler-specific AST
│   │   ├── AnnotationValues.java  # Annotation parameter extraction
│   │   ├── AnnotationProcessor.java # Entry point for javac annotation processing
│   │   ├── LombokConfiguration.java # Cascading config resolution
│   │   └── TypeLibrary.java       # Type name normalization
│   ├── javac/
│   │   ├── JavacAST.java          # javac-specific AST wrapper
│   │   ├── JavacNode.java         # javac-specific node
│   │   ├── JavacAnnotationHandler.java # Handler base class for javac
│   │   ├── JavacTransformer.java  # AST transformation pipeline
│   │   ├── HandlerLibrary.java    # Handler discovery and dispatch
│   │   └── handlers/
│   │       ├── HandleGetter.java      # @Getter code generation
│   │       ├── HandleSetter.java      # @Setter code generation
│   │       ├── HandleBuilder.java     # @Builder code generation
│   │       ├── HandleSuperBuilder.java # @SuperBuilder code generation
│   │       ├── HandleConstructor.java  # @NoArgsConstructor etc.
│   │       ├── HandleEqualsAndHashCode.java # @EqualsAndHashCode
│   │       ├── HandleToString.java     # @ToString
│   │       ├── HandleData.java         # @Data composite
│   │       ├── HandleValue.java        # @Value composite
│   │       ├── HandleWith.java         # @With wither methods
│   │       ├── HandleLog.java          # @Slf4j, @Log, etc.
│   │       ├── HandleSneakyThrows.java # @SneakyThrows
│   │       ├── HandleCleanup.java      # @Cleanup
│   │       ├── HandleNonNull.java      # @NonNull null checks
│   │       ├── HandleSynchronized.java # @Synchronized
│   │       ├── HandleLocked.java       # @Locked (ReentrantLock)
│   │       ├── HandleDelegate.java     # @Delegate
│   │       ├── HandleVal.java          # val/var type inference
│   │       ├── JavacHandlerUtil.java   # Shared AST building utilities
│   │       └── JavacSingularsRecipes.java # @Singular collection builders
│   └── eclipse/
│       ├── handlers/              # Eclipse/ECJ handler mirrors
│       └── EclipseAST.java        # Eclipse-specific AST wrapper
├── delombok/                      # Delombok: reverse-transform to plain Java
├── eclipseAgent/                  # Eclipse IDE agent for live processing
└── installer/                     # IDE plugin installer
```

## Scale indicators

- ~405 Java source files (excluding stubs)
- ~73K lines of code
- Dual javac + Eclipse handler implementations
- Configuration system with ~100 configuration keys
- Singularizer recipes for Guava and java.util collections

---

## Tasks

33 tasks (11 narrow, 11 medium, 11 wide).

## Narrow

### N1: Fix `@Builder` not respecting `@Accessors(prefix)` on fields

When a class uses both `@Builder` and `@Accessors(prefix = "m")` on fields like `mName`, the builder generates `mName(String)` setter methods instead of stripping the prefix to produce `name(String)`. The `HandleBuilder` reads the field name directly without consulting `HandleAccessors` prefix resolution.

### N2: Add warning for `@ToString.Include` on static fields

When `@ToString.Exclude` is placed on a static field, `InclusionExclusionUtils.handleIncludeExcludeMarking` correctly emits a warning: "The @Exclude annotation is not needed; static fields aren't included anyway." However, when `@ToString.Include` is placed on a static field, the field is silently included in `toString()` output without any diagnostic — the include check at line 163 runs before the static filter at line 184, so the static field bypasses the filter and is added to members. Add a warning in the `markInclude` branch of `handleIncludeExcludeMarking` when the annotated member is a static field, for symmetry with the existing `@Exclude` warning on static fields.

### N3: Add specific error for `@Getter(lazy=true)` on a `volatile` field

`@Getter(lazy=true)` generates a double-checked locking pattern using a private `AtomicReference` backing field and requires the annotated field to be `private final`. `HandleGetter` at line 179-180 emits the generic error "'lazy' requires the field to be private and final" when this condition is not met. Since `volatile` and `final` are mutually exclusive Java modifiers, a `volatile` field with `lazy=true` triggers this generic message rather than a clear explanation. Add a dedicated check for the `volatile` modifier before the private/final check — similar to the existing check at line 184 that says "'lazy' is not supported on transient fields" — so that the error message explicitly states "'lazy' is not supported on volatile fields".

### N4: Fix `@NonNull` null check inserted after `super()`/`this()` when parameter is passed to constructor call

When `@NonNull` is on a constructor parameter that is also passed as an argument to a `super()` or `this()` call, `HandleNonNull.addNullCheckIfNeeded` places the null check **after** the delegating constructor call in the generated code. The loop (lines 200–208) collects constructor calls into `head` and re-prepends them before the null check, resulting in the order `[super/this-call, nullCheck, rest-of-body]`. This means the superclass or sibling constructor already executed with the potentially-null argument before the null check throws. `HandleNonNull` should detect when the `@NonNull` parameter appears in the delegating constructor's argument list and emit a warning that the null check cannot be enforced before the `super()`/`this()` call executes.

### N5: Add warning for `@EqualsAndHashCode.Include` on static fields

`@EqualsAndHashCode` skips static fields by default. When a static field is explicitly annotated with `@EqualsAndHashCode.Include`, `InclusionExclusionUtils.handleIncludeExcludeMarking` includes the field in equals/hashCode computation without any diagnostic — the include check at line 163 runs before the static filter at line 184, so the static field bypasses the filter. Including static (shared) state in equals/hashCode comparisons is almost always unintentional and produces semantically incorrect equality. Add a warning in the `markInclude` branch of `handleIncludeExcludeMarking` when `@EqualsAndHashCode.Include` is placed on a static field, for consistency with the existing warning behavior for `@Exclude` on fields that are redundantly annotated.

### N6: Add configuration key to set default `@Builder` method name

`@Builder` defaults to `builder()` as the static method name. Projects that use a different convention (e.g., `newBuilder()`) must set `builderMethodName` on every annotation. Add a configuration key `lombok.builder.defaultBuilderMethodName` that `HandleBuilder` reads as the default. Document the new key in `doc/changelog.markdown` and add it to the configuration keys listing in `README.md`.

### N7: Fix `@Cleanup` not handling exceptions from the `close()` call

`@Cleanup` generates a try/finally that calls `close()` in the finally block. If `close()` throws, it suppresses the original exception from the try block. The generated code should use `addSuppressed` when both the try block and close throw.

### N8: Fix `@SneakyThrows` not working on lambda expressions

`@SneakyThrows` can be placed on methods and constructors but is silently ignored when placed on a lambda expression. `HandleSneakyThrows` should either emit a warning that lambdas are unsupported or generate the try/catch wrapping inside the lambda body.

### N9: Fix `@ToString.Include(name="...")` silently ignored when `includeFieldNames` is `false`

`@ToString.Include` already has a `String name() default "";` attribute (in `ToString.java`) that customizes the label used for a field in `toString()` output. However, when `@ToString(includeFieldNames=false)` is set at the class level (or via the `lombok.toString.includeFieldNames=false` configuration key), `HandleToString` skips the `name` attribute and emits the field value without any label — the custom name is silently ignored. `HandleToString` should emit a warning when a `@ToString.Include` member has a non-empty `name` attribute but the effective `includeFieldNames` setting is `false`, since the user-supplied display name will have no effect on the generated output.

### N10: Fix `@Setter` on a `Boolean` wrapper field with `is` prefix generating wrong method name

For a field `Boolean isActive` (using the `java.lang.Boolean` wrapper type, not primitive `boolean`), `@Setter` generates `setIsActive(Boolean)` instead of `setActive(Boolean)`. `JavacHandlerUtil.isBoolean()` (line 812) only returns `true` when the field type is the primitive `boolean` (`varType.toString().equals("boolean")`), so the `is`-prefix stripping logic in `HandlerUtil.toAccessorName` does not apply to `Boolean` wrapper fields. Extend `isBoolean()` to also return `true` for `java.lang.Boolean`, or apply the `is`-prefix stripping unconditionally for fields whose names start with `is` followed by an uppercase letter, regardless of whether the type is primitive or boxed.

### N11: Fix `doc/changelog.markdown` entries missing cross-references to issue tracker

The `doc/changelog.markdown` file lists changes per release but individual entries lack links to the corresponding GitHub issue or PR. Add hyperlinked issue references to existing changelog entries, establish a changelog entry format in `CONTRIBUTING` guidelines (referenced from `.github/copilot-instructions.md`), and update `doc/PlannedExtensions.txt` to indicate which planned features have corresponding tracking issues.

## Medium

### M1: Implement `@Builder.Default` for collection fields with empty collection initialization

`@Builder.Default` requires an initializer expression. For collection fields, users typically want `new ArrayList<>()` as the default, but the builder's `build()` method calls the all-args constructor which passes the builder's field (which is null if not set). Implement special-case default handling for List/Set/Map fields that initializes to empty collections when not explicitly set via the builder. Changes span `HandleBuilder` and `HandleBuilderDefault`.

### M2: Add `@With` support for `@SuperBuilder` generated classes

`@With` generates wither methods that create a new instance using the all-args constructor. But for `@SuperBuilder` classes in an inheritance hierarchy, there is no all-args constructor—only the builder. Generate wither methods that use the builder pattern instead. Changes span `HandleWith`, `HandleSuperBuilder`, and `JavacHandlerUtil`.

### M3: Implement `@Getter`/`@Setter` support for Java records

Records are immutable by design and have component accessor methods. However, `@Getter` on a record is silently accepted without generating any methods (since records already have component accessors), and does not honor custom access levels or add `@Override` annotations to the existing accessor methods. Implement support so that `@Getter` on a record generates overriding accessor methods at the requested access level (e.g., `@Getter(AccessLevel.PROTECTED)`) or, when the same level as the default, at least emits an `@Override`-annotated pass-through. Changes span `HandleGetter`, `HandleSetter`, and `JavacHandlerUtil` (record detection).

### M4: Add Checker Framework `@CalledMethods` support to `@SuperBuilder`

`@Builder` generates `@CalledMethods` annotations on the builder's setter methods (in `HandleBuilder.createSetterMethodsForBuilder`, lines 715–731), enabling the Checker Framework's Object Construction Checker to verify that required fields are set before `build()` is called. `@SuperBuilder` does not generate any `@CalledMethods` annotations on its setter methods — `HandleSuperBuilder` has no equivalent logic — so the Object Construction Checker cannot verify completeness for `@SuperBuilder`-generated builders. Add `@CalledMethods` support to `HandleSuperBuilder`, mirroring the pattern from `HandleBuilder`. Changes span `HandleSuperBuilder` (setter method generation), the Eclipse equivalent, `CheckerFrameworkVersion` (verify existing constants), and new test cases.

### M5: Implement `@Builder` validation hooks

Add a `@Builder.Validate` annotation for a method that is called in `build()` before returning the constructed object. The validate method receives no arguments and can throw exceptions. Support validation on both `@Builder` and `@SuperBuilder`. Changes span `HandleBuilder`, `HandleSuperBuilder`, and annotation definitions.

### M6: Add `@Data` support for sealed class hierarchies

`@Data` generates `equals`, `hashCode`, `toString`, getters, setters, and required-args constructor. When applied to a sealed class with permitted subclasses, the generated `equals` should use `canEqual` to prevent symmetry violations across the hierarchy. Changes span `HandleData`, `HandleEqualsAndHashCode` (canEqual for sealed), and `HandleConstructor`.

### M7: Implement delombok output formatting configuration

The delombok tool generates plain Java but uses its own code formatting. Add configuration for indentation style (tabs/spaces, width), brace placement, and blank line insertion. Changes span `delombok/` (formatting output), configuration key registration, and `LombokConfiguration` parsing. Update `build.xml` with a delombok formatting integration test target and document formatting options in `doc/publishing.txt`.

### M8: Add global `lombok.log.fieldAccess` configuration key for `@Log` default access level

The `@Log` family of annotations (`@Slf4j`, `@Log4j2`, `@CommonsLog`, etc.) each have an `access` attribute that defaults to `AccessLevel.PRIVATE`. There is no global `lombok.config` key to override this default — the access level can only be set per-annotation. Add a `lombok.log.fieldAccess` configuration key to `ConfigurationKeys` that `HandleLog.processAnnotation` reads as the default when the annotation's `access` attribute is at its default value. This mirrors the pattern of existing global log-field keys (`lombok.log.fieldName`, `lombok.log.fieldIsStatic`). Changes span `ConfigurationKeys` (new key), `HandleLog` (reading the config key in `processAnnotation`), the Eclipse equivalent, and the annotation definition files (`Slf4j.java`, `Log4j2.java`, `CommonsLog.java`, etc.) to document the new default-override behavior.

### M9: Implement `@Singular` support for Guava `ImmutableMultimap` / `ImmutableListMultimap` / `ImmutableSetMultimap`

`@Builder` with `@Singular` supports Guava `ImmutableMap`, `ImmutableBiMap`, and `ImmutableSortedMap` via `JavacGuavaMapSingularizer`. Support for `ImmutableMultimap`, `ImmutableListMultimap`, and `ImmutableSetMultimap` is explicitly noted as a TODO in `JavacGuavaMapSingularizer` (line 30) and `EclipseGuavaMapSingularizer` (line 30). These Guava multi-value map types require a `put(K, V)` singular method that appends values to a multi-valued key. Add singularizer support for these three types in both `JavacGuavaMapSingularizer` and `EclipseGuavaMapSingularizer`, register them in `GuavaTypeMap`, and add corresponding test cases.

### M10: Fix `@EqualsAndHashCode` hash-code caching broken for objects whose `hashCode()` is `0`

`@EqualsAndHashCode(cacheStrategy = CacheStrategy.LAZY)` generates a `private transient int $hashCodeCache` field and uses `0` as the sentinel value meaning "not yet computed." The generated check `if (this.$hashCodeCache != 0) return this.$hashCodeCache;` means that if `hashCode()` genuinely evaluates to `0` for a particular instance, the cached value is never stored and the hash code is recomputed on every call. Fix this off-by-one sentinel issue by replacing the `int` cache field with either a nullable `Integer` reference (storing `null` for "not computed") or by adding a separate `boolean $hashCodeCacheSet` flag. Changes span `HandleEqualsAndHashCode` (javac, `createHashCode` and field generation), the Eclipse equivalent, and the `@EqualsAndHashCode` Javadoc to document the corrected behavior.

### M11: Modernize build system from Ant to Gradle

Migrate the primary build from `build.xml` and `buildScripts/*.ant.xml` to Gradle with Kotlin DSL. Convert the Ant targets (compile, test, javadoc, eclipse-p2, maven publish) to Gradle tasks. Update `.github/workflows/ant.yml` to use the new Gradle build, rename the workflow file to `build.yml`, configure the Gradle wrapper, and update `README.md` with the new build instructions. Preserve backward compatibility by keeping `build.xml` as a thin wrapper that delegates to Gradle during the transition period. Changes span `build.xml`, `buildScripts/`, `.github/workflows/`, `README.md`, and new `build.gradle.kts` and `settings.gradle.kts` files.

## Wide

### W1: Implement `@Memoize` annotation for method result caching

Add `@Memoize` that generates a caching wrapper around a method: compute the result once and return the cached value on subsequent calls. Support configurable cache key (arguments), thread safety mode (none/synchronized/concurrent), and TTL-based expiration. Changes span a new `HandleMemoize` handler (javac + eclipse), annotation definition, `ConfigurationKeys`, and `JavacHandlerUtil` (cache field generation).

### W2: Add comprehensive null-safety framework

Extend `@NonNull` into a full null-safety system: `@Nullable`, `@NonNullApi` (package-level default), `@NonNullFields` (class-level default). Generate null checks at API boundaries, including method parameters, return values, and field assignments. Changes span `HandleNonNull`, new annotations, `JavacHandlerUtil` (null check insertion), `HandleGetter`/`HandleSetter` (null contract propagation), `ConfigurationKeys`, and Eclipse handlers.

### W3: Implement `@Mapper` for type-safe object mapping

Add `@Mapper` that generates mapping methods between two types based on field name matching. Support custom field mappings, nested object mapping, collection mapping, and type conversion. Changes span a new `HandleMapper` handler (javac + eclipse), annotation definitions, `JavacHandlerUtil` (mapping code generation), type resolution utilities, and `ConfigurationKeys`.

### W4: Add incremental compilation support for javac

Lombok currently reprocesses all files on each compilation. Implement incremental processing by tracking which generated code depends on which source files. Cache handler output and invalidate only affected files. Changes span `AnnotationProcessor`, `JavacTransformer`, `HandlerLibrary`, a new `incremental/` package for dependency tracking, and configuration.

### W5: Implement `@Observe` annotation for observable properties

Add `@Observe` that generates property-change notification code: a `PropertyChangeSupport` field, `addPropertyChangeListener`/`removePropertyChangeListener` methods, and modified setters that fire change events. Changes span a new `HandleObserve` handler (javac + eclipse), annotation definitions, `HandleSetter` integration, `JavacHandlerUtil`, and `ConfigurationKeys`.

### W6: Add Java module system (JPMS) full compatibility layer

Lombok's agent-based approach conflicts with JPMS's strict encapsulation. Implement a pure annotation-processor mode that works without `--add-opens` flags. Changes span `AnnotationProcessor` (processor-only mode), `JavacTransformer` (JPMS-safe AST access), `HandlerLibrary` (fallback mechanisms), agent launcher, and installer (module configuration).

### W7: Implement `@Retry` annotation for method retry logic

Add `@Retry` that wraps method bodies in retry logic with configurable max attempts, backoff strategy (fixed/exponential), retryable exception types, and circuit breaker integration. Changes span a new `HandleRetry` handler (javac + eclipse), annotation definition with attributes, `JavacHandlerUtil` (try/catch generation with loop), `ConfigurationKeys`, and exception type resolution.

### W8: Add real-time IDE feedback for lombok-generated code

Implement a language-server-protocol (LSP) extension that shows generated code inline in IDEs beyond Eclipse. Provide hover info for lombok annotations showing generated methods, go-to-definition support for generated members, and completion suggestions. Changes span `eclipseAgent/` (hover provider), a new `lsp/` module, `HandlerLibrary` (code preview), delombok (on-the-fly), and installer (LSP registration).

### W9: Implement cross-annotation dependency validation

Add compile-time validation that detects conflicting annotation combinations: `@Data` + explicit `@Getter` with different access, `@Builder` + `@Value` on mutable fields, `@Synchronized` + `@Locked` on the same method. Changes span `HandlerLibrary` (validation phase), each handler (conflict detection), new annotation-level error reporting, `JavacHandlerUtil`, and `ConfigurationKeys` (suppression).

### W10: Add configurable code generation templates

Allow users to customize generated code via template files: toString format strings, equals comparison strategies, builder method naming patterns, constructor parameter ordering. Changes span `ConfigurationKeys` (template paths), `LombokConfiguration` (template loading), a new `templates/` package for template parsing, `HandleToString` / `HandleEqualsAndHashCode` / `HandleBuilder` / `HandleConstructor` (template-driven generation), and delombok.

### W11: Create comprehensive developer guide and extension authoring documentation

Build a structured developer documentation system from existing scattered files: consolidate `doc/changelog.markdown`, `doc/PlannedExtensions.txt`, `doc/experiences.txt`, and `doc/git-workflow.txt` into a cohesive developer guide; create an extension authoring tutorial explaining how to add new annotation handlers for both javac and Eclipse pipelines; document the build system in `buildScripts/` with architecture diagrams; update `README.md` with links to all documentation; add an `AUTHORS` file maintenance guide; and create a `CONTRIBUTING.md` at the repository root. Changes span `doc/`, `buildScripts/`, `README.md`, `AUTHORS`, `SECURITY.md`, and `.github/workflows/`.
