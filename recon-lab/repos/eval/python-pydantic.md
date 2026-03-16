# pydantic/pydantic

| Field | Value |
|-------|-------|
| **URL** | https://github.com/pydantic/pydantic |
| **License** | MIT |
| **Language** | Python |
| **Scale** | Medium-large |
| **Category** | Data validation library |
| **Set** | eval |
| **Commit** | `af85f64e9a88ac236e0252de63840f0059c83ac2` |

## Why this repo

- **Multi-subsystem**: Core validation, JSON Schema generation, serialization, type adapter, dataclasses, plugin system, experimental pipeline API
- **Well-structured**: Clean separation between public API (`main.py`, `fields.py`, `types.py`) and internals (`_internal/`)
- **Rich history**: 10K+ commits, foundational library for FastAPI and hundreds of Python projects

## Structure overview

```
pydantic/
├── main.py                  # BaseModel definition
├── fields.py                # Field(), FieldInfo
├── types.py                 # Constrained types, special types
├── config.py                # ConfigDict, model configuration
├── json_schema.py           # JSON Schema generation
├── type_adapter.py          # TypeAdapter for non-model validation
├── root_model.py            # RootModel for single-value models
├── dataclasses.py           # Pydantic-enhanced dataclasses
├── functional_validators.py # @field_validator, AfterValidator, etc.
├── functional_serializers.py# @field_serializer, PlainSerializer, etc.
├── aliases.py               # AliasPath, AliasChoices
├── networks.py              # URL, email types
├── validate_call_decorator.py # @validate_call
├── mypy.py                  # MyPy plugin
├── plugin/                  # Plugin system hooks
├── experimental/            # Pipeline API, arguments schema
├── deprecated/              # V1 compatibility shims
└── _internal/               # Schema generation, model construction internals
    ├── _generate_schema.py  # Core schema generation (~2900 LOC)
    ├── _model_construction.py # Metaclass, model building
    ├── _fields.py           # Field resolution logic
    ├── _discriminated_union.py # Tagged union handling
    ├── _decorators.py       # Validator/serializer decorator processing
    ├── _typing_extra.py     # Typing utilities
    └── _validate_call.py    # validate_call internals
```

## Scale indicators

- ~80 Python source files (excluding v1 compat)
- ~32K lines of source code
- Complex schema generation pipeline with pydantic-core Rust backend
- Extensive type system integration (mypy, pyright plugins)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix `model_copy(update=...)` not triggering validators on updated fields

When calling `model.model_copy(update={'field': value})`, the updated values bypass field validators. The `model_copy` method in `main.py` uses `__dict__` manipulation directly without re-validating the updated fields through the core schema.

### N2: Fix `TypeAdapter.validate_json()` not applying per-call `strict` override for `TypedDict` types with `__pydantic_config__`

When a `TypeAdapter` wraps a `TypedDict` subclass that has `__pydantic_config__ = ConfigDict(strict=False)`, calling `validate_json(..., strict=True)` does not raise a `ValidationError` for inputs that should be rejected in strict mode. The per-call `strict` parameter is not correctly overriding the type-level config stored in the validator built by `_init_core_attrs` in `type_adapter.py`. There is a known failing test (`test_validate_json_strict`) that documents this behavior.

### N3: Fix pydantic dataclass fields with `init=False` being silently skipped in assignment validation

When a pydantic dataclass uses `validate_assignment=True` in its config and has a field declared with `init=False`, setting that field after construction is silently accepted without validation. The `_internal/_fields.py` code skips `init=False` dataclass fields entirely when building the field map (with a TODO comment acknowledging the issue), so they are absent from the assignment-validation schema. The fix should include those fields in the schema used for assignment validation while still excluding them from `__init__`.

### N4: Add `include_if` parameter to `Field()` for conditional field inclusion during serialization

