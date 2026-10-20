# -*- coding: utf-8 -*-
"""关卡编辑：levels.txt 列表 + 关卡场景画布 + 生成点编辑。

和动画编辑最大的不同：**画布的横轴是关卡长度（x），纵轴是画面深度 z**
（z 从屏幕顶往下数，levels.txt 里的 `z zmin zmax BGheight` 决定可走范围）。
生成点的 `coords x z a` 里 x 相对屏幕，真正的关卡位置 = `at` + x。
"""
import os

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetrics,
                         QPainter, QPen, QPixmap, QPolygonF, QTextCursor)
from PyQt5.QtWidgets import (QApplication, QCheckBox, QFileDialog,
                             QFormLayout, QGroupBox, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit,
                             QMenu, QMessageBox,
                             QPushButton, QSpinBox, QSplitter, QTextEdit,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                             QWidget)

import bor_data as bl
from bor_data import openbor
from bor_view import BorHighlighter, BorTextEdit
from core import QSS, iu

SCREEN_W = 320          # 画面兜底宽（真实尺寸看 video.txt → canvas.vw()）
SCREEN_H = 240          # 画面兜底高（真实尺寸看 video.txt → canvas.vh()）
COL_ROW_H = 44          # 按组排列模式：一行的高度（屏幕 px，文字为主）
COL_TOP = 46            # 按组排列模式：第一行距画布顶的最小距离
                         # （实际取 max(COL_TOP, zmax线以下)，实体条排在场景画面下方）
COL_SIDE = 132          # 纵向卷轴的按组排列：左侧标记条栏的宽度
THUMB_W = 56            # 按组排列模式：辅助缩略图的最大宽
THUMB_H = 40            # 按组排列模式：辅助缩略图的最大高
ITEM_TH_W = 56          # 属性面板 item 缩略图的最大宽
ITEM_TH_H = 40          # 属性面板 item 缩略图的最大高
Z_BAND_COLOR = QColor(120, 220, 150)
WALL_COLOR = QColor(90, 170, 255)
HOLE_COLOR = QColor(255, 120, 110)
MARK_COLOR = QColor(255, 205, 90)
SEL_COLOR = QColor(255, 120, 60)
WARN_COLOR = QColor(255, 110, 90)       # z 越界
ERR_BG = QColor(255, 96, 96, 70)        # 文本编辑器里的错误行底色（红）
NOTE_BG = QColor(255, 205, 90, 60)      # 文本编辑器里的提醒行底色（黄）
MARK_DIM = QColor(120, 128, 138)        # 结构子段（scene/end…）的灰字

# ---- 关卡文本编辑器的关键字配色（借 BorHighlighter 的三类颜色）----
LEVEL_OBJ_KWS = frozenset(
    "spawn group wait light shadow rock camera".split())
LEVEL_TER_KWS = frozenset("wall hole endhole".split())
LEVEL_FIELD_KWS = frozenset(
    "coords at health aggression item itemmap itemalias alias map flip "
    "boss 2pspawn 2pwait".split())


class LevelHighlighter(BorHighlighter):
    """关卡文件的着色：对象命令青色、属性/关卡设置蓝色、地形红色。

    只认行首第一个 token，其余都是参数——和实体文件的规则一致。
    """

    def _classify(self, word):
        w = word.lower()
        if w in LEVEL_TER_KWS:
            return "attack"            # 红色：wall / hole（挡路的家伙）
        if w in LEVEL_OBJ_KWS:
            return "anim"              # 青色：spawn / group / wait …
        if w in LEVEL_FIELD_KWS:
            return "header"            # 蓝色：coords / at / health …
        return None


