#!/usr/bin/env bash
# Clone all 30 ranking training repos, remove origin, and run cpl init.
#
# Usage:
#   cd ranking && bash clone_repos.sh
#
# Prerequisites:
#   - git
#   - cpl CLI on PATH (codeplane)
#
# Each repo is cloned as a shallow copy (--depth=1) to save space.
# The remote is removed so no accidental pushes occur.
# cpl init runs blocking per repo to build the index.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLONES_DIR="$SCRIPT_DIR/clones"

mkdir -p "$CLONES_DIR"

# Repo URLs extracted from ranking/repos/*.md
REPOS=(
  https://github.com/fmtlib/fmt
  https://github.com/google/googletest
  https://github.com/opencv/opencv
  https://github.com/dotnet/efcore
  https://github.com/Humanizr/Humanizer
  https://github.com/JamesNK/Newtonsoft.Json
  https://github.com/charmbracelet/bubbletea
  https://github.com/caddyserver/caddy
  https://github.com/go-gitea/gitea
  https://github.com/google/gson
  https://github.com/square/okhttp
  https://github.com/spring-projects/spring-boot
  https://github.com/composer/composer
  https://github.com/guzzle/guzzle
  https://github.com/laravel/framework
  https://github.com/django/django
  https://github.com/fastapi/fastapi
  https://github.com/encode/httpx
  https://github.com/jekyll/jekyll
  https://github.com/rack/rack
  https://github.com/rails/rails
  https://github.com/BurntSushi/ripgrep
  https://github.com/serde-rs/serde
  https://github.com/tokio-rs/tokio
  https://github.com/Alamofire/Alamofire
  https://github.com/swiftlang/swift-package-manager
  https://github.com/vapor/vapor
  https://github.com/mermaid-js/mermaid
  https://github.com/nestjs/nest
  https://github.com/colinhacks/zod
)

total=${#REPOS[@]}
i=0

for url in "${REPOS[@]}"; do
  i=$((i + 1))
  name=$(basename "$url")
  dest="$CLONES_DIR/$name"

  echo ""
  echo "=== [$i/$total] $name ==="

  # Skip if already cloned
  if [[ -d "$dest/.git" ]]; then
    echo "  already cloned, skipping git clone"
  else
    echo "  cloning $url ..."
    git clone --depth=1 "$url" "$dest"
  fi

  # Remove remote to prevent accidental pushes
  if git -C "$dest" remote get-url origin &>/dev/null; then
    echo "  removing origin remote"
    git -C "$dest" remote remove origin
  fi

  # Run cpl init (blocking)
  echo "  running cpl init ..."
  if (cd "$dest" && cpl init); then
    echo "  cpl init complete"
  else
    echo "  WARNING: cpl init failed for $name (exit $?)"
  fi
done

echo ""
echo "=== Done: $total repos cloned and indexed ==="
