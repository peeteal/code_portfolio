import sys
from PyQt5.QtWidgets import QApplication
from frontend import SSHApp

if __name__ == "__main__":
    app = QApplication(sys.argv)

    app.setStyleSheet("""
        /* Base widget style */
        QWidget {
            background-color: #1E1E1E;  /* dark background */
            color: #D4D4D4;             /* main text color */
            font-family: 'Segoe UI', Arial, sans-serif;
            font-weight: 600;
            font-size: 12pt;
        }

        /* ComboBox */
        QComboBox {
            background-color: #2D2D2D;
            color: #D4D4D4;
            border: 1px solid #3C3C3C;
            padding: 3px;
            min-height: 25px;
        }

        QComboBox QAbstractItemView {
            background-color: #2D2D2D;
            selection-background-color: #007ACC;
            color: #D4D4D4;
        }

        /* TextEdit */
        QTextEdit {
            background-color: #1E1E1E;
            color: #D4D4D4;
            border: 1px solid #3C3C3C;
            font-family: 'Consolas', monospace;
            font-weight: 300;
            font-size: 11pt;
            padding: 8px;
        }

        /* Buttons */
        QPushButton {
            background-color: #007ACC;
            color: #FFFFFF;
            font-weight: 600;
            border-radius: 5px;
            padding: 5px;
        }

        QPushButton:hover {
            background-color: #005F9E;
        }

        /* Labels */
        QLabel {
            color: #D4D4D4;
            font-weight: 600;
        }

        QLineEdit {
                background-color: #2D2D2D;
                color: #D4D4D4;
                border: 1px solid #3C3C3C;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 12pt;
                font-weight: 600;
                padding: 6px;  /* padding inside the text field */
                border-radius: 4px;
            }

        /* --- Disabled styles --- */
            QLineEdit:disabled {
                background-color: #3C3C3C;
                color: #6A6A6A;
                border: 1px solid #3C3C3C;
            }
        QWidget:disabled {
            background-color: #2D2D2D;  /* darker gray overlay */
            color: #6A6A6A;             /* gray text */
        }

        QPushButton:disabled {
            background-color: #3C3C3C;
            color: #6A6A6A;
            border: 1px solid #3C3C3C;
        }

        QComboBox:disabled {
            background-color: #3C3C3C;
            color: #6A6A6A;
            border: 1px solid #3C3C3C;
        }

        QTextEdit:disabled {
            background-color: #2D2D2D;
            color: #6A6A6A;
            border: 1px solid #3C3C3C;
        }
    """)

    window = SSHApp()
    window.show()
    sys.exit(app.exec_())
