# date-fns/date-fns

| Field | Value |
|-------|-------|
| **URL** | https://github.com/date-fns/date-fns |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Large |
| **Category** | Date utility library |
| **Set** | Cutoff |

## Why this repo

- **Well-structured**: Each date function lives in its own file under
  `src/` (e.g., `addDays/`, `format/`, `parse/`, `differenceInDays/`),
  making the codebase highly modular. Locale definitions under `src/locale/`
  follow a uniform structure with `formatDistance`, `formatRelative`,
  `localize`, and `formatLong` entries. Functional programming variants
  under `src/fp/` wrap every function with curried signatures.
- **Rich history**: 5K+ commits, 34K+ stars. The most popular modular
  date library for JavaScript/TypeScript. PRs cover edge cases around
  DST transitions, locale additions, and time zone handling.
- **Comprehensive types**: The library exports strong TypeScript types
  for intervals, durations, locale objects, and format tokens via
  `types.ts` and per-function type exports.
- **Permissive**: MIT license.

## Structure overview

```
src/
├── addDays/              # addDays(date, amount) — add days to a date
├── addMonths/            # addMonths(date, amount) — add months
├── addWeeks/             # addWeeks(date, amount) — add weeks
├── addYears/             # addYears(date, amount) — add years
├── differenceInDays/     # differenceInDays(dateLeft, dateRight)
├── differenceInHours/    # differenceInHours(dateLeft, dateRight)
├── differenceInMonths/   # differenceInMonths(dateLeft, dateRight)
├── eachDayOfInterval/    # eachDayOfInterval(interval) — iterate days
├── endOfDay/             # endOfDay(date) — set to 23:59:59.999
├── endOfMonth/           # endOfMonth(date) — last moment of month
├── format/               # format(date, formatStr, options) — format dates
├── formatDistance/        # formatDistance(date, baseDate) — "3 days ago"
├── formatRelative/       # formatRelative(date, baseDate) — relative words
├── getDay/               # getDay(date) — day of week (0-6)
├── getMonth/             # getMonth(date) — month (0-11)
├── getYear/              # getYear(date) — full year
├── isAfter/              # isAfter(date, dateToCompare) — comparison
├── isBefore/             # isBefore(date, dateToCompare) — comparison
├── isEqual/              # isEqual(dateLeft, dateRight) — exact equality
├── isSameDay/            # isSameDay(dateLeft, dateRight)
├── isValid/              # isValid(date) — check for Invalid Date
├── isWithinInterval/     # isWithinInterval(date, interval) — range check
├── parse/                # parse(dateString, formatString, referenceDate)
├── parseISO/             # parseISO(argument) — parse ISO 8601 strings
├── startOfDay/           # startOfDay(date) — set to 00:00:00.000
├── startOfMonth/         # startOfMonth(date) — first moment of month
├── startOfWeek/          # startOfWeek(date, options) — week start
├── sub/                  # sub(date, duration) — subtract a duration
├── fp/                   # Functional programming curried variants
│   ├── addDays/          # addDays(amount)(date) — curried
│   ├── format/           # format(formatStr)(date) — curried
│   └── ...               # One curried wrapper per function
├── locale/               # Locale definitions (50+ locales)
│   ├── en-US/            # English (US) — default locale
│   ├── de/               # German
│   ├── fr/               # French
│   ├── ja/               # Japanese
│   ├── zh-CN/            # Chinese (Simplified)
│   └── ...               # Each with formatDistance, localize, formatLong
├── constants.ts          # millisecondsInSecond, daysInWeek, etc.
├── types.ts              # Interval, Duration, Locale, Day, Month types
├── _lib/                 # Internal helpers
│   ├── toDate/           # Normalize input to Date object
│   ├── requiredArgs/     # Argument count validation
│   ├── getTimezoneOffsetInMilliseconds/
│   └── defaultLocale/    # Default locale (en-US) reference
└── index.ts              # Public barrel export of all functions
```

## Scale indicators

- ~220 function modules under `src/`
- ~50 locale definitions under `src/locale/`
- ~200 curried wrappers under `src/fp/`
- ~30K lines of TypeScript source (excluding tests)

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix format() not handling escaped single quotes inside format strings

