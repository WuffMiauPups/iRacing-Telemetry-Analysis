import os
from datetime import datetime

import matplotlib
matplotlib.use('Agg')  # Non-interactive backend (no GUI needed)
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


def _safe_int(val, default=None):
    """Parse a value to int, returning default if invalid."""
    if val is None or val == '' or val == 'None':
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val, default=0.0):
    """Parse a value to float, returning default if invalid."""
    if val is None or val == '' or val == 'None':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def generate_session_summary(session_dir, lap_data, track_name='', session_type=''):
    """Generate a session summary .txt and position graph .png."""
    if not lap_data:
        return

    # Parse lap data
    laps = []
    for row in lap_data:
        lap_num = _safe_int(row.get('Lap'), 0)
        lap_time = _safe_float(row.get('LapTime'), None)
        if lap_time is not None and lap_time <= 0:
            lap_time = None
        position = _safe_int(row.get('Position'))
        # Filter out Position 0 (not classified / in pit)
        if position is not None and position <= 0:
            position = None
        pos_change = _safe_int(row.get('PositionChange'), 0)
        incidents = _safe_int(row.get('Incidents'), 0)
        # Clamp negative incidents to 0 (counter reset)
        if incidents < 0:
            incidents = 0
        fuel = _safe_float(row.get('FuelUsed'))
        # Clamp negative fuel to 0 (refueled)
        if fuel < 0:
            fuel = 0.0

        laps.append({
            'lap': lap_num,
            'time': lap_time,
            'position': position,
            'pos_change': pos_change,
            'incidents': incidents,
            'fuel_used': fuel,
            'avg_speed': _safe_float(row.get('AvgSpeed_kmh')),
            'max_speed': _safe_float(row.get('MaxSpeed_kmh')),
            'avg_throttle': _safe_float(row.get('AvgThrottle')),
            'avg_brake': _safe_float(row.get('AvgBrake')),
            'gear_shifts': _safe_int(row.get('GearShifts'), 0),
        })

    if not laps:
        return

    # --- Only count valid laps (have a position and are on track) ---
    valid_laps = [l for l in laps if l['position'] is not None and l['position'] > 0]

    # Exclude lap 1 for best/worst/avg (standing start / out-lap)
    timed_laps = [l for l in valid_laps if l['time'] is not None and l['time'] > 0 and l['lap'] > 1]

    # Starting/finishing position — use first/last valid position
    starting_pos = valid_laps[0]['position'] if valid_laps else None
    finishing_pos = valid_laps[-1]['position'] if valid_laps else None

    best_lap = min(timed_laps, key=lambda l: l['time']) if timed_laps else None
    worst_lap = max(timed_laps, key=lambda l: l['time']) if timed_laps else None
    avg_time = sum(l['time'] for l in timed_laps) / len(timed_laps) if timed_laps else 0

    total_overtakes_gained = sum(l['pos_change'] for l in valid_laps if l['pos_change'] > 0)
    total_positions_lost = sum(abs(l['pos_change']) for l in valid_laps if l['pos_change'] < 0)
    total_incidents = sum(l['incidents'] for l in laps)

    # Fuel — only from valid laps (not reset laps)
    fuel_laps = [l for l in valid_laps if l['fuel_used'] > 0]
    total_fuel = sum(l['fuel_used'] for l in fuel_laps)
    avg_fuel_per_lap = total_fuel / len(fuel_laps) if fuel_laps else 0

    # --- Write summary text ---
    summary_path = os.path.join(session_dir, 'session_summary.txt')
    now = datetime.now()

    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write('=' * 60 + '\n')
        f.write('  iRacing Session Summary\n')
        f.write('=' * 60 + '\n\n')

        f.write(f'  Datum:    {now.strftime("%d.%m.%Y %H:%M")}\n')
        f.write(f'  Strecke:  {track_name}\n')
        f.write(f'  Session:  {session_type}\n')
        f.write(f'  Runden:   {len(valid_laps)} (von {len(laps)} gesamt)\n\n')

        f.write('-' * 60 + '\n')
        f.write('  ERGEBNIS\n')
        f.write('-' * 60 + '\n')
        f.write(f'  Startposition:  P{starting_pos}\n' if starting_pos else '  Startposition:  --\n')
        f.write(f'  Endposition:    P{finishing_pos}\n' if finishing_pos else '  Endposition:    --\n')
        pos_diff = 0
        if starting_pos and finishing_pos:
            pos_diff = starting_pos - finishing_pos
        if pos_diff > 0:
            f.write(f'  Veraenderung:   +{pos_diff} Plaetze gewonnen\n')
        elif pos_diff < 0:
            f.write(f'  Veraenderung:   {abs(pos_diff)} Plaetze verloren\n')
        else:
            f.write(f'  Veraenderung:   Keine\n')
        f.write('\n')

        f.write('-' * 60 + '\n')
        f.write('  RUNDENZEITEN (ohne Runde 1 / Out-Lap)\n')
        f.write('-' * 60 + '\n')
        if best_lap:
            f.write(f'  Beste Runde:    {_fmt_time(best_lap["time"])} (Runde {best_lap["lap"]})\n')
        if worst_lap:
            f.write(f'  Schlechteste:   {_fmt_time(worst_lap["time"])} (Runde {worst_lap["lap"]})\n')
        if avg_time > 0:
            f.write(f'  Durchschnitt:   {_fmt_time(avg_time)}\n')
        if best_lap and worst_lap:
            spread = worst_lap['time'] - best_lap['time']
            f.write(f'  Streuung:       {spread:.3f}s (Worst - Best)\n')
        f.write('\n')

        f.write('-' * 60 + '\n')
        f.write('  UEBERHOLMANOEVER\n')
        f.write('-' * 60 + '\n')
        f.write(f'  Plaetze gewonnen:   {total_overtakes_gained}\n')
        f.write(f'  Plaetze verloren:   {total_positions_lost}\n')
        net = total_overtakes_gained - total_positions_lost
        f.write(f'  Netto:              {net:+d}\n\n')

        f.write('  Runde  Pos  Veraenderung\n')
        f.write('  ' + '-' * 30 + '\n')
        for l in valid_laps:
            pos_str = f'P{l["position"]}'
            change_str = ''
            if l['pos_change'] > 0:
                change_str = f'+{l["pos_change"]}'
            elif l['pos_change'] < 0:
                change_str = f'{l["pos_change"]}'
            f.write(f'  {l["lap"]:>4}   {pos_str:<4} {change_str}\n')
        f.write('\n')

        f.write('-' * 60 + '\n')
        f.write('  INCIDENTS\n')
        f.write('-' * 60 + '\n')
        f.write(f'  Gesamt:  {total_incidents}x\n')
        for l in laps:
            if l['incidents'] > 0:
                f.write(f'  Runde {l["lap"]}: +{l["incidents"]}x\n')
        if total_incidents == 0:
            f.write('  Saubere Session!\n')
        f.write('\n')

        f.write('-' * 60 + '\n')
        f.write('  KRAFTSTOFF\n')
        f.write('-' * 60 + '\n')
        f.write(f'  Verbrauch gesamt:    {total_fuel:.2f} L\n')
        f.write(f'  Verbrauch pro Runde: {avg_fuel_per_lap:.2f} L\n\n')

        f.write('-' * 60 + '\n')
        f.write('  GESCHWINDIGKEIT\n')
        f.write('-' * 60 + '\n')
        if timed_laps:
            avg_speed = sum(l['avg_speed'] for l in timed_laps) / len(timed_laps)
            max_speed = max(l['max_speed'] for l in valid_laps) if valid_laps else 0
            f.write(f'  Durchschnitt:  {avg_speed:.1f} km/h\n')
            f.write(f'  Hoechste:      {max_speed:.1f} km/h\n')
        f.write('\n')

        f.write('-' * 60 + '\n')
        f.write('  RUNDENDETAILS\n')
        f.write('-' * 60 + '\n')
        f.write(f'  {"Runde":>5}  {"Zeit":>10}  {"Pos":>4}  {"Avg km/h":>8}  '
                f'{"Max km/h":>8}  {"Gas%":>5}  {"Bremse%":>7}  {"Schaltv.":>8}\n')
        f.write('  ' + '-' * 65 + '\n')
        for l in valid_laps:
            time_str = _fmt_time(l['time']) if l['time'] else '---'
            pos_str = f'P{l["position"]}'
            f.write(f'  {l["lap"]:>5}  {time_str:>10}  {pos_str:>4}  '
                    f'{l["avg_speed"]:>8.1f}  {l["max_speed"]:>8.1f}  '
                    f'{l["avg_throttle"]:>5.1f}  {l["avg_brake"]:>7.1f}  '
                    f'{l["gear_shifts"]:>8}\n')
        f.write('\n')

        f.write('=' * 60 + '\n')
        f.write('  Generated by iRacing Telemetry Tool\n')
        f.write('=' * 60 + '\n')

    # --- Generate position graph PNG ---
    _generate_position_graph(session_dir, valid_laps, track_name, now)

    return summary_path


