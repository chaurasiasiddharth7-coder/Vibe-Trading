#!/bin/bash
# Opens the 9router dashboard that runs on your server, through a private ssh tunnel (nothing is public).
# Keep this window open while you use the dashboard; close it to end the tunnel.
cd "$(dirname "$0")/.." || exit 1
H="$(cat scripts/.deploy_host)"
LPORT=20138
echo "Opening a private tunnel to the router on $H ..."
( sleep 3; open "http://localhost:$LPORT/dashboard" ) &
echo "Dashboard: http://localhost:$LPORT/dashboard   (password: scripts/.router_login)"
echo "Close this window when you're done."
ssh -N -o ExitOnForwardFailure=yes -L "$LPORT:127.0.0.1:20128" "$H"
