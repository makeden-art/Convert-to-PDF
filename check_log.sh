#!/bin/bash
log=$(docker exec convert-to-pdf-service find /tmp -name convert.log -type f | head -1)
if [ -n "$log" ]; then
    echo "Found log: $log"
    docker exec convert-to-pdf-service cat "$log"
else
    echo "No log found in container"
fi
