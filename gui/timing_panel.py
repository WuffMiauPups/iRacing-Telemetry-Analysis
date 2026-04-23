"""TimingPanel — ahead / me / behind blocks with gap + catch info."""

from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, Slot


PANEL_QSS = """
QFrame#TimingPanel {
    border: 1px solid #1f3a5f;
    border-radius: 8px;
    background: #0d1b2a;
}
QLabel#TimingTitle {
    color: #7fb8e8;
    font-weight: bold;
    font-size: 17px;
    letter-spacing: 1px;
    padding: 14px 16px 6px;
}
QLabel.entry {
    color: #ffffff;
    font-size: 18px;
    padding: 16px 18px;
    background: #15273f;
    border: 1px solid #1f3a5f;
    border-radius: 6px;
    margin: 8px 12px;
    line-height: 170%;
}
QLabel.entry[player="true"] {
    background: #183454;
    border: 2px solid #3a7bd5;
    font-size: 21px;
}
"""


def _fmt_lap(sec):
    if sec is None:
        return "--:--.---"
    m = int(sec // 60)
    s = sec % 60
    return f"{m}:{s:06.3f}"


def _fmt_sectors(sectors):
    if not sectors:
        return "<span style='color:#777'>---</span>"
    return "    ".join(f"<b>S{i+1}</b> {s:.2f}s" for i, s in enumerate(sectors))


def _fmt_catch(catch, is_ahead):
    if catch is None:
        return ""
    gap = catch.get('gap')
    delta = catch.get('per_lap_delta', 0)
    gaining = catch.get('gaining', False)
    laps = catch.get('laps_to_catch')

    parts = []
    if gap is not None:
        parts.append(f"<b>Gap:</b> {gap:.2f}s")
    if delta:
        if is_ahead:
            if gaining:
                parts.append(f"<span style='color:#7cf38b'>+{abs(delta):.3f}s/Rnd schneller</span>")
            else:
                parts.append(f"<span style='color:#ff8080'>-{abs(delta):.3f}s/Rnd langsamer</span>")
        else:
            if gaining:
                parts.append(f"<span style='color:#ff8080'>kommt {abs(delta):.3f}s/Rnd näher</span>")
            else:
                parts.append(f"<span style='color:#7cf38b'>verliert {abs(delta):.3f}s/Rnd</span>")
    if laps is not None and laps > 0:
        if is_ahead:
            parts.append(f"<b style='color:#7cf38b'>in ~{laps:.0f} Rnd eingeholt</b>")
        else:
            parts.append(f"<b style='color:#ff8080'>holt mich in ~{laps:.0f} Rnd ein</b>")
    return "   ·   ".join(parts)


class TimingPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TimingPanel")
        self.setStyleSheet(PANEL_QSS)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 14)
        v.setSpacing(6)

        title = QLabel("RUNDENZEITEN & GAPS")
        title.setObjectName("TimingTitle")
        v.addWidget(title)

        self.ahead_lbl = QLabel("—")
        self.ahead_lbl.setProperty("class", "entry")
        self.ahead_lbl.setWordWrap(True)
        self.ahead_lbl.setTextFormat(Qt.RichText)
        v.addWidget(self.ahead_lbl)

        self.player_lbl = QLabel("—")
        self.player_lbl.setProperty("class", "entry")
        self.player_lbl.setProperty("player", True)
        self.player_lbl.setWordWrap(True)
        self.player_lbl.setTextFormat(Qt.RichText)
        v.addWidget(self.player_lbl)

        self.behind_lbl = QLabel("—")
        self.behind_lbl.setProperty("class", "entry")
        self.behind_lbl.setWordWrap(True)
        self.behind_lbl.setTextFormat(Qt.RichText)
        v.addWidget(self.behind_lbl)

        v.addStretch(1)

        for lbl in (self.ahead_lbl, self.player_lbl, self.behind_lbl):
            lbl.style().polish(lbl)

    def _render_entry(self, entry, catch, is_ahead):
        if entry is None:
            return "<span style='color:#666'>—</span>"
        lines = [
            f"<span style='color:#7fb8e8; font-weight:bold; letter-spacing:0.5px;'>"
            f"{entry.get('label', '')}</span>"
            f"   <span style='color:#c8c8c8'>(#{entry.get('car_number', '?')} "
            f"{entry.get('driver_name', '?')})</span>",
            f"<b>Letzte</b> {_fmt_lap(entry.get('last_lap'))}"
            f"    <b>Beste</b> <span style='color:#8fd98f'>{_fmt_lap(entry.get('best_lap'))}</span>",
            f"<span style='color:#c8c8c8'>{_fmt_sectors(entry.get('sectors', []))}</span>",
        ]
        catch_line = _fmt_catch(catch, is_ahead)
        if catch_line:
            lines.append(catch_line)
        return "<br>".join(lines)

    @Slot(dict)
    def update_snapshot(self, snapshot):
        t = snapshot.get('timing_data')
        if not t:
            self.ahead_lbl.setText("<span style='color:#666'>—</span>")
            self.player_lbl.setText("<span style='color:#888'>Keine Timing-Daten</span>")
            self.behind_lbl.setText("<span style='color:#666'>—</span>")
            return
        self.ahead_lbl.setText(self._render_entry(t.get('ahead'), t.get('catch_ahead'), True))
        self.player_lbl.setText(self._render_entry(t.get('player'), None, False))
        self.behind_lbl.setText(self._render_entry(t.get('behind'), t.get('catch_behind'), False))
