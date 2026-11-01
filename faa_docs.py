#!/usr/bin/env python3
"""
Download a set of FAA documents for general aviation pilots (PDF) and bundle
them into several zips, one per category, for the Avare / avarex app.

Every zip is extracted into the app's root (data) folder and carries its own
manifest named after the zip (no ".zip"):

    FAA_AIM.zip                 AIM/...        Aeronautical Information Manual
    FAA_CFR14.zip               CFR14/...      14 CFR parts used by GA pilots (govinfo.gov) + NTSB 830
    FAA_HANDBOOKS.zip           Handbooks/...  PHAK, AFH, IFH, IPH, risk management, instructor, weather
    FAA_HANDBOOKS_SPECIALTY.zip Handbooks/...  helicopter, glider, weight and balance, remote pilot, safety guides
    FAA_ACS.zip                 ACS/...        Airman Certification Standards (all levels)
    FAA_AC.zip                  AC/...         frequently used Advisory Circulars

The manifest follows the same convention as the chart / databases zips: line 1
is the cycle number, every following line is a file (relative to the root
folder) that the app deletes when the user deletes that item. It is written as
the last entry of each zip.

Only the standard library and curl are needed (the FAA CDN answers urllib with
errors, so curl with a browser user agent is used, same as common.py). If
ghostscript (gs) is installed, PDFs over 10 MB are recompressed when that makes
them smaller.

Usage:
    python3 faa_docs.py                    # download + build all zips in the current folder
    python3 faa_docs.py --list             # resolve URLs only, print the manifests
    python3 faa_docs.py --only AIM,ACS     # only some folders
    python3 faa_docs.py --out-dir dist     # write the zips to dist/
    python3 faa_docs.py --prefix FAA       # put everything under FAA/ inside the zips
    python3 faa_docs.py --cycle 2611       # write another cycle number to the manifests
"""

import argparse
import datetime
import os
import re
import shutil
import string
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import cycle

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REFERER = "https://www.faa.gov/"

FAA = "https://www.faa.gov"
AIM_URL = FAA + "/air_traffic/publications/media/aim.pdf"
HB_PAGE = FAA + "/regulations_policies/handbooks_manuals/aviation"
HB_FILES = HB_PAGE.replace(FAA, FAA + "/sites/faa.gov/files")
ACS_PAGE = FAA + "/training_testing/testing/acs"
AC_BASE = FAA + "/documentLibrary/media/Advisory_Circular/"

MIN_PDF_BYTES = 10 * 1024
WORKERS = 4
SHRINK_OVER_BYTES = 10 * 1024 * 1024   # PDFs bigger than this are recompressed (0 = never)


# ----------------------------------------------------------------------------
# curl helpers
# ----------------------------------------------------------------------------

def _curl_base() -> List[str]:
    return ["curl", "-sS", "-L", "--http1.1", "--connect-timeout", "30",
            "-A", USER_AGENT, "-e", REFERER, "-H", "Accept: */*"]


_head_cache = {}
_head_lock = threading.Lock()
_head_slots = threading.Semaphore(2)   # the CDN throttles bursts of requests


class Unsure(Exception):
    """The server never gave a definitive answer (throttled / network)."""


def is_pdf_url(url: str) -> bool:
    """True if a HEAD request answers 200 with a PDF content type, False on
    404. Anything else is retried, then raises Unsure instead of guessing
    (a wrong "no" would silently pick an older revision of a document)."""
    with _head_lock:
        if url in _head_cache:
            return _head_cache[url]
    ok, definitive = False, False
    for attempt in range(6):
        code, ctype = "000", ""
        with _head_slots:
            try:
                out = subprocess.run(
                    _curl_base() + ["-I", "-m", "60", "-o", "/dev/null",
                                    "-w", "%{http_code} %{content_type}", url],
                    capture_output=True, text=True, timeout=120).stdout.strip()
                code, _, ctype = out.partition(" ")
            except Exception:
                pass
        ok = code == "200" and "pdf" in ctype.lower()
        if code == "200" or code in ("404", "410"):
            definitive = True      # a 200 that is not a PDF is a "no" too
            break
        time.sleep(3 * (attempt + 1))
    if not definitive:
        raise Unsure("no answer for " + url)
    with _head_lock:
        _head_cache[url] = ok
    return ok