def _generate_position_graph(session_dir, laps, track_name, timestamp):
    """Create a lap-by-lap position chart as PNG.

    Only uses laps with valid positions (> 0).
    """
    graph_path = os.path.join(session_dir, 'position_graph.png')

    # Only plot laps with valid position
    lap_nums = [l['lap'] for l in laps if l['position'] is not None and l['position'] > 0]
    positions = [l['position'] for l in laps if l['position'] is not None and l['position'] > 0]

    if len(lap_nums) < 2:
        return

    fig, ax = plt.subplots(figsize=(12, 5))

    # Dark theme
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')

    # Plot position line
    ax.plot(lap_nums, positions, color='#00ff88', linewidth=2.5, marker='o',
            markersize=6, markerfacecolor='#00ff88', markeredgecolor='white',
            markeredgewidth=1, zorder=5)

    # Fill area under the line
    max_pos = max(positions)
    ax.fill_between(lap_nums, positions, max_pos + 1,
                     color='#00ff88', alpha=0.1)

    # Highlight best position
    best_pos = min(positions)
    best_lap_idx = positions.index(best_pos)
    ax.plot(lap_nums[best_lap_idx], best_pos, marker='*', markersize=15,
            color='#FFD700', zorder=10)
    ax.annotate(f'P{best_pos}', (lap_nums[best_lap_idx], best_pos),
                textcoords="offset points", xytext=(10, -15),
                color='#FFD700', fontsize=10, fontweight='bold')

    # Invert Y axis (P1 at top)
    ax.invert_yaxis()

    # Y limits: ensure P1 is always visible, add padding
    ax.set_ylim(max_pos + 0.5, 0.5)

    # Labels
    ax.set_xlabel('Runde', color='white', fontsize=12)
    ax.set_ylabel('Position', color='white', fontsize=12)
    ax.set_title(f'Positionsverlauf - {track_name}\n{timestamp.strftime("%d.%m.%Y")}',
                 color='white', fontsize=14, fontweight='bold', pad=15)

    # Grid
    ax.grid(True, alpha=0.2, color='white')
    ax.tick_params(colors='white')

    # Y-axis: only integer positions
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))

    # Spine colors
    for spine in ax.spines.values():
        spine.set_color('#333333')

    # Start/finish annotations
    if len(positions) >= 2:
        ax.annotate(f'Start P{positions[0]}', (lap_nums[0], positions[0]),
                    textcoords="offset points", xytext=(15, 5),
                    color='#aaaaaa', fontsize=9)
        ax.annotate(f'Finish P{positions[-1]}', (lap_nums[-1], positions[-1]),
                    textcoords="offset points", xytext=(-60, 5),
                    color='#aaaaaa', fontsize=9)

    plt.tight_layout()
    plt.savefig(graph_path, dpi=150, facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)


def _fmt_time(seconds):
    """Format seconds to M:SS.mmm"""
    if seconds is None or seconds <= 0:
        return '--:--.---'
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f'{minutes}:{secs:06.3f}'
