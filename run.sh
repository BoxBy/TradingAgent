#!/bin/bash
# Move to TradingAgent directory
cd /home/ubuntu/TradingAgent

# Create logs directory if it doesn't exist
mkdir -p logs

# Run the autonomous daemon mode directly
# This script should be run from within an existing screen session:
# screen -S tradingagent
# ./run.sh

# Default interval is 15 minutes as configured in main.py
echo "--- [TradingAgent] : $(date) : Starting in foreground (run inside screen for persistence) ---" >> watchdog_claw.log
python3 -u main.py --daemon >> logs/autonomous.log 2>&1

# If the script exits, log it and exit (don't auto-restart - let the user restart)
echo "--- [TradingAgent] : $(date) : Exited ---" >> watchdog_claw.log