def get_text(url: str) -> str:
    """Page body. Retries when the CDN throttles; raises Unsure if it never
    answers, so a throttled index page is never mistaken for "no links"
    (which would silently fall back to an outdated pinned URL)."""
    for attempt in range(6):
        with _head_slots:
            try:
                r = subprocess.run(
                    _curl_base() + ["-m", "60", "-w", "\n%{http_code}", url],
                    capture_output=True, text=True, timeout=120)
                body, _, code = r.stdout.rpartition("\n")
                if code == "200" and body.strip():
                    return body
                if code in ("404", "410"):
                    return ""
            except Exception:
                pass
        time.sleep(3 * (attempt + 1))
    raise Unsure("no answer for " + url)


def scrape_links(page: str) -> List[str]:
    """Absolute URLs of every link on a page (pdf or not), in page order."""
    html = get_text(page)
    links = []
    for href in re.findall(r'href="([^"#]+)"', html, flags=re.IGNORECASE):
        url = urllib.parse.urljoin(page + "/", href.replace("&amp;", "&"))
        if url not in links:
            links.append(url)
    return links


def scrape_pdf_links(page: str) -> List[str]:
    """Absolute URLs of every .pdf linked from an FAA page."""
    return [u for u in scrape_links(page) if u.lower().split("?")[0].endswith(".pdf")]


def rev_key(url: str):
    """Sort key so that ...-15B.pdf < ...-15C.pdf (natural order, case-insensitive)."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", url)]


def bump_letter(url: str, tries: int = 3) -> str:
    """If url ends in a revision letter (...15B.pdf) and newer letters exist
    (...15C.pdf, ...15D.pdf) return the newest one that exists."""
    m = re.match(r"^(.*[0-9-])([A-Za-z])(\.pdf)$", url)
    if not m:
        return url
    stem, letter, ext = m.groups()
    best, misses = url, 0
    c = letter
    while misses < tries and c.upper() != "Z":
        c = chr(ord(c) + 1)
        cand = stem + c + ext
        if is_pdf_url(cand):
            best, misses = cand, 0
        else:
            misses += 1
    return best


def shrink_pdf(path: str) -> None:
    """Re-render a big PDF with ghostscript's /ebook preset (150 dpi images).
    The FAA full-resolution handbooks are 60-260 MB each; this makes them
    5-10x smaller and still sharp on a phone or tablet. Text, bookmarks and
    links are kept. The original is only replaced if the result is smaller."""
    if not shutil.which("gs"):
        return
    tmp = path + ".shrink"
    try:
        subprocess.run(
            ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite",
             "-dCompatibilityLevel=1.5", "-dPDFSETTINGS=/ebook", "-sOutputFile=" + tmp, path],
            check=True, capture_output=True, text=True, timeout=1800)
        if MIN_PDF_BYTES <= os.path.getsize(tmp) < os.path.getsize(path):
            with open(tmp, "rb") as f:
                if f.read(5) == b"%PDF-":
                    os.replace(tmp, path)
    except Exception:
        pass
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def download_pdf(url: str, dest: str) -> Optional[str]:
    """Download to dest atomically. Returns an error string or None."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    partial = dest + ".partial"
    try:
        os.remove(partial)
    except FileNotFoundError:
        pass
    try:
        subprocess.run(
            _curl_base() + ["-f", "--retry", "6", "--retry-delay", "10",
                            "-m", "1200", "-o", partial, url],
            check=True, capture_output=True, text=True)
        size = os.path.getsize(partial)
        with open(partial, "rb") as f:
            head = f.read(5)
        if head != b"%PDF-" or size < MIN_PDF_BYTES:
            raise ValueError("not a PDF (%d bytes)" % size)
        if SHRINK_OVER_BYTES and size > SHRINK_OVER_BYTES:
            shrink_pdf(partial)
        os.replace(partial, dest)
        return None
    except Exception as e:
        try:
            os.remove(partial)
        except FileNotFoundError:
            pass
        err = getattr(e, "stderr", "") or str(e)
        return err.strip().splitlines()[-1] if err.strip() else repr(e)


