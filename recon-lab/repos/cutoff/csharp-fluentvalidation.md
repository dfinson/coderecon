# FluentValidation/FluentValidation

| Field | Value |
|-------|-------|
| **URL** | https://github.com/FluentValidation/FluentValidation |
| **License** | Apache-2.0 |
| **Language** | C# |
| **Scale** | Medium |
| **Category** | Validation library |
| **Set** | Cutoff |
| **Commit** | `cc9917c3688d790f7a414b17d1e03ce337a4151c` |

## Why this repo

- **Well-structured**: Clean separation between the core validator
  framework (`AbstractValidator`, `Internal/`), built-in validators
  (`Validators/`), validation results (`Results/`), localisation
  (`Resources/`), and test helpers (`TestHelper/`). The fluent
  rule-builder API is layered via `DefaultValidatorExtensions`,
  `RuleBuilder`, and `RuleBase`.
- **Rich history**: 3K+ commits, 9K+ stars. The most widely used
  .NET validation library. Issues and PRs cover new validator types,
  async validation edge cases, cascade behaviour, and localisation.
- **Permissive**: Apache-2.0 license.

## Structure overview

```
src/FluentValidation/
├── AbstractValidator.cs                  # Base class — rule registration, Validate(), ValidateAsync()
├── DefaultValidatorExtensions.cs         # Extension methods — NotNull(), NotEmpty(), Length(), etc.
├── DefaultValidatorExtensions_Validate.cs# Validate/ValidateAndThrow extensions
├── DefaultValidatorOptions.cs            # Fluent chaining — WithMessage(), When(), Unless()
├── ValidatorOptions.cs                   # Global defaults — CascadeMode, LanguageManager
├── ValidationException.cs               # Thrown by ValidateAndThrow
├── Enums.cs                              # CascadeMode, ApplyConditionTo, Severity
├── Syntax.cs                             # IRuleBuilder, IRuleBuilderInitial interfaces
├── InlineValidator.cs                    # Lambda-based validator for quick use
├── AssemblyScanner.cs                    # Auto-discovery of validators via DI
├── Internal/
│   ├── RuleBase.cs                       # Core rule execution — components, conditions, cascade
│   ├── PropertyRule.cs                   # Single-property rule
│   ├── CollectionPropertyRule.cs         # ForEach / collection rules
│   ├── RuleBuilder.cs                    # Fluent builder that chains validators
│   ├── RuleComponent.cs                  # Wraps an IPropertyValidator + message + severity
│   ├── ConditionBuilder.cs              # When()/Unless() condition attachment
│   ├── ValidationStrategy.cs            # Selector-based validation (rulesets, properties)
│   ├── MessageFormatter.cs              # {PropertyName}, {PropertyValue} placeholder replacement
│   ├── PropertyChain.cs                 # Nested property path tracking
│   └── AccessorCache.cs                # Compiled expression cache
├── Validators/
│   ├── AbstractComparisonValidator.cs   # Base for GreaterThan, LessThan, etc.
│   ├── LengthValidator.cs              # MinimumLength / MaximumLength / Length
│   ├── EmailValidator.cs               # Email format validation
│   ├── RegularExpressionValidator.cs   # Matches() regex validator
│   ├── CreditCardValidator.cs          # Luhn algorithm
│   ├── EnumValidator.cs                # IsInEnum() validator
│   ├── PrecisionScaleValidator.cs      # ScalePrecision() for decimals
│   ├── PolymorphicValidator.cs         # Runtime type-switch validation
│   ├── ChildValidatorAdaptor.cs        # SetValidator() nested validation
│   └── RangeValidator.cs               # InclusiveBetween / ExclusiveBetween
├── Results/
│   ├── ValidationResult.cs             # Aggregated result with list of failures
│   └── ValidationFailure.cs            # Single failure — property, message, code, severity
├── Resources/
│   ├── LanguageManager.cs              # Pluggable error message localisation
│   └── ILanguageManager.cs             # Language manager interface
└── TestHelper/
    ├── ValidatorTestExtensions.cs      # ShouldHaveValidationErrorFor test DSL
    └── TestValidationResult.cs         # Assertion-friendly validation result wrapper
```

## Scale indicators

- ~60 C# source files (library only)
- ~13K lines of code
- Moderate depth (2 levels — Internal/, Validators/, etc.)
- Minimal dependencies (targets netstandard2.0/net8.0)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Add closest-match suggestion to EnumValidator error message

