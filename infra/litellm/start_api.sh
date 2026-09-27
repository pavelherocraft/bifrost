#!/bin/bash
# Kill all old api.py and start fresh
pkill -9 -f "opencode-setup/api.py" 2>/dev/null
sleep 2
cd /opt/opencode-setup
nohup python3 api.py >> /tmp/opencode-setup.log 2>&1 &
disown
sleep 2
ps aux | grep "opencode-setup/api.py" | grep -v grep
echo "---"
tail -5 /tmp/opencode-setup.log