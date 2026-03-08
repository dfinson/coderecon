# thephpleague/flysystem

| Field | Value |
|-------|-------|
| **URL** | https://github.com/thephpleague/flysystem |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Medium |
| **Category** | Filesystem abstraction |
| **Set** | Cutoff |
| **Commit** | `0faf66a23e934a90bee5d24e7791264fafe5afaa` |

## Why this repo

- **Well-structured**: Clean adapter pattern — `FilesystemAdapter`
  interface (read, write, delete, move, copy, list, visibility,
  metadata), `Filesystem` class (path normalization, config
  propagation, public/temporary URL generation), `MountManager`
  (multi-filesystem routing by scheme prefix), 12+ adapter
  implementations (`Local`, `InMemory`, `AwsS3V3`, `AsyncAwsS3`,
  `AzureBlobStorage`, `GoogleCloudStorage`, `Ftp`, `PhpseclibV3`
  SFTP, `WebDAV`, `GridFS`, `ZipArchive`, `ReadOnly`), path
  normalization (`WhitespacePathNormalizer`), path prefixing, URL
  generation, checksum computation, and a rich exception hierarchy.
- **Rich history**: 13K+ stars, 1.4K+ commits. The standard
  filesystem abstraction for PHP, used by Laravel and thousands of
  packages. Issues span adapter compatibility, path normalization
  edge cases, visibility handling, and cloud storage specifics.
- **Permissive**: MIT licensed.

## Structure overview