When `format()` receives a format string containing an escaped single
quote within a quoted literal (e.g., `"h 'o''clock'"`), the token
parser in `format/` incorrectly splits the literal at the escaped
quote, producing garbled output. Fix the tokenizer to treat two
consecutive single quotes inside a quoted section as a literal quote
character.

### N2: Fix differenceInMonths returning wrong result across DST boundaries

`differenceInMonths()` returns an off-by-one result when the date range
crosses a DST transition because the comparison uses raw timestamps
without compensating for the timezone offset change. Fix the month
difference calculation in `differenceInMonths/` to normalize both dates
to UTC-equivalent values before comparison.

### N3: Fix startOfWeek not respecting weekStartsOn from locale

When `startOfWeek()` is called with a locale that sets `weekStartsOn: 1`
(Monday) but no explicit `weekStartsOn` option, the function defaults to
Sunday (0) instead of reading the locale's setting. Fix the options
resolution in `startOfWeek/` to fall back to the locale's
`options.weekStartsOn` when the parameter is not explicitly provided.

### N4: Fix parseISO silently returning Invalid Date for time-only strings

`parseISO("T14:30:00")` returns an Invalid Date without any indication
of what went wrong. The ISO parser in `parseISO/` should recognize
time-only ISO 8601 strings (starting with `T`) and parse them relative
to the epoch date, or throw a `RangeError` with a clear message
explaining that a date component is required.

### N5: Fix isWithinInterval not handling intervals where start equals end

`isWithinInterval(date, { start, end })` returns `false` when `start`
and `end` are the same timestamp and `date` equals that timestamp. A
zero-length interval at a point should include that exact point. Fix
the boundary comparison in `isWithinInterval/` to use `<=` for both
bounds.

### N6: Fix formatDistance rounding error for durations near threshold boundaries

`formatDistance()` shows "about 1 hour" for a difference of exactly 44
minutes because the rounding logic rounds 44 up to 45, which crosses
the hour threshold. Fix the rounding in `formatDistance/` to use
floor-based thresholds so that 44 minutes remains "44 minutes" and the
hour label only triggers at 45 minutes.

### N7: Fix addMonths producing invalid dates when adding to the 31st

`addMonths(new Date(2024, 0, 31), 1)` returns March 2 instead of
February 29 (2024 is a leap year) because the overflow logic does not
clamp to the last day of the target month. Fix the month addition in
`addMonths/` to detect month overflow and clamp to the last valid day
of the resulting month.

### N8: Fix toDate helper not rejecting non-finite numeric inputs

The internal `toDate()` helper in `_lib/toDate/` accepts `Infinity` and
`-Infinity` as numeric inputs and converts them to an Invalid Date
without throwing. Other functions that rely on `toDate()` then silently
propagate the Invalid Date. Fix `toDate()` to throw a `RangeError` for
non-finite numeric inputs.

### N9: Fix eachDayOfInterval including an extra day when interval spans DST fall-back

`eachDayOfInterval()` generates one extra day when the interval crosses
a fall-back DST transition because the 25-hour day causes the iteration
to produce two entries for the same calendar date. Fix the day iteration
in `eachDayOfInterval/` to use `startOfDay()` normalization within the
loop to avoid duplicate calendar dates.

### N10: Fix locale formatRelative not receiving the correct base date

In locale definitions, the `formatRelative` function is called with the
wrong argument order — it receives `(token, date)` instead of
`(token, date, baseDate, options)`, preventing locales from using the
base date for context-dependent phrasing. Fix the call site in
`formatRelative/` to pass all four arguments to the locale function.

## Medium

### M1: Implement duration arithmetic functions (addDuration, subDuration)

Add `addDuration(date, duration)` and `subDuration(date, duration)` that
accept a `Duration` object `{ years, months, weeks, days, hours, minutes,
seconds }` and apply all fields in order. Requires creating new function
modules under `src/`, integrating with existing `add*` functions for each
field, handling the `Duration` type from `types.ts`, and generating
corresponding `fp/` curried variants.

### M2: Add ISO 8601 duration parsing and formatting

Implement `parseISODuration("P1Y2M3DT4H5M6S")` that returns a
`Duration` object, and `formatISODuration(duration)` that serializes
back to an ISO 8601 duration string. Requires new function modules,
a parser for the ISO 8601 duration grammar, validation for negative
or fractional values, and proper type exports in `types.ts`.

