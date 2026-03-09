# nlohmann/json

| Field | Value |
|-------|-------|
| **URL** | https://github.com/nlohmann/json |
| **License** | MIT |
| **Language** | C++ |
| **Scale** | Medium (single-header with modular detail/) |
| **Category** | JSON library |
| **Set** | Cutoff |
| **Commit** | `f534f4f75e12893716ea688679aeb768bff426c4` |

## Why this repo

- **Iconic C++ library**: The most widely used JSON library for modern
  C++ with 43K+ stars, exercising advanced template metaprogramming,
  SFINAE, and ADL-based serialization. Navigation requires understanding
  deeply nested namespaces, tag-dispatch patterns, and template
  specialization throughout `detail/`.
- **Well-structured**: Despite shipping as a single amalgamated header
  (`json.hpp`), the source is organized into `detail/` subdirectories
  covering conversions, input/output adapters, iterators, binary
  formats, and metaprogramming utilities — each with clear responsibilities.
- **Rich feature surface**: Supports JSON Pointer (RFC 6901), JSON Patch
  (RFC 6902), JSON Merge Patch (RFC 7396), CBOR, MessagePack, UBJSON,
  BSON, and BJData binary formats, plus SAX-style parsing and
  user-defined type conversions via `to_json`/`from_json` ADL.
- **Permissive**: MIT license.

## Structure overview

```
include/nlohmann/
├── json.hpp                          # Amalgamated single-header (generated)
├── json_fwd.hpp                      # Forward declarations
├── ordered_map.hpp                   # Insertion-order-preserving map
├── byte_container_with_subtype.hpp   # Binary subtype container (CBOR tags)
├── adl_serializer.hpp                # Default ADL-based serializer
├── detail/
│   ├── conversions/
│   │   ├── from_json.hpp             # from_json overloads (deserialize)
│   │   └── to_json.hpp              # to_json overloads (serialize)
│   ├── input/
│   │   ├── input_adapters.hpp        # Input sources (string, stream, iterator)
│   │   ├── lexer.hpp                 # Tokenizer / scanner
│   │   ├── parser.hpp                # Recursive-descent JSON parser
│   │   ├── binary_reader.hpp         # CBOR/MessagePack/UBJSON/BSON/BJData decoder
│   │   ├── json_sax.hpp             # SAX event interface
│   │   └── position_t.hpp           # Source position tracking
│   ├── output/
│   │   ├── output_adapters.hpp       # Output sinks (string, stream, vector)
│   │   ├── serializer.hpp            # JSON text serializer (dump)
│   │   └── binary_writer.hpp        # CBOR/MessagePack/UBJSON/BSON/BJData encoder
│   ├── iterators/
│   │   ├── iter_impl.hpp             # Main bidirectional iterator
│   │   ├── iteration_proxy.hpp       # items() key-value proxy
│   │   ├── json_reverse_iterator.hpp # Reverse iterator adapter
│   │   ├── internal_iterator.hpp     # Union of object/array iterators
│   │   └── primitive_iterator.hpp    # Iterator for non-structured types
│   ├── meta/
│   │   ├── type_traits.hpp           # SFINAE helpers, is_basic_json, etc.
│   │   ├── detected.hpp              # Detection idiom (void_t, detected_t)
│   │   ├── is_sax.hpp               # SAX handler concept check
│   │   ├── call_std/
│   │   │   ├── begin.hpp            # ADL begin()
│   │   │   └── end.hpp              # ADL end()
│   │   └── identity_tag.hpp         # Tag dispatch helpers
│   ├── json_pointer.hpp              # JSON Pointer (RFC 6901)
│   ├── json_ref.hpp                  # Reference wrapper for init-list
│   ├── hash.hpp                      # std::hash specialization
│   ├── string_concat.hpp            # Compile-time string concatenation
│   ├── string_escape.hpp            # JSON string escaping
│   ├── value_t.hpp                   # Value type enum (null, object, array, ...)
│   ├── macro_scope.hpp              # Internal macro definitions
│   ├── macro_unscope.hpp            # Internal macro cleanup
│   ├── exceptions.hpp               # Exception hierarchy (parse, type, out_of_range, other)
│   └── abi_macros.hpp               # ABI versioning macros
```

## Scale indicators

