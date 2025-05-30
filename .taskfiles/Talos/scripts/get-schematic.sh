#!/bin/bash

# Check if node name is provided
if [ -z "$1" ]; then
    echo "Usage: $0 <node-name>"
    exit 1
fi

NODE_NAME=$1
TALCONFIG_FILE="/workspaces/home-ops-upgrade/kubernetes/bootstrap/talos/talconfig.yaml"

# Extract the talosImageURL for the given node and get just the schematic ID
SCHEMA=$(yq e ".nodes[] | select(.hostname == \"$NODE_NAME\") | .talosImageURL" "$TALCONFIG_FILE" | grep -o '[^/]*$' | cut -d':' -f1)

if [ -z "$SCHEMA" ]; then
    echo "Error: Node $NODE_NAME not found in talconfig.yaml"
    exit 1
fi

echo "$SCHEMA"
