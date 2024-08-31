import glob
import os
import urllib.request
import re
from bs4 import BeautifulSoup
from subprocess import check_call, check_output
import zipfile
from tqdm import tqdm
import cycle
import cifp

states_in_regions = {
    "AK":  ["AK",],
    "PAC": ["HI", "XX"],
    "NW":  ["WA", "MT", "WY", "ID", "OR"],
    "SW":  ["CA", "NV", "UT", "CO", "NM", "AZ"],
    "NC":  ["ND", "MN", "IA", "MO", "KS", "NE", "SD"],
    "EC":  ["WI", "MI", "OH", "IN", "IL"],
    "SC":  ["OK", "AR", "MS", "LA", "TX"],
    "NE":  ["NY", "ME", "VT", "NH", "MA", "RI", "CT", "NJ", "DE", "MD", "DC", "VA", "WV", "PA"],
    "SE":  ["KY", "NC", "SC", "GA", "FL", "AL", "TN", "PR", "VI"]
}


def list_crawl(url, match):
    charts = []
    html_page = urllib.request.urlopen(url)
    soup = BeautifulSoup(html_page, "html.parser")
    for link in tqdm(soup.findAll('a'), desc="Scanning website links"):
        link_x = link.get('href')
        if link_x is None:
            continue
        if re.search(match, link_x):
            charts.append(link_x)
    list_set = set(charts)  # unique
    return list(list_set)


def download(url):
    name = url.split("/")[-1]
    # check if exists
    if not os.path.isfile(name):
        urllib.request.urlretrieve(url, name)
    if name.endswith(".zip") or name.endswith(".ZIP"):  # if a zipfile, unzip first
        with zipfile.ZipFile(name, 'r') as zip_ref:
            zip_ref.extractall(".")


def download_list(charts):
    for cc in tqdm(range(len(charts)), desc="Downloading/unzipping"):
        download(charts[cc])


def call_script(script):
    check_call([script], shell=True)


def call_script_return(script):
    return check_output([script], shell=True, encoding='utf8').strip()


def call_perl_script(script):
    check_call(["perl" + " " + script + ".pl > " + script + ".csv"], shell=True)


def make_data():
    with zipfile.ZipFile("SAA-AIXM_5_Schema/SaaSubscriberFile.zip", 'r') as zip_ref:
        zip_ref.extractall(".")
    with zipfile.ZipFile("Saa_Sub_File.zip", 'r') as zip_ref:
        zip_ref.extractall(".")

    # parse all FAA data
    for script in tqdm(["saa", "airport", "runway", "freq", "fix", "nav", "dof", "awos", "aw"],
                       desc="Running PERL database files"):
        call_perl_script(script)
    # CIFP too
    cifp.parse_cifp()


def make_db(extra=""):
    try:
        os.unlink("main.db")
    except FileNotFoundError as e:
        pass
    call_script("sqlite3 main.db < importother.sql")

    try:
        os.remove("databases" + extra + ".zip")
        os.remove("databases" + extra)
    except FileNotFoundError as e:
        pass

    zip_file = zipfile.ZipFile("databases" + extra + ".zip", "w")
    manifest_file = open("databases" + extra, "w+")
    manifest_file.write(cycle.get_cycle() + "\n")
    manifest_file.write("main.db\n")
    manifest_file.close()
    zip_file.write("databases" + extra)
    zip_file.write("main.db")
    zip_file.close()
