CREATE TABLE airports(LocationID Text,ARPLatitude float,ARPLongitude float,Type Text,FacilityName Text,Use Text,FSSPhone Text,Manager Text,ManagerPhone Text,ARPElevation Text,MagneticVariation Text,TrafficPatternAltitude Text,FuelTypes Text,Customs Text,Beacon Text,LightSchedule Text,SegCircle Text,ATCT Text,UNICOMFrequencies Text,CTAFFrequency Text,NonCommercialLandingFee Text,State Text, City Text,
UNIQUE(LocationID));
.separator ","
.import airport.csv airports 

CREATE TABLE airportfreq(LocationID Text,Type Text, Freq Text);
.import freq.csv airportfreq 

CREATE TABLE airportrunways(LocationID Text,Length Text,Width Text,Surface Text,LEIdent Text,HEIdent Text,LELatitude Text,HELatitude Text,LELongitude Text,HELongitude Text,LEElevation Text,HEElevation Text,LEHeadingT Text,HEHeading Text,LEDT Text,HEDT Text,LELights Text,HELights Text,LEILS Text,HEILS Text,LEVGSI Text,HEVGSI Text,LEPattern Text, HEPattern Text);

.import runway.csv airportrunways 

CREATE TABLE nav(LocationID Text,ARPLatitude float,ARPLongitude float,Type Text,FacilityName Text,Variation TinyInt,Class Text,Hiwas Text,Elevation Text);

.import nav.csv nav

CREATE TABLE fix(LocationID Text,ARPLatitude float,ARPLongitude float,Type Text,FacilityName Text);

.import fix.csv fix

CREATE TABLE obs(ARPLatitude float,ARPLongitude float,Height float);
.import dof.csv obs 

CREATE TABLE awos(LocationID Text, Type Text, Status Text, Latitude float,Longitude float, Elevation Text, Frequency1 Text, Frequency2 Text, Telephone1 Text, Telephone2 Text, Remark Text);

.import awos.csv awos

CREATE TABLE saa(designator TEXT,name TEXT,upperlimit TEXT,lowerlimit TEXT,begintime TEXT,endtime TEXT,timeref TEXT,beginday TEXT,endday TEXT,day TEXT,FreqTx TEXT,FreqRx TEXT,lat FLOAT,lon FLOAT);
.import saa.csv saa

CREATE TABLE airways(name Text, sequence Text, Latitude float, Longitude float);
.import aw.csv airways

CREATE TABLE cifp_sid_star_app(record_type Text,customer_area_code Text,section_code Text,airport_identifier Text,icao_code_1 Text,subsection_code Text,sid_star_approach_identifier Text,route_type Text,transition_identifier Text,sequence_number Text,fix_identifier Text,icao_code_2 Text,section_code_2 Text,subsection_code_2 Text,continuation_record_number Text,waypoint_description_code Text,turn_direction Text,rnp Text,path_and_termination Text,turn_direction_valid Text,recommended_navaid Text,icao_code_3 Text,arc_radius Text,theta Text,rho Text,magnetic_course Text,route_distance_holding_distance_or_time Text,recd_nav_section Text,recd_nav_subsection Text,reserved Text,altitude_description Text,atc_indicator Text,altitude_1 Text,altitude_2 Text,transition_altitude Text,speed_limit Text,vertical_angle Text,center_fix_or_taa_procedure_turn_indicator Text,multiple_code_or_taa_sector_identifier Text,icao_code_4 Text,section_code_3 Text,subsection_code_3 Text,gps_fms_indication Text,speed_limit_description Text,apch_route_qualifier_1 Text,apch_route_qualifier_2 Text,file_record_number Text,cycle_date Text);
.import cifp_sid_star_app.csv cifp_sid_star_app