### M3: Implement configurable default options via a context provider

Add `setDefaultOptions({ locale, weekStartsOn, firstWeekContainsDate })`
that sets global defaults used by all functions when options are not
explicitly provided. Requires modifying the options resolution logic
shared across all function modules via `_lib/defaultOptions/`, updating
the `defaultLocale` helper, and ensuring the global state is isolated
in module scope to avoid cross-contamination in bundled applications.

### M4: Add recurring interval generation functions

Implement `eachWeekOfInterval()`, `eachMonthOfInterval()`, and
`eachYearOfInterval()` that generate arrays of dates at the specified
cadence within a given interval. Requires new function modules that
follow the same pattern as `eachDayOfInterval/`, DST-safe iteration
using `startOfWeek/startOfMonth/startOfYear`, and corresponding `fp/`
curried wrappers.

### M5: Implement intlFormat function using Intl.DateTimeFormat

Add `intlFormat(date, formatOptions, localeOptions)` that wraps the
browser's `Intl.DateTimeFormat` API to produce locale-aware formatted
strings without relying on date-fns locale objects. Requires a new
function module, type definitions for the `Intl.DateTimeFormat` options
subset, graceful fallback for environments without `Intl` support, and
an `fp/` curried variant.

### M6: Add quarter-based date functions

Implement `startOfQuarter()`, `endOfQuarter()`, `getQuarter()`,
`setQuarter()`, `addQuarters()`, `differenceInQuarters()`, and
`isSameQuarter()`. Requires seven new function modules following the
existing patterns, `fp/` curried wrappers for each, and updates to
`index.ts` barrel exports. Quarter boundaries must respect the calendar
year (Q1 = Jan-Mar).

### M7: Implement a date range type with iteration and containment checks

Add an `Interval` class or factory `interval(start, end)` that provides
`contains(date)`, `overlaps(other)`, `intersection(other)`,
`union(other)`, and `[Symbol.iterator]()` for daily iteration. Requires
a new module, integration with the existing `Interval` type in
`types.ts`, validation that `start <= end`, and `fp/` curried variants
for each method.

### M8: Add custom format token registration for format()

Implement `format.addToken(token, formatter)` that allows users to
register custom format tokens (e.g., `'Q'` for quarter) that the
`format()` function recognizes alongside built-in tokens. Requires
changes to the token parser in `format/`, a token registry with conflict
detection for built-in tokens, and documentation of the formatter
callback signature.

### M9: Implement locale-aware ordinal formatting across all locales

Add ordinal support to `format()` via the `'do'`, `'Mo'`, `'Qo'` tokens
so that dates render as "1st", "2nd", "3rd" etc., with correct ordinal
rules per locale. Requires extending each locale definition under
`src/locale/` with an `ordinalNumber` function, updating the `format/`
token processor to dispatch ordinal tokens to the locale, and adding
the ordinal type to `types.ts`.

### M10: Add tree-shakeable sub-path exports to package.json

Restructure the package's `exports` map in `package.json` to expose
each function as a sub-path export (e.g., `date-fns/addDays`), each
locale as `date-fns/locale/en-US`, and each FP variant as
`date-fns/fp/addDays`. Requires updating `package.json` exports,
verifying that bundlers (webpack, esbuild, rollup) correctly tree-shake
unused functions, and ensuring the `fp/` re-exports resolve correctly.

## Wide

### W1: Implement time zone support via a companion module

Add a `date-fns-tz` companion module under `src/tz/` that provides
`zonedTimeToUtc()`, `utcToZonedTime()`, `formatInTimeZone()`, and
`getTimezoneOffset()`. Requires a time zone database lookup (via
`Intl.DateTimeFormat` or bundled IANA data), integration with the
`format/` token system for zone-aware formatting, updates to
`types.ts` for zone-aware types, `fp/` curried variants, and changes
to `_lib/getTimezoneOffsetInMilliseconds/` for offset calculations.

### W2: Implement a fluent date builder API

Add a chainable `dateFns(date).addDays(3).startOfMonth().format('yyyy-MM-dd')`
API that wraps date-fns functions in a fluent interface. Requires a new
`DateFns` wrapper class with methods mirroring every public function,
lazy evaluation so chained operations compose without intermediate Date
allocations, locale and options propagation through the chain, and
TypeScript overloads that preserve return types through the chain.

