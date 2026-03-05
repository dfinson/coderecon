# go-gitea/gitea

| Field | Value |
|-------|-------|
| **URL** | https://github.com/go-gitea/gitea |
| **License** | MIT |
| **Language** | Go |
| **Scale** | Large (multi-team project) |
| **Category** | Self-hosted Git service |

## Why this repo

- **No single developer knows it all**: Git operations, issue tracker, pull
  requests, code review, CI/CD (Actions), package registry, wiki, OAuth/SSO,
  API (REST + GraphQL-like), webhooks, notifications, admin dashboard, LFS —
  each a deep subsystem with its own models, services, and routes.
- **Well-structured**: Clean Go package layout with `models/`, `services/`,
  `routers/`, `modules/` separation. Models further split by domain
  (issues, repo, user, packages, actions, etc.).
- **Rich history**: 15K+ commits, highly active PR flow, Hacktoberfest
  participation. Feature-rich with varied development patterns.
- **Permissive**: MIT license.

## Structure overview

```
.
├── models/              # Database models and queries
│   ├── issues/          # Issues, labels, milestones, comments
│   ├── repo/            # Repository metadata, stars, forks
│   ├── user/            # User accounts, orgs, teams
│   ├── packages/        # Package registry
│   ├── actions/         # CI/CD actions
│   └── git/             # Git-specific models
├── services/            # Business logic layer
│   ├── repository/      # Repo creation, deletion, transfer
│   ├── issue/           # Issue lifecycle
│   ├── pull/            # PR merge, review, conflicts
│   ├── auth/            # Authentication sources
│   └── webhook/         # Webhook delivery
├── routers/             # HTTP route handlers
│   ├── api/             # REST API v1
│   ├── web/             # Web UI routes
│   └── private/         # Internal API
├── modules/             # Shared infrastructure
│   ├── git/             # Git command interface
│   ├── markup/          # Markdown, org-mode rendering
│   ├── indexer/         # Search indexing (bleve/elasticsearch)
│   └── notification/    # Notification delivery
└── cmd/                 # CLI commands
```

## Scale indicators

- ~1,500 Go source files
- ~300K+ lines of code
- Deep module hierarchies (3-5 levels)
- Cross-cutting auth, notification, indexing concerns

---

## Tasks

10 tasks (3 narrow, 4 medium, 3 wide) for the self-hosted Git service.

## Narrow

### N1: Fix issue comment count not updated after bulk delete

When an admin bulk-deletes spam comments from an issue, the issue's
`num_comments` field is not decremented. The count only updates when
comments are deleted individually through the API or UI. Fix the bulk
delete operation to update the issue's comment count atomically.

### N2: Add "copy branch name" button to PR page

On the pull request page, there's no easy way to copy the source branch
name (for checking out locally). Add a clipboard copy button next to
the branch name display, similar to the existing "copy clone URL"
button on the repository page.

### N3: Fix webhook delivery retries using wrong HTTP method

When a webhook delivery fails and is retried, the retry uses POST
regardless of what method was configured for the webhook. The original
HTTP method is not persisted in the webhook delivery record. Fix the
retry logic to use the configured HTTP method.

## Medium

### M1: Implement issue templates with YAML frontmatter

Add support for issue templates defined in `.gitea/ISSUE_TEMPLATE/`
with YAML frontmatter for metadata (title prefix, labels, assignees,
projects). Support template types: bug report, feature request, and
custom. Render the template form in the "New Issue" UI with typed
form fields (text input, textarea, dropdown, checkboxes) defined
in the YAML. Validate required fields before submission.

### M2: Add code owners with required reviews

Implement a CODEOWNERS file (`.gitea/CODEOWNERS`) that maps file
patterns to responsible teams or users. When a PR modifies files
matching a pattern, automatically request reviews from the code
owners. Add a "code owner approval required" branch protection rule
that blocks merging until at least one code owner approves. Show
code owner status on the PR files changed view.

### M3: Implement repository archival with read-only mode

