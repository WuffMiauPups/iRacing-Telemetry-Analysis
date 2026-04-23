from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text


def _format_kpa(p):
    if p is None:
        return '--'
    return f'{p:.0f} kPa'


def _format_temp(t):
    if t is None:
        return '--'
    return f'{t:.0f}C'


def _format_laptime(seconds):
    """Format lap time from seconds to M:SS.mmm"""
    if seconds is None:
        return '--:--.---'
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f'{minutes}:{secs:06.3f}'


def _format_sector(seconds):
    """Format sector time."""
    if seconds is None:
        return '--.-'
    return f'{seconds:.1f}s'


def _format_delta(delta):
    """Format a per-lap delta with +/- sign and color."""
    if delta is None or delta == 0:
        return Text('--', style='dim')
    if delta > 0:
        return Text(f'+{delta:.3f}s', style='green')  # I'm faster
    else:
        return Text(f'{delta:.3f}s', style='red')  # I'm slower


def _render_catch_block(text, catch_data, is_ahead):
    """Render a catch/caught info block into a Text object.

    is_ahead=True: we are chasing this car
    is_ahead=False: this car is chasing us
    """
    gap = catch_data.get('gap')
    delta = catch_data.get('per_lap_delta', 0)
    gaining = catch_data.get('gaining', False)
    laps = catch_data.get('laps_to_catch')
    live_dps = catch_data.get('live_delta_per_sec')

    if gap:
        text.append(f'  Gap: {gap:.2f}s', style='bold')

    if is_ahead:
        # We are trying to catch the car ahead
        if delta != 0:
            if gaining:
                text.append(f'   +{abs(delta):.3f}s/Runde schneller', style='green bold')
            else:
                text.append(f'   -{abs(delta):.3f}s/Runde langsamer', style='red')

        if laps is not None and laps > 0:
            text.append(f'   Eingeholt in ~{laps:.0f} Runden', style='bright_green bold')
        elif gap and delta <= 0:
            text.append('   Wird nicht eingeholt', style='dim')
    else:
        # Car behind is trying to catch us
        if delta != 0:
            if gaining:
                text.append(f'   Kommt {abs(delta):.3f}s/Runde naeher', style='red bold')
            else:
                text.append(f'   Verliert {abs(delta):.3f}s/Runde', style='green')

        if laps is not None and laps > 0:
            text.append(f'   Holt mich ein in ~{laps:.0f} Runden', style='bright_red bold')
        elif gap and delta <= 0:
            text.append('   Holt mich nicht ein', style='dim')

    # Show live trend indicator
    if live_dps is not None and abs(live_dps) > 0.001:
        trend_dir = 'naeher' if (is_ahead and live_dps > 0) or (not is_ahead and live_dps > 0) else 'weiter'
        trend_style = 'green' if (is_ahead and live_dps > 0) or (not is_ahead and live_dps < 0) else 'red'
        text.append(f'\n  Live: {abs(live_dps):.3f}s/sek {trend_dir}', style=trend_style)

    text.append('\n')


