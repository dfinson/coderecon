# google/googletest

| Field | Value |
|-------|-------|
| **URL** | https://github.com/google/googletest |
| **License** | BSD-3-Clause |
| **Language** | C++ |
| **Scale** | Medium (multi-module project) |
| **Category** | Testing framework |

## Why this repo

- **Multi-module with clear boundaries**: Google Test (test framework —
  assertions, test fixtures, parameterized tests, death tests, typed tests)
  and Google Mock (mocking framework — matchers, actions, expectations,
  cardinalities) are two distinct subsystems with shared infrastructure.
  Both have their own macro systems, internal implementations, and public
  APIs.
- **Well-structured**: `googletest/` and `googlemock/` are cleanly separated
  top-level directories. Each has its own `include/` (public headers) and
  `src/` (implementation). Internal helpers are separated from the public API.
- **Rich history**: 5K+ commits, maintained by Google. The standard C++ test
  framework. PRs cover new assertion types, matcher combinators, and
  cross-platform compatibility.
- **Permissive**: BSD-3-Clause.

## Structure overview

```
googletest/
├── include/gtest/
│   ├── gtest.h              # Main public header
│   ├── gtest-param-test.h   # Parameterized tests
│   ├── gtest-typed-test.h   # Typed tests
│   ├── gtest-death-test.h   # Death tests
│   ├── gtest-matchers.h     # Matchers
│   └── internal/            # Internal implementation headers
│       ├── gtest-internal.h
│       ├── gtest-param-util.h
│       └── gtest-port.h     # Platform abstraction
├── src/
│   ├── gtest.cc             # Core framework implementation
│   ├── gtest-death-test.cc  # Death test implementation
│   ├── gtest-filepath.cc    # File path utilities
│   └── gtest-port.cc        # Platform implementations

googlemock/
├── include/gmock/
│   ├── gmock.h              # Main mock header
│   ├── gmock-matchers.h     # Matcher combinators
│   ├── gmock-actions.h      # Mock actions
│   ├── gmock-spec-builders.h # Expectation building
│   └── internal/            # Internal headers
├── src/
│   ├── gmock.cc
│   ├── gmock-matchers.cc
│   └── gmock-spec-builders.cc
```

## Scale indicators

- ~60 C++ header/source files across both libraries
- ~50K lines of code
- 2-3 levels of include nesting
- Clear gtest/gmock boundary with shared internal utilities