# ----------------------------------------------------------------------------
# catalog
# ----------------------------------------------------------------------------

@dataclass
class Item:
    folder: str                                    # zip folder, e.g. "AC"
    resolve: Callable[[], Optional[tuple]]         # -> (url, file name) or None
    label: str                                     # for messages
    required: bool = True
    group: str = ""                                # zip it goes into, e.g. FAA_AC
    # filled in later
    url: Optional[str] = None
    name: Optional[str] = None
    status: str = "pending"                        # ok / cached / failed
    error: str = ""

    @property
    def rel(self) -> str:
        return "%s/%s" % (self.folder, self.name)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def fixed(url: str, name: str) -> Callable[[], Optional[tuple]]:
    return lambda: (url, name) if is_pdf_url(url) else None


def scraped(page: str, pattern: str, fallback: str, name: str) -> Callable[[], Optional[tuple]]:
    """Newest PDF link on page matching regex pattern, else the fallback URL."""
    def resolve():
        matches = [u for u in scrape_pdf_links(page) if re.search(pattern, u, flags=re.IGNORECASE)]
        for url in sorted(matches, key=rev_key, reverse=True):
            if is_pdf_url(url):
                return bump_letter(url), name
        print("  NOTE: %s not found on %s, using pinned %s" % (name, page, fallback), flush=True)
        return (bump_letter(fallback), name) if is_pdf_url(fallback) else None
    return resolve


def latest_handbook(pdf_pattern: str, pages: List[str], fallback: str, name: str,
                    page_pattern: Optional[str] = None) -> Callable[[], Optional[tuple]]:
    """Newest edition of a handbook whose file/page name carries the revision.
    Looks for PDF links on the index pages; with page_pattern it also follows
    links to per-handbook pages (e.g. .../faa-h-8083-28b-aviation-weather-handbook)
    and looks for the PDF there. Falls back to the pinned URL, then tries newer
    revision letters of whatever it found."""
    def resolve():
        found = []
        for page in pages:
            links = scrape_links(page)
            found += [u for u in links if u.lower().endswith(".pdf") and re.search(pdf_pattern, u, re.I)]
            if page_pattern:
                subpages = [u for u in links if re.search(page_pattern, u, re.I) and not u.lower().endswith(".pdf")]
                for sub in sorted(subpages, key=rev_key, reverse=True)[:2]:   # newest edition's page first
                    found += [u for u in scrape_pdf_links(sub) if re.search(pdf_pattern, u, re.I)]
                    if found:
                        break
        for url in sorted(set(found), key=rev_key, reverse=True):
            if is_pdf_url(url):
                return bump_letter(url), name
        print("  NOTE: %s not found on the FAA index page, using pinned %s" % (name, fallback), flush=True)
        return (bump_letter(fallback), name) if is_pdf_url(fallback) else None
    return resolve


# ---- 14 CFR (govinfo) -------------------------------------------------------

