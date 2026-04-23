"""MapWidget — QPainter-based track map with live car positions.

Ported from display/map_window.py (Tkinter Canvas) with the same drawing
math. Paints on demand: each new snapshot calls self.update() which
triggers paintEvent at Qt's next repaint.
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QPointF, Slot
from PySide6.QtGui import (
    QPainter, QPainterPath, QColor, QPen, QBrush, QFont, QPolygonF,
)

import config


CAR_COLORS = [
    '#FFD700', '#C0C0C0', '#CD7F32', '#00FF00', '#00CCFF', '#FFFFFF',
]
PLAYER_COLOR = '#FF00FF'
TRACK_COLOR = '#444444'
CENTER_COLOR = '#555555'
BG_COLOR = '#1a1a2e'
MARGIN = 40


def _car_color(position):
    if position <= 0:
        return '#555555'
    if position <= 3:
        return CAR_COLORS[position - 1]
    if position <= 10:
        return CAR_COLORS[3]
    if position <= 20:
        return CAR_COLORS[4]
    return CAR_COLORS[5]


class MapWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(config.GUI_MAP_MIN_W, config.GUI_MAP_MIN_H)
        self.setAutoFillBackground(True)

        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor(BG_COLOR))
        self.setPalette(pal)

        self._outline = None           # list of (nx, ny)
        self._cars = []
        self._map_status = ""
        self._mapping_progress = None
        self._outline_path = None
        self._cached_size = None

    @Slot(dict)
    def update_snapshot(self, snapshot):
        self._outline = snapshot.get('track_outline')
        self._cars = snapshot.get('cars') or []
        self._map_status = snapshot.get('map_status') or ""

        # Extract mapping progress from the status string ("Erfasse … NN%"),
        # used only for the progress bar when outline isn't ready yet.
        self._mapping_progress = None
        if self._outline is None and 'Erfasse' in self._map_status:
            try:
                pct_str = self._map_status.split('…')[-1].strip()
                pct = float(pct_str.split('%')[0].strip())
                self._mapping_progress = pct / 100.0
            except (ValueError, IndexError):
                pass

        # Invalidate cached path whenever outline changes.
        if self._outline is not None:
            self._outline_path = self._build_path(self._outline)
        else:
            self._outline_path = None

        self.update()

    def _build_path(self, outline):
        if not outline or len(outline) < 2:
            return None
        path = QPainterPath()
        # Build in NORMALIZED coordinates; scale+translate in paintEvent.
        path.moveTo(outline[0][0], outline[0][1])
        for nx, ny in outline[1:]:
            path.lineTo(nx, ny)
        path.closeSubpath()
        return path

    def _xform(self):
        """Return (offset_x, offset_y, scale) that maps 0-1 to widget pixels."""
        draw_w = self.width() - 2 * MARGIN
        draw_h = self.height() - 2 * MARGIN
        if draw_w <= 0 or draw_h <= 0:
            return MARGIN, MARGIN, 1.0
        scale = min(draw_w, draw_h)
        offset_x = MARGIN + (draw_w - scale) / 2
        offset_y = MARGIN + (draw_h - scale) / 2
        return offset_x, offset_y, scale

    def _to_screen(self, nx, ny):
        ox, oy, scale = self._xform()
        return ox + nx * scale, oy + ny * scale

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(BG_COLOR))

        # Title
        p.setPen(QColor('#FFFFFF'))
        p.setFont(QFont('Consolas', 14, QFont.Bold))
        p.drawText(QRectF(0, 4, self.width(), 20), Qt.AlignHCenter, 'TRACK MAP')

        # Mapping progress screen
        if self._outline_path is None and self._mapping_progress is not None:
            self._paint_progress(p)
            p.end()
            return

        # Waiting screen
        if self._outline_path is None:
            p.setPen(QColor('#888888'))
            p.setFont(QFont('Consolas', 12))
            p.drawText(self.rect(), Qt.AlignCenter,
                       self._map_status or 'Warte auf Streckendaten …')
            p.end()
            return

        # Track outline — scaled on the fly from normalized path.
        ox, oy, scale = self._xform()
        p.save()
        p.translate(ox, oy)
        p.scale(scale, scale)

        outline_pen = QPen(QColor(TRACK_COLOR))
        outline_pen.setWidthF(14.0 / scale)
        outline_pen.setCapStyle(Qt.RoundCap)
        outline_pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(outline_pen)
        p.drawPath(self._outline_path)

        center_pen = QPen(QColor(CENTER_COLOR))
        center_pen.setWidthF(2.0 / scale)
        p.setPen(center_pen)
        p.drawPath(self._outline_path)
        p.restore()

        # Cars (player drawn last so it's on top).
        sorted_cars = sorted(self._cars, key=lambda c: c.get('is_player', False))
        p.setFont(QFont('Consolas', 8, QFont.Bold))
        for car in sorted_cars:
            self._paint_car(p, car)

        # Footer: status + car count
        p.setPen(QColor('#888888'))
        p.setFont(QFont('Consolas', 8))
        p.drawText(QRectF(8, self.height() - 18, self.width() - 16, 14),
                   Qt.AlignLeft, self._map_status)
        p.drawText(QRectF(8, self.height() - 18, self.width() - 16, 14),
                   Qt.AlignRight, f"{len(self._cars)} Autos auf der Strecke")

        p.end()

    def _paint_car(self, p, car):
        x, y = car.get('x', 0), car.get('y', 0)
        sx, sy = self._to_screen(x, y)
        is_player = car.get('is_player', False)
        radius = 8 if is_player else 5
        color = QColor(PLAYER_COLOR if is_player else _car_color(car.get('position', 0)))

        p.setBrush(QBrush(color))
        if is_player:
            pen = QPen(QColor('#FFFFFF'))
            pen.setWidth(2)
            p.setPen(pen)
        else:
            p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(sx, sy), radius, radius)

        car_num = car.get('car_number', '?')
        label_color = QColor('#FFFFFF' if is_player else '#CCCCCC')
        p.setPen(label_color)
        label = f"#{car_num}"
        tw = p.fontMetrics().horizontalAdvance(label)
        p.drawText(QPointF(sx - tw / 2, sy - radius - 4), label)

    def _paint_progress(self, p):
        pct = self._mapping_progress
        p.setPen(QColor('#888888'))
        p.setFont(QFont('Consolas', 12))
        p.drawText(self.rect(), Qt.AlignCenter,
                   f"Strecke wird erfasst …  {int(pct * 100)}%\nFahre die erste Runde weiter.")

        bar_w = 300
        bar_h = 18
        bx = (self.width() - bar_w) // 2
        by = self.height() // 2 + 40
        p.setPen(QColor('#555555'))
        p.setBrush(QColor(BG_COLOR))
        p.drawRect(bx, by, bar_w, bar_h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#00CC66'))
        p.drawRect(bx, by, int(bar_w * pct), bar_h)
