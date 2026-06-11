#!/usr/bin/env bash
# Generate a self-signed TLS certificate for on-prem VoiceTuner.
# Run once before `docker compose up` — certs are valid for 10 years.
#
# Usage: bash scripts/gen-certs.sh [hostname]
# Default hostname: voicetuner.local

set -euo pipefail

HOSTNAME="${1:-voicetuner.local}"
CERT_DIR="$(cd "$(dirname "$0")/../nginx/certs" && pwd)"

mkdir -p "$CERT_DIR"

echo "[certs] Generating self-signed certificate for: $HOSTNAME"
echo "[certs] Output: $CERT_DIR/"

openssl req -x509 -nodes -days 3650 \
  -newkey rsa:2048 \
  -keyout "$CERT_DIR/voicetuner.key" \
  -out    "$CERT_DIR/voicetuner.crt" \
  -subj   "/CN=$HOSTNAME/O=VoiceTuner/C=IN" \
  -addext "subjectAltName=DNS:$HOSTNAME,DNS:localhost,IP:127.0.0.1"

chmod 600 "$CERT_DIR/voicetuner.key"
chmod 644 "$CERT_DIR/voicetuner.crt"

echo ""
echo "[certs] Done."
echo "  Certificate : $CERT_DIR/voicetuner.crt"
echo "  Private key : $CERT_DIR/voicetuner.key"
echo ""
echo "To trust this cert system-wide on macOS:"
echo "  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain $CERT_DIR/voicetuner.crt"
echo ""
echo "To trust on Ubuntu/Debian:"
echo "  sudo cp $CERT_DIR/voicetuner.crt /usr/local/share/ca-certificates/voicetuner.crt && sudo update-ca-certificates"
