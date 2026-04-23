"""CarStatusPanel — live RPM / Oil / Water / Voltage readouts with
threshold coloring. Sits below the track map on the Live tab.

Thresholds are fixed (not car-specific) — generic Formula-car ranges:
  RPM:       green <85% of redline, yellow 85–95%, red >95%
  OilTemp:   green <110°C,        yellow 110–130, red >130
  WaterTemp: green <95°C,         yellow 95–110,  red >110
  Voltage:   green >12.5V,        yellow 12.0–12.5, red <12.0
"""

from PySide6.QtWidgets import QFrame, QGridLayout, QLabel
from PySide6.QtCore import Slot


QSS = """
QFrame#CarStatusPanel {
    border: 1px solid #1f3a5f;
    background: #0d1b2a;
    border-radius: 8px;
}
QLabel[role="caption"] {
    color: #7fb8e8;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 1.5px;
}
QLabel[role="value"] {
    color: #ffffff;
    font-size: 26px;
    font-weight: bold;
    font-family: Consolas, monospace;
}
QLabel[role="value"][level="ok"]   { color: #7cf38b; }
QLabel[role="value"][level="warn"] { color: #f5d06a; }
QLabel[role="value"][level="bad"]  { color: #ff6b6b; }
QLabel[role="value"][level="none"] { color: #666666; }
"""


def _rpm_level(rpm, redline):
    if rpm is None:
        return 'none'
    if redline and redline > 0:
        frac = rpm / redline
        if frac > 0.95:
            return 'bad'
        if frac > 0.85:
            return 'warn'
        return 'ok'
    # No redline info: treat >8000 as warn, >10000 as bad
    if rpm > 10000:
        return 'bad'
    if rpm > 8000:
        return 'warn'
    return 'ok'


def _temp_level(v, warn_above, bad_above):
    if v is None:
        return 'none'
    if v > bad_above:
        return 'bad'
    if v > warn_above:
        return 'warn'
    return 'ok'


def _voltage_level(v):
    if v is None:
        return 'none'
    if v < 12.0:
        return 'bad'
    if v < 12.5:
        return 'warn'
    return 'ok'


class CarStatusPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CarStatusPanel")
        self.setStyleSheet(QSS)

        grid = QGridLayout(self)
        grid.setContentsMargins(16, 10, 16, 12)
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(4)

        self._value_labels = {}

        for col, (key, caption) in enumerate((
            ('rpm',     'RPM'),
            ('oil',     'OIL °C'),
            ('water',   'WATER °C'),
            ('voltage', 'VOLT'),
        )):
            cap = QLabel(caption)
            cap.setProperty('role', 'caption')
            val = QLabel('—')
            val.setProperty('role', 'value')
            val.setProperty('level', 'none')
            grid.addWidget(cap, 0, col)
            grid.addWidget(val, 1, col)
            self._value_labels[key] = val

    def _set_level(self, label, level):
        if label.property('level') != level:
            label.setProperty('level', level)
            label.style().unpolish(label)
            label.style().polish(label)

    @Slot(dict)
    def update_snapshot(self, snapshot):
        cs = snapshot.get('car_status') or {}
        rpm = cs.get('rpm')
        oil = cs.get('oil_temp')
        water = cs.get('water_temp')
        volt = cs.get('voltage')
        redline = cs.get('rpm_redline')

        # RPM
        lbl = self._value_labels['rpm']
        lbl.setText(f"{int(rpm)}" if rpm is not None else '—')
        self._set_level(lbl, _rpm_level(rpm, redline))

        # Oil
        lbl = self._value_labels['oil']
        lbl.setText(f"{oil:.0f}" if oil is not None else '—')
        self._set_level(lbl, _temp_level(oil, 110, 130))

        # Water
        lbl = self._value_labels['water']
        lbl.setText(f"{water:.0f}" if water is not None else '—')
        self._set_level(lbl, _temp_level(water, 95, 110))

        # Voltage
        lbl = self._value_labels['voltage']
        lbl.setText(f"{volt:.1f}" if volt is not None else '—')
        self._set_level(lbl, _voltage_level(volt))