```
flysystem/
├── composer.json                                      # Composer package configuration
├── phpunit.xml.dist                                   # PHPUnit configuration
├── src/
│   ├── Filesystem.php                                 # Core Filesystem — read, write, delete, copy, move, list, URL, checksum
│   ├── FilesystemAdapter.php                          # Adapter interface — fileExists, write, read, delete, listContents, etc.
│   ├── FilesystemOperator.php                         # Combined reader + writer interface
│   ├── FilesystemReader.php                           # Read-only filesystem interface
│   ├── FilesystemWriter.php                           # Write-only filesystem interface
│   ├── Config.php                                     # Config value object — get, extend, withDefaults, withSetting
│   ├── PathNormalizer.php                             # PathNormalizer interface
│   ├── WhitespacePathNormalizer.php                   # Default normalizer — strips .., whitespace, backslashes
│   ├── PathPrefixer.php                               # Adds/strips directory prefix for adapter paths
│   ├── MountManager.php                               # Multi-filesystem routing by scheme prefix (scheme://path)
│   ├── Visibility.php                                 # Visibility constants — PUBLIC, PRIVATE
│   ├── StorageAttributes.php                          # Base interface for file/directory attributes
│   ├── FileAttributes.php                             # File metadata — path, size, mimeType, visibility, lastModified
│   ├── DirectoryAttributes.php                        # Directory metadata — path, visibility, lastModified
│   ├── DirectoryListing.php                           # Lazy iterable of StorageAttributes with filter/map/toArray
│   ├── DecoratedAdapter.php                           # Adapter decorator base class
│   ├── PortableVisibilityGuard.php                    # Validates visibility values
│   ├── ResolveIdenticalPathConflict.php               # Enum for copy/move same-path behavior
│   ├── CalculateChecksumFromStream.php                # Trait for computing checksums via stream reads
│   ├── ChecksumProvider.php                           # Interface for adapters providing native checksums
│   ├── FilesystemException.php                        # Base exception interface
│   ├── FilesystemOperationFailed.php                  # Base class for operation failures
│   ├── UnableToReadFile.php                           # Exception for read failures
│   ├── UnableToWriteFile.php                          # Exception for write failures
│   ├── UnableToCopyFile.php                           # Exception for copy failures
│   ├── UnableToMoveFile.php                           # Exception for move failures
│   ├── UnableToDeleteFile.php                         # Exception for file deletion failures
│   ├── UnableToDeleteDirectory.php                    # Exception for directory deletion failures
│   ├── UnableToCreateDirectory.php                    # Exception for directory creation failures
│   ├── UnableToRetrieveMetadata.php                   # Exception for metadata retrieval failures
│   ├── UnableToSetVisibility.php                      # Exception for visibility change failures
│   ├── UnableToCheckExistence.php                     # Exception for existence check failures
│   ├── UnableToListContents.php                       # Exception for listing failures
│   ├── UnableToProvideChecksum.php                    # Exception for checksum failures
│   ├── UnableToGeneratePublicUrl.php                  # Exception for public URL generation failures
│   ├── UnableToGenerateTemporaryUrl.php               # Exception for temporary URL generation failures
│   ├── CorruptedPathDetected.php                      # Exception for paths with control characters
│   ├── PathTraversalDetected.php                      # Exception for path traversal attempts
│   ├── InvalidStreamProvided.php                      # Exception for invalid stream resources
│   ├── InvalidVisibilityProvided.php                  # Exception for invalid visibility values
│   ├── SymbolicLinkEncountered.php                    # Exception for symbolic link encounters
│   ├── Local/
│   │   ├── LocalFilesystemAdapter.php                 # Local filesystem adapter — LOCK_EX writes, link handling, MIME detection
│   │   └── ...                                        # Tests and visibility converter
│   ├── InMemory/
│   │   ├── InMemoryFilesystemAdapter.php              # In-memory adapter — hash map of InMemoryFile objects
│   │   ├── InMemoryFile.php                           # In-memory file representation — contents, visibility, timestamp
│   │   └── StaticInMemoryAdapterRegistry.php          # Shared in-memory state across instances
│   ├── AwsS3V3/
│   │   ├── AwsS3V3Adapter.php                         # AWS S3 adapter via AWS SDK v3
│   │   ├── PortableVisibilityConverter.php            # S3 ACL-based visibility conversion
│   │   └── VisibilityConverter.php                    # Interface for S3 visibility mapping
│   ├── AsyncAwsS3/                                    # Async AWS S3 adapter
│   ├── AzureBlobStorage/                              # Azure Blob Storage adapter
│   ├── GoogleCloudStorage/                            # Google Cloud Storage adapter
│   ├── Ftp/                                           # FTP adapter
│   ├── PhpseclibV3/                                   # SFTP adapter via phpseclib v3
│   ├── WebDAV/                                        # WebDAV adapter
│   ├── GridFS/                                        # MongoDB GridFS adapter
│   ├── ZipArchive/                                    # ZIP archive adapter
│   ├── ReadOnly/                                      # Read-only adapter decorator
│   ├── PathPrefixing/                                 # Path-prefixing adapter decorator
│   ├── UnixVisibility/
│   │   ├── PortableVisibilityConverter.php            # Unix permission-based visibility conversion
│   │   └── VisibilityConverter.php                    # Interface for Unix visibility mapping
│   ├── UrlGeneration/
│   │   ├── PublicUrlGenerator.php                     # Interface for public URL generation
│   │   ├── TemporaryUrlGenerator.php                  # Interface for temporary/signed URL generation
│   │   ├── PrefixPublicUrlGenerator.php               # Prefix-based public URL generation
│   │   └── ShardedPrefixPublicUrlGenerator.php        # Sharded prefix URL generation for CDNs
│   └── AdapterTestUtilities/                          # Shared test utilities for adapter conformance testing
└── test_files/                                        # Test fixture files
```

## Scale indicators

- ~182 PHP files (source + tests combined in src/)
- ~21,300 lines total
- 12+ adapter implementations spanning local, cloud, and remote filesystems
- Dependencies: `league/mime-type-detection`

---

## Tasks

30 tasks (10 narrow, 10 medium, 10 wide).

## Narrow

### N1: Fix WhitespacePathNormalizer not rejecting paths with trailing dots on Windows

In `src/WhitespacePathNormalizer.php`, `normalizePath` strips
directory traversal (`..`) and empty segments but does not reject
paths with trailing dots (e.g., `"file."`). On Windows, trailing
dots are silently stripped by the OS, causing `file.` to resolve
to `file`, leading to unintended overwrites. Fix `normalizePath`
to reject paths containing segments with trailing dots by throwing
`CorruptedPathDetected`.