Add a "repository archive" feature that puts a repository into a
read-only state — no new pushes, PRs, or issues, but all existing
content remains accessible. Show an "archived" banner on the
repository page. Allow un-archiving by the repository owner. Update
the API, webhooks, and repository listing/search to respect archive
status.

### M4: Add CI/CD pipeline status badges

Implement status badge generation for Gitea Actions pipelines.
Provide badge URLs in Markdown and HTML formats showing the current
status (passing/failing/running) of a specified workflow. Support
branch-specific badges. Cache badge SVGs with appropriate TTL.
Add a "Get badge" UI in the repository Actions settings.

## Wide

### W1: Implement project boards with Kanban and table views

Add project management boards that span across repositories within
an organization. Support Kanban view (draggable cards between columns)
and table view (sortable/filterable list). Cards can be issues or
PRs from any repository in the organization. Support custom fields
(status, priority, size), swimlanes by assignee or label, and WIP
limits per column. Add API endpoints for all project board operations.

### W2: Add full-text code search with syntax-aware indexing

Implement a code search feature that indexes repository contents
with syntax awareness. Support exact matches, regex, and symbol
search. Use the existing bleve indexer infrastructure but add
language-specific tokenization (camelCase splitting, identifier
extraction). Show results with syntax-highlighted context. Support
searching across all repositories the user has access to. Add
search filters for language, file path, and repository.

### W3: Implement SSO with SAML 2.0 support

Add SAML 2.0 identity provider integration alongside the existing
OAuth2 and LDAP authentication sources. Implement SP-initiated and
IdP-initiated SSO flows. Support SAML attribute mapping to Gitea
user fields (email, username, groups → teams). Add JIT (just-in-time)
provisioning that creates Gitea accounts on first SAML login. Implement
single logout (SLO). Add admin UI for SAML configuration with
metadata import/export.

### N4: Fix milestone progress percentage rounding to zero for 1-2 issues

When a milestone has only 1 or 2 issues and one is closed, the progress
bar shows 0% because integer division truncates the percentage. Fix the
milestone progress calculation to use proper rounding for small issue
counts.

### N5: Add `Closes #N` auto-linking in commit messages for wiki pages

Commit messages on repository code support auto-closing issues via
`Closes #123`, but commits via the wiki editor do not trigger issue
auto-close. Fix the wiki commit handler to parse and process issue
close keywords.

### N6: Fix user profile heatmap showing wrong day for timezone edge cases

The contribution heatmap on user profiles shows the wrong day for
contributions made near midnight in timezones far from UTC. The
server-side date grouping uses UTC without adjusting for the user's
configured timezone. Fix the heatmap query to group by the user's
local date.

### N7: Fix repository transfer not updating webhook delivery URLs

When a repository is transferred to a new owner, webhooks configured
with repository-relative URLs still point to the old owner's URL path.
The transfer operation does not update webhook URLs. Fix repository
transfer to update webhook URLs or mark them for review.

### N8: Add "Copy commit SHA" button on the commit detail page

The commit detail page shows the full SHA but has no clipboard button.
Add a click-to-copy button next to the commit SHA, consistent with
the existing copy buttons on the repository clone URL and branch name.

### N9: Fix LFS file edit in web editor creating corrupt LFS pointer

When editing an LFS-tracked file through Gitea's web editor, the saved
content is written directly to the repository instead of going through
the LFS pipeline. The result is a non-LFS file that looks like a
corrupt LFS pointer. Fix the web editor to route LFS file saves
through the LFS storage backend.

### N10: Fix email notification subject truncating unicode characters mid-codepoint

When an issue title contains multi-byte unicode characters and the
subject is truncated to fit the email subject length limit, the
truncation can split a multi-byte character. This produces invalid
UTF-8 in the email subject. Fix the truncation to respect unicode
codepoint boundaries.

### M5: Implement pull request review approval rules

Add configurable approval rules beyond the existing simple "N approvals
required." Support rules like: at least one approval from each team in
a list, approval from the code owner of each changed path, dismissal
of stale approvals on new pushes, and blocking reviews that prevent
merge until resolved.

### M6: Add repository dependency graph visualization

