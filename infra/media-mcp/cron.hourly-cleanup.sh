#!/bin/sh
# media-mcp: delete generated/uploaded files older than 24h
find /opt/media-mcp/files -type f -mmin +1440 -delete 2>/dev/null
