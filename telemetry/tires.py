"""Tyre telemetry helper.

Reads pressure / surface temperatures for the four tyres via the
connection's get_direct() so we always see the latest values.

Returns a dict shaped:
    {
        'lf': {'pressure_kpa': float|None, 'temp_c': float|None},
        'rf': {...},
        'lr': {...},
        'rr': {...},
    }
"""


# iRacing variable names. *Pressure* is in kPa, *Temp* in Celsius.
PRESSURE_VARS = {
    'lf': 'LFcoldPressure',
    'rf': 'RFcoldPressure',
    'lr': 'LRcoldPressure',
    'rr': 'RRcoldPressure',
}

# Surface temperatures: middle of contact patch is the most useful single
# number. iRacing also exposes inner/outer per tyre.
TEMP_VARS = {
    'lf': 'LFtempCM',
    'rf': 'RFtempCM',
    'lr': 'LRtempCM',
    'rr': 'RRtempCM',
}


def read_tires(conn):
    """Read all four tyres. Always returns a dict, missing values are None."""
    out = {}
    for corner in ('lf', 'rf', 'lr', 'rr'):
        pressure = conn.get_direct(PRESSURE_VARS[corner])
        if pressure is None:
            pressure = conn.get(PRESSURE_VARS[corner])
        temp = conn.get_direct(TEMP_VARS[corner])
        if temp is None:
            temp = conn.get(TEMP_VARS[corner])
        out[corner] = {
            'pressure_kpa': pressure,
            'temp_c': temp,
        }
    return out


def has_any_data(tire_data):
    """True if at least one corner has any non-None value."""
    if not tire_data:
        return False
    for corner in tire_data.values():
        if corner.get('pressure_kpa') is not None or corner.get('temp_c') is not None:
            return True
    return False
