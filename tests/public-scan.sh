#!/usr/bin/env bash
# public-scan.sh: everything outside docs/internal must be fit to publish at every commit.
#
#   bash tests/public-scan.sh
#
# Checks each tracked file outside docs/internal for secret-shaped values, links into
# docs/internal and, where the list exists, the workspace markers in
# docs/internal/public-markers.txt. That list stays in docs/internal, so a public copy never shows
# what it looks for; there, the scan checks secrets and links only. It prints file, line and
# kind, never the matched text. A scan that can't run (git or grep failing) fails, rather than
# passing as clean.
set -u
export LC_ALL=C
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 2

markers=()
if [ -f docs/internal/public-markers.txt ]; then
  while IFS= read -r m || [ -n "$m" ]; do
    m="${m%$'\r'}"
    case "$m" in ""|"#"*) continue ;; esac
    markers+=("$m")
  done < docs/internal/public-markers.txt
fi
secret='-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|(AKIA|ASIA)[A-Z0-9]{16}|xox[abprs]-[A-Za-z0-9-]{10,}'
internal_link='\]\([^)]*docs/inter''nal'

failures=0
report() { printf '%s:%s: %s\n' "$1" "$2" "$3"; failures=$((failures + 1)); }
scan_for() {  # <file> <pattern> <kind> [grep flags]
  local out rc line
  out=$(grep -naE ${4:-} -- "$2" "$1" 2>&1); rc=$?
  if [ "$rc" -eq 0 ]; then
    while IFS=: read -r line _; do report "$1" "$line" "$3"; done <<<"$out"
  elif [ "$rc" -ne 1 ]; then
    report "$1" 0 "the scan couldn't run (grep exit $rc)"
  fi
}

files=$(git ls-files --cached --others --exclude-standard) || { echo "public-scan: git couldn't list the files"; exit 2; }
[ -n "$files" ] || { echo "public-scan: no files to scan"; exit 2; }
while IFS= read -r f; do
  case "$f" in docs/internal/*) continue ;; esac
  [ -f "$f" ] || continue
  for m in ${markers[@]+"${markers[@]}"}; do scan_for "$f" "$m" "workspace marker" -i; done
  scan_for "$f" "$secret" "possible secret"
  scan_for "$f" "$internal_link" "link to an internal document"
done <<<"$files"

if [ "$failures" -gt 0 ]; then printf 'public-scan: %d finding(s)\n' "$failures"; exit 1; fi
if [ ${#markers[@]} = 0 ]; then
  echo "public-scan: clean (no marker list here, so secrets and internal links only)"
else
  echo "public-scan: clean (${#markers[@]} markers, secrets and internal links)"
fi
