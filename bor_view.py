# -*- coding: utf-8 -*-
"""OpenBOR 的**界面**控件：动画画布、时间轴、通用输入控件、
源码编辑框（语法高亮 / 一键优化）、攻击预览窗口。

（由 bor_canvas.py、bor_timeline.py、bor_widgets.py、bor_text.py、
bor_preview.py 合并而来）"""

import json
import math
import os
import re
import sys

from PyQt5.QtCore import QPoint, QPointF, QRectF, QSizeF, QTimer, Qt
from PyQt5.QtGui import (QBrush, QColor, QDoubleValidator, QFont, QIcon, QIntValidator, QLinearGradient, QPainter, QPen, QPixmap, QPolygonF, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextFormat, QTransform)
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSizePolicy, QSpinBox, QTextEdit, QVBoxLayout, QWidget)

# 本项目的其它模块
from bor_data import (HEADER_CMDS, HEADER_TAB_WIDTH, SCRIPT_EVENTS, bor_dm, bs, is_script_end, is_script_start, resolve_frame_path)
from core import (MARK_COLORS, VIVID_COLORS, iu)



# ====================================================================
# 原 bor_canvas.py
# ====================================================================

def draw_arrow(p, a, b, color, width=4, head=13, dashed=False):
    """画一条粗箭头（从 a 到 b），带实心箭头帽。主画布 / 预览场景共用。"""
    if a is None or b is None:
        return
    dx, dy = b.x() - a.x(), b.y() - a.y()
    ln = (dx * dx + dy * dy) ** 0.5
    if ln < 2.0:
        return
    ux, uy = dx / ln, dy / ln
    # 箭头尖端留一点空隙，免得盖住目标点
    tip = QPointF(b.x() - ux * min(head * 0.5, ln * 0.3),
                  b.y() - uy * min(head * 0.5, ln * 0.3))
    pen = QPen(color, width,
               Qt.DashLine if dashed else Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawLine(a, tip)
    # 箭头帽（三角形）
    px, py = -uy, ux                       # 垂直方向
    h = min(head, ln * 0.6)
    w = h * 0.52
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([
        QPointF(tip.x() + ux * h, tip.y() + uy * h),
        QPointF(tip.x() + px * w, tip.y() + py * w),
        QPointF(tip.x() - px * w, tip.y() - py * w)]))
    p.setBrush(Qt.NoBrush)


