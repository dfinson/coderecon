# composer/composer

| Field | Value |
|-------|-------|
| **URL** | https://github.com/composer/composer |
| **License** | MIT |
| **Language** | PHP |
| **Scale** | Medium (multi-module project) |
| **Category** | Dependency manager |

## Why this repo

- **Multi-module with clear boundaries**: Dependency resolution (SAT solver),
  package repository sources (Packagist, VCS, path), autoload generation,
  installer plugins, lock file management, script execution, platform
  requirements — each a distinct subsystem requiring navigation between them.
- **Well-structured**: Source under `src/Composer/` with clear namespace
  split. Dependency solver, repository layer, installer, downloader, and
  autoload generator are separated by concern.
- **Rich history**: 10K+ commits, foundational to the PHP ecosystem. Every
  PHP project uses it. PRs cover dependency resolution edge cases, repository
  source handling, and performance improvements.
- **Permissive**: MIT license.

## Structure overview

```
src/Composer/
├── Composer.php              # Main Composer class
├── Factory.php               # Factory for creating Composer instances
├── Command/                  # CLI commands (require, install, update, etc.)
├── DependencyResolver/       # SAT-based dependency resolution
│   ├── Solver.php            # Constraint solver
│   ├── Pool.php              # Package pool
│   ├── Request.php           # Resolution request
│   └── Rule.php              # Solver rules
├── Repository/               # Package sources
│   ├── ComposerRepository.php    # Packagist/Composer repos
│   ├── VcsRepository.php         # Git/SVN/Hg repos
│   ├── PathRepository.php        # Local path repos
│   └── RepositoryManager.php     # Repository orchestration
├── Installer/                # Package installation
├── Downloader/               # Package downloading (zip, git, etc.)
├── Autoload/                 # Autoload file generation
│   ├── AutoloadGenerator.php
│   └── ClassMapGenerator.php
├── Package/                  # Package model
├── Plugin/                   # Plugin system
└── Script/                   # Script execution
```

## Scale indicators

- ~200 PHP source files
- ~50K lines of code
- 2-3 levels of namespace nesting
- Clear subsystem boundaries
