#!/bin/bash
# One-time: installs (or removes) the weekday schedule on this Mac.
#   07:40 opens the Kite login   ·   09:00 starts the PAPER guard (stops 15:40)
# Run again and type "remove" to uninstall.
cd "$(dirname "$0")" || exit 1
LA="$HOME/Library/LaunchAgents"
UIDN=$(id -u)
read -r -p "Type 'install' to add the schedule, or 'remove' to take it away: " ACT
if [ "$ACT" != "install" ] && [ "$ACT" != "remove" ]; then
  echo "Nothing changed (you typed '$ACT'). Run it again and type install or remove."
  read -r -p "Press Enter to close."
  exit 1
fi
for P in com.vibedesk.kite-login com.vibedesk.guard; do
  launchctl bootout "gui/$UIDN/$P" 2>/dev/null
  rm -f "$LA/$P.plist"
done
if [ "$ACT" = "install" ]; then
  mkdir -p "$LA" ../logs
  chmod +x guard_daemon.sh kite_login.command
  for P in com.vibedesk.kite-login com.vibedesk.guard; do
    cp "launchd/$P.plist" "$LA/" && launchctl bootstrap "gui/$UIDN" "$LA/$P.plist" && echo "installed $P"
  done
  echo
  echo "Done. Weekdays: 07:40 Kite login opens, 09:00 paper guard starts (needs the Mac awake and the lid open)."
  echo "Optional, so the Mac wakes itself:  sudo pmset repeat wakeorpoweron MTWRF 07:35:00"
else
  echo "Schedule removed."
fi
read -r -p "Press Enter to close."
