import os
import enum

import librosa
import soundfile

from PySide6.QtCore import *
from PySide6.QtWidgets import *
from PySide6.QtGui import *
from slicer import Slicer

from gui.Ui_MainWindow import Ui_MainWindow


class WorkStatus(enum.Enum):
    NEW = enum.auto()
    PENDING = enum.auto()
    PROCESSING = enum.auto()
    FINISHED = enum.auto()
    ERROR = enum.auto()

    @staticmethod
    def suffix(status):
        return {
            WorkStatus.NEW: "",
            WorkStatus.PENDING: " (pending)",
            WorkStatus.PROCESSING: " (processing)",
            WorkStatus.FINISHED: " (finished)",
            WorkStatus.ERROR: " (error)",
        }.get(status, "")


class WorkerSignals(QObject):
    startRunning = Signal(int)
    finishedWithArgs = Signal(int)


class Worker(QRunnable):
    def __init__(self, filename: str,
                 db_threshold: float, min_length: int, win_l: int, win_s: int, max_silence_kept: int,
                 out_dir, jobid: int = None):
        super(Worker, self).__init__()
        self.signals = WorkerSignals()

        self.filename = filename

        self.db_threshold = float(db_threshold)
        self.min_length = int(min_length)
        self.win_l = int(win_l)
        self.win_s = int(win_s)
        self.max_silence_kept = int(max_silence_kept)
        self.out_dir = out_dir

        if not isinstance(jobid, int):
            self.jobid = -1
        self.jobid = jobid

    def run(self):
        self.signals.startRunning.emit(self.jobid)
        audio, sr = librosa.load(self.filename, sr=None)
        slicer = Slicer(
            sr=sr,
            db_threshold=self.db_threshold,
            min_length=self.min_length,
            win_l=self.win_l,
            win_s=self.win_s,
            max_silence_kept=self.max_silence_kept
        )
        chunks = slicer.slice(audio)
        out_dir = self.out_dir
        if out_dir == '':
            out_dir = os.path.dirname(os.path.abspath(self.filename))
        for i, chunk in enumerate(chunks):
            path = os.path.join(out_dir, f'%s_%d.wav' % (os.path.basename(self.filename)
                                                         .rsplit('.', maxsplit=1)[0], i))
            soundfile.write(path, chunk, sr)
        self.signals.finishedWithArgs.emit(self.jobid)


