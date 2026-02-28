#!/bin/bash
# Generate Airspace MBTiles from GeoJSON using tippecanoe
# Includes Class Airspace and Special Use Airspace (SUA/SAA)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLASS_AIRSPACE_FILE="${SCRIPT_DIR}/Additional_Data/Shape_Files/Class_Airspace.geojson"
SUA_AIRSPACE_FILE="${SCRIPT_DIR}/Additional_Data/Shape_Files/SUA_Airspace.geojson"
OUTPUT_DIR="${SCRIPT_DIR}/maps"
OUTPUT_FILE="${OUTPUT_DIR}/nasr.mbtiles"

# Check for tippecanoe
if ! command -v tippecanoe &> /dev/null; then
    echo "Error: tippecanoe not installed. Install with: brew install tippecanoe"
    exit 1
fi

# Check for Class Airspace
if [ ! -f "$CLASS_AIRSPACE_FILE" ]; then
    echo "Error: Class Airspace file not found: $CLASS_AIRSPACE_FILE"
    exit 1
fi

# Generate SUA GeoJSON from XML files if needed
if [ ! -f "$SUA_AIRSPACE_FILE" ]; then
    echo "Generating SUA Airspace GeoJSON from XML files..."
    if command -v python3 &> /dev/null; then
        python3 "${SCRIPT_DIR}/saa_to_geojson.py" \
            --input-dir "${SCRIPT_DIR}" \
            --output "$SUA_AIRSPACE_FILE"
    else
        echo "Warning: python3 not found, skipping SUA generation"
    fi
fi

mkdir -p "$OUTPUT_DIR"

echo ""
echo "=========================================="
echo "Generating Airspace MBTiles"
echo "=========================================="
echo "Class Airspace: $CLASS_AIRSPACE_FILE"
if [ -f "$SUA_AIRSPACE_FILE" ]; then
    echo "SUA Airspace:   $SUA_AIRSPACE_FILE"
fi
echo "Output:         $OUTPUT_FILE"
echo ""

# Build tippecanoe command with available layers
# -r1 or --drop-rate=1: Don't drop any features
# -pk: No tile size limit
# -pf: No feature limit
# --gamma=0: Don't gamma-correct (prevents polygon thinning)
TIPPECANOE_ARGS=(
    --output="$OUTPUT_FILE"
    --force
    --name="Airspace"
    --description="FAA Class Airspace and Special Use Airspace"
    --minimum-zoom=0
    --maximum-zoom=10
    --drop-rate=1
    --no-tile-size-limit
    --no-feature-limit
    --gamma=0
)

# Add Class Airspace layer
TIPPECANOE_ARGS+=(
    --named-layer="class_airspace:$CLASS_AIRSPACE_FILE"
)

# Add SUA layer if available
if [ -f "$SUA_AIRSPACE_FILE" ]; then
    TIPPECANOE_ARGS+=(
        --named-layer="sua_airspace:$SUA_AIRSPACE_FILE"
    )
fi

echo "Running tippecanoe..."
tippecanoe "${TIPPECANOE_ARGS[@]}"

echo ""
echo "=========================================="
echo "Done: $OUTPUT_FILE"
echo "=========================================="
ls -lh "$OUTPUT_FILE"

echo ""
echo "Layers included:"
echo "  - class_airspace (Class B, C, D, E airspace)"
if [ -f "$SUA_AIRSPACE_FILE" ]; then
    echo "  - sua_airspace (MOAs, Restricted, Warning, Alert, Prohibited, NSA)"
fi
