# -*- coding: utf-8 -*-
"""
PDF 工具集：合并、拆分、A4 多页拼版。
"""
import math
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Qt, QSize, QMimeData, QRect
from PySide6.QtGui import QColor, QDrag, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


A4_PORTRAIT = (595.27559055, 841.88976378)
A4_LANDSCAPE = (841.88976378, 595.27559055)


def _import_fitz():
    errors = []
    try:
        import pymupdf
        return pymupdf
    except ImportError as exc:
        errors.append(f"pymupdf: {exc}")
    try:
        import fitz
        return fitz
    except ImportError as exc:
        errors.append(f"fitz: {exc}")
    exe = getattr(sys, "executable", "")
    detail = "; ".join(errors)
    raise RuntimeError(
        "缺少 PyMuPDF 依赖，PDF 工具无法处理文件。\n"
        "请在当前运行环境安装：python -m pip install pymupdf\n"
        f"当前运行环境：{exe}\n"
        f"导入详情：{detail}"
    )


def merge_pdfs(input_files, output_file):
    """将多个 PDF 按列表顺序合并为一个 PDF。"""
    fitz = _import_fitz()
    files = [Path(p) for p in input_files if str(p).strip()]
    if not files:
        raise ValueError("请至少选择一个 PDF 文件")
    for pdf in files:
        if not pdf.exists():
            raise FileNotFoundError(str(pdf))

    output = Path(output_file)
    output.parent.mkdir(parents=True, exist_ok=True)
    result = fitz.open()
    try:
        for pdf in files:
            with fitz.open(str(pdf)) as doc:
                result.insert_pdf(doc)
        result.save(str(output), garbage=4, deflate=True)
    finally:
        result.close()
    return output


def split_pdf(input_file, output_dir, pages_per_file=1, prefix=None):
    """将一个 PDF 按 pages_per_file 页一份拆分为多个 PDF。"""
    fitz = _import_fitz()
    src = Path(input_file)
    if not src.exists():
        raise FileNotFoundError(str(src))
    if pages_per_file < 1:
        raise ValueError("每个文件页数必须大于 0")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = prefix or src.stem
    outputs = []

    with fitz.open(str(src)) as doc:
        total = doc.page_count
        for start in range(0, total, pages_per_file):
            end = min(start + pages_per_file, total)
            out = out_dir / f"{prefix}_{start + 1:03d}-{end:03d}.pdf"
            part = fitz.open()
            try:
                part.insert_pdf(doc, from_page=start, to_page=end - 1)
                part.save(str(out), garbage=4, deflate=True)
            finally:
                part.close()
            outputs.append(out)
    return outputs


def nup_pdf_to_a4(
    input_file,
    output_file,
    pages_per_sheet=4,
    rows=2,
    landscape=True,
    margin=5.0,
):
    """将 PDF 按指定页数拼接到 A4 纸上，按 rows 行排列，列数自动计算。"""
    fitz = _import_fitz()
    src = Path(input_file)
    if not src.exists():
        raise FileNotFoundError(str(src))
    if pages_per_sheet < 1:
        raise ValueError("每张 A4 拼接页数必须大于 0")
    if rows < 1 or rows > pages_per_sheet:
        raise ValueError("行数必须大于 0 且不能超过每张拼接页数")
    if margin < 0:
        raise ValueError("边距不能为负数")

    cols = math.ceil(pages_per_sheet / rows)
    page_w, page_h = A4_LANDSCAPE if landscape else A4_PORTRAIT
    slot_w = page_w / cols
    slot_h = page_h / rows
    if slot_w <= 2 * margin or slot_h <= 2 * margin:
        raise ValueError("边距过大，当前拼版格子无法放入页面")

    output = Path(output_file)
    output.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(str(src)) as doc:
        result = fitz.open()
        try:
            for start in range(0, doc.page_count, pages_per_sheet):
                sheet = result.new_page(width=page_w, height=page_h)
                stop = min(start + pages_per_sheet, doc.page_count)
                for offset, page_index in enumerate(range(start, stop)):
                    row = offset // cols
                    col = offset % cols
                    x0 = col * slot_w + margin
                    y0 = row * slot_h + margin
                    rect = fitz.Rect(
                        x0,
                        y0,
                        x0 + slot_w - 2 * margin,
                        y0 + slot_h - 2 * margin,
                    )
                    sheet.show_pdf_page(
                        rect,
                        doc,
                        page_index,
                        keep_proportion=True,
                        overlay=True,
                    )
            result.save(str(output), garbage=4, deflate=True)
        finally:
            result.close()
    return output


class PdfWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            self.finished.emit(self.func(*self.args, **self.kwargs))
        except Exception as exc:
            self.failed.emit(str(exc))