class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self.ui.pushButtonAddFiles.clicked.connect(self._q_add_audio_files)
        self.ui.pushButtonBrowse.clicked.connect(self._q_browse_output_dir)
        self.ui.pushButtonClearList.clicked.connect(self._q_clear_audio_list)
        self.ui.pushButtonAbout.clicked.connect(self._q_about)
        self.ui.pushButtonStart.clicked.connect(self._q_start)

        self.ui.progressBar.setMinimum(0)
        self.ui.progressBar.setMaximum(100)
        self.ui.progressBar.setValue(0)

        validator = QRegularExpressionValidator(QRegularExpression(r"\d+"))
        self.ui.lineEditThreshold.setValidator(QDoubleValidator())
        self.ui.lineEditMinLen.setValidator(validator)
        self.ui.lineEditWinLarge.setValidator(validator)
        self.ui.lineEditWinSmall.setValidator(validator)
        self.ui.lineEditMaxSilence.setValidator(validator)
        self.ui.lineEditThreads.setValidator(validator)
        self.ui.lineEditThreads.setText(str(QThread.idealThreadCount()))

        # State variables
        #self.workers:list[QThread] = []
        self.workCount = 0
        self.workFinished = 0
        self.processing = False

        self.setWindowTitle(QApplication.applicationName())

    def _q_browse_output_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Browse Output Directory", ".")
        if path != "":
            self.ui.lineEditOutputDir.setText(QDir.toNativeSeparators(path))

    def _q_add_audio_files(self):
        if self.processing:
            self.warningProcessNotFinished()
            return

        paths, _ = QFileDialog.getOpenFileNames(
            self, 'Select Audio Files', ".", 'Wave Files (*.wav)')
        for path in paths:
            item = QListWidgetItem()
            item.setText(QFileInfo(path).fileName())
            # Save full path at custom role
            item.setData(Qt.ItemDataRole.UserRole + 1, path)
            item.setData(Qt.ItemDataRole.UserRole + 2, WorkStatus.NEW)
            self.ui.listWidgetTaskList.addItem(item)

    def _q_clear_audio_list(self):
        if self.processing:
            self.warningProcessNotFinished()
            return

        self.ui.listWidgetTaskList.clear()

    def _q_about(self):
        QMessageBox.information(self, "About", "OpenVPI Team")

    def _q_set_ui_controls(self, is_processing: bool):
        self.ui.pushButtonStart.setText('Slicing...' if is_processing else 'Start')
        self.ui.pushButtonStart.setEnabled(not is_processing)
        self.ui.pushButtonAddFiles.setEnabled(not is_processing)
        #self.ui.listWidgetTaskList.setEnabled(not is_processing)
        self.ui.pushButtonClearList.setEnabled(not is_processing)
        self.ui.lineEditThreshold.setEnabled(not is_processing)
        self.ui.lineEditMinLen.setEnabled(not is_processing)
        self.ui.lineEditWinLarge.setEnabled(not is_processing)
        self.ui.lineEditWinSmall.setEnabled(not is_processing)
        self.ui.lineEditMaxSilence.setEnabled(not is_processing)
        self.ui.lineEditThreads.setEnabled(not is_processing)
        self.ui.lineEditOutputDir.setEnabled(not is_processing)
        self.ui.pushButtonBrowse.setEnabled(not is_processing)

    def _q_start(self):
        if self.processing:
            self.warningProcessNotFinished()
            return

        item_count = self.ui.listWidgetTaskList.count()

        if item_count == 0:
            return

        self.ui.progressBar.setMaximum(item_count)
        self.ui.progressBar.setValue(0)
        self._q_set_ui_controls(is_processing=True)

        self.workCount = item_count
        self.workFinished = 0
        self.processing = True

        self.threadpool = QThreadPool()
        print("Multithreading with maximum {} threads".format(self.threadpool.maxThreadCount()))

        for i in range(0, item_count):
            item = self.ui.listWidgetTaskList.item(i)
            path = item.data(Qt.ItemDataRole.UserRole + 1)  # Get full path

            worker = Worker(filename=path,
                            db_threshold=float(self.ui.lineEditThreshold.text()),
                            min_length=int(self.ui.lineEditMinLen.text()),
                            win_l=int(self.ui.lineEditWinLarge.text()),
                            win_s=int(self.ui.lineEditWinSmall.text()),
                            max_silence_kept=int(self.ui.lineEditMaxSilence.text()),
                            out_dir=self.ui.lineEditOutputDir.text(),
                            jobid=i)
            worker.signals.startRunning.connect(self._q_threadStartProcessing)
            worker.signals.finishedWithArgs.connect(self._q_threadFinished)

            status = WorkStatus.PENDING
            item.setData(Qt.ItemDataRole.UserRole + 2, status)
            item.setText(QFileInfo(item.data(Qt.ItemDataRole.UserRole + 1)).fileName() + WorkStatus.suffix(status))
            #worker.start()
            try:
                max_threads = int(self.ui.lineEditThreads.text())
                if max_threads < 1:
                    raise ValueError("Number of threads must be a positive integer.")
            except ValueError:
                max_threads = QThread.idealThreadCount()
                self.ui.lineEditThreads.setText(str(max_threads))

            self.threadpool.setMaxThreadCount(max_threads)
            self.threadpool.start(worker)

            #self.workers.append(worker)  # Collect in case of auto deletion

    def _q_threadStartProcessing(self, jobid):
        item = self.ui.listWidgetTaskList.item(jobid)
        status = WorkStatus.PROCESSING
        item.setData(Qt.ItemDataRole.UserRole + 2, status)
        item.setText(QFileInfo(item.data(Qt.ItemDataRole.UserRole + 1)).fileName() + WorkStatus.suffix(status))

    def _q_threadFinished(self, jobid=-1):
        self.workFinished += 1
        self.ui.progressBar.setValue(self.workFinished)
        if jobid >= 0:
            currentItem = self.ui.listWidgetTaskList.item(jobid)
            currentItem.setData(Qt.ItemDataRole.UserRole + 2, WorkStatus.FINISHED)
            currentItem.setText(
                QFileInfo(currentItem.data(Qt.ItemDataRole.UserRole + 1)).fileName() +
                            WorkStatus.suffix(WorkStatus.FINISHED))

        if self.workFinished == self.workCount:
            # Join all workers
            #for worker in self.workers:
            #    worker.wait()
            #self.workers.clear()
            self.processing = False

            self._q_set_ui_controls(is_processing=False)
            QMessageBox.information(
                self, QApplication.applicationName(), "Slicing complete!")

    def warningProcessNotFinished(self):
        QMessageBox.warning(self, QApplication.applicationName(),
                            "Please wait for slicing to complete!")

    def closeEvent(self, event):
        if self.processing:
            self.warningProcessNotFinished()
            event.ignore()