Fields can be conditionally excluded from serialization via the existing `exclude_if` parameter in `fields.py`, but there is no complementary `include_if` parameter. Add an `include_if` parameter to `Field()` (and the corresponding `FieldInfo` class) that accepts a callable: when the callable returns `False`, the field is omitted from `model_dump()` and `model_dump_json()` output. The `_internal/_generate_schema.py` schema generator should apply the callable as a serialization filter, symmetric with how `exclude_if` is handled.

### N5: Fix JSON Schema `$ref` generation for recursive models with `by_alias=True`

When generating JSON Schema with `mode='serialization'` and `by_alias=True` for recursive models, the `$ref` paths use the original field names instead of aliases. The `GenerateJsonSchema` class in `json_schema.py` doesn't apply alias transformation to `$defs` keys.

### N6: Fix `@field_validator` with `mode='wrap'` not receiving the correct handler type

When a wrap-mode field validator's handler is called, it receives the raw core validator function instead of a properly typed handler. The decorator processing in `_decorators.py` wraps the handler but doesn't match the `ValidatorFunctionWrapHandler` signature.

### N7: Fix `RootModel.__setattr__` raising `ValueError` instead of `AttributeError` for unknown attributes

When setting an attribute on a `RootModel` instance that is neither the `root` field nor a declared private attribute, pydantic raises a `ValueError` (e.g., `ValueError('other_attr')`) rather than an `AttributeError`. The Python convention — and what `hasattr()` checks — expects `AttributeError` for missing or read-only attributes. The `__setattr__` implementation in `root_model.py` should raise `AttributeError` for these cases to align with standard Python behavior and make `hasattr()` reliable on `RootModel` instances. There is a TODO comment in `tests/test_root_model.py` tracking this issue.

### N8: Add `transform` parameter to `PlainSerializer` for pre-serialization data transformation

`PlainSerializer` in `functional_serializers.py` replaces the entire serialization but lacks a way to transform data before the default serializer runs. Add a `transform` parameter that applies a function before default serialization.

### N9: Fix `GenerateJsonSchema.__get_pydantic_json_schema__` being called extra times for models with custom JSON schema hooks

When a `BaseModel` subclass defines `__get_pydantic_json_schema__` and that model appears both as a top-level type and as a type referenced through `Annotated` metadata containing custom schema annotations, the `GenerateJsonSchema` machinery in `json_schema.py` calls the hook an extra time during schema resolution. On the second encounter, the model's schema is retrieved from `__pydantic_core_schema__`, but the metadata JSON schema functions list is appended again, leading to duplicate handlers and potentially wrong schema output. There is a known xfail test (`test_get_pydantic_core_schema_calls`) documenting this behavior.

### N10: Fix `@validate_call` not supporting validation context for annotation-level validators

When a function decorated with `@validate_call` uses annotation-level validators such as `AfterValidator`, `BeforeValidator`, or `WrapValidator`, those validators receive a `ValidationInfo` object whose `context` is always `None` because there is no mechanism to pass a context when calling the decorated function. The `ValidateCallWrapper.__call__` in `_validate_call.py` invokes the pydantic-core validator without a context argument, and `update_wrapper_attributes` in `validate_call_decorator.py` does not expose a `context` pathway. Add a `.call_with_context(ctx, *args, **kwargs)` helper method to the wrapper so callers can inject validation context.

### N11: Update `docs/concepts/fields.md` to document the `include_if` parameter for `Field()`

The `include_if` parameter added to `Field()` is not reflected in the documentation. Add a section to `docs/concepts/fields.md` covering usage examples, behavior when the callable returns `False`, interaction with `exclude_if`, and the relationship to `model_dump(exclude=...)`. Cross-reference from `docs/concepts/serialization.md` for users who discover conditional serialization from that page. Update `mkdocs.yml` navigation if a new section anchor is needed.

## Medium

### M1: Implement computed fields with dependency tracking

Add the ability for `@computed_field` to declare which model fields it depends on, so revalidation only recomputes affected computed fields when `model_copy(update=...)` is called. Changes span `main.py`, `_fields.py`, `_model_construction.py`, and `_generate_schema.py`.

### M2: Add model inheritance conflict detection and resolution