Implement a dependency graph page that visualizes package dependencies
declared in the repository's manifest files (go.mod, package.json,
Gemfile, requirements.txt, etc.). Show a DAG of direct and transitive
dependencies. Highlight known security vulnerabilities. Link to the
dependency's repository if hosted on the same Gitea instance.

### M7: Implement branch comparison view

Add a `/compare/branch_a...branch_b` page that shows the diff between
two branches, including changed files count, lines added/removed, and
commit list. Support swapping comparison direction. Include a "Create
Pull Request" button pre-filled with the compared branches.

### M8: Add actions workflow dispatch with inputs

Implement manual workflow dispatch for Gitea Actions. Support the
`workflow_dispatch` event with configurable input fields (string,
choice, boolean) defined in the workflow YAML. Add a "Run workflow"
button in the Actions tab that shows a form for the inputs.

### M9: Implement repository archive download with format selection

Add `/archive/{ref}.{format}` endpoint supporting zip, tar.gz, and
tar.bz2 formats. Cache generated archives for frequently requested
refs. Support subdirectory archives (`/archive/{ref}/{path}.zip`).
Add download buttons to the repository web UI.

### M10: Add Git blame with inline annotations

Implement a blame view that shows per-line commit attribution alongside
the source code, similar to GitHub's blame view. Show commit SHA,
author, and date for each line group. Support navigating to the commit
that last changed a specific line. Handle blame across renames.

### W4: Add container registry support

Implement a Docker/OCI container registry as part of Gitea's package
infrastructure. Support image push/pull, tag listing, image manifest
inspection, vulnerability scanning integration, and garbage collection
of untagged layers. Add authentication via Gitea's existing auth system.
Integrate with the repository's Actions for automated builds. Changes
span the package registry, HTTP routing, storage backend, and auth
systems.

### W5: Implement repository insights and analytics

Add a repository analytics page showing: commit activity over time,
active contributors chart, code frequency (additions/deletions per
week), pull request merge time distribution, issue resolution time
trends, and code language breakdown over time. Store aggregate metrics
in a time-series table. Include an API for programmatic access.
Changes span models, services, routers, and add scheduled aggregation
workers.

### W6: Add federated repository interaction via ActivityPub

Implement ActivityPub federation so users on one Gitea instance can
star, fork, and open issues/PRs on repositories hosted on a different
Gitea instance. Implement the Actor model for users and repositories,
Activity types for git operations, and inbox/outbox endpoints.
Handle authentication across instances. Changes span the user model,
repository model, API routing, notification system, and add a
federation module.

### W7: Implement Gitea Actions with reusable workflows

Add support for reusable workflows in Gitea Actions: a workflow can
call another workflow in the same or a different repository via
`uses: owner/repo/.gitea/workflows/reusable.yaml@ref`. Support input
parameters, output values, secrets passing, and nested workflow calls.
Includes workflow resolution, input validation, output propagation,
and the runner protocol changes.

### W8: Add advanced code search with code navigation

Implement code search with IDE-like features: go-to-definition from
search results, find-all-references in the repository, symbol search
across repositories in an organization, and search within specific
language constructs (search only in function bodies, or only in
comments). Uses the bleve indexer with language-aware tokenization.
Changes span the indexer, search API, web UI, and add a code
navigation module.

### W9: Implement organization-level security policies

Add organization-level security settings that cascade to all member
repositories: required branch protection rules, mandatory code review
policies, required CI checks, allowed merge strategies, secret
scanning rules, and dependency vulnerability policies. Organization
admins configure policies; repository admins cannot weaken them.
Changes span organization models, repository settings, branch
protection, and the admin UI.

### W10: Add built-in CI/CD with pipeline visualization

Extend Gitea Actions with a visual pipeline editor and live execution
visualization. Show a DAG of jobs with status indicators, live log
streaming per job, artifact browsing, and pipeline-level metrics.
Support matrix builds with visual expansion. Add pipeline templates
for common language/framework combinations. Changes span the Actions
runner, web UI, API, and storage for logs and artifacts.