- ~50 header files under `include/nlohmann/detail/`
- ~25K lines of C++ template code
- Deeply nested namespace structure (`nlohmann::detail::...`)
- Zero runtime dependencies (header-only, standard library only)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix lexer not including offending byte value in ill-formed UTF-8 error messages

The `scan_string()` function in `detail/input/lexer.hpp` validates
raw (non-escaped) multi-byte UTF-8 sequences and rejects ill-formed
byte sequences via `next_byte_in_range()` and the `default` case of
the inner switch. However, when an ill-formed byte is detected, the
error message is always the generic
`"invalid string: ill-formed UTF-8 byte"` without identifying the
specific offending byte value. This contrasts with the detailed
control-character error messages elsewhere in `scan_string()` (e.g.,
`"invalid string: control character U+0000 (NUL) must be escaped"`).
Fix `next_byte_in_range()` and the `default` ill-formed-byte case in
`scan_string()` to include the hex value of the offending byte in the
error message — e.g., `"invalid string: ill-formed UTF-8 byte 0xC0"`
— so that users can identify malformed bytes without additional
tooling.

### N2: Fix binary_reader not validating BSON document size against consumed bytes

The BSON reader in `detail/input/binary_reader.hpp` reads the 4-byte
document size prefix but does not validate that the total bytes
consumed during parsing match the declared size. If the BSON data
contains extra trailing bytes within the declared document boundary,
they are silently ignored rather than flagged as corruption. Fix the
BSON parsing path in `binary_reader.hpp` to verify that the consumed
byte count matches the declared document size and throw
`parse_error.110` on mismatch.

### N3: Fix serializer emitting invalid UTF-8 replacement on lone surrogates

The serializer in `detail/output/serializer.hpp` replaces invalid
UTF-8 sequences with `\ufffd` when `error_handler_t::replace` is
active, but for lone high surrogates in `\uD800`–`\uDBFF` it emits
raw bytes instead of the replacement character. Fix the
`dump_escaped` method to detect unpaired surrogates and apply the
replacement consistently.

### N4: Fix hash function producing different results for equivalent number types

The `hash()` function in `detail/hash.hpp` uses separate
`std::hash<number_integer_t>` and `std::hash<number_unsigned_t>`
specializations combined with different `value_t` discriminators for
integer and unsigned values. This means `json(1)` (signed) and
`json(1u)` (unsigned) produce different hashes despite comparing
equal with `operator==`. Fix `hash.hpp` to normalize equivalent
integer and unsigned values to the same hash by using a unified
hashing path when the values are numerically equal.

### N5: Fix ordered_map::erase not using allocator for placement construction during shift

`ordered_map::erase(const key_type&)` in `ordered_map.hpp` shifts
elements down after erasure using placement `new (&*it)
value_type{std::move(*next)}` to reconstruct each shifted element.
This bypass the container’s allocator entirely, using global
`operator new` for placement construction instead of
`std::allocator_traits<Allocator>::construct()`. When `ordered_map`
is used with a custom allocator (e.g., a pool allocator that tracks
constructions), the shift path produces elements that were not
constructed through the allocator. Fix `erase` to use allocator-aware
construction for the shifted elements.

### N6: Fix binary_reader not validating CBOR tag values for typed arrays

The CBOR reader in `detail/input/binary_reader.hpp` accepts any tag
value without validation when reading typed arrays (tags 64–87).
Invalid tag values outside the typed-array range are silently treated
as untagged data. Fix the reader to reject unexpected tag values with
`parse_error.112`.

### N7: Fix iter_impl::operator* using JSON_ASSERT for bounds checking in release builds

`iter_impl::operator*` in `detail/iterators/iter_impl.hpp` guards
dereference of object and array iterators with `JSON_ASSERT`, which
maps to `assert()` and is compiled out in release builds (when
`NDEBUG` is defined). In release mode, calling `operator*` on an
end-positioned object or array iterator therefore silently causes
undefined behavior instead of throwing a catchable
`invalid_iterator` exception. Fix the object and array branches of
`operator*` to replace the release-mode no-op `JSON_ASSERT` checks
with runtime `if` guards that throw
`invalid_iterator::create(214, "cannot get value", m_object)` when
the underlying iterator is at end, matching the exception already
thrown in the null and primitive branches of the same function.

