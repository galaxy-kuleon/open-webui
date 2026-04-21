#!/usr/bin/env bash
# verify_hermes_hooks.sh — sanity-check that all marker-bracketed hooks
# registered in docs/hermes-rebase-patchmap.md are still present in both
# repos after an upstream rebase.
#
# Exit codes:
#   0 — every registered marker is present + every BEGIN has a matching END
#   1 — at least one registered marker is missing
#   2 — unmatched BEGIN/END (structural defect in code)
#   3 — script invoked from the wrong directory

set -uo pipefail
# Note: intentionally NOT using `set -e` — grep returns exit 1 on no-match,
# which is expected and non-fatal when the patchmap is empty or when searching
# for markers that don't exist yet. We handle exit codes explicitly at the end.

OPENWEBUI_ROOT="${OPENWEBUI_ROOT:-$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel 2>/dev/null || true)}"
HERMES_ROOT="${HERMES_ROOT:-/Users/noelbao/Works/hermes-agent}"
PATCHMAP="${OPENWEBUI_ROOT}/docs/hermes-rebase-patchmap.md"

if [[ -z "${OPENWEBUI_ROOT}" || ! -d "${OPENWEBUI_ROOT}" ]]; then
    echo "verify_hermes_hooks: OPENWEBUI_ROOT not set or not a directory (got: '${OPENWEBUI_ROOT}')" >&2
    exit 3
fi

if [[ ! -f "${PATCHMAP}" ]]; then
    echo "verify_hermes_hooks: patchmap missing at ${PATCHMAP}" >&2
    exit 3
fi

echo "Scanning for HERMES-HOOK markers..."
echo "  openwebui: ${OPENWEBUI_ROOT}"
echo "  hermes:    ${HERMES_ROOT}"
echo

# 1. Extract registered marker names from the patchmap tables (column 1 after "| ").
#    Skip the "anticipated markers" section since those aren't committed yet.
registered_markers=$(
    awk '
        /^## Anticipated markers/ { in_anticipated=1 }
        /^---$/ { in_anticipated=0 }
        !in_anticipated && /^\| HERMES-HOOK-/ {
            # Extract between first "| " and " |"
            match($0, /HERMES-HOOK-[A-Z0-9_-]+/)
            if (RSTART > 0) print substr($0, RSTART, RLENGTH)
        }
    ' "${PATCHMAP}" | sort -u
)

registered_count=$(echo -n "${registered_markers}" | grep -c . || true)
echo "Registered markers: ${registered_count}"

# 2. Find all BEGIN/END occurrences in both repos.
find_markers() {
    local root="$1"
    [[ -d "${root}" ]] || return 0
    # Search code files, skip node_modules / .venv / __pycache__ / .git
    grep -rE --include='*.py' --include='*.ts' --include='*.tsx' --include='*.js' --include='*.svelte' \
         --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.git --exclude-dir=__pycache__ \
         --exclude-dir=.svelte-kit --exclude-dir=build --exclude-dir=dist \
         'HERMES-HOOK-[A-Z0-9_-]+-(BEGIN|END)' "${root}" 2>/dev/null || true
}

all_hits=$(find_markers "${OPENWEBUI_ROOT}"; find_markers "${HERMES_ROOT}")

# 3. Check every registered marker has both a BEGIN and an END somewhere.
missing=()
while IFS= read -r marker; do
    [[ -z "${marker}" ]] && continue
    has_begin=$(echo "${all_hits}" | grep -c "${marker}-BEGIN" || true)
    has_end=$(echo "${all_hits}" | grep -c "${marker}-END" || true)
    if [[ "${has_begin}" == "0" || "${has_end}" == "0" ]]; then
        missing+=("${marker} (begin=${has_begin}, end=${has_end})")
    fi
done <<< "${registered_markers}"

# 4. Independent sanity: every BEGIN should have a matching END in the code.
all_begins=$(echo "${all_hits}" | grep -oE 'HERMES-HOOK-[A-Z0-9_-]+-BEGIN' 2>/dev/null | sort -u | sed 's/-BEGIN$//' || true)
all_ends=$(echo "${all_hits}" | grep -oE 'HERMES-HOOK-[A-Z0-9_-]+-END' 2>/dev/null | sort -u | sed 's/-END$//' || true)

orphan_begins=$(comm -23 <(echo "${all_begins}") <(echo "${all_ends}") 2>/dev/null || true)
orphan_ends=$(comm -13 <(echo "${all_begins}") <(echo "${all_ends}") 2>/dev/null || true)

# 5. Report.
exit_code=0

if [[ ${#missing[@]} -gt 0 ]]; then
    echo "MISSING registered markers (${#missing[@]}):" >&2
    printf '  - %s\n' "${missing[@]}" >&2
    exit_code=1
fi

if [[ -n "${orphan_begins}" ]]; then
    echo "Orphan BEGIN markers (no matching END):" >&2
    echo "${orphan_begins}" | sed 's/^/  - /' >&2
    exit_code=2
fi

if [[ -n "${orphan_ends}" ]]; then
    echo "Orphan END markers (no matching BEGIN):" >&2
    echo "${orphan_ends}" | sed 's/^/  - /' >&2
    exit_code=2
fi

if [[ "${exit_code}" -eq 0 ]]; then
    echo "OK: all ${registered_count} registered markers present and structurally balanced."
fi

exit "${exit_code}"
