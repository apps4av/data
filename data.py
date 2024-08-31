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

common.make_data()
common.make_db()

# copy all files from x folder
common.call_script("cp x/* .")

common.make_data()
common.make_db("x")
