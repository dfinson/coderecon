# Tasks — Humanizr/Humanizer

8 tasks (3 narrow, 3 medium, 2 wide) for the C# string manipulation library.

## Narrow

### N1: Fix `ToQuantity` formatting negative numbers incorrectly

`(-3).ToWords()` returns "minus three" correctly, but
`"item".ToQuantity(-3, ShowQuantityAs.Words)` returns "minus three item"
(singular) instead of "minus three items" (plural). The pluralization
logic does not handle negative quantities. Fix `ToQuantity` to use the
absolute value for pluralization decisions.

### N2: Add ordinal word support for Portuguese

The Portuguese localization supports `ToWords()` for cardinal numbers
but not `ToOrdinalWords()`. Implement the ordinal number-to-words
converter for Portuguese, handling gender agreement (primeiro/primeira)
and irregular ordinals through 10th.

### N3: Fix `Truncate` with `TruncateFrom.Left` adding ellipsis at wrong end

When truncating from the left with `"long string".Truncate(5, Truncator.FixedLength, TruncateFrom.Left)`, the ellipsis characters appear
at the end instead of the beginning. The output is `"tring…"` but
should be `"…tring"`. Fix the left-truncation to place the truncation
indicator at the left.

## Medium

### M1: Add relative time formatting with granularity control

The current `Humanize()` on `DateTime` shows "2 hours ago" but with no
control over granularity. Implement a `Humanize(precision)` overload
that shows multiple units: "2 hours and 15 minutes ago" (precision=2),
"2 hours, 15 minutes, and 30 seconds ago" (precision=3). Support both
past and future directions. Add `maxUnit` and `minUnit` parameters
to cap the range.

### M2: Implement file size formatting with IEC/SI units

Add `ByteSize` formatting that supports both IEC binary units (KiB, MiB,
GiB — powers of 1024) and SI decimal units (KB, MB, GB — powers of 1000).
Include parsing from formatted strings back to byte counts. Support
custom decimal precision, automatic unit selection, and explicit unit
specification. Add localization for unit names in supported languages.

### M3: Add collection humanization

Implement `IEnumerable<T>.Humanize()` that formats collections as
English lists: `["a", "b", "c"].Humanize()` → `"a, b, and c"`.
Support conjunctions (`and`, `or`), Oxford comma configuration,
custom separators, custom element formatters, and truncation for
long lists (`"a, b, and 8 more"`). Add localization for conjunctions
and list formatting conventions.

## Wide

### W1: Add comprehensive date/time humanization

Implement a unified date/time humanization system covering: relative
time ("2 days ago"), calendar time ("last Tuesday"), approximate
duration ("about a month"), date ranges ("March 3–7"), recurring
patterns ("every other Tuesday"), and time-of-day descriptions
("this morning", "tonight"). Support timezone-aware formatting and
cultural conventions (24h vs 12h, date order). Add localization for
all time expressions.

### W2: Implement a fluent builder API for complex formatting

Add a `Formatter` builder that chains humanization operations:
`Formatter.For(value).Truncate(50).Titleize().ToQuantity(3)`.
The builder should be composable, reusable (save a formatter
configuration and apply to many values), and type-safe (only show
methods applicable to the current value type). Include predefined
formatters for common patterns (slug generation, display name
formatting, search-friendly text). Add serialization of formatter
configurations for use in configuration files.