CFR14_PARTS = [
    (1, "Definitions_and_Abbreviations"),
    (43, "Maintenance_Preventive_Maintenance_Rebuilding_Alteration"),
    (61, "Certification_Pilots_Flight_and_Ground_Instructors"),
    (67, "Medical_Standards_and_Certification"),
    (68, "Requirements_for_Operating_Certain_Small_Aircraft_BasicMed"),
    (71, "Designation_of_Class_A_B_C_D_E_Airspace"),
    (91, "General_Operating_and_Flight_Rules"),
    (93, "Special_Air_Traffic_Rules"),
    (95, "IFR_Altitudes"),
    (97, "Standard_Instrument_Procedures"),
    (99, "Security_Control_of_Air_Traffic"),
    (103, "Ultralight_Vehicles"),
    (105, "Parachute_Operations"),
    (107, "Small_Unmanned_Aircraft_Systems"),
    (119, "Certification_Air_Carriers_and_Commercial_Operators"),
    (135, "Commuter_and_On_Demand_Operations"),
    (141, "Pilot_Schools"),
    (142, "Training_Centers"),
]


def cfr14_volume(part: int) -> int:
    if part <= 59:
        return 1
    if part <= 109:
        return 2
    return 3   # 110-199


def govinfo_cfr(title: int, vol: int, part: int, name: str) -> Callable[[], Optional[tuple]]:
    """Newest annual CFR edition on govinfo (current year, then older)."""
    def resolve():
        year = datetime.date.today().year
        for y in (year, year - 1, year - 2):
            url = ("https://www.govinfo.gov/content/pkg/CFR-%d-title%d-vol%d/pdf/"
                   "CFR-%d-title%d-vol%d-part%d.pdf" % (y, title, vol, y, title, vol, part))
            if is_pdf_url(url):
                return url, name
        return None
    return resolve


# ---- Advisory circulars -----------------------------------------------------

# (number, preferred revision letter or "", title)
ADVISORY_CIRCULARS = [
    ("61-65", "H", "Certification Pilots and Flight and Ground Instructors"),
    ("61-67", "C", "Stall and Spin Awareness Training"),
    ("61-98", "E", "Currency Requirements and Guidance for the Flight Review"),
    ("60-22", "", "Aeronautical Decision Making"),
    ("60-28", "B", "FAA English Language Standard for an FAA Certificate Issued Under 14 CFR Parts 61, 63, 65"),
    ("00-6", "B", "Aviation Weather"),
    ("00-45", "H", "Aviation Weather Services"),
    ("00-24", "C", "Thunderstorms"),
    ("00-57", "", "Hazardous Mountain Winds"),
    ("00-63", "A", "Use of Flight Deck Displays of Digital Weather and Aeronautical Information"),
    ("00-46", "F", "Aviation Safety Reporting Program"),
    ("90-66", "B", "Non-Towered Airport Flight Operations"),
    ("90-48", "D", "Pilots Role in Collision Avoidance"),
    ("90-100", "A", "US Terminal and En Route Area Navigation Operations"),
    ("90-105", "A", "Approval Guidance for RNP Operations and Barometric VNAV"),
    ("90-107", "", "Guidance for Localizer Performance with Vertical Guidance and LPV"),
    ("90-114", "B", "ADS-B Operations"),
    ("91-92", "", "Pilots Guide to a Preflight Briefing"),
    ("91-74", "B", "Pilot Guide Flight in Icing Conditions"),
    ("91-79", "A", "Mitigating the Risks of a Runway Overrun Upon Landing"),
    ("91-67", "", "Minimum Equipment Requirements for General Aviation Operations"),
    ("91-36", "D", "Visual Flight Rules Flight Near Noise-Sensitive Areas"),
    ("91-57", "C", "Exception for Limited Recreational Operations of Unmanned Aircraft"),
    ("20-138", "E", "Airworthiness Approval of Positioning and Navigation Systems"),
    ("20-165", "B", "Airworthiness Approval of ADS-B Out Systems"),
    ("20-167", "A", "Airworthiness Approval of Enhanced Vision Systems"),
    ("20-27", "G", "Certification and Operation of Amateur-Built Aircraft"),
    ("120-71", "B", "Standard Operating Procedures and Pilot Monitoring Duties"),
    ("120-76", "D", "Authorization for Use of Electronic Flight Bags"),
    ("103-6", "", "Ultralight Vehicle Operations Airports Air Traffic Airspace and Pilot Requirements"),
    ("107-2", "A", "Small Unmanned Aircraft Systems"),
    ("43-9", "C", "Maintenance Records"),
]

