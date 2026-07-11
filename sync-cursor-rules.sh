#!/usr/bin/env bash
#
# sync-cursor-rules.sh — Anti-drift mechanism.
#
# .claude/rules/**/*.md and .claude/templates/rules/*.md are the SINGLE SOURCE OF TRUTH.
# .cursor/rules/*.mdc and .cursor/templates/rules/*.mdc are GENERATED from them —
# never hand-edit the .mdc files.
#
# .claude/rules/ is scanned RECURSIVELY: both _framework/ (framework-owned) and
# project/ (user-owned) rules generate .mdc. Merge artifacts (*.framework.md) are skipped.
#
# Mapping:
#   .claude rule with `paths:` frontmatter  -> .mdc with globs:[...]  alwaysApply:false
#   .claude rule with NO `paths:`           -> .mdc with globs:[]     alwaysApply:true
#   Non-`paths:` frontmatter keys (source, etc.) are ignored, NOT treated as globs.
#   Rule body is copied verbatim.
#
# App-type templates without `paths:` become alwaysApply:true so that, once a user
# activates them, they auto-attach (empty globs + alwaysApply:false = weak/no attach).
#
# Usage: ./sync-cursor-rules.sh        (regenerate)
#        ./sync-cursor-rules.sh --check (CI: exit 1 if out of sync, no writes)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$SCRIPT_DIR/.claude/rules"
DST_DIR="$SCRIPT_DIR/.cursor/rules"
TPL_SRC_DIR="$SCRIPT_DIR/.claude/templates/rules"
TPL_DST_DIR="$SCRIPT_DIR/.cursor/templates/rules"

CHECK_ONLY=false
[ "${1:-}" = "--check" ] && CHECK_ONLY=true

# Per-file description for the Cursor frontmatter `description` field.
describe() {
  case "$1" in
    backend)    echo "Backend API, auth, error handling, observability, and concurrency rules" ;;
    code)       echo "Core code style and quality rules" ;;
    database)   echo "Database, migrations, ORM, seeding, caching, and AI-agent safety rules" ;;
    devops)     echo "CI/CD and dependency upgrade rules" ;;
    frontend)   echo "Frontend components, data fetching, mutations, accessibility, and performance rules" ;;
    git)        echo "Git branching, commit, and pull request rules" ;;
    guardrails) echo "Core safety guardrails — ask when unsure, verify before assuming" ;;
    security)   echo "Security headers, CORS, secrets, supply chain, access control, and MCP safety rules" ;;
    testing)    echo "Testing strategy and execution rules" ;;
    app-ecommerce)    echo "Rules for e-commerce and payment processing including Stripe integration, idempotency, and inventory safety" ;;
    app-dashboard)    echo "Rules for dashboard and data-heavy applications including virtualization, pagination, and resource management" ;;
    mobile)           echo "Rules for React Native and Expo mobile development including performance, routing, and secure storage" ;;
    app-realtime)     echo "Rules for real-time applications using WebSocket, SSE, or chat including connection management and reconnection" ;;
    app-ai-llm)       echo "Rules for AI and LLM application development including token budgets, context management, and prompt safety" ;;
    app-cms)          echo "Rules for CMS and content management systems including sanitization, uploads, and publish workflows" ;;
    app-pwa)          echo "Rules for progressive web apps and offline-first applications including service workers, caching, and sync" ;;
    monorepo)         echo "Rules for monorepo setups with Turborepo, Nx, or pnpm workspaces including import boundaries and task orchestration" ;;
    type-safe-api)    echo "Rules for type-safe API development with tRPC, GraphQL, and OpenAPI including code generation and schema validation" ;;
    observability)    echo "Rules for observability with OpenTelemetry, Sentry, and Datadog including tracing, alerting, and error context" ;;
    infra)            echo "Rules for infrastructure-as-code with Terraform and Pulumi including state management, secrets, and drift detection" ;;
    app-multi-tenant) echo "Rules for multi-tenant SaaS applications including tenant isolation, cache scoping, and context propagation" ;;
    *)          echo "Project rules" ;;
  esac
}

