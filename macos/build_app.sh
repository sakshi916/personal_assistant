#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_ROOT="$PROJECT_ROOT/dist/build"
APP="$PROJECT_ROOT/dist/Personal Assistant.app"
DMG="$PROJECT_ROOT/dist/Personal Assistant.dmg"

rm -rf "$BUILD_ROOT" "$APP" "$DMG"
mkdir -p "$BUILD_ROOT/python"

cd "$PROJECT_ROOT"
uv run --group packaging -- python -m PyInstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name assistant-backend \
  --distpath "$BUILD_ROOT/python" \
  --workpath "$BUILD_ROOT/pyinstaller" \
  --specpath "$BUILD_ROOT" \
  --add-data "$PROJECT_ROOT/app_registry.json:." \
  --add-data "$PROJECT_ROOT/SystemPrompt.txt:." \
  --add-data "$PROJECT_ROOT/memory:memory" \
  --add-data "$PROJECT_ROOT/apps/calendar.applescript:apps" \
  --add-data "$PROJECT_ROOT/apps/browser.applescript:apps" \
  --hidden-import apps.notes_tools \
  --hidden-import apps.text_editor_tools \
  --hidden-import apps.calendar_tools \
  --hidden-import apps.browser_tools \
  assistant_api.py

GIT_CONFIG_COUNT=1 \
GIT_CONFIG_KEY_0=safe.bareRepository \
GIT_CONFIG_VALUE_0=all \
swift build --package-path "$SCRIPT_DIR" \
  --configuration release \
  --scratch-path "$BUILD_ROOT/swift"

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Library/LoginItems/AssistantBackend.app/Contents/MacOS"
cp "$BUILD_ROOT/swift/release/PersonalAssistant" \
  "$APP/Contents/MacOS/PersonalAssistant"
cp "$SCRIPT_DIR/Info.plist" "$APP/Contents/Info.plist"
cp "$BUILD_ROOT/python/assistant-backend" \
  "$APP/Contents/Library/LoginItems/AssistantBackend.app/Contents/MacOS/AssistantBackend"
cp "$SCRIPT_DIR/BackendInfo.plist" \
  "$APP/Contents/Library/LoginItems/AssistantBackend.app/Contents/Info.plist"
chmod +x "$APP/Contents/Library/LoginItems/AssistantBackend.app/Contents/MacOS/AssistantBackend"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

STAGING="$BUILD_ROOT/dmg"
mkdir -p "$STAGING"
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"
hdiutil create \
  -volname "Personal Assistant" \
  -srcfolder "$STAGING" \
  -ov \
  -format UDZO \
  "$DMG"

printf 'Created app: %s\nCreated installer: %s\n' "$APP" "$DMG"