LETTERS = [""] + list(string.ascii_uppercase)


def resolve_ac(number: str, pref: str, title: str) -> Callable[[], Optional[tuple]]:
    """Find the newest revision of an AC near the preferred revision letter,
    including a consolidated change (_Chg_N)."""

    def url(letter: str, chg: str = "") -> str:
        return "%sAC_%s%s%s.pdf" % (AC_BASE, number, letter, chg)

    def resolve():
        i = LETTERS.index(pref)
        best = None
        if is_pdf_url(url(LETTERS[i])):
            best = i
        else:
            for d in (1, 2, 3):                    # preferred revision not found, search around
                for j in (i + d, i - d):
                    if 0 <= j < len(LETTERS) and is_pdf_url(url(LETTERS[j])):
                        best = j
                        break
                if best is not None:
                    break
        if best is None:
            return None
        while best + 1 < len(LETTERS):             # newer revisions (tolerate one missing letter)
            if is_pdf_url(url(LETTERS[best + 1])):
                best += 1
            elif best + 2 < len(LETTERS) and is_pdf_url(url(LETTERS[best + 2])):
                best += 2
            else:
                break
        letter = LETTERS[best]
        found, chg_url = url(letter), ""
        for n in (1, 2, 3, 4):                     # consolidated changes
            for fmt in ("_Chg_%d", "_CHG_%d"):
                candidate = url(letter, fmt % n)
                if is_pdf_url(candidate):
                    found, chg_url = candidate, candidate
                    break
            else:
                break
        designation = "AC_%s%s" % (number, letter)
        return found, "%s_%s.pdf" % (designation, slug(title))

    return resolve


# ---- ACS --------------------------------------------------------------------

ACS_FALLBACK = [
    "private_airplane_acs_6", "instrument_rating_airplane_acs_8", "commercial_airplane_acs_7",
    "atp_airplane_acs_11", "cfi_airplane_acs_25", "private_helicopter_acs_15",
    "instrument_helicopter_acs_14", "commercial_helicopter_acs_16", "cfi_helicopter_acs_29",
    "private_pl_acs_13", "instrument_pl_acs_3", "commercial_pl_acs_2", "atp_pl_acs_17",
    "cfi_pl_acs_27", "cfii_pl_acs_28", "uas_acs", "acs_companion_guide_pilots",
    "acs_knowledge_test_table",
]
ACS_SKIP = re.compile(r"briefing|brochure|faq|amt|mechanic|mcn|_SFC_", re.IGNORECASE)


def acs_items() -> List[Item]:
    """Every pilot ACS linked from the FAA ACS page (curated list as fallback)."""
    urls = []
    try:
        page_links = scrape_pdf_links(ACS_PAGE)
    except Unsure:
        sys.exit("The FAA ACS page did not answer (throttled?). Re-run in a few minutes.")
    for url in page_links:
        base = url.rsplit("/", 1)[-1]
        if "/training_testing/testing/acs/" in url and re.search(r"acs", base, re.IGNORECASE) \
                and not ACS_SKIP.search(base):
            urls.append(url)
    if not urls:
        print("  NOTE: could not read the ACS page, using the built-in (possibly outdated) list", flush=True)
        urls = ["%s/%s.pdf" % (ACS_PAGE, n) for n in ACS_FALLBACK]
    items = []
    for url in sorted(set(urls)):
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        items.append(Item("ACS", fixed(url, name), name, required=True))
    return items


# ---- Handbooks --------------------------------------------------------------