The `EnumValidator` in `Validators/EnumValidator.cs` rejects values not
defined in the target enum but only reports the invalid value. Add
string-distance matching so the error message includes a "Did you mean
'Active'?" suggestion when the string representation of the value is
close to a valid enum member name. This requires modifying the
`IsValid()` override and the default error message template in
`LanguageManager`. Also update `docs/built-in-validators.md` to
document the new suggestion feature, including an example of the
updated error message format and configuration options for disabling
suggestions.

### N2: Fix PrecisionScaleValidator not handling negative decimal values symmetrically

The `PrecisionScaleValidator` in `Validators/PrecisionScaleValidator.cs`
calculates precision and scale using `Math.Truncate` and string-length
arithmetic. For negative decimals very close to zero (e.g., `-0.001`),
the truncation logic can produce an off-by-one scale count because the
minus sign is included in the digit count. Fix the precision/scale
calculation to strip the sign before measuring digits.

### N3: Add WithErrorCode() dynamic overloads for instance-based error codes

The `DefaultValidatorOptions.cs` provides `WithErrorCode(string code)` to
set a static error code on a rule, but unlike `WithMessage()` there are no
overloads that accept a delegate to derive the code at validation time.
Add `WithErrorCode<T, TProperty>(Func<T, string> codeProvider)` and
`WithErrorCode<T, TProperty>(Func<T, TProperty, string> codeProvider)`
extension methods in `DefaultValidatorOptions.cs` that store the delegate
in `RuleComponent` and invoke it when building the `ValidationFailure`,
mirroring how `WithMessage(Func<T, string>)` works for error messages.

### N4: Add generic IsEnumName<TEnum>() convenience overload for string-to-enum validation

The `IsEnumName` extension in `DefaultValidatorExtensions.cs` validates a
string property against enum member names but requires the caller to pass
an explicit `Type` argument (e.g., `IsEnumName(typeof(MyEnum))`). There
is no generic overload that infers the enum type from a type parameter,
forcing unnecessary boilerplate. Add a generic
`IsEnumName<T, TEnum>(this IRuleBuilder<T, string> ruleBuilder, bool caseSensitive = true)`
overload in `DefaultValidatorExtensions.cs` that delegates to the existing
`StringEnumValidator<T>` implementation using `typeof(TEnum)`, providing a
cleaner call site: `RuleFor(x => x.Status).IsEnumName<MyModel, StatusEnum>()`.

### N5: Fix MessageFormatter not escaping braces in custom property values

The `MessageFormatter` in `Internal/MessageFormatter.cs` performs
placeholder replacement (e.g., `{PropertyName}`) using simple string
substitution. If a property value itself contains braces (e.g.,
`"{json}"`), the replacement can produce malformed output or throw. Fix
the formatter to escape literal braces in substituted values so they are
not interpreted as placeholders.

### N6: Add MinimumItems and MaximumItems validators for collection properties

The `CollectionPropertyRule` in `Internal/CollectionPropertyRule.cs` runs
per-element validation via `RuleForEach`, but there is no built-in
validator that checks the collection's count. Add `MinimumItems(int min)`
and `MaximumItems(int max)` extension methods in
`DefaultValidatorExtensions.cs` that attach a new `CollectionLengthValidator`
in `Validators/`, operating on `ICollection<T>` and checking `Count`.

### N7: Fix AccessorCache not evicting stale compiled expressions under memory pressure

The `AccessorCache` in `Internal/AccessorCache.cs` caches compiled
`Expression<Func<T, TProperty>>` accessors using a
`ConcurrentDictionary` but never evicts entries. For long-lived
applications that dynamically create validators for many types, this
can leak memory. Add a bounded eviction policy (e.g., LRU with a
configurable max size) to the cache.

### N8: Add URL validator with scheme and host constraints

There is no built-in URL validator. Add a `UrlValidator` in
`Validators/UrlValidator.cs` that validates string properties as
well-formed URIs using `Uri.TryCreate()`, with optional `AllowedSchemes`
(e.g., `["https"]`) and `RequireHost` parameters. Wire it with an
`IsUrl()` extension method in `DefaultValidatorExtensions.cs` and
register a default error message in `LanguageManager`.

### N9: Fix ValidationResult.ToString() not including severity level

The `ValidationResult` class in `Results/ValidationResult.cs` has a
`ToString()` method that joins error messages, but it does not include
the `Severity` level (Error, Warning, Info) of each `ValidationFailure`.
Fix `ToString()` to prefix each message with its severity when the
severity is not the default `Error`, e.g., `"[Warning] Name is too short"`.

### N10: Add WithSeverity() overloads for IRuleBuilderOptionsConditions

