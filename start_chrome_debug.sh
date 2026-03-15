#!/usr/bin/env bash
# Start Chrome with remote debugging so the automation can attach.
# Then log into LinkedIn and CareerShift in that window before running: python main.py

CHROME_APP="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [[ ! -x "$CHROME_APP" ]]; then
  CHROME_APP="/Applications/Chromium.app/Contents/MacOS/Chromium"
fi
if [[ ! -x "$CHROME_APP" ]]; then
  echo "Chrome/Chromium not found. Install Google Chrome or set CHROME_APP."
  exit 1
fi

exec "$CHROME_APP" --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-profile "$@"
