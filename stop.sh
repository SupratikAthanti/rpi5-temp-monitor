#!/usr/bin/env bash
#
# Stop ThermalScope - Remove from crontab
#

set -uo pipefail

echo "ThermalScope Stop"
echo "================="
echo ""

# Check if crontab has ThermalScope entries
TEMP_CRON=$(crontab -l 2>/dev/null | grep -c "collect.sh" || echo "0")

if [[ "$TEMP_CRON" -eq "0" ]]; then
    echo "No ThermalScope entries found in crontab."
    echo "Nothing to stop."
    exit 0
fi

echo "Found $TEMP_CRON ThermalScope entry/entries in crontab."
echo ""
echo "Current crontab entries containing 'collect.sh':"
crontab -l 2>/dev/null | grep "collect.sh"
echo ""

read -p "Remove these entries from crontab? (y/N) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Remove lines containing collect.sh
    crontab -l 2>/dev/null | grep -v "collect.sh" | crontab -
    echo "ThermalScope removed from crontab."
    echo ""
    echo "Note: The reset.sh script can clear collected data if desired."
else
    echo "No changes made."
fi
