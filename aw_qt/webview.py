import sys
import signal

from PyQt5.QtCore import QUrl, QTimer
from PyQt5.QtWidgets import QApplication, QWidget

from PyQt5.QtWebEngineWidgets import (
    QWebEngineView,
    QWebEnginePage,
    QWebEngineSettings,
)


class MyBrowser(QWebEnginePage):
    """Settings for the browser."""

    def userAgentForUrl(self, url):
        """Returns a User Agent that will be seen by the website."""
        return "Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.36"


class Browser(QWebEngineView):
    def __init__(self, *args, **kwargs):
        QWebEngineView.__init__(self, *args, **kwargs)
        self.setPage(MyBrowser())

        settings = QWebEngineSettings.globalSettings()
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.AllowRunningInsecureContent, True)

        self.setWindowTitle("Loading...")
        self.titleChanged.connect(self.adjustTitle)
        # super(Browser).connect(self.ui.webView,QtCore.SIGNAL("titleChanged (const QString&amp;)"), self.adjustTitle)

    def load(self, url):
        self.setUrl(QUrl(url))

    def adjustTitle(self):
        self.setWindowTitle(self.title())


def exit() -> None:
    print("Shutdown initiated, stopping all services...")
    # Terminate entire process group, just in case.
    # os.killpg(0, signal.SIGINT)

    QApplication.quit()


def create_webview(parent):
    view = Browser(parent=parent)
    # view.showMaximized()
    view.load("http://localhost:5666/")
    view.show()
    print("Opened webview")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("ActivityWatch")

    # Without this, Ctrl+C will have no effect
    signal.signal(signal.SIGINT, lambda *args: exit())
    # Ensure cleanup happens on SIGTERM
    signal.signal(signal.SIGTERM, lambda *args: exit())

    timer = QTimer()
    timer.start(100)  # You may change this if you wish.
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.

    widget = QWidget()
    create_webview(widget)
    widget.show()

    QApplication.setQuitOnLastWindowClosed(False)

    print("Initialized")

    app.exec_()