When a child model redefines a parent field with incompatible validators or types, pydantic silently overwrites. Implement conflict detection in `_model_construction.py` that warns on type narrowing violations and validator conflicts, with an explicit `override=True` parameter in `Field()` to suppress.

### M3: Implement partial model validation for PATCH-style updates

Add a `model_validate_partial()` class method that accepts incomplete data and returns a model with only the provided fields set, leaving others at their defaults. Changes span `main.py`, `_model_construction.py`, and `type_adapter.py` to generate a partial core schema variant.

### M4: Fix `Optional[DiscriminatedUnion]` JSON Schema not hoisting `discriminator` to the outer `anyOf` level

When a discriminated union is wrapped in `Optional` (i.e., `Optional[Annotated[Union[A, B], Field(discriminator='type')]]`), the generated JSON Schema places the `discriminator` object and `oneOf` inside the first element of the outer `anyOf` rather than at the schema root. This nesting breaks OpenAPI tooling that expects the `discriminator` at the outermost level of a schema. The `GenerateJsonSchema` class in `json_schema.py` should detect when an `anyOf` contains exactly one discriminated-union branch and one null branch, and hoist the `discriminator` mapping to the outer level. Changes span `json_schema.py`, `_internal/_discriminated_union.py`, `_internal/_generate_schema.py`, `fields.py`, and `type_adapter.py`.

### M5: Implement custom error message templates for validation errors

Add configurable error message templates per field or per model via `Field(error_messages={...})` and `model_config`. The template system should support variable interpolation (`{input}`, `{field_name}`, `{constraint}`). Changes span `fields.py`, `config.py`, `_generate_schema.py`. Update `docs/concepts/validators.md` with error message template documentation and add usage examples to `docs/concepts/fields.md`.

### M6: Add `TypeAdapter` caching and reuse for repeated type patterns

Creating `TypeAdapter` instances is expensive due to schema generation. Implement a cache keyed by type annotation that reuses core schemas and validators. Add cache invalidation when `ConfigDict` differs. Changes span `type_adapter.py` and `_internal/_generate_schema.py`.

### M7: Add `serializer_fallback` option to `model_config` for handling unregistered types during serialization

When a model contains fields of types that have no registered serializer, pydantic falls back to a default behavior that may produce incorrect output. Add a `serializer_fallback` key to `ConfigDict` in `config.py` that accepts a callable `(value: Any) -> Any`. When a field's type has no registered serializer and the default serialization would fail, the callable is invoked instead. The `_internal/_generate_schema.py` schema generator should inject this fallback into the serialization schema; `main.py` and `type_adapter.py` must pass it through to `model_dump()` and `dump_python()`. Changes span `config.py`, `main.py`, `functional_serializers.py`, `_internal/_generate_schema.py`, `type_adapter.py`, and `_internal/_model_construction.py`.

### M8: Add validation performance profiling to the plugin system

Extend the plugin interface in `plugin/` to support validation timing hooks: `on_validate_start`, `on_validate_end` with field-level granularity. Expose profiling data through a `ValidationProfile` object. Changes span `plugin/`, `_internal/_generate_schema.py`, and `main.py`.

### M9: Implement `TypeAdapter.validate_many()` for batch validation with error collection

Add a `validate_many(items: list)` method to `TypeAdapter` that validates all items and returns a result object containing both valid items and per-item errors, rather than failing on the first error. Changes span `type_adapter.py` and add batch validation logic.

### M10: Add model schema versioning for backward-compatible deserialization

Implement schema version tracking via `model_config['schema_version']` with migration functions between versions. When validating data with an older version tag, apply the migration chain before validation. Changes span `config.py`, `main.py`, and `_model_construction.py`.

### M11: Add migration guide and changelog for partial model validation

Create a new section in `docs/migration.md` covering `model_validate_partial()` usage patterns and migration from workaround approaches. Update `docs/concepts/models.md` with partial validation examples and caveats. Add a changelog entry to `HISTORY.md` describing the feature. Update `mkdocs.yml` navigation to surface the partial validation docs.

## Wide