# ============================================================ 画布
class BorCanvas(QWidget):
    """左键拖拽：平移；右键拖拽：画框模式；中键单击：画点模式。
    矩形/点画出后保持，由角落按钮确认为 BBox/Attack/Offset。"""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setMinimumSize(240, 200)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.scale = 2.0
        self.pan = QPointF(20, 20)
        self._drag = None            # ("pan", pos) | ("rect", start_scene)
        self._rect = None            # 待确认的矩形 (x, y, w, h)（场景坐标）
        self._point = None           # 待确认的点 (x, y)
        self._qimg = None
        self._rel = None
        self._resolved = None
        self._checker = None
        # ---- drawmethod 显示 ----
        self._pil = None          # 当前帧的 RGBA 图（颜色特效要重画像素）
        self._pal = None          # 当前帧的 256 色表（fillcolor 是色表索引）
        self._fx = {}             # 颜色特效结果缓存
        self.show_dm = True       # 是否把 drawmethod 画出来（按钮可关）
        self._dm_has = False      # 当前帧有没有 drawmethod（不管开关）
        self._dm_desc = ""        # 当前帧的 drawmethod 说明（画布角标）
        # ---- 动画显示增强：位移 / 跳跃 / 生成物 ----
        self.motion = None       # 播放中的 (dx, dz, dy)；None = 不模拟
        self.ghosts = []         # 半透明生成物
        self.marks = []          # 生成位置标记 [(x, z, 文字)]
        self.show_shadow = True  # 跳跃时在地上画影子
        # 画布对齐模式："offset"=Offset 点不动（默认） / "origin"=图片原点不动
        self.align_mode = "offset"
        self._anchor_screen = None      # Offset 点应停留的屏幕位置

        # 对齐方式切换（两个极小按钮，必选其一）
        self.align_group = QButtonGroup(self)
        self.align_group.setExclusive(True)
        self.btn_align_pic = QPushButton("P", self)
        self.btn_align_off = QPushButton("O", self)
        self.btn_align_pic.setToolTip(
            "P：图片原点对齐 —— 图片的 (0,0) 在画布上始终不动")
        self.btn_align_off.setToolTip(
            "O：Offset 点对齐 —— 当前帧 Offset 点在画布上始终不动（默认）")
        for b, mode in ((self.btn_align_pic, "origin"),
                        (self.btn_align_off, "offset")):
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setFixedSize(20, 18)
            b.setStyleSheet(
                "QPushButton{font-size:11px;padding:0px;}"
                "QPushButton:checked{background:#2f6db3;color:#ffffff;}")
            self.align_group.addButton(b)
            b.clicked.connect(lambda _=False, m=mode: self.set_align_mode(m))
        # 静音（播放动画时是否播放帧音效）—— 纯符号圆形按钮，紧挨 P/O
        self.btn_mute = QPushButton("♪", self)
        self.btn_mute.setCheckable(True)
        self.btn_mute.setFixedSize(28, 28)
        self.btn_mute.setToolTip("静音：播放动画时不播放帧音效"
                                 "（♪ 带删除线 = 已静音）")
        self.btn_mute.clicked.connect(self._mute_clicked)
        # 恢复设置：默认缩放 + 锚点居中
        self.btn_reset = QPushButton("恢复设置", self)
        self.btn_reset.setFixedSize(62, 20)
        self.btn_reset.setStyleSheet(
            "QPushButton{font-size:11px;padding:0px;}"
            "QPushButton:hover{background:#e6eef7;}")
        self.btn_reset.setToolTip("恢复默认缩放，并把 Offset 点 / 图片原点"
                                  "移到画布中间（依 P/O 模式）")
        self.btn_reset.clicked.connect(self.reset_view)
        # drawmethod 效果开关（画 / 不画）
        self.btn_dm = QPushButton("DM", self)
        self.btn_dm.setCheckable(True)
        self.btn_dm.setChecked(True)
        self.btn_dm.setFixedSize(28, 20)
        self.btn_dm.setToolTip(
            "显示 drawmethod 的效果（缩放 / 翻转 / 旋转 / 倾斜 / "
            "透明度 / 填充色…）\n不勾＝按原图显示，方便对照着调")
        self.btn_dm.clicked.connect(self._dm_clicked)

        self.btn_off = QPushButton("设为Offset", self)
        self.btn_bbox = QPushButton("设为BBox", self)
        self.btn_atk = QPushButton("设为Attack", self)
        self.btn_off.setToolTip("把画出的点（鼠标中键单击）设为当前帧 Offset")
        self.btn_bbox.setToolTip("把画出的矩形（鼠标右键拖拽）设为当前帧 BBox")
        self.btn_atk.setToolTip("把画出的矩形（鼠标右键拖拽）设为当前帧 Attack 框")
        for b in (self.btn_off, self.btn_bbox, self.btn_atk):
            b.setFixedWidth(88)          # 只锁宽度，高度随字体自适应
            b.setEnabled(False)
        self.btn_off.clicked.connect(panel.apply_pending_offset)
        self.btn_bbox.clicked.connect(panel.apply_pending_bbox)
        self.btn_atk.clicked.connect(panel.apply_pending_attack)

    def show_frame(self, rel, resolved):
        self._rel = rel
        self._resolved = resolved
        self._qimg = None
        self._pil = None
        self._pal = None
        if resolved and os.path.exists(resolved):
            try:
                raw = iu.load(resolved)
                if raw.mode == "P":
                    self._pal = (iu.palette_colors(raw) or (None, None))[0]
                self._pil = iu.openbor_rgba(raw)
                self._qimg = iu.pil_to_qimage(self._pil)
            except Exception:
                self._qimg = None
                self._pil = None
        self._refresh_dm_text()
        if self._anchor_screen is None:
            self._anchor_screen = QPointF(self.width() * 0.5,
                                          self.height() * 0.62)
        self.apply_align()
        self.update()

    # ---- 对齐模式 ----
    def set_align_mode(self, mode):
        """切换对齐方式：先把当前锚点位置记下来，避免视图跳动。"""
        if self.align_mode != mode:
            if mode == "offset":
                self._anchor_screen = self.to_screen(self._anchor_scene())
            self.align_mode = mode
        self.btn_align_pic.setChecked(mode == "origin")
        self.btn_align_off.setChecked(mode == "offset")
        self.panel._save_pref("align", mode)
        self.apply_align()
        self.update()

    def _anchor_scene(self):
        """当前锚点的场景坐标（origin=图片原点，offset=Offset 点）。"""
        if self.align_mode == "offset":
            fr = self.panel.current_frame_info()
            if fr and fr["offset"]:
                try:
                    return QPointF(float(fr["offset"][0]),
                                   float(fr["offset"][1]))
                except ValueError:
                    pass
        return QPointF(0, 0)

    def apply_align(self):
        """按对齐模式调整 pan，使锚点停在 _anchor_screen。"""
        if self.align_mode != "offset" or self._anchor_screen is None:
            return
        a = self._anchor_scene()
        self.pan = QPointF(a.x() - self._anchor_screen.x() / self.scale,
                           a.y() - self._anchor_screen.y() / self.scale)

    def _after_view_change(self):
        """用户手动平移/缩放后，更新锚点的屏幕位置（供后续帧对齐）。"""
        if self.align_mode == "offset":
            self._anchor_screen = self.to_screen(self._anchor_scene())
        self.update()

    # ---- 静音外观 ----
    def _apply_mute_look(self):
        on = self.btn_mute.isChecked()
        f = self.btn_mute.font()
        f.setPointSize(12)
        f.setBold(True)
        f.setStrikeOut(on)
        self.btn_mute.setFont(f)
        self.btn_mute.setStyleSheet(
            "QPushButton{background:%s;color:%s;border:1px solid %s;"
            "border-radius:13px;padding:0px;}"
            "QPushButton:hover{background:%s;}"
            % (("#fdecec", "#c0392b", "#e0a0a0", "#fbdcdc") if on else
               ("#eaf3fb", "#2f6db3", "#9dc0e4", "#dcebf9")))

    def _mute_clicked(self):
        self.panel._muted = self.btn_mute.isChecked()
        self._apply_mute_look()
        self.panel._save_pref("muted", self.panel._muted)

    # ---- 恢复默认视图 ----
    def reset_view(self):
        self.scale = 2.0
        self._anchor_screen = QPointF(self.width() * 0.5,
                                      self.height() * 0.62)
        a = self._anchor_scene()
        self.pan = QPointF(a.x() - self._anchor_screen.x() / self.scale,
                           a.y() - self._anchor_screen.y() / self.scale)
        self.update()

    def to_scene(self, p):
        """屏幕点 → 帧**本地**坐标。

        现在拖时间轴也会显示 move 位移，画面上的精灵/框是整块平移过的，
        而 offset / bbox / attack 存的都是不随 move 变的本地值，
        所以取点时要先把位移减掉，画的时候再加回来（见 paintEvent）。
        """
        q = QPointF(p.x() / self.scale + self.pan.x(),
                    p.y() / self.scale + self.pan.y())
        if self.motion:
            dx, dz, dy = self.motion[0], self.motion[1], self.motion[2]
            q = QPointF(q.x() - dx, q.y() - (dz - dy))
        return q

    def to_screen(self, p):
        return QPointF((p.x() - self.pan.x()) * self.scale,
                       (p.y() - self.pan.y()) * self.scale)

    # ---- drawmethod 显示 ----
    def _dm_raw(self):
        """当前帧的 drawmethod 规格（**不管开关**），没有则 None。"""
        fr = self.panel.current_frame_info()
        if fr is None or fr.get("nodm"):
            return None
        s = bor_dm.parse(fr.get("dm") or {}, self._pal)
        return s if s["on"] else None

    def dm_spec(self):
        """真正要画出来的 drawmethod 规格（开关关了就是 None）。"""
        return self._dm_raw() if self.show_dm else None

    def set_show_dm(self, on):
        self.show_dm = bool(on)
        self.btn_dm.setChecked(self.show_dm)
        self._refresh_dm_text()
        self.panel._save_pref("dm", self.show_dm)
        self.update()

    def _refresh_dm_text(self):
        raw = self._dm_raw()
        self._dm_has = raw is not None
        self._dm_desc = bor_dm.describe(raw) if (raw and self.show_dm) else ""
        self._apply_dm_look()

    def _dm_clicked(self):
        self.set_show_dm(not self.show_dm)

    def _apply_dm_look(self):
        """开关外观：本帧真有 drawmethod 时用紫色（和设置区 DrawMethod 同色）。"""
        on = self.btn_dm.isChecked()
        act = bool(self._dm_desc)
        bg, fg, bd = ("#8e5bd0", "#ffffff", "#6c3fae") if (on and act) \
            else (("#2f6db3", "#ffffff", "#255a94") if on else
                  ("#e8eaee", "#7a7f88", "#c4c8d0"))
        self.btn_dm.setStyleSheet(
            "QPushButton{font-size:11px;padding:0px;background:%s;color:%s;"
            "border:1px solid %s;border-radius:3px;}"
            "QPushButton:hover{background:%s;}" % (bg, fg, bd, fg))
        tip = ("显示 drawmethod 的效果（缩放 / 翻转 / 旋转 / 倾斜 / "
               "透明度 / 填充色…）\n不勾＝按原图显示，方便对照着调")
        if act:
            tip += "\n当前帧：drawmethod " + self._dm_desc
        self.btn_dm.setToolTip(tip)

    def _dm_img(self, spec):
        """带颜色特效（填充 / 染色 / 通道）的图，结果带缓存。"""
        key = bor_dm.color_key(spec)
        if key is None or self._pil is None:
            return self._qimg
        k = (self._rel, key)
        im = self._fx.get(k)
        if im is None:
            try:
                im = iu.pil_to_qimage(bor_dm.color_image(self._pil, spec))
            except Exception:
                im = self._qimg
            if len(self._fx) > 40:
                self._fx.clear()
            self._fx[k] = im
        return im

    def _dm_pivot(self, fr, mot):
        """变换中心 = 帧的 offset 点（手册：centerx / centery 就取自帧 offset）。

        返回的已经是**画布坐标**（含 move 位移），因为 drawmethod 的变换矩阵
        要叠在画布已有的变换之上。
        """
        iw = self._qimg.width() if self._qimg else 0
        ih = self._qimg.height() if self._qimg else 0
        px, py = iw / 2.0, ih / 2.0
        if fr is not None and fr.get("offset"):
            try:
                px = float(fr["offset"][0])
                py = float(fr["offset"][1])
            except (TypeError, ValueError, IndexError):
                pass
        q = self.to_screen(QPointF(px, py))
        dx, dz, dy = mot
        return QPointF(q.x() + dx * self.scale,
                       q.y() + (dz - dy) * self.scale)

    def _local_t(self, spec, mot, fr):
        """帧本地坐标 → 屏幕坐标 的**完整**变换。

        链路（从右往左作用）：drawmethod 变换 · move 位移 · 缩放 · 平移。
        图本身就是走这条链路画出来的（见 _draw_sprite），所以判定框
        （bbox / attack / offset，存的都是不受 drawmethod 影响的本地值）
        要画在"变换后"的对应位置上，也必须走同一条链路。
        """
        dx, dz, dy = mot
        t = QTransform()
        if spec is not None:
            g = bor_dm.geometry(spec, self._dm_pivot(fr, mot))
            if g is not None:
                t = g * t
        t.translate(dx * self.scale, (dz - dy) * self.scale)
        t.scale(self.scale, self.scale)
        t.translate(-self.pan.x(), -self.pan.y())
        return t

    def _draw_sprite(self, p, tl, fr, mot):
        """画当前帧图；有 drawmethod 就按它变换（开关关了就画原图）。"""
        img = self._qimg
        if img is None:
            return
        spec = self.dm_spec()
        base = QRectF(tl, QSizeF(img.width() * self.scale,
                                 img.height() * self.scale))
        if spec is None:
            p.drawImage(base, img)
            return
        src = self._dm_img(spec)
        t = bor_dm.geometry(spec, self._dm_pivot(fr, mot))
        if t is None:
            bor_dm.paint(p, src, tl, self.scale, spec)
        else:
            p.save()
            p.setTransform(t, True)
            bor_dm.paint(p, src, tl, self.scale, spec)
            p.restore()

    # ---- 动画显示增强：位移 / 跳跃 / 生成物 ----
    def set_motion(self, m):
        """m = (dx, dz, dy)：相对动画起点的水平 / 深度位移与离地高度。
        传 None 表示不模拟（编辑状态）。"""
        self.motion = m
        self.update()

    def set_overlay(self, ghosts=None, marks=None):
        """ghosts: [{"img","ox","oy","x","z","alpha","label"}] 半透明生成物；
        marks:   [(x, z, 文字)] 生成位置标记。"""
        self.ghosts = list(ghosts or [])
        self.marks = list(marks or [])
        self.update()

    def _draw_actor(self, p, img, ox, oy, px, pz, alpha=1.0, label=""):
        """把一个实体画在场景 (px, pz)：精灵左上角 = (px-ox, pz-oy)。"""
        if img is None:
            return
        tl = self.to_screen(QPointF(px - ox, pz - oy))
        old = p.opacity()
        p.setOpacity(alpha)
        p.drawImage(QRectF(tl, QSizeF(img.width() * self.scale,
                                      img.height() * self.scale)), img)
        p.setOpacity(old)
        if label:
            p.setPen(QColor(160, 230, 255))
            p.drawText(QRectF(tl.x() - 40, tl.y() - 14, 160, 13),
                       Qt.AlignLeft | Qt.AlignVCenter, label)

    def _refresh_corner_buttons(self):
        self.btn_off.setEnabled(self._point is not None)
        ok = self._rect is not None and self._rect[2] >= 1 and self._rect[3] >= 1
        self.btn_bbox.setEnabled(ok)
        self.btn_atk.setEnabled(ok)

    def clear_pending(self):
        self._rect = None
        self._point = None
        self._refresh_corner_buttons()
        self.update()

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
        fr = self.panel.current_frame_info()
        iw = self._qimg.width() if self._qimg else 0
        ih = self._qimg.height() if self._qimg else 0
        m = self.motion
        dx, dz, dy = (m[0], m[1], m[2]) if m else (0.0, 0.0, 0.0)
        # 跳跃时先在“地面”画一个影子（影子不吃 dy）
        if m and dy > 0.3 and self.show_shadow and fr is not None:
            try:
                ox, oy = (int(float(fr["offset"][0])),
                          int(float(fr["offset"][1]))) \
                    if fr["offset"] else (iw / 2, ih)
            except (ValueError, IndexError):
                ox, oy = iw / 2, ih
            g = self.to_screen(QPointF(ox + dx, oy + dz))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 110))
            p.drawEllipse(QRectF(g.x() - 14 * self.scale / 2,
                                 g.y() - 4 * self.scale / 2,
                                 14 * self.scale, 4 * self.scale))
        p.save()
        if m:
            # z 是“深度”，画在屏幕纵向；y 是离地高度，画成往上
            p.translate(QPointF(dx * self.scale, (dz - dy) * self.scale))
        if self._qimg:
            tl = self.to_screen(QPointF(0, 0))
            self._draw_sprite(p, tl, fr, (dx, dz, dy))
        elif self._rel is None:
            p.setPen(QColor(220, 220, 220))
            p.drawText(self.rect(), Qt.AlignCenter, "（无帧图）")

        if fr is not None and iw:
            spec = self.dm_spec()
            # 判定框要跟着 drawmethod 一起变换：换个干净的 CTM，位置自己算全
            lt = self._local_t(spec, (dx, dz, dy), fr)
            prev = p.transform()
            p.setTransform(QTransform())
            if fr["bbox"] and len(fr["bbox"]) >= 4:
                try:
                    x, y, r, d = [int(float(v)) for v in fr["bbox"][:4]]
                    self._draw_rect_t(p, lt, x, y, r, d,
                                      self.panel.bbox_color)
                except ValueError:
                    pass
            if fr["attack"]:
                _, aa = fr["attack"]
                if len(aa) >= 4 and aa[0] != "0":
                    try:
                        x, y, r, d = [int(float(v)) for v in aa[:4]]
                        self._draw_rect_t(p, lt, x, y, r, d,
                                          self.panel.attack_color)
                    except ValueError:
                        pass
            if fr["offset"]:
                try:
                    ox, oy = int(float(fr["offset"][0])), int(float(fr["offset"][1]))
                    sp = lt.map(QPointF(ox, oy))
                    p.setPen(QPen(QColor(0, 230, 230), 1))
                    p.drawLine(QPointF(sp.x() - 10, sp.y()),
                               QPointF(sp.x() + 10, sp.y()))
                    p.drawLine(QPointF(sp.x(), sp.y() - 10),
                               QPointF(sp.x(), sp.y() + 10))
                    p.drawEllipse(sp, 5, 5)     # 十字花中间加圆圈
                except (ValueError, IndexError):
                    pass
            p.setTransform(prev)
        p.restore()
        # ---- move 位移：从原本的 Offset 点画一条粗箭头，指到当前帧移到的位置 ----
        if m and fr is not None:
            try:
                ox = int(float(fr["offset"][0])) if fr["offset"] else 0
                oy = int(float(fr["offset"][1])) \
                    if fr["offset"] and len(fr["offset"]) > 1 else 0
            except (ValueError, IndexError):
                ox, oy = 0, 0
            o = self.to_screen(QPointF(ox, oy))                  # 起点：原 offset
            c = QPointF(o.x() + dx * self.scale,
                        o.y() + (dz - dy) * self.scale)          # 终点：当前位置
            if (c.x() - o.x()) ** 2 + (c.y() - o.y()) ** 2 > 36.0:
                # 起点留一个空心圈，说明“从这里出发”
                p.setPen(QPen(QColor(255, 208, 96), 2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QRectF(o.x() - 6, o.y() - 6, 12, 12))
                draw_arrow(p, o, c, QColor(255, 190, 60), width=4, head=15)
                txt = f"move {dx:+.0f},{dz:+.0f}" + (f" ↑{dy:.0f}" if dy else "")
                p.setPen(QColor(255, 226, 150))
                mid = QPointF((o.x() + c.x()) / 2.0, (o.y() + c.y()) / 2.0)
                p.drawText(QRectF(mid.x() - 70, mid.y() - 22, 180, 16),
                           Qt.AlignLeft | Qt.AlignVCenter, txt)
        # 生成物 / 生成位置标记（坐标里已经含了主实体的位移）
        for g in self.ghosts:
            self._draw_actor(p, g.get("img"), g.get("ox", 0), g.get("oy", 0),
                             g.get("x", 0), g.get("z", 0),
                             g.get("alpha", 0.55), g.get("label", ""))
        for x, z, label in self.marks:
            sp = self.to_screen(QPointF(x, z))
            p.setPen(QPen(QColor(120, 235, 160), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(sp.x() - 9, sp.y() - 5, 18, 10))
            p.drawLine(QPointF(sp.x() - 14, sp.y()), QPointF(sp.x() + 14, sp.y()))
            p.drawLine(QPointF(sp.x(), sp.y() - 9), QPointF(sp.x(), sp.y() + 9))
            if label:
                p.setPen(QColor(150, 240, 190))
                p.drawText(QRectF(sp.x() - 60, sp.y() - 30, 160, 14),
                           Qt.AlignLeft | Qt.AlignVCenter, label)
        # 待确认的框 / 点：和精灵一样跟着位移走（画在你“看到”的地方）
        off = QPointF(0.0, 0.0)
        if self.motion:
            off = QPointF(self.motion[0] * self.scale,
                          (self.motion[1] - self.motion[2]) * self.scale)
        if self._rect is not None:
            p.save()
            p.translate(off)
            self._draw_rect(p, *self._rect, QColor(255, 255, 255), dashed=True)
            p.restore()
        if self._point is not None:
            sp = self.to_screen(QPointF(*self._point)) + off
            p.setPen(QPen(QColor(255, 230, 80), 2))
            p.drawLine(QPointF(sp.x() - 8, sp.y()), QPointF(sp.x() + 8, sp.y()))
            p.drawLine(QPointF(sp.x(), sp.y() - 8), QPointF(sp.x(), sp.y() + 8))
        z = self.to_screen(QPointF(0, 0))
        p.setPen(QPen(QColor(180, 180, 180), 1, Qt.DashLine))
        p.drawLine(QPointF(z.x() - 6, z.y()), QPointF(z.x() + 6, z.y()))
        p.drawLine(QPointF(z.x(), z.y() - 6), QPointF(z.x(), z.y() + 6))
        if self._rel:
            missing = self._resolved is None and self._rel.lower() != "none"
            p.setPen(QColor(255, 122, 114) if missing else QColor(240, 240, 240))
            # 显示从 data 目录起的**完整路径**（不再只显示文件名）
            fname = self._rel + ("（未找到）" if missing else "")
            p.drawText(QRectF(6, 2, self.width() - 12, 16),
                       Qt.AlignVCenter, fname)
            dx = p.fontMetrics().width(fname) + 10
            # drawmethod 说明挪到文件名**同一行**（紫色 = DrawMethod 分组色）
            if self._dm_desc:
                p.setPen(QColor(196, 150, 255))
                p.drawText(QRectF(6 + dx, 2, max(10, self.width() - 18 - dx),
                                  16),
                           Qt.AlignVCenter, "drawmethod " + self._dm_desc)
            elif self._dm_has:
                p.setPen(QColor(150, 156, 168))
                p.drawText(QRectF(6 + dx, 2, max(10, self.width() - 18 - dx),
                                  16),
                           Qt.AlignVCenter, "（drawmethod 已关显示）")
        elif self._dm_desc:
            p.setPen(QColor(196, 150, 255))
            p.drawText(QRectF(6, 2, self.width() - 12, 16),
                       Qt.AlignVCenter, "drawmethod " + self._dm_desc)

    def _draw_rect(self, p, x, y, w, h, color, dashed=False):
        a = self.to_screen(QPointF(x, y))
        b = self.to_screen(QPointF(x + w, y + h))
        p.setPen(QPen(color, 1, Qt.DashLine if dashed else Qt.SolidLine))
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 40))
        p.drawRect(QRectF(a, b))

    def _draw_rect_t(self, p, t, x, y, w, h, color, dashed=False):
        """按完整变换矩阵 t 画一个框（可能被缩放 / 翻转 / 倾斜 / 旋转过）。"""
        pts = [t.map(QPointF(x, y)), t.map(QPointF(x + w, y)),
               t.map(QPointF(x + w, y + h)), t.map(QPointF(x, y + h))]
        p.setPen(QPen(color, 1, Qt.DashLine if dashed else Qt.SolidLine))
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 40))
        p.drawPolygon(QPolygonF(pts))

    # ---- 鼠标：左键平移 / 右键画框 / 中键画点 ----
    def mousePressEvent(self, e):
        self.setFocus()
        if self.panel.doc is None:
            return
        sp = self.to_scene(e.pos())
        if e.button() == Qt.RightButton:             # 右键拖拽：画框
            self._drag = ("rect", sp)
            self._rect = (int(sp.x()), int(sp.y()), 0, 0)
            self.update()
        elif e.button() == Qt.MidButton:             # 中键单击：画点
            self._point = (int(round(sp.x())), int(round(sp.y())))
            self._refresh_corner_buttons()
            self.update()
        elif e.button() == Qt.LeftButton:            # 左键拖拽：平移
            self._drag = ("pan", e.pos())

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        if self._drag[0] == "pan":
            d = e.pos() - self._drag[1]
            self.pan -= QPointF(d.x() / self.scale, d.y() / self.scale)
            self._drag = ("pan", e.pos())
            self._after_view_change()
        elif self._drag[0] == "rect":
            s = self.to_scene(e.pos())
            x0, y0 = self._drag[1].x(), self._drag[1].y()
            self._rect = (int(min(x0, s.x())), int(min(y0, s.y())),
                          int(abs(s.x() - x0)), int(abs(s.y() - y0)))
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[0] == "rect":
            self._refresh_corner_buttons()
        self._drag = None
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        x = self.width() - self.btn_atk.width() - 8
        bh = self.btn_off.sizeHint().height()
        self.btn_off.move(x, 8)
        self.btn_bbox.move(x, 8 + bh + 6)
        self.btn_atk.move(x, 8 + 2 * (bh + 6))
        # 左上角一排：P/O 对齐 + 静音 + 恢复设置 + DM 开关
        self.btn_align_pic.move(6, 21)
        self.btn_align_off.move(28, 21)
        self.btn_mute.move(52, 16)
        self.btn_reset.move(88, 21)
        self.btn_dm.move(154, 21)

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        dx = e.angleDelta().x()
        mods = e.modifiers()
        if dx:
            self.pan += QPointF(40 * dx / 120.0 / self.scale, 0)
            self._after_view_change()
            if not d:
                return
        if not d:
            return
        if mods & Qt.ControlModifier:
            old = self.scale
            self.scale = min(32.0, max(0.5, self.scale * (1.25 if d > 0 else 0.8)))
            sp = self.to_scene(e.pos())
            self.pan = QPointF(sp.x() - e.pos().x() / self.scale,
                               sp.y() - e.pos().y() / self.scale)
            if abs(self.scale - old) > 1e-9:
                self._after_view_change()
        elif mods & Qt.ShiftModifier:
            self.pan += QPointF((-40 if d > 0 else 40) / self.scale, 0)
            self._after_view_change()
        else:
            self.pan += QPointF(0, (-40 if d > 0 else 40) / self.scale)
            self._after_view_change()


