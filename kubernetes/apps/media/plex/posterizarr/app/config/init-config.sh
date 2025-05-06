#!/bin/sh

# Use yq to process the template and replace environment variables
echo "Reading template file..."
cat /app/config-file/config.json.template > /tmp/config.json

# Replace environment variables using yq
echo "Processing configuration..."
yq -i '.ApiPart.FanartTvAPIKey = env(FANARTTV_API_KEY)' /tmp/config.json
yq -i '.ApiPart.tvdbapi = env(TVDB_API_KEY)' /tmp/config.json
yq -i '.ApiPart.tmdbtoken = env(TMDB_READ_API_TOKEN)' /tmp/config.json
yq -i '.ApiPart.PlexToken = env(PLEX_TOKEN)' /tmp/config.json

# Move the processed file to the app config directory
mkdir -p /config
chmod 660 /tmp/config.json

mv /tmp/config.json /config/config.json


echo "Config processed successfully"


# Remove running file if it exists
if [ -f "/config/temp/Posterizarr.Running" ]; then
    rm /config/temp/Posterizarr.Running
fi

echo "Container ready to start"

# NEW: Copy asset files from the ConfigMap mount to the PVC
ASSET_SOURCE_DIR="/app/config-file"
ASSET_DEST_DIR="/config" # This is the PVC

echo "INFO: Copying asset files from $ASSET_SOURCE_DIR to $ASSET_DEST_DIR..."

# Ensure target directory exists (it should, as it's a PVC mount)
mkdir -p "$ASSET_DEST_DIR"

# Copy asset files
if [ -f "$ASSET_SOURCE_DIR/overlay-innerglow.png" ]; then
    cp "$ASSET_SOURCE_DIR/overlay-innerglow.png" "$ASSET_DEST_DIR/overlay-innerglow.png"
else
    echo "WARN: overlay-innerglow.png not found in $ASSET_SOURCE_DIR"
fi

if [ -f "$ASSET_SOURCE_DIR/backgroundoverlay-innerglow.png" ]; then
    cp "$ASSET_SOURCE_DIR/backgroundoverlay-innerglow.png" "$ASSET_DEST_DIR/backgroundoverlay-innerglow.png"
else
    echo "WARN: backgroundoverlay-innerglow.png not found in $ASSET_SOURCE_DIR"
fi

if [ -f "$ASSET_SOURCE_DIR/Rocky.ttf" ]; then
    cp "$ASSET_SOURCE_DIR/Rocky.ttf" "$ASSET_DEST_DIR/Rocky.ttf"
else
    echo "WARN: Rocky.ttf not found in $ASSET_SOURCE_DIR"
fi

echo "INFO: Asset files copy process completed."

# Ensure correct permissions if necessary.
# The fsGroup in helmrelease should handle group ownership for the PVC.
# If specific file permissions are needed:
# chmod 644 "$ASSET_DEST_DIR"/*.png "$ASSET_DEST_DIR"/*.ttf

echo "INFO: Init config finished."
exit 0