#!/usr/bin/env bash
# Deploy the committed code (HEAD) to the Proxmox container. Runs from Git Bash on Windows or any Unix shell.
#
#   scripts/deploy.sh              # deploy HEAD
#   scripts/deploy.sh --sync-env   # also upload the local .env (PTP_EMAIL / PTP_PASSWORD) to the container
#   scripts/deploy.sh --worktree   # deploy the working tree instead of HEAD (to test uncommitted changes)
#
# Target: BRIDGE_DEPLOY_HOST (default root@192.168.178.10, the Proxmox host) and BRIDGE_DEPLOY_CTID
# (default 108, the LXC container shared with SRC and the platform). Needs key-based SSH to the host.
set -euo pipefail

PVE_HOST=${BRIDGE_DEPLOY_HOST:-root@192.168.178.10}
CTID=${BRIDGE_DEPLOY_CTID:-108}
NEW_DIR=/opt/signal-bridge/repo.new
ENV_FILE=/etc/signal-bridge/.env

worktree=0
sync_env=0
for arg in "$@"; do
    case $arg in
        --worktree) worktree=1 ;;
        --sync-env) sync_env=1 ;;
        -h | --help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "deploy.sh: unknown option $arg" >&2; exit 2 ;;
    esac
done

cd "$(git rev-parse --show-toplevel)"
in_ct() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$PVE_HOST" "pct exec $CTID -- $*"; }

revision=$(git rev-parse --short HEAD)
echo "==> Uploading $([[ $worktree == 1 ]] && echo "the working tree (based on $revision)" || echo "commit $revision") to container $CTID on ${PVE_HOST#*@}"
in_ct "bash -c 'rm -rf $NEW_DIR && mkdir -p $NEW_DIR'"
if [[ $worktree == 1 ]]; then
    revision="$revision-dirty"
    # Tracked and untracked, non-ignored files that exist on disk; CRLF from core.autocrlf is stripped remotely.
    git ls-files -co --exclude-standard | while IFS= read -r f; do [[ -f $f ]] && printf '%s\n' "$f"; done \
        | tar -cf - -T - | in_ct "tar -xf - --no-same-owner -C $NEW_DIR"
    in_ct "bash -c \"find $NEW_DIR/deploy $NEW_DIR/scripts -type f -exec sed -i 's/\\r\$//' {} +\""
else
    git archive --format=tar HEAD | in_ct "tar -xf - --no-same-owner -C $NEW_DIR"
fi

if [[ $sync_env == 1 ]] || ! in_ct "test -s $ENV_FILE"; then
    if [[ -f .env ]]; then
        echo "==> Uploading .env to $ENV_FILE"
        in_ct "bash -c 'install -d -m 755 /etc/signal-bridge && umask 027 && cat > $ENV_FILE'" < .env
    else
        echo "==> No local .env and none on the container; copy .env.example to .env first" >&2
        exit 1
    fi
fi

echo "==> Installing"
in_ct "bash $NEW_DIR/deploy/install.sh --revision $revision"