def handbook_items() -> List[Item]:
    h = HB_FILES
    spec = [
        # (name, resolver, required)
        ("PHAK_Pilots_Handbook_of_Aeronautical_Knowledge.pdf",
         scraped(HB_PAGE + "/phak", r"faa-h-8083-25[a-z]?\.pdf$",
                 HB_PAGE + "/faa-h-8083-25c.pdf", "PHAK_Pilots_Handbook_of_Aeronautical_Knowledge.pdf"), True),
        ("PHAK_Addendum_MOSAIC.pdf",
         fixed(HB_PAGE + "/PHAK_Addendum_(MOSAIC).pdf", "PHAK_Addendum_MOSAIC.pdf"), False),
        ("Airplane_Flying_Handbook.pdf",
         scraped(HB_PAGE + "/airplane_handbook", r"00_afh_full\.pdf$",
                 h + "/airplane_handbook/00_afh_full.pdf", "Airplane_Flying_Handbook.pdf"), True),
        ("AFH_Addendum_MOSAIC.pdf",
         fixed(HB_PAGE + "/AFH_Addendum_(MOSAIC).pdf", "AFH_Addendum_MOSAIC.pdf"), False),
        ("Instrument_Flying_Handbook.pdf",
         latest_handbook(r"FAA-H-8083-15[A-Z]?\.pdf$", [HB_PAGE], h + "/FAA-H-8083-15B.pdf",
                         "Instrument_Flying_Handbook.pdf"), True),
        ("Instrument_Flying_Handbook_Addendum.pdf",
         fixed(h + "/ifh_addendum_b.pdf", "Instrument_Flying_Handbook_Addendum.pdf"), False),
        ("Instrument_Procedures_Handbook.pdf",
         scraped(HB_PAGE + "/instrument_procedures_handbook", r"FAA-H-8083-16[A-Z]?\.pdf$",
                 h + "/instrument_procedures_handbook/FAA-H-8083-16B.pdf",
                 "Instrument_Procedures_Handbook.pdf"), True),
        ("Risk_Management_Handbook.pdf",
         latest_handbook(r"risk_management_handbook[^/]*\.pdf$", [HB_PAGE],
                         FAA + "/sites/faa.gov/files/2022-06/risk_management_handbook_2A.pdf",
                         "Risk_Management_Handbook.pdf", page_pattern=r"risk-management-handbook"), True),
        ("Aviation_Instructors_Handbook.pdf",
         scraped(HB_PAGE + "/aviation_instructors_handbook", r"aviation_instructors_handbook\.pdf$",
                 h + "/aviation_instructors_handbook/aviation_instructors_handbook.pdf",
                 "Aviation_Instructors_Handbook.pdf"), True),
        ("Aviation_Weather_Handbook.pdf",
         latest_handbook(r"FAA-H-8083-28[A-Z]?\.pdf$", [HB_PAGE],
                         FAA + "/sites/faa.gov/files/FAA-H-8083-28B.pdf",
                         "Aviation_Weather_Handbook.pdf", page_pattern=r"8083-28"), True),
        ("Helicopter_Flying_Handbook.pdf",
         fixed(h + "/helicopter_flying_handbook/helicopter_flying_handbook.pdf",
               "Helicopter_Flying_Handbook.pdf"), False),
        ("Glider_Flying_Handbook.pdf",
         fixed(FAA + "/sites/faa.gov/files/Glider-Flying-Handbook.pdf",
               "Glider_Flying_Handbook.pdf"), False),
        ("Aircraft_Weight_and_Balance_Handbook.pdf",
         fixed(h + "/FAA-H-8083-1.pdf", "Aircraft_Weight_and_Balance_Handbook.pdf"), False),
        ("Weight_and_Balance_Addendum_MOSAIC.pdf",
         fixed(HB_PAGE + "/Weight_Balance_HB_Addendum_(MOSAIC).pdf",
               "Weight_and_Balance_Addendum_MOSAIC.pdf"), False),
        ("Tips_on_Mountain_Flying.pdf",
         fixed(h + "/tips_on_mountain_flying.pdf", "Tips_on_Mountain_Flying.pdf"), False),
        ("Powerlines_and_Thunderstorms.pdf",
         fixed(h + "/powerlines_and_thunderstorms.pdf", "Powerlines_and_Thunderstorms.pdf"), False),
        ("Remote_Pilot_Study_Guide.pdf",
         fixed(h + "/remote_pilot_study_guide.pdf", "Remote_Pilot_Study_Guide.pdf"), False),
    ]
    items = [Item("Handbooks", resolver, name, required=req) for name, resolver, req in spec]

    # Pilot risk management scenarios (several small PDFs)
    try:
        risk_links = scrape_pdf_links(HB_PAGE + "/pilot_risk")
    except Unsure:
        sys.exit("The FAA pilot risk page did not answer (throttled?). Re-run in a few minutes.")
    for url in risk_links:
        if "/pilot_risk/" in url:
            name = "Pilot_Risk_" + urllib.parse.unquote(url.rsplit("/", 1)[-1])
            items.append(Item("Handbooks", fixed(url, name), name, required=False))
    return items