### N8: Fix value_t ordering array not guarded by static_assert against enum changes

The `operator<` and `operator<=>` comparisons for `value_t` in
`detail/value_t.hpp` use a fixed-size `std::array<std::uint8_t, 9>`
to map each enum value to an ordering rank. The array size (9) must
match the number of non-discarded enumerators, but there is no
compile-time check enforcing this. If a new value type is added to
the `value_t` enum without extending the `order` array, the bounds
check (`l_index < order.size()`) silently treats the new type as
unordered rather than producing a compilation error. Fix
`value_t.hpp` to add a `static_assert` verifying the array size
matches the enum range.

### N9: Fix parser losing context when wrapping lexer parse_error tokens

The iterative SAX parser in `detail/input/parser.hpp` passes
`token_type::uninitialized` as the expected token in the
`parse_error` token case of `sax_parse_internal` (the case at the
`token_type::parse_error` label). The comment notes this is
intentional to suppress the "expected" clause, but as a result,
parse errors originating in the lexer (e.g.,
`"invalid string: ill-formed UTF-8 byte"`) are emitted without the
parser context that describes what was being parsed — `"object key"`,
`"value"`, etc. Fix `sax_parse_internal` so that `parse_error`
tokens in value position carry the same context string used in
surrounding structural error paths (e.g., `"value"` inside the
parse_value context), producing messages like
`"syntax error while parsing value - invalid string: ..."` rather
than `"syntax error - invalid string: ..."`.

### N10: Add to_json and from_json overloads for std::variant

The conversion functions in `detail/conversions/to_json.hpp` and
`detail/conversions/from_json.hpp` cover standard library types
including `std::optional`, `std::pair`, `std::tuple`, and
`std::filesystem::path`, but do not include `std::variant`. Users
who hold typed alternatives via `std::variant` must manually match
the active alternative before serialization. Add `to_json` and
`from_json` overloads for `std::variant<Types...>` that serialize the
active alternative as a JSON array `[index, value]` (where `index`
is the zero-based `std::variant::index()`) and deserialize by reading
the index and constructing the corresponding alternative via
`std::get<N>`. Guard the overloads with `__cpp_lib_variant` or an
equivalent feature-detection check. Add a supporting `is_variant`
type trait in `detail/meta/type_traits.hpp` to gate the SFINAE
constraints.

## Medium

### M1: Add SAX-based streaming JSON parser with depth-limited events

Extend the SAX interface in `detail/input/json_sax.hpp` with a new
`filtered_sax` adapter that only emits events for nodes at or below
a specified JSON Pointer prefix. The adapter wraps any user-provided
SAX handler and suppresses events outside the filter path. Changes
span `json_sax.hpp` for the adapter class, `parser.hpp` for wiring
filtered SAX into `sax_parse()`, and `basic_json` for a convenience
`parse(input, json_pointer, sax)` overload.

### M2: Implement JSON Merge Patch diff generation

Add a `json::diff_merge_patch(source, target)` static method that
computes the RFC 7396 merge-patch document transforming `source` into
`target`. The implementation must handle nested objects, array
replacement, and null-means-delete semantics. Changes span
`basic_json` for the public API, `detail/json_pointer.hpp` for path
traversal utilities, and new logic alongside the existing `patch()`
and `diff()` methods.

### M3: Add CBOR diagnostic notation output

Implement `json::to_cbor_diagnostic()` that serializes a JSON value
into CBOR diagnostic notation (RFC 8949 §8) for debugging binary
payloads. The output must handle tags, indefinite-length containers,
byte strings with hex encoding, and special float values. Changes span
`detail/output/binary_writer.hpp` for the diagnostic output mode,
`detail/output/output_adapters.hpp` for a diagnostic string adapter,
and `basic_json` for the public entry point.

### M4: Implement custom allocator support for basic_json

Extend `basic_json` to propagate a user-provided allocator to all
internal containers (object map, array vector, string). The allocator
must be threaded through `ordered_map.hpp`, the internal
`json_value` union construction in `basic_json`, and all
`detail/conversions/from_json.hpp` overloads that construct temporary
containers. Add an `allocator_type` typedef and
`get_allocator()` method.

### M5: Add JSON Schema draft-07 validation

