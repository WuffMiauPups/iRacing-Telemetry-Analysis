"""Fuel-based pit-window estimation.

Pure functions over telemetry values. No iRacing dependency at module
level — easy to unit-test.
"""


def estimate_laps_remaining(fuel_level, fuel_per_lap):
    """How many full laps can we drive with the current fuel.

    Returns None if either input is missing or fuel_per_lap is too small
    to make a meaningful estimate.
    """
    if fuel_level is None or fuel_per_lap is None:
        return None
    if fuel_per_lap <= 0.01:
        return None
    return fuel_level / fuel_per_lap


def fuel_per_lap_from_history(lap_fuel_used):
    """Average fuel/lap from a list of completed-lap fuel-used values.

    Filters out zero/negative entries (refuel laps, in/out laps).
    Returns None if no valid samples.
    """
    valid = [f for f in lap_fuel_used if f is not None and f > 0]
    if not valid:
        return None
    return sum(valid) / len(valid)


def fuel_for_laps(fuel_per_lap, laps, reserve_laps=0.5):
    """Litres needed to finish `laps` more laps with a small reserve."""
    if fuel_per_lap is None or laps is None:
        return None
    return fuel_per_lap * (laps + reserve_laps)


def compute_pit_window(fuel_level, fuel_use_per_hour, last_lap_time,
                       lap_fuel_history=None):
    """Build a pit-window summary dict for the renderer.

    Strategy:
    1. Prefer lap-by-lap history if available (most accurate)
    2. Fall back to FuelUsePerHour * lap_time / 3600 if not enough history
    3. Return all-None if neither source is usable

    Returns a dict with keys:
        fuel_per_lap, laps_remaining, fuel_level, source
    """
    fuel_per_lap = None
    source = None

    if lap_fuel_history:
        fuel_per_lap = fuel_per_lap_from_history(lap_fuel_history)
        if fuel_per_lap is not None:
            source = 'history'

    if fuel_per_lap is None and fuel_use_per_hour is not None and last_lap_time:
        if fuel_use_per_hour > 0 and last_lap_time > 0:
            fuel_per_lap = fuel_use_per_hour * (last_lap_time / 3600.0)
            source = 'rate'

    return {
        'fuel_level': fuel_level,
        'fuel_per_lap': fuel_per_lap,
        'laps_remaining': estimate_laps_remaining(fuel_level, fuel_per_lap),
        'source': source,
    }
