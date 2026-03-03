#!/bin/bash
# Move to TradingClaw directory
cd /home/ubuntu/TradingClaw

# Create logs directory if it doesn't exist
mkdir -p logs

# Infinite Loop (Watchdog)
while true
do
    echo "--- [Watchdog] : $(date) : TradingClaw daemon started ---" >> watchdog_claw.log
    
    # Run the autonomous daemon mode
    # Default interval is 15 minutes as configured in main.py
    python3 -u main.py --daemon >> logs/autonomous.log 2>&1
    
    echo "--- [Watchdog] : $(date) : TradingClaw daemon exited. Restarting in 10s. ---" >> watchdog_claw.log
    sleep 10
done
