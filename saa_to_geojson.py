"""
Convert FAA Special Activity Airspace (SAA) XML files to GeoJSON.

Parses AIXM 5 formatted XML files to extract:
- MOAs (Military Operations Areas)
- Restricted Areas (R-xxxx)
- Warning Areas (W-xxxx)  
- Alert Areas (A-xxxx)
- National Security Areas (NSA)
- Prohibited Areas (P-xxxx)
"""

import json
import os
import re
import glob
from xml.etree import ElementTree as ET
from typing import Optional


NAMESPACES = {
    'aixm': 'http://www.aixm.aero/schema/5.0',
    'gml': 'http://www.opengis.net/gml/3.2',
    'saa': 'urn:us:gov:dot:faa:aim:saa',
    'sua': 'urn:us:gov:dot:faa:aim:saa:sua',
}


def parse_saa_xml(filepath: str) -> Optional[dict]:
    """Parse a single SAA XML file and return a GeoJSON feature."""
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"  Warning: Could not parse {filepath}: {e}")
        return None

    airspace = root.find('.//aixm:Airspace', NAMESPACES)
    if airspace is None:
        return None

    timeslice = airspace.find('.//aixm:AirspaceTimeSlice', NAMESPACES)
    if timeslice is None:
        return None

    designator_el = timeslice.find('aixm:designator', NAMESPACES)
    name_el = timeslice.find('aixm:name', NAMESPACES)
    
    designator = designator_el.text if designator_el is not None else ''
    name = name_el.text if name_el is not None else ''

    volume = timeslice.find('.//aixm:AirspaceVolume', NAMESPACES)
    if volume is None:
        return None

    upper_limit_el = volume.find('aixm:upperLimit', NAMESPACES)
    upper_ref_el = volume.find('aixm:upperLimitReference', NAMESPACES)
    lower_limit_el = volume.find('aixm:lowerLimit', NAMESPACES)
    lower_ref_el = volume.find('aixm:lowerLimitReference', NAMESPACES)

    upper_limit = upper_limit_el.text if upper_limit_el is not None else ''
    upper_ref = upper_ref_el.text if upper_ref_el is not None else ''
    lower_limit = lower_limit_el.text if lower_limit_el is not None else ''
    lower_ref = lower_ref_el.text if lower_ref_el is not None else ''

    upper_uom = upper_limit_el.get('uom', '') if upper_limit_el is not None else ''
    lower_uom = lower_limit_el.get('uom', '') if lower_limit_el is not None else ''

    sua_ext = timeslice.find('.//sua:AirspaceExtension', NAMESPACES)
    sua_type = ''
    if sua_ext is not None:
        sua_type_el = sua_ext.find('sua:suaType', NAMESPACES)
        if sua_type_el is not None:
            sua_type = sua_type_el.text

    type_map = {
        'MOA': 'MOA',
        'RA': 'RESTRICTED',
        'WA': 'WARNING',
        'AA': 'ALERT',
        'PA': 'PROHIBITED',
        'NSA': 'NSA',
    }
    sua_type = type_map.get(sua_type, sua_type)

    if not sua_type or sua_type == 'OTHER':
        if designator.startswith('M'):
            sua_type = 'MOA'
        elif designator.startswith('R'):
            sua_type = 'RESTRICTED'
        elif designator.startswith('W'):
            sua_type = 'WARNING'
        elif designator.startswith('A'):
            sua_type = 'ALERT'
        elif designator.startswith('P'):
            sua_type = 'PROHIBITED'
        elif designator.startswith('N'):
            sua_type = 'NSA'
        else:
            sua_type = 'OTHER'

    pos_elements = volume.findall('.//gml:pos', NAMESPACES)
    if not pos_elements:
        return None

    coordinates = []
    for pos in pos_elements:
        try:
            parts = pos.text.strip().split()
            if len(parts) >= 2:
                lon = float(parts[0])
                lat = float(parts[1])
                coordinates.append([lon, lat])
        except (ValueError, AttributeError):
            continue

    if len(coordinates) < 3:
        return None

    if coordinates[0] != coordinates[-1]:
        coordinates.append(coordinates[0])

    upper_val = format_altitude(upper_limit, upper_uom, upper_ref)
    lower_val = format_altitude(lower_limit, lower_uom, lower_ref)

    feature = {
        'type': 'Feature',
        'properties': {
            'DESIGNATOR': designator,
            'NAME': name,
            'TYPE': sua_type,
            'UPPER_VAL': upper_val,
            'LOWER_VAL': lower_val,
            'UPPER_LIMIT': upper_limit,
            'UPPER_UOM': upper_uom,
            'UPPER_REF': upper_ref,
            'LOWER_LIMIT': lower_limit,
            'LOWER_UOM': lower_uom,
            'LOWER_REF': lower_ref,
        },
        'geometry': {
            'type': 'Polygon',
            'coordinates': [coordinates]
        }
    }

    return feature


def format_altitude(limit: str, uom: str, ref: str) -> str:
    """Format altitude for display."""
    if not limit:
        return ''
    
    limit = limit.strip()
    uom = uom.strip() if uom else ''
    ref = ref.strip() if ref else ''
    
    if limit.upper() in ('GND', 'SFC', 'SURFACE'):
        return 'SFC'
    elif limit.upper() == 'UNL' or limit.upper() == 'UNLIMITED':
        return 'UNL'
    elif uom == 'FL':
        return f"FL{limit}"
    elif uom == 'FT':
        if ref == 'MSL':
            return f"{limit} MSL"
        elif ref in ('SFC', 'GND', 'AGL'):
            return f"{limit} AGL"
        elif ref:
            return f"{limit} {ref}"
        else:
            return f"{limit} FT"
    else:
        return f"{limit} {uom} {ref}".strip()


def convert_saa_to_geojson(xml_dir: str, output_file: str) -> int:
    """Convert all SAA XML files in a directory to a single GeoJSON file."""
    
    xml_files = glob.glob(os.path.join(xml_dir, '*.xml'))
    print(f"Found {len(xml_files)} XML files in {xml_dir}")
    
    features = []
    for filepath in xml_files:
        feature = parse_saa_xml(filepath)
        if feature:
            features.append(feature)
    
    geojson = {
        'type': 'FeatureCollection',
        'features': features
    }
    
    with open(output_file, 'w') as f:
        json.dump(geojson, f)
    
    print(f"Wrote {len(features)} features to {output_file}")
    return len(features)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Convert SAA XML files to GeoJSON')
    parser.add_argument('--input-dir', '-i', default='.', 
                        help='Directory containing SAA XML files (default: current directory)')
    parser.add_argument('--output', '-o', default='Additional_Data/Shape_Files/SUA_Airspace.geojson',
                        help='Output GeoJSON file path')
    
    args = parser.parse_args()
    
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    
    count = convert_saa_to_geojson(args.input_dir, args.output)
    
    if count > 0:
        print(f"\nSuccessfully converted {count} Special Use Airspace areas to GeoJSON")
        print(f"Output: {args.output}")
    else:
        print("\nNo valid airspace features found")


if __name__ == '__main__':
    main()
