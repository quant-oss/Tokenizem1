#!/bin/bash
# Deploy Shawn M1 Q4 Core Container to qxrp via Docker
# ByteID: QVTX-53686177-6e204d31-1a1d2226-436f

set -e

DEPLOY_IP="209.74.87.249"
DEPLOY_USER="root"
DEPLOY_DIR="/opt/shawn-m1-intake"
SSH_KEY="$HOME/.ssh/id_ed25519_qvtx"
REPO="https://github.com/quant-oss/shawn-m1-intake.git"

echo "[SHAWN-M1] Deploying to qxrp ($DEPLOY_IP)"
echo "[SHAWN-M1] ByteID: QVTX-53686177-6e204d31-1a1d2226-436f"

ssh -i "$SSH_KEY" "$DEPLOY_USER@$DEPLOY_IP" "
  set -e
  # Install docker if missing
  which docker || (apt-get update -q && apt-get install -y docker.io docker-compose)

  # Clone or update repo
  if [ -d $DEPLOY_DIR/.git ]; then
    cd $DEPLOY_DIR && git pull
  else
    git clone $REPO $DEPLOY_DIR
  fi

  cd $DEPLOY_DIR

  # Build and start via compose
  # RHEL9 uses podman-docker shim — use podman-compose or podman directly
  if command -v podman-compose &>/dev/null; then
    podman-compose down || true
    podman-compose up -d --build
  else
    # Podman with docker-compose shim
    podman pod rm -f shawn-m1-pod 2>/dev/null || true
    podman build -t qvtx/shawn-m1-intake:latest .
    podman run -d --name shawn-m1 --restart=always \
      -p 42881:42881 \
      -v shawn_m1_ledger:/opt/shawn-m1/ledger \
      -v shawn_m1_logs:/opt/shawn-m1/logs \
      -e M1_PORT=42881 \
      qvtx/shawn-m1-intake:latest
  fi

  sleep 3
  podman ps --filter name=shawn-m1
  echo ''
  podman logs shawn-m1 --tail=20 2>/dev/null || true
"

echo ""
echo "[SHAWN-M1] Health check:"
sleep 2
curl -s "http://$DEPLOY_IP:42881/health" | python3 -m json.tool || echo "Service still starting..."

echo ""
echo "LIVE:"
echo "  Health:  http://$DEPLOY_IP:42881/health"
echo "  Ledger:  http://$DEPLOY_IP:42881/"
echo "  Receive: http://$DEPLOY_IP:42881/receive  (POST pacs.008 XML)"
echo ""
echo "  USD IBAN: LU680010000000000062  (acct 000000062)"
echo "  EUR IBAN: DE28500700100000000063  (acct 000000063)"