### W1: Implement async validation support for I/O-bound validators

Add `@async_field_validator` and `@async_model_validator` decorators that support `async` validator functions, with `await model.async_validate()`. Changes span `functional_validators.py`, `_decorators.py`, `_generate_schema.py`, `_model_construction.py`, `main.py`, and `type_adapter.py`.

### W2: Add OpenAPI 3.1 schema generation mode

Implement a dedicated OpenAPI schema generator that produces spec-compliant output: nullable handling, `readOnly`/`writeOnly` from field metadata, `discriminator` objects, response schema variants, and proper `$ref` handling. Changes span `json_schema.py`, `fields.py`, `config.py`, `types.py`, `functional_serializers.py`, and add an `openapi/` module.

### W3: Implement model diffing and change tracking

Add `model.model_changes()` that tracks which fields changed since construction, `model.model_diff(other)` for comparing two model instances, and `model.model_changelog()` for full mutation history. Changes span `main.py`, `_model_construction.py`, `_fields.py`, `root_model.py`, `dataclasses.py`, and add change tracking infrastructure.

### W4: Add multi-format serialization framework

Implement `model.model_dump_yaml()`, `model.model_dump_toml()`, `model.model_dump_msgpack()` with custom serializer registration via `model_config`. Changes span `main.py`, `config.py`, `functional_serializers.py`, `type_adapter.py`, `_internal/_generate_schema.py`, and add format-specific serialization modules. Update `docs/concepts/serialization.md` with YAML, TOML, and MsgPack serialization examples and add changelog entries to `HISTORY.md`.

### W5: Implement pydantic settings with hierarchical configuration sources

Add a configuration management system where models can declare multiple config sources (env vars, files, remote) with priority ordering, type-safe overrides, and hot-reloading. Changes span `config.py`, `fields.py`, `main.py`, `_model_construction.py`, `type_adapter.py`, and add a `settings/` module.

### W6: Add GraphQL schema generation from pydantic models

Implement automatic GraphQL type generation from pydantic models: types, inputs, enums, unions with discriminators, and nested resolution. Changes span `json_schema.py`, `main.py`, `fields.py`, `types.py`, `_internal/_generate_schema.py`, `_discriminated_union.py`, and add a `graphql/` module.

### W7: Implement model migration framework for schema evolution

Add a migration system for evolving model schemas across versions: field renames, type changes, computed defaults, and data transformations. Include forward/backward migration with validation. Changes span `main.py`, `config.py`, `fields.py`, `_model_construction.py`, `_internal/_fields.py`, and add a new `_internal/_schema_migration.py` module to house the migration infrastructure.

### W8: Add comprehensive model testing utilities

Implement `pydantic.testing` with `ModelFactory` for generating valid model instances, `assert_validates`/`assert_rejects` helpers, schema compatibility checking between model versions, and fuzzing support. Changes span `main.py`, `fields.py`, `types.py`, `type_adapter.py`, `json_schema.py`, and add a `testing/` module.

### W9: Implement cross-model validation and referential integrity

Add the ability to define cross-model validation rules where one model's field references another model's instance, with referential integrity checking, cascading updates, and lazy loading. Changes span `main.py`, `fields.py`, `_model_construction.py`, `_generate_schema.py`, `_fields.py`, `root_model.py`, and add a registry module.

### W10: Add real-time validation event streaming

Implement an event system that emits structured validation events (field validated, coercion applied, default used, error raised) to configurable sinks for monitoring and debugging. Changes span `main.py`, `plugin/`, `_generate_schema.py`, `_model_construction.py`, `type_adapter.py`, `config.py`, and add an events module.

### W11: Overhaul CI and documentation for async validation support

Update `.github/workflows/ci.yml` to add an async test matrix covering `asyncio` and `trio` backends. Update `docs/concepts/validators.md` with async validator documentation and usage examples. Add performance comparison notes to `docs/concepts/performance.md`. Update `HISTORY.md` with async validation changelog entries. Update `docs/contributing.md` with async testing guidelines and update `pyproject.toml` with new async test dependencies.