# ============================================================ 时间轴



# ====================================================================
# 原 bor_timeline.py
# ====================================================================

# 生成新 entity 的帧用这个颜色打标记
SPAWN_COLOR = QColor(120, 235, 160)


# ============================================================ 时间轴
class BorTimeline(QWidget):
    """单轨时间轴（暗底）：缩略图、光标线、方向键、滚轮查看、
    空格播放、jumpframe/dropframe/landframe 彩色标记、sound 小喇叭、
    生成 entity 的帧标记 ✦、右下角的“攻击预览”按钮。"""

    RULER = 18

    def __init__(self, panel, with_attack_button=True):
        super().__init__()
        self.panel = panel
        self.cell_w = 56
        self.scroll_x = 0
        self._scrub = False
        self._thumbs = {}
        self.setFixedHeight(104)
        self.setMinimumWidth(220)
        self.setFocusPolicy(Qt.StrongFocus)
        # ---- 右下角：攻击预览（只有带攻击框的动画才允许按下） ----
        self.btn_atk = QPushButton("预览", self)
        self.btn_atk.setFixedSize(46, 22)
        self.btn_atk.setToolTip(
            "攻击预览：新开一个窗口，用关卡场景 + 假人试打这个攻击动画"
            "\n（只有带攻击框的动画才能按）")
        self.right_pad = 0
        if with_attack_button:
            self.btn_atk.clicked.connect(self._open_attack_preview)
            self.right_pad = self.btn_atk.width() + 10
        else:
            self.btn_atk.hide()

    def _open_attack_preview(self):
        fn = getattr(self.panel, "open_attack_preview", None)
        if fn is not None:
            fn()

    def set_attack_enabled(self, on):
        """当前动画有没有攻击框 → 决定“预览”按钮能不能按。"""
        self.btn_atk.setVisible(True)
        self.btn_atk.setEnabled(bool(on))
        self.btn_atk.setToolTip(
            "攻击预览：新开一个窗口，用关卡场景 + 假人试打这个攻击动画"
            + ("" if on else "\n（当前动画没有攻击框）"))

    def view_width(self):
        """画格子的可用宽度（右下角按钮占掉的那条不算）。"""
        return max(40, self.width() - self.right_pad)

    def clear_cache(self):
        self._thumbs.clear()

    def _frames(self):
        a = self.panel.current_anim()
        return a.frames() if a else []

    def _index_at(self, x):
        return max(0, int((x + self.scroll_x) // self.cell_w))

    def _thumb(self, fr):
        rel = fr["path"]
        if rel.lower() == "none":
            return None
        path = resolve_frame_path(self.panel.root_dirs, rel)
        key = path or rel
        if key not in self._thumbs:
            img = None
            if path:
                try:
                    img = iu.preview_qimage(path, None, 80)
                except Exception:
                    img = None
            self._thumbs[key] = img
            if len(self._thumbs) > 600:
                self._thumbs.clear()
        return self._thumbs[key]

    def ensure_visible(self, i):
        sx = i * self.cell_w
        vw = self.view_width()
        if sx < self.scroll_x:
            self.scroll_x = sx
        elif sx + self.cell_w > self.scroll_x + vw:
            self.scroll_x = sx + self.cell_w - vw
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(27, 28, 33))
        frames = self._frames()
        if not frames:
            p.setPen(QColor(140, 146, 156))
            p.drawText(self.rect(), Qt.AlignCenter, "（无动画 / 无帧）")
            return
        markers = self.panel.anim_markers()
        spawn = markers.get("spawn") or ()
        cur = self.panel.frame_idx
        cw = self.cell_w
        n = len(frames)
        vw = self.view_width()
        first = max(0, self.scroll_x // cw)
        last = min(n - 1, (self.scroll_x + vw) // cw + 1)
        p.fillRect(QRectF(0, 0, self.width(), self.RULER), QColor(36, 38, 44))
        if self.panel._play_mode:
            p.setPen(QColor(110, 210, 130))
            p.drawText(QRectF(6, 0, 44, self.RULER), Qt.AlignVCenter,
                       "▶ 播放" if self.panel._play_mode == "once"
                       else "▶ 循环")
        for i in range(first, last + 2):
            x = i * cw - self.scroll_x
            p.setPen(QColor(70, 74, 84))
            p.drawLine(int(x), self.RULER - 5, int(x), self.RULER)
            if 0 <= i < n:
                p.setPen(QColor(180, 186, 196))
                p.drawText(QRectF(x + 3, 1, cw - 4, self.RULER - 2),
                           Qt.AlignVCenter, str(i))
        for i in range(first, last + 1):
            x = i * cw - self.scroll_x
            cell = QRectF(x + 1, self.RULER + 1, cw - 2,
                          self.height() - self.RULER - 3)
            fr = frames[i]
            has_atk = fr["attack"] and fr["attack"][1] \
                and fr["attack"][1][0] != "0"
            fill = QColor(96, 52, 58) if has_atk else QColor(56, 59, 68)
            if fr["path"].lower() == "none":
                fill = QColor(38, 40, 46)
            p.fillRect(cell, fill)
            img = self._thumb(fr)
            if img is not None and cw >= 20:
                s = img.scaled(max(1, int(cell.width()) - 4),
                               max(1, int(cell.height()) - 16),
                               Qt.KeepAspectRatio, Qt.FastTransformation)
                p.drawImage(int(cell.x() + (cell.width() - s.width()) / 2),
                            int(cell.y() + 2), s)
            # 生成 entity 的帧：左上角画一个绿色菱形 + S
            if i in spawn:
                sx = int(cell.x()) + 7
                sy = int(cell.y()) + 7
                p.setPen(Qt.NoPen)
                p.setBrush(SPAWN_COLOR)
                p.drawPolygon(QPolygonF([
                    QPointF(sx, sy - 5), QPointF(sx + 5, sy),
                    QPointF(sx, sy + 5), QPointF(sx - 5, sy)]))
                if cw >= 30:
                    fnt = QFont(self.font())
                    fnt.setBold(True)
                    fnt.setPointSize(max(7, self.font().pointSize() - 1))
                    p.setFont(fnt)
                    p.setPen(QColor(24, 60, 40))
                    p.drawText(QRectF(sx - 5, sy - 5, 11, 11),
                               Qt.AlignCenter, "S")
                    p.setFont(self.font())
            my = int(cell.bottom() - 5)
            mx = int(cell.x()) + 3
            for cmd, idxs in (("jumpframe", markers["jumpframe"]),
                              ("dropframe", markers["dropframe"]),
                              ("landframe", markers["landframe"])):
                if i in idxs:
                    p.setPen(Qt.NoPen)
                    p.setBrush(MARK_COLORS[cmd])
                    p.drawRect(mx, my, 7, 5)
                    mx += 9
            has_sound = i in markers["sound"]
            if has_sound:
                p.setPen(QColor(220, 224, 230))
                p.drawText(QRectF(cell.right() - 16, cell.bottom() - 14, 14, 12),
                           Qt.AlignCenter, "🔊")
            # 帧脚本标记：Times New Roman 斜体 f（点击可编辑脚本）
            if fr.get("script") or fr.get("atcmd"):
                fnt = QFont("Times New Roman")
                fnt.setItalic(True)
                p.setFont(fnt)
                p.setPen(QColor(150, 215, 255))
                p.drawText(self._f_rect(cell, has_sound), Qt.AlignCenter, "f")
                p.setFont(self.font())
            p.setPen(QPen(QColor(242, 201, 76), 2) if i == cur
                     else QPen(QColor(30, 32, 38), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(cell)
        phx = cur * cw - self.scroll_x
        p.setPen(QPen(QColor(229, 83, 75), 2))
        p.drawLine(int(phx), 0, int(phx), self.height())

    def _scrub_to(self, x):
        frames = self._frames()
        if frames:
            i = min(self._index_at(x), len(frames) - 1)
            if i != self.panel.frame_idx:
                self.panel.set_frame_idx(i)

    def _cell_rect(self, i):
        return QRectF(i * self.cell_w - self.scroll_x + 1, self.RULER + 1,
                      self.cell_w - 2, self.height() - self.RULER - 3)

    @staticmethod
    def _f_rect(cell, has_sound):
        """帧右下角的 f 标记区域（有喇叭时左移，避免重叠）。"""
        fx = cell.right() - (32 if has_sound else 16)
        return QRectF(fx - 4, cell.bottom() - 14, 16, 12)

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.LeftButton:
            frames = self._frames()
            i = self._index_at(e.pos().x())
            if frames and 0 <= i < len(frames) and \
                    (frames[i].get("script") or frames[i].get("atcmd")):
                cell = self._cell_rect(i)
                if self._f_rect(cell, i in self.panel.anim_markers()["sound"]) \
                        .contains(e.pos()):
                    self.panel.edit_frame_script(i)
                    return
            self.panel.stop_play()
            self._scrub = True
            self._scrub_to(e.pos().x())

    def mouseMoveEvent(self, e):
        if self._scrub:
            self._scrub_to(e.pos().x())

    def mouseReleaseEvent(self, e):
        self._scrub = False

    def keyPressEvent(self, e):
        frames = self._frames()
        n = len(frames)
        k = e.key()
        if k == Qt.Key_Left:
            self.panel.set_frame_idx(max(0, self.panel.frame_idx - 1))
        elif k == Qt.Key_Right:
            self.panel.set_frame_idx(min(max(0, n - 1),
                                         self.panel.frame_idx + 1))
        elif k == Qt.Key_Home:
            self.panel.set_frame_idx(0)
        elif k == Qt.Key_End:
            self.panel.set_frame_idx(max(0, n - 1))
        elif k == Qt.Key_Space:
            if self.panel._play_mode:
                self.panel.stop_play()
                self.update()
            elif e.modifiers() & Qt.ControlModifier:
                self.panel.start_play("loop")
            else:
                self.panel.start_play("once")
        else:
            super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.right_pad:
            self.btn_atk.move(self.width() - self.btn_atk.width() - 6,
                              self.height() - self.btn_atk.height() - 5)

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        dx = e.angleDelta().x()
        n = len(self._frames())
        maxv = max(0, n * self.cell_w - self.view_width())
        if e.modifiers() & Qt.ControlModifier:
            min_w = max(24, min(48, self.view_width() // max(1, n)))
            if d > 0:
                self.cell_w = min(240, max(self.cell_w + 1,
                                           int(self.cell_w * 1.15)))
            else:
                self.cell_w = max(min_w, int(self.cell_w * 0.87))
        else:
            self.scroll_x = min(maxv, max(0, self.scroll_x - d - dx))
        self.update()



# ====================================================================
# 原 bor_widgets.py
# ====================================================================

# ---- 本地设置（记住上次打开的 data 目录、框颜色、画布对齐模式等） ----
# 打包成 exe 后：设置放在 exe 旁边（绿色软件的习惯），而不是 _internal 里
if getattr(sys, "frozen", False):
    _SETTINGS_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _SETTINGS_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(_SETTINGS_DIR, "spriter_settings.json")


class blocked:
    """临时屏蔽一批控件的信号（刷新界面回写值时用）。

    刷新时往控件里 setValue / setCurrentIndex 会照样发出信号，
    延时提交类控件还会把它记成"待提交"，等定时器一响就当成用户改值写回
    文档——表现为"什么都没做却提示未保存"。回写一律包在 with blocked(...) 里。
    """

    def __init__(self, *widgets):
        self.widgets = [w for w in widgets if w is not None]

    def __enter__(self):
        self.saved = [w.blockSignals(True) for w in self.widgets]
        return self

    def __exit__(self, *_a):
        for w, old in zip(self.widgets, self.saved):
            w.blockSignals(old)
        return False


# ---- “正在回写界面”临界区 ----
# blocked 要逐个列举控件，漏一个就前功尽弃；延时提交控件还有一个更隐蔽的
# 漏洞：定时器是在 setValue 之后才响的，那时 blocked 早就退出了。
# 所以另外配一个全局临界区：刷新期间延时提交一律不登记、不提交。
_REFRESH_DEPTH = 0


def refresh_begin():
    """进入“程序回写界面”的临界区（配对调用 refresh_end）。"""
    global _REFRESH_DEPTH
    _REFRESH_DEPTH += 1


def refresh_end():
    global _REFRESH_DEPTH
    _REFRESH_DEPTH = max(0, _REFRESH_DEPTH - 1)


def is_refreshing():
    """此刻是否在回写界面：是的话任何“值变了”都不能当成用户改值。"""
    return _REFRESH_DEPTH > 0


def _text_width(fm, s):
    """文本宽度：Qt≥5.11 用 horizontalAdvance，老版本退回 width()。"""
    fn = getattr(fm, "horizontalAdvance", None)
    try:
        if fn is not None:
            return fn(s)
    except Exception:
        pass
    return fm.width(s)


def _load_settings():
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {}


def _save_settings(d):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=1)
    except Exception:
        pass


# ============================================================ 颜色块按钮
class ColorSwatch(QPushButton):
    """单色块按钮：颜色=当前框线色，单击弹出鲜艳色下拉菜单。"""

    def __init__(self, color, on_pick, tip=""):
        super().__init__()
        self.setFixedSize(20, 20)
        self.setToolTip(tip)
        self.on_pick = on_pick
        self.clicked.connect(self._popup)
        self.set_color(color)

    def set_color(self, c):
        self.setStyleSheet(
            "QPushButton{background:%s;border:1px solid #7a828e;"
            "border-radius:3px;}QPushButton:hover{border:1px solid #2f6db3;}"
            % QColor(c).name())

    def _popup(self):
        """只显示色块，不显示 RGB 色值（色值放在悬浮提示里）。"""
        m = QMenu(self)
        m.setStyleSheet("QMenu{background:#ffffff;border:1px solid #ccd1d8;}"
                        "QMenu::item{padding:5px 14px;}"
                        "QMenu::item:selected{background:#d5e4f7;}")
        for hexc in VIVID_COLORS:
            pm = QPixmap(20, 20)
            pm.fill(QColor(hexc))
            act = m.addAction(QIcon(pm), "")
            act.setToolTip(hexc)
            act.triggered.connect(lambda _=False, h=hexc: self._pick(h))
        m.exec_(self.mapToGlobal(QPoint(0, self.height())))

    def _pick(self, h):
        self.set_color(QColor(h))
        self.on_pick(QColor(h))


def _icon_btn(text, color, tip, fn):
    """无边框小图标按钮（红✕=清除 / 蓝▶=继承）。"""
    b = QPushButton(text)
    b.setFixedSize(20, 20)
    b.setToolTip(tip)
    b.setStyleSheet("QPushButton{border:none;background:transparent;"
                    "color:%s;font-weight:bold;padding:0px;}"
                    "QPushButton:hover{background:#e8ecf2;border-radius:3px;}"
                    "QPushButton:disabled{color:#b6bac2;background:transparent;}"
                    % color)
    b.clicked.connect(fn)
    return b


def _section(text):
    lb = QLabel(text)
    lb.setProperty("role", "section")
    return lb


def _lbl(text):
    """控件前的小标签（12px 灰），自然宽度、自然高度。"""
    lb = QLabel(text)
    lb.setProperty("role", "unit")
    return lb


def _to_int(v, default=0):
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _mk_spin(lo=-100000, hi=100000, w=48, deferred=False):
    """小数字框；deferred=True 时滚轮连续调值只在停手 / 移出鼠标后提交一次。"""
    s = DeferSpinBox() if deferred else QSpinBox()
    s.setRange(lo, hi)
    s.setFixedWidth(w)
    return s


# ---- 分组卡片：白底 + 左侧 3px 语义色条 + 同色标题，等宽纵向排列 ----
GROUP_STYLES = {
    "anim_header": "#4a7fb5",
    "jump":        "#4d9b6a",
    "frame_basic": "#b08a2e",
    "dm":          "#8e6bb8",
    "attack":      "#c0504a",
}


def _styled_group(title, key):
    gb = QGroupBox(title)
    acc = GROUP_STYLES[key]
    gb.setStyleSheet(
        "QGroupBox{background:#ffffff;border:1px solid #d9dce1;"
        "border-left:3px solid %s;border-radius:6px;margin-top:14px;}"
        "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;"
        "color:%s;font-weight:bold;}" % (acc, acc))
    gb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
    return gb


# 注：这里曾经有过 field_rows()（按固定列数排网格）与 FlowLayout（流式排布），
# 两者都会把“哪个字段在第几行第几列”藏进算法里，改排版只能调数字、不能拖控件。
# 已全部移除：设置区现在在 openbor_ui.py 里一行一行手写。


# ---- 参数位置为帧序号的命令（数值不得超过当前动画总帧数） ----
FRAME_INDEX_ARGS = {
    "cancel": (0, 1),
    "counterframe": (0,),
    "counterrange": (0, 1),
    "pshotframe": (0,),
    "shootframe": (0,),
    "throwframe": (0,),
    "tossframe": (0,),
    "flipframe": (0,),
    "quakeframe": (0,),
    "spawnframe": (0,),
    "summonframe": (0,),
    "unsummonframe": (0,),
    "weaponframe": (0,),
}


class NumEdit(QLineEdit):
    """数字文本框（替代 gauge：避免滚轮误触调值）。
    editingFinished 才提交，程序化 setValue 不触发信号。"""

    def __init__(self, lo=-100000, hi=100000, w=56, double=False):
        super().__init__()
        self._lo, self._hi, self._double = lo, hi, double
        if double:
            v = QDoubleValidator(lo, hi, 2, self)
            v.setNotation(QDoubleValidator.StandardNotation)
            self.setValidator(v)
        else:
            self.setValidator(QIntValidator(lo, hi, self))
        self.setFixedWidth(w)
        self._prev = "0"
        self.setText("0")

    def value(self):
        t = (self.text() or "").strip()
        if not t:
            return 0
        try:
            v = float(t)
        except ValueError:
            return 0
        v = max(self._lo, min(self._hi, v))
        return v if self._double else int(v)

    def setValue(self, v):
        self._prev = f"{v:g}" if self._double else str(int(v))
        self.setText(self._prev)

    def revert(self):
        """拒绝修改：恢复到上一次的值。"""
        self.setText(self._prev)


class DeferredMixin:
    """滚轮等“短时间内能连续变化”的控件：变化先挂起，鼠标移出或停手
    DEFER_MS 毫秒后才真正提交一次，避免一轮滚轮产生几十次重算/撤销记录。"""

    DEFER_MS = 260
    # 类级默认值：没调过 set_deferred() 的实例（只当普通下拉框用）也要能安全
    # 收到 leaveEvent / focusOutEvent，否则会在 defer_flush 里炸 AttributeError。
    _commit_cb = None
    _pending = False
    _timer = None

    @property
    def _defer_ready(self):
        return self._timer is not None

    def defer_setup(self, commit):
        self._commit_cb = commit
        self._pending = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DEFER_MS)
        self._timer.timeout.connect(self.defer_flush)
        self.setMouseTracking(True)

    def defer_mark(self, *_a):
        # 回写界面期间收到的 valueChanged 是程序自己写进去的，不是用户改的
        if not self._defer_ready or is_refreshing():
            return
        self._pending = True
        self._timer.start()

    def defer_flush(self):
        if not self._defer_ready:
            return
        self._timer.stop()
        if not self._pending:
            return
        self._pending = False
        if is_refreshing():
            return
        cb = self._commit_cb
        if cb is not None:
            cb()

    def _call_base(self, name, e):
        """把事件交给基类实现。

        mixin 必须写在 Qt 基类**前面**（class X(DeferredMixin, QComboBox)），
        否则 MRO 里 super() 会落到 object 上，拿到 AttributeError。
        这里再兜一层，避免将来调整继承顺序又触发同样的崩溃。
        """
        fn = getattr(super(), name, None)
        if fn is not None:
            fn(e)

    def leaveEvent(self, e):
        self.defer_flush()
        self._call_base("leaveEvent", e)

    def focusOutEvent(self, e):
        self.defer_flush()
        self._call_base("focusOutEvent", e)


class SafeComboBox(DeferredMixin, QComboBox):
    """自带滚轮换项的下拉框（到端点即停），并支持延迟提交。

    原生 QComboBox 在滚轮换项时会同步发出 currentTextChanged，外部槽函数
    若在同一次事件里回写 setCurrentText，会把内部索引状态打乱，表现为
    “滚到第一项/最后一项后滚不动了”。这里改为自己处理滚轮。

    set_deferred(commit) 之后：换项只做“挂起”，实际 commit 在鼠标移出
    或停手 260ms 后才执行（一次滚轮只算一次修改）。
    """

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if not d or self.count() == 0:
            e.ignore()
            return
        e.accept()
        i = self.currentIndex() + (1 if d < 0 else -1)
        if i < 0 or i >= self.count():
            return                      # 到达端点，保持不动
        self.setCurrentIndex(i)

    def set_deferred(self, commit):
        self.defer_setup(commit)
        self.currentIndexChanged.connect(self.defer_mark)


class DeferSpinBox(DeferredMixin, QSpinBox):
    """滚轮可连续改值的数字框，同样延迟提交。"""

    def set_deferred(self, commit):
        self.defer_setup(commit)
        self.valueChanged.connect(self.defer_mark)


# ============================================================ 命令字段单元
class CmdField:
    """一个命令字段：启用勾选框 + 命令名标签 + 控件，横排一条。
    bi 型：勾选框本身即值；其余型：勾选仅启用，取消勾选删除命令行。"""

    def __init__(self, panel, cmd, spec, frame_level=False):
        self.panel = panel
        self.cmd = cmd
        self.spec = spec
        self.frame_level = frame_level
        self.unit = QWidget()
        lay = QHBoxLayout(self.unit)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.widgets = []
        self.le = None
        self._idx_widgets = []
        self._interactive = []
        t = spec[0] if isinstance(spec, tuple) else spec
        if t == "bi":
            self.ck = QCheckBox(cmd)
            self.ck.setToolTip("勾选写入命令，取消勾选删除命令行")
            self.ck.stateChanged.connect(lambda _: self._changed())
            lay.addWidget(self.ck)
            self.widgets.append(self.ck)
        else:
            self.ecb = QCheckBox()
            self.ecb.setToolTip(f"勾选启用 {cmd} 设置")
            self.ecb.stateChanged.connect(self._enable_toggled)
            lay.addWidget(self.ecb)
            lay.addWidget(_lbl(cmd))
            if t == "int":
                s = NumEdit(spec[1], spec[2], 35)
                s.editingFinished.connect(self._changed)
                lay.addWidget(s)
                self.widgets.append(s)
            elif t == "ints":
                w = 35 #if spec[1] >= 3 else 56   # 长参数串用窄框，保持单行
                for _ in range(spec[1]):
                    s = NumEdit(spec[2], spec[3], w)
                    s.editingFinished.connect(self._changed)
                    lay.addWidget(s)
                    self.widgets.append(s)
            elif t == "combo":
                c = SafeComboBox()
                c.addItems(spec[1])
                c.set_deferred(self._changed)
                lay.addWidget(c)
                self.widgets.append(c)
            elif t == "path":
                self.le = QLineEdit()
                self.le.setReadOnly(True)
                self.le.setFixedWidth(80)
                self.le.textChanged.connect(lambda _: self._changed())
                lay.addWidget(self.le)
                b = QPushButton("~")
                b.setFixedWidth(26)
                b.clicked.connect(self._browse)
                lay.addWidget(b)
                self._interactive.append(b)
            elif t == "name":
                self.le = QLineEdit()
                self.le.setFixedWidth(80)
                self.le.textChanged.connect(lambda _: self._changed())
                lay.addWidget(self.le)
            elif t == "cancel":
                for _ in range(3):
                    s = NumEdit(0, 100000, 32)
                    s.editingFinished.connect(self._changed)
                    lay.addWidget(s)
                    self.widgets.append(s)
                self.le = QLineEdit()
                self.le.setFixedWidth(64)
                self.le.setPlaceholderText("输入序列")
                self.le.textChanged.connect(lambda _: self._changed())
                lay.addWidget(self.le)
                s = NumEdit(1, 99, 35)
                s.editingFinished.connect(self._changed)
                lay.addWidget(s)
                self.widgets.append(s)
            elif t == "energy":
                s = NumEdit(0, 100000, 40)
                s.editingFinished.connect(self._changed)
                lay.addWidget(s)
                self.widgets.append(s)
                for _ in range(2):
                    c = SafeComboBox()
                    c.addItems(["0", "1", "2"])
                    c.setFixedWidth(40)
                    c.set_deferred(self._changed)
                    lay.addWidget(c)
                    self.widgets.append(c)
            elif t == "grabin":
                c = SafeComboBox()
                c.addItems(["0", "1"])
                c.setFixedWidth(56)
                c.set_deferred(self._changed)
                lay.addWidget(c)
                self.widgets.append(c)
                s = NumEdit(0, 100000)
                s.editingFinished.connect(self._changed)
                lay.addWidget(s)
                self.widgets.append(s)
            # 帧号参数 → 控件映射（供总帧数上限校验）
            idxs = FRAME_INDEX_ARGS.get(self.cmd, ())
            if idxs:
                if t == "int":
                    self._idx_widgets = [self.widgets[0]]
                elif t == "ints":
                    self._idx_widgets = [self.widgets[p] for p in idxs
                                         if p < len(self.widgets)]
                elif t == "cancel":
                    m = {0: 0, 1: 1, 2: 2, 4: 3}
                    self._idx_widgets = [self.widgets[m[p]] for p in idxs
                                         if p in m]
            self._interactive.extend(self.widgets)
            if self.le is not None:
                self._interactive.append(self.le)
            self._set_widgets_enabled(False)    # 默认不勾选 = 禁用

    def _set_widgets_enabled(self, on):
        for w in self._interactive:
            w.setEnabled(on)

    # ---- 读取 / 写入 ----
    def read(self):
        t = self.spec[0] if isinstance(self.spec, tuple) else self.spec
        if t == "bi":
            return ["1"] if self.ck.isChecked() else None
        if t == "int":
            return [str(self.widgets[0].value())]
        if t == "ints":
            return [str(w.value()) for w in self.widgets]
        if t == "combo":
            return [self.widgets[0].currentText()]
        if t in ("path", "name"):
            txt = (self.le.text() or "").strip()
            return [txt] if txt else None
        if t == "cancel":
            return [str(self.widgets[0].value()), str(self.widgets[1].value()),
                    str(self.widgets[2].value()),
                    (self.le.text() or "").strip() or "-",
                    str(self.widgets[3].value())]
        if t == "energy":
            return [str(self.widgets[0].value()), self.widgets[1].currentText(),
                    self.widgets[2].currentText()]
        if t == "grabin":
            return [self.widgets[0].currentText(), str(self.widgets[1].value())]
        return None

    def _all_widgets(self):
        """本字段涉及的全部控件（屏蔽信号时用）。"""
        ws = list(self.widgets)
        for attr in ("ck", "ecb", "le"):
            w = getattr(self, attr, None)
            if w is not None:
                ws.append(w)
        return ws

    def refresh(self, args, inherit=False):
        """把文档里的值回写到控件（全程屏蔽信号，不会被当成用户改值）。

        inherit=True：这个值不是本帧写的、是**从前面帧继承**来的，
        给值控件刷淡黄底提醒（和 delay / offset / bbox 一致的提示方式）。
        """
        with blocked(*self._all_widgets()):
            self._refresh(args)
            self._mark_inherit(inherit)

    def _mark_inherit(self, inherit):
        """给“值”控件刷/清淡黄底（勾选框、标签不刷）。"""
        css = "background:#fdf3c0;color:#6b5520;" if inherit else ""
        for w in self.widgets:
            try:
                w.setStyleSheet(css)
            except RuntimeError:
                pass
        le = getattr(self, "le", None)
        if le is not None:
            try:
                le.setStyleSheet(css)
            except RuntimeError:
                pass

    def _refresh(self, args):
        t = self.spec[0] if isinstance(self.spec, tuple) else self.spec
        if t == "bi":
            self.ck.setChecked(bool(args) and args[0] == "1")
            return
        present = bool(args) if t in ("path", "name") else args is not None
        self.ecb.setChecked(present)
        self._set_widgets_enabled(present)
        if t == "int":
            self.widgets[0].setValue(_to_int(args[0]) if args else 0)
        elif t == "ints":
            vals = list(args or []) + ["0"] * len(self.widgets)
            for w, v in zip(self.widgets, vals):
                w.setValue(_to_int(v))
        elif t == "combo":
            if args:
                i = self.widgets[0].findText(args[0])
                self.widgets[0].setCurrentIndex(max(0, i))
            else:
                self.widgets[0].setCurrentIndex(0)
        elif t in ("path", "name"):
            self.le.setText(args[0] if args else "")
        elif t == "cancel":
            vals = list(args or []) + ["0", "0", "0", "", "1"]
            vals = vals[:5]
            self.widgets[0].setValue(_to_int(vals[0]))
            self.widgets[1].setValue(_to_int(vals[1]))
            self.widgets[2].setValue(_to_int(vals[2]))
            self.le.setText(vals[3])
            self.widgets[3].setValue(_to_int(vals[4], 1))
        elif t == "energy":
            vals = list(args or []) + ["0", "0", "0"]
            self.widgets[0].setValue(_to_int(vals[0]))
            for c, v in zip(self.widgets[1:], vals[1:3]):
                c.setCurrentIndex(max(0, c.findText(v)))
        elif t == "grabin":
            vals = list(args or []) + ["0", "0"]
            self.widgets[0].setCurrentIndex(
                max(0, self.widgets[0].findText(vals[0])))
            self.widgets[1].setValue(_to_int(vals[1]))

    # ---- 事件 ----
    def _browse(self):
        filt = "音频 (*.wav);;所有文件 (*)" \
            if self.spec[1] == "wav" else "图片 (*.png);;所有文件 (*)"
        path, _ = QFileDialog.getOpenFileName(
            self.panel, f"选择文件（{self.cmd}）", self.panel.data_dir or "", filt)
        if path:
            self.le.setText(self.panel._rel_path(path, keep_ext=True))

    def _enable_toggled(self, state):
        if self.panel._loading:
            return
        on = bool(state)
        self._set_widgets_enabled(on)
        if not on:                          # 取消勾选 = 删除命令行
            self._delete_line()
            self.panel._refresh_frame_ui()

    def _changed(self):
        if self.panel._loading:
            return
        # 帧号不得超过当前动画总帧数，否则弹窗拒绝
        n = self.panel.anim_frame_count()
        if n is not None and self._idx_widgets:
            for w in self._idx_widgets:
                if w.value() > n:
                    QMessageBox.warning(
                        self.panel, "帧号超出范围",
                        f"“{self.cmd}” 的帧号（{w.value()}）超过了当前动画"
                        f"的总帧数（{n}），已拒绝修改。")
                    w.revert()
                    return
        args = self.read()
        if args is None:
            self._delete_line()
        elif self.frame_level:
            self.panel._set_frame_prop(self.cmd, args)
        else:
            self.panel._set_anim_cmd(self.cmd, args)
        self.panel._refresh_frame_ui()

    def _delete_line(self):
        if self.frame_level:
            self.panel._del_frame_cmd(self.cmd)
        else:
            self.panel._set_anim_cmd(self.cmd, None)


# ============================================================ 文本编辑高亮



# ====================================================================
# 原 bor_text.py
# ====================================================================

# 特殊攻击类型（替代 attack{#} 的独立命令）
SPECIAL_ATTACK_TYPES = ["burn", "shock", "blast", "freeze", "steal"]


# ============================================================ 一键优化
def _split_comment(line):
    i = line.find("#")
    if i < 0:
        return line, ""
    return line[:i].rstrip(), line[i:]


def _brace_delta(s):
    d = 0
    for ch in s:
        if ch == "{":
            d += 1
        elif ch == "}":
            d -= 1
    return d


def _tidy_code(s):
    """脚本行内部：多个空白压缩为一个空格。"""
    return re.sub(r"[ \t]+", " ", s).strip()


def _num_zero(v):
    try:
        return float(v) == 0.0
    except (ValueError, TypeError):
        return False


# anim 必须后跟空格/制表/行尾才算关键字（否则 animationscript 会被误判）
_ANIM_START_RE = re.compile(r"anim(\s|$)")
_ATTACK_RE = re.compile(r"attack\d*$")


def format_bor_text(text):
    """一键优化：规整缩进与空白（只在编辑器内处理，写文件仍需确认）。
    - anim / Header 顶格；anim 内统一缩进 1 个制表符
    - Header 字段与值之间 2 个制表符（字段名超过 9 字符时只用 1 个）；
      anim 内关键字与值之间 1 个制表符
    - 每个 anim 之前强制空 2 行（不会被压缩成 1 行）
    - 去冗余：与上一次生效值完全相同的 offset / bbox / attack 声明会被删除；
      动画内首次出现的 offset / bbox / attack 若 x,y,w,h 全为 0（或未设置）
      同样视为冗余删除
    - @script … @end_script 内每行至少多缩进 1 个制表符
    - 连续空行压成一个；全空白行视作空行
    """
    raw = text.splitlines()
    out = []
    blank = 0
    in_anim = False
    prev = {"offset": None, "bbox": None, "attack": None}
    seen = {"offset": False, "bbox": False, "attack": False}
    i = 0
    while i < len(raw):
        line = raw[i].rstrip()
        i += 1
        s = line.strip()
        if not s:
            if out and blank == 0:
                out.append("")
            blank += 1
            continue
        blank = 0
        low = s.lower()
        code, comment = _split_comment(s)
        code = code.strip()
        if not code:                       # 纯注释行
            out.append(("\t" if in_anim else "") + s)
            continue
        base = "\t" if in_anim else ""
        # ---- 全局 / anim 内脚本块 ----
        # 块开头两种写法：单独一行 "@script"，或 "script   @script"
        ct = code.split()
        if ct and is_script_start(ct[0].lower(), ct[1:]):
            out.append(base + code)      # 原样保留开头行（可能是 script @script）
            depth = 0
            while i < len(raw):
                sl = raw[i].rstrip()
                i += 1
                ss = sl.strip()
                if is_script_end(ss.split()[0].lower() if ss.split() else ""):
                    out.append(base + "@end_script")
                    break
                if not ss:
                    out.append("")
                    continue
                if ss.startswith("}") or ss.startswith(")"):
                    depth = max(0, depth - 1)
                out.append(base + "\t" * (1 + depth) + _tidy_code(ss))
                depth = max(0, depth + _brace_delta(ss))
            continue
        if _ANIM_START_RE.match(low):
            in_anim = True
            prev = {"offset": None, "bbox": None, "attack": None}
            seen = {"offset": False, "bbox": False, "attack": False}
            while out and out[-1] == "":
                out.pop()
            if out:                        # Header 与 anim 之间强制空 2 行
                out.append("")
                out.append("")
            out.append("anim " + " ".join(code.split()[1:]))
            continue
        tok = code.split()
        cmd = tok[0]
        args = tok[1:]
        cl = cmd.lower()
        # ---- 去冗余（只针对 anim 内的 offset / bbox / attack）----
        kind = None
        if cl == "offset":
            kind = "offset"
        elif cl == "bbox":
            kind = "bbox"
        elif _ATTACK_RE.match(cl) or cl in SPECIAL_ATTACK_TYPES:
            kind = "attack"
        if kind and in_anim:
            key = (cl, tuple(args)) if kind == "attack" else tuple(args)
            need = 2 if kind == "offset" else 4
            if not seen[kind]:
                seen[kind] = True
                vals = args[:need]
                if len(args) < need or (vals and all(_num_zero(v) for v in vals)):
                    prev[kind] = key       # 首次声明即全 0 / 未设置 → 冗余
                    continue
            elif prev[kind] is not None and prev[kind] == key:
                continue                   # 与上一次生效值相同 → 冗余
            prev[kind] = key
        # Header 字段较长（>9 字符）时只用一个制表符，避免值被推得太远
        if in_anim:
            sep = "\t"
        else:
            sep = "\t" if len(cmd) >= HEADER_TAB_WIDTH else "\t\t"
        body = base + cmd + (sep + " ".join(args) if args else "")
        if comment:
            body += " " + comment.strip()
        out.append(body)
    res = "\n".join(out)
    if not res.endswith("\n"):
        res += "\n"
    return res


# ============================================================ 文本编辑高亮
class BorHighlighter(QSyntaxHighlighter):
    """三类关键字三种颜色；注释灰色斜体。

    - Header 段字段（含 animationscript 之类 script 指令）  → 蓝色
    - 动画 Header 级命令（anim/loop/hitfx/jumpframe…）      → 青色
    - 帧级命令（delay/offset/bbox/move/sound…）             → 绿色
    - attack 及攻击相关设置（attack#/blast/forcedirection…） → 红色
    每行只识别第一个关键字（后面都是参数或注释），且关键字必须是完整的
    首 token（animationscript 不会被当成 anim）。
    @cmd 开头的行整行紫色；@script … @end_script 区块整段紫色。
    """

    def __init__(self, parent, keywords=None):
        super().__init__(parent)
        self.kw_fmt = {}          # 类别 -> QTextCharFormat
        for kind, (day, night) in KW_COLORS.items():
            f = QTextCharFormat()
            f.setFontWeight(QFont.Bold)
            f.setForeground(QColor(day))
            self.kw_fmt[kind] = f
        self._colors = dict(KW_COLORS)
        self.cm_fmt = QTextCharFormat()
        self.cm_fmt.setFontItalic(True)
        self.cm_fmt.setForeground(QColor(138, 143, 152))
        self.sc_fmt = QTextCharFormat()
        self.sc_fmt.setForeground(QColor(139, 70, 199))
        self._night = False

    def set_night(self, on):
        """夜间模式配色。"""
        self._night = on
        idx = 1 if on else 0
        for kind, pair in KW_COLORS.items():
            self.kw_fmt[kind].setForeground(QColor(pair[idx]))
        self.cm_fmt.setForeground(
            QColor(122, 130, 144) if on else QColor(138, 143, 152))
        self.sc_fmt.setForeground(
            QColor(199, 146, 234) if on else QColor(139, 70, 199))
        self.rehighlight()

    def _classify(self, word):
        w = word.lower()
        if w in ATTACK_KWS:
            return "attack"
        if w in ANIM_HDR_KWS:
            return "animhdr"          # 动画 Header 级：和帧级命令区分开
        if w in ANIM_KWS:
            return "anim"
        if w in HEADER_KWS:
            return "header"
        return None

    def highlightBlock(self, text):
        state = self.previousBlockState()
        if state < 0:
            state = 0
        stripped = text.strip().lower()
        code = text
        ci = text.find("#")
        if ci >= 0:
            code = text[:ci]
        tok0 = code.strip().split()
        if state == 1:
            self.setFormat(0, len(text), self.sc_fmt)
            self.setCurrentBlockState(
                0 if is_script_end(tok0[0].lower() if tok0 else "") else 1)
            return
        self.setCurrentBlockState(0)
        # 块开头：@script，或 script @script（整块都是 script 字段的值）
        if tok0 and is_script_start(tok0[0].lower(), tok0[1:]):
            self.setFormat(0, len(text), self.sc_fmt)
            self.setCurrentBlockState(1)
            return
        if stripped.startswith("@cmd"):
            self.setFormat(0, len(text), self.sc_fmt)
            return
        if ci >= 0:
            self.setFormat(ci, len(text) - ci, self.cm_fmt)
        if not tok0:
            return
        kind = self._classify(tok0[0])
        if kind is None:
            return
        # 第一关键字 = 行内第一个 token，其后必然是空白或行尾
        pos = code.index(tok0[0])
        self.setFormat(pos, len(tok0[0]), self.kw_fmt[kind])


# ============================================================ 源码编辑框


# ============================================================ 源码编辑框
class BorTextEdit(QTextEdit):
    """Ctrl+D 原地复制当前行；Ctrl+3 / Ctrl+Q 注释（/取消注释）选中行。
    当前行淡色高亮；flash_line() 可跳到指定行并做黄色闪烁提示。"""

    FLASH_STEPS = 7

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._night = False
        self._flash_steps = 0
        self._flash_blocks = []          # [(行号, 基础色)]
        self._err_marks = []             # [(行号, QColor)] 持久错误标记
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(95)
        self._flash_timer.timeout.connect(self._flash_tick)
        self.cursorPositionChanged.connect(self._update_extra)
        self.textChanged.connect(self._update_extra)
        self._fsize = self.fontPointSize() or 10.0

    # ---- 行背景 ----
    def _mk_sel(self, block_no, color):
        b = self.document().findBlockByNumber(block_no)
        if not b.isValid():
            return None
        sel = QTextEdit.ExtraSelection()
        sel.cursor = QTextCursor(b)
        sel.format.setBackground(color)
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        return sel

    def set_night(self, on):
        self._night = on
        self._update_extra()

    def set_error_lines(self, marks):
        """持久标红某些行（错误标记，直到下次调用才消失）。
        marks = [(行号, QColor), ...]；传空列表清除。"""
        self._err_marks = [(b, QColor(c)) for b, c in marks]
        self._update_extra()

    def _update_extra(self):
        sels = []
        cur = self.textCursor()
        base = QColor("#2b3440") if self._night else QColor("#e8f1fb")
        s = self._mk_sel(cur.blockNumber(), base)
        if s is not None:
            sels.append(s)
        for blk, col in self._err_marks:
            s = self._mk_sel(blk, col)
            if s is not None:
                sels.append(s)
        if self._flash_steps > 0:
            a = self._flash_steps / float(self.FLASH_STEPS)
            for blk, col in self._flash_blocks:
                c = QColor(col)
                c.setAlpha(int(40 + 165 * a))
                s = self._mk_sel(blk, c)
                if s is not None:
                    sels.append(s)
        self.setExtraSelections(sels)

    def _flash_tick(self):
        self._flash_steps -= 1
        if self._flash_steps <= 0:
            self._flash_timer.stop()
            self._flash_steps = 0
        self._update_extra()

    def flash_line(self, block_no, color=None, focus=True, top=None):
        """跳到第 block_no 行（0 起）并闪烁高亮；color 决定闪光颜色。

        top=True 时把该行顶到**窗口最上面**，这样它下面的内容能一次看全。
        """
        return self.flash_lines(
            [(block_no, color or QColor(255, 226, 90))], focus, top)

    def _scroll_to_top(self, block_no):
        """把第 block_no 行滚到视口最上面；靠近文件末尾时滚到底为止。

        直接算：先让光标落过去（顺便触发排版），再按它此刻在视口里的
        位置把滚动条往下推同样的距离。文件末尾滚不动了就自然留在原处。
        """
        b = self.document().findBlockByNumber(block_no)
        if not b.isValid():
            return
        cur = QTextCursor(b)
        self.setTextCursor(cur)
        self.ensureCursorVisible()        # 先保证它可见，量出来的坐标才有效
        r = self.cursorRect(cur)
        sb = self.verticalScrollBar()
        sb.setValue(min(sb.value() + r.top(), sb.maximum()))

    def flash_lines(self, marks, focus=True, top=None):
        """同时闪烁多行：marks = [(行号, QColor), ...]。

        对照模式下左侧编辑触发的跳转不该抢走焦点（否则没法连续改），
        所以 focus=False；此时默认把目标行顶到窗口最上面（top=True），
        方便一眼看到选中动画 / Header 后面的内容。
        """
        if top is None:
            top = not focus
        marks = [(b, c) for b, c in marks
                 if 0 <= b < self.document().blockCount()]
        if not marks:
            return False
        first = min(b for b, _ in marks)
        b = self.document().findBlockByNumber(first)
        cur = QTextCursor(b)
        cur.movePosition(QTextCursor.EndOfBlock)
        self.setTextCursor(cur)
        self.ensureCursorVisible()
        if top:
            self._scroll_to_top(first)
        self._flash_blocks = marks
        self._flash_steps = self.FLASH_STEPS
        self._flash_timer.start()
        self._update_extra()
        if focus:
            self.setFocus()
        return True

    def set_plain_keep(self, text):
        """替换全文但保留当前行号。"""
        line = self.textCursor().blockNumber()
        self.setPlainText(text)
        self.flash_line(min(line, self.document().blockCount() - 1))

    # ---- Ctrl + 滚轮：缩放显示字号 ----
    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self._fsize = min(28.0, max(6.0, self._fsize + (0.5 if d > 0
                                                                else -0.5)))
                f = self.font()
                f.setPointSizeF(self._fsize)
                self.setFont(f)
                e.accept()
                return
        super().wheelEvent(e)

    def _blocks(self):
        cur = self.textCursor()
        doc = self.document()
        if cur.hasSelection():
            b = doc.findBlock(cur.selectionStart())
            last = doc.findBlock(cur.selectionEnd())
            out = []
            while b.isValid():
                out.append(b)
                if b == last:
                    break
                b = b.next()
            return out
        return [cur.block()]

    def toggle_comment(self):
        live = [(b, b.text()) for b in self._blocks()]
        live = [(b, t) for b, t in live if t.strip()]     # 空行不参与
        if not live:
            return
        commented = all(t.lstrip().startswith("#") for _, t in live)
        cur = self.textCursor()
        cur.beginEditBlock()
        for b, t in live:
            c = QTextCursor(b)
            if commented:                                  # 去掉 #
                i = len(t) - len(t.lstrip())
                j = t.index("#", i)
                c.setPosition(b.position() + j)
                c.deleteChar()
                if t[j + 1:j + 2] == " ":
                    c.deleteChar()
            else:                                          # 加 #
                i = len(t) - len(t.lstrip())
                c.setPosition(b.position() + i)
                c.insertText("# ")
        cur.endEditBlock()

    def dup_line(self):
        cur = self.textCursor()
        b = cur.block()
        cur.beginEditBlock()
        cur.movePosition(QTextCursor.EndOfBlock)
        cur.insertText("\n" + b.text())
        cur.endEditBlock()
        self.setTextCursor(cur)

    def keyPressEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            if e.key() in (Qt.Key_3, Qt.Key_Q):
                self.toggle_comment()
                return
            if e.key() == Qt.Key_D:
                self.dup_line()
                return
        super().keyPressEvent(e)


# ============================================================ 语法高亮
# ---- 文本高亮用的三类关键字（浅色 / 夜间两套配色）----
# 这里只关心“名字”，不关心控件长什么样，所以直接把名字抄一遍。
# 新增字段时：在 openbor_ui.py 里加控件，顺便把名字补到下面对应的集合里。
HEADER_KWS = set(HEADER_CMDS) | set(SCRIPT_EVENTS)

# 动画 Header 级命令：写在 `anim xxx` 之后、第一帧之前，管的是**整条动画**。
# 它们和帧级命令（delay / offset / bbox / move…）不是一个层级，
# 所以高亮用另一种颜色区分（见 BorHighlighter 的 "animhdr"）。
ANIM_HDR_KWS = {
    "anim",
    # ---- 常用 ----
    "loop", "hitfx", "hitflash", "followanim", "followcond", "cancel",
    "range", "subentity", "fastattack", "attackone", "mponly", "energycost",
    # ---- 不常用 ----
    "blockfx", "blockflash", "rangez", "rangea", "rangeb", "dive",
    "animheight", "sync", "chargetime", "counterattack", "counterframe",
    "counterrange", "ignoreattackid", "bouncefactor", "custentity",
    "custknife", "custstar", "custboomerang", "custbomb", "custpbomb",
    "pshotframe", "shootframe", "throwframe", "tossframe", "flipframe",
    "quakeframe", "spawnframe", "summonframe", "unsummonframe",
    "weaponframe",
    # ---- 跳帧 ----
    "jumpframe", "dropframe", "landframe",
}

# 帧级命令：只作用于它下面那一帧（也有继承性）。
ANIM_KWS = {
    # ---- 帧基础 ----
    "delay", "offset", "bbox", "frame", "drawmethod", "nodrawmethod",
    # ---- 帧附加 ----
    "move", "movea", "movez", "seta", "sound", "idle", "fshadow",
    "shadowcoords", "itembox", "platform", "alphamask",
} | ANIM_HDR_KWS

ATTACK_KWS = {
    # ---- 攻击框本体 ----
    "attack", "attack1", "attack2", "attack3", "attack4", "attack5",
    "attack6", "attack7", "attack8", "attack9", "attack10",
    # ---- 特殊攻击类型 ----
    "burn", "shock", "blast", "freeze", "steal",
    # ---- 攻击框附加 ----
    "grabin", "jugglecost", "otg", "dropv", "staydown", "forcedirection",
    "guardcost", "seal", "stun", "noreflect", "forcemap", "dot", "nokill",
    "damageonlanding",
}
KW_COLORS = {                      # 类别 -> (浅色, 夜间)
    "header":  ("#1f5fd0", "#7fb3ff"),
    "anim":    ("#0f7a52", "#5fd0a0"),      # 帧级命令
    "animhdr": ("#12667a", "#63c7db"),      # 动画 Header 级命令（另一种色）
    "attack":  ("#c0392b", "#ff8a7a"),
}



# ====================================================================
# 原 bor_preview.py
# ====================================================================

TICK_MS = 10                 # 一个“游戏刻”= 10ms（OpenBOR 的 delay 单位是厘秒）

# 水平翻转后的镜像图缓存（rel 路径 → QImage）
_MIRROR = {}


def mirror_image(panel, rel):
    """取左右镜像后的图（带缓存）。"""
    if not rel or rel.lower() == "none":
        return None
    if rel in _MIRROR:
        return _MIRROR[rel]
    img = panel._sim_image(rel)
    m = img.mirrored(True, False) if img is not None else None
    _MIRROR[rel] = m
    if len(_MIRROR) > 300:
        _MIRROR.clear()
    return m
DEFAULT_Z_MIN, DEFAULT_Z_MAX = -140, 140
GRID_Z = 20                  # 深度网格间距（z 单位）


# ============================================================ 演员
class Actor:
    """场景里的一个实体：主实体、假人、生成的实体都用它。"""

    def __init__(self, doc, name, panel, x=0.0, z=0.0):
        self.doc = doc
        self.name = name or ""
        self.panel = panel
        self.base_x, self.base_z = float(x), float(z)
        self.anim = None
        self.frames = []
        self.frame = 0
        self.wait = 0
        self.loop = False
        self.loop_start = 0
        self.done = False
        self.track = None        # MotionTrack（跟随动画）
        self.state = ""          # 假人用：idle / pain / fall / rise
        self.flash = 0           # 白闪剩余刻数
        self.life = None         # 有限寿命（生成物 / 特效）
        self.is_fx = False
        self.flipx = False       # 左右翻转（右键菜单切换）

    # ---- 动画切换 ----
    def set_anim(self, anim_name, loop=False, track=True):
        a = bs.anim_of(self.doc, anim_name) if self.doc else None
        if a is None:
            return False
        self.anim = a
        self.frames = a.frames()
        if not self.frames:
            return False
        self.frame = 0
        self.loop = loop
        self.loop_start = bs.loop_start(a)
        self.done = False
        self.wait = bs.delay_of(self.frames[0])
        self.track = bs.MotionTrack(a, None) if track else None
        return True

    # ---- 推进 ----
    def tick(self):
        if self.wait > 0:
            self.wait -= 1
            return False
        self._advance()
        return True

    def _advance(self):
        if not self.frames:
            return
        nxt = self.frame + 1
        if nxt >= len(self.frames):
            if self.loop:
                nxt = min(self.loop_start, len(self.frames) - 1)
            else:
                self.done = True
                nxt = len(self.frames) - 1
        self.frame = nxt
        self.wait = bs.delay_of(self.frames[nxt])

    # ---- 当前姿态 ----
    @property
    def cur(self):
        if not self.frames:
            return None
        return self.frames[min(self.frame, len(self.frames) - 1)]

    def offset(self):
        """原始 Offset（未考虑翻转）。"""
        fr = self.cur
        if fr is None or not fr["offset"]:
            return (0, 0)
        try:
            return (int(float(fr["offset"][0])),
                    int(float(fr["offset"][1])))
        except (ValueError, IndexError):
            return (0, 0)

    def draw_offset(self):
        """翻转后真正用来摆图的 Offset（左右翻转时按图片宽度镜像）。"""
        ox, oy = self.offset()
        if self.flipx:
            w = self.img_width()
            if w:
                ox = w - ox
        return (ox, oy)

    def motion(self):
        """(dx, dz, dy)：相对动画起点的水平 / 深度位移与离地高度。"""
        if self.track is None:
            return (0.0, 0.0, 0.0)
        return self.track.at(self.frame)[:3]

    def pos(self):
        """(x, z, y)：场景水平位置、深度、离地高度。"""
        dx, dz, dy = self.motion()
        return (self.base_x + dx, self.base_z + dz, dy)

    def scene_box(self, box):
        px, z, y = self.pos()
        ox, oy = self.draw_offset()
        bx, by, bw, bh, d = box
        if self.flipx:
            w = self.img_width()
            if w:
                bx = w - (bx + bw)       # 框也跟着镜像
        return bs.scene_box((bx, by, bw, bh, d), px, z - y, ox, oy, z)

    def img_width(self):
        img = self.image()
        return img.width() if img is not None else 0

    def image(self):
        """当前帧的图（翻转时返回镜像图）。"""
        fr = self.cur
        if fr is None:
            return None
        return (mirror_image(self.panel, fr["path"]) if self.flipx
                else self.panel._sim_image(fr["path"]))


# ============================================================ 场景画布
class SceneCanvas(QWidget):
    """还原 OpenBOR 关卡场景：背景墙 + 地面 + z 深度网格 + 实体。"""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setMinimumSize(420, 300)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setMouseTracking(True)
        self.scale = 1.0
        self.floor_top = 90
        self.z_min, self.z_max = DEFAULT_Z_MIN, DEFAULT_Z_MAX
        self.cam_x = 0.0
        self.grid = GRID_Z
        self.show_grid = True
        self.show_boxes = True
        self.show_shadow = True
        self.follow = True
        self._drag = None
        self._auto = True

    # ---- 坐标 ----
    def z_to_y(self, z):
        return self.floor_top + (z - self.z_min) * self.scale

    def x_to_x(self, x):
        return self.width() * 0.5 + (x - self.cam_x) * self.scale

    def to_screen(self, x, z):
        return QPointF(self.x_to_x(x), self.z_to_y(z))

    def to_level(self, p):
        return ((p.x() - self.width() * 0.5) / self.scale + self.cam_x,
                (p.y() - self.floor_top) / self.scale + self.z_min)

    def fit(self):
        """自动缩放：让整条 z 范围刚好铺满画布。"""
        h = max(120, self.height())
        self.floor_top = int(h * 0.24)
        span = max(1.0, self.z_max - self.z_min)
        self.scale = max(0.15, (h - self.floor_top - 24) / span)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._auto:
            self.fit()
        else:
            self.floor_top = int(self.height() * 0.24)

    # ---- 画 ----
    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        # 背景墙
        gw = QLinearGradient(0, 0, 0, max(1, self.floor_top))
        gw.setColorAt(0.0, QColor(38, 48, 70))
        gw.setColorAt(1.0, QColor(72, 84, 106))
        p.fillRect(0, 0, w, max(0, self.floor_top), QBrush(gw))
        # 地面
        gf = QLinearGradient(0, self.floor_top, 0, h)
        gf.setColorAt(0.0, QColor(74, 78, 88))
        gf.setColorAt(0.55, QColor(102, 106, 116))
        gf.setColorAt(1.0, QColor(130, 132, 140))
        p.fillRect(0, self.floor_top, w, h - self.floor_top, QBrush(gf))
        # 墙脚线
        p.setPen(QPen(QColor(28, 30, 36), 2))
        p.drawLine(0, self.floor_top, w, self.floor_top)
        # z 范围之外（太靠近镜头）压暗
        y_far = self.z_to_y(self.z_max)
        if y_far < h:
            p.fillRect(0, int(y_far), w, h - int(y_far),
                       QColor(0, 0, 0, 110))
            p.setPen(QPen(QColor(220, 120, 110), 1, Qt.DashLine))
            p.drawLine(0, int(y_far), w, int(y_far))
            p.setPen(QColor(235, 170, 165))
            p.drawText(QRectF(6, int(y_far) - 15, 200, 14),
                       Qt.AlignVCenter, f"z 上限 {self.z_max}")
        if self.show_grid:
            self._draw_grid(p, w, h)
        self._draw_ruler(p)
        # 实体：z 小的（远的）先画
        acts = [a for a in self.win.actors if a.cur is not None]
        acts.sort(key=lambda a: a.pos()[1])
        for a in acts:
            self._draw_actor(p, a)
        # move 位移：从起点（base）画粗箭头指到当前位置
        for a in acts:
            dx, dz, dy = a.motion()
            if abs(dx) < 0.5 and abs(dz) < 0.5 and abs(dy) < 0.5:
                continue
            o = QPointF(self.x_to_x(a.base_x), self.z_to_y(a.base_z))
            c = QPointF(self.x_to_x(a.base_x + dx),
                        self.z_to_y(a.base_z + dz) - dy * self.scale)
            if (c.x() - o.x()) ** 2 + (c.y() - o.y()) ** 2 < 30.0:
                continue
            p.setPen(QPen(QColor(255, 208, 96), 2, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(o.x() - 6, o.y() - 3, 12, 6))
            draw_arrow(p, o, c, QColor(255, 190, 60), width=4, head=15)
        self._draw_fx(p)
        self._draw_hud(p)

    def _draw_grid(self, p, w, h):
        z0 = int(self.z_min // self.grid) * self.grid
        z = z0
        while z <= self.z_max + 0.5:
            y = self.z_to_y(z)
            if y >= self.floor_top - 1:
                major = (int(round(z)) % (self.grid * 5) == 0)
                p.setPen(QPen(QColor(255, 255, 255, 54 if major else 26), 1))
                p.drawLine(0, int(y), w, int(y))
            z += self.grid
        # 纵向参考线（跟着相机走，用来看水平位移）
        gx = 60
        x0 = int(self.cam_x // gx) * gx - gx * 6
        x = x0
        while x < self.cam_x + gx * 8:
            sx = self.x_to_x(x)
            if -1 <= sx <= w + 1:
                p.setPen(QPen(QColor(255, 255, 255, 20), 1))
                p.drawLine(int(sx), self.floor_top, int(sx), h)
            x += gx

    def _draw_ruler(self, p):
        fnt = QFont(self.font())
        fnt.setPointSize(max(7, self.font().pointSize() - 2))
        p.setFont(fnt)
        z0 = int(self.z_min // self.grid) * self.grid
        z = z0
        while z <= self.z_max + 0.5:
            y = self.z_to_y(z)
            if y >= self.floor_top - 1 and int(round(z)) % (self.grid * 2) == 0:
                p.setPen(QColor(215, 226, 240, 190))
                p.drawText(QRectF(2, int(y) - 7, 52, 14),
                           Qt.AlignVCenter, f"z{int(round(z)):+d}")
            z += self.grid
        p.setFont(self.font())

    def _draw_actor(self, p, a):
        fr = a.cur
        img = a.image()
        px, z, y = a.pos()
        ox, oy = a.draw_offset()
        sx, sy = self.x_to_x(px), self.z_to_y(z)
        if self.show_shadow:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 120))
            rw = max(10, 26 * self.scale) * (1.0 - min(0.55, y / 260.0))
            p.drawEllipse(QRectF(sx - rw, sy - 3.5 * self.scale * 0.6,
                                 rw * 2, 7 * self.scale * 0.6))
        if img is not None:
            tl = QPointF(sx - ox * self.scale, sy - (oy + y) * self.scale)
            old = p.opacity()
            if a.is_fx:
                p.setOpacity(0.85)
            p.drawImage(QRectF(tl, QPointF(tl.x() + img.width() * self.scale,
                                           tl.y() + img.height() * self.scale)),
                        img)
            if a.flash > 0:
                # 白闪：只在精灵不透明的地方盖一层白（SourceAtop）
                p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
                p.setOpacity(min(1.0, a.flash / 6.0))
                p.fillRect(QRectF(tl, QPointF(
                    tl.x() + img.width() * self.scale,
                    tl.y() + img.height() * self.scale)), QColor(255, 255, 255))
                p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setOpacity(old)
        if self.show_boxes:
            bb = bs.bbox_of(fr)
            if bb:
                self._box(p, a.scene_box(bb), QColor(60, 140, 255))
            ab = bs.attack_box(fr)
            if ab:
                self._box(p, a.scene_box(ab[:4] + (ab[6] or 40,)),
                          QColor(255, 70, 70))
        # 名字
        if a.name and not a.is_fx:
            p.setPen(QColor(235, 240, 250))
            p.drawText(QRectF(sx - 60, sy - (oy + y) * self.scale - 16,
                              160, 14),
                       Qt.AlignCenter, a.name)

    def _box(self, p, sb, color):
        a = QPointF(self.x_to_x(sb[0]), self.z_to_y(sb[2]))
        b = QPointF(self.x_to_x(sb[1]), self.z_to_y(sb[3]))
        p.setPen(QPen(color, 1))
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 45))
        p.drawRect(QRectF(a, b))

    def _draw_fx(self, p):
        for fx in self.win.fx:
            if fx["kind"] != "burst":
                continue
            t = fx["t"]
            life = max(1, fx["life"])
            k = t / life
            sx, sy = self.x_to_x(fx["x"]), self.z_to_y(fx["y"])
            r = (6 + 26 * k) * max(0.6, self.scale * 0.9)
            p.setPen(QPen(QColor(255, 240, 150, int(230 * (1 - k))), 2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(sx - r, sy - r * 0.62, r * 2, r * 1.24))
            p.setPen(QPen(QColor(255, 190, 90, int(220 * (1 - k))), 2))
            for i in range(6):
                ang = math.pi * i / 6.0 + k * 0.9
                p.drawLine(QPointF(sx, sy),
                           QPointF(sx + math.cos(ang) * r * 1.35,
                                   sy + math.sin(ang) * r * 0.8))

    def _draw_hud(self, p):
        w = self.win
        main = w.main
        lines = []
        if main is not None:
            px, z, y = main.pos()
            dx, dz, dy = main.motion()
            lines.append(
                f"{main.anim.name}  帧 {main.frame + 1}/{len(main.frames)}"
                f"　x {px:+.0f}　z {z:+.0f}　离地 {y:.0f}"
                f"　位移 ({dx:+.0f},{dz:+.0f})")
        v = w.victim
        if v is not None:
            px, z, _ = v.pos()
            lines.append(f"假人 {v.name}　{v.state or 'idle'}　"
                         f"x {px:+.0f}　z {z:+.0f}")
        lines.append("空格播放 / Ctrl+空格循环 / 左右单帧　·　滚轮缩放　·　"
                     "左键拖假人换位置　·　右键假人：左右翻转 / 复位 / 移除")
        p.setPen(QColor(240, 244, 252))
        for i, t in enumerate(lines):
            p.drawText(QRectF(6, 4 + i * 15, self.width() - 12, 15),
                       Qt.AlignLeft | Qt.AlignVCenter, t)

    # ---- 交互 ----
    def _victim_hit(self, pos):
        v = self.win.victim
        if v is None:
            return False
        px, z, y = v.pos()
        sx, sy = self.x_to_x(px), self.z_to_y(z)
        return abs(pos.x() - sx) < 46 and -70 < pos.y() - sy < 12

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.RightButton and self._victim_hit(e.pos()):
            self._victim_menu(e.pos())
            return
        if e.button() == Qt.LeftButton and self._victim_hit(e.pos()):
            self._drag = ("victim", self.to_level(e.pos()),
                          self.win.victim.base_x, self.win.victim.base_z)
            return
        if e.button() == Qt.LeftButton:
            self._drag = ("pan", e.pos(), self.cam_x)

    def _victim_menu(self, pos):
        """右键点假人：左右翻转 / 复位 / 移除。"""
        v = self.win.victim
        if v is None:
            return
        m = QMenu(self)
        act_flip = m.addAction(("✔ 左右翻转" if v.flipx else "左右翻转"))
        act_home = m.addAction("回到默认位置")
        m.addSeparator()
        act_del = m.addAction("移除这个假人")
        got = m.exec_(self.mapToGlobal(pos))
        if got is None:
            return
        if got is act_flip:
            v.flipx = not v.flipx
        elif got is act_home:
            v.base_x, v.base_z = 100.0, 0.0
            v.flipx = False
        elif got is act_del:
            self.win.cmb_victim.setCurrentIndex(0)
        self.update()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        if self._drag[0] == "victim":
            _, start, bx, bz = self._drag
            cur = self.to_level(e.pos())
            self.win.victim.base_x = bx + (cur[0] - start[0])
            self.win.victim.base_z = bz + (cur[1] - start[1])
            self.update()
        else:
            _, start, cx = self._drag
            self.cam_x = cx - (e.pos().x() - start.x()) / self.scale
            self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        mods = e.modifiers()
        if mods & Qt.ControlModifier:
            self._auto = False
            self.scale = min(8.0, max(0.15,
                                      self.scale * (1.2 if d > 0 else 0.83)))
            self.update()
        elif mods & Qt.ShiftModifier:
            self.cam_x += (-40 if d > 0 else 40) / self.scale
            self.update()
        else:
            self.z_min += (-8 if d > 0 else 8)
            self.z_max += (8 if d > 0 else -8)
            self._auto = False
            self.update()


# ============================================================ 时间轴宿主
class _Host:
    """让 BorTimeline 能直接驱动预览窗口（接口与主面板一致）。"""

    def __init__(self, win):
        self.win = win

    def current_anim(self):
        return self.win.anim

    @property
    def frame_idx(self):
        return self.win.main.frame if self.win.main else 0

    def set_frame_idx(self, i):
        self.win.seek(i)

    @property
    def _play_mode(self):
        return self.win.play_mode

    def start_play(self, mode):
        self.win.start_play(mode)

    def stop_play(self, restore=False):
        self.win.stop_play()

    def anim_markers(self):
        return self.win.markers()

    @property
    def root_dirs(self):
        return self.win.panel.root_dirs

    def edit_frame_script(self, i):
        pass


# ============================================================ 预览窗口
class AttackPreview(QWidget):
    """攻击预览窗口（非模态，可以一直开着改参数）。"""

    def __init__(self, panel, anim=None):
        super().__init__()
        self.panel = panel
        self.setWindowTitle("攻击预览 —— 关卡场景试打")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(900, 640)
        self.setStyleSheet(
            "QWidget{background:#22252b;color:#e6eaf2;}"
            "QPushButton{background:#333842;color:#e6eaf2;border:1px solid "
            "#4a5260;border-radius:4px;padding:3px 8px;}"
            "QPushButton:hover{background:#3d444f;}"
            "QPushButton:disabled{color:#7a8290;}"
            "QComboBox,QSpinBox{background:#2b2f36;color:#e6eaf2;"
            "border:1px solid #4a5260;padding:2px 4px;}"
            "QCheckBox{color:#e6eaf2;}")

        self.anim = anim or panel.current_anim()
        self.doc = panel.doc
        self.main = None
        self.victim = None
        self.actors = []
        self.fx = []
        self.play_mode = None
        self._hit_done = False
        self._hit_once = False
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)

        # ---------- 顶部工具条 ----------
        top = QHBoxLayout()
        top.setSpacing(8)
        self.lbl_anim = QLabel(
            f"<b>{self.anim.name if self.anim else '（无动画）'}</b>")
        top.addWidget(self.lbl_anim)
        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.clicked.connect(self._toggle_play)
        top.addWidget(self.btn_play)
        self.btn_loop = QPushButton("▶ 循环")
        self.btn_loop.clicked.connect(self._toggle_loop)
        top.addWidget(self.btn_loop)
        self.btn_reset = QPushButton("重置")
        self.btn_reset.clicked.connect(self.reset)
        top.addWidget(self.btn_reset)
        top.addSpacing(10)

        self.cmb_victim = QComboBox()
        self.cmb_victim.setMinimumWidth(150)
        self.cmb_victim.addItem("（不放假人）")
        names = sorted({(n or "") for n, _, _ in panel.entities if n})
        for n in names:
            self.cmb_victim.addItem(n)
        self.cmb_victim.currentIndexChanged.connect(self._pick_victim)
        top.addWidget(QLabel("假人"))
        top.addWidget(self.cmb_victim)

        self.cmb_react = QComboBox()
        self.cmb_react.addItems(["自动（按 power）", "强制 pain", "强制 fall"])
        top.addWidget(QLabel("受击"))
        top.addWidget(self.cmb_react)

        self.sp_grav = QSpinBox()
        self.sp_grav.setRange(0, 40)
        self.sp_grav.setValue(0)
        self.sp_grav.setSuffix(" ‰")
        self.sp_grav.setToolTip("跳跃重力；0 = 按 jumpframe 初速自动定标")
        self.sp_grav.valueChanged.connect(self._rebuild)
        top.addWidget(QLabel("重力"))
        top.addWidget(self.sp_grav)
        top.addStretch(1)

        self.ck_boxes = QCheckBox("框")
        self.ck_boxes.setChecked(True)
        self.ck_boxes.setToolTip("画 BBox（蓝）与 Attack（红）")
        self.ck_boxes.toggled.connect(self._on_view)
        self.ck_grid = QCheckBox("网格")
        self.ck_grid.setChecked(True)
        self.ck_grid.toggled.connect(self._on_view)
        self.ck_shadow = QCheckBox("影子")
        self.ck_shadow.setChecked(True)
        self.ck_shadow.toggled.connect(self._on_view)
        self.ck_follow = QCheckBox("跟随")
        self.ck_follow.setChecked(True)
        self.ck_follow.setToolTip("相机水平跟随主实体")
        for w in (self.ck_boxes, self.ck_grid, self.ck_shadow, self.ck_follow):
            top.addWidget(w)

        # ---------- 场景 + 时间轴 ----------
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)
        root.addLayout(top)
        self.scene = SceneCanvas(self)
        root.addWidget(self.scene, 1)
        self.host = _Host(self)
        self.timeline = BorTimeline(self.host, with_attack_button=False)
        root.addWidget(self.timeline)
        self._on_view()
        self.reset()

    # ---------------- 数据 ----------------
    def markers(self):
        if self.anim is None:
            return {"jumpframe": [], "dropframe": [], "landframe": [],
                    "sound": set(), "spawn": set()}
        m = {"jumpframe": [], "dropframe": [], "landframe": [],
             "sound": set(), "spawn": set()}
        j = bs.jump_cmds(self.anim)
        for src, dst in (("jump", "jumpframe"), ("drop", "dropframe"),
                         ("land", "landframe")):
            v = j[src]
            if v is None:
                continue
            m[dst] = [v[0]] if isinstance(v, tuple) else [v]
        for i, fr in enumerate(self.anim.frames()):
            if any(c == "sound" for _, c, _ in fr["extras"]):
                m["sound"].add(i)
        m["spawn"] = bs.spawn_index(self.anim)
        return m

    def gravity(self):
        g = self.sp_grav.value()
        return (g / 1000.0) if g > 0 else None

    # ---------------- 重置 / 播放 ----------------
    def reset(self):
        self.stop_play()
        self.actors = []
        self.fx = []
        self._hit_done = False
        self._hit_once = False
        if self.anim is None:
            self.main = None
            self.scene.update()
            return
        nm = self.doc.header_get("name") if self.doc else None
        self.main = Actor(self.doc, (nm[0] if nm else "?"),
                          self.panel, x=0.0, z=0.0)
        self.main.set_anim(self.anim.name, loop=False)
        self.main.track = bs.MotionTrack(self.anim, self.gravity())
        self.actors.append(self.main)
        self._respawn_victim(keep_pos=False)
        self.seek(0)
        self.scene.update()
        self.timeline.update()

    def _rebuild(self):
        if self.main is not None and self.anim is not None:
            self.main.track = bs.MotionTrack(self.anim, self.gravity())

    def seek(self, i):
        if self.main is None or not self.main.frames:
            return
        self.main.frame = max(0, min(int(i), len(self.main.frames) - 1))
        self.main.wait = bs.delay_of(self.main.frames[self.main.frame])
        self.main.done = False
        self._hit_done = False
        self.timeline.ensure_visible(self.main.frame)
        self.scene.update()
        self.timeline.update()

    def start_play(self, mode):
        if self.main is None or not self.main.frames:
            return
        self.main.frame = 0
        self.main.wait = bs.delay_of(self.main.frames[0])
        self.main.done = False
        self._hit_done = False
        self.play_mode = mode
        self.timer.start()
        self.scene.update()
        self.timeline.update()

    def stop_play(self):
        self.play_mode = None
        self.timer.stop()
        # 预览里主实体停在最后一帧，但生成物 / 假人状态保留，方便看结果
        self.scene.update()
        self.timeline.update()

    def _toggle_play(self):
        if self.play_mode is not None:
            self.stop_play()
        else:
            self.start_play("once")

    def _toggle_loop(self):
        if self.play_mode == "loop":
            self.stop_play()
        else:
            self.start_play("loop")

    # ---------------- 假人 ----------------
    def _pick_victim(self, idx):
        if idx <= 0:
            if self.victim in self.actors:
                self.actors.remove(self.victim)
            self.victim = None
            self.scene.update()
            return
        name = self.cmb_victim.itemText(idx)
        doc = self.panel._entity_doc(name)
        if doc is None:
            self.victim = None
            self.scene.update()
            return
        # 只放一个：再选新的直接替换
        if self.victim in self.actors:
            self.actors.remove(self.victim)
        self.victim = Actor(doc, name, self.panel, x=100.0, z=0.0)
        self.victim.state = "idle"
        if not self.victim.set_anim("idle", loop=True) and doc.anims:
            self.victim.set_anim(doc.anims[0].name, loop=True)
        self.actors.append(self.victim)
        self.scene.update()

    def _respawn_victim(self, keep_pos=True):
        idx = self.cmb_victim.currentIndex()
        if idx <= 0:
            return
        old = self.victim
        x, z = (100.0, 0.0)
        if keep_pos and old is not None:
            x, z = old.base_x, old.base_z
        if old in self.actors:
            self.actors.remove(old)
        name = self.cmb_victim.itemText(idx)
        doc = self.panel._entity_doc(name)
        if doc is None:
            self.victim = None
            return
        v = Actor(doc, name, self.panel, x=x, z=z)
        v.state = "idle"
        if not v.set_anim("idle", loop=True) and doc.anims:
            v.set_anim(doc.anims[0].name, loop=True)   # 没有 idle 就播第一个
        self.victim = v
        self.actors.append(v)

    # ---------------- 每刻推进 ----------------
    def _tick(self):
        if self.play_mode is None:
            self.timer.stop()
            return
        # 主实体
        changed = self.main.tick() if self.main else False
        if changed:
            self._on_main_frame()
            self.timeline.ensure_visible(self.main.frame)
        # 假人 / 生成物
        for a in list(self.actors):
            if a is self.main:
                continue
            if a.tick() and a is self.victim:
                self._on_victim_anim_end()
            if a.life is not None:
                a.life -= 1
        self.actors = [a for a in self.actors
                       if a.life is None or a.life > 0]
        if self.victim not in self.actors:
            self.victim = None
        for a in self.actors:
            if a.flash > 0:
                a.flash -= 1
        self.fx = [f for f in self.fx if f["t"] < f["life"]]
        for f in self.fx:
            f["t"] += 1
        self._check_hit()
        if self.scene.follow and self.main is not None:
            self.scene.cam_x = self.main.pos()[0]
        if self.main is not None and self.main.done \
                and self.play_mode == "once":
            # 主实体演完了，但假人可能还在 fall / rise ——
            # 等它回到 idle 再停，否则受击反应会冻在半截
            if self.victim is None or self.victim.state in ("", "idle"):
                self.stop_play()
        self.scene.update()
        self.timeline.update()

    def _on_main_frame(self):
        """主实体换帧：帧音效 + 生成命令。"""
        fr = self.main.cur
        if fr is None:
            return
        if not self.panel._muted:
            try:
                self.panel._play_frame_sound(fr)
            except Exception:
                pass
        sps = bs.spawn_frames(self.anim).get(self.main.frame)
        if not sps:
            return
        px, z, y = self.main.pos()
        ox, oy = self.main.offset()
        for sp in sps:
            doc = self.panel._entity_doc(sp["name"])
            if doc is None:
                continue
            a = Actor(doc, sp["name"], self.panel,
                      x=px + sp.get("x", 0.0),
                      z=z + sp.get("z", 0.0) - sp.get("y", 0.0))
            if a.set_anim("idle", loop=True) or \
                    (doc.anims and a.set_anim(doc.anims[0].name, loop=True)):
                a.life = 600          # 播放停止 / 重置时清掉
                self.actors.append(a)

    def _victim_idle(self):
        """假人回 idle（没有 idle 动画就播第一个动画）。"""
        v = self.victim
        if v is None:
            return
        if not v.set_anim("idle", loop=True) and v.doc is not None \
                and v.doc.anims:
            v.set_anim(v.doc.anims[0].name, loop=True)

    def _on_victim_anim_end(self):
        v = self.victim
        if v is None or not v.done:
            return
        if v.state == "pain":
            v.state = "idle"
            self._victim_idle()
        elif v.state == "fall":
            if v.set_anim("rise", loop=False):
                v.state = "rise"
            else:
                v.state = "idle"
                self._victim_idle()
        elif v.state == "rise":
            v.state = "idle"
            self._victim_idle()

    # ---------------- 命中判定 ----------------
    def _check_hit(self):
        m, v = self.main, self.victim
        if m is None or v is None or self._hit_done:
            return
        atk = bs.attack_box(m.cur)
        if atk is None:
            return
        bb = bs.bbox_of(v.cur)
        if bb is None:
            return
        mb = m.scene_box(atk[:4] + (atk[6] or 40,))
        vb = v.scene_box(bb)
        if not bs.boxes_overlap(mb, vb):
            return
        self._hit_done = True
        self._do_hit(atk, bs.overlap_point(mb, vb))

    def _do_hit(self, atk, pt):
        v = self.victim
        if v is None:
            return
        mode = self.cmb_react.currentIndex()
        knockdown = (atk[5] >= 1) if mode == 0 else (mode == 2)
        target = "fall" if knockdown else "pain"
        if not v.set_anim(target, loop=False):
            if not v.set_anim("pain", loop=False):
                self._victim_idle()
                v.state = "idle"
                return
            target = "pain"
        v.state = target
        # ---- hitflash / hitfx ----
        hitfx, hitflash = bs.anim_hit_fx(self.main.anim)
        if hitflash:
            v.flash = 6
        self.fx.append({"kind": "burst", "x": pt[0], "y": pt[1],
                        "z": pt[2], "t": 0, "life": 14})
        for nm in (hitfx, hitflash):
            if not nm:
                continue
            doc = self.panel._entity_doc(nm)
            if doc is None:
                continue
            a = Actor(doc, nm, self.panel)
            if a.set_anim("idle", loop=False):
                a.is_fx = True
                a.base_x, a.base_z = pt[0], pt[2]
                a.life = 24
                self.actors.append(a)

    # ---------------- 杂项 ----------------
    def _on_view(self):
        self.scene.show_boxes = self.ck_boxes.isChecked()
        self.scene.show_grid = self.ck_grid.isChecked()
        self.scene.show_shadow = self.ck_shadow.isChecked()
        self.scene.follow = self.ck_follow.isChecked()
        self.scene.update()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Left, Qt.Key_Right,
                       Qt.Key_Home, Qt.Key_End):
            self.timeline.keyPressEvent(e)
            return
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)
