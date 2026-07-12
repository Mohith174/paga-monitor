#!/bin/bash
# PAGA Lead Gen - One-command startup
# Run both web app and scheduler together

echo "=========================================="
echo "PAGA Lead Gen - Starting..."
echo "=========================================="

# Find the best Python executable
if [ -f "./.venv/bin/python3" ]; then
    PYTHON="./.venv/bin/python3"
elif command -v python3.11 &> /dev/null; then
    PYTHON="python3.11"
elif command -v python3 &> /dev/null; then
    PYTHON="python3"
else
    echo "❌ Python 3 not found"
    exit 1
fi

echo "Using Python: $($PYTHON --version) ($PYTHON)"


# Check dependencies
echo "Checking dependencies..."
$PYTHON -c "import flask" 2>/dev/null || { echo "❌ Flask not installed. Run: pip install -r requirements-scraper.txt"; exit 1; }
$PYTHON -c "import playwright" 2>/dev/null || { echo "❌ Playwright not installed. Run: pip install -r requirements-scraper.txt"; exit 1; }

echo "✓ Dependencies OK"
echo ""

# Create database if needed
$PYTHON -c "from database import Database; Database()"

# Start web app in background
echo "Starting web dashboard on http://localhost:5001 ..."
$PYTHON app.py > web.log 2>&1 &
WEB_PID=$!
sleep 2

# Start scheduler in background
echo "Starting auto-scraper (every 5 minutes)..."
$PYTHON scheduler.py > scheduler.log 2>&1 &
SCHEDULER_PID=$!

echo ""
echo "=========================================="
echo "✓ System Running!"
echo "=========================================="
echo ""
echo "  Web Dashboard: http://localhost:5001"
echo "  Web PID:       $WEB_PID"
echo "  Scheduler PID: $SCHEDULER_PID"
echo ""
echo "Logs:"
echo "  Web app:   tail -f web.log"
echo "  Scheduler: tail -f scheduler.log"
echo ""
echo "To stop:"
echo "  kill $WEB_PID $SCHEDULER_PID"
echo ""
echo "Press Ctrl+C to stop all services"
echo "=========================================="

# Wait for Ctrl+C
trap "kill $WEB_PID $SCHEDULER_PID 2>/dev/null; echo 'Stopped'; exit" INT

# Keep script running
while true; do
    sleep 1
done