# Render one .claude rule file to .mdc text on stdout.
render() {
  local src="$1" name="$2" rel="${3:-.claude/rules/$name.md}"
  # Split frontmatter (between the first two `---` lines) from the body.
  # Only list items UNDER the `paths:` key become globs; other frontmatter keys
  # (source, description, ...) are ignored so they aren't mistaken for globs.
  # awk emits: "PATH\t<glob>" per path, then "---BODY---", then "BODY\t<line>".
  awk '
    NR==1 && $0=="---" { infm=1; next }
    infm && $0=="---"  { infm=0; print "---BODY---"; next }
    infm {
      # A top-level key line, e.g. "paths:" or "source: framework".
      if ($0 ~ /^[A-Za-z_][A-Za-z0-9_-]*:/) {
        if ($0 ~ /^paths:/) inpaths=1; else inpaths=0
        next
      }
      # A list item; only collect it when we are inside the paths: block.
      if (inpaths && $0 ~ /^[[:space:]]*-[[:space:]]*/) {
        line=$0
        sub(/^[[:space:]]*-[[:space:]]*/, "", line)   # strip "  - "
        gsub(/^"|"$/, "", line)                        # strip surrounding quotes
        if (line != "") print "PATH\t" line
      }
      next
    }
    { print "BODY\t" $0 }
  ' "$src" > /tmp/_sync_parse.$$

  local globs="" body_started=false has_fm=false body=""
  while IFS=$'\t' read -r tag rest; do
    case "$tag" in
      PATH) has_fm=true; globs="$globs\"$rest\", " ;;
      "---BODY---") body_started=true ;;
      BODY) body="$body$rest"$'\n' ;;
    esac
  done < /tmp/_sync_parse.$$
  rm -f /tmp/_sync_parse.$$

  globs="${globs%, }"  # trim trailing ", "
  local always="true"
  [ -n "$globs" ] && always="false"

  printf -- '---\n'
  printf -- 'description: %s\n' "$(describe "$name")"
  printf -- 'globs: [%s]\n' "$globs"
  printf -- 'alwaysApply: %s\n' "$always"
  printf -- '---\n\n'
  printf -- '<!-- GENERATED from %s by sync-cursor-rules.sh — do not edit by hand -->\n\n' "$rel"
  printf -- '%s' "$body"
}

drift=0

# Generate every .mdc in dst_dir from the .md sources under src_dir (recursive),
# then remove orphan .mdc files whose source no longer exists. Output is flat:
# every source .md, wherever it sits in the tree, maps to dst_dir/<basename>.mdc.
sync_dir() {
  local src_dir="$1" dst_dir="$2"
  [ -d "$src_dir" ] || return
  mkdir -p "$dst_dir"
  local generated=() src name dst new rel
  while IFS= read -r src; do
    [ -e "$src" ] || continue
    name="$(basename "$src" .md)"
    dst="$dst_dir/$name.mdc"
    rel="${src#"$SCRIPT_DIR"/}"
    generated+=("$name")
    new="$(render "$src" "$name" "$rel")"
    if $CHECK_ONLY; then
      if [ ! -f "$dst" ] || [ "$new" != "$(cat "$dst")" ]; then
        echo "DRIFT: $dst is out of sync with $rel"
        drift=1
      fi
    else
      printf -- '%s' "$new" > "$dst"
      echo "generated $dst (from $rel)"
    fi
  done < <(find "$src_dir" -type f -name '*.md' ! -name '*.framework.md' | sort)

  local d kept g
  for d in "$dst_dir"/*.mdc; do
    [ -e "$d" ] || continue
    name="$(basename "$d" .mdc)"
    kept=false
    for g in "${generated[@]}"; do [ "$g" = "$name" ] && kept=true; done
    if ! $kept; then
      if $CHECK_ONLY; then
        echo "DRIFT: $d has no matching source under $src_dir"
        drift=1
      else
        rm "$d"; echo "removed orphan $d"
      fi
    fi
  done
}

sync_dir "$SRC_DIR" "$DST_DIR"
sync_dir "$TPL_SRC_DIR" "$TPL_DST_DIR"

if $CHECK_ONLY; then
  [ "$drift" -eq 0 ] && echo "OK: .cursor rules in sync with .claude sources"
  exit "$drift"
fi
echo "Done. .cursor/rules and .cursor/templates/rules regenerated from .claude sources."