Implement `json::validate(instance, schema)` that validates a JSON
document against a JSON Schema (draft-07) definition. Support `type`,
`properties`, `required`, `items`, `enum`, `pattern`, `minimum`,
`maximum`, `allOf`, `anyOf`, `oneOf`, `not`, and `$ref` resolution.
Changes span a new `detail/schema/` directory with a `validator.hpp`,
modifications to `basic_json` for the public API, and
`detail/json_pointer.hpp` for `$ref` resolution. Also update
`CMakeLists.txt` to add the new `detail/schema/` headers to the
install target, and update `docs/README.md` with a "JSON Schema
Validation" section documenting the supported keywords.

### M6: Implement efficient binary search for sorted JSON arrays

Add `json::binary_search()`, `json::lower_bound()`, and
`json::upper_bound()` member functions for sorted JSON arrays that
perform O(log n) lookups using a user-provided comparator. The
functions must integrate with `iter_impl.hpp` for random-access
iterator support and validate that the array is actually sorted.
Changes span `basic_json`, `detail/iterators/iter_impl.hpp` for
random-access operations, and `detail/meta/type_traits.hpp` for
comparator concept checking.

### M7: Add bjdata_use_optimized convenience option and round-trip marker validation

The BJData writer (`detail/output/binary_writer.hpp`) already detects
homogeneous arrays and emits `$` (type) and `#` (count) markers when
`use_type=true`, but the public `to_bjdata()` API in `basic_json`
exposes only the raw `use_size` / `use_type` pair with both defaulting
to `false`. There is no single `bjdata_use_optimized` boolean to
enable both simultaneously, making optimized output harder to
discover. Furthermore, the writer's internal excluded-marker list
(`bjdx` in `write_ubjson_internal` in `binary_writer.hpp`) is defined
inline but never cross-validated against the reader's
`bjd_optimized_type_markers` list in `binary_reader.hpp`, leaving a
latent round-trip gap if the two diverge. Add a `bjdata_use_optimized`
convenience parameter to the three `to_bjdata()` overloads in
`basic_json` that enables `use_size=true, use_type=true`, wire it
through `binary_writer.hpp`, and add a `static_assert` in
`binary_writer.hpp` that validates the two excluded-marker lists
remain consistent with `binary_reader.hpp`.

### M8: Implement JSON Path (RFC 9535) query support

Add `json::json_path(expression)` that evaluates a JSONPath expression
against a JSON document and returns a `json` array of matching values.
Support root (`$`), child (`.key`), recursive descent (`..`),
wildcards (`*`), array slices (`[0:3]`), and filter expressions
(`[?(@.price < 10)]`). Changes span a new `detail/json_path.hpp`
for the expression parser and evaluator, `basic_json` for the public
method, and `detail/meta/type_traits.hpp` for expression type
detection.

### M9: Add copy-on-write semantics for large JSON values

Implement COW (copy-on-write) for JSON objects and arrays by wrapping
the internal storage in a reference-counted pointer. Copies share
data until mutation. Changes span `basic_json` for the internal
`json_value` union (replacing raw storage with COW pointers),
`detail/iterators/iter_impl.hpp` for detach-on-write in mutable
iterator access, and `detail/conversions/from_json.hpp` for
ensuring proper detach during deserialization into existing values.

### M10: Implement pretty-print customization with indentation callbacks

Add a `json::dump(callback)` overload that accepts an
`indent_callback_t` function invoked at each nesting level, allowing
custom formatting (e.g., aligned colons, compact small arrays, max
line width). Changes span `detail/output/serializer.hpp` for the
callback-driven formatting path, `detail/output/output_adapters.hpp`
for buffered output with rewind support, and `basic_json` for the
new `dump()` overload and callback type definition.

## Wide

### W1: Implement full JSON Schema draft-2020 validation with output formats

Build a complete JSON Schema validator supporting draft-2020-12
including `$dynamicRef`, `prefixItems`, `unevaluatedProperties`,
`unevaluatedItems`, vocabulary system, and format validation. Produce
structured output per the specification (flag, basic, detailed,
verbose formats). Requires a new `detail/schema/` directory with
vocabulary-aware resolver, validator engine, and output formatter,
modifications to `detail/json_pointer.hpp` for dynamic scope
resolution, `detail/exceptions.hpp` for validation error types,
and `basic_json` for the public validation API.

