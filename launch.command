#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "First run — setting up environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

pip install -r requirements.txt -q 2>&1 | grep -v "already satisfied"

echo "Starting Smart Invoice on http://localhost:5050"
sleep 0.5 && open "http://localhost:5050" &
python3 app.py