### N2: Fix MountManager.copy not working across different filesystem mounts

In `src/MountManager.php`, the `copy` method resolves source and
destination filesystems via `determineFilesystemAndPath`. When source
and destination are on different mounts, it calls `copy` on the
source filesystem with the destination path, which fails because the
source adapter cannot access the destination. Fix `copy` to detect
cross-mount operations and fall back to read-then-write using
`readStream` from source and `writeStream` to destination. Also
add a `cross-mount` test group in `phpunit.xml.dist` so these
integration tests can be run in isolation.

### N3: Fix Filesystem.publicUrl not falling back to adapter when PublicUrlGenerator is null

In `src/Filesystem.php`, the `publicUrl` method checks for a
configured `PublicUrlGenerator` and throws
`UnableToGeneratePublicUrl` when none is set. However, some adapters
(e.g., `AwsS3V3Adapter`) implement `PublicUrlGenerator` themselves.
Fix `publicUrl` to check if the adapter implements
`PublicUrlGenerator` as a fallback before throwing.

### N4: Fix DirectoryListing.filter not preserving generator laziness

In `src/DirectoryListing.php`, the `filter` method applies a
callable to filter `StorageAttributes` entries. The implementation
collects all entries into an array before filtering, losing the
lazy evaluation benefit of the generator-based listing. Fix `filter`
to return a new `DirectoryListing` wrapping a `Generator` that
applies the filter lazily without materializing the full list.

### N5: Fix PathPrefixer not handling empty prefix correctly for root-level operations

In `src/PathPrefixer.php`, when an empty string prefix is configured,
`prefixPath` prepends an empty string, resulting in paths starting
with `/` (e.g., `/file.txt` instead of `file.txt`). Fix
`prefixPath` to return the path unchanged when the prefix is empty,
and `stripPrefix` to handle the corresponding case.

### N6: Fix Config.extend not performing deep merge for nested arrays

In `src/Config.php`, `extend` uses `array_merge` which performs a
shallow merge. When both the base config and the extension contain
nested arrays for the same key (e.g., `['metadata' => ['a' => 1]]`
extended with `['metadata' => ['b' => 2]]`), the nested array is
replaced instead of merged. Fix `extend` to recursively merge
nested associative arrays while preserving numeric array behavior.

### N7: Fix InMemoryFilesystemAdapter.move not preserving file visibility

In `src/InMemory/InMemoryFilesystemAdapter.php`, the `move` method
internally calls `copy` followed by `delete`. The `copy`
implementation creates a new `InMemoryFile` with the default
visibility instead of preserving the source file's visibility. Fix
`move` (and `copy`) to carry over the source file's visibility to
the destination.

### N8: Fix Filesystem.checksum not catching adapter exceptions when falling back to stream

In `src/Filesystem.php`, the `checksum` method first tries the
adapter's native checksum (if the adapter implements `ChecksumProvider`).
If the adapter throws `ChecksumAlgoIsNotSupported`, it falls back
to computing from a stream. However, if the adapter throws a
different `FilesystemException` (e.g., `UnableToReadFile`), the
exception is caught by a broad `Throwable` catch that wraps it in
`UnableToProvideChecksum`, losing the original exception context.
Fix the catch to only catch `ChecksumAlgoIsNotSupported` for the
fallback and re-throw other exceptions.

### N9: Fix FileAttributes.jsonSerialize losing extra metadata fields

In `src/FileAttributes.php`, the `jsonSerialize` method returns a
fixed set of fields (`path`, `visibility`, `fileSize`, etc.) but
does not include the `extraMetadata` array that adapters can attach.
Fix `jsonSerialize` to include `extraMetadata` in the serialized
output when it is non-empty.

### N10: Fix MountManager.listContents not supporting deep listing across mounts

In `src/MountManager.php`, `listContents(string $location, bool $deep)`
resolves a single filesystem for the location and delegates the
listing. When called with a root-level path and `deep = true`, it
does not aggregate listings across all mounted filesystems. Fix
`listContents` to iterate over all mounts when the location is the
root or matches multiple mount points, merging results with mount
prefix prepended.