# Handbooks that go into the smaller specialty zip instead of FAA_HANDBOOKS.zip
SPECIALTY_HANDBOOKS = {
    "Helicopter_Flying_Handbook.pdf",
    "Glider_Flying_Handbook.pdf",
    "Aircraft_Weight_and_Balance_Handbook.pdf",
    "Weight_and_Balance_Addendum_MOSAIC.pdf",
    "Remote_Pilot_Study_Guide.pdf",
    "Tips_on_Mountain_Flying.pdf",
    "Powerlines_and_Thunderstorms.pdf",
}


def zip_group(item: "Item") -> str:
    if item.folder == "Handbooks":
        return "FAA_HANDBOOKS_SPECIALTY" if item.label in SPECIALTY_HANDBOOKS else "FAA_HANDBOOKS"
    return "FAA_" + item.folder.upper()


def build_catalog(only: Optional[List[str]]) -> List[Item]:
    def wanted(folder: str) -> bool:
        return not only or folder.lower() in only

    items: List[Item] = []
    if wanted("AIM"):
        items.append(Item("AIM", fixed(AIM_URL, "AIM_Aeronautical_Information_Manual.pdf"),
                          "aim.pdf"))
    if wanted("CFR14"):
        for part, title in CFR14_PARTS:
            name = "14CFR_Part_%03d_%s.pdf" % (part, title)
            items.append(Item("CFR14", govinfo_cfr(14, cfr14_volume(part), part, name),
                              "14 CFR %d" % part))
        items.append(Item("CFR14", govinfo_cfr(49, 7, 830, "NTSB_49CFR_Part_830_Accident_Reporting.pdf"),
                          "49 CFR 830", required=False))
    if wanted("Handbooks"):
        items.extend(handbook_items())
    if wanted("ACS"):
        items.extend(acs_items())
    if wanted("AC"):
        for number, rev, title in ADVISORY_CIRCULARS:
            items.append(Item("AC", resolve_ac(number, rev, title),
                              "AC %s%s" % (number, rev), required=(number == "61-65")))
    for item in items:
        item.group = zip_group(item)
    return items


# ----------------------------------------------------------------------------
# run
# ----------------------------------------------------------------------------

def process(item: Item, work: str, prefix: str, list_only: bool) -> Item:
    try:
        resolved = item.resolve()
    except Unsure as e:
        item.status, item.error = "failed", "server did not answer, re-run later (%s)" % e
        return item
    if not resolved:
        item.status, item.error = "failed", "could not find a PDF"
        return item
    item.url, item.name = resolved
    dest = os.path.join(work, item.folder, item.name)
    if list_only:
        item.status = "ok"
    elif os.path.isfile(dest) and os.path.getsize(dest) >= MIN_PDF_BYTES:
        item.status = "cached"
    else:
        err = download_pdf(item.url, dest)
        item.status, item.error = ("failed", err) if err else ("ok", "")
    return item


