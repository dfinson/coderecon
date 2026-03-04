# apple/swift-package-manager

| Field | Value |
|-------|-------|
| **URL** | https://github.com/swiftlang/swift-package-manager |
| **License** | Apache-2.0 |
| **Language** | Swift |
| **Scale** | Large (multi-team project) |
| **Category** | Build system and package manager |

## Why this repo

- **No single developer knows it all**: Package manifest parsing (Package.swift
  DSL), dependency resolution (pubgrub algorithm), build system (llbuild
  integration), workspace management, registry client, plugin system, test
  runner integration, cross-compilation, binary targets — each a substantial
  subsystem with deep internals.
- **Well-structured**: Multiple Swift packages in the workspace (`PackageModel`,
  `PackageGraph`, `PackageLoading`, `Build`, `Workspace`, `SourceControl`,
  `PackageRegistry`). Each package has clear responsibility boundaries.
- **Rich history**: 8K+ commits, maintained by Apple/Swift core team. Dense
  PR history covering dependency resolution, build planning, registry support,
  and platform-specific behavior.
- **Permissive**: Apache-2.0.

## Structure overview

```
Sources/
├── PackageModel/            # Package manifest data model
│   ├── Package.swift        # Package type
│   ├── Target.swift         # Target definitions
│   ├── Product.swift        # Product definitions
│   └── Manifest/            # Manifest loading and parsing
├── PackageGraph/            # Dependency graph
│   ├── Resolution/          # pubgrub dependency resolver
│   └── ModulesGraph.swift   # Resolved module graph
├── PackageLoading/          # Package.swift evaluation
├── Workspace/               # Workspace management
│   ├── Workspace.swift      # Main workspace type
│   ├── ResolverPrecomputationProvider.swift
│   └── ManagedDependency.swift
├── Build/                   # Build planning and execution
│   ├── BuildPlan/           # Build plan generation
│   ├── BuildOperation.swift # llbuild integration
│   └── SwiftCompilerOutputParser.swift
├── SourceControl/           # Git operations
├── PackageRegistry/         # Swift package registry client
├── PackagePlugin/           # Plugin infrastructure
└── Commands/                # CLI commands (build, test, run, resolve)
```

## Scale indicators

- ~500 Swift source files
- ~150K+ lines of code
- Deep module hierarchies (3-5 levels)
- Cross-cutting workspace, resolution, and build concerns

## Notes

- Repo recently moved from `apple/swift-package-manager` to
  `swiftlang/swift-package-manager`. Use the current canonical URL.
