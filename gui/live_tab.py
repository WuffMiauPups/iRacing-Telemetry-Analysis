"""Live tab — real-time telemetry display fed by TelemetryWorker snapshots."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy,
)
from PySide6.QtCore import Qt, Slot

from gui.timing_panel import TimingPanel
from gui.qualifying_panel import QualifyingPanel
from gui.map_widget import MapWidget
from gui.car_status_panel import CarStatusPanel


HEADER_QSS = """
QLabel#LiveHeader {
    color: #ffffff;
    background: #0d1b2a;
    border: 1px solid #1f3a5f;
    border-radius: 6px;
    padding: 10px 14px;
    font-size: 16px;
    font-weight: bold;
    letter-spacing: 0.5px;
}
QLabel#LiveHeader[state="disconnected"] { color: #f5a623; border-color: #4d3c1f; }
QLabel#LiveHeader[state="off_track"]    { color: #c8c8c8; border-color: #333; }
QLabel#LiveHeader[state="driving"]      { color: #8be3ff; border-color: #2a5a8a; }
QLabel#TrackPB {
    color: #00FF66;
    background: #0d1b2a;
    border: 1px solid #1f3a5f;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 14px;
    font-weight: bold;
    letter-spacing: 0.5px;
}
QLabel#SectorDelta {
    background: #0d1b2a;
    border: 1px solid #1f3a5f;
    border-radius: 6px;
    padding: 10px 14px;
    font-size: 28px;
    font-weight: bold;
    letter-spacing: 0.5px;
}
QLabel#SectorDelta[sign="neg"]  { color: #7cf38b; border-color: #2d5a2d; }
QLabel#SectorDelta[sign="pos"]  { color: #ff8080; border-color: #5a2d2d; }
QLabel#SectorDelta[sign="best"] { color: #00FF66; border-color: #2d5a4d; }
QLabel#SectorDelta[sign="none"] { color: #888888; border-color: #333; }
"""


def _fmt_pb_time(sec):
    m = int(sec // 60)
    s = sec % 60
    return f"{m}:{s:06.3f}"


def _fmt_pb_ago(dt):
    if dt is None:
        return ""
    from datetime import datetime
    delta = datetime.now() - dt
    days = delta.days
    if days == 0:
        return "heute"
    if days == 1:
        return "gestern"
    if days < 30:
        return f"vor {days} Tagen"
    if days < 365:
        return f"vor {days // 30} Monaten"
    return f"vor {days // 365} Jahren"


class LiveTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("QWidget { background: #0b1422; }")
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        self.header = QLabel("Warte auf iRacing …")
        self.header.setObjectName("LiveHeader")
        self.header.setProperty("state", "disconnected")
        self.header.setStyleSheet(HEADER_QSS)
        root.addWidget(self.header)

        self.pb_label = QLabel("")
        self.pb_label.setObjectName("TrackPB")
        self.pb_label.setStyleSheet(HEADER_QSS)
        self.pb_label.setVisible(False)
        root.addWidget(self.pb_label)

        self.sector_delta_label = QLabel("Sektoren: warte auf erste Runde …")
        self.sector_delta_label.setObjectName("SectorDelta")
        self.sector_delta_label.setProperty("sign", "none")
        self.sector_delta_label.setStyleSheet(HEADER_QSS)
        root.addWidget(self.sector_delta_label)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, 1)

        left = QVBoxLayout()
        left.setSpacing(10)
        body.addLayout(left, 3)

        self.qual_panel = QualifyingPanel()
        self.timing_panel = TimingPanel()

        left.addWidget(self.qual_panel)
        left.addWidget(self.timing_panel, 1)

        # Right column: map on top, car-status panel below.
        right = QVBoxLayout()
        right.setSpacing(10)
        body.addLayout(right, 4)

        self.map_widget = MapWidget()
        self.map_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.map_widget, 4)

        self.car_status_panel = CarStatusPanel()
        right.addWidget(self.car_status_panel, 0)

    def _set_header(self, text, state):
        self.header.setText(text)
        if self.header.property("state") != state:
            self.header.setProperty("state", state)
            self.header.style().unpolish(self.header)
            self.header.style().polish(self.header)

    @Slot(dict)
    def on_snapshot(self, snapshot):
        if not snapshot.get('connected'):
            self._set_header("Warte auf iRacing …", "disconnected")
        elif not snapshot.get('on_track'):
            self._set_header("Nicht auf der Strecke", "off_track")
        else:
            sess = snapshot.get('session_info') or {}
            track = sess.get('track_name', '?')
            stype = snapshot.get('session_type', '?')
            status = snapshot.get('map_status', '')
            self._set_header(f"{track}    |    {stype}    |    {status}", "driving")

        self._update_pb_label(snapshot)
        self._update_sector_delta_label(snapshot)

        self.qual_panel.update_snapshot(snapshot)
        self.timing_panel.update_snapshot(snapshot)
        self.map_widget.update_snapshot(snapshot)
        self.car_status_panel.update_snapshot(snapshot)

    def _update_pb_label(self, snapshot):
        pb = snapshot.get('track_pb')
        track = snapshot.get('track_name', '') or ''
        if not pb or not isinstance(pb, dict):
            self.pb_label.setVisible(False)
            return
        best = pb.get('best_time')
        if best is None:
            self.pb_label.setVisible(False)
            return
        ago = _fmt_pb_ago(pb.get('date'))
        ago_txt = f"  ({ago})" if ago else ""
        self.pb_label.setText(f"★ PB {track}: {_fmt_pb_time(best)}{ago_txt}")
        self.pb_label.setVisible(True)

    def _update_sector_delta_label(self, snapshot):
        sd = snapshot.get('sector_delta')
        bests = snapshot.get('sector_bests') or []

        # Always render the bests summary on the second line.
        bests_txt_parts = []
        for i, b in enumerate(bests):
            if b is None:
                bests_txt_parts.append(f"S{i+1} —")
            else:
                bests_txt_parts.append(f"S{i+1} {b:.2f}s")
        bests_txt = "    ".join(bests_txt_parts) if bests_txt_parts else ""

        if not sd:
            self.sector_delta_label.setText(
                "Sektoren: warte auf erste Runde …"
                + (f"\n{bests_txt}" if bests_txt else ""))
            self._set_sector_sign("none")
            return

        idx = sd.get('sector_idx', 0)
        t = sd.get('time')
        delta = sd.get('delta') or 0.0
        is_new_best = sd.get('is_new_best', False)

        if is_new_best:
            head = f"S{idx+1}: {t:.3f}s  ★ NEUER BEST"
            sign = "best"
        elif delta < 0:
            head = f"S{idx+1}: {t:.3f}s  ({delta:+.3f}s)"
            sign = "neg"
        elif delta > 0:
            head = f"S{idx+1}: {t:.3f}s  ({delta:+.3f}s)"
            sign = "pos"
        else:
            head = f"S{idx+1}: {t:.3f}s"
            sign = "none"

        self.sector_delta_label.setText(
            head + (f"\n{bests_txt}" if bests_txt else ""))
        self._set_sector_sign(sign)

    def _set_sector_sign(self, sign):
        if self.sector_delta_label.property("sign") != sign:
            self.sector_delta_label.setProperty("sign", sign)
            self.sector_delta_label.style().unpolish(self.sector_delta_label)
            self.sector_delta_label.style().polish(self.sector_delta_label)
