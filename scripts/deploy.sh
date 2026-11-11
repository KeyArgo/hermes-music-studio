#!/usr/bin/env bash
# Deploy the studio: install this tree, stamp the build, restart the host, prove what is served.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALLED="$HOME/.hermes/plugins/hermes-music-studio"
cd "$REPO"

VERSION="$(grep -m1 '^version:' plugin.yaml | cut -d: -f2- | tr -d '"' | xargs)"
COMMIT="$(git rev-parse HEAD)"
COMMIT_DATE="$(git log -1 --format=%cI)"
BUILT_AT="$(date -Iseconds)"
DISPLAY="${VERSION}-dev.$(git rev-list --count HEAD)"
DIRTY="false"
# Build outputs and the stamp itself are excluded: counting them would mark every deploy modified.
[ -n "$(git status --porcelain -- . ':!dist' ':!BUILD_INFO.json' ':!*.pyc')" ] && DIRTY="true"

echo "==> installing $COMMIT"
# A gateway reload can hold the plugin directory for a moment, and the installer then reports a
# permissions problem that is gone a second later. Retry once on exactly that symptom rather than
# sending a person to chmod a directory that was never misconfigured.
install_once() {
  hermes plugins install KeyArgo/hermes-music-studio-dev --ref "$COMMIT" --force --yes-deps --enable 2>&1 | tail -2
}
INSTALL_OUT="$(install_once || true)"
if printf '%s' "$INSTALL_OUT" | grep -qi 'permissions on .*plugins'; then
  echo "==> installer hit the transient plugin-dir lock; retrying once"
  sleep 3
  INSTALL_OUT="$(install_once || true)"
fi
printf '%s\n' "$INSTALL_OUT"

python3 - "$INSTALLED" "$DISPLAY" "$VERSION" "$COMMIT" "$COMMIT_DATE" "$BUILT_AT" "$DIRTY" <<'PY'
import json, pathlib, sys
installed, display, release, commit, date, built, dirty = sys.argv[1:8]
payload = {"version": display, "release": release, "commit": commit, "commit_date": date,
           "built_at": built, "dirty": dirty == "true",
           "channel": "dev" if "-dev." in display else "release"}
pathlib.Path(installed, "BUILD_INFO.json").write_text(json.dumps(payload, indent=2) + "\n")
print(f"==> stamped {display} · {commit[:8]}")
PY

# Hermes re-applies local edits on top of an install; sync afterwards so the tree is what runs.
rsync -a --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' --exclude 'BUILD_INFO.json' \
  "$REPO/" "$INSTALLED/"

for pid in $(ps -eo pid,args | awk '$2 ~ /python3/ && /music_studio\.host/ {print $1}'); do kill "$pid" 2>/dev/null || true; done
sleep 1
( cd "$INSTALLED" && env -u PYTHONHOME -u PYTHONPATH \
    /usr/bin/python3.13 -c "from music_studio import host_launcher; print(host_launcher.ensure_host())" )

python3 scripts/verify-served.py 8775