### N11: Fix `docker-compose.yml` WebDAV service not exposing TLS port for HTTPS testing

The `docker-compose.yml` file configures a `sabredav` service on port
4040 and a `bytemark/webdav` service on port 4080, both over plain
HTTP. The `WebDAV/WebDAVAdapter` supports HTTPS connections, but
there is no TLS-enabled service for integration testing. Add a TLS
proxy service to `docker-compose.yml` using `nginx:alpine` with a
self-signed certificate that proxies to the existing WebDAV service.
Update `phpunit.xml.dist` to add environment variables
`FLYSYSTEM_TEST_WEBDAV_TLS_HOST` and `FLYSYSTEM_TEST_WEBDAV_TLS_PORT`
for the new service, and update `.dockerignore` to exclude test
certificates from non-test Docker contexts.

## Medium

### M1: Implement filesystem event/hook system for write and delete operations

Add `FilesystemEventDispatcher` that wraps a `FilesystemOperator` and
dispatches events (before/after write, delete, copy, move). Requires
a new `src/EventDispatcher/FilesystemEventDispatcher.php` decorator,
event classes (`BeforeWrite`, `AfterWrite`, `BeforeDelete`, etc.) in
`src/EventDispatcher/Events/`, `EventListener` interface, dispatcher
integration with `Filesystem`, and tests.

### M2: Add filesystem diff utility for comparing two filesystem instances

Implement `FilesystemDiff` that compares two `FilesystemOperator`
instances and returns differences (added, removed, modified files).
Requires `src/Diff/FilesystemDiff.php`, `DiffResult.php` with
change type and path, comparison by content hash and metadata,
filtering by path patterns, `DirectoryListing`-based traversal,
and tests.

### M3: Implement filesystem quota enforcement adapter decorator

Add `QuotaAdapter` that wraps any `FilesystemAdapter` with storage
quota enforcement: tracks total bytes used, rejects writes exceeding
quota. Requires `src/Quota/QuotaAdapter.php`, `QuotaExceeded.php`
exception, quota tracking via file listing, atomic quota check-and-write,
configurable per-file and total limits, and tests with `InMemoryFilesystemAdapter`.

### M4: Add versioned file storage with history tracking

Implement `VersionedFilesystem` that maintains file revision history:
write creates a new version, read returns latest, `readVersion(path, version)`
retrieves specific versions. Requires `src/Versioning/VersionedFilesystem.php`,
`FileVersion.php` metadata class, version metadata storage strategy,
maximum version count configuration, version pruning, and tests.

### M5: Implement encrypted filesystem adapter decorator

Add `EncryptedAdapter` that encrypts file contents on write and
decrypts on read using AES-256-GCM. Requires
`src/Encryption/EncryptedAdapter.php`, `EncryptionProvider.php`
interface, `SodiumEncryptionProvider.php` implementation,
key management abstraction, IV/nonce generation, metadata
preservation through encryption, stream encryption support, and tests.

### M6: Add filesystem migration utility for transferring between adapters

Implement `FilesystemMigrator` that transfers all files from one
adapter to another with progress reporting. Requires
`src/Migration/FilesystemMigrator.php`, `MigrationProgress.php`
for progress tracking, batch transfer with configurable concurrency,
integrity verification via checksums, resume-on-failure with offset
tracking, dry-run mode, and tests. Also update `composer.json` to
add `ext-pcntl` as a suggested dependency for concurrent transfers,
and add the `Migration/` directory to the `phpstan.neon` scan paths
(currently several adapter directories are excluded from analysis).

### M7: Implement read-through caching adapter with TTL

Add `CachingAdapter` that caches reads from a slow adapter into a
fast adapter with configurable TTL. Requires
`src/Caching/CachingAdapter.php`, cache invalidation on writes,
TTL-based cache expiry tracking, metadata caching (MIME type, size),
cache warming for directory listings, configurable cache key
strategy, and tests with `InMemoryFilesystemAdapter` as cache.

### M8: Add streaming upload support with chunked writes

