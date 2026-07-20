"""Build flyable transition sequences from the FAA CIFP (ARINC 424-18).

This turns the fixed-width ``FAACIFP18`` file (SID ``PD`` / STAR ``PE`` /
approach ``PF`` records) into a flat ``procedures.csv`` where every procedure is
expanded into one sequence per starting point (e.g. an approach with several
IAFs becomes several sequences).

Output columns (no header, so it loads with sqlite ``.import``)::

    airport, procedure, initial_fix, sequence, fix, altitude,
    latitude, longitude, bearing

``latitude``/``longitude`` are decimal degrees looked up from the CIFP fix
records (waypoints, navaids, runways, airports). ``bearing`` is the magnetic
course to the fix (the initial great-circle course from the previous fix,
adjusted by the airport magnetic variation); it is blank for the first fix.

Procedure names come from the d-TPP metafile (``d-TPP_Metafile.xml``) when it is
present: SIDs/STARs link through the ``faanfd18`` field, approaches are matched
by decoding the CIFP identifier against the chart names. When the metafile is
missing, a name synthesized from the CIFP identifier is used instead.

Stdlib only.
"""

import csv
import math
import os
import re
from collections import OrderedDict
from xml.etree import ElementTree as ET

CIFP_FILE = "FAACIFP18"
METAFILE = "d-TPP_Metafile.xml"
OUTPUT = "procedures.csv"

SUBSECTION_SID = "D"
SUBSECTION_STAR = "E"
SUBSECTION_APPROACH = "F"

# Route types (col 20) that begin a flyable sequence, by subsection.
_START_ROUTE_TYPES = {
    SUBSECTION_SID: frozenset({"1", "4", "T"}),   # SID runway transitions
    SUBSECTION_STAR: frozenset({"1", "4"}),        # STAR enroute transitions
}
_COMMON_ROUTE_TYPES = frozenset({"2", "5"})
_COMMON_TRANSITION_IDS = frozenset({"", "ALL"})


# ---------------------------------------------------------------------------
# ARINC 424 leg parsing (offsets 0-indexed, matching cifp.py conventions)
# ---------------------------------------------------------------------------

class Leg(object):
    __slots__ = (
        "airport", "subsection", "route_id", "route_type", "transition_id",
        "seq_no", "fix_id", "fix_region", "fix_section", "fix_subsection",
        "wp_desc", "alt1",
    )

    def __init__(self, line):
        self.airport = line[6:10].strip()
        self.subsection = line[12:13]
        self.route_id = line[13:19].strip()
        self.route_type = line[19:20]
        self.transition_id = line[20:25].strip()
        self.seq_no = line[26:29].strip()
        self.fix_id = line[29:34].strip()
        self.fix_region = line[34:36].strip()
        self.fix_section = line[36:37]
        self.fix_subsection = line[37:38]
        self.wp_desc = line[39:43]
        self.alt1 = line[84:89].strip()

    @property
    def fix_key(self):
        return (self.fix_id, self.fix_region, self.fix_section, self.fix_subsection)

    @property
    def is_map(self):
        # 4th waypoint description code flags the missed-approach point.
        return len(self.wp_desc) >= 4 and self.wp_desc[3] == "M"

    @property
    def altitude(self):
        return parse_altitude(self.alt1)