class PdfDropListWidget(QListWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setProperty("dropHint", "支持拖拽多个 PDF 到此处")

    def dragEnterEvent(self, event):
        if self._event_pdf_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._event_pdf_files(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = self._event_pdf_files(event)
        if not files:
            event.ignore()
            return
        self.files_dropped.emit(files)
        event.acceptProposedAction()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.count():
            return
        hint = self.property("dropHint") or "支持拖拽 PDF 到此处"
        painter = QPainter(self.viewport())
        painter.setPen(QColor("#8a98a8"))
        painter.drawText(self.viewport().rect(), Qt.AlignCenter, hint)

    @staticmethod
    def _event_pdf_files(event):
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        files = []
        for url in mime.urls():
            path = url.toLocalFile()
            if path and path.lower().endswith(".pdf") and Path(path).is_file():
                files.append(path)
        return files


class PdfMergeListWidget(PdfDropListWidget):
    preview_requested = Signal(str)
    expand_requested = Signal(str)

    INTERNAL_REORDER_MIME = "application/x-pdf-merge-reorder"
    THUMB_SIZE = QSize(190, 140)
    GRID_SIZE = QSize(230, 215)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._browse_mode = False
        self._overlay = QFrame(self.viewport())
        self._overlay.setObjectName("pdfItemOverlay")
        self._overlay.setStyleSheet(
            """
            QFrame#pdfItemOverlay {
                background:#ffffff;
                border:1px solid #a9b4bf;
                border-radius:4px;
            }
            QToolButton {
                border:none;
                padding:4px;
                color:#4b5563;
                font-size:12px;
            }
            QToolButton:hover { background:#edf5ff; color:#0984e3; }
            """
        )
        overlay_layout = QVBoxLayout(self._overlay)
        overlay_layout.setContentsMargins(4, 4, 4, 4)
        overlay_layout.setSpacing(4)

        self._delete_btn = QToolButton(self._overlay)
        self._delete_btn.setToolTip("删除选中的 PDF")
        self._delete_btn.setText("删除")
        self._delete_btn.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        self._preview_btn = QToolButton(self._overlay)
        self._preview_btn.setToolTip("放大预览")
        self._preview_btn.setText("放大")
        self._preview_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView))
        self._expand_btn = QToolButton(self._overlay)
        self._expand_btn.setToolTip("展开多页预览")
        self._expand_btn.setText("展开")
        self._expand_btn.setIcon(self.style().standardIcon(QStyle.SP_TitleBarUnshadeButton))
        for btn in (self._delete_btn, self._preview_btn, self._expand_btn):
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            overlay_layout.addWidget(btn)

        self._overlay.hide()
        self._drop_indicator = QFrame(self.viewport())
        self._drop_indicator.setObjectName("pdfDropIndicator")
        self._drop_indicator.setStyleSheet(
            """
            QFrame#pdfDropIndicator {
                background:rgba(9, 132, 227, 90);
                border:1px solid rgba(9, 132, 227, 180);
                border-radius:3px;
            }
            """
        )
        self._drop_indicator.hide()
        self.itemSelectionChanged.connect(self._update_overlay)
        self.itemDoubleClicked.connect(self._preview_double_clicked_item)
        self._delete_btn.clicked.connect(self.remove_current_item)
        self._preview_btn.clicked.connect(self._preview_current_item)
        self._expand_btn.clicked.connect(self._expand_current_item)
        self.setWordWrap(True)
        self.set_browse_mode(True)

    def set_browse_mode(self, enabled):
        self._browse_mode = enabled
        self.setDragEnabled(enabled)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(enabled)
        self.setDefaultDropAction(Qt.MoveAction if enabled else Qt.CopyAction)
        self.setDragDropMode(QListWidget.DragDrop if enabled else QListWidget.DropOnly)
        self.setDragDropOverwriteMode(False)
        self.setMovement(QListView.Snap if enabled else QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setWrapping(enabled)
        self.setViewMode(QListView.IconMode if enabled else QListView.ListMode)
        self.setIconSize(self.THUMB_SIZE if enabled else QSize(0, 0))
        self.setGridSize(self.GRID_SIZE if enabled else QSize())
        self.setSpacing(14 if enabled else 2)
        self.setProperty(
            "dropHint",
            "拖拽 PDF 到此处，浏览模式支持拖动缩略图排序"
            if enabled
            else "支持拖拽多个 PDF 到此处",
        )
        for row in range(self.count()):
            self._refresh_item(self.item(row))
        self._drop_indicator.hide()
        self._update_overlay()
        self.viewport().update()

    def add_pdf(self, path):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, str(path))
        item.setData(Qt.UserRole + 1, self.pdf_page_count(path))
        item.setToolTip(str(path))
        item.setFlags(item.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        self._refresh_item(item)
        self.addItem(item)
        return item

    def contains_path(self, path):
        needle = str(path)
        for row in range(self.count()):
            if self.item(row).data(Qt.UserRole) == needle:
                return True
        return False

    def file_paths(self):
        return [self.item(row).data(Qt.UserRole) for row in range(self.count())]

    def remove_current_item(self):
        item = self.currentItem()
        if item is None:
            return
        self.takeItem(self.row(item))
        self._update_overlay()

    def remove_selected_items(self):
        rows = sorted((self.row(item) for item in self.selectedItems()), reverse=True)
        for row in rows:
            self.takeItem(row)
        self._update_overlay()

    def clear(self):
        super().clear()
        self._overlay.hide()
        self._drop_indicator.hide()

    def dragEnterEvent(self, event):
        if self._event_pdf_files(event):
            event.acceptProposedAction()
            return
        if self._browse_mode and self._is_internal_item_drag(event):
            self._show_drop_indicator(event)
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._event_pdf_files(event):
            event.acceptProposedAction()
            return
        if self._browse_mode and self._is_internal_item_drag(event):
            self._show_drop_indicator(event)
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        files = self._event_pdf_files(event)
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()
            return
        if self._browse_mode and self._is_internal_item_drag(event):
            self._move_selected_to_drop_position(event)
            self._drop_indicator.hide()
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dropEvent(event)
        self._drop_indicator.hide()
        self._update_overlay()

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction

    def dragLeaveEvent(self, event):
        self._drop_indicator.hide()
        super().dragLeaveEvent(event)

    def startDrag(self, supported_actions):
        if not self._browse_mode or not self.selectedItems():
            super().startDrag(supported_actions)
            return

        self._overlay.hide()
        self._drop_indicator.hide()

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self.INTERNAL_REORDER_MIME, b"1")
        drag.setMimeData(mime)

        current = self.currentItem() or self.selectedItems()[0]
        icon = current.icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(QSize(120, 90)))

        drag.exec(Qt.MoveAction, Qt.MoveAction)
        self._drop_indicator.hide()
        self._update_overlay()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._drop_indicator.hide()
        self._update_overlay()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._drop_indicator.hide()
        self._update_overlay()

    def _refresh_item(self, item):
        path = item.data(Qt.UserRole)
        name = Path(path).name if path else ""
        if self._browse_mode:
            item.setText(name)
            item.setIcon(QIcon(self.render_pdf_thumbnail(path, self.THUMB_SIZE)))
            item.setTextAlignment(Qt.AlignHCenter)
            item.setSizeHint(self.GRID_SIZE)
        else:
            item.setText(path or name)
            item.setIcon(QIcon())
            item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            item.setSizeHint(QSize())

    def _preview_current_item(self):
        item = self.currentItem()
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if path:
            self.preview_requested.emit(path)

    def _preview_double_clicked_item(self, item):
        self.setCurrentItem(item)
        self._preview_current_item()

    def _expand_current_item(self):
        item = self.currentItem()
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if path:
            self.expand_requested.emit(path)

    def _is_internal_item_drag(self, event):
        if not self.selectedItems():
            return False
        source = event.source() if hasattr(event, "source") else None
        if source is self:
            return True
        mime = event.mimeData()
        return bool(mime and mime.hasFormat(self.INTERNAL_REORDER_MIME))

    def _move_selected_to_drop_position(self, event):
        rows = sorted({self.row(item) for item in self.selectedItems()})
        if not rows:
            return

        target_row = self._drop_target_row(event)
        moving = []
        current_path = self.currentItem().data(Qt.UserRole) if self.currentItem() else None
        for row in reversed(rows):
            moving.insert(0, self.takeItem(row))
            if row < target_row:
                target_row -= 1

        target_row = max(0, min(target_row, self.count()))
        for offset, item in enumerate(moving):
            self.insertItem(target_row + offset, item)
            item.setSelected(True)

        if current_path:
            for row in range(self.count()):
                item = self.item(row)
                if item.data(Qt.UserRole) == current_path:
                    self.setCurrentItem(item)
                    break
        else:
            self.setCurrentItem(moving[0])
        self._update_overlay()

    def _drop_target_row(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        for row in range(self.count()):
            rect = self.visualItemRect(self.item(row))
            if not rect.isValid():
                continue
            if pos.y() < rect.top():
                return row
            if rect.top() <= pos.y() <= rect.bottom() and pos.x() < rect.center().x():
                return row
        return self.count()

    def _show_drop_indicator(self, event):
        geometry = self._drop_indicator_geometry(self._drop_target_row(event))
        if geometry is None:
            self._drop_indicator.hide()
            return
        self._drop_indicator.setGeometry(*geometry)
        self._drop_indicator.raise_()
        self._drop_indicator.show()

    def _drop_indicator_geometry(self, target_row):
        if self.count() == 0:
            return None

        line_width = 8
        if target_row < self.count():
            rect = self.visualItemRect(self.item(target_row))
            if not rect.isValid():
                return None
            x = max(0, rect.left() - line_width - 3)
        else:
            rect = self.visualItemRect(self.item(self.count() - 1))
            if not rect.isValid():
                return None
            x = min(self.viewport().width() - line_width, rect.right() + 10)

        y = rect.top() + 10
        height = max(74, rect.height() - 20)
        return x, y, line_width, height

    def _update_overlay(self):
        if not self._browse_mode or self.currentItem() is None:
            self._overlay.hide()
            return
        rect = self.visualItemRect(self.currentItem())
        if not rect.isValid() or rect.bottom() < 0 or rect.top() > self.viewport().height():
            self._overlay.hide()
            return
        page_count = self.currentItem().data(Qt.UserRole + 1) or 1
        show_expand = page_count > 1
        self._expand_btn.setVisible(show_expand)
        self._overlay.setFixedSize(58, 138 if show_expand else 92)
        x = max(rect.left() + 6, rect.right() - self._overlay.width() - 8)
        y = rect.top() + 8
        self._overlay.move(x, y)
        self._overlay.raise_()
        self._overlay.show()

    @staticmethod
    def pdf_page_count(path):
        try:
            fitz = _import_fitz()
            with fitz.open(str(path)) as doc:
                return doc.page_count
        except Exception:
            return 1

    @staticmethod
    def render_pdf_thumbnail(path, target_size, page_index=0):
        pixmap = QPixmap(target_size)
        pixmap.fill(QColor("#f8fafc"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#c5ced8"))
        painter.setBrush(QColor("#ffffff"))
        page_rect = pixmap.rect().adjusted(12, 8, -12, -8)
        painter.drawRect(page_rect)
        painter.setPen(QColor("#8a98a8"))
        painter.drawText(page_rect, Qt.AlignCenter, "PDF")
        painter.end()

        try:
            fitz = _import_fitz()
            with fitz.open(str(path)) as doc:
                if doc.page_count < 1:
                    return pixmap
                page_index = max(0, min(page_index, doc.page_count - 1))
                page = doc.load_page(page_index)
                rect = page.rect
                scale = min(
                    target_size.width() / max(rect.width, 1),
                    target_size.height() / max(rect.height, 1),
                    3,
                )
                rendered = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                fmt = QImage.Format_RGB888 if rendered.n < 4 else QImage.Format_RGBA8888
                image = QImage(rendered.samples, rendered.width, rendered.height, rendered.stride, fmt).copy()
                page_pixmap = QPixmap.fromImage(image).scaled(
                    target_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
        except Exception:
            return pixmap

        pixmap.fill(QColor("#f8fafc"))
        painter = QPainter(pixmap)
        x = (target_size.width() - page_pixmap.width()) // 2
        y = (target_size.height() - page_pixmap.height()) // 2
        painter.drawPixmap(x, y, page_pixmap)
        painter.setPen(QColor("#d0d7de"))
        painter.drawRect(x, y, page_pixmap.width() - 1, page_pixmap.height() - 1)
        painter.end()
        return pixmap


class NoNativeSelectionDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        clean = QStyleOptionViewItem(option)
        clean.state &= ~QStyle.State_Selected
        clean.state &= ~QStyle.State_HasFocus
        clean.state &= ~QStyle.State_MouseOver
        super().paint(painter, clean, index)


class PdfPagePreviewListWidget(QListWidget):
    preview_page_requested = Signal(str, int)

    PAGE_SIZE = QSize(170, 220)
    TILE_SIZE = QSize(190, 258)
    GRID_SIZE = QSize(210, 280)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pdf_path = ""
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setWrapping(True)
        self.setIconSize(self.TILE_SIZE)
        self.setGridSize(self.GRID_SIZE)
        self.setSpacing(18)
        self.setWordWrap(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setItemDelegate(NoNativeSelectionDelegate(self))
        self.setStyleSheet(
            """
            QListWidget {
                background:#eeeeee;
                border:1px solid #dfe6e9;
                padding:18px;
                font-size:13px;
            }
            QListWidget::item {
                background:transparent;
                border:none;
                color:#2d3436;
            }
            QListWidget::item:selected {
                background:transparent;
                border:none;
                color:#2d3436;
            }
            QListWidget::item:selected:active,
            QListWidget::item:selected:!active,
            QListWidget::item:hover,
            QListWidget::item:focus {
                background:transparent;
                border:none;
                outline:none;
            }
            """
        )

        self._overlay = QFrame(self.viewport())
        self._overlay.setObjectName("pdfPageOverlay")
        self._overlay.setFixedSize(58, 48)
        self._overlay.setStyleSheet(
            """
            QFrame#pdfPageOverlay {
                background:#ffffff;
                border:1px solid #a9b4bf;
                border-radius:4px;
            }
            QToolButton {
                border:none;
                padding:4px;
                color:#4b5563;
                font-size:12px;
            }
            QToolButton:hover { background:#edf5ff; color:#0984e3; }
            """
        )
        overlay_layout = QVBoxLayout(self._overlay)
        overlay_layout.setContentsMargins(4, 4, 4, 4)
        self._preview_btn = QToolButton(self._overlay)
        self._preview_btn.setToolTip("放大预览")
        self._preview_btn.setText("放大")
        self._preview_btn.setIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView))
        self._preview_btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        overlay_layout.addWidget(self._preview_btn)
        self._overlay.hide()

        self.itemSelectionChanged.connect(self._update_overlay)
        self.itemDoubleClicked.connect(self._preview_double_clicked_page)
        self._preview_btn.clicked.connect(self._preview_current_page)

    def load_pdf(self, path):
        self._pdf_path = str(path)
        self.clear()
        page_count = PdfMergeListWidget.pdf_page_count(path)
        for page_index in range(page_count):
            item = QListWidgetItem("")
            item.setData(Qt.UserRole, page_index)
            item.setIcon(QIcon(self._render_page_tile(path, page_index)))
            item.setTextAlignment(Qt.AlignHCenter)
            item.setSizeHint(self.GRID_SIZE)
            item.setToolTip(f"第 {page_index + 1} 页")
            self.addItem(item)
        self._overlay.hide()

    def clear(self):
        super().clear()
        if hasattr(self, "_overlay"):
            self._overlay.hide()

    def paintEvent(self, event):
        super().paintEvent(event)
        self._paint_selected_frame()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_overlay()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._update_overlay()

    def _paint_selected_frame(self):
        item = self.currentItem()
        if item is None:
            return
        rect = self.visualItemRect(item)
        if not rect.isValid() or rect.bottom() < 0 or rect.top() > self.viewport().height():
            return
        frame_width = min(self.TILE_SIZE.width() + 8, rect.width() - 6)
        frame_height = min(self.TILE_SIZE.height() + 8, rect.height() - 6)
        frame_left = rect.left() + (rect.width() - frame_width) // 2
        frame = QRect(frame_left, rect.top() + 3, frame_width, frame_height)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#0984e3"))
        painter.setBrush(QColor(219, 234, 254, 90))
        painter.drawRoundedRect(frame, 4, 4)
        painter.end()

    def _render_page_tile(self, path, page_index):
        pixmap = QPixmap(self.TILE_SIZE)
        pixmap.fill(QColor("#eeeeee"))

        page_pixmap = PdfMergeListWidget.render_pdf_thumbnail(path, self.PAGE_SIZE, page_index)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        x = (self.TILE_SIZE.width() - page_pixmap.width()) // 2
        painter.drawPixmap(x, 0, page_pixmap)
        painter.setPen(QColor("#2d3436"))
        painter.drawText(
            0,
            self.PAGE_SIZE.height() + 8,
            self.TILE_SIZE.width(),
            self.TILE_SIZE.height() - self.PAGE_SIZE.height() - 8,
            Qt.AlignHCenter | Qt.AlignTop,
            str(page_index + 1),
        )
        painter.end()
        return pixmap

    def _preview_current_page(self):
        item = self.currentItem()
        if item is None or not self._pdf_path:
            return
        self.preview_page_requested.emit(self._pdf_path, item.data(Qt.UserRole))

    def _preview_double_clicked_page(self, item):
        self.setCurrentItem(item)
        self._preview_current_page()

    def _update_overlay(self):
        item = self.currentItem()
        if item is None:
            self._overlay.hide()
            self.viewport().update()
            return
        rect = self.visualItemRect(item)
        if not rect.isValid() or rect.bottom() < 0 or rect.top() > self.viewport().height():
            self._overlay.hide()
            self.viewport().update()
            return
        x = max(rect.left() + 6, rect.right() - self._overlay.width() - 8)
        y = rect.top() + 8
        self._overlay.move(x, y)
        self._overlay.raise_()
        self._overlay.show()
        self.viewport().update()


class PdfDropLineEdit(QLineEdit):
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setProperty("dropInput", True)
        self.setPlaceholderText("支持拖拽 PDF 到此处")
        self.setToolTip("可拖拽 PDF 文件到这里")

    def dragEnterEvent(self, event):
        if self._event_first_pdf(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._event_first_pdf(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        path = self._event_first_pdf(event)
        if not path:
            event.ignore()
            return
        self.file_dropped.emit(path)
        event.acceptProposedAction()

    @staticmethod
    def _event_first_pdf(event):
        mime = event.mimeData()
        if not mime.hasUrls():
            return ""
        for url in mime.urls():
            path = url.toLocalFile()
            if path and path.lower().endswith(".pdf") and Path(path).is_file():
                return path
        return ""


class PDFToolBox(QWidget):
    def __init__(self):
        super().__init__()
        self._thread = None
        self._worker = None

        self.setWindowTitle("PDF 工具集")
        self.resize(980, 720)
        self.setStyleSheet("""
            PDFToolBox { background:#f5f6fa; }
            QGroupBox { font-weight:bold; color:#2d3436; border:none; margin-top:14px; padding:14px 0 4px 0; }
            QGroupBox::title { padding:0 0 6px 0; border-bottom:2px solid #0984e3; }
            QLineEdit { border:1px solid #dfe6e9; border-radius:4px; padding:7px 8px; background:white; font-size:13px; }
            QLineEdit[dropInput="true"] { border:1px dashed #9bb7d4; background:#fbfdff; }
            QListWidget, QTextEdit { border:1px solid #dfe6e9; border-radius:4px; background:white; font-size:13px; }
            QListWidget { padding:6px; }
            QTabWidget::pane { border:1px solid #dfe6e9; border-radius:4px; background:white; }
            QTabBar::tab { padding:9px 22px; font-size:13px; border:none; }
            QTabBar::tab:selected { border-bottom:2px solid #0984e3; color:#0984e3; font-weight:bold; }
            QSpinBox {
                border:1px solid #dfe6e9;
                border-radius:4px;
                padding:3px 26px 3px 8px;
                background:white;
                min-width:82px;
                min-height:30px;
                font-size:13px;
            }
            QSpinBox::up-button {
                subcontrol-origin:border;
                subcontrol-position:top right;
                width:24px;
                height:16px;
                border-left:1px solid #dfe6e9;
                border-bottom:1px solid #edf2f5;
                border-top-right-radius:4px;
                background:#f8fafc;
            }
            QSpinBox::down-button {
                subcontrol-origin:border;
                subcontrol-position:bottom right;
                width:24px;
                height:16px;
                border-left:1px solid #dfe6e9;
                border-bottom-right-radius:4px;
                background:#f8fafc;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover { background:#edf5ff; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        title = QLabel("PDF 工具集")
        title.setStyleSheet("font-size:18px;font-weight:bold;color:#2d3436;")
        root.addWidget(title)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self._build_merge_tab()
        self._build_split_tab()
        self._build_nup_tab()

        log_group = QGroupBox("处理日志")
        log_layout = QVBoxLayout(log_group)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(130)
        log_layout.addWidget(self.log_box)
        root.addWidget(log_group)

    def _btn_style(self, color="#0984e3"):
        hover = {
            "#0984e3": "#0873c4",
            "#27ae60": "#219a52",
            "#636e72": "#535c69",
            "#d63031": "#c0392b",
        }.get(color, color)
        return (
            f"QPushButton{{background:{color};color:white;padding:8px 20px;"
            "border:none;border-radius:4px;font-size:13px;}}"
            f"QPushButton:hover{{background:{hover};}}"
            "QPushButton:disabled{background:#b2bec3;color:white;}"
        )

    def _secondary_btn(self):
        return (
            "QPushButton{background:white;color:#2d3436;padding:7px 16px;"
            "border:1px solid #d0d0d0;border-radius:4px;font-size:13px;}"
            "QPushButton:hover{background:#f0f2f5;}"
        )

    def _path_row(self, label, line_edit, browse_func):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addWidget(line_edit, 1)
        btn = QPushButton("浏览")
        btn.setStyleSheet(self._secondary_btn())
        btn.clicked.connect(browse_func)
        row.addWidget(btn)
        return row

    def _setup_spinbox(self, spinbox):
        spinbox.setButtonSymbols(QSpinBox.UpDownArrows)
        spinbox.setFixedSize(92, 34)
        spinbox.setKeyboardTracking(False)

    def _build_merge_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        file_group = QGroupBox("输入 PDF 文件")
        file_layout = QVBoxLayout(file_group)
        self.merge_list = PdfMergeListWidget()
        self.merge_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.merge_list.setToolTip("可拖拽多个 PDF 文件到这里")
        file_layout.addWidget(self.merge_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("添加 PDF")
        remove_btn = QPushButton("移除选中")
        clear_btn = QPushButton("清空")
        up_btn = QPushButton("上移")
        down_btn = QPushButton("下移")
        self.merge_browse_btn = QPushButton("浏览")
        self.merge_list_btn = QPushButton("列表")
        for btn in (self.merge_browse_btn, self.merge_list_btn):
            btn.setCheckable(True)
            btn.setStyleSheet(self._secondary_btn())
        for btn in (add_btn, remove_btn, clear_btn, up_btn, down_btn):
            btn.setStyleSheet(self._secondary_btn())
            btn_row.addWidget(btn)
        btn_row.addStretch()
        btn_row.addWidget(QLabel("显示模式"))
        btn_row.addWidget(self.merge_browse_btn)
        btn_row.addWidget(self.merge_list_btn)
        file_layout.addLayout(btn_row)
        layout.addWidget(file_group, 1)

        self.merge_output = QLineEdit()
        layout.addLayout(self._path_row("输出文件", self.merge_output, self._choose_merge_output))

        run = QPushButton("开始合并")
        run.setStyleSheet(self._btn_style("#0984e3"))
        layout.addWidget(run, alignment=Qt.AlignRight)

        add_btn.clicked.connect(self._add_merge_files)
        remove_btn.clicked.connect(self.merge_list.remove_selected_items)
        clear_btn.clicked.connect(self.merge_list.clear)
        up_btn.clicked.connect(lambda: self._move_selected(self.merge_list, -1))
        down_btn.clicked.connect(lambda: self._move_selected(self.merge_list, 1))
        self.merge_list.files_dropped.connect(self._handle_merge_files)
        self.merge_list.preview_requested.connect(self._preview_merge_pdf)
        self.merge_list.expand_requested.connect(self._expand_merge_pdf)
        self.merge_browse_btn.clicked.connect(lambda: self._set_merge_browse_mode(True))
        self.merge_list_btn.clicked.connect(lambda: self._set_merge_browse_mode(False))
        self._set_merge_browse_mode(True)
        run.clicked.connect(self._run_merge)
        self.tabs.addTab(tab, "PDF 合并")

    def _build_split_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self.split_input = PdfDropLineEdit()
        self.split_output_dir = QLineEdit()
        self.split_pages = QSpinBox()
        self.split_pages.setRange(1, 9999)
        self.split_pages.setValue(1)
        self._setup_spinbox(self.split_pages)
        self.split_prefix = QLineEdit()

        layout.addLayout(self._path_row("输入文件", self.split_input, self._choose_split_input))
        layout.addLayout(self._path_row("输出目录", self.split_output_dir, self._choose_split_dir))

        opt = QHBoxLayout()
        opt.addWidget(QLabel("每个 PDF 页数"))
        opt.addWidget(self.split_pages)
        opt.addSpacing(20)
        opt.addWidget(QLabel("文件名前缀"))
        opt.addWidget(self.split_prefix, 1)
        layout.addLayout(opt)
        layout.addStretch()

        run = QPushButton("开始拆分")
        run.setStyleSheet(self._btn_style("#27ae60"))
        layout.addWidget(run, alignment=Qt.AlignRight)

        self.split_input.file_dropped.connect(self._set_split_input)
        run.clicked.connect(self._run_split)
        self.tabs.addTab(tab, "PDF 拆分")

    def _build_nup_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self.nup_input = PdfDropLineEdit()
        self.nup_output = QLineEdit()
        self.nup_pages = QSpinBox()
        self.nup_pages.setRange(1, 64)
        self.nup_pages.setValue(4)
        self.nup_rows = QSpinBox()
        self.nup_rows.setRange(1, 64)
        self.nup_rows.setValue(2)
        self.nup_margin = QSpinBox()
        self.nup_margin.setRange(0, 50)
        self.nup_margin.setValue(5)
        for spin in (self.nup_pages, self.nup_rows, self.nup_margin):
            self._setup_spinbox(spin)
        self.nup_preview = QLabel()
        self.nup_preview.setStyleSheet("color:#636e72;font-size:13px;")

        layout.addLayout(self._path_row("输入文件", self.nup_input, self._choose_nup_input))
        layout.addLayout(self._path_row("输出文件", self.nup_output, self._choose_nup_output))

        opt = QHBoxLayout()
        opt.addWidget(QLabel("每张 A4 页数"))
        opt.addWidget(self.nup_pages)
        opt.addSpacing(20)
        opt.addWidget(QLabel("行数"))
        opt.addWidget(self.nup_rows)
        opt.addSpacing(20)
        opt.addWidget(QLabel("边距"))
        opt.addWidget(self.nup_margin)
        opt.addWidget(QLabel("pt"))
        opt.addStretch()
        layout.addLayout(opt)
        layout.addWidget(self.nup_preview)
        layout.addStretch()

        run = QPushButton("开始拼版")
        run.setStyleSheet(self._btn_style("#0984e3"))
        layout.addWidget(run, alignment=Qt.AlignRight)

        self.nup_pages.valueChanged.connect(self._refresh_nup_preview)
        self.nup_rows.valueChanged.connect(self._refresh_nup_preview)
        self.nup_input.file_dropped.connect(self._set_nup_input)
        self._refresh_nup_preview()
        run.clicked.connect(self._run_nup)
        self.tabs.addTab(tab, "A4 拼版")

    def _append_log(self, msg):
        self.log_box.append(msg)

    def _set_merge_browse_mode(self, enabled):
        self.merge_list.set_browse_mode(enabled)
        self.merge_browse_btn.setChecked(enabled)
        self.merge_list_btn.setChecked(not enabled)

    def _preview_merge_pdf(self, path, page_index=0):
        dialog = QDialog(self)
        suffix = f" - 第 {page_index + 1} 页" if page_index else ""
        dialog.setWindowTitle(Path(path).name + suffix)
        dialog.resize(860, 720)

        layout = QVBoxLayout(dialog)
        title = QLabel(f"{path}    第 {page_index + 1} 页")
        title.setStyleSheet("font-size:13px;color:#2d3436;")
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        preview = QLabel()
        preview.setAlignment(Qt.AlignCenter)
        preview.setStyleSheet("background:#f8fafc;border:1px solid #dfe6e9;")
        preview.setPixmap(PdfMergeListWidget.render_pdf_thumbnail(path, QSize(760, 980), page_index))
        scroll.setWidget(preview)
        layout.addWidget(scroll, 1)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(self._secondary_btn())
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)
        dialog.exec()

    def _expand_merge_pdf(self, path):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{Path(path).name} - 展开预览")
        dialog.resize(980, 720)

        layout = QVBoxLayout(dialog)
        title = QLabel(str(path))
        title.setStyleSheet("font-size:13px;color:#2d3436;")
        layout.addWidget(title)

        page_list = PdfPagePreviewListWidget()
        page_list.load_pdf(path)
        page_list.preview_page_requested.connect(self._preview_merge_pdf)
        layout.addWidget(page_list, 1)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(self._secondary_btn())
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignRight)
        dialog.exec()

    def _add_merge_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择 PDF 文件", "", "PDF Files (*.pdf)")
        self._handle_merge_files(files)

    def _handle_merge_files(self, files):
        files = [str(Path(file)) for file in files if str(file).strip() and str(file).lower().endswith(".pdf")]
        if not files:
            return
        added = 0
        for file in files:
            if not self.merge_list.contains_path(file):
                self.merge_list.add_pdf(file)
                added += 1
        if len(files) > 1:
            self._set_merge_browse_mode(True)
        if not self.merge_output.text().strip():
            first = Path(files[0])
            self.merge_output.setText(str(first.with_name(first.stem + "_合并.pdf")))
        if added:
            self._append_log(f"已添加 {added} 个 PDF 文件")

    def _choose_merge_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", self.merge_output.text(), "PDF Files (*.pdf)")
        if path:
            self.merge_output.setText(self._ensure_pdf_suffix(path))

    def _choose_split_input(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF 文件", "", "PDF Files (*.pdf)")
        if path:
            self._set_split_input(path)

    def _set_split_input(self, path):
        src = Path(path)
        self.split_input.setText(str(src))
        if not self.split_output_dir.text().strip():
            self.split_output_dir.setText(str(src.with_name(src.stem + "_拆分")))
        if not self.split_prefix.text().strip():
            self.split_prefix.setText(src.stem)
        self._append_log(f"已选择拆分输入：{src}")

    def _choose_split_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", self.split_output_dir.text())
        if path:
            self.split_output_dir.setText(path)

    def _choose_nup_input(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF 文件", "", "PDF Files (*.pdf)")
        if path:
            self._set_nup_input(path)

    def _set_nup_input(self, path):
        src = Path(path)
        self.nup_input.setText(str(src))
        if not self.nup_output.text().strip():
            pages = self.nup_pages.value()
            rows = self.nup_rows.value()
            self.nup_output.setText(str(src.with_name(f"{src.stem}_A4横向{pages}合1_{rows}行.pdf")))
        self._append_log(f"已选择拼版输入：{src}")

    def _choose_nup_output(self):
        path, _ = QFileDialog.getSaveFileName(self, "选择输出文件", self.nup_output.text(), "PDF Files (*.pdf)")
        if path:
            self.nup_output.setText(self._ensure_pdf_suffix(path))

    def _run_merge(self):
        files = self.merge_list.file_paths()
        output = self.merge_output.text().strip()
        if not files or not output:
            QMessageBox.warning(self, "提示", "请选择输入 PDF 并设置输出文件。")
            return
        self._start_worker("开始合并 PDF...", merge_pdfs, files, output)

    def _run_split(self):
        src = self.split_input.text().strip()
        out_dir = self.split_output_dir.text().strip()
        if not src or not out_dir:
            QMessageBox.warning(self, "提示", "请选择输入 PDF 和输出目录。")
            return
        self._start_worker(
            "开始拆分 PDF...",
            split_pdf,
            src,
            out_dir,
            self.split_pages.value(),
            self.split_prefix.text().strip() or None,
        )

    def _run_nup(self):
        src = self.nup_input.text().strip()
        output = self.nup_output.text().strip()
        pages = self.nup_pages.value()
        rows = self.nup_rows.value()
        if rows > pages:
            QMessageBox.warning(self, "提示", "行数不能大于每张 A4 页数。")
            return
        if not src or not output:
            QMessageBox.warning(self, "提示", "请选择输入 PDF 并设置输出文件。")
            return
        self._start_worker(
            "开始 A4 拼版...",
            nup_pdf_to_a4,
            src,
            output,
            pages,
            rows,
            True,
            float(self.nup_margin.value()),
        )

    def _start_worker(self, start_msg, func, *args, **kwargs):
        if self._thread and self._thread.isRunning():
            QMessageBox.information(self, "提示", "当前已有任务正在执行，请稍候。")
            return
        self._append_log(start_msg)
        self._thread = QThread(self)
        self._worker = PdfWorker(func, *args, **kwargs)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._cleanup_worker)
        self._thread.start()

    def _on_worker_finished(self, result):
        if isinstance(result, list):
            self._append_log(f"处理完成，共生成 {len(result)} 个文件。")
            if result:
                self._append_log(f"输出目录：{result[0].parent}")
        else:
            self._append_log(f"处理完成：{result}")
        QMessageBox.information(self, "完成", "PDF 处理完成。")

    def _on_worker_failed(self, msg):
        self._append_log(f"处理失败：{msg}")
        QMessageBox.critical(self, "处理失败", msg)

    def _cleanup_worker(self):
        self._thread = None
        self._worker = None

    def _refresh_nup_preview(self):
        pages = self.nup_pages.value()
        rows = min(self.nup_rows.value(), pages)
        cols = math.ceil(pages / rows)
        self.nup_preview.setText(
            f"当前参数：每张 A4 横版拼接 {pages} 页，按 {rows} 行 x {cols} 列排列；最后一张自动放置剩余页面。"
        )

    @staticmethod
    def _ensure_pdf_suffix(path):
        return path if path.lower().endswith(".pdf") else path + ".pdf"

    @staticmethod
    def _list_contains(list_widget, text):
        for i in range(list_widget.count()):
            if list_widget.item(i).text() == text:
                return True
        return False

    @staticmethod
    def _remove_selected(list_widget):
        for item in list_widget.selectedItems():
            list_widget.takeItem(list_widget.row(item))

    @staticmethod
    def _move_selected(list_widget, delta):
        row = list_widget.currentRow()
        if row < 0:
            return
        new_row = row + delta
        if new_row < 0 or new_row >= list_widget.count():
            return
        item = list_widget.takeItem(row)
        list_widget.insertItem(new_row, item)
        list_widget.setCurrentRow(new_row)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = PDFToolBox()
    w.show()
    sys.exit(app.exec())
