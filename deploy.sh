#!/bin/bash
# Deploy Shawn M1 Q4 Core Container
# ByteID: QVTX-53686177-6e204d31-1a1d2226-436f

set -e

DEPLOY_HOST="${M1_DEPLOY_HOST:?set M1_DEPLOY_HOST}"
DEPLOY_USER="root"
DEPLOY_DIR="/opt/shawn-m1-intake"
SSH_KEY="$HOME/.ssh/id_ed25519_qvtx"
REPO="https://github.com/quant-oss/shawn-m1-intake.git"

echo "[SHAWN-M1] Deploying to $DEPLOY_HOST"
echo "[SHAWN-M1] ByteID: QVTX-53686177-6e204d31-1a1d2226-436f"

ssh -i "$SSH_KEY" "$DEPLOY_USER@$DEPLOY_HOST" "
  set -e
  if [ -d $DEPLOY_DIR/.git ]; then
    cd $DEPLOY_DIR && git pull
  else
    git clone $REPO $DEPLOY_DIR
  fi

  cd $DEPLOY_DIR

  if command -v podman-compose &>/dev/null; then
    podman-compose down || true
    podman-compose up -d --build
  else
    podman pod rm -f shawn-m1-pod 2>/dev/null || true
    podman build -t qvtx/shawn-m1-intake:latest .
    podman run -d --name shawn-m1 --restart=always \
      -p 42881:42881 \
      -v shawn_m1_ledger:/opt/shawn-m1/ledger \
      -v shawn_m1_logs:/opt/shawn-m1/logs \
      -e M1_PORT=42881 \
      -e M1_CREDIT_BASE=\"\${M1_CREDIT_BASE}\" \
      -e M1_CREDIT_CREDS=\"\${M1_CREDIT_CREDS}\" \
      -e M1_CREDIT_TENANT=\"\${M1_CREDIT_TENANT:-omnl}\" \
      qvtx/shawn-m1-intake:latest
  fi

  sleep 3
  podman ps --filter name=shawn-m1
  podman logs shawn-m1 --tail=20 2>/dev/null || true
"

echo ""
echo "[SHAWN-M1] Health check:"
sleep 2
curl -s "http://$DEPLOY_HOST:42881/health" | python3 -m json.tool || echo "Service still starting..."