def parse_altitude(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.upper().startswith("FL"):
        digits = raw[2:].strip()
        return int(digits) * 100 if digits.isdigit() else None
    if raw.isdigit():
        return int(raw)
    digits = "".join(ch for ch in raw if ch.isdigit())
    return int(digits) if digits else None


def read_legs(path):
    legs = []
    with open(path, "r", encoding="latin-1") as fh:
        for line in fh:
            if len(line) < 49:
                continue
            if line[0] != "S" or line[4] != "P":
                continue
            if line[12] not in (SUBSECTION_SID, SUBSECTION_STAR, SUBSECTION_APPROACH):
                continue
            if line[38] not in ("0", "1"):  # skip continuation records
                continue
            legs.append(Leg(line))
    return legs


# ---------------------------------------------------------------------------
# Coordinates and magnetic variation
# ---------------------------------------------------------------------------

_LAT_RE = re.compile(r"^[NS]\d{8}$")   # sign + DDMMSSss
_LON_RE = re.compile(r"^[EW]\d{9}$")   # sign + DDDMMSSss


def _dms_to_decimal(value):
    sign = 1 if value[0] in "NE" else -1
    body = value[1:]
    if value[0] in "NS":
        deg, mm, ss, hs = int(body[0:2]), int(body[2:4]), int(body[4:6]), int(body[6:8])
    else:
        deg, mm, ss, hs = int(body[0:3]), int(body[3:5]), int(body[5:7]), int(body[7:9])
    return sign * (deg + mm / 60.0 + (ss + hs / 100.0) / 3600.0)


def _parse_variation(value):
    """Parse an ARINC magnetic variation field, e.g. ``W0160`` -> -16.0.

    Returned east-positive (E is positive, W negative) so that
    ``magnetic = true - variation``.
    """
    value = (value or "").strip()
    if len(value) < 2 or value[0] not in "EWTG":
        return None
    digits = value[1:]
    if not digits.isdigit():
        return None
    deg = int(digits) / 10.0
    return deg if value[0] in "ET" else -deg


def read_reference_data(path):
    """Return ``(terminal, enroute, variation)`` coordinate indexes from CIFP.

    Terminal fixes (section ``P``: airport waypoints, runways, localizers,
    airports) are keyed by ``(airport, identifier, section, subsection)`` because
    identifiers such as ``RW16`` are only unique within an airport, not within
    an ICAO region. Enroute waypoints and navaids (sections ``E`` / ``D``) are
    keyed by ``(identifier, region, section, subsection)``.
    ``variation`` maps airport ICAO -> magnetic variation (east-positive).
    """
    terminal = {}
    enroute = {}
    variation = {}
    with open(path, "r", encoding="latin-1") as fh:
        for line in fh:
            if len(line) < 51:
                continue
            section = line[4]
            lat, lon = line[32:41], line[41:51]
            if not (_LAT_RE.match(lat) and _LON_RE.match(lon)) and section == "D":
                # DME/TACAN records leave the primary position blank; the
                # navaid position is in the second coordinate field.
                lat, lon = line[55:64], line[64:74]
            if _LAT_RE.match(lat) and _LON_RE.match(lon):
                point = (_dms_to_decimal(lat), _dms_to_decimal(lon))
                if section == "P":
                    subsection = line[12]
                    airport = line[6:10].strip()
                    ident = airport if subsection == "A" else line[13:18].strip()
                    if ident:
                        terminal.setdefault((airport, ident, section, subsection), point)
                else:  # enroute (E) waypoint or navaid (D / DB)
                    subsection = line[5]
                    ident = line[13:18].strip()
                    region = line[19:21].strip()
                    if ident:
                        enroute.setdefault((ident, region, section, subsection), point)
            if section == "P" and line[12] == "A":
                var = _parse_variation(line[51:56])
                if var is not None:
                    variation[line[6:10].strip()] = var
    return terminal, enroute, variation


def lookup_coords(terminal, enroute, airport, fix_key):
    """Resolve ``(lat, lon)`` for a leg's fix, or ``None``."""
    fix_id, region, section, subsection = fix_key
    if section == "P":
        point = terminal.get((airport, fix_id, section, subsection))
        if point is None and subsection == "N":
            # Terminal NDBs are often only stored as enroute NDB (D/B) records.
            point = enroute.get((fix_id, region, "D", "B"))
        return point
    return enroute.get((fix_id, region, section, subsection))


def initial_bearing(lat1, lon1, lat2, lon2):
    """Initial great-circle (true) bearing from point 1 to point 2, degrees."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    y = math.sin(dlon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


# ---------------------------------------------------------------------------
# d-TPP metafile
# ---------------------------------------------------------------------------

_PROC_CHART_CODES = frozenset({"IAP", "DP", "ODP", "STR"})


class AirportCharts(object):
    __slots__ = ("approaches", "departures", "arrivals")

    def __init__(self):
        self.approaches = []   # list of (chart_name, faanfd18)
        self.departures = []
        self.arrivals = []


def _text(elem, tag):
    child = elem.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def parse_metafile(path):
    """Return ``{icao: AirportCharts}`` or ``{}`` when the file is absent."""
    result = {}
    if not path or not os.path.isfile(path):
        return result
    current = None
    for event, elem in ET.iterparse(path, events=("start", "end")):
        if event == "start" and elem.tag == "airport_name":
            key = (elem.get("icao_ident") or elem.get("apt_ident") or "").strip()
            current = AirportCharts()
            result[key] = current
        elif event == "end" and elem.tag == "record":
            if current is not None:
                code = _text(elem, "chart_code")
                if code in _PROC_CHART_CODES:
                    entry = (_text(elem, "chart_name"), _text(elem, "faanfd18"))
                    if code == "IAP":
                        current.approaches.append(entry)
                    elif code in ("DP", "ODP"):
                        current.departures.append(entry)
                    else:
                        current.arrivals.append(entry)
            elem.clear()
        elif event == "end" and elem.tag == "airport_name":
            current = None
            elem.clear()
    return result


# ---------------------------------------------------------------------------
# Approach identifier decoding and chart-name matching
# ---------------------------------------------------------------------------

_APPROACH_TYPE = {
    "I": ("ILS", frozenset({"ILS"})),
    "L": ("LOC", frozenset({"LOC", "ILS"})),   # LOC mins often on the ILS plate
    "B": ("LOC BC", frozenset({"LOCBC"})),
    "R": ("RNAV (GPS)", frozenset({"RNAV"})),
    "H": ("RNAV (RNP)", frozenset({"RNP"})),
    "X": ("LDA", frozenset({"LDA"})),
    "U": ("SDF", frozenset({"SDF"})),
    "D": ("VOR/DME", frozenset({"VORDME", "VOR"})),
    "S": ("VOR", frozenset({"VOR", "VORDME"})),
    "V": ("VOR", frozenset({"VOR", "VORDME"})),
    "N": ("NDB", frozenset({"NDB", "NDBDME"})),
    "Q": ("NDB/DME", frozenset({"NDBDME", "NDB"})),
    "P": ("GPS", frozenset({"GPS", "RNAV"})),
    "G": ("GLS", frozenset({"GLS"})),
    "T": ("TACAN", frozenset({"TACAN"})),
}

_APPROACH_MNEMONIC = {
    "VOR": ("VOR", frozenset({"VOR", "VORDME"})),
    "VDM": ("VOR/DME", frozenset({"VORDME", "VOR"})),
    "NDB": ("NDB", frozenset({"NDB", "NDBDME"})),
    "LOC": ("LOC", frozenset({"LOC"})),
    "LBC": ("LOC BC", frozenset({"LOCBC"})),
    "LDA": ("LDA", frozenset({"LDA"})),
    "GPS": ("GPS", frozenset({"GPS", "RNAV"})),
    "RNV": ("RNAV (GPS)", frozenset({"RNAV"})),
    "TCN": ("TACAN", frozenset({"TACAN"})),
}


class ApproachKey(object):
    __slots__ = ("prefix", "tokens", "runway", "side", "variant", "circling")

    def __init__(self, prefix, tokens, runway="", side="", variant="", circling=""):
        self.prefix = prefix
        self.tokens = tokens
        self.runway = runway
        self.side = side
        self.variant = variant
        self.circling = circling

    @property
    def is_circling(self):
        return not self.runway


def decode_approach_id(route_id):
    rid = (route_id or "").strip().upper()
    if not rid:
        return None
    m = re.match(r"^([A-Z]{3})-?([A-Z0-9])$", rid)
    if m and m.group(1) in _APPROACH_MNEMONIC:
        prefix, tokens = _APPROACH_MNEMONIC[m.group(1)]
        return ApproachKey(prefix, tokens, circling=m.group(2))
    m = re.match(r"^([A-Z])(\d{2})[-]?([LCR]?)([UVWXYZ]?)$", rid)
    if m and m.group(1) in _APPROACH_TYPE:
        prefix, tokens = _APPROACH_TYPE[m.group(1)]
        return ApproachKey(prefix, tokens, runway=m.group(2),
                           side=m.group(3), variant=m.group(4))
    letter = rid[0]
    if letter in _APPROACH_TYPE:
        prefix, tokens = _APPROACH_TYPE[letter]
        digits = re.search(r"(\d{2})", rid)
        return ApproachKey(prefix, tokens, runway=digits.group(1) if digits else "")
    return None


def synthesize_approach_name(key):
    if key.is_circling:
        return "%s-%s" % (key.prefix, key.circling) if key.circling else key.prefix
    rwy = key.runway.lstrip("0") or key.runway
    parts = [key.prefix]
    if key.variant:
        parts.append(key.variant)
    parts.append("RWY %s%s" % (rwy, key.side))
    return " ".join(parts)


_CONT_RE = re.compile(r",\s*CONT\.\d+$", re.I)
_CHART_TOKEN_PATTERNS = [
    ("RNP", r"RNAV \(RNP\)"),
    ("RNAV", r"RNAV \(GPS\)"),
    ("LOCBC", r"LOC(?:/DME)? BC"),
    ("ILS", r"ILS"),
    ("LDA", r"LDA"),
    ("SDF", r"SDF"),
    ("LOC", r"LOC"),
    ("VORDME", r"VOR/DME"),
    ("VOR", r"VOR"),
    ("NDBDME", r"NDB/DME"),
    ("NDB", r"NDB"),
    ("TACAN", r"TACAN"),
    ("GLS", r"GLS"),
    ("GPS", r"GPS"),
]
_CHART_TOKEN_RE = [(tok, re.compile(pat)) for tok, pat in _CHART_TOKEN_PATTERNS]
_RWY_RE = re.compile(r"\bRWY\s+(\d{1,2})([LCR]?)")
_COMBINED_SIDE_RE = re.compile(r"\bRWY\s+\d{1,2}\s*[LCR]?(?:\s*/\s*[LCR])+")
_CIRCLING_RE = re.compile(r"-([A-Z0-9])$")
_VARIANT_RE = re.compile(r"(?<![A-Z])\b([UVWXYZ])\b(?![A-Z])")
_NOISE_MARKERS = ("CAT", "PRM", "HI-", "COPTER", "CONVERGING", "(SA")


class ChartKey(object):
    __slots__ = ("tokens", "runway", "side", "variant", "circling", "name")

    def __init__(self, tokens, runway, side, variant, circling, name):
        self.tokens = tokens
        self.runway = runway
        self.side = side
        self.variant = variant
        self.circling = circling
        self.name = name


def _chart_tokens(name):
    found = set()
    for tok, rx in _CHART_TOKEN_RE:
        if rx.search(name):
            found.add(tok)
    if "VORDME" in found:
        found.add("VOR")
    if "NDBDME" in found:
        found.add("NDB")
    return found


def parse_chart_name(chart_name):
    name = _CONT_RE.sub("", chart_name).strip().upper()
    tokens = _chart_tokens(name)
    rwy = _RWY_RE.search(name)
    circ_m = _CIRCLING_RE.search(name)
    circ = "" if rwy else (circ_m.group(1) if circ_m else "")
    side = "" if _COMBINED_SIDE_RE.search(name) else (rwy.group(2) if rwy else "")
    head = _RWY_RE.split(name)[0]
    var_m = _VARIANT_RE.search(head)
    return ChartKey(
        tokens=tokens,
        runway=rwy.group(1) if rwy else "",
        side=side,
        variant=var_m.group(1) if var_m else "",
        circling=circ,
        name=_CONT_RE.sub("", chart_name).strip(),
    )


def _noise_penalty(name):
    up = name.upper()
    return sum(1 for m in _NOISE_MARKERS if m in up)


def _score(ak, ck):
    if not (ak.tokens & ck.tokens):
        return None
    if ak.is_circling:
        if not ck.runway and ak.circling and ck.circling and ak.circling != ck.circling:
            return None
        if ck.runway:
            return None
    else:
        if ck.runway and ak.runway.lstrip("0") != ck.runway.lstrip("0"):
            return None
        if ak.side and ck.side and ak.side != ck.side:
            return None
    score = 100
    if ak.variant and ck.variant:
        score += 20 if ak.variant == ck.variant else -50
    elif ak.variant != ck.variant:
        score -= 5
    if ak.side and ck.side and ak.side == ck.side:
        score += 5
    score -= 3 * _noise_penalty(ck.name)
    score -= len(ck.name) // 40
    return score


def match_approach_name(route_id, charts):
    key = decode_approach_id(route_id)
    if key is None:
        return route_id
    best = None
    for chart_name, _faanfd18 in charts:
        ck = parse_chart_name(chart_name)
        s = _score(key, ck)
        if s is None:
            continue
        if best is None or s > best[0]:
            best = (s, ck.name)
    if best is not None:
        return best[1]
    return synthesize_approach_name(key)


def build_sidstar_names(charts, route_ids_by_subsection):
    """Map ``(subsection, route_id) -> chart_name`` via the faanfd18 link."""
    result = {}
    if charts is None:
        return result
    for subsection, records in (("D", charts.departures), ("E", charts.arrivals)):
        known = route_ids_by_subsection.get(subsection, set())
        for chart_name, faanfd18 in records:
            name = _CONT_RE.sub("", chart_name).strip()
            for tok in (t.strip().upper() for t in faanfd18.split(".") if t.strip()):
                if tok in known:
                    result.setdefault((subsection, tok), name)
    return result


# ---------------------------------------------------------------------------
# Sequence construction
# ---------------------------------------------------------------------------

def _seq_int(leg):
    try:
        return int(leg.seq_no)
    except ValueError:
        return 0


def _truncate_at_map(legs):
    out = []
    for leg in legs:
        out.append(leg)
        if leg.is_map:
            return out
    return legs


def _collapse(legs):
    """Drop fix-less legs and collapse consecutive duplicate fixes.

    Returns a list of ``[fix_id, altitude, fix_key]``.
    """
    out = []
    for leg in legs:
        if not leg.fix_id:
            continue
        if out and out[-1][0] == leg.fix_id:
            if out[-1][1] is None:
                out[-1][1] = leg.altitude
            continue
        out.append([leg.fix_id, leg.altitude, leg.fix_key])
    return out


def _partition(subsection, legs):
    starts = OrderedDict()
    common = []
    common_fallback = []
    for leg in legs:
        if subsection == SUBSECTION_APPROACH:
            is_start = leg.transition_id != ""
            is_common = leg.transition_id == ""
        else:
            is_start = leg.route_type in _START_ROUTE_TYPES.get(subsection, frozenset())
            is_common = leg.route_type in _COMMON_ROUTE_TYPES
        if is_start:
            starts.setdefault(leg.transition_id, []).append(leg)
        elif is_common:
            common_fallback.append(leg)
            if leg.transition_id in _COMMON_TRANSITION_IDS:
                common.append(leg)
    if not common:
        common = common_fallback
    common.sort(key=_seq_int)
    for tid in starts:
        starts[tid].sort(key=_seq_int)
    return starts, common


def build_sequences(legs, metafile, terminal=None, enroute=None, variation=None):
    """Yield rows ``(airport, procedure, initial_fix, sequence, fix, altitude,
    latitude, longitude, bearing)``."""
    terminal = terminal or {}
    enroute = enroute or {}
    variation = variation or {}
    # Group legs by (airport, subsection, route_id), preserving order.
    groups = OrderedDict()
    route_ids = {}
    for leg in legs:
        groups.setdefault((leg.airport, leg.subsection, leg.route_id), []).append(leg)
        route_ids.setdefault(leg.airport, {}).setdefault(leg.subsection, set()).add(
            leg.route_id
        )

    sidstar_cache = {}

    for (airport, subsection, route_id), group in groups.items():
        charts = metafile.get(airport)
        if subsection == SUBSECTION_APPROACH:
            procedure = match_approach_name(
                route_id, charts.approaches if charts else []
            )
        else:
            if airport not in sidstar_cache:
                sidstar_cache[airport] = build_sidstar_names(
                    charts, route_ids.get(airport, {})
                )
            procedure = sidstar_cache[airport].get((subsection, route_id), route_id)

        starts, common = _partition(subsection, group)
        if subsection == SUBSECTION_APPROACH:
            common = _truncate_at_map(common)

        sequences = []
        if starts:
            for transition_legs in starts.values():
                sequences.append(_collapse(list(transition_legs) + list(common)))
        elif common:
            sequences.append(_collapse(common))

        var = variation.get(airport)
        for fixes in sequences:
            if not fixes:
                continue
            initial_fix = fixes[0][0]
            prev_ll = None
            for i, (fix, altitude, fix_key) in enumerate(fixes, start=1):
                ll = lookup_coords(terminal, enroute, airport, fix_key)
                lat = "" if ll is None else round(ll[0], 6)
                lon = "" if ll is None else round(ll[1], 6)
                bearing = ""
                if prev_ll is not None and ll is not None:
                    true_brg = initial_bearing(prev_ll[0], prev_ll[1], ll[0], ll[1])
                    if var is not None:
                        true_brg = (true_brg - var) % 360.0
                    bearing = round(true_brg, 1)
                if ll is not None:
                    prev_ll = ll
                yield (airport, procedure, initial_fix, i, fix,
                       "" if altitude is None else altitude, lat, lon, bearing)


def _clean(value):
    """Strip commas so the naive sqlite ``.import`` (comma split) stays intact."""
    return str(value).replace(",", " ").strip()


def parse_procedures(cifp_path=CIFP_FILE, metafile_path=METAFILE, out_path=OUTPUT):
    legs = read_legs(cifp_path)
    metafile = parse_metafile(metafile_path)
    terminal, enroute, variation = read_reference_data(cifp_path)
    with open(out_path, "w+", newline="") as fh:
        writer = csv.writer(fh)
        for row in build_sequences(legs, metafile, terminal, enroute, variation):
            airport, procedure, initial_fix, seq, fix, altitude, lat, lon, bearing = row
            writer.writerow([
                _clean(airport), _clean(procedure), _clean(initial_fix),
                seq, _clean(fix), _clean(altitude), lat, lon, bearing,
            ])


if __name__ == "__main__":
    parse_procedures()
