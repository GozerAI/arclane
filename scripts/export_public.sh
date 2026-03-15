#!/usr/bin/env bash
# export_public.sh — Creates a clean public export of Arclane for GozerAI/arclane.
# Usage: bash scripts/export_public.sh [target_dir]
#
# Strips proprietary Pro/Enterprise modules, C-Suite integrations, and internal infrastructure,
# leaving only community-tier code + Python stub __init__.py files.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-${REPO_ROOT}/../arclane-public-export}"

echo "=== Arclane Public Export ==="
echo "Source: ${REPO_ROOT}"
echo "Target: ${TARGET}"

# Clean target
rm -rf "${TARGET}"
mkdir -p "${TARGET}"

# Use git archive to get a clean copy (respects .gitignore, excludes .git)
cd "${REPO_ROOT}"
git archive HEAD | tar -x -C "${TARGET}"

# ===== STRIP PROPRIETARY MODULES =====

# Pro tier — advanced site generation engine
rm -rf "${TARGET}/src/arclane/engine/"

# Pro tier — C-Suite bridge and external integrations (CRITICAL: contains C-Suite refs)
rm -rf "${TARGET}/src/arclane/integrations/"

# Enterprise tier — auto-provisioning
rm -rf "${TARGET}/src/arclane/provisioning/"

# Enterprise tier — notification system
rm -f "${TARGET}/src/arclane/notifications.py"

# ===== STRIP TESTS FOR PROPRIETARY MODULES =====
rm -f "${TARGET}/tests/test_orchestrator.py"
rm -f "${TARGET}/tests/test_integrations.py"
rm -f "${TARGET}/tests/test_nexus_integration.py"
rm -f "${TARGET}/tests/test_workflow_integration.py"
rm -f "${TARGET}/tests/test_notifications.py"
rm -f "${TARGET}/tests/test_intake.py"
rm -f "${TARGET}/tests/test_deploy.py"
rm -f "${TARGET}/tests/test_scheduler.py"
rm -f "${TARGET}/tests/test_templates.py"

# ===== STRIP INTERNAL FILES =====
rm -rf "${TARGET}/.github/"
rm -f "${TARGET}/CLAUDE.md"
rm -f "${TARGET}/.env.example"
rm -f "${TARGET}/deploy/GO-TO-MARKET.md"
rm -f "${TARGET}/deploy/LAUNCH-GUIDE.md"

# ===== STRIP WORKFLOW FILES (may contain C-Suite references) =====
rm -rf "${TARGET}/workflows/"

# ===== CREATE STUB __init__.py FOR STRIPPED PACKAGES =====

STUB_CONTENT='"""This module requires a commercial license.

Visit https://gozerai.com/pricing for Pro and Enterprise tier details.
Set VINZY_LICENSE_KEY to unlock licensed features.
"""

raise ImportError(
    f"{__name__} requires a commercial license. "
    "Visit https://gozerai.com/pricing for details."
)'

# Pro: engine/
mkdir -p "${TARGET}/src/arclane/engine"
echo "${STUB_CONTENT}" > "${TARGET}/src/arclane/engine/__init__.py"

# Pro: integrations/
mkdir -p "${TARGET}/src/arclane/integrations"
echo "${STUB_CONTENT}" > "${TARGET}/src/arclane/integrations/__init__.py"

# Enterprise: provisioning/
mkdir -p "${TARGET}/src/arclane/provisioning"
echo "${STUB_CONTENT}" > "${TARGET}/src/arclane/provisioning/__init__.py"

# Enterprise: notifications.py stub
echo "${STUB_CONTENT}" > "${TARGET}/src/arclane/notifications.py"

# ===== SANITIZE REFERENCES =====
find "${TARGET}" -type f \( -name "*.py" -o -name "*.md" -o -name "*.yml" -o -name "*.yaml" -o -name "*.toml" -o -name "*.txt" -o -name "*.cfg" -o -name "*.sh" -o -name "*.env*" -o -name "Dockerfile" -o -name "Caddyfile" \) -exec sed -i \
    -e 's|1450enterprises\.com|gozerai.com|g' \
    -e 's|GozerAI/arclane|GozerAI/arclane|g' \
    -e 's|dev@gozerai.com[a-zA-Z.]*|dev@gozerai.com|g' \
    {} +

# Double-check: warn about any remaining C-Suite references in kept files
for f in $(grep -rl "c-suite\|csuite\|c_suite" "${TARGET}/src/arclane/" 2>/dev/null || true); do
    echo "WARNING: C-Suite reference found in kept file: ${f}"
    echo "  Review and manually clean if needed."
done

echo ""
echo "=== Export complete: ${TARGET} ==="
echo ""
echo "Community-tier modules included:"
echo "  __init__.py, api/, cli.py, core/, models/, services/"
echo ""
echo "Stripped (Pro/Enterprise/Private):"
echo "  engine/ (Pro), integrations/ (Pro — C-Suite bridge),"
echo "  provisioning/ (Enterprise), notifications.py (Enterprise)"
echo ""
echo "Next steps:"
echo "  cd ${TARGET}"
echo "  git init && git add -A && git commit -m 'Initial public release'"
echo "  gh repo create GozerAI/arclane --public --description 'AI-powered website builder and deployment platform — Part of the GozerAI ecosystem'"
echo "  git remote add origin https://github.com/GozerAI/arclane.git"
echo "  git push -u origin main"
echo "  gh release create v1.0.0 --title 'v1.0.0' --notes 'Initial public release under GozerAI organization. Community-tier features included. Pro/Enterprise features require a commercial license — visit https://gozerai.com/pricing'"
