"""Clone repos for Recon Lab — ported from clone_repos.sh.

Repo manifest is declarative. Each entry is (url, commit_sha).
The clone process is idempotent: re-running skips already-done steps.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

# ── Repo manifest ────────────────────────────────────────────────
# Each tuple: (github_url, pinned_commit_sha)

RANKER_GATE: list[tuple[str, str]] = [
    ("https://github.com/fmtlib/fmt", "696dd855fc82b582ad6da2e732a3c57aa3e56dff"),
    ("https://github.com/google/googletest", "0299475a381902f1c81dc8da388edc4b3dea65b6"),
    ("https://github.com/opencv/opencv", "fe160f3eed3ec0344baff4bfb6a0771d01b5882d"),
    ("https://github.com/dotnet/efcore", "8e7f5641775281a0607a6d76077e743965c86761"),
    ("https://github.com/Humanizr/Humanizer", "5054735ad364a56d7c51345cc322ec8fbc65af99"),
    ("https://github.com/JamesNK/Newtonsoft.Json", "e1cf98c5792302e814b7c5a083c36cd8f139d5fe"),
    ("https://github.com/charmbracelet/bubbletea", "8cc4f1a832aa6f268e0b7e97a31530c5e961360f"),
    ("https://github.com/caddyserver/caddy", "a118b959e27f6c09ab077e90bd60accea529eb28"),
    ("https://github.com/go-gitea/gitea", "5d87bb3d4566e71b791a8114bfc9e25c037ab5fe"),
    ("https://github.com/google/gson", "990f1377e2e21d15e280e83190132e2f6baffae2"),
    ("https://github.com/square/okhttp", "4f843e44998e52caf60b36a7abd72da421c326d1"),
    ("https://github.com/spring-projects/spring-boot", "fd18d6ba968dbce31a793edaf62a39ae0b5ba718"),
    ("https://github.com/composer/composer", "213661a06ab4b080c03334c354b08430af0bb108"),
    ("https://github.com/guzzle/guzzle", "1ef0adc83863b51dae427f1f64b1b5002f0bf911"),
    ("https://github.com/laravel/framework", "bddeb4a5cc576202723ffcfe607260d86a05aee2"),
    ("https://github.com/django/django", "09b7e84b79073e915ee74a2941ba82dad1e8918a"),
    ("https://github.com/fastapi/fastapi", "da58ab04cfcbeb0219c1da9f5f67807de10b17fb"),
    ("https://github.com/encode/httpx", "5201e3557257fc107492114435cda52faf6c8c0e"),
    ("https://github.com/jekyll/jekyll", "491d4737611298a54d82c91118a40563a00d485f"),
    ("https://github.com/rack/rack", "1fd28e537f7c8a11e28bae92d368a11e8dafaf35"),
    ("https://github.com/rails/rails", "d9fa3a2883ed87f8afdaafc28fe919e280911835"),
    ("https://github.com/BurntSushi/ripgrep", "0884e89f38b7b756b58aed8318c2aa05de0a750c"),
    ("https://github.com/serde-rs/serde", "3fd6b4840a8c7dcc34284f8d478c744c4f78ebfb"),
    ("https://github.com/tokio-rs/tokio", "6a44775e078ad518923dd10f922a7f210364dd64"),
    ("https://github.com/Alamofire/Alamofire", "14dc760dee02fcd28c42f3d8fd760ebfbae6ce0d"),
    ("https://github.com/swiftlang/swift-package-manager", "b908844f8e335dbc36735eea71eb0fc30baffb66"),
    ("https://github.com/vapor/vapor", "f66f400e54277eaacc319b38225b32d72586235b"),
    ("https://github.com/mermaid-js/mermaid", "6e40ff272949ef2eec09c6efd42b6284b3d51148"),
    ("https://github.com/nestjs/nest", "3de9ef6c92531869a0537e25ce79d83d32d9337f"),
    ("https://github.com/colinhacks/zod", "58498da33b1cd110e15fed3a83733f24d41a6bb9"),
]

CUTOFF: list[tuple[str, str]] = [
    ("https://github.com/nlohmann/json", "0d92c01619b04aab4d1f52bdc5ec6a25e62195fd"),
    ("https://github.com/gabime/spdlog", "355676231ecc8054df12bee275b2193eeeef5ccb"),
    ("https://github.com/DapperLib/Dapper", "9769c710c1b7a73b5233548b6f5e0106f167b2af"),
    ("https://github.com/jbogard/MediatR", "6a1bf54413124866b5c8647ce42eb5901c93b7b9"),
    ("https://github.com/App-vNext/Polly", "7ddb44ec982dd37533790bb938e8af681292b0e7"),
    ("https://github.com/go-chi/chi", "4eff323f8e26176988c7f5dcb0357ed21d1caae7"),
    ("https://github.com/spf13/cobra", "67d04b958aa39de087ebfcb4b5435bfdde822813"),
    ("https://github.com/gorilla/mux", "d01bcc7473e6d2352174958219e4721435102e52"),
    ("https://github.com/assertj/assertj", "9a79aeb6f27683917012432650d6af4fc0572189"),
    ("https://github.com/google/guava", "79d3be798b9b631efe8814e4e5ee2d1f02b25241"),
    ("https://github.com/FasterXML/jackson-databind", "3116d07e791128ca034bd06c909706399be1be14"),
    ("https://github.com/thephpleague/flysystem", "0faf66a23e934a90bee5d24e7791264fafe5afaa"),
    ("https://github.com/Seldaek/monolog", "976f90a093b015be5f3fbc7f2479bb2740935243"),
    ("https://github.com/sebastianbergmann/phpunit", "18e05b1ae14f6b93203132545d2f9094213b5126"),
    ("https://github.com/pallets/click", "e49914d65bc0dba44dde864b5c9adcad378c55ad"),
    ("https://github.com/pallets/flask", "a0f7083b3bd9e4a7088b034eaf908f082c2b9246"),
    ("https://github.com/pallets/jinja", "5c574d2d6d11708c6a6d4d23f5b786819895c8e0"),
    ("https://github.com/marshmallow-code/marshmallow", "4c1dc98631114e94d9a753ffdc82d4961b5dff0a"),
    ("https://github.com/Textualize/rich", "e6719c48f3b812ab369b10217b79fef56dcfcc03"),
    ("https://github.com/fastapi/typer", "ddef2291832331b1a2c5e2931f57ab7e5a4d133b"),
    ("https://github.com/heartcombo/devise", "ecdd02b2991e26af67c017de2df5956d21be891a"),
    ("https://github.com/lostisland/faraday", "2de6beec29f571051b6e010a8ad745fb667445ca"),
    ("https://github.com/sidekiq/sidekiq", "60bf70dae2792729b0fb1ad4a80a13584b52d141"),
    ("https://github.com/clap-rs/clap", "338eb713cb550c5c1a91bce160aa43c2206c71a4"),
    ("https://github.com/crossbeam-rs/crossbeam", "bc5f78cb544fa03a40474e878a84b3cdd640f2fa"),
    ("https://github.com/seanmonstar/reqwest", "77e44d769fb2bf909bc6051eb6556df1a39878b1"),
    ("https://github.com/onevcat/Kingfisher", "f24c47b5d78353836faae8f2813bc67f291868da"),
    ("https://github.com/Moya/Moya", "67fece7bb6f678a3bb77f732f94c1f3e99cc06fe"),
    ("https://github.com/SnapKit/SnapKit", "72d8c252b6715debfff3527e27fa18ecf483026f"),
    ("https://github.com/date-fns/date-fns", "ec4d9f88d32059967196605435e929de880c4e3c"),
    ("https://github.com/sindresorhus/execa", "b016bf41352cea7e5bc470ce873ed7d96c1cd02f"),
    ("https://github.com/trpc/trpc", "1e7e6986101ca60f9d48dff4480fd32e6bf5b065"),
    ("https://github.com/psf/requests", "0e4ae38f0c93d4f92a96c774bd52c069d12a4798"),
    ("https://github.com/fastify/fastify", "b61c362cc9fba35e7e060a71284154e4f86d54f4"),
    ("https://github.com/labstack/echo", "1753170a74959596a69735c553f3fe5a4bd07715"),
    ("https://github.com/hyperium/hyper", "8ba900853b0f619b165e8530fc8c310bc13e056b"),
    ("https://github.com/square/retrofit", "4a60aef50e8cc2a323ea6b095b35abaa696d2c67"),
    ("https://github.com/FluentValidation/FluentValidation", "cc9917c3688d790f7a414b17d1e03ce337a4151c"),
    ("https://github.com/puma/puma", "a1b5b5e7e1b8d34b7d24964f668733299be930a2"),
    ("https://github.com/briannesbitt/Carbon", "72ee09e5ada27bd82d668ba30e877722251d8322"),
    ("https://github.com/airbnb/lottie-ios", "ea35e6a4ec7f443a2b0b69ae97cccf0e946ef4a2"),
    ("https://github.com/abseil/abseil-cpp", "60152322663f4e5a16cb71ca8c5f18c38a081265"),
    ("https://github.com/simdjson/simdjson", "262ddad0370cdfa656b61c388c52bad02697f8a1"),
    ("https://github.com/CLIUtils/CLI11", "b5fc53c89afc1f2c4fce49c5061d44dddcd41fc4"),
    ("https://github.com/pallets/markupsafe", "b2e4d9c7687be25695fffbe93a37622302b24fb1"),
    ("https://github.com/tj/commander.js", "8247364da749736570161e95682b07fc2d72497b"),
    ("https://github.com/apache/kafka", "55d1e3823b76590649cbe584cb906e330ca59fcc"),
    ("https://github.com/dotnet/aspnetcore", "0b12e6f18f1f22a103d09d254c8579f9d5d47422"),
]

EVAL: list[tuple[str, str]] = [
    ("https://github.com/catchorg/Catch2", "0ad9824bc644fbc4c0c1226340a04f0ded7919de"),
    ("https://github.com/grpc/grpc", "4a1e0fb594588a81e11187d0c34507a22a141e42"),
    ("https://github.com/AutoMapper/AutoMapper", "fc8cb3f3d6aafe35b77697fcd67639f7ae42fb70"),
    ("https://github.com/gofiber/fiber", "f36904db43e5499929f515332c8883f3ffada979"),
    ("https://github.com/gin-gonic/gin", "f3e1194361e27f0ed0f6666509d60f15af8b21d8"),
    ("https://github.com/projectlombok/lombok", "c2babe33dd54e326ef3d4ef1a0fd74eb4c9ffbd9"),
    ("https://github.com/mockito/mockito", "080ab96725a418f5a27eb3112d8ac7347f38afd8"),
    ("https://github.com/symfony/console", "d5795ce9e707206d9364c2cbec275cce6d4103ba"),
    ("https://github.com/celery/celery", "92c2606aab31a521b3e006e53ca729f2e586d1b6"),
    ("https://github.com/pydantic/pydantic", "fd9bfc8aefe91bf2e16c3464d2e3efba9df83fce"),
    ("https://github.com/sinatra/sinatra", "b2c6e2087d5e12c6bddcdfa8703ac94c7c4cfad7"),
    ("https://github.com/tokio-rs/axum", "39eda3c6be7ad34687dc50d9f11a3cb4c3f9521e"),
    ("https://github.com/ReactiveX/RxSwift", "c5a74e0378ab8fe8a8f16844fd438347d87e5641"),
    ("https://github.com/evanw/esbuild", "f566f21d943aa2a741e7e57b3f76425634b4a576"),
    ("https://github.com/vitest-dev/vitest", "e06f175cba08346bf0382c0b3e137a822bced280"),
    ("https://github.com/diesel-rs/diesel", "f5e93c0125694914dca6888ae09f8d84528353f6"),
    ("https://github.com/xunit/xunit", "63aad206c62c2db373a9420486aa8ebc1a3daad9"),
    ("https://github.com/filp/whoops", "67342bc807854844244f219fb74687fdf2f62e00"),
    ("https://github.com/pointfreeco/swift-composable-architecture", "68a0237ea65261f8694d131d33a36288cfd93863"),
]

REPO_SETS: dict[str, list[tuple[str, str]]] = {
    "ranker-gate": RANKER_GATE,
    "cutoff": CUTOFF,
    "eval": EVAL,
}

# Canonical mapping: repo_id → {set, clone_name}.
# repo_id is the task definition stem (e.g. "python-flask").
# clone_name is the GitHub repo name used as the directory under clones/{set}/.
REPO_MANIFEST: dict[str, dict[str, str]] = {
    "cpp-abseil": {"set": "cutoff", "clone_name": "abseil-cpp"},
    "cpp-catch2": {"set": "eval", "clone_name": "Catch2"},
    "cpp-cli11": {"set": "cutoff", "clone_name": "CLI11"},
    "cpp-fmt": {"set": "ranker-gate", "clone_name": "fmt"},
    "cpp-googletest": {"set": "ranker-gate", "clone_name": "googletest"},
    "cpp-grpc": {"set": "eval", "clone_name": "grpc"},
    "cpp-nlohmann-json": {"set": "cutoff", "clone_name": "json"},
    "cpp-opencv": {"set": "ranker-gate", "clone_name": "opencv"},
    "cpp-simdjson": {"set": "cutoff", "clone_name": "simdjson"},
    "cpp-spdlog": {"set": "cutoff", "clone_name": "spdlog"},
    "csharp-aspnetcore": {"set": "cutoff", "clone_name": "aspnetcore"},
    "csharp-automapper": {"set": "eval", "clone_name": "AutoMapper"},
    "csharp-dapper": {"set": "cutoff", "clone_name": "Dapper"},
    "csharp-efcore": {"set": "ranker-gate", "clone_name": "efcore"},
    "csharp-fluentvalidation": {"set": "cutoff", "clone_name": "FluentValidation"},
    "csharp-humanizer": {"set": "ranker-gate", "clone_name": "Humanizer"},
    "csharp-mediatr": {"set": "cutoff", "clone_name": "MediatR"},
    "csharp-newtonsoft-json": {"set": "ranker-gate", "clone_name": "Newtonsoft.Json"},
    "csharp-polly": {"set": "cutoff", "clone_name": "Polly"},
    "csharp-xunit": {"set": "eval", "clone_name": "xunit"},
    "go-bubbletea": {"set": "ranker-gate", "clone_name": "bubbletea"},
    "go-caddy": {"set": "ranker-gate", "clone_name": "caddy"},
    "go-chi": {"set": "cutoff", "clone_name": "chi"},
    "go-cobra": {"set": "cutoff", "clone_name": "cobra"},
    "go-echo": {"set": "cutoff", "clone_name": "echo"},
    "go-fiber": {"set": "eval", "clone_name": "fiber"},
    "go-gin": {"set": "eval", "clone_name": "gin"},
    "go-gitea": {"set": "ranker-gate", "clone_name": "gitea"},
    "go-mux": {"set": "cutoff", "clone_name": "mux"},
    "java-assertj": {"set": "cutoff", "clone_name": "assertj"},
    "java-gson": {"set": "ranker-gate", "clone_name": "gson"},
    "java-guava": {"set": "cutoff", "clone_name": "guava"},
    "java-jackson": {"set": "cutoff", "clone_name": "jackson-databind"},
    "java-kafka": {"set": "cutoff", "clone_name": "kafka"},
    "java-lombok": {"set": "eval", "clone_name": "lombok"},
    "java-mockito": {"set": "eval", "clone_name": "mockito"},
    "java-okhttp": {"set": "ranker-gate", "clone_name": "okhttp"},
    "java-retrofit": {"set": "cutoff", "clone_name": "retrofit"},
    "java-spring-boot": {"set": "ranker-gate", "clone_name": "spring-boot"},
    "php-carbon": {"set": "cutoff", "clone_name": "Carbon"},
    "php-composer": {"set": "ranker-gate", "clone_name": "composer"},
    "php-console": {"set": "eval", "clone_name": "console"},
    "php-flysystem": {"set": "cutoff", "clone_name": "flysystem"},
    "php-guzzle": {"set": "ranker-gate", "clone_name": "guzzle"},
    "php-laravel": {"set": "ranker-gate", "clone_name": "framework"},
    "php-monolog": {"set": "cutoff", "clone_name": "monolog"},
    "php-phpunit": {"set": "cutoff", "clone_name": "phpunit"},
    "php-whoops": {"set": "eval", "clone_name": "whoops"},
    "python-celery": {"set": "eval", "clone_name": "celery"},
    "python-click": {"set": "cutoff", "clone_name": "click"},
    "python-django": {"set": "ranker-gate", "clone_name": "django"},
    "python-fastapi": {"set": "ranker-gate", "clone_name": "fastapi"},
    "python-flask": {"set": "cutoff", "clone_name": "flask"},
    "python-httpx": {"set": "ranker-gate", "clone_name": "httpx"},
    "python-jinja": {"set": "cutoff", "clone_name": "jinja"},
    "python-markupsafe": {"set": "cutoff", "clone_name": "markupsafe"},
    "python-marshmallow": {"set": "cutoff", "clone_name": "marshmallow"},
    "python-pydantic": {"set": "eval", "clone_name": "pydantic"},
    "python-requests": {"set": "cutoff", "clone_name": "requests"},
    "python-rich": {"set": "cutoff", "clone_name": "rich"},
    "python-typer": {"set": "cutoff", "clone_name": "typer"},
    "ruby-devise": {"set": "cutoff", "clone_name": "devise"},
    "ruby-faraday": {"set": "cutoff", "clone_name": "faraday"},
    "ruby-jekyll": {"set": "ranker-gate", "clone_name": "jekyll"},
    "ruby-puma": {"set": "cutoff", "clone_name": "puma"},
    "ruby-rack": {"set": "ranker-gate", "clone_name": "rack"},
    "ruby-rails": {"set": "ranker-gate", "clone_name": "rails"},
    "ruby-sidekiq": {"set": "cutoff", "clone_name": "sidekiq"},
    "ruby-sinatra": {"set": "eval", "clone_name": "sinatra"},
    "rust-axum": {"set": "eval", "clone_name": "axum"},
    "rust-clap": {"set": "cutoff", "clone_name": "clap"},
    "rust-crossbeam": {"set": "cutoff", "clone_name": "crossbeam"},
    "rust-diesel": {"set": "eval", "clone_name": "diesel"},
    "rust-hyper": {"set": "cutoff", "clone_name": "hyper"},
    "rust-reqwest": {"set": "cutoff", "clone_name": "reqwest"},
    "rust-ripgrep": {"set": "ranker-gate", "clone_name": "ripgrep"},
    "rust-serde": {"set": "ranker-gate", "clone_name": "serde"},
    "rust-tokio": {"set": "ranker-gate", "clone_name": "tokio"},
    "swift-alamofire": {"set": "ranker-gate", "clone_name": "Alamofire"},
    "swift-composable-architecture": {"set": "eval", "clone_name": "swift-composable-architecture"},
    "swift-kingfisher": {"set": "cutoff", "clone_name": "Kingfisher"},
    "swift-lottie": {"set": "cutoff", "clone_name": "lottie-ios"},
    "swift-moya": {"set": "cutoff", "clone_name": "Moya"},
    "swift-package-manager": {"set": "ranker-gate", "clone_name": "swift-package-manager"},
    "swift-rxswift": {"set": "eval", "clone_name": "RxSwift"},
    "swift-snapkit": {"set": "cutoff", "clone_name": "SnapKit"},
    "swift-vapor": {"set": "ranker-gate", "clone_name": "vapor"},
    "typescript-commander": {"set": "cutoff", "clone_name": "commander.js"},
    "typescript-date-fns": {"set": "cutoff", "clone_name": "date-fns"},
    "typescript-esbuild": {"set": "eval", "clone_name": "esbuild"},
    "typescript-execa": {"set": "cutoff", "clone_name": "execa"},
    "typescript-fastify": {"set": "cutoff", "clone_name": "fastify"},
    "typescript-mermaid": {"set": "ranker-gate", "clone_name": "mermaid"},
    "typescript-nestjs": {"set": "ranker-gate", "clone_name": "nest"},
    "typescript-trpc": {"set": "cutoff", "clone_name": "trpc"},
    "typescript-vitest": {"set": "eval", "clone_name": "vitest"},
    "typescript-zod": {"set": "ranker-gate", "clone_name": "zod"},
}


def clone_dir_for(repo_id: str, clones_dir: Path) -> Path | None:
    """Resolve the clone directory for a repo_id."""
    entry = REPO_MANIFEST.get(repo_id)
    if entry is None:
        return None
    return clones_dir / entry["set"] / entry["clone_name"]


def repo_set_for(repo_id: str) -> str | None:
    """Return the set name for a repo_id."""
    entry = REPO_MANIFEST.get(repo_id)
    return entry["set"] if entry else None


def _repo_name(url: str) -> str:
    """Extract repo name from GitHub URL."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def _git(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check,
        capture_output=True, text=True,
    )