Implement `ChunkedWriter` that splits large files into chunks for
adapters that have upload size limits. Requires
`src/Chunking/ChunkedWriter.php`, configurable chunk size,
chunk reassembly on read, chunk naming convention, integrity
verification across chunks, atomic commit after all chunks,
and tests.

### M9: Implement access log adapter decorator for audit trails

Add `AuditAdapter` that logs all filesystem operations (who, what,
when, path) to a configurable log sink. Requires
`src/Audit/AuditAdapter.php`, `AuditEntry.php` value object,
`AuditLogger.php` interface with file-based default implementation,
configurable operation filtering, structured log format (JSON),
and tests.

### M10: Add file locking mechanism for concurrent access control

Implement `LockingAdapter` that provides advisory file locks for
concurrent write prevention. Requires `src/Locking/LockingAdapter.php`,
`LockProvider.php` interface, `FlockLockProvider.php` for local
filesystem, `LockException.php`, lock timeout support, automatic
lock release on scope exit, and tests.

### M11: Add subsplit CI validation and adapter test suite coverage

The `config.subsplit-publish.json` maps adapter directories to
downstream Git repositories for subtree splitting, but there is no
CI check that validates the directory entries still exist or that
new adapter directories have been added. Add a PHPUnit test or CI
step that reads `config.subsplit-publish.json` and verifies each
`directory` exists under `src/`. Update `composer.json` to add a
`scripts.validate-subsplits` command that runs this check. Update
`phpunit.xml.dist` to add per-adapter test suite entries (matching
the subsplit names) so individual adapters can be tested with
`--testsuite=aws-s3-v3` instead of running the entire suite. Update
`CHANGELOG.md` to document the new per-adapter test suite names.

## Wide

### W1: Implement reactive filesystem watcher for file change monitoring

Add a filesystem watcher that detects file changes in near-real-time:
polling-based and inotify-based backends, change events for
create/modify/delete, directory watching, debouncing, and event
callback registration. Changes span `src/Watcher/FilesystemWatcher.php`,
`src/Watcher/PollingWatcher.php`, `src/Watcher/InotifyWatcher.php`,
`src/Watcher/ChangeEvent.php`, `src/Watcher/WatcherFactory.php`,
debounce logic, adapter integration for cloud change feeds, and tests.

### W2: Implement S3-compatible filesystem server for testing

Build an S3-compatible HTTP server that serves files from any
Flysystem adapter, enabling integration testing without AWS. Changes
span `src/S3Server/Server.php`, `src/S3Server/RequestHandler.php`,
`src/S3Server/Router.php`, S3 API endpoint implementations (GET,
PUT, DELETE, LIST, HEAD, COPY), authentication simulation,
multipart upload support, response XML formatting, and integration
tests with `AwsS3V3Adapter`.

### W3: Implement multi-cloud synchronization service

Build a synchronization engine that keeps files in sync across
multiple filesystem adapters: conflict detection, resolution
strategies (newest-wins, manual), bidirectional sync, and change
tracking. Changes span `src/Sync/SyncEngine.php`,
`src/Sync/ChangeTracker.php`, `src/Sync/ConflictResolver.php`,
`src/Sync/SyncState.php` for state persistence,
`src/Sync/Strategy/` for resolution strategies, delta-based sync
using checksums, and integration tests.

### W4: Implement virtual filesystem with union mount support

Build a union filesystem that layers multiple adapters with
configurable precedence: reads search layers top-down, writes go to
a specific layer, whiteout files for delete propagation. Changes
span `src/Union/UnionFilesystem.php`, `src/Union/Layer.php`,
`src/Union/WhiteoutManager.php`, `src/Union/MergedListing.php`,
copy-on-write semantics, layer priority configuration,
`UnionFilesystemBuilder`, and tests.

### W5: Implement full-text search index over filesystem contents

Build a search system that indexes file contents across any adapter:
inverted index, tokenization, stemming, query parsing, and ranked
results. Changes span `src/Search/SearchIndex.php`,
`src/Search/Indexer.php`, `src/Search/Tokenizer.php`,
`src/Search/InvertedIndex.php`, `src/Search/Query.php`,
`src/Search/SearchResult.php`, incremental index updates on
write/delete, serializable index storage, and tests.

