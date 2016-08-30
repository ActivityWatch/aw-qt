import logging

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QPlainTextEdit


class LogViewer(QWidget):
    def __init__(self, *args, name="unknown", **kwargs):
        QWidget.__init__(self, *args, **kwargs)
        self.logger = logging.getLogger("logviewer")

        self.name = name

        self.build_ui()

    def build_ui(self):
        self.text_edit = QPlainTextEdit()
        self.text_edit.setLineWrapMode(QPlainTextEdit.NoWrap)

        self.hbox = QHBoxLayout(self)

        self.hbox.addWidget(self.text_edit)
        self.setLayout(self.hbox)

        self.move(100, 100)
        self.resize(600, 400)
        self.setWindowTitle('Log for {}'.format(self.name))
        self.show()

    def set_log(self, text):
        self.text_edit.document().setPlainText(text)


if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication
    app = QApplication(sys.argv)
    lv = LogViewer()
    lv.set_log("Log would go here")
    sys.exit(app.exec_())