def _process_repo(
    url: str, commit: str, dest: Path, depth: int, dry_run: bool, verbose: bool,
) -> str:
    """Clone and pin a single repo. Returns status string."""
    name = dest.name

    if dry_run:
        click.echo(f"  [dry-run] would clone {name} → {dest}")
        return "dry-run"

    # Stage 1: Clone
    if (dest / ".git").is_dir():
        if verbose:
            click.echo(f"  {name}: clone exists")
    else:
        click.echo(f"  {name}: cloning...")
        _git(["clone", f"--depth={depth}", url, str(dest)])

    # Stage 2: Pin to exact commit
    result = _git(["rev-parse", "HEAD"], cwd=dest)
    current = result.stdout.strip()
    if current == commit:
        if verbose:
            click.echo(f"  {name}: pinned at {commit[:10]}")
    else:
        click.echo(f"  {name}: checking out {commit[:10]}...")
        _git(["fetch", "origin", commit, "--depth=1"], cwd=dest, check=False)
        _git(["checkout", commit], cwd=dest, check=False)

    # Stage 3: Remove origin
    result = _git(["remote", "get-url", "origin"], cwd=dest, check=False)
    if result.returncode == 0:
        _git(["remote", "remove", "origin"], cwd=dest)

    # Stage 4: recon init
    recon_dir = dest / ".recon"
    if recon_dir.is_dir() and (recon_dir / "index.db").is_file():
        if verbose:
            click.echo(f"  {name}: already indexed")
    else:
        from cpl_lab.index import _ensure_recon_models, _recon_init_cmd

        click.echo(f"  {name}: running recon init...")
        _ensure_recon_models()
        cmd, env = _recon_init_cmd(dest, reindex=recon_dir.is_dir())
        subprocess.run(cmd, env=env, check=False)

    # Commit any uncommitted artifacts
    result = _git(["status", "--porcelain"], cwd=dest)
    if result.stdout.strip():
        _git(["add", "-A"], cwd=dest)
        _git(["commit", "-m", "cpl init: add coderecon config files", "--no-verify", "-q"], cwd=dest)

    return "ok"


def run_clone(
    clones_dir: Path,
    repo_set: str = "all",
    jobs: int = 4,
    depth: int = 1,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Clone repos into the workspace clones directory."""
    sets = REPO_SETS if repo_set == "all" else {repo_set: REPO_SETS[repo_set]}

    total = sum(len(repos) for repos in sets.values())
    i = 0

    for set_name, repos in sets.items():
        set_dir = clones_dir / set_name
        set_dir.mkdir(parents=True, exist_ok=True)

        click.echo(f"\n=== {set_name} ({len(repos)} repos) ===")
        for url, commit in repos:
            i += 1
            name = _repo_name(url)
            dest = set_dir / name
            click.echo(f"\n[{i}/{total}] {set_name}/{name}")
            _process_repo(url, commit, dest, depth, dry_run, verbose)

    click.echo(f"\nDone: {total} repos processed")
