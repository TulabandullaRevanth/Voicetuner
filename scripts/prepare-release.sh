#!/bin/bash

# Script to prepare a signed release build
# Usage: ./scripts/prepare-release.sh

set -e

echo "🔑 Checking for signing keys..."

if [ ! -f ~/.tauri/voicetuner.key ]; then
  echo "❌ Private key not found at ~/.tauri/voicetuner.key"
  echo "Run: cd tauri && bun tauri signer generate -w ~/.tauri/voicetuner.key"
  exit 1
fi

if [ ! -f ~/.tauri/voicetuner.key.pub ]; then
  echo "❌ Public key not found at ~/.tauri/voicetuner.key.pub"
  exit 1
fi

echo "✅ Signing keys found"
echo ""

# Check if public key is in tauri.conf.json
if grep -q "REPLACE_WITH_YOUR_PUBLIC_KEY" tauri/src-tauri/tauri.conf.json; then
  echo "⚠️  Public key not configured in tauri.conf.json"
  echo ""
  echo "Add this to tauri/src-tauri/tauri.conf.json:"
  echo ""
  cat ~/.tauri/voicetuner.key.pub
  echo ""
  exit 1
fi

echo "🔧 Setting up environment..."

export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/voicetuner.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""

echo "✅ Environment configured"
echo ""

echo "📦 Building release..."
echo ""

bun run build

echo ""
echo "✅ Release build complete!"
echo ""
echo "📂 Bundles created in: tauri/src-tauri/target/release/bundle/"
echo ""
echo "Next steps:"
echo "1. Create a GitHub release"
echo "2. Upload all files from the bundle directory"
echo "3. Create latest.json with update metadata"
echo ""