class Renderer:
    """Terminal renderer using rich for colored output."""

    def __init__(self):
        self.console = Console()

    def build_timing_panel(self, timing_data):
        """Build the timing display panel with catch calculator."""
        if timing_data is None:
            return Panel('Keine Timing-Daten verfuegbar', title='RUNDENZEITEN')

        grid = Table.grid(expand=True)
        catch_ahead = timing_data.get('catch_ahead')
        catch_behind = timing_data.get('catch_behind')

        for key in ['ahead', 'player', 'behind']:
            entry = timing_data[key]
            if entry is None:
                continue

            text = Text()
            label = entry['label']
            car_num = entry['car_number']
            name = entry['driver_name']
            text.append(f'  {label} (#{car_num} {name})\n', style='bold')
            text.append(f'  Letzte Runde: {_format_laptime(entry["last_lap"])}')
            text.append(f'   Beste: {_format_laptime(entry["best_lap"])}\n')

            sectors = entry.get('sectors', [])
            if sectors:
                sector_str = '  '.join(f'S{i+1} {_format_sector(s)}' for i, s in enumerate(sectors))
                text.append(f'  Sektoren: {sector_str}\n')
            else:
                text.append('  Sektoren: ---\n', style='dim')

            # Catch info for car ahead
            if key == 'ahead' and catch_ahead is not None:
                text.append('\n')
                _render_catch_block(text, catch_ahead, is_ahead=True)

            # Catch info for car behind
            if key == 'behind' and catch_behind is not None:
                text.append('\n')
                _render_catch_block(text, catch_behind, is_ahead=False)

            style = 'bright_yellow' if key == 'player' else 'white'
            grid.add_row(Panel(text, border_style=style))

        return Panel(grid, title='RUNDENZEITEN & GAPS', border_style='bright_blue')

    def build_weather_panel(self, weather_data, session_info):
        """Build the weather/session display panel."""
        if weather_data is None:
            return Panel('Keine Wetterdaten verfuegbar', title='WETTER & SESSION')

        text = Text()

        if session_info:
            text.append(f'  Strecke: {session_info["track_name"]}', style='bold')
            text.append(f'  ({session_info["track_length"]})\n')

        w = weather_data
        text.append(f'  Luft: {w["air_temp"]:.1f}C' if w['air_temp'] is not None else '  Luft: --')
        text.append(f'   Strecke: {w["track_temp"]:.1f}C\n' if w['track_temp'] is not None else '   Strecke: --\n')
        text.append(f'  Himmel: {w["skies"]}')
        text.append(f'   Wetter: {w["weather_type"]}\n')

        if w['wind_speed_kmh'] is not None:
            text.append(f'  Wind: {w["wind_speed_kmh"]} km/h aus {w["wind_direction"]}\n')

        if w['humidity'] is not None:
            text.append(f'  Luftfeuchtigkeit: {w["humidity"]}%\n')

        return Panel(text, title='WETTER & SESSION', border_style='bright_blue')

    def build_pit_panel(self, pit_data):
        """Build the pit/fuel panel. Returns None if data unusable."""
        if pit_data is None:
            return None
        fuel = pit_data.get('fuel_level')
        fpl = pit_data.get('fuel_per_lap')
        laps_left = pit_data.get('laps_remaining')
        source = pit_data.get('source')
        if fuel is None and fpl is None:
            return None

        text = Text()
        text.append(f'  Tank: {fuel:.2f} L\n' if fuel is not None else '  Tank: --\n')
        text.append(f'  Verbrauch: {fpl:.2f} L/Runde\n' if fpl is not None else '  Verbrauch: --\n')
        if laps_left is not None:
            style = 'red bold' if laps_left < 3 else ('yellow' if laps_left < 6 else 'green')
            text.append(f'  Reichweite: ~{laps_left:.1f} Runden\n', style=style)
        else:
            text.append('  Reichweite: --\n', style='dim')
        if source:
            text.append(f'  Quelle: {source}\n', style='dim')
        return Panel(text, title='KRAFTSTOFF / PIT WINDOW', border_style='bright_blue')

    def build_tire_panel(self, tire_data):
        """Build the tyre panel as a 2x2 grid. Returns None if no data."""
        if not tire_data:
            return None
        # Compact 2x2 layout matching real tyre positions (LF RF / LR RR)
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(justify='center')
        grid.add_column(justify='center')

        def cell(label, corner):
            t = Text()
            t.append(f'{label}\n', style='bold')
            t.append(f'{_format_kpa(corner.get("pressure_kpa"))}\n')
            t.append(_format_temp(corner.get('temp_c')))
            return t

        grid.add_row(cell('LF', tire_data['lf']), cell('RF', tire_data['rf']))
        grid.add_row(cell('LR', tire_data['lr']), cell('RR', tire_data['rr']))
        return Panel(grid, title='REIFEN', border_style='bright_blue')

    def render(self, timing_data=None, weather_data=None, session_info=None,
               map_status=None, pit_data=None, tire_data=None, **kwargs):
        """Build complete display output."""
        panels = []

        if timing_data is not None:
            panels.append(self.build_timing_panel(timing_data))

        if weather_data is not None:
            panels.append(self.build_weather_panel(weather_data, session_info))

        if pit_data is not None:
            p = self.build_pit_panel(pit_data)
            if p is not None:
                panels.append(p)

        if tire_data is not None:
            p = self.build_tire_panel(tire_data)
            if p is not None:
                panels.append(p)

        if not panels:
            panels.append(Panel('Warte auf Daten...', border_style='dim'))

        grid = Table.grid()
        for p in panels:
            grid.add_row(p)

        footer = '  Update: alle 1.0s   [STRG+C] Beenden'
        if map_status:
            footer += f'   | Track Map: {map_status}'
        grid.add_row(Text(footer, style='dim'))

        return grid
