"""Qualifying panel — large, prominent best-lap display.

Always visible. Extra emphasised (blue border + larger fonts) when the
current iRacing session type contains "Qualify" so you can read it at a
glance from a second monitor while driving.
"""

from PySide6.QtWidgets import QFrame, QLabel, QHBoxLayout, QVBoxLayout
from PySide6.QtCore import Slot

import config


QSS = """
QFrame#QualPanel {
    border: 1px solid #1f3a5f;
    background: #0d1b2a;
    border-radius: 8px;
}
QFrame#QualPanel[emphasized="true"] {
    border: 3px solid #3a7bd5;
    background: #0d2340;
}
QLabel#QualCaption {
    color: #7fb8e8;
    font-size: 17px;
    font-weight: bold;
    letter-spacing: 2px;
}
QLabel#QualBest {
    color: #00FF66;
    font-weight: bold;
}
QLabel#QualLast {
    color: #ffffff;
    font-weight: bold;
}
QLabel#QualDelta {
    font-weight: bold;
}
QLabel#QualDelta[sign="neg"] { color: #7cf38b; }
QLabel#QualDelta[sign="pos"] { color: #ff8080; }
QLabel#QualDelta[sign="none"] { color: #aaa; }
QLabel#QualSectors {
    color: #c8c8c8;
    font-size: 19px;
    font-weight: bold;
    letter-spacing: 1px;
}
"""


def _format_laptime(sec):
    if sec is None:
        return "--:--.---"
    m = int(sec // 60)
    s = sec % 60
    return f"{m}:{s:06.3f}"


def _format_delta(d):
    if d is None:
        return ("--.--", "none")
    if abs(d) < 0.001:
        return ("0.000", "none")
    sign = "neg" if d < 0 else "pos"
    return (f"{d:+.3f}", sign)


class QualifyingPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("QualPanel")
        self.setProperty("emphasized", False)
        self.setStyleSheet(QSS)

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 18)
        v.setSpacing(10)

        cap = QLabel("BESTE RUNDE")
        cap.setObjectName("QualCaption")
        v.addWidget(cap)

        self.best_lbl = QLabel("--:--.---")
        self.best_lbl.setObjectName("QualBest")
        self.best_lbl.setStyleSheet(f"font-size: {config.GUI_QUAL_BEST_FONT_PX}px;")
        v.addWidget(self.best_lbl)

        row = QHBoxLayout()
        row.setSpacing(28)

        last_box = QVBoxLayout()
        last_box.setSpacing(2)
        last_cap = QLabel("LETZTE")
        last_cap.setObjectName("QualCaption")
        last_box.addWidget(last_cap)
        self.last_lbl = QLabel("--:--.---")
        self.last_lbl.setObjectName("QualLast")
        self.last_lbl.setStyleSheet(f"font-size: {config.GUI_QUAL_LAST_FONT_PX}px;")
        last_box.addWidget(self.last_lbl)
        row.addLayout(last_box)

        delta_box = QVBoxLayout()
        delta_box.setSpacing(2)
        delta_cap = QLabel("DELTA")
        delta_cap.setObjectName("QualCaption")
        delta_box.addWidget(delta_cap)
        self.delta_lbl = QLabel("--.--")
        self.delta_lbl.setObjectName("QualDelta")
        self.delta_lbl.setProperty("sign", "none")
        self.delta_lbl.setStyleSheet(f"font-size: {config.GUI_QUAL_DELTA_FONT_PX}px;")
        delta_box.addWidget(self.delta_lbl)
        row.addLayout(delta_box)

        row.addStretch(1)
        v.addLayout(row)

        self.sector_lbl = QLabel("S1 --.-    S2 --.-    S3 --.-")
        self.sector_lbl.setObjectName("QualSectors")
        v.addWidget(self.sector_lbl)

    @Slot(dict)
    def update_snapshot(self, snapshot):
        session_type = snapshot.get('session_type', '') or ''
        want_emph = 'Qualify' in session_type
        if self.property("emphasized") != want_emph:
            self.setProperty("emphasized", want_emph)
            self.style().unpolish(self)
            self.style().polish(self)

        q = snapshot.get('qual_data') or {}
        self.best_lbl.setText(_format_laptime(q.get('best_lap')))
        self.last_lbl.setText(_format_laptime(q.get('last_lap')))

        text, sign = _format_delta(q.get('delta'))
        self.delta_lbl.setText(text)
        if self.delta_lbl.property("sign") != sign:
            self.delta_lbl.setProperty("sign", sign)
            self.delta_lbl.style().unpolish(self.delta_lbl)
            self.delta_lbl.style().polish(self.delta_lbl)

        sectors = q.get('sectors_last') or []
        if sectors:
            parts = [f"S{i+1} {s:.2f}s" for i, s in enumerate(sectors)]
            self.sector_lbl.setText("    ".join(parts))
        else:
            self.sector_lbl.setText("S1 --.-    S2 --.-    S3 --.-")
