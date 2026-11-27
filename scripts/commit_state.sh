#!/usr/bin/env bash
# Commit this run's ledger/topic changes without clobbering a run that committed meanwhile.
set -u
MSG="${1:-state update}"
SAVE=$(mktemp -d)
cp state/ledger.csv "$SAVE/ledger.csv" 2>/dev/null || true
for f in channels/*/topics-*.csv; do
  ch=$(basename "$(dirname "$f")"); cp "$f" "$SAVE/topics__${ch}__$(basename "$f" .csv).csv"
done
mkdir -p "$SAVE/meta"; cp -r state/meta/. "$SAVE/meta/" 2>/dev/null || true
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
for i in 1 2 3 4 5; do
  git stash -u -q 2>/dev/null || true
  git pull -q --rebase origin "${GITHUB_REF_NAME:-main}" || { git rebase --abort 2>/dev/null; git reset -q --hard "origin/${GITHUB_REF_NAME:-main}"; }
  git stash drop -q 2>/dev/null || true
  python -m pipeline merge-state --from "$SAVE"
  mkdir -p state/meta; cp -r "$SAVE/meta/." state/meta/ 2>/dev/null || true
  git add state channels
  git diff --cached --quiet && { echo "no state changes"; exit 0; }
  git commit -q -m "$MSG [skip ci]"
  git push -q origin "HEAD:${GITHUB_REF_NAME:-main}" && { echo "state pushed"; exit 0; }
  echo "push attempt $i failed; retrying"; git reset -q --soft HEAD~1; sleep $((i * 7))
done
echo "could not push state after 5 attempts"; exit 1
