import math


# Sky condition labels
SKIES_MAP = {
    0: 'Klar',
    1: 'Leicht bewölkt',
    2: 'Bewölkt',
    3: 'Bedeckt',
}

WEATHER_TYPE_MAP = {
    0: 'Konstant',
    1: 'Dynamisch',
}


class SessionMonitor:
    """Reads weather and session info from iRacing."""

    def __init__(self, connection):
        self.conn = connection

    def get_weather(self):
        """Get current weather data."""
        wind_vel = self.conn.get('WindVel')
        wind_dir_rad = self.conn.get('WindDir')

        # Convert wind direction from radians to compass direction
        wind_dir_str = None
        if wind_dir_rad is not None:
            wind_dir_deg = math.degrees(wind_dir_rad) % 360
            wind_dir_str = self._deg_to_compass(wind_dir_deg)

        # Convert wind speed from m/s to km/h
        wind_kmh = round(wind_vel * 3.6, 1) if wind_vel is not None else None

        skies_val = self.conn.get('Skies')
        weather_type_val = self.conn.get('WeatherType')
        humidity = self.conn.get('RelativeHumidity')

        return {
            'air_temp': self.conn.get('AirTemp'),
            'track_temp': self.conn.get('TrackTemp'),
            'wind_speed_ms': wind_vel,
            'wind_speed_kmh': wind_kmh,
            'wind_direction': wind_dir_str,
            'weather_type': WEATHER_TYPE_MAP.get(weather_type_val, '?'),
            'skies': SKIES_MAP.get(skies_val, '?'),
            'humidity': round(humidity * 100, 1) if humidity is not None else None,
            'fog': self.conn.get('FogLevel'),
        }

    def get_session_info(self):
        """Get basic session info from cached YAML."""
        weekend = self.conn.weekend_info
        if weekend is None:
            return None
        return {
            'track_name': weekend.get('TrackDisplayName', '?'),
            'track_length': weekend.get('TrackLength', '?'),
            'series': weekend.get('SeriesShortName', '?'),
        }

    @staticmethod
    def _deg_to_compass(deg):
        """Convert degrees to 8-point compass direction."""
        directions = ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW']
        idx = round(deg / 45) % 8
        return directions[idx]
