import pygeodesy
from pygeodesy.ellipsoidalKarney import LatLon
from magnetic_field_calculator import MagneticFieldCalculator
from tqdm import tqdm

# Create a geo database of declination and geoid height

calculator = MagneticFieldCalculator()
interpolator = pygeodesy.GeoidKarney("./egm84-30.pgm", kind=2)

# open an output file named geo.csv
f = open("geo.csv", "w")

for lat1 in tqdm(range(-90, 91), desc="Latitude"):
    # 1 degree resolution
    for lon1 in range(-180, 181):
        lon = lon1 / 1.0
        lat = lat1 / 1.0

        single_position = LatLon(lat, lon)
        h = interpolator(single_position)
        result = calculator.calculate(latitude=lat, longitude=lon)
        # store CSV
        output = (str(lat1) + "," + str(lon1) + "," + str(round(h)) + "," + str(round(result['field-value']
                                                                                      ['declination']['value'])))
        f.write(output + "\n")

f.close()
