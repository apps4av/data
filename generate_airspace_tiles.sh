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

POLYGONS_FILE="${OUTPUT_DIR}/polygons.mbtiles"
LABELS_FILE="${OUTPUT_DIR}/labels.mbtiles"

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

# Step 1: Generate polygon layers
echo "Generating polygon layers..."
POLYGON_ARGS=(
    --output="$POLYGONS_FILE"
    --force
    --minimum-zoom=0
    --maximum-zoom=10
    --drop-rate=1
    --no-tile-size-limit
    --no-feature-limit
    --gamma=0
    --no-line-simplification
    --no-tiny-polygon-reduction
    --named-layer="class_airspace:$CLASS_AIRSPACE_FILE"
)
if [ -f "$SUA_AIRSPACE_FILE" ]; then
    POLYGON_ARGS+=(--named-layer="sua_airspace:$SUA_AIRSPACE_FILE")
fi
tippecanoe "${POLYGON_ARGS[@]}"

# Step 2: Generate label point layers (centroids)
echo "Generating label point layers..."
LABEL_ARGS=(
    --output="$LABELS_FILE"
    --force
    --minimum-zoom=5
    --maximum-zoom=10
    --drop-rate=1
    --no-tile-size-limit
    --no-feature-limit
    --convert-polygons-to-label-points
    --named-layer="class_airspace_labels:$CLASS_AIRSPACE_FILE"
)
if [ -f "$SUA_AIRSPACE_FILE" ]; then
    LABEL_ARGS+=(--named-layer="sua_airspace_labels:$SUA_AIRSPACE_FILE")
fi
tippecanoe "${LABEL_ARGS[@]}"

# Step 3: Combine into final output
echo "Combining layers..."
tile-join --output="$OUTPUT_FILE" --force --name="Airspace" \
    --description="FAA Class Airspace and Special Use Airspace" \
    "$POLYGONS_FILE" "$LABELS_FILE"

# Cleanup temp files
rm -f "$POLYGONS_FILE" "$LABELS_FILE"

echo ""
echo "=========================================="
echo "Done: $OUTPUT_FILE"
echo "=========================================="
ls -lh "$OUTPUT_FILE"

echo ""
echo "Layers included:"
echo "  - class_airspace (Class B, C, D, E airspace polygons)"
echo "  - class_airspace_labels (Class airspace label points)"
if [ -f "$SUA_AIRSPACE_FILE" ]; then
    echo "  - sua_airspace (MOAs, Restricted, Warning, Alert, Prohibited, NSA polygons)"
    echo "  - sua_airspace_labels (SUA label points)"
fi