### W3: Add comprehensive locale validation and generation tooling

Build a locale development toolkit: a validator that checks every locale
definition for completeness (all required formatters, correct token
coverage, proper pluralization rules), a generator scaffold that creates
a new locale from a template, and a test harness that runs format/parse
round-trip tests against every locale. Requires traversing all 50+
locale directories under `src/locale/`, defining a locale schema,
building a CLI tool, and generating test fixtures per locale.

### W4: Implement a date expression DSL for complex date calculations

Add `evaluate("now + 3 business days - 1 month @ startOfDay")` that
parses a string expression into a chain of date-fns operations.
Support arithmetic (`+`, `-`), snap operations (`@startOfMonth`),
business day logic (skipping weekends and configurable holidays), and
variable references. Requires a tokenizer, parser, and evaluator under
a new `src/expression/` module, integration with existing function
modules for each operation, and type-safe return types.

### W5: Add comprehensive parse format pattern support matching format()

Extend `parse()` to support all format tokens that `format()` supports,
achieving round-trip fidelity. Currently `parse()` handles a subset of
tokens. Requires adding parsers for era (`G`), quarter (`Q`), week-
numbering year (`Y`), local day of week (`e`, `c`), day period (`a`,
`b`, `B`), timezone (`z`, `x`, `X`), and timestamp (`t`, `T`) tokens.
Changes span the parse token registry, locale-aware parsing for day/
month names, and the `_lib/` helpers for week-numbering calculations.

### W6: Implement a streaming/observable date utility layer

Add reactive wrappers that emit date values over time: `interval$(period)`
emitting dates at intervals, `timer$(date)` emitting when a target date
is reached, `countdown$(target)` emitting remaining durations, and
`dateChange$()` emitting on calendar date transitions. Requires a new
`src/observable/` module, integration with the existing duration and
interval types, support for both RxJS-compatible and native async
iterator interfaces, and cleanup/disposal handling.

### W7: Implement business day calculation functions with holiday calendars

Add `addBusinessDays()`, `differenceInBusinessDays()`,
`isBusinessDay()`, `nextBusinessDay()`, and `previousBusinessDay()` that
skip weekends and user-provided holiday calendars. Support configurable
work weeks (e.g., Sun-Thu for Middle Eastern locales). Requires new
function modules, a `HolidayCalendar` type in `types.ts`, integration
with locale `options.weekStartsOn` for work-week determination, a
holiday lookup structure, and `fp/` curried variants.

### W8: Add a migration CLI for upgrading from date-fns v2 to v3

Build a codemod tool that automatically migrates user code from v2 to v3
API: rename changed function names, update import paths from
`date-fns/esm/` to `date-fns/`, convert removed `locale` re-exports,
update option parameter names, and flag deprecated usage patterns with
inline comments. Requires an AST parser (using TypeScript compiler API),
a transformation rule engine, a CLI entry point, and a dry-run mode that
reports changes without writing.

### W9: Implement a date arithmetic constraint solver

Add `closestTo(date, dates)`, `clamp(date, interval)`, `roundToNearest(date, unit, method)`, and `snap(date, unit, direction)` functions plus a
constraint solver `findDate({ after, before, dayOfWeek, monthDay, not })`
that returns the nearest date satisfying all constraints. Requires new
function modules for each utility, a constraint evaluation engine, an
iteration strategy for constraint satisfaction, and comprehensive edge-
case handling for month/year boundaries.

### W10: Implement full ICU-compatible week numbering system

Add complete ISO 8601 and US week-numbering support: `getISOWeek()`,
`getWeek()` (locale-aware), `setISOWeek()`, `setWeek()`,
`getISOWeekYear()`, `getWeekYear()`, `startOfISOWeekYear()`,
`startOfWeekYear()`, and `getWeeksInYear()`. Each variant must respect
`firstWeekContainsDate` and `weekStartsOn` from locale options. Requires
nine new function modules, updates to `_lib/` week-numbering helpers,
integration with the `format/` and `parse/` token systems for `I`, `R`,
`w`, and `Y` tokens, and `fp/` curried wrappers.