### W6: Implement filesystem-backed key-value store

Build `KeyValueStore` on top of Flysystem: keys mapped to file
paths, atomic put/get/delete, namespacing, TTL with expiry sweeping,
batch operations, and serialization formats (JSON, PHP serialize).
Changes span `src/KeyValue/KeyValueStore.php`,
`src/KeyValue/Serializer.php`, `src/KeyValue/ExpiryManager.php`,
`src/KeyValue/Namespace.php`, atomic write via temporary files,
listing as key enumeration, and tests.

### W7: Implement content-addressable storage adapter

Build a CAS adapter that stores files by content hash (SHA-256):
deduplication, immutable storage, garbage collection for unreferenced
blobs, reference counting, and metadata mapping. Changes span
`src/CAS/ContentAddressableAdapter.php`,
`src/CAS/ReferenceTracker.php`, `src/CAS/GarbageCollector.php`,
`src/CAS/HashCalculator.php`, blob-to-name mapping index,
streaming hash computation, and tests.

### W8: Implement filesystem metrics and monitoring integration

Build a metrics collection layer: operation counters, latency
histograms, bytes read/written, error rates, and export to
StatsD/Prometheus. Changes span `src/Metrics/MetricsAdapter.php`,
`src/Metrics/MetricsCollector.php`, `src/Metrics/Counter.php`,
`src/Metrics/Histogram.php`, `src/Metrics/StatsDExporter.php`,
`src/Metrics/PrometheusExporter.php`, per-adapter labeling,
and tests.

### W9: Implement filesystem snapshot and restore for backup

Build a snapshot system that captures the complete state of a
filesystem at a point in time and restores it: incremental snapshots
using checksums, snapshot metadata, restore to same or different
adapter. Changes span `src/Snapshot/SnapshotManager.php`,
`src/Snapshot/SnapshotManifest.php`, `src/Snapshot/IncrementalDiff.php`,
`src/Snapshot/SnapshotRestorer.php`, compressed archive format,
progress reporting, and tests.

### W10: Implement WebDAV PROPFIND/PROPPATCH compliance for shared editing

Build full WebDAV property support for the WebDAV adapter:
PROPFIND multi-status parsing, PROPPATCH custom properties,
dead properties storage, live property computation, locking
support (LOCK/UNLOCK), and conflict detection. Changes span
`src/WebDAV/PropertyManager.php`, `src/WebDAV/LockManager.php`,
`src/WebDAV/PropfindParser.php`, `src/WebDAV/ProppatchHandler.php`,
`src/WebDAV/WebDAVAdapter.php` extensions, XML response building,
and integration tests with a real WebDAV server. Also update
`docker-compose.yml` to add a dedicated WebDAV service with
PROPPATCH support enabled (the existing `bytemark/webdav` image has
limited property support), and update
`.github/workflows/quality-assurance.yml` to start the WebDAV
Docker services as a prerequisite for the integration test job.

### W11: Overhaul testing infrastructure and CI for adapter-parallel execution

The current CI workflow in `.github/workflows/quality-assurance.yml`
runs all adapter tests sequentially in a single job, leading to long
CI times. Restructure the workflow to run adapter-specific test
suites in parallel jobs, each starting only the Docker services it
needs from `docker-compose.yml`. Add a `docker-compose.override.yml`
for local development that starts all services. Update
`docker-compose.yml` to add a Google Cloud Storage emulator service
(`fsouza/fake-gcs-server`) and an Azure Azurite service
(`mcr.microsoft.com/azure-storage/azurite`) for cloud adapter
testing without credentials. Update `phpstan.neon` to remove the
blanket `excludePaths` for adapter directories and instead enable
analysis for all adapters at level 6, resolving or baselining the
new errors in `phpstan-baseline.neon`. Update `readme.md` with a
test matrix table showing which Docker services each adapter
requires, and update `CODE_OF_CONDUCT.md` to reference the
latest Contributor Covenant version (2.1).