### W2: Add streaming parser with zero-copy string views

Replace the current DOM parser pipeline with an optional zero-copy
mode where string values are represented as `std::string_view` into
the original input buffer. Requires modifying `detail/input/lexer.hpp`
for view-based token emission, `detail/input/parser.hpp` for
lifetime-aware DOM construction, `basic_json` for a
`basic_json_view` variant with non-owning string storage,
`detail/conversions/from_json.hpp` for view-aware deserialization,
`detail/output/serializer.hpp` for serializing view-backed values,
and `detail/iterators/iter_impl.hpp` for iterator support over the
new type.

### W3: Implement parallel JSON processing with thread-safe accessors

Add thread-safe read access to `basic_json` via a `json::concurrent`
wrapper that uses shared mutexes for concurrent reads and exclusive
writes. Implement parallel `for_each()`, `transform()`, and
`find_all()` that partition large arrays/objects across threads.
Changes span `basic_json` for the concurrent wrapper API,
`detail/iterators/iter_impl.hpp` for partitioned iteration,
`detail/meta/type_traits.hpp` for executor concept detection,
`ordered_map.hpp` for thread-safe bucket access, and
`detail/output/serializer.hpp` for parallel sub-tree serialization.

### W4: Rewrite binary format layer as a pluggable codec framework

Refactor the monolithic `binary_reader.hpp` and `binary_writer.hpp`
into a codec plugin architecture where each binary format (CBOR,
MessagePack, UBJSON, BSON, BJData) is a self-contained codec class
implementing a common `binary_codec` interface. Add codec registration,
auto-detection from byte signatures, and per-codec options. Changes
span splitting `detail/input/binary_reader.hpp` into per-format
readers, splitting `detail/output/binary_writer.hpp` into per-format
writers, a new `detail/binary/codec_registry.hpp`, updates to
`basic_json` for codec-based API, and `detail/input/input_adapters.hpp`
for peek-ahead support needed by auto-detection. Also update the
`Makefile` to add `fuzz_testing_bjdata` target for the BJData codec,
and update `FILES.md` to document the new `detail/binary/` directory
structure.

### W5: Implement JSON-LD context processing and expansion

Add JSON-LD 1.1 context processing: `json::expand(context)`,
`json::compact(context)`, and `json::flatten()` per the W3C spec.
Support `@context` resolution (including remote via a pluggable
fetcher), term definitions, type coercion, `@id`/`@type` keywords,
and language maps. Requires a new `detail/jsonld/` directory with
context processor, expansion/compaction algorithms, `basic_json`
for the public API, `detail/json_pointer.hpp` for IRI-based
navigation, and `detail/conversions/to_json.hpp` for RDF literal
serialization.

### W6: Add compile-time JSON parsing with constexpr basic_json

Make `basic_json` usable in `constexpr` contexts for C++20 and later.
The lexer, parser, and value construction must all be `constexpr`-
capable, enabling compile-time JSON validation and embedding.
Changes span `detail/input/lexer.hpp` for constexpr tokenization,
`detail/input/parser.hpp` for constexpr recursive descent,
`basic_json` for constexpr constructors and accessors,
`detail/value_t.hpp` for constexpr comparison, `ordered_map.hpp` for
constexpr map operations, and `detail/exceptions.hpp` for constexpr-
friendly error reporting.

### W7: Implement JSON diff and patch with move detection and conflict resolution

Extend the existing `json::diff()` (RFC 6902) with move-operation
detection, copy-operation detection, and minimal patch generation.
Add three-way merge via `json::merge3(base, ours, theirs)` with
conflict markers for incompatible changes. Changes span `basic_json`
for the extended diff/merge API, `detail/json_pointer.hpp` for
longest-common-subsequence path analysis, a new
`detail/patch/diff_engine.hpp` for the optimized diff algorithm,
`detail/patch/merge3.hpp` for three-way merge logic, and
`detail/exceptions.hpp` for conflict-error types.

### W8: Implement memory-mapped large file JSON processing

