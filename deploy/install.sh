#!/usr/bin/env bash
# Install or upgrade the bridge inside the container. Run as root by scripts/deploy.sh, which has
# unpacked the new code to /opt/signal-bridge/repo.new. Idempotent; keeps config, login and journal.
#
#   bash /opt/signal-bridge/repo.new/deploy/install.sh --revision <git revision>
set -euo pipefail

APP_DIR=/opt/signal-bridge
NEW_DIR=$APP_DIR/repo.new
REPO_DIR=$APP_DIR/repo
CONF_DIR=/etc/signal-bridge
DATA_DIR=/var/lib/signal-bridge
USER=srcenter   # the SRC service user, so SRC's snapshot is readable
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

revision=unknown
while [[ $# -gt 0 ]]; do
    case $1 in
        --revision) revision=$2; shift 2 ;;
        *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
    esac
done

[[ $EUID -eq 0 ]] || { echo "install.sh: run as root" >&2; exit 1; }
[[ -d $NEW_DIR ]] || { echo "install.sh: $NEW_DIR missing; run scripts/deploy.sh" >&2; exit 1; }
id "$USER" &>/dev/null || { echo "install.sh: user $USER missing; install the Sentiment Research Center first" >&2; exit 1; }

if ! command -v uv &>/dev/null; then
    echo "==> Installing uv"
    curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi

echo "==> Building the environment"
# --no-editable: the venv holds a copy of the package, so it does not depend on the repo directory.
UV_PYTHON_INSTALL_DIR=$APP_DIR/python UV_PROJECT_ENVIRONMENT=$APP_DIR/venv \
    uv sync --project "$NEW_DIR" --frozen --no-dev --no-editable --compile-bytecode -q

rm -rf "$REPO_DIR"
mv "$NEW_DIR" "$REPO_DIR"
echo "$revision" > "$REPO_DIR/REVISION"
chmod -R a+rX "$APP_DIR"

install -d -m 755 "$CONF_DIR"
install -d -o "$USER" -g "$USER" -m 750 "$DATA_DIR"
if [[ ! -f $CONF_DIR/bridge.yaml ]]; then
    echo "==> Creating $CONF_DIR/bridge.yaml"
    install -m 644 "$REPO_DIR/deploy/bridge.server.yaml" "$CONF_DIR/bridge.yaml"
fi
if [[ ! -s $CONF_DIR/.env ]]; then
    echo "install.sh: $CONF_DIR/.env missing (PTP_EMAIL, PTP_PASSWORD); run scripts/deploy.sh --sync-env" >&2
    exit 1
fi
chown root:"$USER" "$CONF_DIR/.env"
chmod 640 "$CONF_DIR/.env"

echo "==> Checking config, login and SRC snapshot (dry run)"
(cd "$DATA_DIR" && runuser -u "$USER" -- "$APP_DIR/venv/bin/bridge" --config "$CONF_DIR/bridge.yaml" run --dry-run > /dev/null)

echo "==> Installing the systemd units"
install -m 644 "$REPO_DIR"/deploy/systemd/signal-bridge.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now signal-bridge.timer
systemctl list-timers signal-bridge.timer --no-pager | head -2

echo "==> Deployed $revision"
