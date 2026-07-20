import common
import cycle

# download
start_date = cycle.get_version_start(cycle.get_cycle_download())  # to download which cycle
all_charts = [
    "https://nfdc.faa.gov/webContent/28DaySub/28DaySubscription_Effective_" + start_date + ".zip",
    "https://nfdc.faa.gov/webContent/28DaySub/" + start_date + "/aixm5.0.zip",
    "https://aeronav.faa.gov/Obst_Data/DAILY_DOF_DAT.ZIP",
    "https://aeronav.faa.gov/Upload_313-d/cifp/CIFP_" + start_date[2:].replace("-", "") + ".zip"
]

common.download_list(all_charts)

# d-TPP metafile provides readable names for the CIFP procedures table. It is
# best-effort: if it is not yet published for this cycle, procedures.py falls
# back to names synthesized from the CIFP identifiers.
try:
    common.download(
        "https://aeronav.faa.gov/d-tpp/" + cycle.get_cycle_download()
        + "/xml_data/d-TPP_Metafile.xml"
    )
except Exception as e:
    print("d-TPP metafile download failed (procedure names will be synthesized): " + str(e))

# copy all files from legacy folder
common.call_script("cp legacy/* .")

common.make_data()
common.make_db()

# copy all files from x folder
common.call_script("cp x/* .")

common.make_data()

# Convert shapefile to GeoJSON and generate airspace tiles before creating databasesx.zip
common.call_script("ogr2ogr -f GeoJSON Additional_Data/Shape_Files/Class_Airspace.geojson Additional_Data/Shape_Files/Class_Airspace.shp")
common.call_script("chmod +x generate_airspace_tiles.sh && ./generate_airspace_tiles.sh")

common.make_db("x")
