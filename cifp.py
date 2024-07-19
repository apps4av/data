from collections import OrderedDict


def parse_sid_star_app(line):
    parsed = OrderedDict( {
        'record_type': line[0:1],
        'customer_area_code': line[1:4],
        'section_code': line[4:5],
        'airport_identifier': line[6:10],
        'icao_code_1': line[10:12],
        'subsection_code': line[12:13],
        'sid_star_approach_identifier': line[13:19],
        'route_type': line[19:20],
        'transition_identifier': line[20:25],
        'sequence_number': line[26:29],
        'fix_identifier': line[29:34],
        'icao_code_2': line[34:36],
        'section_code_2': line[36:37],
        'subsection_code_2': line[37:38],
        'continuation_record_number': line[38:39],
        'waypoint_description_code': line[39:43],
        'turn_direction': line[43:44],
        'rnp': line[44:47],
        'path_and_termination': line[47:49],
        'turn_direction_valid': line[49:50],
        'recommended_navaid': line[50:54],
        'icao_code_3': line[54:56],
        'arc_radius': line[56:62],
        'theta': line[62:66],
        'rho': line[66:70],
        'magnetic_course': line[70:74],
        'route_distance_holding_distance_or_time': line[74:78],
        'recd_nav_section': line[78:79],
        'recd_nav_subsection': line[79:80],
        'reserved': line[80:82],
        'altitude_description': line[82:83],
        'atc_indicator': line[83:84],
        'altitude_1': line[84:89],
        'altitude_2': line[89:94],
        'transition_altitude': line[94:99],
        'speed_limit': line[99:102],
        'vertical_angle': line[102:106],
        'center_fix_or_taa_procedure_turn_indicator': line[106:111],
        'multiple_code_or_taa_sector_identifier': line[111:112],
        'icao_code_4': line[112:114],
        'section_code_3': line[114:115],
        'subsection_code_3': line[115:116],
        'gps_fms_indication': line[116:117],
        'speed_limit_description': line[117:118],
        'apch_route_qualifier_1': line[118:119],
        'apch_route_qualifier_2': line[119:120],
        'file_record_number': line[123:128],
        'cycle_date': line[128:132],
    })
    return parsed


def parse_cifp():
    with open ('FAACIFP18', 'r') as f:
        # read all lines in the file
        lines = f.readlines()

    with open('cifp_sid_star_app.csv', 'w+') as f:
        for line in lines:
            # sid / star
            if line[4] == 'P' and (line[12] == 'D' or line[12] == 'E' or line[12] == 'F'):
                o_dict = parse_sid_star_app(line)
                # Convert the values of the OrderedDict to a list of strings
                values_as_str = [str(value) for value in o_dict.values()]
                # Join the list into a comma-separated string
                comma_separated_values = ','.join(values_as_str)
                f.write(comma_separated_values + '\n')



