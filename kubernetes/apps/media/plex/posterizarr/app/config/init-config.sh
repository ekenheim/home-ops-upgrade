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
ASSET_DEST_DIR_ROOT="/config" # Posterizarr's Script Root
ASSET_DEST_DIR_TEMP="/config/temp" # Posterizarr's working temp directory

echo "INFO: Copying asset files from $ASSET_SOURCE_DIR..."

# Ensure target directories exist
mkdir -p "$ASSET_DEST_DIR_ROOT"
mkdir -p "$ASSET_DEST_DIR_TEMP"

# Copy asset files
copy_asset() {
    local asset_filename="$1"
    if [ -f "$ASSET_SOURCE_DIR/$asset_filename" ]; then
        echo "INFO: Copying $asset_filename to $ASSET_DEST_DIR_ROOT and $ASSET_DEST_DIR_TEMP"
        cp "$ASSET_SOURCE_DIR/$asset_filename" "$ASSET_DEST_DIR_ROOT/$asset_filename"
        cp "$ASSET_SOURCE_DIR/$asset_filename" "$ASSET_DEST_DIR_TEMP/$asset_filename"
    else
        echo "WARN: $asset_filename not found in $ASSET_SOURCE_DIR"
    fi
}

copy_asset "overlay-innerglow.png"
copy_asset "backgroundoverlay-innerglow.png"
copy_asset "Rocky.ttf"

echo "INFO: Asset files copy process completed."

# Ensure correct permissions if necessary.
# The fsGroup in helmrelease should handle group ownership for the PVC.
# If specific file permissions are needed:
# chmod 644 "$ASSET_DEST_DIR"/*.png "$ASSET_DEST_DIR"/*.ttf

echo "INFO: Init config finished."
exit 0
