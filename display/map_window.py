import tkinter as tk
import threading
import queue
import os


# Car colors by position bracket
CAR_COLORS = [
    '#FFD700',  # 1st - gold
    '#C0C0C0',  # 2nd - silver
    '#CD7F32',  # 3rd - bronze
    '#00FF00',  # 4-10 - green
    '#00CCFF',  # 11-20 - cyan
    '#FFFFFF',  # 21+ - white
]

PLAYER_COLOR = '#FF00FF'  # Magenta for player
TRACK_COLOR = '#444444'   # Dark gray track outline
BG_COLOR = '#1a1a2e'      # Dark background


def _get_car_color(position):
    if position <= 0:
        return '#555555'
    if position <= 3:
        return CAR_COLORS[position - 1]
    if position <= 10:
        return CAR_COLORS[3]
    if position <= 20:
        return CAR_COLORS[4]
    return CAR_COLORS[5]


class MapWindow:
    """Tkinter window that displays the track map with car positions.

    All tkinter operations happen in the tkinter thread only.
    Communication from the telemetry thread uses a thread-safe queue.
    Shutdown is handled via a flag that the tkinter thread polls.
    """

    def __init__(self, width=700, height=600):
        self.width = width
        self.height = height
        self._data_queue = queue.Queue(maxsize=5)
        self._running = threading.Event()
        self._thread = None
        self._root = None
        self._canvas = None
        self.margin = 50

    def start(self):
        """Start the map window in a separate thread."""
        self._running.set()
        self._thread = threading.Thread(target=self._run, daemon=True, name='TrackMapGUI')
        self._thread.start()

    def stop(self):
        """Signal the window to close. The tkinter thread will handle its own cleanup."""
        self._running.clear()
        # Don't touch self._root from this thread — that causes the Tcl error!

    def _run(self):
        """Main tkinter loop (runs in its own thread)."""
        try:
            self._root = tk.Tk()
            self._root.title('iRacing Track Map')
            self._root.configure(bg=BG_COLOR)
            self._root.geometry(f'{self.width}x{self.height}')
            self._root.resizable(True, True)

            self._canvas = tk.Canvas(self._root, bg=BG_COLOR, highlightthickness=0)
            self._canvas.pack(fill=tk.BOTH, expand=True)

            self._root.protocol('WM_DELETE_WINDOW', self._on_close)
            self._canvas.bind('<Configure>', self._on_resize)

            # Initial message
            self._canvas.create_text(
                self.width // 2, self.height // 2,
                text='Warte auf Streckendaten...\nFahre eine Runde um die Strecke zu erfassen.',
                fill='#888888', font=('Consolas', 14), justify='center',
            )

            # Start polling loop
            self._poll_data()
            self._root.mainloop()
        except Exception:
            pass
        finally:
            # Clean up references IN this thread
            self._canvas = None
            self._root = None

    def _on_close(self):
        """Called when user clicks the X button — in the tkinter thread."""
        self._running.clear()
        if self._root:
            self._root.destroy()

    def _on_resize(self, event):
        self.width = event.width
        self.height = event.height

    def _poll_data(self):
        """Poll for new data from the telemetry thread. Runs in tkinter thread."""
        # Check if we should shut down
        if not self._running.is_set():
            if self._root:
                self._root.destroy()
            return

        # Process all pending data (only draw the latest)
        latest_data = None
        try:
            while True:
                latest_data = self._data_queue.get_nowait()
        except queue.Empty:
            pass

        if latest_data is not None:
            self._draw(latest_data)

        # Schedule next poll — always from tkinter thread
        if self._root and self._running.is_set():
            self._root.after(100, self._poll_data)

    def update_data(self, track_outline, cars, mapping_progress=None):
        """Called from the telemetry thread to push new data (thread-safe)."""
        data = {
            'track_outline': track_outline,
            'cars': cars,
            'mapping_progress': mapping_progress,
        }
        # Drop old data if queue is full (we only care about latest)
        try:
            self._data_queue.put_nowait(data)
        except queue.Full:
            try:
                self._data_queue.get_nowait()  # discard oldest
            except queue.Empty:
                pass
            try:
                self._data_queue.put_nowait(data)
            except queue.Full:
                pass

    def _to_screen(self, nx, ny):
        """Convert normalized (0-1) coordinates to screen pixels."""
        draw_w = self.width - 2 * self.margin
        draw_h = self.height - 2 * self.margin
        scale = min(draw_w, draw_h)
        offset_x = self.margin + (draw_w - scale) / 2
        offset_y = self.margin + (draw_h - scale) / 2
        return offset_x + nx * scale, offset_y + ny * scale

    def _draw(self, data):
        """Redraw the entire canvas. Always runs in tkinter thread."""
        if not self._canvas:
            return

        self._canvas.delete('all')

        track_outline = data.get('track_outline')
        cars = data.get('cars', [])
        mapping_progress = data.get('mapping_progress')

        # --- Mapping progress screen ---
        if mapping_progress is not None and track_outline is None:
            pct = int(mapping_progress * 100)
            self._canvas.create_text(
                self.width // 2, self.height // 2,
                text=f'Strecke wird erfasst... {pct}%\nFahre die erste Runde weiter.',
                fill='#888888', font=('Consolas', 14), justify='center',
            )
            bar_w, bar_h = 300, 20
            bx = (self.width - bar_w) // 2
            by = self.height // 2 + 50
            self._canvas.create_rectangle(bx, by, bx + bar_w, by + bar_h,
                                          outline='#555555', fill=BG_COLOR)
            self._canvas.create_rectangle(bx, by, bx + bar_w * mapping_progress, by + bar_h,
                                          outline='', fill='#00CC66')
            return

        # --- Waiting screen ---
        if track_outline is None:
            self._canvas.create_text(
                self.width // 2, self.height // 2,
                text='Warte auf Streckendaten...',
                fill='#888888', font=('Consolas', 14),
            )
            return

        # --- Draw track outline ---
        if len(track_outline) >= 2:
            screen_points = []
            for nx, ny in track_outline:
                sx, sy = self._to_screen(nx, ny)
                screen_points.extend([sx, sy])

            # Close the loop
            sx, sy = self._to_screen(track_outline[0][0], track_outline[0][1])
            screen_points.extend([sx, sy])

            # Thick track background
            self._canvas.create_line(
                *screen_points,
                fill=TRACK_COLOR, width=14, smooth=True,
                capstyle='round', joinstyle='round',
            )
            # Center line
            self._canvas.create_line(
                *screen_points,
                fill='#555555', width=2, smooth=True,
            )

        # --- Draw cars (player on top) ---
        sorted_cars = sorted(cars, key=lambda c: c.get('is_player', False))

        for car in sorted_cars:
            x, y = car.get('x', 0), car.get('y', 0)
            sx, sy = self._to_screen(x, y)
            is_player = car.get('is_player', False)

            radius = 8 if is_player else 5
            color = PLAYER_COLOR if is_player else _get_car_color(car.get('position', 0))

            self._canvas.create_oval(
                sx - radius, sy - radius, sx + radius, sy + radius,
                fill=color,
                outline='white' if is_player else '',
                width=2 if is_player else 0,
            )

            car_num = car.get('car_number', '?')
            label_color = '#FFFFFF' if is_player else '#CCCCCC'
            font_size = 10 if is_player else 8
            self._canvas.create_text(
                sx, sy - radius - 8,
                text=f'#{car_num}',
                fill=label_color,
                font=('Consolas', font_size, 'bold' if is_player else 'normal'),
            )

        # --- Title ---
        self._canvas.create_text(
            self.width // 2, 20,
            text='TRACK MAP',
            fill='#FFFFFF', font=('Consolas', 16, 'bold'),
        )

        # --- Legend ---
        legend_y = self.height - 25
        self._canvas.create_oval(10, legend_y - 5, 20, legend_y + 5,
                                 fill=PLAYER_COLOR, outline='white', width=1)
        self._canvas.create_text(25, legend_y, text='Du',
                                 fill='#FFFFFF', font=('Consolas', 9), anchor='w')
        self._canvas.create_text(
            self.width - 10, legend_y,
            text=f'{len(cars)} Autos auf der Strecke',
            fill='#888888', font=('Consolas', 9), anchor='e',
        )
