#!/bin/bash
# Generate Class Airspace MBTiles from GeoJSON using tippecanoe

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INPUT_FILE="${SCRIPT_DIR}/Additional_Data/Shape_Files/Class_Airspace.geojson"
OUTPUT_DIR="${SCRIPT_DIR}/maps"
OUTPUT_FILE="${OUTPUT_DIR}/nasr.mbtiles"

if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: Input file not found: $INPUT_FILE"
    exit 1
fi

if ! command -v tippecanoe &> /dev/null; then
    echo "Error: tippecanoe not installed. Install with: brew install tippecanoe"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "Generating MBTiles..."
echo "Input:  $INPUT_FILE"
echo "Output: $OUTPUT_FILE"

tippecanoe \
    --output="$OUTPUT_FILE" \
    --force \
    --layer="class_airspace" \
    --name="Class Airspace" \
    --minimum-zoom=0 \
    --maximum-zoom=10 \
    --simplification=15 \
    --detect-shared-borders \
    --drop-densest-as-needed \
    --generate-polygon-centers \
    --include="NAME" \
    --include="CLASS" \
    --include="LOCAL_TYPE" \
    --include="LOWER_VAL" \
    "$INPUT_FILE"

echo ""
echo "Done: $OUTPUT_FILE"
ls -lh "$OUTPUT_FILE"
