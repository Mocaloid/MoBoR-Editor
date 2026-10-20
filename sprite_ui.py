# -*- coding: utf-8 -*-
"""索引图（Sprite）编辑：画布、轨道区、工程区与工具区、导入对话框。

（由 canvas.py、timeline.py、panels.py、dialogs.py 合并而来）"""

import math
import os

from PyQt5.QtCore import (QMimeData, QPointF, QRect, QRectF, QSizeF,
                      QTimer, QUrl, Qt)
from PyQt5.QtGui import (QBrush, QColor, QCursor, QDrag, QIntValidator, QKeySequence, QPainter, QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import (QAbstractItemView, QButtonGroup, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollBar, QShortcut, QSlider, QSpinBox, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

# 本项目的其它模块
from core import clamp, iu



# ====================================================================
# 原 canvas.py
# ====================================================================

MIN_SCALE, MAX_SCALE = 0.02, 64.0
MAX_PIXEL = 30000  # 最大像素显示尺寸（缩放上限依据）


def make_pencil_cursor():
    """自绘铅笔光标：笔尖朝向左下角，热点即笔尖。"""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.translate(6, 26)        # 笔尖位置
    p.rotate(-45)             # +x 轴指向右上（笔身方向）
    p.setPen(QPen(QColor(40, 40, 40), 1))
    p.setBrush(QColor(240, 200, 80))
    p.drawRect(7, -4, 16, 8)                       # 笔身（笔尖在 x<7 一侧）
    p.setBrush(QColor(255, 190, 190))
    p.drawRect(23, -4, 4, 8)                       # 橡皮
    p.setBrush(QColor(230, 220, 200))
    p.drawPolygon(QPolygonF([QPointF(7, -4), QPointF(0, 0), QPointF(7, 4)]))
    p.setBrush(QColor(40, 40, 40))
    p.drawPolygon(QPolygonF([QPointF(3.5, -1.5), QPointF(0, 0),
                             QPointF(3.5, 1.5)]))  # 铅芯（尖端在原点）
    p.end()
    return QCursor(pm, 6, 26)


class CanvasWidget(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(220)
        self.scale = 1.0
        self.pan = QPointF(0, 0)   # 视口左上角对应的场景坐标
        self._drag = None
        self._checker = None
        self._handles = []
        self._sel_frame = None
        # 右上角“吸色”按钮（一次性）
        self.btn_pick = QPushButton("吸色", self)
        self.btn_pick.setCheckable(True)
        self.btn_pick.setFixedSize(56, 24)
        self.btn_pick.setToolTip("在画布上吸取一个像素的颜色")
        self.btn_pick.toggled.connect(self.mw.set_picker_mode)

    def project(self):
        return self.mw.project

    def to_scene(self, p):
        return QPointF(p.x() / self.scale + self.pan.x(),
                       p.y() / self.scale + self.pan.y())

    def to_screen(self, p):
        return QPointF((p.x() - self.pan.x()) * self.scale,
                       (p.y() - self.pan.y()) * self.scale)

    def aligned_base(self, w, h):
        prj = self.project()
        if prj.align_mode == "topleft":
            return QPointF(0, 0)
        if prj.align_mode == "fixed":
            return QPointF(prj.fixed_point[0], prj.fixed_point[1])
        return QPointF((self.width() - w) / 2.0, (self.height() - h) / 2.0)

    def anchor_point(self):
        """对齐参考点（十字准星位置，场景坐标）。"""
        prj = self.project()
        if prj.align_mode == "topleft":
            return QPointF(0, 0)
        if prj.align_mode == "fixed":
            return QPointF(prj.fixed_point[0], prj.fixed_point[1])
        return QPointF(self.width() / 2.0, self.height() / 2.0)

    def visible_frames(self):
        """自下而上（绘制顺序）返回 (轨道号, 帧, 基点, (w,h))。"""
        prj = self.project()
        ph = prj.playhead
        out = []
        for ti in range(len(prj.tracks) - 1, -1, -1):
            t = prj.tracks[ti]
            if t.muted:
                continue
            f = t.frame_at(ph)
            if f is None:
                continue
            w, h = f.display_size()
            base = self.aligned_base(w, h) + QPointF(f.offset_x, f.offset_y)
            out.append((ti, f, base, (w, h)))
        return out

    def update_cursor(self):
        if self.mw.picker_mode:
            self.setCursor(Qt.CrossCursor)
        elif self.mw.pencil_mode:
            self.setCursor(make_pencil_cursor())
        else:
            self.unsetCursor()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.btn_pick.move(self.width() - self.btn_pick.width() - 6, 6)

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        p = QPainter(self)
        if self._checker is None:
            self._checker = QPixmap(16, 16)
            self._checker.fill(QColor(96, 96, 96))
            qp = QPainter(self._checker)
            qp.fillRect(0, 0, 8, 8, QColor(72, 72, 72))
            qp.fillRect(8, 8, 8, 8, QColor(72, 72, 72))
            qp.end()
        p.fillRect(self.rect(), QBrush(self._checker))
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)

        # 对齐参考区（“舞台”）
        p.setPen(QPen(QColor(140, 140, 140, 130), 1, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        tl = self.to_screen(QPointF(0, 0))
        p.drawRect(QRectF(tl, QSizeF(self.width() * self.scale,
                                     self.height() * self.scale)))

        # 颜色隔离状态
        iso = None
        if self.mw.color_isolation:
            pal = self.mw.displayed_palette()
            idx = self.mw.tool_panel.grid.selected_index
            if pal is not None and idx is not None:
                iso = pal.colors[idx]

        prj = self.project()
        primary = self.mw.primary_frame()
        self._handles = []
        self._sel_frame = None
        sel_rect = None
        for ti, f, base, (w, h) in self.visible_frames():
            qimg = (self.mw.cache.get_iso(f, prj, iso) if iso
                    else self.mw.cache.get(f, prj))
            sp = self.to_screen(base)
            rect = QRectF(sp, QSizeF(w * self.scale, h * self.scale))
            if rect.width() > 0 and rect.height() > 0:
                p.drawImage(rect, qimg)
            p.setPen(QPen(QColor(230, 40, 40), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(rect)
            if primary and primary[0] == ti and primary[1] == prj.playhead:
                sel_rect = rect
                self._sel_frame = (ti, prj.playhead, f)

        if sel_rect is not None and not self.mw.pencil_mode:
            p.setPen(QPen(QColor(255, 255, 255), 1, Qt.DashLine))
            p.drawRect(sel_rect)
            l, r = sel_rect.left(), sel_rect.right()
            t_, b = sel_rect.top(), sel_rect.bottom()
            mx, my = (l + r) / 2, (t_ + b) / 2
            pts = [QPointF(l, t_), QPointF(mx, t_), QPointF(r, t_), QPointF(r, my),
                   QPointF(r, b), QPointF(mx, b), QPointF(l, b), QPointF(l, my)]
            for i, pt in enumerate(pts):
                p.setPen(QPen(QColor(20, 20, 20), 1))
                p.setBrush(QColor(255, 255, 255))
                p.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))
                self._handles.append((i, pt))

        # 对齐参考点：十字准星
        sp = self.to_screen(self.anchor_point())
        x, y = sp.x(), sp.y()
        p.setPen(QPen(QColor(0, 220, 220), 1))
        g, L = 3, 14
        p.drawLine(QPointF(x - L, y), QPointF(x - g, y))
        p.drawLine(QPointF(x + g, y), QPointF(x + L, y))
        p.drawLine(QPointF(x, y - L), QPointF(x, y - g))
        p.drawLine(QPointF(x, y + g), QPointF(x, y + L))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(sp, 5, 5)

    # ---------- 缩放 ----------
    def max_scale(self):
        m = 1
        for _, _, _, (w, h) in self.visible_frames():
            m = max(m, w, h)
        return min(MAX_SCALE, max(1.0, MAX_PIXEL / m))

    def set_scale(self, s, anchor=None):
        s = clamp(s, MIN_SCALE, self.max_scale())
        if abs(s - self.scale) < 1e-9:
            return
        if anchor is not None:
            sp = self.to_scene(anchor)
            self.scale = s
            self.pan = QPointF(sp.x() - anchor.x() / s, sp.y() - anchor.y() / s)
        else:
            self.scale = s
        self.update()
        self.mw.sync_zoom_slider()

    # ---------- 吸色 ----------
    def _pick(self, pos):
        prj = self.project()
        for ti, f, base, (w, h) in reversed(self.visible_frames()):
            rect = QRectF(self.to_screen(base),
                          QSizeF(w * self.scale, h * self.scale))
            if not rect.contains(QPointF(pos)):
                continue
            lp = self.to_scene(pos) - base
            lx, ly = int(lp.x()), int(lp.y())
            qimg = self.mw.cache.get(f, prj)
            if 0 <= lx < qimg.width() and 0 <= ly < qimg.height():
                c = qimg.pixelColor(lx, ly)
                if c.alpha() > 10:
                    self.mw.apply_picked_color((c.red(), c.green(), c.blue()))
                    # Alt 临时吸色：吸完保持吸色状态，直到松开 Alt
                    if not self.mw._alt_picker:
                        self.mw.set_picker_mode(False)
                    return
        self.mw.statusBar().showMessage("这里没有可吸取的颜色（透明区域）", 2500)
        if not self.mw._alt_picker:
            self.mw.set_picker_mode(False)

    def frame_hit(self, pos):
        """点击位置是否落在某个可见帧图片范围内（画布空白点击判定）。"""
        for ti, f, base, (w, h) in reversed(self.visible_frames()):
            rect = QRectF(self.to_screen(base),
                          QSizeF(w * self.scale, h * self.scale))
            if rect.contains(QPointF(pos)):
                return True
        return False

    # ---------- 铅笔 ----------
    def paint_at(self, f, pos):
        for ti, ff, base, (w, h) in self.visible_frames():
            if ff is f:
                break
        else:
            return
        lp = self.to_scene(pos) - base
        lx, ly = int(math.floor(lp.x())), int(math.floor(lp.y()))
        if f.crop:                       # 显示坐标 -> 原图坐标
            lx += f.crop[0]
            ly += f.crop[1]
        self.mw.paint_to(f, lx, ly)

    # ---------- 鼠标 ----------
    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.LeftButton:
            return
        pos = e.pos()
        if self.mw.picker_mode:                      # 吸色模式
            self._pick(pos)
            return
        if self.mw.pencil_mode:                      # 铅笔模式
            for ti, f, base, (w, h) in reversed(self.visible_frames()):
                rect = QRectF(self.to_screen(base),
                              QSizeF(w * self.scale, h * self.scale))
                if rect.contains(QPointF(pos)):
                    ph = self.project().playhead
                    self.mw.set_current_track(ti)
                    self.mw.set_selection({(ti, ph)}, primary=(ti, ph))
                    if self.mw.begin_stroke(f):
                        self._drag = ("paint", f)
                        self.paint_at(f, pos)
                    return
            return
        for idx, pt in self._handles:                # 裁剪手柄优先
            if abs(pos.x() - pt.x()) <= 6 and abs(pos.y() - pt.y()) <= 6:
                self._drag = ("crop", idx, False)
                return
        for ti, f, base, (w, h) in reversed(self.visible_frames()):  # 命中图片
            rect = QRectF(self.to_screen(base), QSizeF(w * self.scale, h * self.scale))
            if rect.contains(QPointF(pos)):
                ph = self.project().playhead
                self.mw.set_current_track(ti)
                self.mw.set_selection({(ti, ph)}, primary=(ti, ph))
                self._drag = ("move", f, pos, False)
                return
        self._drag = ("pan", pos)

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        kind = self._drag[0]
        if kind == "pan":
            d = e.pos() - self._drag[1]
            self.pan -= QPointF(d.x() / self.scale, d.y() / self.scale)
            self._drag = ("pan", e.pos())
            self.update()
        elif kind == "paint":
            self.paint_at(self._drag[1], e.pos())
        elif kind == "move":
            _, f, last, pushed = self._drag
            if not pushed:
                self.mw.push_undo()
                pushed = True
            d = e.pos() - last
            f.offset_x += int(round(d.x() / self.scale))
            f.offset_y += int(round(d.y() / self.scale))
            self._drag = ("move", f, e.pos(), pushed)
            self.update()
        elif kind == "crop":
            _, hidx, pushed = self._drag
            if not self._sel_frame:
                return
            _, _, f = self._sel_frame
            if not pushed:
                self.mw.push_undo()
                pushed = True
            ow, oh = f.orig_size
            x, y, w, h = f.crop or (0, 0, ow, oh)
            base = self.aligned_base(w, h) + QPointF(f.offset_x, f.offset_y)
            lp = self.to_scene(e.pos()) - (base - QPointF(x, y))  # 原图像素坐标
            lx, ly = lp.x(), lp.y()
            right, bottom = x + w, y + h
            if hidx in (0, 1, 2):   # 上边
                ny = clamp(int(round(ly)), 0, bottom - 1)
                h, y = bottom - ny, ny
            if hidx in (4, 5, 6):   # 下边
                h = clamp(int(round(ly)) + 1, y + 1, oh) - y
            if hidx in (0, 6, 7):   # 左边
                nx = clamp(int(round(lx)), 0, right - 1)
                w, x = right - nx, nx
            if hidx in (2, 3, 4):   # 右边
                w = clamp(int(round(lx)) + 1, x + 1, ow) - x
            f.crop = (x, y, w, h)
            self._drag = ("crop", hidx, pushed)
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag:
            if self._drag[0] == "paint":
                self.mw.end_stroke()
            elif self._drag[0] in ("move", "crop") and self._drag[-1]:
                self.mw.refresh_all()   # 确认拖动/裁剪
        self._drag = None

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        dx = e.angleDelta().x()
        mods = e.modifiers()
        if dx:                                   # 横向滚轮（倾斜滚轮）
            self.pan += QPointF(40 * dx / 120.0, 0)
            self.update()
            if not d:
                return
        if not d:
            return
        if mods & Qt.ControlModifier:            # 缩放画布
            self.set_scale(self.scale * 1.25 ** (d / 120.0), anchor=e.pos())
        elif mods & Qt.ShiftModifier:            # 代替左右方向键
            self.pan += QPointF(-40 if d > 0 else 40, 0)
            self.update()
        else:                                    # 代替上下方向键
            self.pan += QPointF(0, -40 if d > 0 else 40)
            self.update()

    def keyPressEvent(self, e):
        k = e.key()
        step = 30
        if k == Qt.Key_Left:
            self.pan -= QPointF(step, 0)
        elif k == Qt.Key_Right:
            self.pan += QPointF(step, 0)
        elif k == Qt.Key_Up:
            self.pan -= QPointF(0, step)
        elif k == Qt.Key_Down:
            self.pan += QPointF(0, step)
        elif k == Qt.Key_P:                      # 恢复默认显示位置
            pf = self.mw.primary_frame()
            if pf:
                f = self.project().tracks[pf[0]].frame_at(pf[1])
                if f and (f.offset_x or f.offset_y):
                    self.mw.push_undo()
                    f.offset_x = f.offset_y = 0
        else:
            super().keyPressEvent(e)
            return
        self.update()


class CanvasPanel(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("对齐方式:"))
        self.btn_tl = QPushButton("左上方对齐")
        self.btn_c = QPushButton("中心对齐")
        self.btn_anchor = QPushButton("设置锚点")
        self.btn_tl.setToolTip("所有帧以场景 (0,0) 为左上角对齐")
        self.btn_c.setToolTip("所有帧在舞台中心对齐")
        self.btn_anchor.setToolTip("所有帧的左上角对齐到填写的 X,Y 坐标")
        grp = QButtonGroup(self)
        grp.setExclusive(True)
        for mode, btn in (("topleft", self.btn_tl), ("center", self.btn_c)):
            btn.setCheckable(True)
            grp.addButton(btn)
            bar.addWidget(btn)
            btn.clicked.connect(lambda _=False, m=mode: mw.set_align(m))
        self.btn_c.setChecked(True)
        bar.addWidget(QLabel("X:"))
        self.le_x = QLineEdit()
        bar.addWidget(self.le_x)
        bar.addWidget(QLabel("Y:"))
        self.le_y = QLineEdit()
        bar.addWidget(self.le_y)
        for le in (self.le_x, self.le_y):
            le.setFixedWidth(56)
            le.setValidator(QIntValidator(-99999, 99999, self))  # 整数，可为负
            le.setToolTip("对齐参考点（仅整数，允许负数）")
            le.returnPressed.connect(mw.apply_anchor_from_boxes)
        self.btn_anchor.setCheckable(True)
        grp.addButton(self.btn_anchor)
        self.btn_anchor.clicked.connect(mw.apply_anchor_from_boxes)
        bar.addWidget(self.btn_anchor)
        bar.addStretch(1)
        self.info = QLabel("")
        bar.addWidget(self.info)
        v.addLayout(bar)

        self.canvas = CanvasWidget(mw)
        v.addWidget(self.canvas, 1)

        zb = QHBoxLayout()
        zb.addWidget(QLabel("画布缩放:"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.valueChanged.connect(self._on_slider)
        self.lbl = QLabel("100%")
        btn100 = QPushButton("100%")
        btn100.clicked.connect(lambda: self.canvas.set_scale(1.0))
        zb.addWidget(self.slider, 1)
        zb.addWidget(self.lbl)
        zb.addWidget(btn100)
        v.addLayout(zb)
        self.sync_slider()

    def _on_slider(self, v):
        self.canvas.set_scale(MIN_SCALE * (MAX_SCALE / MIN_SCALE) ** (v / 1000.0))

    def sync_slider(self):
        s = self.canvas.scale
        v = int(round(1000 * math.log(s / MIN_SCALE) / math.log(MAX_SCALE / MIN_SCALE)))
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.lbl.setText(f"{s * 100:.0f}%")

    def sync_anchor_boxes(self):
        """非固定模式下，显示当前帧的对齐参考坐标；固定模式下显示锚点。"""
        if self.le_x.hasFocus() or self.le_y.hasFocus():
            return
        prj = self.mw.project
        if prj.align_mode == "fixed":
            x, y = prj.fixed_point
        else:
            f = self.mw.reference_frame()
            if f is None:
                return
            w, h = f.display_size()
            base = self.canvas.aligned_base(w, h)
            x, y = int(round(base.x())), int(round(base.y()))
        self.le_x.setText(str(x))
        self.le_y.setText(str(y))

    def update_info(self):
        f = self.mw.reference_frame()
        if f is None:
            self.info.setText("")
            return
        w, h = f.display_size()
        parts = [f"{w}×{h}"]
        if f.offset_x or f.offset_y:
            parts.append(f"偏移({f.offset_x},{f.offset_y})")
        if f.crop:
            parts.append(f"裁剪{tuple(f.crop)}")
        if f.is_empty:
            parts.append("空帧")
        elif self.mw.is_temp(f.source_path):
            parts.append("临时文件")
        self.info.setText("　".join(parts))



# ====================================================================
# 原 timeline.py
# ====================================================================

RULER_H = 22
HEADER_W = 132
MIME = "application/x-fg-frames"
TEMP_BG = QColor(88, 52, 96)      # 强制转换未导出的帧底色（紫）
CELL_BG = QColor(62, 62, 68)


class TimelineWidget(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self.parent_panel = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.setMinimumHeight(140)
        self.scroll_x = 0
        self.scroll_y = 0
        self.cell_w = 46
        self.track_h = 52
        self.fps = 25
        self.playing = 0            # 0 停止 / 1 正放 / -1 倒放
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self._press = None
        self._btn_rects = {}
        self._drop = None

    def project(self):
        return self.mw.project

    # ---------- 几何 ----------
    def index_at_x(self, x):
        return max(0, (x - HEADER_W + self.scroll_x) // self.cell_w)

    def track_at_y(self, y):
        if y < RULER_H:
            return None
        return (y - RULER_H + self.scroll_y) // self.track_h

    def viewport_w(self):
        return max(0, self.width() - HEADER_W)

    def content_w(self):
        return max(self.project().max_frames() + 2,
                   self.project().playhead + 2) * self.cell_w

    def content_h(self):
        return len(self.project().tracks) * self.track_h

    def set_scroll_x(self, v):
        self.scroll_x = max(0, v)
        self.update()

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        prj = self.project()
        p = QPainter(self)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), QColor(34, 34, 34))
        self._btn_rects = {}
        hl_pid = self.mw.palette_highlight

        first_t = max(0, self.scroll_y // self.track_h)
        last_t = min(len(prj.tracks) - 1, (self.scroll_y + H) // self.track_h + 1)
        for ti in range(first_t, last_t + 1):
            t = prj.tracks[ti]
            y0 = RULER_H + ti * self.track_h - self.scroll_y
            # ---- 头部（不被帧格覆盖） ----
            p.fillRect(QRect(0, y0, HEADER_W, self.track_h), QColor(54, 54, 54))
            if ti == prj.current_track:
                p.fillRect(QRect(0, y0, 4, self.track_h), QColor(90, 150, 250))
            mr = QRect(6, y0 + self.track_h // 2 - 10, 28, 20)
            self._btn_rects[("mute", ti)] = mr
            p.fillRect(mr, QColor(170, 50, 50) if t.muted else QColor(70, 70, 70))
            p.setPen(QColor(230, 230, 230))
            p.drawRect(mr)
            p.drawText(mr, Qt.AlignCenter, "🔇" if t.muted else "🔊")
            dr = QRect(HEADER_W - 28, y0 + self.track_h // 2 - 10, 22, 20)
            self._btn_rects[("del", ti)] = dr
            p.fillRect(dr, QColor(70, 70, 70))
            p.drawRect(dr)
            p.drawText(dr, Qt.AlignCenter, "✖")
            p.setPen(QColor(220, 220, 220))
            p.drawText(QRect(38, y0, HEADER_W - 70, self.track_h),
                       Qt.AlignVCenter, t.name)

            # ---- 帧格区（裁剪，防止盖住头部按钮） ----
            p.save()
            p.setClipRect(QRect(HEADER_W, RULER_H, max(0, W - HEADER_W),
                                max(0, H - RULER_H)))
            p.fillRect(QRect(HEADER_W, y0, W - HEADER_W, self.track_h),
                       QColor(40, 40, 40) if ti % 2 == 0 else QColor(45, 45, 45))
            first_f = max(0, self.scroll_x // self.cell_w)
            last_f = min(len(t.frames) - 1,
                         (self.scroll_x + self.viewport_w()) // self.cell_w)
            for fi in range(first_f, last_f + 1):
                x0 = HEADER_W + fi * self.cell_w - self.scroll_x
                cell = QRect(x0 + 1, y0 + 1, self.cell_w - 2, self.track_h - 2)
                f = t.frames[fi]
                if f is None:                       # 空位
                    p.fillRect(cell, QColor(28, 28, 28))
                    p.setPen(QColor(58, 58, 58))
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(cell)
                    continue
                is_tmp = bool(f.source_path) and self.mw.is_temp(f.source_path)
                p.fillRect(cell, TEMP_BG if is_tmp else CELL_BG)
                name_h = 12 if (self.cell_w >= 56 and self.track_h >= 44
                                and f.source_path) else 0
                if f.is_empty:
                    p.setPen(QColor(150, 150, 150))
                    p.drawText(cell, Qt.AlignCenter, "空")
                elif self.cell_w >= 12 and self.track_h >= 12:
                    img = self.mw.cache.get(f, prj)
                    s = img.scaled(max(1, cell.width() - 4),
                                   max(1, cell.height() - 4 - name_h),
                                   Qt.KeepAspectRatio, Qt.FastTransformation)
                    p.drawImage(cell.x() + (cell.width() - s.width()) // 2,
                                cell.y() + (cell.height() - name_h
                                            - s.height()) // 2, s)
                    if f.cleared:
                        p.setPen(QColor(255, 180, 60))
                        p.drawText(cell, Qt.AlignCenter, "清")
                if name_h:
                    # 帧格足够大时显示文件名（能显示几个字符就显示几个）
                    name = os.path.basename(f.source_path)
                    nr = QRect(cell.x() + 2, cell.bottom() - name_h,
                               cell.width() - 4, name_h)
                    p.fillRect(nr, QColor(30, 30, 34, 190))
                    p.setPen(QColor(190, 190, 190))
                    el = p.fontMetrics().elidedText(name, Qt.ElideMiddle,
                                                    nr.width())
                    p.drawText(nr, Qt.AlignHCenter | Qt.AlignVCenter, el)
                if f.crop is not None:              # 裁剪标记（橙点）
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(255, 160, 40))
                    p.drawRect(QRect(cell.right() - 6, cell.top() + 2, 5, 5))
                if hl_pid and f.palette_id == hl_pid:
                    p.setPen(QPen(QColor(60, 220, 90), 2))
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(cell)
                if (ti, fi) in self.mw.selection:
                    p.setPen(QPen(QColor(90, 150, 255), 2))
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(cell.adjusted(1, 1, -1, -1))
            # 播放头所在列的高亮
            phx = HEADER_W + prj.playhead * self.cell_w - self.scroll_x
            if HEADER_W <= phx < W:
                p.fillRect(QRect(phx, y0, self.cell_w, self.track_h),
                           QColor(139, 0, 0, 40))
            p.restore()

        # 标尺
        p.fillRect(QRect(0, 0, W, RULER_H), QColor(50, 50, 50))
        p.fillRect(QRect(0, 0, HEADER_W, RULER_H), QColor(44, 44, 44))
        step = max(1, int(math.ceil(34 / self.cell_w)))
        first_f = max(0, self.scroll_x // self.cell_w)
        last_f = (self.scroll_x + self.viewport_w()) // self.cell_w + 1
        for fi in range(first_f, last_f + 1):
            x0 = HEADER_W + fi * self.cell_w - self.scroll_x
            p.setPen(QColor(120, 120, 120))
            p.drawLine(x0, RULER_H - 6, x0, RULER_H)
            if fi % step == 0:
                p.setPen(QColor(210, 210, 210))
                p.drawText(QRect(x0 + 3, 2, self.cell_w * step, RULER_H - 4),
                           Qt.AlignVCenter, str(fi))
        # 播放头（暗红色）
        phx = HEADER_W + prj.playhead * self.cell_w - self.scroll_x
        p.setPen(QPen(QColor(139, 0, 0), 2))
        p.drawLine(phx, 0, phx, H)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(139, 0, 0))
        p.drawPolygon(QPolygonF([QPointF(phx - 5, 0), QPointF(phx + 5, 0),
                                 QPointF(phx, 7)]))
        # 拖放指示线
        if self._drop:
            ti, idx = self._drop
            x = HEADER_W + idx * self.cell_w - self.scroll_x
            p.setPen(QPen(QColor(90, 200, 120), 2))
            if ti < len(prj.tracks):
                y0 = RULER_H + ti * self.track_h - self.scroll_y
                p.drawLine(x, y0, x, y0 + self.track_h)
            else:
                y0 = RULER_H + len(prj.tracks) * self.track_h - self.scroll_y
                p.drawLine(HEADER_W, y0, W, y0)

    # ---------- 播放 ----------
    def play(self, direction):
        self.playing = direction
        self.timer.start(max(10, int(1000 / self.fps)))

    def stop(self):
        self.playing = 0
        self.timer.stop()

    def _tick(self):
        prj = self.project()
        nxt = prj.playhead + self.playing
        if nxt < 0 or nxt >= prj.max_frames():   # 放到头/尾自动停
            self.stop()
            self.mw.refresh_all()
            return
        prj.playhead = nxt
        self.ensure_visible()
        self.mw.refresh_all()

    def set_playhead(self, ph, update_selection=True):
        prj = self.project()
        ph = max(0, min(int(ph), 99999))         # 允许停在空白区域
        prj.playhead = ph
        self.ensure_visible()
        if update_selection and self.playing == 0 and prj.tracks:
            ti = min(prj.current_track, len(prj.tracks) - 1)
            if prj.tracks[ti].frame_at(ph) is not None:
                self.mw.set_selection({(ti, ph)}, primary=(ti, ph))
            else:
                self.mw.set_selection(set())
        self.mw.refresh_all()

    def ensure_visible(self):
        sx = self.project().playhead * self.cell_w
        if sx < self.scroll_x:
            self.scroll_x = sx
        elif sx + self.cell_w > self.scroll_x + self.viewport_w():
            self.scroll_x = sx + self.cell_w - self.viewport_w()
        if self.parent_panel:
            self.parent_panel.sync_scroll()

    # ---------- 轨道重命名 ----------
    def edit_track_name(self, ti, rect):
        t = self.project().tracks[ti]
        le = QLineEdit(t.name, self)
        le.setGeometry(rect.adjusted(2, 2, -2, -2))
        le.show()
        le.setFocus()
        le.selectAll()

        def done():
            name = le.text().strip()
            le.deleteLater()
            if name and name != t.name:
                self.mw.push_undo()
                t.name = name
            self.mw.refresh_all()
            self.setFocus()
        le.editingFinished.connect(done)

    # ---------- 鼠标 ----------
    def mousePressEvent(self, e):
        self.setFocus()
        prj = self.project()
        pos = e.pos()
        # 清除“浏览色表”的绿色着重框：点空白处，或点没有绿框的帧
        if self.mw.palette_highlight:
            hpid = self.mw.palette_highlight
            target_f = None
            ti = self.track_at_y(pos.y())
            if (ti is not None and ti < len(prj.tracks)
                    and pos.x() >= HEADER_W and pos.y() >= RULER_H):
                target_f = prj.tracks[ti].frame_at(self.index_at_x(pos.x()))
            if target_f is None or target_f.palette_id != hpid:
                self.mw.palette_highlight = None

        if e.button() == Qt.LeftButton:
            for key, r in self._btn_rects.items():
                if r.contains(pos):
                    kind, ti = key
                    (self.mw.toggle_mute if kind == "mute"
                     else self.mw.delete_track)(ti)
                    return
            if pos.y() < RULER_H and pos.x() >= HEADER_W:
                self._press = ("scrub",)
                self.set_playhead(self.index_at_x(pos.x()))
                return
            if pos.x() < HEADER_W:
                if ti is not None and ti < len(prj.tracks):
                    self.select_track_frames(ti)   # 点头部：选中整条轨道的帧
                return
            fi = self.index_at_x(pos.x())
            ti = self.track_at_y(pos.y())
            f = (prj.tracks[ti].frame_at(fi)
                 if ti is not None and ti < len(prj.tracks) else None)
            if f is not None:
                mods = e.modifiers()
                if mods & Qt.ControlModifier:                # Ctrl 多选
                    sel = set(self.mw.selection)
                    if (ti, fi) in sel:
                        sel.discard((ti, fi))
                    else:
                        sel.add((ti, fi))
                    self.mw.set_selection(sel, primary=(ti, fi), track=ti)
                elif mods & Qt.ShiftModifier:                # Shift 范围选
                    a = self.mw.anchor or (ti, fi)
                    lo, hi = sorted([a[1], fi])
                    sel = {(ti, i) for i in range(lo, hi + 1)
                           if prj.tracks[ti].frame_at(i) is not None}
                    self.mw.set_selection(sel, primary=(ti, fi), track=ti,
                                          keep_anchor=True)
                else:
                    if (ti, fi) in self.mw.selection:
                        self.mw.set_primary((ti, fi))
                        self.mw.set_current_track(ti)
                    else:
                        self.mw.set_selection({(ti, fi)}, primary=(ti, fi), track=ti)
                self.set_playhead(fi, update_selection=False)
                self._press = ("maybe_drag", pos)
            else:
                # 头部（含按钮区以外）：选中整条轨道；其余空白（含轨道外）定位光标线
                if pos.x() < HEADER_W:
                    pass
                if ti is not None and ti < len(prj.tracks):
                    self.mw.set_current_track(ti)
                self.mw.set_selection(set())
                self.set_playhead(fi, update_selection=False)
                self._press = ("scrub",)          # 拖拽中光标线实时跟随
        elif e.button() == Qt.RightButton:
            self.show_context(pos)

    def mouseDoubleClickEvent(self, e):
        prj = self.project()
        pos = e.pos()
        ti = self.track_at_y(pos.y())
        if ti is None or ti >= len(prj.tracks) or pos.x() >= HEADER_W:
            return
        rect = QRect(38, RULER_H + ti * self.track_h - self.scroll_y,
                     HEADER_W - 70, self.track_h)
        if rect.contains(pos):
            self.edit_track_name(ti, rect)

    def mouseMoveEvent(self, e):
        if not self._press:
            return
        if self._press[0] == "scrub":
            self.set_playhead(self.index_at_x(e.pos().x()))
        elif self._press[0] == "maybe_drag":
            if (e.pos() - self._press[1]).manhattanLength() > 8:
                self.start_drag()

    def mouseReleaseEvent(self, e):
        self._press = None

    def start_drag(self):
        if not self.mw.selection:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME, b"frames")
        drag.setMimeData(mime)
        ti, fi = sorted(self.mw.selection)[0]
        f = self.project().tracks[ti].frame_at(fi)
        if f is not None and not f.is_empty:
            pm = QPixmap.fromImage(self.mw.cache.get(f, self.project()).scaled(
                48, 48, Qt.KeepAspectRatio, Qt.FastTransformation))
            drag.setPixmap(pm)
        self._press = None
        drag.exec_(Qt.MoveAction)
        self._drop = None
        self.update()

    # ---------- 拖放 ----------
    @staticmethod
    def _urls(md):
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile()]

    def drop_target(self, pos):
        ti = self.track_at_y(pos.y())
        if ti is None:
            ti = 0
        return ti, self.index_at_x(pos.x())

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasFormat(MIME) or any(iu.is_image(p) for p in self._urls(md)):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        self._drop = self.drop_target(e.pos())
        e.acceptProposedAction()
        self.update()

    def dragLeaveEvent(self, e):
        self._drop = None
        self.update()

    def dropEvent(self, e):
        ti, idx = self.drop_target(e.pos())
        self._drop = None
        md = e.mimeData()
        if md.hasFormat(MIME):
            self.mw.move_frames(set(self.mw.selection), ti, idx)
        else:
            paths = sorted(p for p in self._urls(md) if iu.is_image(p))
            if paths:
                self.mw.import_images(paths, ti, idx)
        e.acceptProposedAction()
        self.update()

    # ---------- 轨道头部：整轨选择 ----------
    def select_track_frames(self, ti):
        prj = self.project()
        sel = {(ti, i) for i, f in enumerate(prj.tracks[ti].frames)
               if f is not None}
        if sel:
            self.mw.set_selection(sel, primary=max(sel), track=ti)
        else:
            self.mw.set_current_track(ti)

    # ---------- 右键菜单 ----------
    def show_context(self, pos):
        prj = self.project()
        ti = self.track_at_y(pos.y())
        if ti is None or ti >= len(prj.tracks) or pos.x() < HEADER_W:
            return
        fi = self.index_at_x(pos.x())
        self.mw.set_current_track(ti)
        f = prj.tracks[ti].frame_at(fi)
        menu = QMenu(self)
        if f is None:
            act = menu.addAction("在此处插入空帧")
            if menu.exec_(self.mapToGlobal(pos)) == act:
                self.mw.insert_empty_at(ti, fi)
            return
        if (ti, fi) not in self.mw.selection:
            self.mw.set_selection({(ti, fi)}, primary=(ti, fi), track=ti)
            self.set_playhead(fi, update_selection=False)
        a_del = menu.addAction("删除帧\tDel")
        a_clr = menu.addAction("清空帧\tShift+Del")
        a_ins = menu.addAction("插入同尺寸空帧\tCtrl+E")
        a_dup = menu.addAction("复制帧\tCtrl+D")
        menu.addSeparator()
        a_exp = menu.addAction("导出为索引图…")
        act = menu.exec_(self.mapToGlobal(pos))
        if act == a_del:
            self.mw.delete_selected()
        elif act == a_clr:
            self.mw.clear_selected()
        elif act == a_ins:
            self.mw.insert_empty_after(ti, fi)
        elif act == a_dup:
            self.mw.duplicate_after(ti, fi)
        elif act == a_exp:
            self.mw.export_selected()

    # ---------- 键盘 / 滚轮 ----------
    def keyPressEvent(self, e):
        k = e.key()
        mods = e.modifiers()
        prj = self.project()
        if k == Qt.Key_Left:
            self.set_playhead(prj.playhead - 1)
        elif k == Qt.Key_Right:
            self.set_playhead(prj.playhead + 1)
        elif k == Qt.Key_Home:
            self.set_playhead(0)
        elif k == Qt.Key_End:
            self.set_playhead(max(0, prj.max_frames() - 1))
        elif k == Qt.Key_Space:
            if self.playing:
                self.stop()                       # 播放中再按空格：停在当前位置
                self.mw.refresh_all()
            elif mods & Qt.ShiftModifier:
                self.play(-1)
            else:
                self.play(1)
        elif k == Qt.Key_A and mods & Qt.ControlModifier:   # 全选所有帧
            sel = {(ti, fi) for ti, t in enumerate(prj.tracks)
                   for fi, f in enumerate(t.frames) if f is not None}
            if sel:
                self.mw.set_selection(sel, primary=max(sel))
        elif k == Qt.Key_Delete and mods & Qt.ShiftModifier:
            self.mw.clear_selected()
        elif k == Qt.Key_Delete:
            self.mw.delete_selected()
        elif k == Qt.Key_E and mods & Qt.ControlModifier:
            self.mw.insert_empty_after_primary()
        elif k == Qt.Key_D and mods & Qt.ControlModifier:
            self.mw.duplicate_primary()
        elif k == Qt.Key_A and mods & Qt.AltModifier:
            self.mw.remove_gaps(prj.current_track)
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        dx = e.angleDelta().x()
        mods = e.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        shift = bool(mods & Qt.ShiftModifier)
        maxv = max(0, self.content_w() - self.viewport_w())
        if dx:                                      # 横向滚轮（倾斜滚轮）
            self.scroll_x = min(maxv, max(0, self.scroll_x + int(dx * 0.75)))
        elif ctrl and shift:                        # 轨道高度（上下缩放）
            f = 1.15 if d > 0 else 1 / 1.15
            self.track_h = int(min(160, max(24, self.track_h * f)))
        elif ctrl:                                  # 帧格宽度（左右缩放）
            f = 1.15 if d > 0 else 1 / 1.15
            mf = max(1, self.project().max_frames())
            # 允许一直压缩到所有帧都能显示在轴上
            min_w = max(1, min(12, self.viewport_w() // mf))
            if d > 0:
                # 放大：保证至少 +1，避免 int 截断后永远卡在 min_w
                self.cell_w = min(200, max(self.cell_w + 1, int(self.cell_w * f)))
            else:
                self.cell_w = max(min_w, int(self.cell_w * f))
        elif shift:                                 # Shift+滚轮：左右滑动时间轴
            self.scroll_x = min(maxv, max(0, self.scroll_x - d))
        else:                                       # 上下查看轨道
            ms = max(0, self.content_h() - (self.height() - RULER_H))
            self.scroll_y = min(ms, max(0, self.scroll_y - d))
        if self.parent_panel:
            self.parent_panel.sync_scroll()
        self.update()

    def mousePressEventHeader(self, ti):
        self.select_track_frames(ti)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.parent_panel:
            self.parent_panel.sync_scroll()


class TimelinePanel(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        b1 = QPushButton("添加轨道")
        b1.clicked.connect(mw.add_track)
        b2 = QPushButton("插入空帧")
        b2.clicked.connect(mw.insert_empty_at_playhead)
        b3 = QPushButton("删除空位(Alt+A)")
        b3.clicked.connect(lambda: mw.remove_gaps(mw.project.current_track))
        for b in (b1, b2, b3):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(QLabel("跳转到:"))
        self.spin_goto = QSpinBox()
        self.spin_goto.setRange(0, 99999)
        self.spin_goto.valueChanged.connect(self._on_goto)
        bar.addWidget(self.spin_goto)
        self.lbl_stats = QLabel("")
        bar.addWidget(self.lbl_stats)
        bar.addWidget(QLabel("帧率:"))
        self.spin = QSpinBox()
        self.spin.setRange(1, 60)
        self.spin.setValue(25)
        self.spin.valueChanged.connect(self._on_fps)
        bar.addWidget(self.spin)
        v.addLayout(bar)

        self.widget = TimelineWidget(mw)
        self.widget.parent_panel = self
        v.addWidget(self.widget, 1)
        self.hbar = QScrollBar(Qt.Horizontal)
        self.hbar.valueChanged.connect(self.widget.set_scroll_x)
        v.addWidget(self.hbar)

    def _on_fps(self, v):
        w = self.widget
        w.fps = v
        if w.playing:
            w.timer.start(max(10, int(1000 / v)))

    def _on_goto(self, v):
        self.widget.set_playhead(v)

    def update_stats(self):
        prj = self.mw.project
        self.lbl_stats.setText(f"共 {prj.max_frames()} 帧 | 选中 {len(self.mw.selection)}")
        self.spin_goto.blockSignals(True)
        self.spin_goto.setValue(prj.playhead)
        self.spin_goto.blockSignals(False)

    def sync_scroll(self):
        w = self.widget
        maxv = max(0, w.content_w() - w.viewport_w())
        self.hbar.blockSignals(True)
        self.hbar.setRange(0, maxv)
        self.hbar.setPageStep(max(1, w.viewport_w()))
        self.hbar.setValue(min(w.scroll_x, maxv))
        self.hbar.blockSignals(False)
        w.scroll_x = min(w.scroll_x, maxv)



# ====================================================================
# 原 panels.py
# ====================================================================

class FolderTree(QTreeWidget):
    """多根目录树：惰性加载，可拖拽图片，文件夹右键可关闭。"""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.mw = panel.mw
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.itemExpanded.connect(self.ensure_populated)
        self.root_paths = []

    # ---- 构建 ----
    def add_root(self, path):
        ap = os.path.abspath(path)
        if ap in self.root_paths:
            return
        self.root_paths.append(ap)
        it = self._make_item(os.path.basename(ap) or ap, ap, True)
        f = it.font(0)
        f.setBold(True)
        it.setFont(0, f)
        self.addTopLevelItem(it)

    def _make_item(self, name, path, is_dir):
        it = QTreeWidgetItem([name])
        it.setData(0, Qt.UserRole, path)
        it.setToolTip(0, path)
        it.setIcon(0, self.style().standardIcon(
            QStyle.SP_DirIcon if is_dir else QStyle.SP_FileIcon))
        if is_dir:
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            QTreeWidgetItem(it, ["…"])     # 占位，展开时替换
        return it

    @staticmethod
    def _is_dir_item(it):
        p = it.data(0, Qt.UserRole)
        return bool(p) and os.path.isdir(p)

    def ensure_populated(self, it):
        if it.data(0, Qt.UserRole + 1):
            return
        it.setData(0, Qt.UserRole + 1, True)
        while it.childCount():
            it.takeChild(0)
        path = it.data(0, Qt.UserRole)
        try:
            names = sorted(os.listdir(path), key=str.lower)
        except OSError:
            return
        for name in names:
            fp = os.path.join(path, name)
            if os.path.isdir(fp):
                it.addChild(self._make_item(name, fp, True))
            elif iu.is_image(fp):
                it.addChild(self._make_item(name, fp, False))

    def clear_all(self):
        self.clear()
        self.root_paths = []

    # ---- 拖拽 ----
    def mimeData(self, items):
        md = QMimeData()
        urls, seen = [], set()
        for it in items:
            p = it.data(0, Qt.UserRole)
            if p and os.path.isfile(p) and p not in seen:
                seen.add(p)
                urls.append(QUrl.fromLocalFile(p))
        md.setUrls(urls)
        return md

    # ---- 右键：关闭文件夹 ----
    def contextMenuEvent(self, e):
        it = self.itemAt(e.pos())
        if it is None or not self._is_dir_item(it):
            return
        menu = QMenu(self)
        act = menu.addAction("关闭文件夹")
        if menu.exec_(e.globalPos()) == act:
            self.close_folder(it)

    def close_folder(self, it):
        path = it.data(0, Qt.UserRole)
        parent = it.parent()
        if parent is None:                       # 根条目：同步工程数据
            self.takeTopLevelItem(self.indexOfTopLevelItem(it))
            if path in self.root_paths:
                self.root_paths.remove(path)
            self.mw.remove_folder_root(path)
        else:
            parent.takeChild(parent.indexOfChild(it))

    # ---- Ctrl+A ----
    def select_all_images(self):
        def walk(it):
            if self._is_dir_item(it):
                self.ensure_populated(it)
                for i in range(it.childCount()):
                    walk(it.child(i))
            else:
                it.setSelected(True)
        self.clearSelection()
        for i in range(self.topLevelItemCount()):
            walk(self.topLevelItem(i))


class ProjectPanel(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        v = QVBoxLayout(self)
        btn_new = QPushButton("新建工程")
        btn_new.clicked.connect(mw.new_project)
        btn_import = QPushButton("导入图片…")
        btn_import.clicked.connect(mw.import_dialog)
        btn_open = QPushButton("打开文件夹…")
        btn_open.clicked.connect(mw.open_folder)
        v.addWidget(btn_new)
        v.addWidget(btn_import)
        v.addWidget(btn_open)
        v.addWidget(QLabel("文件夹列表（可拖拽 / Ctrl+A 全选 / 右键关闭文件夹）:"))

        self.tree = FolderTree(self)
        v.addWidget(self.tree, 1)
        sc = QShortcut(QKeySequence.SelectAll, self.tree,
                       context=Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(self.tree.select_all_images)

        btn_save = QPushButton("保存工程 (Ctrl+S)")
        btn_save.clicked.connect(mw.save_project)
        btn_load = QPushButton("导入工程…")
        btn_load.clicked.connect(mw.load_project_dialog)
        v.addWidget(btn_save)
        v.addWidget(btn_load)

    def add_root(self, path):
        self.tree.add_root(path)

    def set_roots(self, paths):
        self.tree.clear_all()
        for p in paths or []:
            if p and os.path.isdir(p):
                self.tree.add_root(p)


class PaletteGrid(QWidget):
    """16x16 色表格子。
    - 单击选中单个色块；拖拽实时选中
    - Ctrl+点击：切换任意多个色块的选择
    - Shift+点击：选中与锚点色块之间的连续范围
    - 方向键移动选中色块；双击回调改色
    """

    def __init__(self, cell=16):
        super().__init__()
        self.cell = cell
        self.setFixedSize(16 * cell + 2, 16 * cell + 2)
        self.setFocusPolicy(Qt.StrongFocus)  # 允许方向键移动选中的色块
        self.colors = None
        self.sel = []                        # 按选择顺序排列的选中色块
        self.anchor_index = None             # Shift 范围选择的锚点
        self.tidx = None                     # 透明索引（高亮标记）
        self.on_double_click = None
        self.on_select = None                # 参数为主色块（最后选中的）或 None
        self.on_copy = None
        self.on_paste = None

    # ---- 兼容接口：主色块 ----
    @property
    def selected_index(self):
        return self.sel[-1] if self.sel else None

    @selected_index.setter
    def selected_index(self, idx):
        self.sel = [] if idx is None else [idx]

    def set_colors(self, colors, reset=False, tidx=None):
        self.colors = colors
        self.tidx = tidx                     # 透明索引（高亮标记）
        if reset:
            self.sel = []
            self.anchor_index = None
        self.update()

    def set_selection(self, indices):
        self.sel = list(indices)
        if self.sel:
            self.anchor_index = self.sel[-1]
        self.update()
        if self.on_select:
            self.on_select(self.selected_index)

    def clear_selection(self):
        if not self.sel:
            return
        self.sel = []
        self.anchor_index = None
        self.update()
        if self.on_select:
            self.on_select(None)

    def _index_at(self, e):
        if not self.colors:
            return None
        col = (e.x() - 1) // self.cell
        row = (e.y() - 1) // self.cell
        if 0 <= col < 16 and 0 <= row < 16:
            return row * 16 + col
        return None

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 40))
        if not self.colors:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, "无色表")
            return
        c = self.cell
        for i in range(256):
            r, col = divmod(i, 16)
            p.fillRect(1 + col * c, 1 + r * c, c, c, QColor(*self.colors[i]))
        p.setPen(QPen(QColor(70, 70, 70), 1))
        p.setBrush(Qt.NoBrush)
        for i in range(17):
            p.drawLine(1 + i * c, 1, 1 + i * c, 1 + 16 * c)
            p.drawLine(1, 1 + i * c, 1 + 16 * c, 1 + i * c)
        if self.tidx is not None and 0 <= self.tidx < 256:   # 透明索引标记
            r, col = divmod(self.tidx, 16)
            rect = QRect(1 + col * c, 1 + r * c, c, c)
            p.setPen(QPen(QColor(255, 80, 80), 2))
            p.drawLine(rect.topLeft(), rect.bottomRight())
            p.drawLine(rect.topRight(), rect.bottomLeft())
        for i in self.sel:                        # 选中色块高亮
            r, col = divmod(i, 16)
            rect = QRect(1 + col * c, 1 + r * c, c, c)
            if i == self.selected_index:          # 主色块：黄框
                p.setPen(QPen(QColor(0, 0, 0), 3))
                p.drawRect(rect)
                p.setPen(QPen(QColor(255, 255, 120), 1))
                p.drawRect(rect.adjusted(1, 1, -1, -1))
            else:                                 # 次选色块：青框
                p.setPen(QPen(QColor(0, 0, 0), 3))
                p.drawRect(rect)
                p.setPen(QPen(QColor(120, 220, 255), 1))
                p.drawRect(rect.adjusted(1, 1, -1, -1))

    def _click(self, idx, mods):
        if idx is None:
            return
        if mods & Qt.ControlModifier:             # Ctrl：切换单个
            sel = list(self.sel)
            if idx in sel:
                sel.remove(idx)
            else:
                sel.append(idx)
            self.anchor_index = idx
            self.sel = sel
        elif mods & Qt.ShiftModifier:             # Shift：范围选择
            a = self.anchor_index if self.anchor_index is not None else idx
            lo, hi = sorted((a, idx))
            self.sel = list(range(lo, hi + 1))
        else:
            self.sel = [idx]
            self.anchor_index = idx
        self.update()
        if self.on_select:
            self.on_select(self.selected_index)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_mods = e.modifiers()
            self._click(self._index_at(e), e.modifiers())

    def mouseMoveEvent(self, e):
        # 按住左键拖拽时，实时选中鼠标经过的色块
        # （按下时带 Shift 则持续做范围选择，否则逐个选中）
        if e.buttons() & Qt.LeftButton:
            mods = getattr(self, "_press_mods", Qt.NoModifier)
            if mods & Qt.ShiftModifier:
                self._click(self._index_at(e), Qt.ShiftModifier)
            else:
                self._click(self._index_at(e), Qt.NoModifier)

    def keyPressEvent(self, e):
        if not self.colors:
            super().keyPressEvent(e)
            return
        mods = e.modifiers()
        if e.key() == Qt.Key_C and mods & Qt.ControlModifier:
            if self.on_copy:
                self.on_copy()
            e.accept()
            return
        if e.key() == Qt.Key_V and mods & Qt.ControlModifier:
            if self.on_paste:
                self.on_paste()
            e.accept()
            return
        # 焦点在色块上时，方向键移动选中色块（不触发时间轴切帧）
        d = {Qt.Key_Left: -1, Qt.Key_Right: 1,
             Qt.Key_Up: -16, Qt.Key_Down: 16}.get(e.key())
        if d is None:
            super().keyPressEvent(e)
            return
        cur = self.selected_index if self.selected_index is not None else 0
        r, col = divmod(cur, 16)
        if d in (-1, 1):
            col = min(15, max(0, col + d))
        else:
            r = min(15, max(0, r + (1 if d > 0 else -1)))
        self.sel = [r * 16 + col]
        self.anchor_index = self.sel[0]
        self.update()
        if self.on_select:
            self.on_select(self.selected_index)
        e.accept()

    def mouseDoubleClickEvent(self, e):
        idx = self._index_at(e)
        if idx is not None and self.on_double_click:
            self.on_double_click(idx)


class ToolPanel(QWidget):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self._shown_pid = None
        self._pal = None
        v = QVBoxLayout(self)
        v.addWidget(QLabel("索引颜色表"))

        brow = QHBoxLayout()
        brow.addWidget(QLabel("浏览:"))
        self.spin = QSpinBox()
        self.spin.setRange(0, 0)
        self.spin.valueChanged.connect(self._on_spin)
        brow.addWidget(self.spin)
        self.lbl_count = QLabel("/ 0")
        brow.addWidget(self.lbl_count)
        self.btn_sel = QPushButton("选中帧")
        self.btn_sel.setToolTip("选中所有使用该颜色表的帧")
        self.btn_sel.clicked.connect(mw.select_frames_by_palette)
        brow.addWidget(self.btn_sel)
        brow.addStretch(1)
        v.addLayout(brow)

        self.lbl_name = QLabel("（无色表）")
        v.addWidget(self.lbl_name)
        self.grid = PaletteGrid()
        self.grid.on_double_click = mw.edit_palette_color
        self.grid.on_select = mw.on_grid_color_selected
        self.grid.on_copy = mw.copy_swatches
        self.grid.on_paste = mw.paste_swatches
        v.addWidget(self.grid, 0, Qt.AlignHCenter)

        btn_trans = QPushButton("设置为透明色")
        btn_trans.setToolTip("把选中的色块设为该色表的透明索引")
        btn_trans.clicked.connect(mw.set_transparent_color)
        v.addWidget(btn_trans)

        prow = QHBoxLayout()
        btn_new_pal = QPushButton("新建配色表")
        btn_new_pal.setToolTip("创建一个全新的空白颜色表")
        btn_new_pal.clicked.connect(mw.new_palette)
        btn_imp_pal = QPushButton("导入.pal")
        btn_imp_pal.setToolTip("从 .pal 配色文件（JASC-PAL / 原始768字节）导入颜色表")
        btn_imp_pal.clicked.connect(mw.import_palette)
        btn_exp_pal = QPushButton("导出.pal")
        btn_exp_pal.setToolTip("把当前浏览的颜色表导出为 .pal 配色文件")
        btn_exp_pal.clicked.connect(mw.export_palette)
        for b in (btn_new_pal, btn_imp_pal, btn_exp_pal):
            prow.addWidget(b)
        v.addLayout(prow)

        trow = QHBoxLayout()
        trow.addWidget(QLabel("工具:"))
        self.btn_mouse = QPushButton("🖱")
        self.btn_pencil = QPushButton("✏️")
        self.btn_mouse.setToolTip("普通鼠标指针 (Alt+C)")
        self.btn_pencil.setToolTip("铅笔工具：用选中的色块在帧上涂画 (Alt+C)")
        for b in (self.btn_mouse, self.btn_pencil):
            b.setCheckable(True)
            b.setFixedWidth(40)
        self.btn_mouse.setChecked(True)
        self.btn_mouse.clicked.connect(lambda: mw.set_pencil_mode(False))
        self.btn_pencil.clicked.connect(lambda: mw.set_pencil_mode(True))
        trow.addWidget(self.btn_mouse)
        trow.addWidget(self.btn_pencil)
        trow.addStretch(1)
        v.addLayout(trow)

        self.lbl_info = QLabel("")
        v.addWidget(self.lbl_info)
        btn_merge = QPushButton("合并色表")
        btn_merge.clicked.connect(mw.merge_palettes)
        v.addWidget(btn_merge)
        btn_export = QPushButton("导出选中帧为索引图…")
        btn_export.clicked.connect(mw.export_selected)
        v.addWidget(btn_export)
        v.addStretch(1)

    def _on_spin(self, v):
        if self.spin.maximum() >= 1:
            self.mw.browse_palette(v - 1)   # spin 为 1 起始

    def _update_info(self):
        if self._pal is None:
            return
        t = (f"透明索引: {self._pal.transparent_index}"
             if self._pal.transparent_index is not None else "无透明索引")
        sel = self.grid.sel
        if len(sel) > 1:
            t += f"　|　已选 {len(sel)} 色块 (Ctrl+C 复制)"
        elif sel:
            t += f"　|　选中色块: #{sel[-1]}"
        self.lbl_info.setText(t)

    def show_palette(self, pal, count, idx):
        self._pal = pal
        self.spin.blockSignals(True)
        self.spin.setRange(1 if count else 0, count)
        self.spin.setValue(idx + 1 if count else 0)
        self.spin.setEnabled(count > 0)
        self.spin.blockSignals(False)
        self.lbl_count.setText(f"/ {count}")
        if pal is None:
            self.lbl_name.setText("（无色表）")
            self.grid.set_colors(None)
            self.lbl_info.setText("")
            self._shown_pid = None
        else:
            reset = pal.id != self._shown_pid
            self._shown_pid = pal.id
            self.lbl_name.setText(f"{pal.name}（单击选色，双击改色，"
                                  f"Ctrl/Shift 多选）")
            self.grid.set_colors(pal.colors, reset=reset,
                                 tidx=pal.transparent_index)
            self._update_info()
        for b, st in ((self.btn_mouse, not self.mw.pencil_mode),
                      (self.btn_pencil, self.mw.pencil_mode)):
            b.blockSignals(True)
            b.setChecked(st)
            b.blockSignals(False)



# ====================================================================
# 原 dialogs.py
# ====================================================================

class NonIndexedDialog(QDialog):
    INDEPENDENT = 1
    ASSIGN = 2

    def __init__(self, parent, total, bad):
        super().__init__(parent)
        self.setWindowTitle("检测到非索引图")
        self.choice = None
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"在所有 {total} 张图片中，有 {bad} 张图片没有检测到颜色表。"))
        v.addWidget(QLabel("索引图是必需的，请选择处理方式:"))
        a = QPushButton("独立地生成每张图片的颜色表")
        a.clicked.connect(lambda: self._done(self.INDEPENDENT))
        b = QPushButton("让我指定每张图片的颜色表")
        b.clicked.connect(lambda: self._done(self.ASSIGN))
        c = QPushButton("取消（不导入这些图片）")
        c.clicked.connect(self.reject)
        for btn in (a, b, c):
            v.addWidget(btn)

    def _done(self, choice):
        self.choice = choice
        self.accept()


class PaletteAssignDialog(QDialog):
    """左侧待处理图片列表 / 中间已检测色表 / 右侧预览 + 16x16 色格。"""

    def __init__(self, parent, mw, images):
        super().__init__(parent)
        self.setWindowTitle("指定每张图片的颜色表")
        self.resize(780, 430)
        self.mw = mw
        self.results = {}            # 原图路径 -> (临时文件, palette_id)
        self.remaining = list(images)

        h = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(QLabel("待处理的图片:"))
        self.list_imgs = QListWidget()
        for p in images:
            it = QListWidgetItem(os.path.basename(p))
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.list_imgs.addItem(it)
        left.addWidget(self.list_imgs)

        mid = QVBoxLayout()
        mid.addWidget(QLabel("检测到的颜色表:"))
        self.list_pals = QListWidget()
        for pal in mw.project.palettes:
            self.list_pals.addItem(pal.name)
        mid.addWidget(self.list_pals)

        right = QVBoxLayout()
        self.preview = QLabel("（无预览）")
        self.preview.setFixedSize(180, 180)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("QLabel{border:1px solid #888;}")
        right.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.grid = PaletteGrid(cell=14)
        right.addWidget(self.grid, 0, Qt.AlignHCenter)
        self.btn_use = QPushButton("使用该颜色表")
        self.btn_use.clicked.connect(self.use_current)
        self.btn_all = QPushButton("全部使用该颜色表")
        self.btn_all.clicked.connect(self.use_all)
        right.addWidget(self.btn_use)
        right.addWidget(self.btn_all)

        h.addLayout(left, 1)
        h.addLayout(mid, 1)
        h.addLayout(right, 0)

        self.list_pals.currentRowChanged.connect(lambda _: self.update_preview())
        self.list_imgs.currentRowChanged.connect(lambda _: self.update_preview())
        if self.list_imgs.count():
            self.list_imgs.setCurrentRow(0)
        if self.list_pals.count():
            self.list_pals.setCurrentRow(0)
        if not mw.project.palettes:
            self.btn_use.setEnabled(False)
            self.btn_all.setEnabled(False)
            self.preview.setText("工程中还没有任何颜色表")

    def current_palette(self):
        row = self.list_pals.currentRow()
        pals = self.mw.project.palettes
        return pals[row] if 0 <= row < len(pals) else None

    def current_problem(self):
        it = self.list_imgs.currentItem()
        if it and it.data(Qt.UserRole) in self.remaining:
            return it.data(Qt.UserRole)
        return self.remaining[0] if self.remaining else None

    def update_preview(self):
        pal = self.current_palette()
        if pal is None:
            return
        self.grid.set_colors(pal.colors, tidx=pal.transparent_index)
        # 优先显示与该色表有映射的第一张图（本次分配的优先，其次工程记录）
        for tp, pid in self.results.values():
            if pid == pal.id and os.path.exists(tp):
                self._show(tp, pal)
                return
        for key, rec in self.mw.project.registry.items():
            if rec["pal"] == pal.id:
                src = rec.get("conv") or key
                if os.path.exists(src):
                    self._show(src, pal)
                    return
        # 没有已映射图片时，演示当前问题图片转换后的效果
        prob = self.current_problem()
        if prob:
            try:
                q = iu.map_to_palette(iu.load(prob), pal.colors, pal.transparent_index)
                img = iu.pil_to_qimage(q)
                if max(img.width(), img.height()) > 176:
                    img = img.scaled(176, 176, Qt.KeepAspectRatio, Qt.FastTransformation)
                self.preview.setPixmap(QPixmap.fromImage(img))
                return
            except Exception:
                pass
        self.preview.setText("（暂无映射图片）")

    def _show(self, path, pal):
        try:
            self.preview.setPixmap(QPixmap.fromImage(iu.preview_qimage(path, pal, 176)))
        except Exception:
            self.preview.setText("（预览失败）")

    def _apply(self, paths):
        pal = self.current_palette()
        if pal is None:
            return
        for path in paths:
            try:
                q = iu.map_to_palette(iu.load(path), pal.colors, pal.transparent_index)
            except Exception:
                continue
            tp = self.mw.temp_store.path("conv")
            q.save(tp)
            self.results[path] = (tp, pal.id)
            if path in self.remaining:
                self.remaining.remove(path)
            for i in range(self.list_imgs.count()):
                it = self.list_imgs.item(i)
                if it.data(Qt.UserRole) == path and not it.text().startswith("✔"):
                    it.setText("✔ " + it.text())
        self.update_preview()
        if not self.remaining:
            self.accept()

    def use_current(self):
        prob = self.current_problem()
        if prob:
            self._apply([prob])

    def use_all(self):
        self._apply(list(self.remaining))