The `WithSeverity` extension methods in `DefaultValidatorOptions.cs`
target `IRuleBuilderOptions<T, TProperty>` only. Rules that use
`Must()` or `Custom()` return `IRuleBuilderOptionsConditions<T, TProperty>`,
leaving those rules unable to call `WithSeverity()` without an explicit
cast. Add mirroring `WithSeverity(Severity)`, `WithSeverity(Func<T, Severity>)`,
`WithSeverity(Func<T, TProperty, Severity>)`, and
`WithSeverity(Func<T, TProperty, ValidationContext<T>, Severity>)` overloads
on `IRuleBuilderOptionsConditions<T, TProperty>` in `DefaultValidatorOptions.cs`,
storing the severity provider in `RuleComponent` via the existing
`SeverityProvider` path.

### N11: Update FluentValidation.csproj packaging and ReadTheDocs configuration

The `FluentValidation.csproj` has `EnablePackageValidation` set twice
(duplicate property at lines 12 and 16). Remove the duplicate. Also
update `.readthedocs.yaml` to use Python 3.12 and Ubuntu 24.04 (currently
`ubuntu-22.04` and `python: "3.11"`), update `docs/requirements_rtd.txt`
to pin `sphinx>=7.0` and `sphinx-rtd-theme>=2.0` (currently
`sphinx==1.8.5` and `sphinx-rtd-theme==0.4.3`), update `docs/conf.py`
to set the `html_logo` path to `_static/logo.png`, and update
`nuget.config` to add the `dotnet-public` feed
(`https://pkgs.dev.azure.com/dnceng/public/_packaging/dotnet-public/nuget/v3/index.json`)
as a package source alongside the existing `nuget.org` entry.

## Medium

### M1: Implement ruleset inheritance for nested validators

When a parent validator uses `.SetValidator(new ChildValidator())` via
`ChildValidatorAdaptor` in `Validators/ChildValidatorAdaptor.cs`, the
child validator does not inherit the parent's active ruleset from
`ValidationStrategy`. Add ruleset propagation so that when a parent
validates with `.IncludeRuleSets("Create")`, the child validator also
filters to the `"Create"` ruleset. Requires changes to `ChildValidatorAdaptor`,
`ValidationStrategy`, and the `ValidationContext` threading in
`Internal/RuleBase.cs`.

### M2: Add When()/Unless()/WhenAsync()/UnlessAsync() conditions for RuleForEach collection rules

Rules built with `RuleForEach()` return `IRuleBuilderInitialCollection<T, TCollectionElement>`,
which has no condition methods — `When()`, `Unless()`, `WhenAsync()`, and
`UnlessAsync()` are defined only for `IRuleBuilderOptions<T, TProperty>` and
`IRuleBuilderOptionsConditions<T, TProperty>` in `DefaultValidatorOptions.cs`.
As a result, applying a rule-level condition to an entire `RuleForEach` block
is impossible without wrapping the whole rule in a child validator. Add
`When`, `Unless`, `WhenAsync`, and `UnlessAsync` extension overloads for
`IRuleBuilderInitialCollection<T, TCollectionElement>` in
`DefaultValidatorOptions.cs`, wiring them through `CollectionPropertyRule.ApplyCondition`
and `CollectionPropertyRule.ApplyAsyncCondition` in `Internal/CollectionPropertyRule.cs`,
and registering async conditions through `RuleBase`'s existing condition infrastructure.

### M3: Implement a FluentValidation source generator for compile-time rule registration

Add a Roslyn source generator that analyses `AbstractValidator<T>`
subclasses at compile-time and generates optimised `Validate()` methods
that avoid runtime expression compilation and reflection. Requires a
new `FluentValidation.SourceGenerator` project, analysis of `RuleFor()`
calls, code generation for each rule chain, and integration with the
existing `AbstractValidator` base class via partial methods. Also
add the new project to `FluentValidation.sln`, create a
`FluentValidation.SourceGenerator.csproj` with analyzer packaging
configuration, and update `src/Directory.Build.props` to include shared
compiler settings for the generator project.

### M4: Add differential validation to report only changed-field errors

Add a `ValidateChanges(T original, T modified)` method to
`AbstractValidator<T>` that compares old and new objects and only runs
rules for properties that differ. Requires a change-detection mechanism
using `PropertyRule` expression accessors, a modified `ValidationStrategy`
that selects rules by changed properties, and result filtering in
`ValidationResult`.

### M5: Implement composite validators with And/Or combinators

Add `And()` and `Or()` combinators to the fluent API so rules can be
combined: `RuleFor(x => x.Value).GreaterThan(0).Or().LessThan(-10)`.
Requires a new `CompositeRuleComponent` in `Internal/` that wraps
multiple `RuleComponent` instances, short-circuit evaluation logic in
`RuleBase`, aggregated error messages, and extension methods in
`DefaultValidatorOptions.cs`.