def zip_path(prefix: str, rel: str) -> str:
    return "%s/%s" % (prefix.strip("/"), rel) if prefix.strip("/") else rel


def build_zip(items: List[Item], work: str, out: str, prefix: str, cycle_number: str) -> List[str]:
    manifest_name = os.path.basename(out)
    if manifest_name.lower().endswith(".zip"):
        manifest_name = manifest_name[:-4]

    entries = [zip_path(prefix, i.rel) for i in items]
    manifest = [cycle_number] + entries         # line 1 is the cycle, then files
    manifest_text = "\n".join(manifest) + "\n"

    with open(os.path.join(work, manifest_name), "w") as f:
        f.write(manifest_text)

    try:
        os.remove(out)
    except FileNotFoundError:
        pass
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item, arc in zip(items, entries):
            z.write(os.path.join(work, item.folder, item.name), arc)
        z.writestr(manifest_name, manifest_text)     # manifest last
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=".", help="folder for the zips (default: current folder)")
    ap.add_argument("--work", default="faa_docs_cache", help="download cache folder")
    ap.add_argument("--prefix", default="", help="optional folder wrapping everything inside the zip")
    ap.add_argument("--only", default="", help="comma separated folders: AIM,CFR14,Handbooks,ACS,AC")
    ap.add_argument("--list", action="store_true", help="resolve URLs and print the manifest, no download")
    ap.add_argument("--cycle", default="",
                    help="cycle number written to the manifests (default: the cycle data.py uses, "
                         "from cycle.get_cycle())")
    ap.add_argument("--no-shrink", action="store_true",
                    help="keep the original FAA PDFs (about twice the size)")
    args = ap.parse_args()

    global SHRINK_OVER_BYTES
    if args.no_shrink:
        SHRINK_OVER_BYTES = 0
    elif not shutil.which("gs"):
        print("NOTE: ghostscript (gs) not found, big PDFs are not recompressed (install: brew install ghostscript)")

    cycle_number = args.cycle.strip() or cycle.get_cycle()
    only = [s.strip().lower() for s in args.only.split(",") if s.strip()] or None
    items = build_catalog(only)
    print("Resolving and downloading %d documents ..." % len(items), flush=True)

    done: List[Item] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for item in pool.map(lambda i: process(i, args.work, args.prefix, args.list), items):
            done.append(item)
            mark = {"ok": "OK    ", "cached": "CACHED", "failed": "FAILED"}[item.status]
            detail = item.url if item.status != "failed" else item.error
            print("  %s %-10s %s  %s" % (mark, item.folder, item.name or item.label, detail), flush=True)

    good = [i for i in done if i.status != "failed"]
    failed = [i for i in done if i.status == "failed"]
    missing_required = [i for i in failed if i.required]

    groups = {}
    for i in good:
        groups.setdefault(i.group, []).append(i)

    if args.list:
        for group, members in groups.items():
            print("\nManifest %s:" % group)
            print(cycle_number)
            for i in members:
                print(zip_path(args.prefix, i.rel))
    else:
        os.makedirs(args.out_dir, exist_ok=True)
        print()
        for group, members in groups.items():
            out = os.path.join(args.out_dir, group + ".zip")
            manifest = build_zip(members, args.work, out, args.prefix, cycle_number)
            print("Wrote %s: %d files, %.1f MB, cycle %s"
                  % (out, len(members), os.path.getsize(out) / (1024 * 1024), manifest[0]))

    print("%d ok, %d failed (%d required)" % (len(good), len(failed), len(missing_required)))
    for i in failed:
        print("  %s %s/%s: %s" % ("REQUIRED" if i.required else "optional", i.folder, i.label, i.error))
    return 1 if missing_required else 0


if __name__ == "__main__":
    sys.exit(main())
