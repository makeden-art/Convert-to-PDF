#!/bin/bash
pid=$(ps aux | grep "[c]onvert_worker.py" | awk '{print $2}')
if [ -n "$pid" ]; then
    echo "Found PID: $pid"
    timeout 5 strace -c -p $pid 2>&1
else
    echo "Process not found"
fi