Add support for parsing JSON files via memory-mapped I/O without
loading the entire file into memory. Implement a `mmap_input_adapter`
that integrates with the existing input adapter framework, a
lazy-loading DOM that materializes sub-trees on demand, and
range-based access for streaming over gigabyte-scale files. Changes
span `detail/input/input_adapters.hpp` for the mmap adapter,
`detail/input/parser.hpp` for incremental DOM construction,
`basic_json` for a `lazy_json` type with deferred materialization,
`detail/iterators/iter_impl.hpp` for lazy-aware iteration, and
`detail/output/serializer.hpp` for streaming serialization of
lazy nodes.

### W9: Add full Unicode normalization and collation for string comparison

Implement Unicode NFC/NFD/NFKC/NFKD normalization and
locale-sensitive collation for JSON string comparisons. Add
`json::normalize()` for in-place string normalization and a
`json::compare(a, b, collation)` function. Changes span
a new `detail/unicode/` directory with normalization tables and
algorithm, modifications to `detail/output/serializer.hpp` for
normalized output, `detail/input/lexer.hpp` for normalization during
parsing, `basic_json` for the comparison and normalization API,
and `detail/string_escape.hpp` for normalization-aware escaping.

### W10: Implement a JIT-compiled query engine for repeated JSON queries

Build a JIT-compiled query evaluator that compiles JSONPath or
JMESPath expressions into optimized native code for repeated
evaluation against different JSON documents. Support expression
caching, SIMD-accelerated string matching, and parallel evaluation.
Requires a new `detail/query/` directory with expression compiler
and evaluator, integration with `basic_json` for the query API,
`detail/meta/type_traits.hpp` for platform detection,
`detail/iterators/iter_impl.hpp` for compiled traversal operations,
and `detail/input/lexer.hpp` for reusing the tokenizer in expression
parsing.

### N11: Update CITATION.cff metadata and Meson build configuration

The `CITATION.cff` file specifies version `3.12.0` and a fixed
release date but does not include keywords, a DOI, or contributor
entries beyond the primary author. Update `CITATION.cff` to add
relevant keywords (`json`, `c++`, `header-only`, `serialization`),
add the repository DOI, and list major contributors. Also update
`meson.build` to set the project version from a single source of
truth (matching the version in `CMakeLists.txt`) and ensure the
`Package.swift` Swift package manifest declares the correct minimum
Swift tools version.

### M11: Add Makefile targets for binary format testing and update documentation

The `Makefile` defines fuzz-testing targets for JSON, BSON, CBOR,
MessagePack, and UBJSON formats but is missing a target for BJData
and provides no `run_all_fuzz` aggregate target. Add
`fuzz_testing_bjdata` and `run_all_fuzz` targets to the `Makefile`.
Also add a `check-format` target that runs `clang-format --dry-run`
on all headers in `include/nlohmann/`. Update `docs/README.md` to
add a "Fuzzing" section documenting how to run the fuzz targets.
Update `.github/CONTRIBUTING.md` to mention the fuzz-testing
targets and how to add corpus files. Also update
`.github/workflows/ubuntu.yml` to add a CI job that runs
`make check-amalgamation` on every push.

### W11: Overhaul CI and build configuration across all platforms

The CI configuration spans `.cirrus.yml` (ARM Linux container),
`.github/workflows/ubuntu.yml`, `.github/workflows/macos.yml`,
`.github/workflows/windows.yml`, and several analysis workflows
(CodeQL, Semgrep, Flawfinder, Scorecards). Several gaps remain:
`.cirrus.yml` uses `image: gcc:latest` without pinning the Docker
image to a specific version tag or digest, making ARM-container
builds non-reproducible; `.cirrus.yml` exercises only CMake and
lacks a Meson build step; the Windows workflow covers only `Win32`
and `x64` architectures with no ARM64 Windows target; and none of
the workflows test the C++23 standard mode end-to-end. Synchronize
coverage: pin the `.cirrus.yml` Docker image to a specific versioned
tag, add a Meson build task alongside the existing CMake task in
`.cirrus.yml`, add ARM64 Windows to the Windows workflow matrix,
and add C++23 standard jobs to the Ubuntu workflow. Update
`CMakeLists.txt` to add a `JSON_DisableEnumSerialization` validation
test target that confirms the `JSON_DISABLE_ENUM_SERIALIZATION` macro
path compiles correctly. Update `ChangeLog.md` with a "Build & CI"
section, update `.reuse/dep5` with copyright info for any new files,
and update `.github/CONTRIBUTING.md` to document the ARM and Meson
CI paths.