### M6: Add JSON Schema generation from validator rules

Implement `validator.ToJsonSchema()` that introspects the registered
rules and produces a JSON Schema document. Map built-in validators to
schema keywords: `NotEmpty` → `minLength: 1`, `InclusiveBetween` →
`minimum`/`maximum`, `Matches` → `pattern`, `EmailAddress` → `format:
email`. Requires traversal of `Rules` in `AbstractValidator`, type
mapping for each `IPropertyValidator`, and a JSON Schema builder that
handles nested validators via `ChildValidatorAdaptor`.

### M7: Implement a validation middleware for ASP.NET Core Minimal APIs

Add a `FluentValidation.MinimalApi` integration that automatically
validates request body parameters annotated with `[Validate]` using
registered validators from DI. Requires an endpoint filter factory,
validator resolution via `IValidatorFactory`, automatic 400 response
generation from `ValidationResult`, and integration with the existing
`FluentValidation.DependencyInjectionExtensions` project.

### M8: Add property-path-aware error grouping in ValidationResult

The `ValidationResult` in `Results/ValidationResult.cs` stores a flat
list of `ValidationFailure` objects. Add a `GroupByProperty()` method
that returns failures grouped by their `PropertyName` in a hierarchical
structure, handling nested property paths (e.g., `"Address.City"`)
by splitting on dots and building a tree. Also add `HasErrorsForProperty(string)`
and requires changes to `PropertyChain` path construction.

### M9: Implement cross-property comparison validators

Add `GreaterThanProperty(x => x.OtherProp)` and
`LessThanProperty(x => x.OtherProp)` extension methods that compare
the current property value against another property on the same object.
Requires new `CrossPropertyComparisonValidator` classes in `Validators/`,
expression-based accessor resolution via `AccessorCache`, placeholder
support in `MessageFormatter` for `{ComparisonProperty}`, and extension
wiring in `DefaultValidatorExtensions.cs`.

### M10: Add validation context scoping for unit-of-work patterns

Add `context.BeginScope("OrderLine[0]")` to `IValidationContext` that
pushes a scope onto the `PropertyChain`, automatically prefixing all
failure property names within the scope. Auto-dispose the scope via
`IDisposable`. Requires changes to `PropertyChain`, the `ValidationContext`
class, `RuleBase` path construction, and the `ChildValidatorAdaptor`
to use scopes instead of manual chain manipulation.

### M11: Update CI workflow and Sphinx documentation build pipeline

The `.github/workflows/ci.yml` workflow runs `./build.ps1` but does
not build or validate the documentation site. Add a `docs` job to
`.github/workflows/ci.yml` that runs `make html` using the
`docs/Makefile`, validates all internal links, and checks for Sphinx
warnings treated as errors. Update `docs/Makefile` to add a
`linkcheck` target that runs `sphinx-build -b linkcheck`. Update
`build.ps1` to include a `-Docs` switch that builds the Sphinx site
locally for contributor preview. Also update `ci.yml` to use
`actions/checkout@v4` and `actions/setup-dotnet@v4` (currently both
are pinned to `@v2`).

## Wide

### W1: Implement a rule metadata extraction and documentation system

Add `validator.DescribeRules()` that returns a structured metadata model
describing every rule: property name and type, validator type and
parameters (min/max for Length, pattern for Matches, etc.), conditions,
severity, error code, and custom messages. Use this model to generate
Markdown documentation, OpenAPI schema extensions, and client-side
validation rules. Changes span `AbstractValidator`, every built-in
validator in `Validators/`, `RuleBase`, `RuleComponent`, and a new
`Metadata/` namespace. Also generate a `docs/rule-reference.md` file
from the metadata model, update `docs/index.rst` to include the new
page in the table of contents, and update `Changelog.txt` with a
release note entry for the new metadata extraction feature.

### W2: Add full async pipeline with cancellation support

Currently `ValidateAsync` in `AbstractValidator` runs validators
sequentially. Implement configurable parallel async validation where
independent property rules run concurrently, with proper
`CancellationToken` propagation and cascade-mode respect. Support
`MaxDegreeOfParallelism` on the validator. Changes span `AbstractValidator`,
`RuleBase`, `RuleComponent`, `CollectionPropertyRule`, `ChildValidatorAdaptor`,
and the `ValidationContext` threading model.

### W3: Implement a validation pipeline with pre/post hooks and transforms