# ============================================================ 关卡画布
class LevelMapCanvas(QWidget):
    """横轴 = 关卡长度 x，纵轴 = 深度 z（从屏幕顶往下）。"""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.zoom = 0.3
        self.scroll_x = 0.0
        self.scroll_z = 0.0
        self.row_scroll = 0.0        # 按组排列：行的滚动（屏幕 px，不带动背景）
        self.mode = "map"            # map = 全景 / screen = 屏幕预览
        self.layout = "pos"          # pos = 实际生成位置 / col = 按组排列
        self.sel = None              # 选中的对象下标
        self.group_sel = None        # 只看某一个 group（下标；None = 全部）
        self.wall_sel = None         # 选中的 wall 行号（None = 没选）
        self.wall_draw = False       # 画墙模式：空白处拖出一个矩形即新建 wall
        self.wall_preview = None     # 画墙时的预览矩形 (x0, z0, x1, z1)（关卡坐标）
        self.show_viewbox = False    # 画面范围框默认不显示（按钮可开）
        self.show_wall_alt = True    # 画墙的 alt 高度带（按钮可关）
        self.vb_pos = None           # 画面框在关卡里的位置 (x, z)（滚动轴坐标；
                                     # None = 还没拖过，取关卡起点）
        self._mid_line = None        # 中键点出的竖线位置（at），画到菜单关掉
        self._mk_rows = {}           # 标记排布缓存
        self._owners = {}            # 生成点 → 所属 group 的缓存
        self._col_ord = {}           # 按组排列：生成点 → 组内序号（0 起）
        self._col_cells = {}         # 按组排列：生成点 → 单元格矩形（屏幕）
        self._col_content = 0.0      # 按组排列：内容底边（未滚动坐标）
        self._drag = None
        self._imgs = {}
        self._checker = None

    # ---- 坐标 ----
    @property
    def doc(self):
        return self.panel.doc

    def sx(self, x):
        return (x - self.scroll_x) * self.zoom

    def sy(self, z):
        return (z - self.scroll_z) * self.zoom

    def to_level(self, p):
        return (p.x() / self.zoom + self.scroll_x,
                p.y() / self.zoom + self.scroll_z)

    def view_w(self):
        return self.width() / self.zoom

    def img(self, rel, force_alpha=True):
        """按相对路径取图（带缓存）。

        force_alpha=True：把调色板第 1 个色（index 0）当透明色。
        OpenBOR 里 8 位图的透明色就是它，而很多素材 GIF 并没有在文件里
        写 transparency 标志（或写在了别的索引上）—— 不补的话那块底色
        会糊成一整片纯色。见 image_utils.openbor_rgba。
        **背景图 / 面板图除外**（它们整体就是一张画，不该被抠洞），
        那些调用会传 force_alpha=False。
        """
        if not rel:
            return None
        p = bl.resolve_path(self.panel.root_dirs, rel)
        if not p:
            return None
        key = (p, force_alpha)
        im = self._imgs.get(key)
        if im is None:
            try:
                raw = iu.load(p)
                # 判据是“色表第 1 个色有没有被设成透明”，不是“色表里有没有透明色”
                if force_alpha:
                    im = iu.pil_to_qimage(iu.openbor_rgba(raw))
                else:
                    im = iu.pil_to_qimage(raw.convert("RGBA"))
            except Exception:
                im = None
            self._imgs[key] = im
            if len(self._imgs) > 120:
                self._imgs.clear()
        return im

    # ---- 视图 ----
    def fit(self, for_col=False):
        d = self.doc
        if d is None:
            return
        zmin, zmax, _ = d.z_range()
        L = max(320.0, d.length(self.panel.root_dirs))
        H = self.panel.view_height()
        H = max(H, zmax + 40)
        self.zoom = max(0.02, min(2.0, (self.width() - 24) / L,
                                  (self.height() - 30) / max(60.0, H)))
        self.scroll_x = 0.0
        # 按组排列模式下背景 / panel 等地表元素**钉在这个位置上**：
        # 行的上下滚动由 row_scroll 负责，scroll_z 不再跟着动，
        # 不然一滚行整个背景就被推出画面了（看着像“选组后背景消失”）。
        self.scroll_z = -10.0
        self.row_scroll = 0.0
        self.update()

    def goto_screen(self, pos):
        self.mode = "screen"
        self.scroll_x = max(0.0, pos - self.view_w() * 0.3)
        self.update()

    # ---- 画 ----
    def paintEvent(self, e):
        p = QPainter(self)
        if self._checker is None:
            self._checker = QPixmap(16, 16)
            self._checker.fill(QColor(40, 42, 48))
            qp = QPainter(self._checker)
            qp.fillRect(0, 0, 8, 8, QColor(34, 36, 42))
            qp.fillRect(8, 8, 8, 8, QColor(34, 36, 42))
            qp.end()
        p.fillRect(self.rect(), QBrush(self._checker))
        d = self.doc
        if d is None:
            p.setPen(QColor(150, 156, 168))
            p.drawText(self.rect(), Qt.AlignCenter, "（还没载入关卡）")
            return
        zmin, zmax, bgh = d.z_range()
        p.save()
        # ---- 背景栈：background / bglayer / layer / fglayer / panel / frontpanel
        # 按**引擎 draw_scrolled_bg 的公式**画（见 _draw_bg_stack 的注释）
        self._draw_bg_stack(p)
        # ---- z 可走范围 ----
        self._draw_zband(p, zmin, zmax)
        # ---- 网格 ----
        self._draw_grid(p, zmax)
        # ---- 地形 ----
        self._draw_terrain(p)
        self._draw_wall_sel(p)
        # ---- 玩家出生点 ----
        for k, x, z, a in d.player_spawns():
            self._draw_player(p, k, x, z)
        # ---- 生成对象 ----
        self._draw_objects(p)
        self._draw_wall_preview(p)
        # 中键点出的“在这里加 wait / group”参考线
        if self._mid_line is not None:
            _sd, vert = d.scroll_dir()
            p.setPen(QPen(QColor(255, 255, 255, 200), 2, Qt.DashLine))
            if vert:
                y = int(self.sy(self._mid_line))
                p.drawLine(0, y, self.width(), y)
            else:
                x = int(self.sx(self._mid_line))
                p.drawLine(x, 0, x, self.height())
        p.restore()
        # 游戏画面范围框（画在最上面，框外压暗）
        if self.show_viewbox:
            self._draw_viewbox(p)
        elif self.mode == "screen":
            self._draw_screen_band(p)
        if self.layout == "col":
            self._draw_rowscroll(p)      # 行滚动条（实体太多时出现）
        self._draw_hud(p, zmin, zmax)

    # ---- 背景栈（引擎 draw_scrolled_bg 规则，逐字核对过 v6391 源码）----
    def vw(self):
        """游戏画面宽（video.txt 的 video 模式，不是写死的 320）。"""
        return float(self.panel.video_size()[0])

    def vh(self):
        """游戏画面高（video.txt 的 video 模式，不是写死的 240）。"""
        return float(self.panel.video_size()[1])

    def _cam(self):
        """当前假设的游戏摄像机位置 (x, z) —— 视差层位置的基准。

        - 画面框显示时 = 画面框位置（框里看到的和游戏一致）
        - 屏幕预览模式 = 当前那一屏的 x（z 摄像机在横向关卡里恒为 0）
        - 其它（全景模式）= 关卡起点 (0, 0)：游戏刚开始时摄像机就在这，
          视差层在画布上就有了确定位置 —— 拖动画布只是看，不是移动摄像机
        """
        if self.show_viewbox:
            return self.vb_value()
        if self.mode == "screen":
            return (self.scroll_x, 0.0)
        return (0.0, 0.0)

    def _draw_bg_stack(self, p):
        """背景 / bglayer / layer / fglayer / panel / frontpanel 全套。

        引擎公式（draw_scrolled_bg）：层的屏幕位置
        x = offset.x - camx*(1-xratio)，z = offset.z - camz*(1-zratio)，
        换算到关卡画布坐标就是 **offset + ratio*camera**（panel 的
        ratio=0 → 钉在关卡空间；background 缺省 0.5 → 半速视差；
        frontpanel -0.4 → 比摄像机还快的前景）。瓦片 = 图宽+spacing，
        repeat<0 = 无限平铺（上下 / 左右都铺满），否则只往右 / 往下铺
        repeat 张。
        """
        d = self.doc
        if d is None:
            return
        zmin, zmax, _bgh = d.z_range()
        camx, camz = self._cam()
        panel_z = float(zmin) - 50.0      # 引擎 PANEL_Z = PLAYER_MIN_Z - 50
        infos = d.layer_infos(zmax)
        opacity = 0.9 if self.mode == "map" else 1.0
        # panel 之下的：background / bglayer / 低 z 的 layer
        for it in infos:
            if it["sortz"] < panel_z:
                im = self.img(it["path"], force_alpha=bool(it["transbg"]))
                self._draw_layer_tiles(p, im, it, camx, camz, opacity)
        self._draw_panels(p)
        # panel 之上：fglayer / 高 z 的 layer（引擎里它们排在实体前后之间，
        # 编辑器里画在 panel 之上、生成物之下，已经是够贴近的近似）
        for it in infos:
            if it["sortz"] >= panel_z:
                im = self.img(it["path"], force_alpha=bool(it["transbg"]))
                self._draw_layer_tiles(p, im, it, camx, camz, 0.85)
        self._draw_frontpanels(p, camx, camz)

    def _draw_layer_tiles(self, p, im, it, camx, camz, opacity=1.0):
        """按引擎公式平铺一张图层图（it = layer_infos 的一项）。"""
        if im is None or im.width() < 1 or im.height() < 1:
            return
        z = self.zoom
        tw = (im.width() + it["xs"]) * z
        th = (im.height() + it["zs"]) * z
        if tw < 1 or th < 1:
            return
        A = self.sx(it["xoff"] + it["xr"] * camx)
        B = self.sy(it["zoff"] + it["zr"] * camz)
        W, H = float(self.width()), float(self.height())
        iw, ih = im.width() * z, im.height() * z
        # 横向：无限 → 铺满视野；有限 → 只画第 0..xrep-1 张
        if it["xrep"] < 0:
            k0 = int((0.0 - A) // tw) - 1
            k1 = int((W - A) // tw) + 1
        else:
            k0, k1 = 0, it["xrep"] - 1
        if k1 < k0:
            return
        old = p.opacity()
        if opacity < 1.0:
            p.setOpacity(opacity)
        for k in range(k0, k1 + 1):
            x = A + k * tw
            if x + iw < 0 or x > W:
                continue
            # 纵向：无限 → 铺满**一屏**（引擎把 offset 预移 20000 张再
            # 取模，屏幕内纵向无限平铺，但屏外永远看不到 → 画布上也只
            # 铺 [摄像机z, 摄像机z+屏高] 这段，免得整个画布被重复图淹没）；
            # 有限 → 只往下铺 zrep 张
            if it["zrep"] < 0:
                band0 = self.sy(camz)
                band1 = self.sy(camz + self.vh())
                m0 = int((band0 - B) // th) - 1
                m1 = int((band1 - B) // th) + 1
            else:
                m0, m1 = 0, it["zrep"] - 1
            for m in range(m0, m1 + 1):
                y = B + m * th
                if y + ih < 0 or y > H:
                    continue
                p.drawImage(QRectF(x, y, iw, ih), im)
        if opacity < 1.0:
            p.setOpacity(old)

    def _draw_panels(self, p):
        d = self.doc
        paths = d.panel_paths()
        order = d.panel_order()
        if not paths or not order:
            return
        # 引擎：panel_width = **最后声明**那张 panel 的宽，所有 order 槽
        # 都按 panel_width*i 排（不是按各自的图宽累加）
        ims = {}
        pw = self.vw()
        for n, (_ln, rel) in enumerate(paths):
            im = self.img(rel, force_alpha=True)
            ims[n] = im
            if im is not None:
                pw = float(im.width())
        z = self.zoom
        old = p.opacity()
        if self.mode == "map":
            p.setOpacity(0.9)
        for slot, ch in enumerate(order.lower()):
            k = ord(ch) - 97
            im = ims.get(k)
            if im is None:
                continue
            # panel 也要抠 index 0：OpenBOR 里 8 位图的首色恒为透明键，
            # 关卡面板常用它标出“这里不是地面”（不抠会糊成一整块纯色）
            x = self.sx(pw * slot)
            if x + im.width() * z > 0 and x < self.width():
                p.drawImage(QRectF(x, self.sy(0), im.width() * z,
                                   im.height() * z), im)
        if self.mode == "map":
            p.setOpacity(old)

    def _draw_frontpanels(self, p, camx, camz):
        """frontpanel：ratio -0.4 / 1，第 i 张 offset.x = i*图宽，
        spacing = (张数-1)*图宽，横向无限平铺、纵向只画一张（顶对齐 0）。
        """
        d = self.doc
        fps = d.frontpanel_paths()
        if not fps:
            return
        n = len(fps)
        z = self.zoom
        old = p.opacity()
        p.setOpacity(0.8 if self.mode == "map" else 1.0)
        try:
            for idx, rel in enumerate(fps):
                im = self.img(rel, force_alpha=True)
                if im is None or im.width() < 1:
                    continue
                w = float(im.width())
                iw, ih = w * z, im.height() * z
                A = self.sx(idx * w - 0.4 * camx)
                B = self.sy(camz)
                tw = (w + (n - 1) * w) * z     # 瓦片宽 = 图宽 + spacing
                if tw < 1:
                    continue
                k0 = int((0.0 - A) // tw) - 1
                k1 = int((self.width() - A) // tw) + 1
                for k in range(k0, k1 + 1):
                    x = A + k * tw
                    if x + iw < 0 or x > self.width():
                        continue
                    p.drawImage(QRectF(x, B, iw, ih), im)
        finally:
            p.setOpacity(old)

    def _draw_zband(self, p, zmin, zmax):
        y0, y1 = self.sy(zmin), self.sy(zmax)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(120, 220, 150, 34))
        p.drawRect(QRectF(0, y0, self.width(), max(2.0, y1 - y0)))
        p.setPen(QPen(Z_BAND_COLOR, 1, Qt.DashLine))
        p.drawLine(0, int(y0), self.width(), int(y0))
        p.drawLine(0, int(y1), self.width(), int(y1))
        p.setPen(Z_BAND_COLOR)
        f = QFont(self.font())
        f.setPointSize(max(7, self.font().pointSize() - 1))
        p.setFont(f)
        p.drawText(QRectF(4, y0 - 14, 90, 13), Qt.AlignLeft | Qt.AlignVCenter,
                   f"zmin {int(zmin)}")
        p.drawText(QRectF(4, y1 + 2, 90, 13), Qt.AlignLeft | Qt.AlignVCenter,
                   f"zmax {int(zmax)}")
        p.setFont(self.font())
        # 拖动把手：两条虚线可以直接上下拖（和右下角 z 范围数值联动）
        for y in (y0, y1):
            if not (6 <= y <= self.height() - 6):
                continue
            x = self.width() - 30.0
            p.setPen(QPen(Z_BAND_COLOR, 1))
            p.setBrush(QColor(120, 220, 150, 110))
            p.drawRect(QRectF(x, y - 5, 24, 10))
            p.setPen(QColor(24, 34, 26))
            p.drawText(QRectF(x, y - 7, 24, 14), Qt.AlignCenter, "⇕")

    def _draw_grid(self, p, zmax):
        step = 320 if self.zoom < 0.35 else 160
        x0 = int(self.scroll_x // step) * step
        x = x0
        while x < self.scroll_x + self.view_w() + step:
            sx = self.sx(x)
            if 0 <= sx <= self.width():
                p.setPen(QPen(QColor(255, 255, 255, 40), 1))
                p.drawLine(int(sx), 0, int(sx), self.height())
                if step >= 320 or self.zoom > 0.2:
                    p.setPen(QColor(190, 196, 208))
                    p.drawText(QRectF(sx + 2, 2, 60, 13),
                               Qt.AlignLeft | Qt.AlignVCenter, str(int(x)))
            x += step
        gz = 20
        z0 = 0
        while z0 <= zmax + 60:
            sy = self.sy(z0)
            if 0 <= sy <= self.height():
                p.setPen(QPen(QColor(255, 255, 255, 26), 1))
                p.drawLine(0, int(sy), self.width(), int(sy))
            z0 += gz

    def _draw_terrain(self, p):
        d = self.doc
        try:
            ck = getattr(self.panel, "ck_terrain", None)
        except RuntimeError:
            ck = None          # 面板没走完初始化（测试夹具等）：默认画
        if ck is not None and not ck.isChecked():
            return
        for _, a in d.walls():
            self._draw_quad(p, a, WALL_COLOR)
            if self.show_wall_alt:
                self._draw_wall_alt(p, a)
        for _, a in d.holes():
            self._draw_quad(p, a, HOLE_COLOR)

    def _draw_wall_alt(self, p, a):
        """墙的 alt：把墙画成**柱体** —— 上底面 = 底面那个四边形整体抬高 alt。

        OpenBOR 的 wall 是立体的：底面四边形（x / z / 四角偏移 / depth）决定
        它站在地上的形状，alt 是垂直往上拔高的高度，用来挡住想跳上来的实体
        （站不上它的上底面）。所以这里：
        - 上底面 = 底面四点整体**往上**平移 alt（淡蓝虚线框 + 半透明填充）
        - 四个角各连一条竖线，表示柱体的四条侧棱
        上底面**只是跟着底面动**，不单独参与编辑。
        """
        if len(a) < 8:
            return
        try:
            alt = bl._i(a[7])
        except Exception:
            return
        if alt <= 0:
            return
        c = self.wall_corners(a)
        if c is None:
            return
        base = [(self.sx(x), self.sy(z)) for x, z in c]
        up = alt * self.zoom                     # 高度 → 屏幕上的往上位移
        top = [(x, y - up) for x, y in base]
        # 四条侧棱
        p.setPen(QPen(QColor(150, 195, 255, 150), 1, Qt.DotLine))
        for (x0, y0), (x1, y1) in zip(base, top):
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        # 上底面
        poly = QPolygonF([QPointF(x, y) for x, y in top])
        p.setPen(QPen(QColor(170, 205, 255), 1, Qt.DashLine))
        p.setBrush(QColor(170, 205, 255, 44))
        p.drawPolygon(poly)
        if self.zoom > 0.12 and up > 6:
            r = poly.boundingRect()
            p.setPen(QColor(200, 224, 255))
            p.drawText(QRectF(r.center().x() - 34, r.center().y() - 7, 68, 14),
                       Qt.AlignCenter, f"顶面 alt {alt}")

    def _draw_quad(self, p, a, color):
        pts = self._wall_pts(a)
        if pts is None:
            return
        p.setPen(QPen(color, 2))
        p.setBrush(QColor(color.red(), color.green(), color.blue(), 60))
        p.drawPolygon(QPolygonF(pts))

    # ---- wall 的几何与命中 ----
    @staticmethod
    def wall_corners(a):
        """wall 参数 → 四个角的**关卡坐标** [(TL, TR, BR, BL)]；缺参数 None。

        手册：wall x z ul ll ur lr depth —— 上边的 z = zpos - depth，
        四个偏移值是各角离 xpos 的水平距离。
        """
        if len(a) < 7:
            return None
        x, z = bl._f(a[0]), bl._f(a[1])
        ul, ll, ur, lr = (bl._f(a[2]), bl._f(a[3]), bl._f(a[4]), bl._f(a[5]))
        dep = bl._f(a[6])
        return [(x + ul, z - dep), (x + ur, z - dep),
                (x + lr, z), (x + ll, z)]

    def _wall_pts(self, a):
        c = self.wall_corners(a)
        if c is None:
            return None
        return [QPointF(self.sx(x), self.sy(z)) for x, z in c]

    def wall_at(self, pos):
        """鼠标落在哪个 wall 上（返回它的**行号**；面积小的优先，嵌套的选里面那个）。"""
        d = self.doc
        if d is None or not self._terrain_on():
            return None
        best, best_area = None, 1e18
        for idx, a in d.walls():
            pts = self._wall_pts(a)
            if pts is None:
                continue
            if QPolygonF(pts).containsPoint(QPointF(pos), Qt.WindingFill):
                xs = [q.x() for q in pts]
                ys = [q.y() for q in pts]
                area = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
                if area < best_area:
                    best_area, best = area, idx
        return best

    # 把手编号：0=左上 1=右上 2=右下 3=左下 4=底边中点（整体移动）
    def wall_handles(self, idx):
        """[(把手号, 屏幕 QPointF)]；没选中或图坏了返回 []。"""
        d = self.doc
        if d is None or idx is None:
            return []
        for i, a in d.walls():
            if i == idx:
                c = self.wall_corners(a)
                if c is None:
                    return []
                pts = [QPointF(self.sx(x), self.sy(z)) for x, z in c]
                mid = QPointF((pts[2].x() + pts[3].x()) / 2.0,
                              pts[2].y())            # 底边中点
                return list(enumerate(pts)) + [(4, mid)]
        return []

    def wall_handle_at(self, pos, tol=8):
        """鼠标落在选中 wall 的哪个把手上；返回 (行号, 把手号) 或 None。"""
        if self.wall_sel is None:
            return None
        for hid, q in self.wall_handles(self.wall_sel):
            if abs(pos.x() - q.x()) <= tol and abs(pos.y() - q.y()) <= tol:
                return self.wall_sel, hid
        return None

    def _terrain_on(self):
        ck = getattr(self.panel, "ck_terrain", None)
        return ck is None or ck.isChecked()

    def _draw_wall_sel(self, p):
        """选中的 wall：加亮描边 + 5 个把手（四角各一个 + 底边中点一个整体拖）。"""
        if self.wall_sel is None or not self._terrain_on():
            return
        d = self.doc
        for idx, a in d.walls():
            if idx != self.wall_sel:
                continue
            pts = self._wall_pts(a)
            if pts is None:
                return
            p.setPen(QPen(QColor(255, 255, 255), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(QPolygonF(pts))
            p.setPen(QPen(WALL_COLOR.lighter(140), 3))
            p.drawPolygon(QPolygonF(pts))
            for hid, q in self.wall_handles(idx):
                r = QRectF(q.x() - 5, q.y() - 5, 10, 10)
                p.setPen(QPen(QColor(30, 32, 38), 1))
                p.setBrush(QColor(255, 255, 255) if hid == 4
                           else WALL_COLOR.lighter(130))
                p.drawRect(r)
            # 把手图例
            p.setPen(QColor(190, 215, 255))
            b = pts[2]      # 右下角下方放说明
            p.drawText(QRectF(b.x() + 8, b.y() + 2, 260, 15),
                       Qt.AlignLeft | Qt.AlignVCenter,
                       "拖角改形状 · 拖底边中点整体移动")
            return

    def _draw_wall_preview(self, p):
        """画墙模式的拖拽预览（虚线矩形）。"""
        if self.wall_preview is None:
            return
        x0, z0, x1, z1 = self.wall_preview
        a = QPointF(self.sx(min(x0, x1)), self.sy(min(z0, z1)))
        b = QPointF(self.sx(max(x0, x1)), self.sy(max(z0, z1)))
        p.setPen(QPen(WALL_COLOR.lighter(140), 2, Qt.DashLine))
        p.setBrush(QColor(WALL_COLOR.red(), WALL_COLOR.green(),
                          WALL_COLOR.blue(), 50))
        p.drawRect(QRectF(a, b))
        p.setPen(QColor(190, 215, 255))
        p.drawText(QRectF(a.x(), a.y() - 16, 220, 14),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   "新墙 %d×%d" % (int(abs(x1 - x0)), int(abs(z1 - z0))))

    def _draw_viewbox(self, p):
        """游戏画面范围框：标出游戏里这一屏真正看得到的那块。

        尺寸 = video.txt 的模式（video 1 → 480×272，不是写死的 320×240）。
        框**相对背景（关卡）固定**：滚动视野它跟着背景走，只有用鼠标拖它才动；
        拖动是自由的（横向纵向都能挪），不再锁死在滚动轴上。
        """
        vx, vz = self.vb_value()
        box = QRectF(self.sx(vx), self.sy(vz),
                     self.vw() * self.zoom, self.vh() * self.zoom)
        tip = (f"游戏画面 {int(self.vw())}×{int(self.vh())}　"
               f"x {int(vx)}~{int(vx + self.vw())}　"
               f"z {int(vz)}~{int(vz + self.vh())}（拖框移动）")
        W, H = float(self.width()), float(self.height())
        # 框外压暗（上下左右四块）
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(6, 8, 12, 96))
        for r in (QRectF(0.0, 0.0, W, max(0.0, box.top())),
                  QRectF(0.0, min(H, box.bottom()), W,
                         max(0.0, H - box.bottom())),
                  QRectF(0.0, max(0.0, box.top()), max(0.0, box.left()),
                         max(0.0, box.height())),
                  QRectF(min(W, box.right()), max(0.0, box.top()), W,
                         max(0.0, box.height()))):
            p.drawRect(r)
        p.setPen(QPen(QColor(255, 255, 255, 190), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(box)
        p.setPen(QColor(235, 240, 250))
        p.drawText(QRectF(box.left() + 4, max(2.0, box.top() + 2), 190, 14),
                   Qt.AlignLeft | Qt.AlignVCenter, tip)

    def _draw_screen_band(self, p):
        """屏幕预览：把「当前这一屏」在关卡长度上占的那一竖条标出来。

        只画左右两条竖线（宽 320 = 一屏），不再画一个把 z 也框住的方框：
        那个框会压在 zmin 上面、还会让人误以为背景该跟着它走。
        """
        x0 = self.sx(self.scroll_x)
        x1 = self.sx(self.scroll_x + self.vw())
        p.setPen(QPen(QColor(255, 255, 255, 130), 2, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawLine(int(x0), 0, int(x0), self.height())
        p.drawLine(int(x1), 0, int(x1), self.height())
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(min(x0, x1) + 3, self.height() - 18, 140, 14),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   f"屏 {int(self.scroll_x)}~{int(self.scroll_x + self.vw())}")

    def _draw_player(self, p, k, x, z):
        sp = QPointF(self.sx(x), self.sy(z))
        p.setPen(QPen(QColor(120, 200, 255), 2))
        p.setBrush(QColor(120, 200, 255, 70))
        p.drawEllipse(QRectF(sp.x() - 7, sp.y() - 7, 14, 14))
        p.setPen(QColor(200, 235, 255))
        p.drawText(QRectF(sp.x() - 20, sp.y() - 26, 60, 13),
                   Qt.AlignLeft | Qt.AlignVCenter, f"P{k}")

    # ---- 两种显示模式下的“画在哪 / 落在哪” ----
    def _group_x(self, i):
        """列模式：这个生成点应该画在哪条竖线上（= 它所属 group 的 at）。"""
        d = self.doc
        g = self._owner_of(i)
        if g is not None:
            return d.objects[g].at or 0
        o = d.objects[i]
        return o.at if o.at is not None else (o.abs_x or 0)

    def _gs(self):
        """当前生效的 group 过滤（下标失效了就自动取消）。"""
        g = self.group_sel
        if g is None:
            return None
        if self.doc is None or not (0 <= g < len(self.doc.objects)) \
                or self.doc.objects[g].cmd != "group":
            self.group_sel = None
            return None
        return g

    def _owner_of(self, i):
        """生成点 i 属于哪个 group（对象下标）；无组返回 None。"""
        key = (id(self.doc), getattr(self.doc, "_rev", 0))
        if self._owners.get("key") != key:
            self._owners = {"key": key,
                            "map": dict(self.doc.group_owner())}
        return self._owners["map"].get(i)

    def _col_top(self):
        """按组排列：第一行应该从哪画起 —— 场景画面（zmax 线）的下方。

        实体条排在场景画面下面，正好和画面底边的位置标记（at 竖线）对上。
        """
        d = self.doc
        if d is None:
            return float(COL_TOP)
        _zmin, zmax, _bgh = d.z_range()
        return max(float(COL_TOP), self.sy(zmax) + 12.0)

    def _col_bottom(self):
        """按组排列：行区域的下缘（给底部标记条留出位置）。"""
        return max(self._col_top() + 60.0, float(self.height()) - 46.0)

    def _row_max(self):
        """行滚动能滚到最大值（内容比可视区域多出来的部分）。

        先重算一遍单元格（顺带记下**未滚动坐标**里的内容底边），
        这样不依赖“上一帧的 row_scroll 是多少”。
        """
        self._col_rows()
        if not self._col_cells:
            return 0.0
        return max(0.0, self._col_content - self._col_bottom())

    def _clamp_row_scroll(self):
        self.row_scroll = min(self._row_max(), max(0.0, self.row_scroll))

    def _focus_col_cell(self, i):
        """按组排列：把生成点 i 的单元格滚进视野（定位到**条目**上）。

        横向卷轴先按组的 at 挪 scroll_x，再按单元格的 y 挪 row_scroll；
        纵向卷轴条目 x 是固定的，只挪 row_scroll。
        """
        d = self.doc
        if d is None:
            return
        _sd, vert = d.scroll_dir()
        if not vert:
            gx = self._group_x(i)
            vw = self.view_w()
            if not (self.scroll_x + 24 <= gx <= self.scroll_x + vw - 24):
                self.scroll_x = max(0.0, gx - vw * 0.35)
        self._col_rows()
        cell = self._col_cells.get(i)
        if cell is None:
            return
        top = self._col_top()
        bot = self._col_bottom()
        if cell.top() < top:
            self.row_scroll -= (top - cell.top())
        elif cell.bottom() > bot:
            self.row_scroll += cell.bottom() - bot
        self._clamp_row_scroll()
        self.update()

    def _draw_rowscroll(self, p):
        """行滚动指示条：实体太多、一屏摆不下时出现在右缘。"""
        mx = self._row_max()
        if mx <= 0:
            return
        top, bot = self._col_top(), self._col_bottom()
        span = bot - top
        if span < 40:
            return
        th = max(24.0, span * span / (span + mx))
        y = top + (self.row_scroll / mx) * (span - th)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.setBrush(QColor(255, 255, 255, 60))
        p.drawRect(QRectF(self.width() - 9.0, y, 6.0, th))

    def _col_rows(self):
        """按组排列：[(对象下标, 屏幕 x, 屏幕 y, 组内序号)]，顺带填好
        self._col_cells（单元格矩形，画和命中判定共用）。

        横向卷轴关卡：一个组排成一**列**（上下紧挨，列对在该组的 at 竖线上）；
        纵向卷轴关卡：一个组排成一**行**（从左到右，行首对着左侧边的组标记）。
        每个单元格**以编号 / 名字为主**，帧画面裁剪缩放后只在旁边作辅助。
        行整体从 zmax 线（场景画面下缘）往下排，和底边的位置标记对应。
        """
        d = self.doc
        out = []
        self._col_ord = {}
        self._col_cells = {}
        gs = self._gs()
        buckets = []                       # [(owner, [idx...])]，保持文件顺序
        seen = {}
        for i, o in enumerate(d.objects):
            if o.cmd != "spawn" or o.coords is None:
                continue
            if gs is not None and self._owner_of(i) != gs:
                continue          # 只看某一个 group
            own = self._owner_of(i)
            if own in seen:
                buckets[seen[own]][1].append(i)
            else:
                seen[own] = len(buckets)
                buckets.append((own, [i]))
        fm = QFontMetrics(self.font())
        _sd, vert = d.scroll_dir()
        top = self._col_top()
        if vert:
            # 纵向卷轴：一个组一行，组内按文件顺序从左往右排
            for r, (_own, idxs) in enumerate(buckets):
                y = top + r * (COL_ROW_H + 6) - self.row_scroll
                x = COL_SIDE + 8 - self.scroll_x * self.zoom
                for n, i in enumerate(idxs):
                    w = self._col_cell_w(fm, i, n)
                    self._col_cells[i] = QRectF(x, y, w, COL_ROW_H)
                    out.append((i, x, y, n))
                    self._col_ord[i] = n
                    x += w + 6.0
        else:
            # 横向卷轴：一个组一列；同一 at 上的几个组横向错开
            lanes = {}
            for _own, idxs in buckets:
                gx = self._group_x(idxs[0])
                lane = lanes.get(round(gx), 0)
                lanes[round(gx)] = lane + 1
                px = self.sx(gx) + lane * (COL_SIDE + 8)
                y = top - self.row_scroll
                for n, i in enumerate(idxs):
                    w = self._col_cell_w(fm, i, n)
                    self._col_cells[i] = QRectF(px, y, w, COL_ROW_H)
                    out.append((i, px, y, n))
                    self._col_ord[i] = n
                    y += COL_ROW_H
        # 内容底边（未滚动坐标）：单元格屏幕底边 + row_scroll
        if self._col_cells:
            self._col_content = max(c.bottom() for c in
                                    self._col_cells.values()) + self.row_scroll
        else:
            self._col_content = 0.0
        return out

    def _col_cell_w(self, fm, i, n):
        """按组排列：一个单元格的宽 = 缩略图位 + “#n 名字”文字宽 + 内边距。"""
        o = self.doc.objects[i]
        w = THUMB_W + 8 + fm.width(f"#{n + 1} {o.name}") + 12
        return max(64.0, float(w))

    def spawn_places(self):
        """[(对象下标, 屏幕 x, 屏幕 y)]：当前显示模式下生成点画在哪（屏幕坐标）。"""
        d = self.doc
        if d is None:
            return []
        if self.layout == "col":
            return [(i, px, py) for i, px, py, _n in self._col_rows()]
        out = []
        for i, o in enumerate(d.objects):
            if o.cmd != "spawn" or o.coords is None:
                continue
            gs = self._gs()
            if gs is not None and self._owner_of(i) != gs:
                continue          # 只看某一个 group
            out.append((i, self.sx(o.abs_x),
                        self.sy((o.z or 0)
                                - (o.coords[2] if o.coords else 0))))
        return out

    def _col_order_of(self, i):
        """按组排列：这个生成点在组里排第几个（从 0 起）。"""
        if getattr(self, "_col_ord", None) is None:
            self._col_rows()
        return (self._col_ord or {}).get(i, 0)

    def _draw_objects(self, p):
        d = self.doc
        self._mk_rows = self.marker_rows()      # 标记排布（画和命中判定共用）
        # group / wait / light 标记先画（在底层）
        for i, o in enumerate(d.objects):
            if o.cmd != "spawn":
                self._draw_marker(p, o, i)
        if self.layout == "col":
            self._draw_col_cells(p)
            return
        for i, px, py in self.spawn_places():
            o = d.objects[i]
            if i == self.sel and o.at is not None:
                # at 位置画一条竖虚线，说明“滚到这里才生成”
                p.setPen(QPen(QColor(255, 255, 255, 70), 1, Qt.DashLine))
                p.drawLine(int(self.sx(o.at)), 0,
                           int(self.sx(o.at)), self.height())
            sp = QPointF(px, py)
            warn = o.z_out(*d.z_range()[:2])
            bad = self._ent_bad(o.name)
            map_no = o.prop_int("map", 0)
            im, ox, oy, over, dms = self.panel.entity_spawn_view(
                o.name, map_no)
            if im is not None:
                k = self.zoom * 0.8
                if dms is not None and dms["on"]:
                    # idle 首帧带 drawmethod：几何变换绕 offset 点
                    # （裁剪后即 (ox, oy)），画布坐标 = sp + (T(P)-支点)*k，
                    # 和动画编辑页同一套规则（bor_dm.geometry / paint）
                    p.save()
                    p.translate(sp.x(), sp.y())
                    p.scale(k, k)
                    p.translate(-ox, -oy)
                    g = bl.geometry(dms, QPointF(ox, oy))
                    if g is not None:
                        p.setTransform(g, True)
                    bl.paint(p, im, QPointF(0.0, 0.0), 1.0, dms)
                    p.restore()
                else:
                    w = max(6.0, im.width() * k)
                    h = im.height() * (w / max(1, im.width()))
                    p.drawImage(QRectF(
                        sp.x() - w * (ox / max(1, im.width())),
                        sp.y() - h * (oy / max(1, im.height())), w, h), im)
                # 选中框 / 命中框仍用未变换的占位（变换只影响外观）
                box = QRectF(
                    sp.x() - im.width() * k * (ox / max(1, im.width())),
                    sp.y() - im.height() * k * (oy / max(1, im.height())),
                    im.width() * k, im.height() * k)
            else:
                box = QRectF(sp.x() - 7, sp.y() - 9, 14, 18)
                p.setPen(QPen(WARN_COLOR if (warn or bad) else MARK_COLOR, 1))
                p.setBrush(QColor(255, 205, 90, 90))
                p.drawRect(box)
            if warn:
                # z 跑到可走范围之外：画个醒目的虚线圈，游戏里它会浮在半空
                p.setPen(QPen(WARN_COLOR, 2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(box.adjusted(-7, -7, 7, 7))
            if i == self.sel:
                p.setPen(QPen(SEL_COLOR, 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(box.adjusted(-3, -3, 3, 3))
            if self.zoom > 0.12:
                p.setPen(WARN_COLOR if (warn or bad) else QColor(240, 246, 255))
                p.drawText(QRectF(box.center().x() - 70, box.bottom() + 1,
                                  180, 13),
                           Qt.AlignCenter,
                           f"{o.name}  z{int(o.z or 0)}" +
                           (f" at{o.at}" if o.at else "") +
                           (" ⚠" if warn else "") +
                           (" ⚠map 超界" if over else "") +
                           (" ⚠实体未找到" if bad else ""))

    def _ent_bad(self, name):
        """实体无效 = models.txt 三步校验没过（见 LevelPanel._entity_doc）。

        拿不到 models.txt 索引时不判断 —— 没得比对，宁可不标也不能误报。
        """
        b = getattr(self.panel, "bor", None)
        if b is None:
            return False
        models = getattr(b, "models", None)
        if not getattr(models, "entities", None):
            return False
        return self.panel._entity_doc(name) is None

    def _draw_col_cells(self, p):
        """按组排列模式的单元格：缩略图（裁剪缩小）+「#n 名字」主信息。"""
        d = self.doc
        self._col_rows()                  # 顺便刷新 _col_cells
        bot = self._col_bottom()
        for i, cell in self._col_cells.items():
            if cell.bottom() < 0 or cell.top() > bot:
                continue
            o = d.objects[i]
            n = self._col_ord.get(i, 0)
            sel = (i == self.sel)
            bad = self._ent_bad(o.name)
            p.setPen(QPen(SEL_COLOR if sel else QColor(255, 255, 255, 60),
                          2 if sel else 1))
            p.setBrush(QColor(92, 64, 44, 170) if sel
                       else QColor(58, 64, 76, 150))
            p.drawRect(cell)
            # 缩略图：裁过背景色的 idle 帧，缩进 THUMB_W×THUMB_H
            map_no = o.prop_int("map", 0)
            im, _ox, _oy, over = self.panel.entity_thumb(o.name, map_no)
            if im is not None:
                k = min(THUMB_W / max(1, im.width()),
                        THUMB_H / max(1, im.height()))
                w, h = im.width() * k, im.height() * k
                p.drawImage(QRectF(cell.x() + 3 + (THUMB_W - w) / 2,
                                   cell.y() + (COL_ROW_H - h) / 2, w, h), im)
            else:
                p.setPen(QPen(WARN_COLOR if bad else MARK_COLOR, 1))
                p.setBrush(QColor(255, 205, 90, 90))
                p.drawRect(QRectF(cell.x() + 15, cell.y() + 8, 24,
                                  COL_ROW_H - 16))
            txt = f"#{n + 1} {o.name}"
            if over:
                txt += " ⚠map 超界"
            if bad:
                txt += " ⚠实体未找到"
            p.setPen(WARN_COLOR if (over or bad) else QColor(240, 246, 255))
            p.drawText(QRectF(cell.x() + THUMB_W + 8, cell.y(),
                              cell.width() - THUMB_W - 10, COL_ROW_H),
                       Qt.AlignLeft | Qt.AlignVCenter, txt)

    # ---- group / wait / light 标记 ----
    def marker_rows(self):
        """{at: [对象下标]}：同一个 at 上的标记，按 group → wait → 其它排。

        同一个位置常常同时有 group 和 wait，甚至连着好几个 group，
        所以这里先把它们归堆，再从上往下错开画，免得叠成一坨。
        """
        d = self.doc
        buckets = {}
        for i, o in enumerate(d.objects):
            if o.cmd == "spawn" or o.at is None:
                continue
            buckets.setdefault(int(round(o.at)), []).append(i)
        for k in buckets:
            buckets[k].sort(key=lambda i: (
                {"group": 0, "wait": 1}.get(d.objects[i].cmd, 2), i))
        return buckets

    def _marker_geom(self, at, row, n):
        """标记画的屏幕坐标：同一堆里从上往下排，最下面那个贴着底边。"""
        y_base = self.height() - 26 - (n - 1) * 17
        y_base = max(56, y_base)      # 别顶到左上角 HUD
        return y_base + row * 17

    def _side_markers(self):
        """纵向卷轴 + 按组排列时的**侧边**标记栏：[对象下标]（按文件顺序）。

        纵向关卡里标记的 at 是深度值，画在横轴上没意义，所以挪到左边成一列。
        """
        d = self.doc
        return [i for i, o in enumerate(d.objects)
                if o.cmd != "spawn"]

    def _side_geom(self, k):
        """侧边栏第 k 条的矩形。"""
        return QRectF(4, self._col_top() + k * 22 - self.row_scroll,
                      COL_SIDE - 8, 20)

    def _group_mark(self, o, i):
        """group 标记的文字（带组号）与「首参数 ≠ 实际实体数」警告。"""
        txt = f"G{self.doc.group_no(i) + 1} {o.group_label}"
        warn = False
        if o.cmd == "group":
            n = len(self.doc.group_members(i))
            mn = bl._i(o.args[0]) if o.args else 0
            txt += f"（实 {n}）"
            if o.args and mn != n:
                txt += f"⚠min{mn}≠{n}"
                warn = True
        return txt, warn

    def _draw_marker(self, p, o, i):
        selg = (o.cmd == "group" and i == self._gs())
        col = {"group": QColor(180, 160, 255), "wait": QColor(255, 160, 90),
               "light": QColor(255, 240, 140)}.get(o.cmd,
                                                   QColor(150, 160, 175))
        txt, gw = self._group_mark(o, i) if o.cmd == "group" else (
            (o.title if len(o.title) < 14 else o.cmd), False)
        _sd, vert = self.doc.scroll_dir()
        if vert and self.layout == "col":
            # 纵向卷轴的按组排列：标记条挪到**左侧边栏**，按文件顺序排
            try:
                k = self._side_markers().index(i)
            except ValueError:
                return
            box = self._side_geom(k)
            if box.bottom() < 0 or box.top() > self.height():
                return
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col.red(), col.green(), col.blue(),
                              210 if selg else 110))
            p.drawRect(box)
            p.setPen(QPen(WARN_COLOR if gw else col, 2 if selg else 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(box)
            p.setPen(QColor(20, 22, 28) if selg else
                     (WARN_COLOR if gw else col))
            p.drawText(box.adjusted(3, 0, -3, 0),
                       Qt.AlignLeft | Qt.AlignVCenter, txt)
            return
        if o.at is None:
            return
        sx = self.sx(o.at)
        if not (-60 <= sx <= self.width() + 60):
            return
        buckets = self._mk_rows
        idxs = buckets.get(int(round(o.at)), [])
        n = len(idxs)
        try:
            row = idxs.index(i)
        except ValueError:
            row = 0
        y = self._marker_geom(o.at, row, n)
        # wait 稍微往右挪一点，和同一位置的 group 错开
        dx = 7 if o.cmd == "wait" else 0
        # 竖线：从最上面那个标记一直画到底边
        if row == 0:
            p.setPen(QPen(col, 3 if selg else 2))
            p.drawLine(int(sx), int(y) - 6, int(sx), self.height() - 8)
        fm = p.fontMetrics()
        w = min(170, fm.width(txt) + 10)
        box = QRectF(sx + dx - 3, y - 8, w, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(col.red(), col.green(), col.blue(),
                          200 if selg else 120))
        p.drawRect(box)
        p.setPen(QPen(WARN_COLOR if gw else col, 2 if (selg or gw) else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(box)
        p.setPen(QColor(20, 22, 28) if selg else (WARN_COLOR if gw else col))
        p.drawText(box.adjusted(2, 0, -2, 0), Qt.AlignLeft | Qt.AlignVCenter,
                   txt)

    def _hud_lines(self, zmin, zmax):
        """左上角显示的行（只留关键信息 + 必要的警告）。"""
        d = self.doc
        L = d.length(self.panel.root_dirs)
        way = "实际位置" if self.layout == "pos" else "按组排列"
        sd, vert = d.scroll_dir()
        lines = [f"{os.path.basename(d.path or '')}　长度 {int(L)}　"
                 f"z {int(zmin)}~{int(zmax)}　缩放 {self.zoom:.2f}×　"
                 f"{'全景' if self.mode == 'map' else '屏幕预览'}　"
                 f"显示：{way}　卷轴：{d.scroll_arrow()} {sd}"
                 f"（{'纵向' if vert else '横向'}）"]
        bad = d.out_of_band()
        if bad and self.layout != "col":    # 按组排列只是排版，不关心 z 越界
            lines.append(f"⚠ {len(bad)} 个生成点的 z 在 "
                         f"{int(zmin)}~{int(zmax)} 之外（游戏里会浮空/贴顶）")
        gs = self._gs()
        if gs is not None:
            lines.append(f"只看 G{d.group_no(gs) + 1} "
                         f"{d.objects[gs].group_label}　"
                         f"（按右侧「显示全部」恢复）")
        return lines

    def _draw_hud(self, p, zmin, zmax):
        for i, t in enumerate(self._hud_lines(zmin, zmax)):
            p.drawText(QRectF(6, 4 + i * 15, self.width() - 12, 15),
                       Qt.AlignLeft | Qt.AlignVCenter, t)

    # ---- 交互 ----
    def marker_at(self, pos, tol=14):
        """鼠标落在哪个**生成点**上（返回对象下标）。"""
        d = self.doc
        if d is None:
            return None
        if self.layout == "col":
            if not self._col_cells:
                self._col_rows()
            for i, cell in self._col_cells.items():
                if cell.adjusted(-2, -2, 2, 2).contains(QPointF(pos)):
                    return i
            return None
        best, bd = None, 1e9
        for i, px, py in self.spawn_places():
            o = d.objects[i]
            if abs(pos.x() - px) > tol + 6:
                continue
            im, _ox, _oy, _over = self.panel.entity_thumb(o.name)
            h = 26 if im is None else im.height() * self.zoom * 0.8
            if not (-h - tol <= pos.y() - py <= tol):
                continue
            dd = (pos.x() - px) ** 2 + (pos.y() - py) ** 2
            if dd < bd:
                bd, best = dd, i
        return best

    def group_bar_at(self, pos):
        """鼠标落在哪个 group / wait 标记条上（返回对象下标）。"""
        d = self.doc
        if d is None:
            return None
        _sd, vert = d.scroll_dir()
        if vert and self.layout == "col":
            for k, i in enumerate(self._side_markers()):
                if self._side_geom(k).contains(QPointF(pos)):
                    return i
            return None
        for at, idxs in (self._mk_rows or {}).items():
            sx = self.sx(at)
            n = len(idxs)
            for row, i in enumerate(idxs):
                o = d.objects[i]
                dx = 7 if o.cmd == "wait" else 0
                y = self._marker_geom(at, row, n)
                if 0 <= pos.y() - (y - 8) <= 16 \
                        and -4 <= pos.x() - (sx + dx) <= 170:
                    return i
        return None

    def zline_at(self, pos, tol=5):
        """鼠标是不是落在 zmin / zmax 虚线上（可拖动改 z 范围）。"""
        d = self.doc
        if d is None:
            return None
        zmin, zmax, _ = d.z_range()
        for z, which in ((zmax, "max"), (zmin, "min")):
            if abs(pos.y() - self.sy(z)) <= tol:
                return which
        return None

    def vb_range(self):
        """画面框允许的滑动范围（关卡坐标），x 和 z 都能自由挪：
        ((x下限, x上限), (z下限, z上限))。"""
        d = self.doc
        vw, vh = self.vw(), self.vh()
        L = max(vw, d.length(self.panel.root_dirs))
        zmin, zmax, bgh = d.z_range()
        hi = max(float(zmax), float(bgh), vh)
        return ((0.0, max(0.0, L - vw)),
                (0.0, max(0.0, hi - vh)))

    def vb_value(self):
        """画面框当前位置 (x, z)（没拖过就停在关卡起点）。"""
        if self.vb_pos is not None:
            return self.vb_pos
        (xlo, _xhi), (zlo, _zhi) = self.vb_range()
        return (xlo, zlo)

    def vbox_at(self, pos):
        """鼠标是否落在画面框上（框边 6px 内算“框边”，内部算“框内”）。"""
        if not self.show_viewbox or self.doc is None:
            return None
        vx, vz = self.vb_value()
        box = QRectF(self.sx(vx), self.sy(vz),
                     self.vw() * self.zoom, self.vh() * self.zoom)
        q = QPointF(pos)
        edge = box.adjusted(-6, -6, 6, 6)
        inner = box.adjusted(6, 6, -6, -6)
        if edge.contains(q) and not inner.contains(q):
            return "edge"
        if box.contains(q):
            return "in"
        return None

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.MidButton:
            self._middle_add(e.pos())     # 中键：竖线 + 加 wait / group 菜单
            return
        if e.button() != Qt.LeftButton:
            return
        # 先试试是不是点在 group / wait 标记条上
        g = self.group_bar_at(e.pos())
        if g is not None:
            self.panel.select_group(g)
            return
        # 选中墙的把手：拖角改形状 / 拖底边中点整体移动
        h = self.wall_handle_at(e.pos())
        if h is not None:
            idx, hid = h
            args = self.panel.wall_args(idx)
            if args:
                self.panel._snapshot()
                self._drag = ("wall_h", idx, hid,
                              self.to_level(e.pos()), args)
                return
        i = self.marker_at(e.pos())
        if i is not None:
            self.sel = i
            self.panel.select_wall(None)      # 生成点和墙二选一
            self.panel.select_object(i, from_canvas=True)
            o = self.doc.objects[i]
            if self.layout == "col":
                # 按组排列：顺着排布方向拖 = 改同组里的生成顺序（松手才落盘）
                self._drag = ("order", i, e.pos())
                self.update()
                return
            c = o.coords or (0.0, 0.0, 0.0)
            # 记下按下时的位置**和**实体原本的 coords：拖动按“位移”算，
            # 这样点在脑袋上也不会让实体先跳一大截
            self.panel._snapshot()
            self._drag = ("obj", i, self.to_level(e.pos()), e.pos(),
                          (c[0], c[1] if len(c) > 1 else 0.0))
            self.update()
            return
        # 点在墙上：选中并进入“整体拖动”（像画图工具里拖一个图形）
        w = self.wall_at(e.pos())
        if w is not None:
            args = self.panel.wall_args(w)
            self.panel.select_wall(w)
            if args:
                self.panel._snapshot()
                self._drag = ("wall_move", w,
                              self.to_level(e.pos()), args)
            self.update()
            return
        # 画墙模式：空白处拖出一个矩形
        if self.wall_draw:
            st = self.to_level(e.pos())
            self._drag = ("wall_new", st, st)
            self.wall_preview = (st[0], st[1], st[0], st[1])
            self.update()
            return
        # zmin / zmax 虚线：直接上下拖（和右下角数值联动，松手写回）
        zl = self.zline_at(e.pos())
        if zl is not None:
            self.panel._snapshot()      # z 写的是 levels.txt，快照一起带上它
            self._drag = ("zline", zl)
            return
        # 画面框：框边（或框内空白处）拖动 —— 框相对背景固定，只有拖才动
        if self.vbox_at(e.pos()):
            self._drag = ("vbox", self.to_level(e.pos()), self.vb_value())
            self.setCursor(Qt.ClosedHandCursor)
            return
        # 空白处：只平移视图，**不清掉 group 过滤**（要恢复请按“显示全部”）
        if self.wall_sel is not None:
            self.panel.select_wall(None)
        # 按组排列时纵向拖的是“行滚动”（背景 / panel 不动），否则是 z
        self._drag = ("pan", e.pos(), self.scroll_x,
                      self.row_scroll if self.layout == "col"
                      else self.scroll_z)

    def mouseMoveEvent(self, e):
        if not self._drag:
            # 悬停反馈：把手上给改形状的十字箭头，画墙模式给十字准线
            if self.wall_handle_at(e.pos()) is not None:
                self.setCursor(Qt.SizeFDiagCursor)
            elif self.wall_draw:
                self.setCursor(Qt.CrossCursor)
            elif self.wall_at(e.pos()) is not None:
                self.setCursor(Qt.PointingHandCursor)
            elif self.zline_at(e.pos()) is not None:
                self.setCursor(Qt.SizeVerCursor)      # zmin/zmax 虚线可拖
            elif self.vbox_at(e.pos()):
                self.setCursor(Qt.OpenHandCursor)     # 画面框可拖
            else:
                self.unsetCursor()
            return
        if self._drag[0] == "pan":
            _, st, sx, sz = self._drag
            self.scroll_x = sx - (e.pos().x() - st.x()) / self.zoom
            if self.layout == "col":
                # 按组排列：纵向只滚动“行”，背景 / panel / 地形钉住不动
                self.row_scroll = max(0.0, sz - (e.pos().y() - st.y()))
                self._clamp_row_scroll()
            else:
                self.scroll_z = sz - (e.pos().y() - st.y()) / self.zoom
            self.panel.sync_scroll()
            self.update()
            return
        if self._drag[0] == "zline":
            self.panel.drag_zline(self._drag[1], self.to_level(e.pos())[1])
            return
        if self._drag[0] == "vbox":
            _, start, orig = self._drag
            cur = self.to_level(e.pos())
            # 自由拖动：x / z 两个方向都能挪（各 clamp 在允许范围内）
            dvx = cur[0] - start[0]
            dvz = cur[1] - start[1]
            (xlo, xhi), (zlo, zhi) = self.vb_range()
            self.vb_pos = (min(xhi, max(xlo, orig[0] + dvx)),
                           min(zhi, max(zlo, orig[1] + dvz)))
            self.update()
            return
        if self._drag[0] == "wall_new":
            _, st, _ = self._drag
            cur = self.to_level(e.pos())
            self._drag = ("wall_new", st, cur)
            self.wall_preview = (st[0], st[1], cur[0], cur[1])
            self.update()
            return
        if self._drag[0] == "order":
            # 改生成顺序：拖动中先不动文件，松手时才落盘（免得每像素写一次）
            self.update()
            return
        if self._drag[0] == "wall_h":
            _, idx, hid, start, orig = self._drag
            self.panel.drag_wall_handle(idx, hid, self.to_level(e.pos()),
                                        start, orig)
            self.update()
            return
        if self._drag[0] == "wall_move":
            _, idx, start, orig = self._drag
            cur = self.to_level(e.pos())
            self.panel.move_wall(idx, orig,
                                 cur[0] - start[0], cur[1] - start[1])
            self.update()
            return
        _, i, start, pstart, orig = self._drag
        cur = self.to_level(e.pos())
        mods = e.modifiers()
        only_z = bool(mods & Qt.ShiftModifier)
        only_x = bool(mods & Qt.ControlModifier)
        dz = cur[1] - start[1]
        dx = cur[0] - start[0]
        # Shift = 只改 z（纵向），Ctrl = 只改水平位置
        nx = orig[0] + (0.0 if only_z else dx)
        nz = orig[1] + (0.0 if only_x else dz)
        self.panel.move_object(i, nx, nz)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[0] == "order":
            _, i, pstart = self._drag
            _sd, vert = self.doc.scroll_dir()
            # 横向卷轴看纵向拖动，纵向卷轴看横向拖动；拖过一格 ≈ 挪一位
            dd = (e.pos().x() - pstart.x()) if vert \
                else (e.pos().y() - pstart.y())
            steps = int(round(dd / float(COL_ROW_H)))
            self._drag = None
            if steps:
                self.panel.reorder_object(i, steps)
            self.update()
            return
        if self._drag and self._drag[0] == "zline":
            self._drag = None
            self.panel.apply_z(silent=True)       # 松手才写回 levels.txt
            return
        if self._drag and self._drag[0] == "wall_new":
            r = self.wall_preview
            self.wall_preview = None
            self._drag = None
            if r:
                self.panel.add_wall(r)
            self.update()
            return
        self._drag = None
        self.update()

    def mouseDoubleClickEvent(self, e):
        i = self.marker_at(e.pos())
        if i is not None:
            # 双击生成点：选中（视野不动），并跳到动画编辑页看它的 idle
            self.panel.select_object(i)
            d = self.doc
            if d is not None and 0 <= i < len(d.objects) \
                    and d.objects[i].cmd == "spawn":
                self.panel.jump_to_entity(d.objects[i].name)

    def _middle_add(self, pos):
        """中键：在这里竖一条参考线，选「加 wait / 加 group」。"""
        d = self.doc
        if d is None:
            return
        x, z = self.to_level(pos)
        _sd, vert = d.scroll_dir()
        at = int(round(z if vert else x))
        self._mid_line = at
        self.update()
        m = QMenu(self)
        a_wait = m.addAction(f"在此处添加 wait（at {at}）")
        a_group = m.addAction(f"在此处添加 group（at {at}）")
        act = m.exec_(QCursor.pos())
        self._mid_line = None
        if act is a_wait:
            self.panel.add_marker("wait", [], at)
        elif act is a_group:
            self.panel.add_marker("group", ["1", "1"], at)
        self.update()

    def contextMenuEvent(self, e):
        """右键：点到实体 → 复制/删除/换实体；点到 group 条 → 加实体/清空。"""
        d = self.doc
        if d is None:
            return
        i = self.marker_at(e.pos())
        if i is not None:
            self.sel = i
            self.panel.select_wall(None)
            self.panel.select_object(i, from_canvas=True)
            self.panel.spawn_menu(i, e.globalPos())
            return
        g = self.group_bar_at(e.pos())
        if g is not None and d.objects[g].cmd == "group":
            self.panel.group_menu(g, e.globalPos())

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        hx = e.angleDelta().x()
        # 左右滚轮（倾斜滚轮）：直接左右滚画面，不用再按住 Shift
        if hx:
            self.scroll_x += 40 * hx / 120.0 / max(0.05, self.zoom)
            self.panel.sync_scroll()
            self.update()
            if not d:
                return
        if not d:
            return
        mods = e.modifiers()
        if mods & Qt.ControlModifier:
            self.zoom = min(4.0, max(0.02,
                                     self.zoom * (1.2 if d > 0 else 0.83)))
        elif mods & Qt.ShiftModifier:
            self.scroll_x += (-160 if d > 0 else 160) / max(0.05, self.zoom)
            self.panel.sync_scroll()
        elif self.layout == "col":
            # 按组排列：纵向滚轮动的是“行”，背景 / panel 钉住不动
            self.row_scroll = max(0.0, self.row_scroll
                                  + (-40 if d > 0 else 40))
            self._clamp_row_scroll()
        else:
            self.scroll_z += (-40 if d > 0 else 40) / self.zoom
        self.update()


# ============================================================ 面板
class LevelPanel(QWidget):
    """关卡编辑标签页。"""

    def __init__(self, main=None):
        super().__init__()
        self.main = main
        self.bor = None              # OpenBOR 动画面板（借它的实体 / 根目录）
        self.levels = None           # bl.LevelsTxt
        self.doc = None              # bl.LevelDoc
        self.levels_path = ""
        self._loading = False
        self._saved = ""             # 载入时的原文（判断是否真有改动）
        self._docs = {}              # 实体名 -> EntityDoc（给生成点画图标用）
        self._thumbs = {}            # (实体名, map号) -> (图, ox, oy, 超界)
        self._thumb_pils = {}        # 同键 -> 裁剪后的 PIL（烘 drawmethod 颜色用）
        self._dm_imgs = {}           # (实体名, map号, 色键) -> 烘过色的图
        self._pal_cache = {}         # 实体路径 -> 换色表列表
        self._ent_reason = {}        # 实体名 -> “未找到”的具体原因
        self._clamped = {}           # 当前对象里“值超出数值框范围”的字段
        self._undo = []              # 撤销栈（整个关卡文本的快照）
        self._redo = []
        self._cur_path = ""          # 当前关卡在 levels.txt 里的路径（取消切换时跳回）
        self._obj_line = -1          # 文本编辑器光标上次所在行（行变了才同步）
        self.setObjectName("borRoot")
        self.setStyleSheet(QSS)
        self._build()
        self.clear_cache()

    # ---------------- 对外数据 ----------------
    @property
    def root_dirs(self):
        """资源根目录：levels.txt 所在处最优先（这一页改的就是它），
        其次动画页导入的 data 目录，最后是它的上级。
        """
        out = []
        if self.levels_path:
            d = os.path.dirname(self.levels_path)
            out += [d, os.path.dirname(d), os.path.dirname(os.path.dirname(d))]
        b = self.bor
        if b is not None:
            if getattr(b, "root_dirs", None):
                out += list(b.root_dirs)
            elif getattr(b, "data_dir", None):
                out += [b.data_dir, os.path.dirname(b.data_dir)]
        seen, res = set(), []
        for d in out:
            if d and os.path.isabs(d):
                k = os.path.normcase(os.path.abspath(d))
                if k not in seen:
                    seen.add(k)
                    res.append(d)
        return res

    def video_size(self):
        """video.txt → (宽, 高, zmin, zmax, BGheight)，按 root_dirs 缓存。"""
        return bl.read_video_txt(self.root_dirs)

    def view_height(self):
        d = self.doc
        if d is None:
            return 260
        # 场景可视高度按视频模式来（video 1 → 272，而不是写死的 260）
        return float(self.video_size()[1])

    def cache(self):
        self.canvas._imgs.clear()

    def clear_cache(self):
        if getattr(self, "canvas", None) is not None:
            self.canvas._imgs.clear()
        self._thumbs.clear()
        self._thumb_pils.clear()
        self._dm_imgs.clear()
        self._docs.clear()
        self._ent_reason.clear()

    # ---------------- 实体缩略图（裁背景 + map 换色） ----------------
    def _entity_palettes(self, doc):
        """实体的换色表列表：remap / alternatepal 按声明顺序。

        每项是一张**生效色表**（256 色，下标 = 原图像素索引）：
        - alternatepal {图}：直接用那张图的色表整表替换；
        - remap {图A} {图B}：A、B 两张图逐像素对比得出“旧索引 → 新索引”
          的映射（OpenBOR 就是这么做的），生效色 = B 的色表[映射后的索引]；
          尺寸对不上就跳过这一条。
        """
        key = getattr(doc, "path", None) or id(doc)
        if key in self._pal_cache:
            return self._pal_cache[key]
        pals = []
        try:
            entries = doc.header_entries()
        except Exception:
            entries = []
        for _idx, cmd, args in entries:
            c = cmd.lower()
            try:
                if c == "alternatepal" and args:
                    p = bl.resolve_frame_path(self.root_dirs, args[0])
                    if not p:
                        continue
                    pc = iu.palette_colors(iu.load(p))
                    if pc:
                        pals.append([tuple(x) for x in pc[0]])
                elif c == "remap" and len(args) >= 2:
                    pa = bl.resolve_frame_path(self.root_dirs, args[0])
                    pb = bl.resolve_frame_path(self.root_dirs, args[1])
                    if not pa or not pb:
                        continue
                    ia, ib = iu.load(pa), iu.load(pb)
                    if ia.mode != "P" or ib.mode != "P" \
                            or ia.size != ib.size:
                        continue
                    pcb = iu.palette_colors(ib)
                    if not pcb:
                        continue
                    pal_b = pcb[0]
                    mp = {}
                    for x, y in zip(ia.tobytes(), ib.tobytes()):
                        if x not in mp:      # 冲突时先到先得
                            mp[x] = y
                    pals.append([tuple(pal_b[mp.get(i, i)])
                                 for i in range(256)])
            except Exception:
                continue
        self._pal_cache[key] = pals
        if len(self._pal_cache) > 200:
            self._pal_cache.clear()
        return pals

    def entity_thumb(self, name, map_no=0):
        """(图, ox, oy, map超界)：实体 idle 首帧**裁掉背景色边**后的图。

        - 背景色 = 调色板第 1 色（OpenBOR 透明键），取非背景像素的最大
          外接矩形；整帧透明（empty.gif 之类的占位帧）→ 图返回 None，
          画布改画通用标记
        - map_no > 0 时套用第 map_no 套 remap / alternatepal 配色；
          编号超出配色总数时回退原色，并把第 4 个返回值置 True（由画布标注）
        """
        key = ((name or "").lower(), int(map_no or 0))
        if key in self._thumbs:
            return self._thumbs[key]
        out = (None, 0, 0, False)
        b = self.bor
        doc = self._entity_doc(name)
        if b is not None and doc is not None:
            a = b._find_anim(doc, "idle") \
                or (doc.anims[0] if doc.anims else None)
            frames = a.frames() if a is not None else []
            if frames and frames[0]["path"] \
                    and frames[0]["path"].lower() != "none":
                fr = frames[0]
                path = bl.resolve_frame_path(self.root_dirs, fr["path"])
                if path:
                    try:
                        raw = iu.load(path)
                        over = False
                        if map_no > 0:
                            pals = self._entity_palettes(doc) \
                                if raw.mode == "P" else []
                            if 1 <= map_no <= len(pals):
                                raw = raw.copy()
                                flat = []
                                for c in pals[map_no - 1]:
                                    flat.extend(int(v) for v in c[:3])
                                raw.putpalette(flat)
                            else:
                                over = True      # 超界（或真彩图）：原色 + 标注
                        rgba = iu.openbor_rgba(raw)
                        bbox = rgba.getchannel("A").getbbox()
                        if bbox is not None:
                            rgba = rgba.crop(bbox)
                            off = fr["offset"] or []
                            try:
                                ox = int(float(off[0])) if off else 0
                                oy = int(float(off[1])) if len(off) > 1 else 0
                            except (ValueError, IndexError):
                                ox, oy = 0, 0
                            out = (iu.pil_to_qimage(rgba),
                                   ox - bbox[0], oy - bbox[1], over)
                            self._thumb_pils[key] = rgba
                    except Exception:
                        pass
        self._thumbs[key] = out
        if len(self._thumbs) > 300:
            self._thumbs.clear()
            self._thumb_pils.clear()
            self._dm_imgs.clear()
        return out

    def entity_spawn_view(self, name, map_no=0):
        """关卡画布生成点的完整画法：(图, ox, oy, map超界, drawmethod规格)。

        图 = entity_thumb 裁过背景色的 idle 首帧；再按 idle 首帧的
        drawmethod 处理：颜色特效（填充 / 染色 / 通道）直接烘进图里
        （按色键缓存），几何变换（缩放 / 翻转 / 倾斜 / 旋转）返回规格
        由画布自己套 —— 支点是 offset 点（裁剪后即 (ox, oy)），
        和动画编辑页是同一套规则（bor_dm.geometry / paint）。
        """
        im, ox, oy, over = self.entity_thumb(name, map_no)
        if im is None:
            return (None, 0, 0, False, None)
        spec = None
        b = self.bor
        doc = self._entity_doc(name)
        if b is not None and doc is not None:
            a = b._find_anim(doc, "idle") \
                or (doc.anims[0] if doc.anims else None)
            frames = a.frames() if a is not None else []
            if frames and frames[0].get("dm"):
                spec = bl.parse(frames[0]["dm"])
                ck = bl.color_key(spec)
                if ck is not None:
                    key = ((name or "").lower(), int(map_no or 0), ck)
                    hit = self._dm_imgs.get(key)
                    if hit is not None:
                        im = hit
                    else:
                        pil = self._thumb_pils.get(
                            ((name or "").lower(), int(map_no or 0)))
                        if pil is not None:
                            try:
                                im = iu.pil_to_qimage(
                                    bl.color_image(pil, spec))
                            except Exception:
                                pass
                        self._dm_imgs[key] = im
        return (im, ox, oy, over, spec)

    def _entity_doc(self, name):
        """按 **models.txt** 的索引找实体（不再猜文件名）。

        OpenBOR 里「实体名」和「定义它的 txt 文件名 / 所在文件夹名」常常
        不一致，所以只能以 models.txt 为准，**三步都过了**才算找着：

        1. models.txt 里有没有 `load / know <名字> <文件>` 这一行
        2. 那一行指向的文件存不存在
        3. 那个文件里 `name` 字段是不是就是这个实体名

        任何一步不过 → 返回 None（画布 / 列表标「⚠实体未找到」），
        失败原因记进 self._ent_reason，鼠标悬停能看到。
        """
        b = self.bor
        if b is None or not name:
            return None
        key = (name or "").lower()
        if key in self._docs:
            return self._docs[key]
        doc, reason = self._lookup_entity(name)
        self._docs[key] = doc
        self._ent_reason[key] = reason
        return doc

    def _lookup_entity(self, name):
        """三步校验：返回 (EntityDoc 或 None, 失败原因)。"""
        b = self.bor
        key = (name or "").lower()
        models = getattr(b, "models", None)
        ents = getattr(models, "entities", None) if models is not None else None
        if not ents:
            # 拿不到 models.txt（动画页还没导入）→ 只能借它自己的表，
            # 判不了就不判（见 _ent_bad：没有索引时不标红，免得误报）
            f = getattr(b, "_entity_doc", None)
            if f is None:
                return None, ""
            try:
                return f(name), ""
            except Exception:
                return None, ""
        for kind, ename, rel in ents:
            if (ename or "").lower() != key:
                continue
            path = None
            for root in self.root_dirs:
                p = os.path.join(root, rel.replace("/", os.sep))
                if os.path.exists(p):
                    path = p
                    break
            if path is None:
                return None, f"models.txt 里有 `{kind} {ename} {rel}`，" \
                             f"但这个文件不存在"
            try:
                doc = openbor.EntityDoc(path)
            except Exception as ex:
                return None, f"{rel} 解析失败：{ex}"
            nm = doc.header_get("name")
            real = (nm[0] if nm and nm[0] else "") or ""
            if real.lower() != key:
                return None, (f"{rel} 里定义的 name 是 "
                              f"{real or '（空）'}，不是 {name}")
            return doc, ""
        return None, "models.txt 里没有定义这个实体（游戏里这行 spawn 会被忽略）"

    def _ent_reason_of(self, name):
        """实体找不到的具体原因（给列表 / 画布的悬停提示用）；找着了是空串。"""
        return self._ent_reason.get((name or "").lower(), "")

    # ---------------- 界面 ----------------
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        sp = QSplitter(Qt.Horizontal)

        # ---- 左：关卡列表（set 层次）+ 属性 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(2, 2, 2, 2)
        row = QHBoxLayout()
        self.btn_load = QPushButton("载入 levels.txt")
        self.btn_load.clicked.connect(self.load_levels)
        self.btn_newset = QPushButton("新建 set")
        self.btn_newset.setToolTip(
            "在 levels.txt 末尾新建一个 set 段（名字随手起，\n"
            "基本设置在树里的 set 节点上右键添加）")
        self.btn_newset.clicked.connect(self.new_set)
        self.btn_newlvl = QPushButton("新建 level")
        self.btn_newlvl.setToolTip(
            "给当前选中的 set 加一条 file：\n"
            "在 levels 文件夹里选一个 txt，或输入新名字建一个空关卡")
        self.btn_newlvl.clicked.connect(self.new_level)
        row.addWidget(self.btn_load)
        row.addWidget(self.btn_newset)
        row.addWidget(self.btn_newlvl)
        lv.addLayout(row)
        self.tree_levels = QTreeWidget()
        self.tree_levels.setColumnCount(1)
        self.tree_levels.setHeaderHidden(True)
        self.tree_levels.setToolTip(
            "set 是一段关卡的集合：\n"
            "· 点 set 下面的关卡条目打开它\n"
            "· set 节点右键：修改 / 删除 / 添加基本设置（lives、credits…），\n"
            "  或插入 branch / scene / next / end 子段\n"
            "· 关卡条目右键：删除该条记录")
        self.tree_levels.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_levels.customContextMenuRequested.connect(
            self._on_levels_ctx)
        self.tree_levels.currentItemChanged.connect(self.on_level_picked)
        lv.addWidget(self.tree_levels, 1)
        gb = QGroupBox("关卡属性（双击改值）")
        gl = QVBoxLayout(gb)
        self.tree_props = QTreeWidget()
        self.tree_props.setHeaderLabels(["命令", "值"])
        self.tree_props.setRootIsDecorated(False)
        self.tree_props.itemDoubleClicked.connect(self.edit_prop)
        gl.addWidget(self.tree_props)
        lv.addWidget(gb, 1)

        # ---- 中：画布 ----
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(2, 2, 2, 2)
        bar = QHBoxLayout()
        self.btn_map = QPushButton("全景")
        self.btn_map.setCheckable(True)
        self.btn_map.setChecked(True)
        self.btn_map.clicked.connect(lambda: self.set_mode("map"))
        self.btn_scr = QPushButton("屏幕预览")
        self.btn_scr.setCheckable(True)
        self.btn_scr.clicked.connect(lambda: self.set_mode("screen"))
        self.btn_fit = QPushButton("适应窗口")
        self.btn_fit.clicked.connect(self.canvas_fit)
        self.sp_scroll = QSpinBox()
        self.sp_scroll.setRange(0, 999999)
        self.sp_scroll.setSuffix(" 滚动位置")
        self.sp_scroll.valueChanged.connect(self.on_scroll_spin)
        self.btn_zin = QPushButton("＋")
        self.btn_zin.clicked.connect(lambda: self.zoom_by(1.25))
        self.btn_zout = QPushButton("－")
        self.btn_zout.clicked.connect(lambda: self.zoom_by(0.8))
        # 两种显示方式：实际生成位置 / 按组从上到下排成一列（也可 Ctrl+R）
        self.btn_layout = QPushButton("实际位置")
        self.btn_layout.setCheckable(True)
        self.btn_layout.setChecked(False)
        self.btn_layout.setToolTip(
            "切换显示方式（Ctrl+R，切换时视野原地不动）：\n"
            "实际位置 —— 画在它真正的 spawn 位置（at + coords）\n"
            "按组排列 —— 编号 / 名字为主，帧图裁剪缩小后辅助显示；\n"
            "　横向卷轴关卡一个组一列，纵向卷轴关卡一个组一行")
        self.btn_layout.clicked.connect(self.toggle_layout)
        self.ck_terrain = QCheckBox("地形")
        self.ck_terrain.setChecked(True)
        # 墙的 alt（高度）带：画在墙上边上方，一眼看出这墙有多高
        self.ck_wallalt = QCheckBox("墙高")
        self.ck_wallalt.setChecked(True)
        self.ck_wallalt.setToolTip(
            "显示墙的高度（alt）：\n"
            "墙上边上方那条淡蓝色带就是它的高度范围，\n"
            "游戏里跳跃高度低于它的角色撞不过去")
        # 画墙模式：勾选后在画布空白处拖一个矩形就是一道新墙（Esc 退出）
        self.btn_newwall = QPushButton("画墙")
        self.btn_newwall.setCheckable(True)
        self.btn_newwall.setToolTip(
            "画墙模式：在画布空白处按住拖出一个矩形，\n"
            "松手就在那里新建一道 wall（Esc 或再点一次退出）")
        self.btn_newwall.toggled.connect(self.toggle_wall_draw)
        # 游戏画面范围框：标出游戏里这一屏真正看得到的 320×240（默认关）
        self.btn_viewbox = QPushButton("画面框")
        self.btn_viewbox.setCheckable(True)
        self.btn_viewbox.setChecked(False)
        self.btn_viewbox.setToolTip(
            "画出「游戏画面范围」框：\n"
            "框里就是玩家在 320×240 屏幕上真正看得到的那块，框外会压暗。\n"
            "横向卷轴关卡：框沿关卡长度走；纵向卷轴关卡：框沿 z 深度走")
        self.btn_viewbox.toggled.connect(self.toggle_viewbox)
        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self.save_level)
        self.btn_reload = QPushButton("重载")
        self.btn_reload.clicked.connect(self.reload_level)
        for w in (self.btn_map, self.btn_scr, self.btn_fit, self.sp_scroll,
                  self.btn_zin, self.btn_zout, self.btn_layout,
                  self.ck_terrain, self.ck_wallalt, self.btn_newwall,
                  self.btn_viewbox, self.btn_save, self.btn_reload):
            bar.addWidget(w)
        bar.addStretch(1)
        mv.addLayout(bar)
        self.canvas = LevelMapCanvas(self)
        self.ck_terrain.stateChanged.connect(self.canvas.update)
        self.ck_wallalt.toggled.connect(self._toggle_wall_alt)
        mv.addWidget(self.canvas, 1)

        # ---- 右：生成对象（文本编辑）+ 属性 ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(2, 2, 2, 2)
        gb2 = QGroupBox("生成对象（关卡源码，所见即所得）")
        g2 = QVBoxLayout(gb2)
        self.txt_objs = BorTextEdit()
        self.txt_objs.setFont(QFont("Consolas", 10))
        self.txt_objs.setLineWrapMode(QTextEdit.NoWrap)
        self.txt_objs.setMinimumHeight(260)
        self.obj_highlighter = LevelHighlighter(self.txt_objs.document())
        # 行选对象 + 改完一行倒回文档（动画页同款的双向同步）
        self.txt_objs.cursorPositionChanged.connect(self._on_obj_cursor)
        self.txt_objs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.txt_objs.customContextMenuRequested.connect(self._on_obj_ctx)
        g2.addWidget(self.txt_objs, 1)
        # 错误汇总（具体哪几行错看编辑器里的红底）
        self.lbl_obj_warn = QLabel("")
        self.lbl_obj_warn.setProperty("role", "dim")
        self.lbl_obj_warn.setWordWrap(True)
        g2.addWidget(self.lbl_obj_warn)
        row2 = QHBoxLayout()
        self.btn_add = QPushButton("新增生成点")
        self.btn_add.clicked.connect(self.add_spawn)
        self.btn_del = QPushButton("删除")
        self.btn_del.clicked.connect(self.del_spawn)
        self.btn_zfix = QPushButton("z 拉回可走范围")
        self.btn_zfix.setToolTip("把选中（或全部）生成点的 z 夹回 "
                                 "zmin~zmax 之内")
        self.btn_zfix.clicked.connect(self.fix_z)
        row2.addWidget(self.btn_add)
        row2.addWidget(self.btn_del)
        row2.addWidget(self.btn_zfix)
        g2.addLayout(row2)
        # 生成顺序：在**同一个 group 内**把选中的生成点往前 / 后挪一位
        row2b = QHBoxLayout()
        self.btn_up = QPushButton("↑ 提前")
        self.btn_dn = QPushButton("↓ 推后")
        self.btn_up.setToolTip("把这个生成点在组里往前挪一位（更早生成）\n"
                               "画布列排模式下直接上下拖动也行（#1 #2 … 是顺序）")
        self.btn_dn.setToolTip("把这个生成点在组里往后挪一位（更晚生成）\n"
                               "不会跨到别的组去")
        self.btn_up.clicked.connect(lambda: self.reorder_sel(-1))
        self.btn_dn.clicked.connect(lambda: self.reorder_sel(1))
        row2b.addWidget(self.btn_up)
        row2b.addWidget(self.btn_dn)
        g2.addLayout(row2b)
        # 组过滤：点画布上的 group 条只显示这一组；恢复要按这个按钮
        row3 = QHBoxLayout()
        self.lbl_group = QLabel("显示全部")
        self.lbl_group.setProperty("role", "dim")
        self.btn_all = QPushButton("显示全部")
        self.btn_all.setToolTip("取消 group 过滤，把所有生成点都显示出来")
        self.btn_all.clicked.connect(lambda: self.select_group(None))
        row3.addWidget(self.lbl_group, 1)
        row3.addWidget(self.btn_all)
        g2.addLayout(row3)
        rv.addWidget(gb2, 1)

        gb3 = QGroupBox("选中对象的属性")
        self.gb_obj = gb3
        self.form = QFormLayout(gb3)
        self.ed_name = QLineEdit()
        self.ed_name.editingFinished.connect(self.apply_props)
        self.sp_x = self._spin(-9999, 999999)
        self.sp_z = self._spin(-999, 999)
        self.sp_a = self._spin(0, 999)
        self.sp_at = self._spin(0, 999999)
        self.ck_health = QCheckBox("health")
        self.sp_health = self._spin(0, 999999)
        self.ck_aggr = QCheckBox("aggr")
        self.sp_aggr = self._spin(-999999, 999999)
        self.ck_flip = QCheckBox("flip")
        self.ck_boss = QCheckBox("boss")
        self.ed_alias = QLineEdit()
        self.ed_item = QLineEdit()
        # item 后面带一张掉落物品的 idle 首帧缩略图（裁掉背景色后的最大矩形）
        self.lbl_item_thumb = QLabel()
        self.lbl_item_thumb.setFixedSize(ITEM_TH_W, ITEM_TH_H)
        self.lbl_item_thumb.setAlignment(Qt.AlignCenter)
        self.lbl_item_thumb.setToolTip("掉落物品的 idle 首帧"
                                       "（非背景像素最大外接矩形）")
        self.ck_map = QCheckBox()
        self.sp_map = self._spin(0, 14)
        self.ed_weapon = QLineEdit()
        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        self.form.addRow("实体名", self.ed_name)
        # x / z / a 挤到一行（都是坐标，分三行太占地方）
        self.form.addRow("x / z / a",
                         self._row(QLabel("x"), self.sp_x,
                                   QLabel("z"), self.sp_z,
                                   QLabel("a"), self.sp_a))
        self.form.addRow("at（滚动位置）", self.sp_at)
        # health / aggression 也放一行（各有勾选框 + 数值）
        self.form.addRow("血量 / 攻击性",
                         self._row(self.ck_health, self.sp_health,
                                   self.ck_aggr, self.sp_aggr))
        self.form.addRow("flip / boss", self._row(self.ck_flip, self.ck_boss))
        self.form.addRow("alias", self.ed_alias)
        self.form.addRow("item", self._row(self.ed_item, self.lbl_item_thumb))
        self.form.addRow("map", self._row(self.ck_map, self.sp_map))
        self.form.addRow("weapon", self.ed_weapon)
        self.form.addRow("", self.lbl_info)
        rv.addWidget(gb3)

        # ---- 选中的 group：两个参数（最小 / 最大实体数）可编辑 ----
        gb_g = QGroupBox("选中的 group")
        self.gb_group = gb_g
        gf = QFormLayout(gb_g)
        self.lbl_group_info = QLabel("")
        self.lbl_group_info.setProperty("role", "dim")
        self.lbl_group_info.setWordWrap(True)
        self.sp_gmin = self._gspin(0, 999)
        self.sp_gmax = self._gspin(0, 999)
        self.sp_gat = self._gspin(0, 999999)
        gf.addRow("", self.lbl_group_info)
        gf.addRow("最小实体数", self.sp_gmin)
        gf.addRow("最大实体数", self.sp_gmax)
        gf.addRow("at（滚动位置）", self.sp_gat)
        gb_g.setVisible(False)
        rv.addWidget(gb_g)

        # ---- 选中的 wall（地形）：只有选中墙时才显示 ----
        self.gb_wall = QGroupBox("选中的墙（wall）")
        wf = QFormLayout(self.gb_wall)
        self._wsp = {}
        for key, lab, lo, hi in (
                ("x", "x（关卡位置）", -9999, 999999),
                ("z", "z（底边深度）", -999, 999),
                ("ul", "左上 ul", -9999, 9999),
                ("ur", "右上 ur", -9999, 9999),
                ("ll", "左下 ll", -9999, 9999),
                ("lr", "右下 lr", -9999, 9999),
                ("dep", "深度 depth", 1, 9999),
                ("alt", "高度 alt", 0, 999999),
                ("type", "type", 0, 999)):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.valueChanged.connect(self.apply_wall)
            self._wsp[key] = s
            wf.addRow(lab, s)
        wr = QHBoxLayout()
        self.btn_wdel = QPushButton("删除墙")
        self.btn_wdel.clicked.connect(self.del_wall)
        self.btn_wunsel = QPushButton("取消选择")
        self.btn_wunsel.clicked.connect(lambda: self.select_wall(None))
        wr.addWidget(self.btn_wdel)
        wr.addWidget(self.btn_wunsel)
        wf.addRow(wr)
        self.gb_wall.setVisible(False)
        rv.addWidget(self.gb_wall)
        rv.addStretch(1)

        # z 范围（写回 levels.txt）
        gb4 = QGroupBox("z 范围（写回 levels.txt）")
        g4 = QHBoxLayout(gb4)
        self.sp_zmin = self._spin(0, 999)
        self.sp_zmax = self._spin(0, 999)
        self.sp_bgh = self._spin(0, 999)
        self.btn_z = QPushButton("应用 z")
        self.btn_z.clicked.connect(self.apply_z)
        g4.addWidget(QLabel("min"))
        g4.addWidget(self.sp_zmin)
        g4.addWidget(QLabel("max"))
        g4.addWidget(self.sp_zmax)
        g4.addWidget(QLabel("BG"))
        g4.addWidget(self.sp_bgh)
        g4.addWidget(self.btn_z)
        rv.addWidget(gb4)

        sp.addWidget(left)
        sp.addWidget(mid)
        sp.addWidget(right)
        sp.setSizes([260, 900, 330])
        root.addWidget(sp)

    @staticmethod
    def _row(*ws):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for x in ws:
            h.addWidget(x)
        return w

    def _spin(self, lo, hi):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.valueChanged.connect(self.apply_props)
        return s

    def _gspin(self, lo, hi):
        """group 参数用的数值框（回写走 apply_group，不走 apply_props）。"""
        s = QSpinBox()
        s.setRange(lo, hi)
        s.valueChanged.connect(self.apply_group)
        return s

    def _toggle_wall_alt(self, on):
        self.canvas.show_wall_alt = bool(on)
        self.canvas.update()

    # ---------------- 载入 ----------------
    def sync_from_bor(self, silent=True):
        """用动画页导入的 data 目录打开同目录的 levels.txt。

        没有对应的按钮了：页签切换时的「懒同步」会自动调用它
        （见 main._on_tab_changed / lazy_follow_bor）。
        """
        b = self.bor
        if b is None or not b.data_dir:
            return False
        p = os.path.join(b.data_dir, "levels.txt")
        if not os.path.exists(p):
            return False
        self._do_load(p)
        return True

    def auto_sync(self):
        """切到本页且还没载入过就自动跟随（不做任何弹窗）。"""
        if self.levels is None:
            self.sync_from_bor(silent=True)

    def lazy_follow_bor(self):
        """切回本页时：动画页导入的 data 目录跟当前 levels.txt 不是
        同一个，就静默重载那边的 levels.txt（有一边改了不立刻重载，
        切页时发现不一致才动——和用户的使用习惯一致）。
        """
        b = self.bor
        if self.levels_path is None or b is None or not b.data_dir:
            return
        if os.path.abspath(os.path.dirname(self.levels_path)) \
                == os.path.abspath(b.data_dir):
            return
        p = os.path.join(b.data_dir, "levels.txt")
        if not os.path.exists(p):
            return
        if self._dirty():
            r = QMessageBox.question(
                self, "未保存的修改",
                "动画编辑页换了 data 目录，关卡这边需要重载\n"
                "（会丢掉当前关卡未保存的修改）。\n\n"
                "是：先保存再重载　否：放弃修改　取消：暂时不重载",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                try:
                    self.doc.save()
                except Exception as ex:
                    QMessageBox.warning(self, "保存失败", str(ex))
                    return
        self._do_load(p)

    def load_levels(self):
        d = self.bor.data_dir if (self.bor and self.bor.data_dir) else "."
        p, _ = QFileDialog.getOpenFileName(
            self, "选择 levels.txt", d, "levels.txt (levels.txt);;*.txt")
        if p:
            self._do_load(p)

    def _apply_video_defaults(self):
        """没写 z 行的关卡：引擎用视频模式的默认范围（video 1 → 182~264），
        不能按 320×240 的 160~232 显示。"""
        if self.levels is None:
            return
        _vw, _vh, vzmin, vzmax, vbgh = self.video_size()
        for e in self.levels.entries:
            if e.z_line is None:
                e.zmin, e.zmax, e.bgheight = vzmin, vzmax, vbgh

    def _do_load(self, path):
        try:
            self.levels = bl.LevelsTxt(path)
        except Exception as ex:
            QMessageBox.warning(self, "载入失败", str(ex))
            return
        self._apply_video_defaults()
        self.levels_path = path
        self.clear_cache()
        self.doc = None
        self._cur_path = ""
        self._refresh_level_list()
        it = self.tree_levels.currentItem()
        if it is None:                      # 树里选不上就取第一个关卡
            it = self._first_level_item()
        if it is not None:
            self.on_level_picked(it, None)

    def _first_level_item(self):
        tv = self.tree_levels
        for r in range(tv.topLevelItemCount()):
            root = tv.topLevelItem(r)
            for k in range(root.childCount()):
                it = root.child(k)
                if it.data(0, Qt.UserRole):
                    d = it.data(0, Qt.UserRole)
                    if d[0] == "file":
                        return it
        return None

    def on_level_picked(self, item, _prev=None):
        if item is None or self.levels is None:
            return
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "file":
            return                          # set 节点 / 结构子段不可选
        e = data[1]
        # 有未保存的修改：切换前问一句（保存 / 放弃 / 取消）
        if self.doc is not None and e.path != self._cur_path and self._dirty():
            r = QMessageBox.question(
                self, "未保存的修改",
                "当前关卡有未保存的修改。\n\n"
                "是：先保存再切换　否：放弃修改　取消：留在当前关卡",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes)
            if r == QMessageBox.Cancel:
                self.tree_levels.blockSignals(True)
                self._select_entry(self._cur_path)
                self.tree_levels.blockSignals(False)
                return
            if r == QMessageBox.Yes:
                try:
                    self.doc.save()
                except Exception as ex:
                    QMessageBox.warning(self, "保存失败", str(ex))
                self._remember()
        path = bl.resolve_path(self.root_dirs, e.path)
        if path is None:
            QMessageBox.warning(self, "找不到关卡文件", e.path)
            return
        try:
            self.doc = bl.LevelDoc(path)
        except Exception as ex:
            QMessageBox.warning(self, "解析失败", str(ex))
            return
        self.doc.ref = e
        self._cur_path = e.path
        self._obj_line = -1
        self._undo.clear()                # 撤销栈不跨关卡
        self._redo.clear()
        self.clear_cache()
        self.canvas.sel = None
        self.canvas.group_sel = None      # 换关卡就别再卡在某个组上
        self.canvas.wall_sel = None
        self.canvas.vb_pos = None         # 画面框回到关卡起点
        self.gb_wall.setVisible(False)
        self.gb_group.setVisible(False)
        self.lbl_group.setText("显示全部")
        self.refresh_props()
        self.refresh_objs()
        self.canvas_fit()
        self._remember()          # 载入基线：只有这里和保存成功时才记

    def reload_level(self):
        if self.doc is None:
            return
        if self._dirty():
            r = QMessageBox.question(
                self, "重载", "有未保存的修改，重载将丢弃。是否继续？")
            if r != QMessageBox.Yes:
                return
        self.on_level_picked(self.tree_levels.currentItem())

    def _dirty(self):
        if self.doc is None:
            return False
        return self.doc.to_text() != self._saved

    def _remember(self):
        self._saved = self.doc.to_text() if self.doc else ""

    # ---------------- 撤销 / 重做（关卡文本 + levels.txt 的快照） ----------------
    def _cur_snap(self):
        """当前时刻的完整快照：(关卡文本, levels.txt 文本)。"""
        return (self.doc.to_text() if self.doc else "",
                self.levels.to_text() if self.levels is not None else "")

    def _snapshot(self):
        """改动**之前**喊一声：把当前状态压进撤销栈（连按不变就不压）。

        快照带上 levels.txt —— zmin / zmax 改的是 levels.txt 里那条 `z` 行，
        不带它的话拖 z 线就没法 Ctrl+Z 了。
        编辑器里敲了还没换行的字也先倒回文档，快照才是完整的。
        """
        self._sync_obj_text(push=False)
        if self.doc is None:
            return
        cur = self._cur_snap()
        if self._undo and self._undo[-1] == cur:
            return
        self._undo.append(cur)
        if len(self._undo) > 100:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self):
        self._sync_obj_text(push=False)   # 刚敲完还没换行的字也算当前状态
        d = self.doc
        if d is None or not self._undo:
            self.status_tip("没有可撤销的操作")
            return
        self._redo.append(self._cur_snap())
        self._restore_state(self._undo.pop())
        self.status_tip("已撤销（Ctrl+Shift+Z 重做）")

    def redo(self):
        self._sync_obj_text(push=False)
        d = self.doc
        if d is None or not self._redo:
            self.status_tip("没有可重做的操作")
            return
        self._undo.append(self._cur_snap())
        self._restore_state(self._redo.pop())
        self.status_tip("已重做")

    def _restore_state(self, snap):
        """恢复快照 (关卡文本, levels.txt 文本)。"""
        lvl, lst = snap
        d = self.doc
        if d is not None:
            d.load_text(lvl, d.path)
        if self.levels is not None and lst and lst != self.levels.to_text():
            self.levels.load_text(lst, self.levels.path)
            self._apply_video_defaults()
            try:                 # apply_z 当时已写盘的话，撤销也要写回去
                self.levels.save()
            except Exception:
                pass
            if d is not None:
                d.ref = None
                for x in self.levels.entries:
                    if bl.resolve_path(self.root_dirs, x.path) == d.path:
                        d.ref = x
                        break
            self._refresh_level_list()
        self.canvas.sel = None
        self.canvas.wall_sel = None
        self.canvas._drag = None
        self.clear_cache()
        self.refresh_props()
        self.refresh_objs()
        self.gb_group.setVisible(False)
        self.canvas.update()

    # ---------------- 关卡树（set 层次） ----------------
    @staticmethod
    def _non_sel(it):
        """set 节点 / 结构子段：能看见、能右键，但不能被选成当前关卡。"""
        it.setFlags(Qt.ItemIsEnabled)

    def _select_entry(self, path):
        """把树里的当前项切到 path 这条关卡上（不触发 on_level_picked）。"""
        tv = self.tree_levels
        for r in range(tv.topLevelItemCount()):
            root = tv.topLevelItem(r)
            for k in range(root.childCount()):
                it = root.child(k)
                d = it.data(0, Qt.UserRole)
                if d and d[0] == "file" and d[1].path == path:
                    tv.setCurrentItem(it)
                    tv.scrollToItem(it)
                    return it
        return None

    def _refresh_level_list(self):
        """按当前的 levels.txt 重建关卡树（set 为根、关卡为叶子；
        scene / next / end 这类结构子段也列出来，灰字不可选）。"""
        tv = self.tree_levels
        if self.levels is None:
            return
        tv.blockSignals(True)
        cur_path = self._cur_path
        tv.clear()
        # 没有 set 的关卡（写在文件开头、set 之前的）挂到一个伪根下
        has_orphan = any(not e.set_name for e in self.levels.entries)
        orphan = None
        if has_orphan:
            orphan = QTreeWidgetItem(tv, ["（无 set）"])
            self._non_sel(orphan)
        roots = {"": orphan}
        for b in self.levels.set_blocks:
            txt = f"set {b.name}"
            if b.settings:
                txt += f"　[{len(b.settings)} 项设置]"
            r = QTreeWidgetItem(tv, [txt])
            r.setToolTip(0, "右键：修改 / 删除 / 添加基本设置，插入子段")
            r.setData(0, Qt.UserRole, ("set", b.name))
            self._non_sel(r)
            roots[b.name] = r
        ent_by_line = {e.line: e for e in self.levels.entries}
        cur_root = orphan
        sel = None
        for i, ln in enumerate(self.levels.lines):
            c = ln.cmd()
            if c == "set":
                cur_root = roots.get(" ".join(ln.args()), cur_root)
            elif c == "file" and i in ent_by_line:
                e = ent_by_line[i]
                parent = roots.get(e.set_name, cur_root)
                if parent is None:
                    continue
                it = QTreeWidgetItem(parent, [e.label])
                it.setToolTip(0, f"{e.path}\nz {e.zmin}~{e.zmax}"
                              + (f"\nset: {e.set_name}" if e.set_name else ""))
                it.setData(0, Qt.UserRole, ("file", e))
                if e.path == cur_path:
                    sel = it
            elif c in ("scene", "end", "next"):
                # 结构子段：不是关卡，列出来只是让结构一眼看清
                if cur_root is None:
                    continue
                it = QTreeWidgetItem(cur_root, [ln.raw.strip()])
                it.setToolTip(0, "结构子段（不可选）")
                it.setForeground(0, MARK_DIM)
                self._non_sel(it)
        tv.expandAll()
        if sel is not None:
            tv.setCurrentItem(sel)
            tv.scrollToItem(sel)
        tv.blockSignals(False)

    # ---------------- set 段的编辑（右键菜单 / 新建按钮） ----------------
    def _on_levels_ctx(self, pos):
        """关卡树右键：set 节点 → 改 / 删 / 加基本设置，插子段；
        关卡条目 → 删除该条记录。"""
        tv = self.tree_levels
        it = tv.itemAt(pos)
        if it is None or self.levels is None:
            return
        data = it.data(0, Qt.UserRole)
        if not data:
            return
        m = QMenu(self)
        acts = []
        if data[0] == "set":
            b = self.levels.block_by_name(data[1])
            if b is None:
                return
            if b.settings:
                sub_e = m.addMenu("修改设置…")
                sub_d = m.addMenu("删除设置…")
                for cmd in sorted(b.settings):
                    li, args = b.settings[cmd]
                    a = sub_e.addAction(f"{cmd}\t{' '.join(args)}")
                    a.setData(("edit", b.name, cmd))
                    acts.append(a)
                    a = sub_d.addAction(f"{cmd}\t{' '.join(args)}")
                    a.setData(("del", b.name, cmd))
                    acts.append(a)
            a = m.addAction("添加设置字段…")
            a.setData(("add", b.name))
            acts.append(a)
            m.addSeparator()
            sub_i = m.addMenu("在此 set 末尾插入子段…")
            for cmd, tip in (("branch", "分支关卡标题"),
                             ("scene", "播放一段剧情 scene（txt 路径）"),
                             ("next", "过关后接着下一关"),
                             ("end", "set / 分支结束")):
                a = sub_i.addAction(f"{cmd} — {tip}")
                a.setData(("ins", b.name, cmd))
                acts.append(a)
        else:
            a = m.addAction("删除这条关卡记录（连带它的 branch / z 行）")
            a.setData(("delfile", None, None))
            acts.append(a)
        act = m.exec_(tv.viewport().mapToGlobal(pos))
        if act is None:
            return
        kind, name, cmd = act.data()
        b = self.levels.block_by_name(name) if name else None
        if kind == "edit":
            self._edit_set_setting(b, cmd)
        elif kind == "del":
            self._del_set_setting(b, cmd)
        elif kind == "add":
            self._add_set_setting(b)
        elif kind == "ins":
            self._ins_set_sub(b, cmd)
        elif kind == "delfile":
            self._del_level_entry(data[1])

    def _levels_edited(self):
        """levels.txt 改完的统一收尾：写盘 + 重建树 + 重新挂上 doc.ref。

        行号全变了：所有 LevelRef / SetBlock 都是新建的，所以当前关卡
        的记录要按路径重新找回来。
        """
        L = self.levels
        if L is None:
            return
        try:
            L.save()
        except Exception as ex:
            QMessageBox.warning(self, "写入失败", str(ex))
            return
        self._apply_video_defaults()
        path = self._cur_path
        self._refresh_level_list()
        if self.doc is not None:
            for x in L.entries:
                if x.path == path:
                    self.doc.ref = x
                    break

    def _edit_set_setting(self, b, cmd):
        if b is None:
            return
        st = b.settings.get(cmd.lower())
        old = " ".join(st[1]) if st else ""
        v, ok = QInputDialog.getText(self, f"set {b.name}",
                                     f"{cmd} 的值：", QLineEdit.Normal, old)
        if not ok:
            return
        self._snapshot()
        self.levels.set_setting(b, cmd, v.split())
        self._levels_edited()
        self.status_tip(f"set {b.name}：{cmd} 已改为 {v.strip() or '（空）'}")

    def _del_set_setting(self, b, cmd):
        if b is None:
            return
        r = QMessageBox.question(
            self, "删除设置", f"删掉 set {b.name} 里的 {cmd} ？")
        if r != QMessageBox.Yes:
            return
        self._snapshot()
        self.levels.del_setting(b, cmd)
        self._levels_edited()
        self.status_tip(f"set {b.name}：已删除 {cmd}")

    def _add_set_setting(self, b):
        if b is None:
            return
        fields = list(bl.SET_FIELD_PRESETS)
        cmd, ok = QInputDialog.getItem(
            self, "添加设置字段",
            f"给 set {b.name} 加哪一条？（也可以直接输入别的字段名）",
            fields, 0, True)
        if not ok or not cmd.strip():
            return
        cmd = cmd.strip().split()[0]
        v, ok = QInputDialog.getText(self, cmd, "值：")
        if not ok:
            return
        self._snapshot()
        self.levels.set_setting(b, cmd, v.split())
        self._levels_edited()
        self.status_tip(f"set {b.name}：已添加 {cmd}")

    def _ins_set_sub(self, b, cmd):
        """往 set 末尾插一条 branch / scene / next / end。"""
        if b is None:
            return
        if cmd == "branch":
            v, ok = QInputDialog.getText(self, "插入 branch", "分支名：")
            args = v.split()
        elif cmd == "scene":
            v, ok = QInputDialog.getText(
                self, "插入 scene", "scene 的 txt 路径：")
            args = v.split()
        else:
            ok, args = True, []
        if not ok:
            return
        self._snapshot()
        self.levels.add_sub(b, cmd, args)
        self._levels_edited()
        self.status_tip(f"set {b.name}：已插入 {cmd}")

    def _del_level_entry(self, e):
        if e is None:
            return
        r = QMessageBox.question(
            self, "删除关卡",
            f"从 levels.txt 里删掉这条？\n\n{e.path}\n"
            "（只删 levels.txt 的记录，关卡文件本身不动）")
        if r != QMessageBox.Yes:
            return
        self._snapshot()
        self.levels.del_entry(e)
        self._levels_edited()
        if e.path == self._cur_path:
            self._cur_path = ""
            it = self._first_level_item()
            if it is not None:
                self.tree_levels.setCurrentItem(it)   # 触发载入下一关
        self.status_tip(f"已删除 {e.path}")

    def _cur_set_block(self):
        """当前选中关卡所属的 set（没有就取最后一个 set）。"""
        L = self.levels
        if L is None:
            return None
        if self.doc is not None and self.doc.ref is not None \
                and self.doc.ref.set_name:
            b = L.block_by_name(self.doc.ref.set_name)
            if b is not None:
                return b
        return L.set_blocks[-1] if L.set_blocks else None

    def new_set(self):
        """新建一个 set 段（名字随手起，设置在树里右键加）。"""
        if self.levels is None:
            self.status_tip("先载入 levels.txt")
            return
        name, ok = QInputDialog.getText(
            self, "新建 set", "set 名：", QLineEdit.Normal, "NewSet")
        if not ok or not name.strip():
            return
        self._snapshot()
        b = self.levels.add_set(name.strip())
        self._levels_edited()
        self.tree_levels.expandAll()
        self.status_tip(f"已新建 set {b.name if b else name}"
                        "（右键可加 lives / credits 等设置）")

    def new_level(self):
        """给当前 set 加一条 file：选 levels 文件夹里的 txt，或新建空的。"""
        L = self.levels
        if L is None:
            self.status_tip("先载入 levels.txt")
            return
        b = self._cur_set_block()
        if b is None:
            self.status_tip("先新建一个 set")
            return
        root = os.path.dirname(self.levels_path or "")
        # 关卡文件通常都在 data/levels 下：拿第一条记录推一下
        ddir = root
        for e in L.entries:
            p = bl.resolve_path(self.root_dirs, e.path)
            if p:
                ddir = os.path.dirname(p)
                break
        p, _ = QFileDialog.getSaveFileName(
            self, "选择或新建关卡 txt", ddir,
            "关卡 txt (*.txt);;所有文件 (*)")
        if not p:
            return
        if not os.path.exists(p):
            try:
                with open(p, "w", encoding="utf-8") as fp:
                    fp.write("# 新建关卡\n")
            except Exception as ex:
                QMessageBox.warning(self, "新建失败", str(ex))
                return
        try:
            rel = os.path.relpath(p, root).replace("\\", "/")
        except ValueError:                # 不同盘符：退回绝对路径
            rel = p.replace("\\", "/")
        self._snapshot()
        e = L.add_file(b, rel)
        self._levels_edited()
        if e is not None:
            self._cur_path = ""
            self.tree_levels.setCurrentItem(
                self._select_entry(e.path) or self.tree_levels.currentItem())
            self.status_tip(f"已把 {rel} 加进 set {b.name}")

    # ---------------- 属性表 ----------------
    def refresh_props(self):
        self.tree_props.clear()
        d = self.doc
        if d is None:
            return
        for cmd in sorted(d.props):
            for i, a in d.props[cmd]:
                it = QTreeWidgetItem(self.tree_props, [cmd, " ".join(a)])
                it.setData(0, Qt.UserRole, i)
        self.tree_props.resizeColumnToContents(0)
        zmin, zmax, bg = d.z_range()
        with self._quiet():
            self.sp_zmin.setValue(int(zmin))
            self.sp_zmax.setValue(int(zmax))
            self.sp_bgh.setValue(int(bg))
        # 注意：这里**不能** _remember() —— refresh_props 也会被编辑后调用，
        # 一 _remember 就把“未保存”冲掉了。基线只在载入 / 保存成功时记。

    def edit_prop(self, item, col):
        d = self.doc
        if d is None or item is None:
            return
        cmd = item.text(0)
        idx = item.data(0, Qt.UserRole)
        old = d.lines[idx].args()
        v, ok = QInputDialog.getText(self, f"改 {cmd}", "新值：",
                                     QLineEdit.Normal, " ".join(old))
        if not ok:
            return
        bl.write_line(d.lines[idx], cmd, v.split())
        d._parse()
        self.refresh_props()
        self.canvas.update()

    def apply_z(self, silent=False):
        """把右下角的 zmin / zmax / BGheight 写回 levels.txt。

        silent=True（画布上拖 z 线松手时）不弹“已写回”提示。
        """
        if self.levels is None or self.doc is None or self.doc.ref is None:
            if not silent:
                QMessageBox.information(self, "z 范围",
                                        "关卡要来自 levels.txt 才能写回 z。")
            return
        e = self.doc.ref
        self._snapshot()          # z 改的是 levels.txt，撤销栈要带上它
        self.levels.set_entry_z(e, self.sp_zmin.value(), self.sp_zmax.value(),
                                self.sp_bgh.value())
        try:
            self.levels.save()
        except Exception as ex:
            QMessageBox.warning(self, "写入失败", str(ex))
            return
        # 重新定位到同一条记录（set_entry_z 会重建列表）
        for k, x in enumerate(self.levels.entries):
            if x.path == e.path:
                self.doc.ref = x
                break
        self.canvas.update()
        if not silent:
            QMessageBox.information(self, "z 范围", "已写回 levels.txt。")

    def drag_zline(self, which, z):
        """画布上拖 zmin / zmax 虚线：实时改右下角数值，画布跟着动。

        只改内存（含 levels.txt 的那条记录），松手时 apply_z 才真正写盘。
        """
        d = self.doc
        if d is None:
            return
        zmin, zmax, _ = d.z_range()
        z = int(round(z))
        if which == "min":
            zmin = min(max(0, z), int(zmax) - 1)
        else:
            zmax = max(min(999, z), int(zmin) + 1)
        q = self._quiet()
        with q:
            self.sp_zmin.setValue(int(zmin))
            self.sp_zmax.setValue(int(zmax))
        e = d.ref
        if e is not None:                  # 让画布立即反映（z_range 读的是它）
            e.zmin, e.zmax = int(zmin), int(zmax)
        self.canvas.update()

    # ---------------- 生成对象（文本编辑，所见即所得） ----------------
    def refresh_objs(self, keep_sel=True):
        """把关卡文本刷进编辑器 + 重算错误行标记。

        结构化编辑（按钮 / 画布拖动）改的是 doc，这里把 doc 倒回编辑器；
        编辑器里敲的字由 _sync_obj_text 走反方向。
        """
        d = self.doc
        txt = d.to_text() if d is not None else ""
        if self.txt_objs.toPlainText() != txt:
            # 屏蔽信号：setPlainText 会把光标弹回第 0 行，
            # 不能让它触发 _on_obj_cursor（选对象 / 同步）
            self.txt_objs.blockSignals(True)
            self.txt_objs.setPlainText(txt)
            self.txt_objs.blockSignals(False)
            self._obj_line = -1
        self._update_obj_marks()

    def _update_obj_marks(self):
        """编辑器里的错误行标记 + 底下那行错误汇总。

        - spawn 的实体在库里找不到 → 红底
        - spawn 的 z 不在可走范围 → 红底
        - group 的 min 和组内实际实体数不符 → 黄底
        """
        d = self.doc
        marks, msgs = [], []
        if d is not None:
            zmin, zmax, _ = d.z_range()
            for i, o in enumerate(d.objects):
                if o.cmd == "spawn":
                    why = []
                    if o.z_out(zmin, zmax):
                        why.append(f"z={o.z} 不在可走范围 "
                                   f"{int(zmin)}~{int(zmax)}")
                    if self.canvas._ent_bad(o.name):
                        why.append(self._ent_reason_of(o.name)
                                   or "实体未找到")
                    if why:
                        marks.append((o.line, ERR_BG))
                        msgs.append(f"行{o.line + 1}：{o.name} — "
                                    + "；".join(why))
                elif o.cmd == "group" and o.args:
                    n = len(d.group_members(i))
                    mn = bl._i(o.args[0])
                    if mn != n:
                        marks.append((o.line, NOTE_BG))
                        msgs.append(f"行{o.line + 1}：group min {mn} "
                                    f"≠ 组内实体数 {n}")
        self.txt_objs.set_error_lines(marks)
        if msgs:
            self.lbl_obj_warn.setText("⚠ " + "；".join(msgs[:3])
                                      + ("…" if len(msgs) > 3 else ""))
        else:
            self.lbl_obj_warn.setText("")
        self.lbl_obj_warn.setVisible(bool(msgs))

    def _on_obj_cursor(self):
        """光标换行 = 上一行改完了：先把文本倒回文档，再选光标所在对象。"""
        if self.doc is None or self._loading:
            return
        line = self.txt_objs.textCursor().blockNumber()
        if line == self._obj_line:
            return
        self._obj_line = line
        self._sync_obj_text()          # push=True：改动前的状态进撤销栈
        self._pick_obj_at_line(line)

    def _sync_obj_text(self, push=True):
        """把编辑器文本倒回文档（编辑器才是最终依据）。

        push=True：把改动前的文档压进撤销栈（光标换行触发的同步）；
        push=False：只倒文本不压栈（结构化编辑前先对齐当前状态）。
        """
        d = self.doc
        if d is None:
            return False
        txt = self.txt_objs.toPlainText()
        if txt == d.to_text():
            return False
        if push:
            cur = self._cur_snap()
            if not (self._undo and self._undo[-1] == cur):
                self._undo.append(cur)
                if len(self._undo) > 100:
                    self._undo.pop(0)
                self._redo.clear()
        d.load_text(txt, d.path)
        if self.canvas.sel is not None and \
                self.canvas.sel >= len(d.objects):
            self.canvas.sel = None
        self.canvas._imgs.clear()      # 画布贴图缓存按内容失效
        self.refresh_props()
        self._update_obj_marks()
        self.canvas.update()
        return True

    def _pick_obj_at_line(self, line):
        """光标在第 line 行 → 选中它所属的对象（不动画布视野、
        不把编辑器光标拽走）。"""
        d = self.doc
        if d is None:
            return
        for i, o in enumerate(d.objects):
            if o.line <= line <= o.end_line:
                if i != self.canvas.sel:
                    self.select_object(i, from_canvas=True,
                                       jump_editor=False)
                return

    def _on_obj_ctx(self, pos):
        """编辑器右键：插入模板（生成点 / group / wait）。"""
        d = self.doc
        if d is None:
            return
        m = QMenu(self)
        a_sp = m.addAction("插入生成点…（选实体）")
        a_gr = m.addAction("插入 group（group 2 1）")
        a_wt = m.addAction("插入 wait（at 当前滚动位置）")
        act = m.exec_(self.txt_objs.viewport().mapToGlobal(pos))
        if act == a_sp:
            self._insert_spawn_tpl()
        elif act == a_gr:
            self._insert_lines(["group\t2 1"])
        elif act == a_wt:
            at = int(self.canvas.scroll_x + self.canvas.view_w() * 0.4)
            self._insert_lines(["wait", f"\tat\t{at}"])

    def _insert_spawn_tpl(self):
        names = self._entity_names()
        if names:
            name, ok = QInputDialog.getItem(self, "插入生成点",
                                            "选一个实体：", names, 0, False)
            if not ok:
                return
        else:
            name, ok = QInputDialog.getText(self, "插入生成点", "实体名：")
            if not ok or not name.strip():
                return
            name = name.strip()
        z = int(self._mid_z())
        at = int(self.canvas.scroll_x + self.canvas.view_w() * 0.4)
        self._insert_lines([f"spawn\t{name}",
                            f"\tcoords\t160 {z} 0",
                            f"\tat\t{at}"])

    def _insert_lines(self, lines):
        """在光标所在行上方插入模板行，并把光标放到最后一行。"""
        cur = self.txt_objs.textCursor()
        blk = cur.block()
        c = QTextCursor(blk)
        c.movePosition(QTextCursor.StartOfBlock)
        c.insertText("\n".join(lines) + "\n")
        self._obj_line = -1              # 强制下次换行时重新同步
        self._sync_obj_text()            # 模板立刻生效（画布 / 标记更新）
        self._pick_obj_at_line(c.blockNumber())

    def select_group(self, i):
        """点画布上的 group 条 → 只看这一组的实体；None = 全部显示。"""
        if i is not None and (self.doc is None or not
                              (0 <= i < len(self.doc.objects))):
            return
        self.canvas.group_sel = i
        if i is None:
            self.lbl_group.setText("显示全部")
        else:
            o = self.doc.objects[i]
            n = sum(1 for k, _x, _y in self.canvas.spawn_places())
            g = f"G{self.doc.group_no(i) + 1} " if o.cmd == "group" else ""
            self.lbl_group.setText(f"只看 {g}{o.group_label or o.cmd}"
                                   f"（{n} 个）")
        self.refresh_objs()
        self.canvas.update()

    def toggle_layout(self):
        """在“实际位置”和“按组排列”之间切（Ctrl+R 同一个入口）。

        只换排法，**缩放与滚动都原地不动**（stay where you are）——
        想看整体请按「适应窗口」。
        """
        self.canvas.layout = "col" if self.canvas.layout == "pos" else "pos"
        self.btn_layout.setChecked(self.canvas.layout == "col")
        self.btn_layout.setText("按组排列" if self.canvas.layout == "col"
                                else "实际位置")
        self.refresh_objs()          # z 越界警告只在实际位置模式标
        self.canvas.update()

    def keyPressEvent(self, e):
        if e.modifiers() & Qt.ControlModifier and e.key() == Qt.Key_R:
            self.toggle_layout()
            return
        # Alt+←/→ 换组看，Alt+↑/↓ 逐个看生成对象。
        # 右 Alt 在有些键盘布局（AltGr）下 Qt 会报成 Meta 或 Alt+Ctrl，
        # 所以把 Meta 也算作 Alt，避免“只有左 Alt 好使”。
        if e.modifiers() & (Qt.AltModifier | Qt.MetaModifier):
            if e.key() == Qt.Key_Left:
                self.step_group(-1)
                return
            if e.key() == Qt.Key_Right:
                self.step_group(1)
                return
            if e.key() == Qt.Key_Up:
                self.step_object(-1)
                return
            if e.key() == Qt.Key_Down:
                self.step_object(1)
                return
        if e.key() == Qt.Key_Escape:
            if self.canvas.wall_draw:      # 先退出画墙模式
                self.btn_newwall.setChecked(False)
                return
            if self.canvas.wall_sel is not None:
                self.select_wall(None)
                return
        super().keyPressEvent(e)

    def select_object(self, i, from_canvas=False, jump_editor=True):
        d = self.doc
        o = d.objects[i] if (d is not None and i is not None
                             and 0 <= i < len(d.objects)) else None
        self.canvas.sel = i
        self.canvas.wall_sel = None           # 生成点和墙二选一
        self.gb_wall.setVisible(False)
        if o is not None and o.cmd == "group":
            # group 用自己的参数表单（最小 / 最大实体数、at）
            self.gb_obj.setVisible(False)
            self.gb_group.setVisible(True)
            self.fill_group_form(i)
        else:
            self.gb_obj.setVisible(True)     # 选对象才显示对象属性
            self.gb_group.setVisible(False)
            self.fill_form(i)
        if o is not None and jump_editor:
            # 编辑器跳到这个对象所在的行（不抢焦点）
            self.txt_objs.flash_line(o.line, focus=False)
        if not from_canvas:
            self.canvas.update()

    # ---------------- 快捷键导航（Alt + 方向键） ----------------
    def _group_idxs(self):
        """所有 group 对象的下标。"""
        d = self.doc
        if d is None:
            return []
        return [i for i, o in enumerate(d.objects) if o.cmd == "group"]

    def step_group(self, delta):
        """Alt+← / Alt+→：只看上一组 / 下一组（当前没选组就跳到第一组）。"""
        gs = self._group_idxs()
        if not gs:
            self.status_tip("这个关卡里没有 group")
            return
        cur = self.canvas._gs()
        if cur is None or cur not in gs:
            i = gs[0]
        else:
            k = min(max(gs.index(cur) + delta, 0), len(gs) - 1)
            i = gs[k]
        self.select_group(i)
        self.status_tip(f"只看 {self.doc.objects[i].group_label}"
                        f"（第 {gs.index(i) + 1}/{len(gs)} 组）")

    def step_object(self, delta):
        """Alt+↑ / Alt+↓：看前一个 / 后一个生成对象（没选中就看第一个）。"""
        d = self.doc
        if d is None:
            return
        idxs = [i for i, o in enumerate(d.objects) if o.cmd == "spawn"]
        if not idxs:
            return
        cur = self.canvas.sel
        if cur not in idxs:
            i = idxs[0]
        else:
            k = min(max(idxs.index(cur) + delta, 0), len(idxs) - 1)
            i = idxs[k]
        self.select_object(i)             # 选中（编辑器同步跳行）
        self.focus_object(i)              # 画布也定位过去

    def reorder_sel(self, delta):
        i = self.canvas.sel
        if i is None:
            self.status_tip("先选一个生成点（画布上点，或编辑器里把光标放到它那一行）")
            return
        self.reorder_object(i, delta)

    def reorder_object(self, i, steps):
        """把生成点 i 在**同一个 group 内**挪 steps 位（正 = 更晚生成）。"""
        if self.doc is None or not steps:
            return i
        self._snapshot()
        k = 1 if steps > 0 else -1
        for _ in range(abs(steps)):
            ni = self.doc.move_object_order(i, k)
            if ni == i:                   # 挪不动了（到头了 / 跨组）
                break
            i = ni
        self.canvas.sel = i
        self.refresh_objs()
        self.canvas.update()
        self.status_tip(f"已挪到组内第 {self.canvas._col_order_of(i) + 1} 位"
                        "（#n 就是生成顺序）")
        return i

    def toggle_viewbox(self, on):
        self.canvas.show_viewbox = bool(on)
        self.canvas.update()

    # ---------------- 墙（wall）编辑 ----------------
    def wall_args(self, idx):
        """按行号取 wall 的参数（行号就是身份，改写不挪位置）。"""
        d = self.doc
        if d is None:
            return None
        for i, a in d.walls():
            if i == idx:
                return list(a)
        return None

    def select_wall(self, idx):
        """选中某个 wall（传它的行号；None = 取消选择）。"""
        if idx is not None and self.wall_args(idx) is None:
            idx = None
        if idx is not None:
            self.canvas.sel = None            # 生成点和墙二选一
        self.canvas.wall_sel = idx
        self.gb_wall.setVisible(idx is not None)
        if idx is None:
            # 取消选墙：回到对象（或 group）的表单
            d = self.doc
            i = self.canvas.sel
            o = d.objects[i] if (d is not None and i is not None
                                 and 0 <= i < len(d.objects)) else None
            is_g = o is not None and o.cmd == "group"
            self.gb_obj.setVisible(not is_g)
            self.gb_group.setVisible(is_g)
        else:
            self.gb_obj.setVisible(False)     # 编辑墙的时候不显示对象属性
            self.gb_group.setVisible(False)
        self.fill_wall_form(idx)
        self.canvas.update()

    def toggle_wall_draw(self, on):
        self.canvas.wall_draw = bool(on)
        self.canvas.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        self.canvas.update()

    def fill_wall_form(self, idx):
        """把选中 wall 的参数填进表单（不触发 apply_wall）。"""
        a = self.wall_args(idx) if idx is not None else None
        if a is None:
            return
        vals = [bl._i(v) for v in a] + [0] * (9 - len(a))
        self._wall_clamped = False
        with self._quiet():
            for k, v in zip(("x", "z", "ul", "ll", "ur", "lr", "dep",
                             "alt", "type"), vals[:9]):
                if self._set_spin(self._wsp[k], v):
                    self._wall_clamped = True

    def apply_wall(self):
        """表单改了 → 写回那一行（多余参数如 alt / type 照旧保留）。"""
        if self._loading or self.doc is None:
            return
        idx = self.canvas.wall_sel
        a = self.wall_args(idx)
        if a is None:
            return
        if getattr(self, "_wall_clamped", False):
            self.status_tip("这堵墙有参数超出数值框范围，表单暂不写回"
                            "（要改请直接改文本）")
            return
        new = [str(self._wsp[k].value())
               for k in ("x", "z", "ul", "ll", "ur", "lr", "dep",
                         "alt", "type")]
        new = new[:len(a)] if len(a) <= 9 else new + [str(x) for x in a[9:]]
        self._snapshot()
        self.doc.set_line_args(idx, "wall", new)
        self.refresh_props()
        self.canvas.update()

    def drag_wall_handle(self, idx, hid, cur, start, orig):
        """拖选中墙的把手（hid：0左上 1右上 2右下 3左下 4底边中点整体移）。

        全部按「按下时的位移」算（点在把手边缘也不会让墙先跳一下），
        且始终从按下时的参数（orig）出发，避免逐像素累积的取整漂移。
        """
        a = list(orig)
        if len(a) < 7:
            return
        dx, dz = cur[0] - start[0], cur[1] - start[1]
        x, z = bl._f(a[0]), bl._f(a[1])
        dep = bl._f(a[6])
        if hid == 0:                       # 左上：改 ul 与 depth（底边不动）
            a[2] = str(int(round(bl._f(a[2]) + dx)))
            a[6] = str(max(1, int(round(dep - dz))))
        elif hid == 1:                     # 右上：改 ur 与 depth
            a[4] = str(int(round(bl._f(a[4]) + dx)))
            a[6] = str(max(1, int(round(dep - dz))))
        elif hid in (2, 3):                # 下边两个角：改 ll/lr，纵向整底边跟着走
            k = 5 if hid == 2 else 3       # （上边不动，depth 自动跟着变）
            a[k] = str(int(round(bl._f(a[k]) + dx)))
            nd = max(1.0, dep + dz)
            a[1] = str(int(round(z + nd - dep)))
            a[6] = str(int(round(nd)))
        elif hid == 4:                     # 底边中点：整面墙跟着动
            a[0] = str(int(round(x + dx)))
            a[1] = str(int(round(z + dz)))
        self.doc.set_line_args(idx, "wall", a)
        self.fill_wall_form(idx)
        self.refresh_props()
        self.canvas.update()

    def move_wall(self, idx, orig, dx, dz):
        """整体拖动墙（dx/dz 是从按下时算的总位移）。"""
        a = list(orig)
        if len(a) < 2:
            return
        a[0] = str(int(round(bl._f(a[0]) + dx)))
        a[1] = str(int(round(bl._f(a[1]) + dz)))
        self.doc.set_line_args(idx, "wall", a)
        self.fill_wall_form(idx)
        self.refresh_props()
        self.canvas.update()

    def add_wall(self, rect):
        """画墙模式松手：矩形 → 一道新墙（四角 0/0/w/w，上边 = 矩形上边）。"""
        d = self.doc
        if d is None:
            return
        x0, z0, x1, z1 = rect
        w, dep = int(abs(x1 - x0)), int(abs(z1 - z0))
        if w < 4 or dep < 2:
            return                        # 太小当成误点，不建
        x = int(min(x0, x1))
        z = int(max(z0, z1))              # 底边 = 矩形的下边
        self._snapshot()
        idx = d.add_terrain("wall", [str(x), str(z), "0", "0",
                                     str(w), str(w), str(dep)])
        self.refresh_props()
        self.select_wall(idx)
        self.status_tip("已新建一道墙 x%d z%d 宽%d 深%d —— 拖角改形状，"
                        "拖底边中点整体移动" % (x, z, w, dep))

    def del_wall(self):
        d = self.doc
        idx = self.canvas.wall_sel
        a = self.wall_args(idx)
        if d is None or a is None:
            return
        r = QMessageBox.question(self, "删除墙",
                                 "删除这道墙？\nwall " + " ".join(a))
        if r != QMessageBox.Yes:
            return
        self._snapshot()
        d.del_line(idx)
        self.select_wall(None)
        self.refresh_props()
        self.canvas.update()

    def focus_object(self, i, force=False):
        """把视野挪到生成点 i 上。

        按组排列：定位到**实体条目**上（极端情况下条目离组标记很远，
        定位到组标记就看不到条目了）。
        force=False（Alt+方向键 / 列表点选）时在全景模式下**不切模式、
        不改缩放**，只在它跑出视野时才把画面挪过去 —— 逐个翻看时视线不会被打乱。
        force=True（双击）才是老行为：切到屏幕预览并放大。
        """
        d = self.doc
        if d is None or not (0 <= i < len(d.objects)):
            return
        o = d.objects[i]
        if self.canvas.layout == "col":
            self.canvas._focus_col_cell(i)
            return
        if o.abs_x is None and o.at is None:
            return
        if force or self.canvas.mode == "screen":
            self.canvas.mode = "screen"
            self.btn_scr.setChecked(True)
            self.btn_map.setChecked(False)
            self.canvas.zoom = max(self.canvas.zoom, 0.5)
            self.canvas.scroll_x = max(0.0, (o.at or o.abs_x or 0) - 80)
            self.canvas.scroll_z = -10.0
        else:
            self._ensure_visible(i)
        self.sync_scroll()
        self.canvas.update()

    def _ensure_visible(self, i):
        """全景模式下：对象已经在视野里就不动，跑出去了才把画面挪过去。"""
        o = self.doc.objects[i]
        x = o.abs_x
        if x is None:
            return
        vw = self.canvas.view_w()
        sx = self.canvas.scroll_x
        if sx + 24 <= x <= sx + vw - 24:
            return
        self.canvas.scroll_x = max(0.0, x - vw * 0.35)

    def _focus_group(self, i):
        """列表里选中 group：画布定位到这一组（空组定位标记条）。"""
        d = self.doc
        c = self.canvas
        o = d.objects[i]
        if c.layout == "col":
            mem = d.group_members(i)
            if mem:
                c._focus_col_cell(mem[0])
                return
            _sd, vert = d.scroll_dir()
            if vert:
                try:
                    k = c._side_markers().index(i)
                except ValueError:
                    return
                c.row_scroll = max(0.0, c._col_top() + k * 22 - 40)
                c._clamp_row_scroll()
            else:
                self._scroll_to_at(o.at or 0)
            c.update()
            return
        self._scroll_to_at(o.at or 0)
        c.update()

    def _scroll_to_at(self, at):
        c = self.canvas
        vw = c.view_w()
        if not (c.scroll_x + 24 <= at <= c.scroll_x + vw - 24):
            c.scroll_x = max(0.0, at - vw * 0.35)

    # ---------------- 双击生成点 → 动画编辑页 ----------------
    def jump_to_entity(self, name):
        """双击生成点：切到动画编辑标签页，显示该实体（默认 idle）。

        关卡编辑这边什么都不清：选中项、视野、撤销栈都留着，
        切回「关卡编辑」标签页就回到刚才改的地方。
        """
        b = self.bor
        m = self.main
        if b is None or m is None or not name:
            return
        key = (name or "").lower()
        for r in range(b.ent_list.count()):
            it = b.ent_list.item(r)
            ei = it.data(Qt.UserRole) if it is not None else None
            if ei is not None and ei < len(b.entities) \
                    and (b.entities[ei][0] or "").lower() == key:
                m.tabs.setCurrentWidget(b)
                if b.ent_list.currentRow() != r:
                    b.ent_list.setCurrentRow(r)   # 触发载入（含未保存提示）
                b.ent_list.setFocus()
                return
        self.status_tip(f"实体列表里没有 {name}"
                        "（先在动画编辑页导入 data 目录）")

    @staticmethod
    def _set_spin(sp, v):
        """把值填进数值框，超出范围就**夹住**并返回 True。

        真实关卡里 health / aggression 常被写成 `9999999999999999` 这种
        “当成无限用”的哨兵值（本数据里有 500+ 处），直接 setValue 会让 Qt
        抛 OverflowError。夹住的值**不能写回 txt**，谁被夹了记在
        self._clamped 里，apply_props 会跳过这些字段。
        """
        try:
            n = int(round(float(v)))
        except (TypeError, ValueError):
            sp.setValue(sp.minimum())
            return False
        lo, hi = sp.minimum(), sp.maximum()
        sp.setValue(max(lo, min(hi, n)))
        return not (lo <= n <= hi)

    def fill_form(self, i):
        d = self.doc
        if d is None or not (0 <= i < len(d.objects)):
            return
        o = d.objects[i]
        q = self._quiet()
        self._clamped = {}
        with q:
            self.ed_name.setText(o.name)
            c = o.coords or (0.0, 0.0, 0.0)
            for sp, v in ((self.sp_x, c[0]), (self.sp_z, c[1]),
                          (self.sp_a, c[2])):
                if self._set_spin(sp, v):
                    self._clamped["coords"] = "coords " + " ".join(
                        str(x) for x in c)
            if self._set_spin(self.sp_at, o.at or 0):
                self._clamped["at"] = f"at {o.at}"
            hp = o.props.get("health")
            self.ck_health.setChecked(bool(hp))
            if self._set_spin(self.sp_health, o.prop_int("health", 100)):
                self._clamped["health"] = "health " + " ".join(hp[1] if hp else [])
            ag = o.props.get("aggression")
            self.ck_aggr.setChecked(bool(ag))
            if self._set_spin(self.sp_aggr, o.prop_int("aggression", 0)):
                self._clamped["aggression"] = "aggression " + " ".join(
                    ag[1] if ag else [])
            self.ck_flip.setChecked(o.prop_int("flip", 0) == 1)
            self.ck_boss.setChecked(o.prop_int("boss", 0) == 1)
            self.ed_alias.setText((o.prop("alias") or [""])[0])
            item = (o.prop("item") or [""])[0]
            self.ed_item.setText(item)
            self._show_item_thumb(item)
            mp = o.props.get("map")
            self.ck_map.setChecked(bool(mp))
            if self._set_spin(self.sp_map, o.prop_int("map", 0)):
                self._clamped["map"] = "map " + " ".join(mp[1] if mp else [])
            self.ed_weapon.setText((o.prop("weapon") or [""])[0])
            info = []
            if o.script:
                info.append("带 @script 脚本块（不在这里编辑）")
            for cmd, raw in sorted(self._clamped.items()):
                info.append(f"⚠ {raw}：超出数值框范围，表单不会改写它"
                            "（要改请直接改文本）")
            for cmd, (_, a) in sorted(o.props.items()):
                if cmd not in ("coords", "at", "health", "aggression",
                               "flip", "boss", "alias", "item", "map",
                               "weapon"):
                    info.append(f"{cmd} {' '.join(a)}")
            self.lbl_info.setText("；".join(info))

    # ---- item 掉落物缩略图 ----
    def _show_item_thumb(self, name):
        """item 后面那张小图：掉落物品 idle 首帧（裁掉背景色后的最大矩形）。"""
        pm = None
        if name:
            im, _ox, _oy, _over = self.entity_thumb(name)
            if im is not None:
                k = min(ITEM_TH_W / max(1, im.width()),
                        ITEM_TH_H / max(1, im.height()))
                pm = QPixmap.fromImage(im).scaled(
                    max(1, int(im.width() * k)), max(1, int(im.height() * k)),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
        # setPixmap 不接受 None：没图时给一张空的 QPixmap 把旧的清掉
        self.lbl_item_thumb.setPixmap(pm if pm is not None else QPixmap())

    # ---- group 的参数表单 ----
    def fill_group_form(self, i):
        """把选中 group 的两个参数填进表单（不触发 apply_group）。"""
        d = self.doc
        o = d.objects[i]
        self._g_clamped = False
        with self._quiet():
            a = list(o.args) + ["1", "1"]
            if self._set_spin(self.sp_gmin, bl._i(a[0], 1)):
                self._g_clamped = True
            if self._set_spin(self.sp_gmax, bl._i(a[1], 1)):
                self._g_clamped = True
            if self._set_spin(self.sp_gat, o.at or 0):
                self._g_clamped = True
        n = len(d.group_members(i))
        mn = bl._i(a[0], 1)
        self.lbl_group_info.setText(
            f"G{d.group_no(i) + 1}　组里现有 {n} 个实体"
            + (f"（⚠ 首参数 {mn} 与实际数不符）" if mn != n else ""))

    def apply_group(self):
        """group 表单改了 → 写回那一行（`group 最小 最大`）和 at。"""
        if self._loading or self.doc is None:
            return
        i = self.canvas.sel
        if i is None or not (0 <= i < len(self.doc.objects)):
            return
        o = self.doc.objects[i]
        if o.cmd != "group":
            return
        if getattr(self, "_g_clamped", False):
            self.status_tip("这个 group 有参数超出数值框范围，表单暂不写回"
                            "（要改请直接改文本）")
            return
        self._snapshot()
        d = self.doc
        args = [str(self.sp_gmin.value()), str(self.sp_gmax.value())] \
            + list(o.args[2:])
        bl.write_line(d.lines[o.line], "group", args)
        d._parse()
        if 0 <= i < len(d.objects):
            d.set_obj_prop(d.objects[i], "at", [str(self.sp_gat.value())])
        self.refresh_props()
        self.refresh_objs()
        self.canvas.update()

    # ---- 简单的“回写时不触发”保护 ----
    def _quiet(self):
        outer = self

        class _Q:
            def __enter__(self):
                outer._loading = True
                return self

            def __exit__(self, *a):
                outer._loading = False
        return _Q()

    def apply_props(self):
        if self._loading or self.doc is None:
            return
        i = self.canvas.sel
        if i is None or not (0 <= i < len(self.doc.objects)):
            return
        self._snapshot()
        o = self.doc.objects[i]
        d = self.doc
        if o.cmd == "spawn" and self.ed_name.text().strip() \
                and self.ed_name.text().strip() != o.name:
            bl.write_line(d.lines[o.line], "spawn",
                          [self.ed_name.text().strip()] + o.args[1:])
            d._parse()
            o = d.objects[i]
        # 每一步都可能插入 / 删除行，后面所有编辑都要用**回传的**对象
        # 值超出数值框范围被夹过的字段不写回（否则会把 999999999999 改成 999999）
        if "coords" not in self._clamped:
            o = d.set_obj_prop(o, "coords", [str(self.sp_x.value()),
                                             str(self.sp_z.value()),
                                             str(self.sp_a.value())])
        if "at" not in self._clamped:
            o = d.set_obj_prop(o, "at", [str(self.sp_at.value())])
        o = self._toggle_prop(o, "health", self.ck_health, self.sp_health)
        o = self._toggle_prop(o, "aggression", self.ck_aggr, self.sp_aggr)
        o = self._toggle_prop(o, "flip", self.ck_flip, None, "1", "0")
        o = self._toggle_prop(o, "boss", self.ck_boss, None, "1", "0")
        o = self._toggle_prop(o, "map", self.ck_map, self.sp_map)
        o = self._text_prop(o, "alias", self.ed_alias)
        o = self._text_prop(o, "item", self.ed_item)
        o = self._text_prop(o, "weapon", self.ed_weapon)
        self.canvas.sel = d.objects.index(o) if o in d.objects else i
        self.canvas.update()
        self.refresh_objs()

    def _toggle_prop(self, o, cmd, ck, sp=None, on="1", off="0"):
        """返回（可能重新定位过的）对象，方便连续多次编辑。"""
        d = self.doc
        if cmd in self._clamped:
            return o              # 原值超出数值框范围：保持原样，别写回
        if ck.isChecked():
            args = [str(sp.value())] if sp is not None else [on]
            return d.set_obj_prop(o, cmd, args)
        if o.props.get(cmd):
            return d.del_obj_prop(o, cmd)
        return o

    def _text_prop(self, o, cmd, ed):
        """同 _toggle_prop：返回重新定位后的对象。"""
        d = self.doc
        v = ed.text().strip()
        if v:
            return d.set_obj_prop(o, cmd, v.split())
        if o.props.get(cmd):
            return d.del_obj_prop(o, cmd)
        return o

    def move_object(self, i, x, z):
        """画布上拖动生成点：把 coords 的 x / z 设成给定值（at 不变）。

        传进来的已经是**目标值**（画布那边按“按下去时的位移”算好的），
        所以这里不再做任何坐标换算。
        """
        d = self.doc
        if d is None or not (0 <= i < len(d.objects)):
            return
        o = d.objects[i]
        if o.cmd != "spawn" or o.coords is None:
            return
        d.set_obj_prop(o, "coords", [str(int(round(x))), str(int(round(z))),
                                     str(int(o.coords[2]))])
        self.fill_form(i)
        self.refresh_objs()

    def add_spawn(self):
        d = self.doc
        if d is None:
            return
        names = []
        b = self.bor
        if b is not None:
            names = sorted({(n or "") for n, _, _ in b.entities if n})
        if names:
            name, ok = QInputDialog.getItem(self, "新增生成点",
                                            "选一个实体：", names, 0, False)
            if not ok:
                return
        else:
            name, ok = QInputDialog.getText(self, "新增生成点", "实体名：")
            if not ok or not name.strip():
                return
        self._snapshot()
        scroll = int(self.canvas.scroll_x + self.canvas.view_w() * 0.4)
        o = d.add_spawn(name, coords=(160.0, float(self._mid_z()), 0.0),
                        at=scroll)
        self.refresh_objs()
        if o is not None:
            i = d.objects.index(o)
            self.select_object(i)
            self.focus_object(i)

    def _mid_z(self):
        zmin, zmax, _ = self.doc.z_range()
        return (zmin + zmax) / 2.0

    def fix_z(self):
        """把越界的生成点 z 夹回可走范围（选中一个就只改它）。"""
        d = self.doc
        if d is None:
            return
        zmin, zmax, _ = d.z_range()
        bad = d.out_of_band()
        if not bad:
            QMessageBox.information(self, "z 拉回",
                                    "所有生成点的 z 都在可走范围内。")
            return
        i = self.canvas.sel
        todo = [i] if (i in bad) else bad
        if i in bad and len(bad) > 1:
            r = QMessageBox.question(
                self, "z 拉回",
                f"当前选中的生成点 z 越界了。\n\n"
                f"是：只改这一个\n否：把全部 {len(bad)} 个越界的都改掉")
            todo = [i] if r == QMessageBox.Yes else bad
        n = 0
        self._snapshot()
        for k in sorted(todo):
            o = d.objects[k]
            if o.coords is None:
                continue
            nz = int(min(max(o.coords[1], zmin), zmax))
            o = d.set_obj_prop(o, "coords",
                               [str(int(o.coords[0])), str(nz),
                                str(int(o.coords[2]))])
            n += 1
        self.refresh_objs()
        self.canvas.update()
        if n:
            self.status_tip(f"已把 {n} 个生成点的 z 夹回 "
                            f"{int(zmin)}~{int(zmax)}")

    def status_tip(self, msg):
        m = getattr(self.main, "statusBar", None)
        if callable(m):
            m().showMessage(msg, 4000)
        else:
            self.setWindowTitle("关卡编辑 — " + msg)

    def del_spawn(self):
        d = self.doc
        i = self.canvas.sel
        if d is None or i is None or not (0 <= i < len(d.objects)):
            return
        o = d.objects[i]
        r = QMessageBox.question(self, "删除", f"删除 {o.title} ？")
        if r != QMessageBox.Yes:
            return
        self._snapshot()
        d.delete_object(o)
        self.canvas.sel = None
        self.refresh_objs()
        self.canvas.update()

    # ---------------- 中键 / 右键菜单动作 ----------------
    def _entity_names(self):
        """可选实体名列表（借动画页的索引；没导入就空）。"""
        b = self.bor
        if b is None:
            return []
        return sorted({(n or "") for n, _, _ in b.entities if n})

    def add_marker(self, cmd, args, at):
        """中键菜单：在 at 处插一条 wait / group。"""
        d = self.doc
        if d is None:
            return
        self._snapshot()
        k = d.add_marker(cmd, args, at)
        self.refresh_objs()
        self.refresh_props()
        self.canvas.update()
        if k is not None:
            self.status_tip(f"已在 at {at} 添加 {cmd}"
                            f"（{' '.join(args)}）" if args else
                            f"已在 at {at} 添加 {cmd}")

    def spawn_menu(self, i, gpos):
        """实体右键：复制一份 / 复制到剪贴板 / 换实体 / 删除。"""
        d = self.doc
        if d is None or not (0 <= i < len(d.objects)):
            return
        o = d.objects[i]
        m = QMenu(self)
        a_dup = m.addAction("在旁边复制一份")
        a_copy = m.addAction("复制到剪贴板")
        a_chg = m.addAction("换实体…")
        m.addSeparator()
        a_del = m.addAction("删除")
        act = m.exec_(gpos)
        if act is a_dup:
            self.duplicate_spawn(i)
        elif act is a_copy:
            QApplication.clipboard().setText(
                "\n".join(d.lines[k].raw
                          for k in range(o.line, o.end_line + 1)))
            self.status_tip("已把这个生成点的文本复制到剪贴板")
        elif act is a_chg:
            self.change_spawn_entity(i)
        elif act is a_del:
            self.del_spawn()

    def duplicate_spawn(self, i):
        """把生成点 i 原样复制一份（同组、紧跟其后，x 错开一点免得重叠）。"""
        d = self.doc
        if d is None:
            return
        self._snapshot()
        k = d.duplicate_object(i)
        if k is None:
            return
        o = d.objects[k]
        if o.coords is not None:
            o = d.set_obj_prop(o, "coords",
                               [str(int(o.coords[0] + 16)),
                                str(int(o.coords[1])),
                                str(int(o.coords[2]))])
            k = d.objects.index(o)
        self.refresh_objs()
        self.select_object(k)
        self.status_tip("已复制一份（x 错开 16 像素，归同一个组管）")

    def change_spawn_entity(self, i):
        """换实体：只改 spawn 那行的名字，其余属性原样保留。"""
        d = self.doc
        if d is None or not (0 <= i < len(d.objects)):
            return
        o = d.objects[i]
        names = self._entity_names()
        if names:
            cur = names.index(o.name) if o.name in names else 0
            name, ok = QInputDialog.getItem(self, "换实体", "换成：",
                                            names, cur, False)
        else:
            name, ok = QInputDialog.getText(self, "换实体", "实体名：",
                                            text=o.name)
        if not ok or not str(name).strip() or str(name).strip() == o.name:
            return
        self._snapshot()
        bl.write_line(d.lines[o.line], "spawn",
                      [str(name).strip()] + o.args[1:])
        d._parse()
        self.refresh_objs()
        self.canvas.update()
        self.status_tip(f"已把 {o.name} 换成 {name}")

    def group_menu(self, g, gpos):
        """group 标记条右键：添加实体 / 清空所有实体 / 只看这一组。"""
        d = self.doc
        if d is None or not (0 <= g < len(d.objects)):
            return
        n = len(d.group_members(g))
        m = QMenu(self)
        a_add = m.addAction("在此组添加实体…")
        a_clear = m.addAction(f"清空此组所有实体（{n} 个）")
        m.addSeparator()
        a_only = m.addAction("只看这一组")
        a_all = m.addAction("显示全部")
        act = m.exec_(gpos)
        if act is a_add:
            self.add_spawn_to_group(g)
        elif act is a_clear:
            self.clear_group_entities(g)
        elif act is a_only:
            self.select_group(g)
        elif act is a_all:
            self.select_group(None)

    def add_spawn_to_group(self, g):
        """group 菜单：在这个组末尾加一个新实体。"""
        d = self.doc
        if d is None:
            return
        names = self._entity_names()
        if names:
            name, ok = QInputDialog.getItem(self, "添加实体",
                                            "选一个实体：", names, 0, False)
            if not ok:
                return
        else:
            name, ok = QInputDialog.getText(self, "添加实体", "实体名：")
            if not ok or not name.strip():
                return
        self._snapshot()
        at = d.objects[g].at or 0
        k = d.add_spawn_to_group(g, name.strip(),
                                 (160.0, float(self._mid_z()), 0.0), at)
        self.refresh_objs()
        self.canvas.update()
        if k is not None:
            self.select_object(k)
            self.status_tip(f"已在 {d.objects[g].group_label} 末尾添加 {name}")

    def clear_group_entities(self, g):
        """group 菜单：删掉这个组的全部实体（group 行自己保留）。"""
        d = self.doc
        if d is None:
            return
        n = len(d.group_members(g))
        if not n:
            self.status_tip("这个组本来就是空的")
            return
        r = QMessageBox.question(
            self, "清空组",
            f"删掉 {d.objects[g].group_label} 名下的全部 {n} 个实体？\n"
            f"（group 行自己保留，可 Ctrl+Z 撤销）")
        if r != QMessageBox.Yes:
            return
        self._snapshot()
        d.clear_group(g)
        self.canvas.sel = None
        self.refresh_objs()
        self.canvas.update()
        self.status_tip(f"已清空 {n} 个实体")

    # ---------------- 视图 / 保存 ----------------
    def set_mode(self, mode):
        self.canvas.mode = mode
        self.btn_map.setChecked(mode == "map")
        self.btn_scr.setChecked(mode == "screen")
        if mode == "map":
            self.canvas_fit()
        else:
            self.canvas.update()

    def canvas_fit(self):
        self.canvas.mode = "map"
        self.btn_map.setChecked(True)
        self.btn_scr.setChecked(False)
        self.canvas.fit(for_col=(self.canvas.layout == "col"))

    def zoom_by(self, k):
        self.canvas.zoom = min(4.0, max(0.02, self.canvas.zoom * k))
        self.canvas.update()

    def on_scroll_spin(self, v):
        if self._loading:
            return
        self.canvas.mode = "screen"
        self.btn_scr.setChecked(True)
        self.btn_map.setChecked(False)
        self.canvas.scroll_x = float(v)
        self.canvas.update()

    def sync_scroll(self):
        """画布平移后把滚动位置回写到工具栏（不再反过来触发滚动）。"""
        q = self._quiet()
        with q:
            v = int(max(0, self.canvas.scroll_x))
            if v != self.sp_scroll.value():
                self.sp_scroll.setValue(v)

    def save_level(self):
        self._sync_obj_text(push=False)   # 敲完还没换行的字也要存进去
        d = self.doc
        if d is None:
            return
        r = QMessageBox.question(
            self, "保存", f"把修改写回：\n{d.path}\n\n是否继续？")
        if r != QMessageBox.Yes:
            return
        try:
            d.save()
        except Exception as ex:
            QMessageBox.warning(self, "保存失败", str(ex))
            return
        self._remember()
        QMessageBox.information(self, "保存完成", "已写回关卡 TXT。")
