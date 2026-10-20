"""Mo改OpenBOR编辑器（OpenBoR MoDefine） —— 主窗口。

运行: python main.py  (依赖: PyQt5, Pillow)
标签页顺序：OpenBOR 动画编辑（主）→ 索引图（Sprite）编辑。
"""
import json
import os
import shutil
import sys
import zlib

from PyQt5.QtCore import QEvent, QPointF, QProcess, Qt
from PyQt5.QtGui import QColor, QCursor, QKeySequence
from PyQt5.QtWidgets import (QAbstractButton, QAbstractSlider, QAbstractSpinBox,
                             QApplication, QColorDialog, QDialog, QFileDialog,
                             QHBoxLayout, QInputDialog, QLineEdit, QMainWindow,
                             QMenu, QMessageBox, QPushButton, QShortcut,
                             QSplitter, QTabWidget, QTreeView, QVBoxLayout,
                             QWidget)

try:                                  # QtWebEngine 必须在 QApplication 之前导入
    import manual_viewer
except Exception:                     # pragma: no cover
    manual_viewer = None

from core import ImageCache, TempStore, UndoManager, iu
from core import FrameData, Palette, Project, Track
from sprite_ui import (CanvasPanel, CanvasWidget, PaletteGrid, ProjectPanel,
                       ToolPanel, TimelinePanel, TimelineWidget)