Add `PreValidate()`, `PostValidate()`, and `Transform()` lifecycle hooks
to `AbstractValidator`. Pre-hooks can modify the object before validation,
post-hooks can enrich results, and transforms can normalise values (e.g.,
trim strings) before rules run. Support ordering and async hooks.
Changes span `AbstractValidator`, `RuleBase` execution, the
`ValidationContext`, `DefaultValidatorExtensions`, and `InlineValidator`.

### W4: Add support for validating polymorphic type hierarchies

Extend the `PolymorphicValidator` in `Validators/PolymorphicValidator.cs`
to support deep inheritance chains with automatic validator resolution
via DI. When validating a collection of base-type objects, automatically
dispatch to the correct derived-type validator. Support fallback
validators, ambiguity detection, and integration with `AssemblyScanner`
for auto-registration. Changes span `PolymorphicValidator`,
`ChildValidatorAdaptor`, `CollectionPropertyRule`, `AssemblyScanner`,
and `IValidatorFactory`.

### W5: Implement localised validation with ICU message format support

Replace the simple `{PropertyName}` placeholder system in
`MessageFormatter` with full ICU MessageFormat support, enabling
pluralisation (`{Count, plural, one {# item} other {# items}}`),
gender-aware messages, and locale-specific number/date formatting.
Support lazy-loaded locale data and fallback chains. Changes span
`MessageFormatter`, `LanguageManager`, `RuleComponent`, all built-in
validators' default messages, and the `Resources/` localisation layer.

### W6: Add a visual rule graph and dependency analyser

Implement `validator.AnalyseDependencies()` that builds a directed graph
of rule dependencies: which properties depend on other properties via
`When()` conditions, cross-property comparisons, and nested validators.
Detect circular dependencies, unreachable rules, and redundant
conditions. Output as DOT format for visualisation. Changes span
`AbstractValidator`, `ConditionBuilder`, `RuleBase`, `ChildValidatorAdaptor`,
`PropertyRule`, and a new `Analysis/` namespace.

### W7: Implement a validation caching layer for expensive validators

Add `WithCache(TimeSpan ttl)` to the fluent API that caches validation
results for a property value, avoiding re-execution of expensive
validators (e.g., database lookups in async validators). Use a
configurable `IValidationCache` with in-memory and distributed
implementations. Changes span `RuleComponent`, `RuleBase`,
`DefaultValidatorOptions`, the async validation path, a new
`Caching/` namespace, and DI integration.

### W8: Implement a testing framework for validator coverage analysis

Extend `TestHelper/` with a coverage analyser that tracks which rules
and branches (When/Unless conditions) were exercised during testing.
Report uncovered rules, untested condition branches, and missing severity
levels. Add `ValidatorCoverageCollector` that instruments `RuleBase`
execution, integrates with `ValidatorTestExtensions`, and produces
coverage reports in JSON and HTML.

### W9: Add real-time validation streaming for interactive forms

Implement `ValidateStream(IObservable<PropertyChange<T>>)` that accepts
a stream of property changes and emits `ValidationResult` updates
incrementally. Only re-evaluate rules affected by the changed property.
Support debouncing, dependency tracking, and cancellation of in-flight
async validations. Changes span `AbstractValidator`, `RuleBase`,
`PropertyRule`, the `ValidationStrategy`, and new reactive types in
a `Streaming/` namespace.

### W10: Implement a validator composition and reuse framework

Add `Include(IValidator<T>)` composition with conflict detection, rule
merging, and override semantics. When two included validators define
rules for the same property, support `Replace`, `Append`, and `Prepend`
merge strategies. Add a `ValidatorBuilder<T>` fluent API for composing
validators from reusable rule fragments. Changes span `AbstractValidator`,
`IncludeRule` in `Internal/`, `RuleBase`, `PropertyRule`, the fluent
extensions, and the DI registration in `AssemblyScanner`.

### W11: Restructure documentation site and project upgrade guides

The `docs/` directory contains individual upgrade guides
(`upgrading-to-8.md` through `upgrading-to-12.md`), built-in validator
docs, and Sphinx configuration across `conf.py`, `index.rst`,
`Makefile`, and `make.bat`. Consolidate the upgrade guides into a
versioned `docs/migration/` subdirectory, update `docs/index.rst` to
reference the new paths, and add a `docs/CHANGELOG.md` generated from
`Changelog.txt` with proper Markdown heading structure. Update
`.readthedocs.yaml` to enable PDF and EPUB output formats, add a
`docs/api.rst` page that auto-documents the public API using
`sphinx-csharp`, and update `FluentValidation.sln` to include the
`docs/` directory as a solution folder for IDE navigation. Also update
the `.github/README.md` and `.github/CONTRIBUTING.md` with links to
the restructured documentation site.
