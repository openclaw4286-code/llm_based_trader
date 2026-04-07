#!/usr/bin/env bash
# Gate.io API 키를 대화식으로 입력받아 .env 파일에 저장합니다.
set -e
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

echo "=== Gate.io API Key Setup ==="
echo ""

read -p "GATEIO_API_KEY: " API_KEY
read -s -p "GATEIO_API_SECRET: " API_SECRET
echo ""

cat > "$ENV_FILE" <<EOF
GATEIO_API_KEY=$API_KEY
GATEIO_API_SECRET=$API_SECRET
EOF

chmod 600 "$ENV_FILE"
echo ""
echo "Saved to $ENV_FILE (permissions: 600)"