MAGIC = b"FGP1"
EXT = ".fgp"
APP_TITLE = "Mo改OpenBOR编辑器（OpenBoR MoDefine）v0.1"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project = Project()
        self.cache = ImageCache()
        self.temp_store = TempStore()
        self.undo_mgr = UndoManager(30)
        self.selection = set()
        self.primary = None
        self.anchor = None
        self._trash = []
        self.dirty = False
        self.browse_idx = None
        self.palette_highlight = None
        self.pencil_mode = False
        self.picker_mode = False
        self.color_isolation = False    # 画布颜色隔离（选中色块高亮显示）
        self._swatch_clip = None        # 色块颜色剪贴板（跨色表 Ctrl+C/V）
        self.stroke = None
        self._click_pos = None          # 全局点击判定（区分点击与拖拽）
        self._alt_picker = False        # ALT 键临时吸色模式

        central = QWidget()
        self.setCentralWidget(central)
        lay = QHBoxLayout(central)
        lay.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # ---- 标签页 1：OpenBOR 人物动画编辑（主打功能，放最前） ----
        from openbor_ui import OpenBorPanel
        self.openbor_panel = OpenBorPanel(self)
        self.tabs.addTab(self.openbor_panel, "OpenBOR 动画编辑")

        # ---- 标签页 2：关卡编辑（levels.txt + 关卡场景） ----
        from bor_level_ui import LevelPanel
        self.level_panel = LevelPanel(self)
        # 借动画页的 data 根目录 / 实体列表，这样生成点能画出实体图
        self.level_panel.bor = self.openbor_panel
        self.tabs.addTab(self.level_panel, "关卡编辑")

        # ---- 标签页 3：索引图编辑（原有整套 GUI） ----
        tab1 = QWidget()
        t1 = QHBoxLayout(tab1)
        t1.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)
        self.project_panel = ProjectPanel(self)
        self.canvas_panel = CanvasPanel(self)
        self.timeline_panel = TimelinePanel(self)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        vs = QSplitter(Qt.Vertical)
        vs.addWidget(self.canvas_panel)
        vs.addWidget(self.timeline_panel)
        vs.setStretchFactor(0, 3)
        vs.setStretchFactor(1, 2)
        cl.addWidget(vs)
        self.tool_panel = ToolPanel(self)
        splitter.addWidget(self.project_panel)
        splitter.addWidget(center)
        splitter.addWidget(self.tool_panel)
        splitter.setSizes([240, 950, 240])
        t1.addWidget(splitter)
        self.tabs.addTab(tab1, "索引图编辑")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        QShortcut(QKeySequence.Save, self, activated=self.on_save_hotkey)
        QShortcut(QKeySequence.Undo, self, activated=self.undo)
        QShortcut(QKeySequence.Redo, self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        QShortcut(QKeySequence("Alt+C"), self, activated=self.toggle_tool)

        self.resize(1400, 900)
        self.statusBar().showMessage("提示：点击画布或轨道区获得键盘焦点后，快捷键才对该区域生效")
        self._add_manual_button()
        self.apply_ui_state()

    # ---- 状态栏右下角：启动游戏 + OpenBOR 手册 ----
    def _add_manual_button(self):
        # 启动游戏：全局功能，无论在哪个标签页改了什么都能一键进游戏验证
        g = QPushButton("▶ 启动游戏")
        g.setObjectName("gameBtn")
        g.setToolTip("运行 data 目录同级的 OpenBOR.exe，验证刚改的效果\n"
                     "（没找到会提示）")
        g.setCursor(Qt.PointingHandCursor)
        g.setStyleSheet(
            "QPushButton#gameBtn{background:#3f8f5f;color:#ffffff;"
            "border:1px solid #3f8f5f;border-radius:4px;font-size:12px;"
            "padding:3px 10px;font-weight:bold;}"
            "QPushButton#gameBtn:hover{background:#4aa06d;}"
            "QPushButton#gameBtn:pressed{background:#357a52;}")
        g.clicked.connect(self._launch_game)
        self.btn_game = g
        self.statusBar().addPermanentWidget(g)
        if manual_viewer is None:
            return
        b = QPushButton("❓")
        b.setObjectName("manualBtn")
        b.setToolTip("查看 OpenBOR 手册（本地副本：目录跳转 + 关键字搜索）")
        b.setFixedSize(26, 26)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(
            "QPushButton#manualBtn{background:#2f6db3;color:#ffffff;"
            "border:1px solid #2f6db3;border-radius:13px;font-size:14px;"
            "font-weight:bold;padding:0px;}"
            "QPushButton#manualBtn:hover{background:#3a7cc4;}"
            "QPushButton#manualBtn:pressed{background:#265d99;}")
        b.clicked.connect(lambda: manual_viewer.show_manual(self))
        self.btn_manual = b
        self.statusBar().addPermanentWidget(b)
        self.update_title()

    # ---- 启动游戏（全局：任何标签页都能用） ----
    def _game_data_dir(self):
        """找 data 目录：优先动画页导入的，其次关卡页 levels.txt 所在处。"""
        p = getattr(self, "openbor_panel", None)
        if p is not None and getattr(p, "data_dir", ""):
            return p.data_dir
        lp = getattr(self, "level_panel", None)
        if lp is not None and getattr(lp, "levels_path", ""):
            return os.path.dirname(lp.levels_path)
        return ""

    def _launch_game(self):
        """运行 data 目录同级的 OpenBOR.exe（后台启动，不卡住编辑器）。"""
        dd = self._game_data_dir()
        if not dd:
            QMessageBox.information(self, "启动游戏",
                                    "先在动画编辑页导入 data 目录"
                                    "（或在关卡编辑页载入 levels.txt）。")
            return
        exe = os.path.join(os.path.dirname(dd), "OpenBOR.exe")
        if not os.path.exists(exe):
            QMessageBox.warning(self, "启动游戏",
                                f"没找到 OpenBOR.exe：\n{exe}\n\n"
                                f"它应该和 data 目录在同一个文件夹里。")
            return
        ok = QProcess.startDetached(exe, [], os.path.dirname(exe))
        if not ok:
            QMessageBox.warning(self, "启动游戏", f"启动失败：\n{exe}")

    # ================= 标题 =================
    def update_title(self):
        t = APP_TITLE
        if self.project.file_path:
            t += f" - {os.path.basename(self.project.file_path)}"
        if self.dirty:
            t += " *"
        self.setWindowTitle(t)

    # ================= 全局“空白点击”判定（取消颜色选择用） =================
    def eventFilter(self, obj, ev):
        t = ev.type()
        # ALT 吸色：仅当鼠标悬停在画布上时，按住 ALT 进入临时吸色模式
        if t == QEvent.KeyPress and ev.key() == Qt.Key_Alt \
                and not ev.isAutoRepeat():
            if QApplication.widgetAt(QCursor.pos()) is self.canvas_panel.canvas \
                    and not self.picker_mode:
                self._alt_picker = True
                self.set_picker_mode(True)
        elif t == QEvent.KeyRelease and ev.key() == Qt.Key_Alt \
                and not ev.isAutoRepeat():
            if self._alt_picker:
                self._alt_picker = False
                self.set_picker_mode(False)
        if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._click_pos = ev.globalPos()
        elif t == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
            if (self._click_pos is not None
                    and (ev.globalPos() - self._click_pos).manhattanLength() < 6):
                self._maybe_cancel_color_sel(ev.globalPos())
            self._click_pos = None
        return super().eventFilter(obj, ev)

    @staticmethod
    def _is_interactive(w):
        while w is not None:
            if isinstance(w, (PaletteGrid, CanvasWidget, TimelineWidget,
                              QAbstractButton, QAbstractSlider, QAbstractSpinBox,
                              QLineEdit, QTreeView, QMenu, QDialog)):
                return True
            w = w.parentWidget()
        return False

    def _maybe_cancel_color_sel(self, gpos):
        grid = self.tool_panel.grid
        if not grid.sel and not self.color_isolation:
            return
        w = QApplication.widgetAt(gpos)
        if w is self.canvas_panel.canvas:
            # 画布上：只有点到图片范围之外才取消选择
            if not w.frame_hit(w.mapFromGlobal(gpos)):
                self.cancel_color_selection()
            return
        if self._is_interactive(w):
            return
        self.cancel_color_selection()

    def cancel_color_selection(self):
        self.tool_panel.grid.clear_selection()
        self.color_isolation = False
        self.canvas_panel.canvas.update()
        self.tool_panel._update_info()

    # ================= 选择 =================
    def primary_frame(self):
        if self.primary is None:
            return None
        ti, fi = self.primary
        if ti < len(self.project.tracks) and \
                self.project.tracks[ti].frame_at(fi) is not None:
            return self.primary
        return None

    def reference_frame(self):
        """画布信息/锚点显示所用的“当前帧”。"""
        prj = self.project
        pf = self.primary_frame()
        if pf and pf[1] == prj.playhead:
            return prj.tracks[pf[0]].frame_at(pf[1])
        if prj.tracks:
            ti = min(prj.current_track, len(prj.tracks) - 1)
            return prj.tracks[ti].frame_at(prj.playhead)
        return None

    def set_selection(self, sel, primary=None, track=None, keep_anchor=False):
        self.selection = set(sel)
        if primary is not None:
            self.primary = primary
            if not keep_anchor:
                self.anchor = primary
        elif self.selection:
            self.primary = sorted(self.selection)[-1]
        else:
            self.primary = None
        if track is not None:
            self.project.current_track = track
        for ti, fi in sorted(self.selection):
            f = self.project.tracks[ti].frame_at(fi)
            if f and f.palette_id:
                pal = self.project.find_palette(f.palette_id)
                if pal and pal in self.project.palettes:
                    self.browse_idx = self.project.palettes.index(pal)
                break
        self.refresh_all()

    def set_primary(self, key):
        self.primary = key
        self.refresh_all()

    def set_current_track(self, ti):
        if 0 <= ti < len(self.project.tracks):
            self.project.current_track = ti
            self.refresh_all()

    # ================= 刷新 =================
    def refresh_all(self):
        self.canvas_panel.canvas.update()
        self.canvas_panel.sync_anchor_boxes()
        self.canvas_panel.update_info()
        self.timeline_panel.widget.update()
        self.timeline_panel.sync_scroll()
        self.timeline_panel.update_stats()
        prj = self.project
        pal = self.displayed_palette()
        self.tool_panel.show_palette(pal, len(prj.palettes), self.browse_idx or 0)

    project_changed = refresh_all

    def refresh_canvases(self):
        self.canvas_panel.canvas.update()
        self.timeline_panel.widget.update()

    def sync_zoom_slider(self):
        self.canvas_panel.sync_slider()

    def sync_align_buttons(self):
        m = self.project.align_mode
        cp = self.canvas_panel
        for mode, btn in (("topleft", cp.btn_tl), ("center", cp.btn_c),
                          ("fixed", cp.btn_anchor)):
            btn.blockSignals(True)
            btn.setChecked(m == mode)
            btn.blockSignals(False)

    def set_align(self, mode):
        prj = self.project
        if prj.align_mode == mode:
            return
        self.push_undo()
        prj.align_mode = mode
        self.sync_align_buttons()
        self.refresh_all()

    def apply_anchor_from_boxes(self):
        cp = self.canvas_panel
        try:
            x = int(cp.le_x.text().strip())
            y = int(cp.le_y.text().strip())
        except ValueError:
            self.statusBar().showMessage("锚点坐标必须为整数", 3000)
            self.sync_align_buttons()
            return
        self.push_undo()
        self.project.fixed_point = [x, y]
        self.project.align_mode = "fixed"
        self.sync_align_buttons()
        self.refresh_all()

    # ================= 撤销 / 重做 =================
    def _on_tab_changed(self, i):
        """页签切换时的「懒同步」：同一 data 目录下的 levels.txt 和
        models.txt 本来就是一对，任何一边换了目录都不必立刻重载，
        等切到另一页发现不一致时才动。
        """
        w = self.tabs.widget(i)
        lp = getattr(self, "level_panel", None)
        op = getattr(self, "openbor_panel", None)
        if lp is not None and w is lp:
            try:
                lp.auto_sync()          # 还没载入过：直接跟动画页的目录
            except Exception:
                pass
            try:
                lp.lazy_follow_bor()    # 动画页换了 data 目录：静默重载
            except Exception:
                pass
        elif op is not None and w is op:
            try:
                self._lazy_follow_levels()
            except Exception:
                pass

    def _lazy_follow_levels(self):
        """切到动画页：关卡页载入的 levels.txt 在别的 data 目录下，
        且那边有 models.txt → 重新导入（含检错）；一致就什么都不做。"""
        lp = getattr(self, "level_panel", None)
        op = getattr(self, "openbor_panel", None)
        if lp is None or op is None or not getattr(lp, "levels_path", ""):
            return
        dd = os.path.dirname(os.path.abspath(lp.levels_path))
        cur = getattr(op, "data_dir", "") or ""
        if not cur or os.path.abspath(cur) == dd:
            return
        if not os.path.exists(os.path.join(dd, "models.txt")):
            return
        op.import_folder(dd)

    def push_undo(self):
        self.dirty = True
        self.update_title()
        self.undo_mgr.push(self.project.to_dict())

    def _bor_active(self):
        """当前标签页是不是 OpenBOR 动画编辑。"""
        return self.tabs.currentWidget() is self.openbor_panel

    def undo(self):
        """Ctrl+Z：按当前标签页分发（文本编辑模式让位给编辑器自带撤销）。"""
        lp = getattr(self, "level_panel", None)
        if lp is not None and self.tabs.currentWidget() is lp:
            lp.undo()
            return
        if self._bor_active():
            p = self.openbor_panel
            if p._mode == p.MODE_TEXT:
                p.txt_edit.undo()
            else:
                p.undo_edit()
            return
        st = self.undo_mgr.undo(self.project.to_dict())
        if st is None:
            self.statusBar().showMessage("没有可撤销的操作", 2000)
            return
        self._restore(st)

    def redo(self):
        """Ctrl+Shift+Z / Ctrl+Y：同样按当前标签页分发。"""
        lp = getattr(self, "level_panel", None)
        if lp is not None and self.tabs.currentWidget() is lp:
            lp.redo()
            return
        if self._bor_active():
            p = self.openbor_panel
            if p._mode == p.MODE_TEXT:
                p.txt_edit.redo()
            else:
                p.redo_edit()
            return
        st = self.undo_mgr.redo(self.project.to_dict())
        if st is None:
            self.statusBar().showMessage("没有可重做的操作", 2000)
            return
        self._restore(st)

    def _restore(self, state, keep_view=True):
        c = self.canvas_panel.canvas
        w = self.timeline_panel.widget
        view = (c.scale, QPointF(c.pan), w.cell_w, w.track_h)
        mutes = [t.muted for t in self.project.tracks]  # 静音不可回溯：保留现状
        self.project = Project.from_dict(state)
        for i, t in enumerate(self.project.tracks):
            if i < len(mutes):
                t.muted = mutes[i]
        self.cache.clear()
        self.selection.clear()
        self.primary = self.anchor = None
        self.browse_idx = None
        self.palette_highlight = None
        self.color_isolation = False
        self.stroke = None
        self.timeline_panel.widget.stop()
        self.apply_ui_state()
        if keep_view:
            c.scale, c.pan, w.cell_w, w.track_h = view
            self.canvas_panel.sync_slider()
        self.project_panel.set_roots(self.project.folder_roots)
        self.refresh_all()

    # ================= 新建 / 保存 / 导入工程 =================
    def new_project(self):
        if self.dirty:
            box = QMessageBox(self)
            box.setWindowTitle("新建工程")
            box.setText("当前工程有未保存的修改。")
            box.setInformativeText("是否保存这些修改？")
            b_save = box.addButton("保存修改", QMessageBox.AcceptRole)
            b_dis = box.addButton("放弃修改", QMessageBox.DestructiveRole)
            b_can = box.addButton("取消", QMessageBox.RejectRole)
            box.setDefaultButton(b_save)
            box.exec_()
            c = box.clickedButton()
            if c == b_can:
                return
            if c == b_save:
                self.save_project()
                if self.dirty:
                    return
        self._reset_project()

    def _reset_project(self):
        self.project = Project()
        self.cache.clear()
        self.undo_mgr.clear()
        self.selection.clear()
        self.primary = self.anchor = None
        self.browse_idx = None
        self.palette_highlight = None
        self.color_isolation = False
        self.stroke = None
        self.dirty = False
        w = self.timeline_panel.widget
        w.stop()
        w.scroll_x = 0
        w.scroll_y = 0
        self.project_panel.set_roots([])
        self.apply_ui_state()
        self.refresh_all()
        self.update_title()
        self.statusBar().showMessage("已新建工程", 2000)

    def collect_ui_state(self):
        c = self.canvas_panel.canvas
        w = self.timeline_panel.widget
        self.project.ui = {"scale": c.scale, "pan": [c.pan.x(), c.pan.y()],
                           "cell_w": w.cell_w, "track_h": w.track_h}

    def apply_ui_state(self):
        ui = self.project.ui or {}
        c = self.canvas_panel.canvas
        c.scale = ui.get("scale", 1.0)
        c.pan = QPointF(*ui.get("pan", [0.0, 0.0]))
        w = self.timeline_panel.widget
        w.cell_w = ui.get("cell_w", 46)
        w.track_h = ui.get("track_h", 52)
        self.canvas_panel.sync_slider()
        self.sync_align_buttons()

    def on_save_hotkey(self):
        """Ctrl+S：在 OpenBOR 标签页时保存角色 TXT，否则保存工程。"""
        if self.tabs.currentWidget() is self.openbor_panel:
            self.openbor_panel.save_hotkey()
        else:
            self.save_project()

    def save_project(self):
        path = self.project.file_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "保存工程", "", f"格斗工程文件 (*{EXT})")
        if not path:
            return
        if not path.endswith(EXT):
            path += EXT
        base = os.path.dirname(os.path.abspath(path))
        mig = os.path.join(base, os.path.splitext(os.path.basename(path))[0] + "_temp")
        mapping = {}
        for t in self.project.tracks:
            for f in t.frames:
                if f and f.source_path and self.is_temp(f.source_path):
                    if f.source_path not in mapping:
                        os.makedirs(mig, exist_ok=True)
                        np = os.path.join(mig, os.path.basename(f.source_path))
                        if not os.path.exists(np):
                            shutil.copy2(f.source_path, np)
                        mapping[f.source_path] = np
                    f.source_path = mapping[f.source_path]
        for rec in self.project.registry.values():
            if rec.get("conv") in mapping:
                rec["conv"] = mapping[rec["conv"]]
        self.collect_ui_state()
        self.project.file_path = path
        raw = json.dumps(self.project.to_dict(base=base)).encode("utf-8")
        with open(path, "wb") as fp:
            fp.write(MAGIC + zlib.compress(raw, 9))
        self.dirty = False
        self.update_title()
        self.flush_trash()
        self.statusBar().showMessage(f"工程已保存: {path}", 4000)

    def load_project_dialog(self):
        if self.dirty:
            box = QMessageBox(self)
            box.setWindowTitle("导入工程")
            box.setText("当前工程有未保存的修改。")
            box.setInformativeText("是否先保存这些修改？")
            b_save = box.addButton("保存修改", QMessageBox.AcceptRole)
            b_dis = box.addButton("放弃修改", QMessageBox.DestructiveRole)
            b_can = box.addButton("取消", QMessageBox.RejectRole)
            box.exec_()
            c = box.clickedButton()
            if c == b_can:
                return
            if c == b_save:
                self.save_project()
                if self.dirty:
                    return
        path, _ = QFileDialog.getOpenFileName(self, "导入工程", "",
                                              f"格斗工程文件 (*{EXT})")
        if path:
            self.load_project(path)

    def load_project(self, path):
        try:
            with open(path, "rb") as fp:
                data = fp.read()
            if not data.startswith(MAGIC):
                raise ValueError("不是有效的工程文件")
            d = json.loads(zlib.decompress(data[len(MAGIC):]).decode("utf-8"))
        except Exception as ex:
            QMessageBox.warning(self, "导入工程", f"读取失败: {ex}")
            return
        self.project = Project.from_dict(d, base=os.path.dirname(os.path.abspath(path)))
        self.project.file_path = path
        self.cache.clear()
        self.undo_mgr.clear()
        self.selection.clear()
        self.primary = self.anchor = None
        self.browse_idx = None
        self.palette_highlight = None
        self.color_isolation = False
        self.stroke = None
        self.dirty = False
        self.timeline_panel.widget.stop()
        self.apply_ui_state()
        self.project_panel.set_roots(self.project.folder_roots)
        self.refresh_all()
        self.update_title()
        self.statusBar().showMessage(f"工程已导入: {path}", 4000)

    # ================= 导入图片 / 文件夹 =================
    def import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入图片", "",
            "图片文件 (*.png *.gif *.bmp *.tga *.webp *.jpg *.jpeg *.tif *.tiff *.ico);;所有文件 (*)")
        if paths:
            self.import_images(paths)

    def open_folder(self):
        d = QFileDialog.getExistingDirectory(self, "打开文件夹")
        if not d:
            return
        ap = os.path.abspath(d)
        if ap in self.project.folder_roots:
            self.statusBar().showMessage("该文件夹已在列表中", 2000)
            return
        self.push_undo()
        self.project.folder_roots.append(ap)
        self.project_panel.add_root(ap)

    def remove_folder_root(self, path):
        if path in self.project.folder_roots:
            self.push_undo()
            self.project.folder_roots.remove(path)

    def import_images(self, paths, track_idx=None, start_idx=None):
        paths = [p for p in paths if iu.is_image(p)]
        if not paths:
            return
        self.push_undo()
        prj = self.project
        ti = track_idx if track_idx is not None else prj.current_track
        while ti >= len(prj.tracks):
            prj.tracks.append(Track(f"轨道{len(prj.tracks) + 1}"))
        frames, bad = [], []
        for path in paths:
            ap = os.path.abspath(path)
            rec = prj.registry.get(ap)
            if rec:
                src = rec.get("conv")
                if src and os.path.exists(src):
                    frames.append(FrameData(src, rec["pal"], iu.image_size(src)))
                    continue
                try:
                    im = iu.load(ap)
                except Exception:
                    continue
                if iu.palette_colors(im) is not None:
                    frames.append(FrameData(ap, rec["pal"], im.size))
                else:
                    pal = prj.find_palette(rec["pal"])
                    q = iu.map_to_palette(im, pal.colors, pal.transparent_index)
                    tp = self.temp_store.path("conv")
                    q.save(tp)
                    rec["conv"] = tp
                    frames.append(FrameData(tp, rec["pal"], im.size))
                continue
            try:
                im = iu.load(ap)
            except Exception as ex:
                self.statusBar().showMessage(f"无法读取 {os.path.basename(ap)}: {ex}", 4000)
                continue
            info = iu.palette_colors(im)
            if info is None:
                bad.append((ap, im.size))
            else:
                colors, tidx = info
                pal, _ = prj.add_palette(colors, tidx)
                prj.registry[ap] = {"pal": pal.id, "conv": None}
                frames.append(FrameData(ap, pal.id, im.size))
        if bad:
            from sprite_ui import NonIndexedDialog, PaletteAssignDialog
            dlg = NonIndexedDialog(self, len(paths), len(bad))
            dlg.exec_()
            if dlg.choice == NonIndexedDialog.INDEPENDENT:
                for ap, size in bad:
                    q = iu.quantize_to_indexed(iu.load(ap))
                    colors, tidx = iu.palette_colors(q)
                    pal, _ = prj.add_palette(colors, tidx)
                    tp = self.temp_store.path("conv")
                    q.save(tp)
                    prj.registry[ap] = {"pal": pal.id, "conv": tp}
                    frames.append(FrameData(tp, pal.id, size))
            elif dlg.choice == NonIndexedDialog.ASSIGN:
                dlg2 = PaletteAssignDialog(self, self, [p for p, _ in bad])
                dlg2.exec_()
                sizes = dict(bad)
                for ap, (tp, pid) in dlg2.results.items():
                    prj.registry[ap] = {"pal": pid, "conv": tp}
                    frames.append(FrameData(tp, pid, sizes[ap]))
        if frames:
            track = prj.tracks[ti]
            idx = start_idx if start_idx is not None else len(track.frames)
            self.place_frames(track, frames, idx)
            prj.current_track = ti
            self.timeline_panel.widget.set_playhead(idx)
        self.refresh_all()

    @staticmethod
    def place_frames(track, frames, idx):
        idx = max(0, idx)
        while len(track.frames) < idx:
            track.frames.append(None)
        single = len(frames) == 1
        for f in frames:
            if idx < len(track.frames) and track.frames[idx] is None and single:
                track.frames[idx] = f
            elif idx >= len(track.frames):
                track.frames.append(f)
            else:
                track.frames.insert(idx, f)
            idx += 1

    def move_frames(self, sel, target_ti, idx):
        """整体移动选区：以被拖帧（primary）为基准平移，保持相对间距。"""
        prj = self.project
        sel = {(t, f) for t, f in sel
               if t < len(prj.tracks) and prj.tracks[t].frame_at(f) is not None}
        if not sel:
            return
        if self.primary in sel:
            pti, pfi = self.primary
        else:
            pti, pfi = min(sel)
        hd = idx - pfi
        vd = target_ti - pti
        min_fi = min(fi for _, fi in sel)
        min_ti = min(ti for ti, _ in sel)
        hd = max(hd, -min_fi)
        vd = max(vd, -min_ti)
        max_ti = max(ti for ti, _ in sel)
        self.push_undo()
        while max_ti + vd >= len(prj.tracks):
            prj.tracks.append(Track(f"轨道{len(prj.tracks) + 1}"))
        moved = {}
        for ti, fi in sorted(sel):
            moved[(ti, fi)] = prj.tracks[ti].frames[fi]
            prj.tracks[ti].frames[fi] = None
        new_sel = set()
        for (ti, fi), f in sorted(moved.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            nt, nf = ti + vd, fi + hd
            track = prj.tracks[nt]
            while len(track.frames) <= nf:
                track.frames.append(None)
            if track.frames[nf] is None:
                track.frames[nf] = f
            else:
                track.frames.insert(nf, f)
            new_sel.add((nt, nf))
        prj.current_track = min(target_ti, len(prj.tracks) - 1)
        self.set_selection(new_sel, primary=(pti + vd, pfi + hd))
        self.refresh_all()

    # ================= 轨道 / 帧操作 =================
    def add_track(self):
        self.push_undo()
        self.project.tracks.append(Track(f"轨道{len(self.project.tracks) + 1}"))
        self.refresh_all()

    def delete_track(self, ti):
        prj = self.project
        if len(prj.tracks) <= 1:
            QMessageBox.information(self, "删除轨道", "至少保留一条轨道。")
            return
        t = prj.tracks[ti]
        if any(f is not None for f in t.frames):
            r = QMessageBox.question(self, "删除轨道",
                                     f"轨道“{t.name}”中包含帧，确定删除？")
            if r != QMessageBox.Yes:
                return
        self.push_undo()
        prj.tracks.pop(ti)
        self.selection = {(t2 - 1 if t2 > ti else t2, f) for t2, f in self.selection
                          if t2 != ti}
        self.primary = self.anchor = None
        prj.current_track = min(prj.current_track, len(prj.tracks) - 1)
        self.refresh_all()

    def toggle_mute(self, ti):
        # 静音不是可回溯操作（不入撤销栈），但仍属于未保存修改
        t = self.project.tracks[ti]
        t.muted = not t.muted
        self.dirty = True
        self.update_title()
        self.refresh_all()

    def delete_selected(self):
        if not self.selection:
            return
        self.push_undo()
        for ti, fi in self.selection:
            t = self.project.tracks[ti]
            if 0 <= fi < len(t.frames):
                t.frames[fi] = None
        self.set_selection(set())
        self.refresh_all()

    def clear_selected(self):
        targets = [self.project.tracks[ti].frame_at(fi)
                   for ti, fi in self.selection]
        targets = [f for f in targets if f is not None]
        if not targets:
            return
        self.push_undo()
        for f in targets:
            f.cleared = True
        self.refresh_all()

    def insert_empty_after(self, ti, fi):
        t = self.project.tracks[ti]
        ref = t.frame_at(fi)
        size = ref.display_size() if ref else (64, 64)
        self.push_undo()
        while len(t.frames) <= fi:
            t.frames.append(None)
        t.frames.insert(fi + 1, FrameData(is_empty=True, orig_size=size))
        self.refresh_all()

    def insert_empty_after_primary(self):
        pf = self.primary_frame()
        if pf:
            self.insert_empty_after(*pf)

    def duplicate_after(self, ti, fi):
        t = self.project.tracks[ti]
        ref = t.frame_at(fi)
        if ref is None:
            return
        self.push_undo()
        t.frames.insert(fi + 1, ref.clone())
        self.refresh_all()

    def duplicate_primary(self):
        pf = self.primary_frame()
        if pf:
            self.duplicate_after(*pf)

    def insert_empty_at(self, ti, fi):
        self.push_undo()
        self.place_frames(self.project.tracks[ti],
                          [FrameData(is_empty=True, orig_size=(64, 64))], fi)
        self.refresh_all()

    def insert_empty_at_playhead(self):
        prj = self.project
        ref = prj.tracks[prj.current_track].frame_at(prj.playhead)
        size = ref.display_size() if ref else (64, 64)
        self.push_undo()
        self.place_frames(prj.tracks[prj.current_track],
                          [FrameData(is_empty=True, orig_size=size)], prj.playhead)
        self.refresh_all()

    def remove_gaps(self, ti):
        prj = self.project
        if not (0 <= ti < len(prj.tracks)):
            return
        t = prj.tracks[ti]
        if all(f is not None for f in t.frames):
            self.statusBar().showMessage("当前轨道没有空位", 2000)
            return
        self.push_undo()
        t.frames = [f for f in t.frames if f is not None]
        self.set_selection(set())
        self.refresh_all()

    # ================= 色表浏览 / 颜色选择 =================
    def displayed_palette(self):
        prj = self.project
        if not prj.palettes:
            self.browse_idx = None
            return None
        if self.browse_idx is None or not (0 <= self.browse_idx < len(prj.palettes)):
            self.browse_idx = min(self.browse_idx or 0, len(prj.palettes) - 1)
        return prj.palettes[self.browse_idx]

    def browse_palette(self, i):
        prj = self.project
        if 0 <= i < len(prj.palettes):
            self.browse_idx = i
            self.palette_highlight = prj.palettes[i].id
            self.refresh_all()

    def select_frames_by_palette(self):
        pal = self.displayed_palette()
        if pal is None:
            return
        sel = {(ti, fi) for ti, t in enumerate(self.project.tracks)
               for fi, f in enumerate(t.frames)
               if f is not None and f.palette_id == pal.id}
        if not sel:
            self.statusBar().showMessage("没有帧使用该颜色表", 2000)
            return
        self.set_selection(sel, primary=sorted(sel)[-1])
        self.timeline_panel.widget.setFocus()

    def on_grid_color_selected(self, idx):
        """色块选择变化：有主色块则开启画布颜色隔离，否则关闭。"""
        self.color_isolation = idx is not None
        self.canvas_panel.canvas.update()
        self.tool_panel._update_info()

    def edit_palette_color(self, idx):
        pal = self.displayed_palette()
        if pal is None:
            return
        old = pal.colors[idx]
        c = QColorDialog.getColor(QColor(*old), self, f"修改颜色 #{idx}")
        if c.isValid():
            self.push_undo()
            pal.colors[idx] = (c.red(), c.green(), c.blue())
            pal.version += 1
            self.refresh_all()

    def set_transparent_color(self):
        pal = self.displayed_palette()
        idx = self.tool_panel.grid.selected_index
        if pal is None or idx is None:
            QMessageBox.information(self, "设置为透明色",
                                    "请先在色表中单击选择一个色块。")
            return
        self.push_undo()
        pal.transparent_index = idx        # 自动取代之前的透明色
        pal.version += 1
        self.refresh_all()
        self.statusBar().showMessage(f"已将 {pal.name} 的 #{idx} 设为透明色", 3000)

    # ================= 配色表 新建 / .pal 导入导出 / 色块复制粘贴 =================
    def new_palette(self):
        prj = self.project
        name, ok = QInputDialog.getText(
            self, "新建配色表", "配色表名称:",
            text=f"颜色表{len(prj.palettes) + 1}")
        if not ok:
            return
        self.push_undo()
        # 直接创建（跳过内容去重，允许建立多张空白表再分别粘贴）
        pal = Palette([(0, 0, 0)] * 256, name.strip() or
                      f"颜色表{len(prj.palettes) + 1}", None)
        prj.palettes.append(pal)
        self.browse_idx = len(prj.palettes) - 1
        self.refresh_all()
        self.statusBar().showMessage(f"已新建 {pal.name}", 3000)

    def import_palette(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入配色", "", "配色文件 (*.pal);;所有文件 (*)")
        if not path:
            return
        try:
            colors = iu.load_pal_file(path)
        except Exception as ex:
            QMessageBox.warning(self, "导入配色", f"读取失败: {ex}")
            return
        self.push_undo()
        name = os.path.splitext(os.path.basename(path))[0]
        pal, created = self.project.add_palette(colors, None, name)
        self.browse_idx = self.project.palettes.index(pal)
        self.refresh_all()
        msg = f"已导入配色 {os.path.basename(path)}"
        if not created:
            msg += "（内容与现有色表相同，已复用）"
        self.statusBar().showMessage(msg, 4000)

    def export_palette(self):
        pal = self.displayed_palette()
        if pal is None:
            QMessageBox.information(self, "导出配色", "当前没有颜色表。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配色", pal.name + ".pal", "配色文件 (*.pal)")
        if not path:
            return
        if not path.lower().endswith(".pal"):
            path += ".pal"
        try:
            iu.save_pal_file(pal.colors, path)
        except Exception as ex:
            QMessageBox.warning(self, "导出配色", f"写入失败: {ex}")
            return
        self.statusBar().showMessage(f"已导出配色: {path}", 4000)

    def copy_swatches(self):
        pal = self.displayed_palette()
        sel = self.tool_panel.grid.sel
        if pal is None or not sel:
            self.statusBar().showMessage("没有选中的色块可复制", 2500)
            return
        # 只按选择顺序记录颜色值，不记录间隔
        self._swatch_clip = [pal.colors[i] for i in sel]
        self.statusBar().showMessage(f"已复制 {len(sel)} 个色块的颜色", 2500)

    def paste_swatches(self):
        pal = self.displayed_palette()
        if pal is None or not self._swatch_clip:
            self.statusBar().showMessage("剪贴板中没有色块颜色", 2500)
            return
        start = self.tool_panel.grid.selected_index
        if start is None:
            self.statusBar().showMessage("请先在目标色表上点击一个起始色块", 3000)
            return
        self.push_undo()
        n = 0
        for i, c in enumerate(self._swatch_clip):
            if start + i >= 256:
                break
            pal.colors[start + i] = tuple(c)
            n += 1
        pal.version += 1
        self.refresh_all()
        self.statusBar().showMessage(
            f"已把 {n} 个颜色粘贴到 {pal.name} 的 #{start} 起", 4000)

    # ================= 吸色 =================
    def set_picker_mode(self, on):
        self.picker_mode = bool(on)
        b = self.canvas_panel.canvas.btn_pick
        b.blockSignals(True)
        b.setChecked(self.picker_mode)
        b.blockSignals(False)
        self.canvas_panel.canvas.update_cursor()
        if self.picker_mode:
            self.statusBar().showMessage("吸色模式：在画布上点击一个像素", 3000)

    def apply_picked_color(self, rgb):
        pal = self.displayed_palette()
        if pal is None:
            self.statusBar().showMessage("工程中还没有颜色表", 2500)
            return
        colors = pal.colors
        if rgb in colors:
            idx = colors.index(rgb)
        else:   # 就近匹配
            idx = min(range(256),
                      key=lambda i: sum((colors[i][k] - rgb[k]) ** 2 for k in range(3)))
        self.tool_panel.grid.selected_index = idx
        self.color_isolation = True
        self.refresh_all()
        self.statusBar().showMessage(f"已吸取颜色 {rgb} → {pal.name} 色块 #{idx}", 4000)

    # ================= 合并色表 =================
    def merge_palettes(self):
        prj = self.project
        pals = []
        for ti, fi in sorted(self.selection):
            f = prj.tracks[ti].frame_at(fi)
            if f and f.palette_id:
                p = prj.find_palette(f.palette_id)
                if p and p not in pals:
                    pals.append(p)
        if len(pals) < 2:
            QMessageBox.information(self, "合并色表",
                                    "选中的帧没有使用两个及以上不同的色表。")
            return
        old_ids = {p.id for p in pals}
        used = {p.id: set() for p in pals}
        for t in prj.tracks:
            for f in t.frames:
                if f is None or f.is_empty or f.cleared or not f.source_path:
                    continue
                if f.palette_id not in used:
                    continue
                try:
                    im = iu.load(f.source_path)
                except Exception:
                    continue
                if im.mode == "P":
                    used[f.palette_id].update(im.getdata())
        colors, seen = [], set()
        for p in pals:
            for idx in sorted(used[p.id]):
                c = p.colors[idx]
                if c not in seen:
                    seen.add(c)
                    colors.append(c)
        if len(colors) > 256:
            r = QMessageBox.warning(
                self, "色表溢出",
                f"合并后的颜色数为 {len(colors)}，超过索引图最多 256 色的限制。\n"
                "继续将把颜色压缩到 256 色（可能损失部分色彩）。是否继续？",
                QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Cancel)
            if r != QMessageBox.Ok:
                return
            from PIL import Image
            ci = Image.new("RGB", (len(colors), 1))
            ci.putdata(colors)
            colors = iu.palette_colors(ci.quantize(colors=256))[0]
        self.push_undo()
        tidx = None
        for p in pals:
            if p.transparent_index is not None:
                c = p.colors[p.transparent_index]
                if c in colors:
                    tidx = colors.index(c)
                break
        new_pal, _ = prj.add_palette(colors, tidx)
        for t in prj.tracks:
            for f in t.frames:
                if f is None or f.palette_id not in old_ids:
                    continue
                if f.is_empty or f.cleared or not f.source_path:
                    f.palette_id = new_pal.id
                    continue
                old = prj.find_palette(f.palette_id)
                old_src = f.source_path
                try:
                    im = iu.load(old_src)
                except Exception:
                    f.palette_id = new_pal.id
                    continue
                if im.mode == "P":
                    im = im.copy()
                    iu.apply_palette(im, old)
                rgba = im.convert("RGBA")
                q = iu.map_to_palette(rgba, new_pal.colors, new_pal.transparent_index)
                tp = self.temp_store.path("merge")
                q.save(tp)
                f.source_path = tp
                f.palette_id = new_pal.id
                for key, rec in prj.registry.items():
                    if rec["pal"] in old_ids and (key == old_src or rec.get("conv") == old_src):
                        rec["conv"] = tp
        for rec in prj.registry.values():
            if rec["pal"] in old_ids:
                rec["pal"] = new_pal.id
        prj.palettes = [p for p in prj.palettes
                        if p.id not in old_ids or p.id == new_pal.id]
        if new_pal in prj.palettes:
            self.browse_idx = prj.palettes.index(new_pal)
        self.refresh_all()
        self.statusBar().showMessage(f"已合并为 {new_pal.name}（{len(colors)} 色）", 4000)

    # ================= 铅笔工具 =================
    def set_pencil_mode(self, on):
        self.pencil_mode = bool(on)
        self.canvas_panel.canvas.update_cursor()
        self.refresh_all()
        if self.pencil_mode:
            self.statusBar().showMessage(
                "铅笔模式：先在色表中单击选色，再在画布上拖动涂画；Alt+C 切回鼠标", 5000)

    def toggle_tool(self):
        self.set_pencil_mode(not self.pencil_mode)

    def begin_stroke(self, frame):
        idx = self.tool_panel.grid.selected_index
        if idx is None:
            self.statusBar().showMessage("请先在右侧色表中单击选择一个色块", 3000)
            return False
        if frame.is_empty or frame.source_path is None:
            self.statusBar().showMessage("空帧无法涂画", 3000)
            return False
        if frame.cleared:
            self.statusBar().showMessage("该帧已被清空，无法涂画", 3000)
            return False
        try:
            im = iu.load(frame.source_path)
        except Exception:
            return False
        if im.mode != "P":
            pal = self.project.find_palette(frame.palette_id)
            im = (iu.map_to_palette(im, pal.colors, pal.transparent_index)
                  if pal else iu.quantize_to_indexed(im))
        self.push_undo()
        tp = self.temp_store.path("paint")
        im.save(tp)
        frame.source_path = tp
        self.stroke = {"im": im, "path": tp, "idx": idx, "last": None}
        self.cache.invalidate(tp)
        return True

    def paint_to(self, frame, x, y):
        st = self.stroke
        if st is None:
            return
        im = st["im"]
        x0, y0 = st["last"] if st["last"] else (x, y)
        for px, py in iu.bresenham(x0, y0, x, y):
            if 0 <= px < im.width and 0 <= py < im.height:
                im.putpixel((px, py), st["idx"])
        st["last"] = (x, y)
        im.save(st["path"])
        self.cache.invalidate(st["path"])
        self.refresh_canvases()

    def end_stroke(self):
        if self.stroke:
            self.stroke = None
            self.refresh_all()

    # ================= 导出索引图 =================
    def is_temp(self, path):
        return bool(path) and os.path.abspath(path).startswith(
            os.path.abspath(self.temp_store.dir))

    def suggest_name(self, f):
        src = f.source_path
        if src:
            for key, rec in self.project.registry.items():
                if key == src or rec.get("conv") == src:
                    return os.path.splitext(os.path.basename(key))[0] + ".png"
            return os.path.splitext(os.path.basename(src))[0] + ".png"
        return "empty_frame.png"

    def export_selected(self):
        prj = self.project
        frames, seen = [], set()
        for ti, fi in sorted(self.selection):
            f = prj.tracks[ti].frame_at(fi)
            if f is not None and id(f) not in seen:
                seen.add(id(f))
                frames.append(f)
        if not frames:
            QMessageBox.information(self, "导出", "请先在轨道中选中要导出的帧。")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not out_dir:
            return
        self.push_undo()
        n = 0
        for f in frames:
            pal = prj.find_palette(f.palette_id) if f.palette_id else None
            out = os.path.join(out_dir, self.suggest_name(f))
            base, _ = os.path.splitext(out)
            k = 1
            while os.path.exists(out):
                out = f"{base}_{k}.png"
                k += 1
            iu.export_frame_image(f, pal, out)
            old_src = f.source_path
            if old_src and old_src != out and self.is_temp(old_src):
                self._trash.append(old_src)
            f.source_path = out
            f.crop = None
            f.cleared = False
            f.is_empty = False
            f.orig_size = iu.image_size(out)
            prj.registry[os.path.abspath(out)] = {"pal": f.palette_id, "conv": None}
            n += 1
        self.refresh_all()
        self.statusBar().showMessage(f"已导出 {n} 张索引图到 {out_dir}", 5000)

    def flush_trash(self):
        referenced = {f.source_path for t in self.project.tracks
                      for f in t.frames if f and f.source_path}
        for p in self._trash:
            if p not in referenced:
                try:
                    os.remove(p)
                except OSError:
                    pass
        self._trash.clear()

    def closeEvent(self, e):
        self.timeline_panel.widget.stop()
        self.flush_trash()
        super().closeEvent(e)


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    w = MainWindow()
    app.installEventFilter(w)   # 全局“空白点击”判定（取消颜色选择）
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()