"""PySide6 unified GUI entry point.

Launches a QMainWindow with two tabs:
  - Live: connects to iRacing via TelemetryWorker; shows timing, qualifying,
    pit, tyres, and the live track map (previously a separate Tk window).
  - Analyze: browses race_logs/ and plots lap comparisons interactively.

The existing terminal entry (`python main.py`) remains available unchanged
for users who prefer the CLI.
"""

import os
import sys

from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget
from PySide6.QtCore import Qt, QThread

import config
from gui.live_tab import LiveTab
from gui.analyze_tab import AnalyzeTab
from gui.worker import TelemetryWorker


RACE_LOGS_DIR = os.path.join(os.path.dirname(__file__), 'race_logs')


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iRacing Telemetry")
        self.resize(config.GUI_WINDOW_W, config.GUI_WINDOW_H)

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.live_tab = LiveTab()
        self.analyze_tab = AnalyzeTab(RACE_LOGS_DIR)
        self.tabs.addTab(self.live_tab, "Live")
        self.tabs.addTab(self.analyze_tab, "Analyze")

        # Worker in its own thread
        self.thread = QThread()
        self.thread.setObjectName("TelemetryThread")
        self.worker = TelemetryWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.snapshot.connect(self.live_tab.on_snapshot, Qt.QueuedConnection)
        self.thread.start()

    def closeEvent(self, event):
        self.worker.stop()
        self.thread.quit()
        # Finalization (session_summary + lap_analysis + mini_sectors +
        # variance plots) can take 10–30s on a 30-lap session. 60s gives
        # it plenty of headroom; bail gracefully if it somehow exceeds that.
        if not self.thread.wait(60000):
            print("[app] Worker did not terminate within 60s — forcing close.",
                  flush=True)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
