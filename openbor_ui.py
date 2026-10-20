# -*- coding: utf-8 -*-
"""Mo改OpenBOR编辑器 —— OpenBOR 人物动画编辑面板。

布局：
  左列（窄）—— 导入 DATA 文件夹、实体列表（错误标红）、字典序 / 深度检错
  中列（窄）—— 实体名、模式切换（动画 / 文本 / 对照）、动画列表、Header、问题
  右列     —— 画布 + 设置区 + 时间轴（文本模式=编辑器，对照模式=左右分栏）

已拆分的模块（本文件只放面板逻辑）：
  bor_style     样式常量            bor_widgets  通用控件与布局
  bor_text      高亮 / 一键优化     bor_canvas   画布
  bor_timeline  时间轴              bor_undo     撤销 / 重做
"""
import difflib
import os
import re
from html import escape as _esc

from PyQt5.QtCore import QRect, Qt, QTimer, QUrl
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPalette
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                             QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QPlainTextEdit,
                             QProgressBar, QPushButton, QScrollArea,
                             QSplitter, QStyle,
                             QStyledItemDelegate, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget, QTextEdit)

try:                                  # 帧音效播放（可选依赖）
    from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer, QSound
except Exception:                     # pragma: no cover
    QSound = None
    QMediaPlayer = None

from core import iu
from bor_view import BorCanvas
from bor_data import (MotionTrack, anim_has_attack, spawn_frames,
                        spawn_index)
from core import (ERR_BG, ERR_FG, FLASH_ADD, FLASH_DEL, FLASH_MOD,
                  MODE_BTN_QSS, MORE_BTN_QSS, QSS,
                  WARN_BG, WARN_FG, YELLOW_BG)
from bor_view import (BorHighlighter, BorTextEdit, format_bor_text,
                      SPECIAL_ATTACK_TYPES)
from bor_view import BorTimeline
from core import MOD, BorUndo, State
from bor_view import (CmdField, ColorSwatch, NumEdit, SafeComboBox,
                      _icon_btn, _lbl, _load_settings, _mk_spin,
                      _save_settings, _section, _styled_group, _text_width,
                      _to_int, blocked, refresh_begin, refresh_end)
from bor_data import (ERR, HEADER_CMDS, HEADER_MULTI_CMDS, WARN, Anim,
                     EntityDoc, Line, ModelsTxt, bor_dm, canon_line,
                     deep_validate, dm_sub_cmd, is_script_end,
                     is_script_start, resolve_frame_path, validate_entity)

# 特殊攻击类型（替代 attack{#} 的独立命令）
# ------------------------------------------------------------------
# 字段规格写法（每个字段在下面都是一行：名字 + 规格 + 放在哪一行）
#   ("bi",)                 勾选框（不勾 = 删除该命令行）
#   ("int", lo, hi)         单个整数
#   ("ints", n, lo, hi)     n 个整数
#   ("combo", [选项])       下拉菜单
#   ("path", "wav"/"png")   文件路径（"…" 按钮选文件）
#   ("name",)               实体名 / 动画名文本
#   ("cancel",)             cancel 专用：3 整数 + 输入序列 + 1 整数
#   ("energy",)             energycost 专用：整数 + 0/1/2 + 0/1/2
#   ("grabin",)             grabin 专用：0/1 下拉 + 距离整数
#
# 想改排版：直接把某一行剪切、粘贴到别的 _row(...) 里即可，
# 也可以整行注释掉让字段消失，或整行复制一份换个命令名。
# ------------------------------------------------------------------


def _sev_html(sev, msg):
    """错误信息：红色加粗；警告：琥珀色。"""
    if sev == ERR:
        return ('<div style="color:#ff0000;font-weight:bold">❌ %s</div>'
                % _esc(msg))
    return '<div style="color:#c07a1a">⚠️ %s</div>' % _esc(msg)


class AnimItemDelegate(QStyledItemDelegate):
    """动画列表项：名称后面追加 Times New Roman 标记。

    A —— 红色加粗：该动画里有攻击框
    f —— 斜体：该动画里有 @script / @cmd 脚本设置
    """

    MARK_W = 11

    def paint(self, p, opt, idx):
        name = idx.data(Qt.DisplayRole) or ""
        marks = idx.data(Qt.UserRole) or ""
        sel = bool(opt.state & QStyle.State_Selected)
        if sel:
            p.fillRect(opt.rect, opt.palette.color(QPalette.Highlight))
        else:
            br = idx.data(Qt.BackgroundRole)
            if isinstance(br, QBrush) and br.style() != Qt.NoBrush:
                p.fillRect(opt.rect, br)
            elif idx.row() % 2:
                p.fillRect(opt.rect, opt.palette.color(QPalette.AlternateBase))
        if sel:
            fg = opt.palette.color(QPalette.HighlightedText)
        else:
            fb = idx.data(Qt.ForegroundRole)
            fg = fb.color() if isinstance(fb, QBrush) \
                else opt.palette.color(QPalette.Text)
        r = opt.rect.adjusted(4, 0, -4, 0)
        p.setFont(opt.font)
        fm = QFontMetrics(opt.font)
        w = _text_width(fm, name) + 4
        p.setPen(fg)
        p.drawText(QRect(r.left(), r.top(), w, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, name)
        x = r.left() + w + 4
        tf = QFont("Times New Roman", opt.font.pointSize() or 10)
        for ch in marks:
            if x + self.MARK_W > r.right():
                break
            if ch == "A":
                tf.setBold(True)
                tf.setItalic(False)
                p.setPen(QColor(255, 0, 0))
            else:
                tf.setBold(False)
                tf.setItalic(True)
                p.setPen(fg)
            p.setFont(tf)
            p.drawText(QRect(int(x), r.top(), self.MARK_W, r.height()),
                       Qt.AlignCenter, ch)
            x += self.MARK_W
        p.setFont(opt.font)


# ============================================================ 主面板
class OpenBorPanel(QWidget):
    # 三种编辑模式
    MODE_ANIM, MODE_TEXT, MODE_CMP = "anim", "text", "cmp"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data_dir = None
        self.root_dirs = []
        self.models = None
        self.entities = []
        self.doc = None
        self._ent_name = None
        self._cur_issues = []
        self.anim_idx = None
        self.frame_idx = 0
        self._saved_text = ""     # 上次装载 / 存盘时的文档文本（判“真改了吗”的基准）
        self._loading = False
        self._mode = self.MODE_ANIM          # 动画编辑 / 文本编辑 / 对照
        self._cmp_sizes = [620, 560]         # 对照模式左右两栏宽度
        self._undo = BorUndo()
        self._undo_base = None        # 上一次“封口”时的稳定状态
        self._undo_pre = None         # 本次操作的前态（封口时用）
        self._seal_timer = QTimer(self)
        self._seal_timer.setSingleShot(True)
        self._seal_timer.timeout.connect(self._undo_seal)
        self._txt_original = ""
        self._txt_line = -1          # 上次同步时光标所在行（换行才算改完一行）
        self._play_mode = None
        self._play_start = None       # 空格播放的起始帧（播完自动回位）
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._play_advance)
        # ---- 本地偏好 ----
        st = _load_settings()
        self._settings = st
        self.bbox_color = QColor(st.get("bbox_color", "#3c8cff"))
        self.attack_color = QColor(st.get("attack_color", "#ff4646"))
        self._muted = bool(st.get("muted", False))
        self._night = bool(st.get("night", False))
        cs = st.get("cmp_sizes")
        if isinstance(cs, list) and len(cs) == 2:
            self._cmp_sizes = cs
        self._align = st.get("align", "offset")
        self._show_dm = bool(st.get("dm", True))   # 画布是否显示 drawmethod
        self._dm_new = True          # drawmethod 写法：True=子命令（新）
                                     # False=一整行 10 个整数（老）；随帧自动检测
        self._last_dir = st.get("last_dir", "")
        self._ent_order = []          # 实体列表的显示顺序（存实体下标）
        self._sort_alpha = False      # 实体列表是否按字典序
        self._type_filter = ""        # 只看某一类实体（"" = 全部）
        self._ent_type = {}           # 实体下标 -> type 字段
        self._deep_done = False       # 本次导入是否已做过深度检错
        self._bbox_clip = None        # BBox 复制 / 粘贴剪贴板
        self._atk_clip = None         # Attack 复制 / 粘贴剪贴板
        self._importing = False       # 正在扫描（防止重入）
        # ---- 动画显示增强 ----
        self._sim_gravity = None      # 跳跃重力；None = 按 jumpframe 初速自动定
        self._sim_imgs = {}           # 生成物 / 假人用的图片缓存（路径 → QImage）
        self._ext_docs = {}           # 别的实体文档缓存（小写名 → EntityDoc）
        self._preview_win = None      # 打开的“攻击预览”窗口
        self.setObjectName("borRoot")
        self.setStyleSheet(QSS)
        self._build_ui()
        self._enter_mode(self.MODE_ANIM)     # 默认动画编辑模式
        self.canvas.set_align_mode(self._align)
        self.canvas.btn_mute.setChecked(self._muted)
        self.canvas._apply_mute_look()
        self.canvas.set_show_dm(self._show_dm)
        self._apply_night()
        # ---- 自动载入上次打开的 data 目录 ----
        if self._last_dir and os.path.isdir(self._last_dir) and \
                os.path.exists(os.path.join(self._last_dir, "models.txt")):
            QTimer.singleShot(0, lambda: self.import_folder(self._last_dir))

    def _save_pref(self, key, value):
        self._settings[key] = value
        _save_settings(self._settings)

    def _apply_night(self):
        """文本编辑区夜间模式。"""
        if getattr(self, "highlighter", None) is None:
            return
        self.txt_edit.setStyleSheet(
            "QTextEdit{background:#1e2127;color:#d7dae0;"
            "selection-background-color:#3a6ea5;}" if self._night else "")
        self.txt_edit.set_night(self._night)
        self.highlighter.set_night(self._night)
        self.btn_night.setText("日间模式" if self._night else "夜间模式")
        self.btn_night.setChecked(self._night)

    def _toggle_night(self):
        self._night = not self._night
        self._save_pref("night", self._night)
        self._apply_night()

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(0)
        sp = QSplitter(Qt.Horizontal)
        lay.addWidget(sp)

        # ---- 左列 ----
        left = QWidget()
        left.setMinimumWidth(170)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        lv.setSpacing(6)
        btn = QPushButton("导入data")
        btn.setObjectName("primaryBtn")
        btn.setToolTip("选择含 models.txt 的 DATA 目录")
        btn.clicked.connect(lambda: self.import_folder())
        lv.addWidget(btn)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        lv.addWidget(self.progress)
        lv.addWidget(_section("实体列表"))
        srow = QHBoxLayout()
        srow.setSpacing(4)
        self.btn_sort = QPushButton("字典序")
        self.btn_sort.setCheckable(True)
        self.btn_sort.setToolTip("按名排序；再按一次恢复文件顺序")
        self.btn_sort.clicked.connect(self._toggle_sort)
        self.btn_deep = QPushButton("深度检错")
        self.btn_deep.setToolTip("逐个实体检查图片/音频引用与 @cmd 函数（较慢）")
        self.btn_deep.clicked.connect(self.deep_check)
        self.btn_reload = QPushButton("刷新")
        self.btn_reload.setToolTip("重新载入当前 data 目录（重新扫描 + 重新检错）")
        self.btn_reload.clicked.connect(self.reload_folder)
        srow.addWidget(self.btn_sort)
        srow.addWidget(self.btn_reload)
        srow.addWidget(self.btn_deep)
        srow.addStretch(1)
        lv.addLayout(srow)
        # 只看某一类实体（player / enemy / npc / …）
        frow = QHBoxLayout()
        frow.setSpacing(4)
        frow.addWidget(_lbl("类别"))
        self.cmb_type = SafeComboBox()
        self.cmb_type.addItem("全部")
        self.cmb_type.setToolTip("只看指定 type 的实体（OpenBOR 的类型）")
        self.cmb_type.set_deferred(self._apply_type_filter)
        frow.addWidget(self.cmb_type, 1)
        lv.addLayout(frow)
        self.ent_list = QListWidget()
        self.ent_list.setAlternatingRowColors(True)
        self.ent_list.currentRowChanged.connect(self._on_entity_selected)
        lv.addWidget(self.ent_list, 1)
        self.lbl_scan = QLabel("")
        self.lbl_scan.setProperty("role", "dim")
        self.lbl_scan.setWordWrap(True)
        lv.addWidget(self.lbl_scan)
        sp.addWidget(left)

        # ---- 中列（窄） ----
        mid = QWidget()
        mid.setMinimumWidth(220)
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 6, 0)
        mv.setSpacing(6)
        erow = QHBoxLayout()
        erow.setSpacing(6)
        self.lbl_entity = QLabel("（未选择实体）")
        self.lbl_entity.setProperty("role", "title")
        self.lbl_entity.setTextFormat(Qt.RichText)
        erow.addWidget(self.lbl_entity, 1)
        mv.addLayout(erow)
        # 模式切换：三个互斥按钮（动画编辑 / 文本编辑 / 对照模式）
        mrow = QHBoxLayout()
        mrow.setSpacing(4)
        self.btn_mode_anim = QPushButton("动画编辑")
        self.btn_mode_text = QPushButton("文本编辑")
        self.btn_mode_cmp = QPushButton("对照模式")
        for b, tip in (
                (self.btn_mode_anim, "画布 + 设置区 + 时间轴"),
                (self.btn_mode_text, "直接编辑 TXT（Ctrl+滚轮缩放字号）"),
                (self.btn_mode_cmp,
                 "左右分栏，两侧互通：左边改动→右边跳行闪烁；"
                 "右边改完一行→左边跳到对应帧")):
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setStyleSheet(MODE_BTN_QSS)
            mrow.addWidget(b)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for i, b in enumerate((self.btn_mode_anim, self.btn_mode_text,
                               self.btn_mode_cmp)):
            self.mode_group.addButton(b, i)
        self.mode_group.buttonClicked[int].connect(self._on_mode_clicked)
        self.btn_mode_anim.setChecked(True)
        mv.addLayout(mrow)

        mv.addWidget(_section("动画列表"))
        self.anim_list = QListWidget()
        self.anim_list.setAlternatingRowColors(True)
        self.anim_list.setItemDelegate(AnimItemDelegate(self.anim_list))
        self.anim_list.currentRowChanged.connect(self._on_anim_selected)
        mv.addWidget(self.anim_list, 3)
        arow = QHBoxLayout()
        arow.setSpacing(6)
        b_add = QPushButton("添加")
        b_del = QPushButton("删除")
        b_add.setToolTip("添加动画")
        b_del.setToolTip("删除选中动画")
        b_add.clicked.connect(self.add_anim)
        b_del.clicked.connect(self.del_anim)
        arow.addWidget(b_add)
        arow.addWidget(b_del)
        arow.addStretch(1)
        mv.addLayout(arow)

        mv.addWidget(_section("Header"))
        self.header_tree = QTreeWidget()
        self.header_tree.setColumnCount(2)
        self.header_tree.setHeaderLabels(["字段", "值"])
        self.header_tree.setColumnWidth(0, 132)      # 字段列加宽
        self.header_tree.setColumnWidth(1, 90)       # 值列相应收窄
        self.header_tree.setRootIsDecorated(False)   # 字段列文字顶到最左
        self.header_tree.setAlternatingRowColors(True)
        self.header_tree.itemDoubleClicked.connect(
            lambda *_: self.edit_header_field())
        self.header_tree.itemSelectionChanged.connect(
            self._on_header_item_activated)
        mv.addWidget(self.header_tree, 3)
        hrow = QHBoxLayout()
        hrow.setSpacing(6)
        for text, fn in (("新建", self.add_header_field),
                         ("改值", self.edit_header_field),
                         ("删除", self.del_header_field)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            hrow.addWidget(b)
        hrow.addStretch(1)
        mv.addLayout(hrow)

        mv.addWidget(_section("问题"))
        self.issues = QTextEdit()          # 用富文本：错误红色加粗
        self.issues.setReadOnly(True)
        mv.addWidget(self.issues, 2)
        sp.addWidget(mid)

        # ---- 右列 ----
        # 动画编辑区：画布 + 设置区 + 时间轴（文本模式下整体隐藏）
        self.anim_area = QWidget()
        av = QVBoxLayout(self.anim_area)
        av.setContentsMargins(0, 0, 0, 0)
        av.setSpacing(6)
        self.canvas = BorCanvas(self)
        av.addWidget(self.canvas, 1)

        # 设置区：等宽分组卡片纵向排列，可滚动
        self.ctrl_scroll = QScrollArea()
        self.ctrl_scroll.setWidgetResizable(True)
        self.ctrl_scroll.setMaximumHeight(300)
        self.ctrl_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ctrl = QWidget()
        self.ctrl_scroll.setWidget(ctrl)
        cv = QVBoxLayout(ctrl)
        cv.setContentsMargins(2, 2, 2, 2)
        cv.setSpacing(8)
        cv.addWidget(self._build_anim_header_box())
        cv.addWidget(self._build_frame_box())
        cv.addWidget(self._build_attack_box())
        cv.addWidget(self._build_jump_box())     # 跳帧命令独占一行
        cv.addWidget(self._build_dm_box())       # DrawMethod 独占一行
        cv.addStretch(1)
        av.addWidget(self.ctrl_scroll)

        # 时间轴 + 帧操作
        self.tbar = QHBoxLayout()
        self.tbar.setSpacing(6)
        self.tbar.setContentsMargins(2, 0, 2, 0)
        self.tbar.addWidget(_section("帧"))
        for text, fn, tip in (
                ("增…", self.add_frame, "在本帧后插一帧（选图）"),
                ("删", self.del_frame, "删除本帧"),
                ("替换…", self.replace_frame, "换本帧图片"),
                ("复制", self.dup_frame, "复制本帧（只复制图片）")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            self.tbar.addWidget(b)
        self.tbar.addStretch(1)
        hint = QLabel("左键平移　右键画框　中键画点　"
                      "空格播放　←→切帧")
        hint.setProperty("role", "dim")
        self.tbar.addWidget(hint)
        av.addLayout(self.tbar)
        self.timeline = BorTimeline(self)
        av.addWidget(self.timeline)

        # 保存 / 重载
        self.srow = QHBoxLayout()
        self.srow.setSpacing(6)
        self.srow.setContentsMargins(2, 0, 2, 0)
        b_save = QPushButton("保存")
        b_save.setObjectName("primaryBtn")
        b_save.setToolTip("写回当前实体的 TXT")
        b_save.clicked.connect(self.save_entity)
        b_reload = QPushButton("重载")
        b_reload.setToolTip("放弃改动，重读 TXT")
        b_reload.clicked.connect(self.reload_entity)
        self.lbl_dirty = QLabel("")
        self.lbl_dirty.setProperty("role", "warnText")
        self.srow.addWidget(b_save)
        self.srow.addWidget(b_reload)
        self.srow.addStretch(1)
        self.srow.addWidget(self.lbl_dirty)
        av.addLayout(self.srow)

        # ---- 文本编辑区（初始隐藏） ----
        self.text_area = QWidget()
        tv = QVBoxLayout(self.text_area)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(6)
        self.txt_edit = BorTextEdit()
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(10)
        self.txt_edit.setFont(f)
        self.txt_edit.setLineWrapMode(QTextEdit.NoWrap)
        self.highlighter = BorHighlighter(self.txt_edit.document())
        self.txt_edit.textChanged.connect(self._on_txt_changed)
        # 光标换行 = 上一行改完，这时才把文本同步回文档
        self.txt_edit.cursorPositionChanged.connect(self._on_txt_cursor)
        self.lbl_cmp_hint = QLabel(
            "左侧改动 → 右侧跳行闪烁（绿=增 红=删 黄=改）；"
            "右侧改完一行 → 左侧跳到对应动画/帧")
        self.lbl_cmp_hint.setProperty("role", "dim")
        self.lbl_cmp_hint.setVisible(False)
        tv.addWidget(self.lbl_cmp_hint)
        tv.addWidget(self.txt_edit, 1)
        self.erow2 = QHBoxLayout()
        self.erow2.setSpacing(6)
        b_close = QPushButton("关闭")
        b_close.setToolTip("返回动画编辑（有改动会提示）")
        b_close.clicked.connect(self.close_text)
        b_tsave = QPushButton("保存")
        b_tsave.setToolTip("把编辑器里的文本写入 TXT")
        b_tsave.clicked.connect(self.save_text)
        b_tsave_close = QPushButton("保存并关闭")
        b_tsave_close.setObjectName("primaryBtn")
        b_tsave_close.clicked.connect(self.save_close_text)
        self.lbl_txt_dirty = QLabel("")
        self.lbl_txt_dirty.setProperty("role", "warnText")
        b_opt = QPushButton("一键优化")
        b_opt.setToolTip("规整缩进/制表符/空行（需再保存才写入）")
        b_opt.clicked.connect(self.optimize_text)
        b_night = QPushButton("夜间模式")
        b_night.setCheckable(True)
        b_night.setToolTip("夜间配色")
        b_night.clicked.connect(self._toggle_night)
        self.btn_night = b_night
        self.erow2.addWidget(b_close)
        self.erow2.addWidget(b_tsave)
        self.erow2.addWidget(b_tsave_close)
        self.erow2.addWidget(b_opt)
        self.erow2.addWidget(b_night)
        self.erow2.addStretch(1)
        self.erow2.addWidget(self.lbl_txt_dirty)
        w = QWidget()
        w.setLayout(self.erow2)
        self.txt_bar = w
        tv.addWidget(w)

        # ---- 对照模式：左右分栏，分隔条可拖动 ----
        self.cmp_split = QSplitter(Qt.Horizontal)
        self.cmp_split.setChildrenCollapsible(False)
        self.cmp_split.addWidget(self.anim_area)
        self.cmp_split.addWidget(self.text_area)
        self.cmp_split.splitterMoved.connect(self._cmp_split_moved)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self.cmp_split, 1)
        sp.addWidget(right)
        sp.setSizes([185, 240, 1000])
        sp.setStretchFactor(0, 0)
        sp.setStretchFactor(1, 0)
        sp.setStretchFactor(2, 1)

        # 注：Ctrl+Z / Ctrl+Shift+Z 由主窗口统一分发（见 main.undo / redo），
        # 这里不再单独挂快捷键，避免和索引图编辑器的撤销栈抢同一组按键。

    # ============================================================
    # 设置区：一行一行手写
    #
    # 这里刻意写成最笨的样子——不用网格、不用流式、不用列数常量。
    # 每一个 _row(...) 就是界面上的一行；
    # 里面每一个 self._xxx_field("命令名", 规格) 就是那一行上的一个控件。
    #
    # 要调整排版：把某一行整行剪切，粘贴到别的 _row(...) 里即可。
    # 要删掉某个字段：把它那一行注释掉。
    # 要加字段：复制一行，改命令名和规格。
    # ============================================================

    @staticmethod
    def _row(spacing=6):
        """新建一行（横向排布，末尾记得 addStretch）。"""
        r = QHBoxLayout()
        r.setSpacing(spacing)
        return r

    def _anim_field(self, cmd, spec):
        """动画级字段：登记进 self.anim_fields，返回可直接 addWidget 的小部件。"""
        f = CmdField(self, cmd, spec, frame_level=False)
        self.anim_fields.append(f)
        return f.unit

    def _frame_field(self, cmd, spec):
        """帧级附加字段：登记进 self.frame_fields。"""
        f = CmdField(self, cmd, spec, frame_level=True)
        self.frame_fields.append(f)
        return f.unit

    def _atk_field(self, cmd, spec):
        """攻击框附加字段：登记进 self.atk_fields。"""
        f = CmdField(self, cmd, spec, frame_level=True)
        self.atk_fields.append(f)
        return f.unit

    @staticmethod
    def _more_btn(slot):
        b = QPushButton("更多 ▸")
        b.setStyleSheet(MORE_BTN_QSS)
        b.clicked.connect(slot)
        return b

    @staticmethod
    def _collapsed(rows):
        """把若干行装进一个默认隐藏的容器（配合“更多”按钮）。"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        for r in rows:
            v.addLayout(r)
        w.setVisible(False)
        return w

    def _toggle_more(self):
        on = not self.more_widget.isVisible()
        self.more_widget.setVisible(on)
        self.btn_more.setText("更多 ▾" if on else "更多 ▸")

    def _toggle_fx_more(self):
        on = not self.fx_more_widget.isVisible()
        self.fx_more_widget.setVisible(on)
        self.btn_fx_more.setText("更多 ▾" if on else "更多 ▸")

    def _toggle_ax_more(self):
        on = not self.ax_more_widget.isVisible()
        self.ax_more_widget.setVisible(on)
        self.btn_ax_more.setText("更多 ▾" if on else "更多 ▸")

    # ---------------------------------------------------------- 动画 Header
    def _build_anim_header_box(self):
        gb = _styled_group("动画", "anim_header")
        v = QVBoxLayout(gb)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        self.anim_fields = []

        # 第 1 行：loop 开关 + 起止帧
        r = self._row()
        self.btn_loop = QPushButton("loop ❌")
        self.btn_loop.setObjectName("loopBtn")
        self.btn_loop.setCheckable(True)
        self.btn_loop.setFlat(True)
        self.btn_loop.setToolTip("按一下启用循环（loop 1），再按关闭（loop 0）")
        self.btn_loop.clicked.connect(self._loop_toggled)
        r.addWidget(self.btn_loop)
        r.addWidget(_lbl("起"))
        self.sp_loop_s = NumEdit(0, 100000, 35)
        r.addWidget(self.sp_loop_s)
        r.addWidget(_lbl("止"))
        self.sp_loop_e = NumEdit(0, 100000, 35)
        r.addWidget(self.sp_loop_e)
        self.sp_loop_s.editingFinished.connect(self._loop_param_changed)
        self.sp_loop_e.editingFinished.connect(self._loop_param_changed)
        self.sp_loop_s.setEnabled(False)
        self.sp_loop_e.setEnabled(False)
        r.addWidget(self._anim_field("hitfx", ("path", "wav")))
        r.addWidget(self._anim_field("hitflash", ("name",)))
        r.addWidget(self._anim_field("followanim", ("int", 1, 10000)))
        r.addWidget(self._anim_field("followcond", ("combo", ["1", "2", "3", "4", "5"])))
        r.addStretch(1)
        v.addLayout(r)

        # 第 2 行
        r = self._row()
        r.addWidget(self._anim_field("mponly", ("combo", ["-1", "0", "1", "2"])))
        r.addWidget(self._anim_field("耗蓝", ("energy",)))
        r.addWidget(self._anim_field("cancel", ("cancel",)))
        r.addWidget(self._anim_field("range", ("ints", 2, -100000, 100000)))
        r.addWidget(self._anim_field("快攻", ("bi",)))
        r.addWidget(self._anim_field("单伤", ("bi",)))
        r.addStretch(1)
        v.addLayout(r)

        # 展开更多：动画 Header 不常用字段
        r = self._row()
        self.btn_more = self._more_btn(self._toggle_more)
        r.addWidget(self.btn_more)
        r.addStretch(1)
        v.addLayout(r)
        
        ma = self._row()
        ma.addWidget(self._anim_field("rangez", ("ints", 2, -100000, 100000)))
        ma.addWidget(self._anim_field("rangea", ("ints", 2, -100000, 100000)))
        ma.addWidget(self._anim_field("rangeb", ("ints", 2, -100000, 100000)))
        ma.addWidget(self._anim_field("flip帧", ("int", 0, 100000)))
        ma.addWidget(self._anim_field("quake帧", ("ints", 3, 0, 100000)))
        ma.addWidget(self._anim_field("weapon帧", ("ints", 2, 0, 100000)))
        ma.addStretch(1)
        
        mb = self._row()
        mb.addWidget(self._anim_field("subentity", ("name",)))
        #ma.addWidget(self._anim_field("custentity", ("name",)))
        mb.addWidget(self._anim_field("summon帧", ("ints", 5, 0, 100000)))
        mb.addWidget(self._anim_field("spawn帧", ("ints", 5, 0, 100000)))
        mb.addWidget(self._anim_field("unsummon帧", ("int", 0, 100000)))
        mb.addStretch(1)

        mc = self._row()
        mc.addWidget(self._anim_field("blockfx", ("path", "wav")))
        mc.addWidget(self._anim_field("blockflash", ("name",)))
        mc.addWidget(self._anim_field("dive", ("ints", 2, -100000, 100000)))
        mc.addWidget(self._anim_field("animheight", ("int", -100000, 100000)))
        mc.addWidget(self._anim_field("sync", ("name",)))
        mc.addWidget(self._anim_field("bouncefactor", ("int", 0, 100000)))
        mc.addStretch(1)

        md = self._row()
        md.addWidget(self._anim_field("chargetime", ("int", 0, 100000)))
        md.addWidget(self._anim_field("ignoreattackid", ("int", 0, 100000)))
        md.addWidget(self._anim_field("counterattack", ("bi",)))
        md.addWidget(self._anim_field("counterframe", ("ints", 3, 0, 100000)))
        md.addWidget(self._anim_field("counterrange", ("ints", 4, 0, 100000)))
        md.addStretch(1)

        me = self._row()
        me.addWidget(self._anim_field("custknife", ("name",)))
        me.addWidget(self._anim_field("custstar", ("name",)))
        me.addWidget(self._anim_field("custboomerang", ("name",)))
        me.addWidget(self._anim_field("custbomb", ("name",)))
        me.addWidget(self._anim_field("custpbomb", ("name",)))
        me.addStretch(1)

        mf = self._row()
        mf.addWidget(self._anim_field("shoot帧", ("ints", 2, 0, 100000)))
        mf.addWidget(self._anim_field("throw帧", ("ints", 2, 0, 100000)))
        mf.addWidget(self._anim_field("toss帧", ("ints", 2, 0, 100000)))
        mf.addStretch(1)

        self.more_widget = self._collapsed([ma, mb, mc, md, me, mf])
        v.addWidget(self.more_widget)
        return gb

    # ---------------------------------------------------------- 帧设置
    def _build_frame_box(self):
        gb = _styled_group("帧", "frame_basic")
        v = QVBoxLayout(gb)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        self.frame_fields = []

        # 第 1 行：Delay
        r = self._row()
        r.addWidget(_lbl("Delay"))
        self.sp_delay = NumEdit(0, 100000, 35)
        self.sp_delay.editingFinished.connect(self._delay_changed)
        r.addWidget(self.sp_delay)
        r.addSpacing(6)

        # 第 1 行（续）：Offset
        r.addWidget(_lbl("Offset x,y"))
        self.sp_ox = NumEdit()
        self.sp_oy = NumEdit()
        self.sp_ox.editingFinished.connect(self._offset_changed)
        self.sp_oy.editingFinished.connect(self._offset_changed)
        r.addWidget(self.sp_ox)
        r.addWidget(self.sp_oy)
        r.addSpacing(6)

        # 第 1 行（续）：BBox
        self.sw_bbox = ColorSwatch(
            self.bbox_color, self._pick_bbox_color,
            "点击选择 BBox 框线颜色（背景色相近时换个鲜艳色）")
        r.addWidget(self.sw_bbox)
        r.addWidget(_lbl("BBox x,y,w,h"))
        self.sp_bbox = []
        for i, nm in enumerate(("x", "y", "w", "h")):
            s = NumEdit(w=40)
            s.setToolTip(f"bbox {nm}")
            s.editingFinished.connect(self._bbox_changed)
            r.addWidget(s)
            self.sp_bbox.append(s)
        r.addWidget(_lbl("z"))
        s1 = NumEdit(w=40)
        s1.setToolTip("bbox z")
        s1.editingFinished.connect(self._bbox_changed)
        r.addWidget(s1)
        self.sp_bbox.append(s1)
        r.addSpacing(6)
        r.addWidget(_icon_btn("✕", "#d0342c", "清除：写入 bbox 0 0 0 0 0（无框）",
                              self.clear_bbox))
        r.addWidget(_icon_btn("▶", "#2f6db3",
                              "继承：删除本帧 bbox，沿用前一帧设定",
                              self.inherit_bbox))
        r.addWidget(_icon_btn("⧉", "#2f6db3",
                              "复制：记下当前帧的完整 BBox 设置",
                              self.copy_bbox))
        r.addWidget(_icon_btn("⤵", "#2f9267",
                              "粘贴：把复制的 BBox 设置覆盖到当前帧",
                              self.paste_bbox))
        r.addStretch(1)
        v.addLayout(r)

        # 展开更多：帧附加命令
        r = self._row()
        self.btn_fx_more = self._more_btn(self._toggle_fx_more)
        r.addWidget(self.btn_fx_more)
        r.addStretch(1)
        v.addLayout(r)

        fa = self._row()
        fa.addWidget(self._frame_field("move", ("int", -100000, 100000)))
        fa.addWidget(self._frame_field("movea", ("int", -100000, 100000)))
        fa.addWidget(self._frame_field("movez", ("int", -100000, 100000)))
        fa.addWidget(self._frame_field("seta", ("int", -100000, 100000)))
        fa.addWidget(self._frame_field("sound", ("path", "wav")))
        fa.addStretch(1)

        fb = self._row()
        fb.addWidget(self._frame_field("alphamask", ("path", "png")))
        fb.addWidget(self._frame_field("idle", ("bi",)))
        fb.addWidget(self._frame_field("fshadow", ("int", 0, 100000)))
        fb.addWidget(self._frame_field("shadowcoords", ("ints", 2, -100000, 100000)))
        fb.addStretch(1)

        fc = self._row()
        fc.addWidget(self._frame_field("itembox", ("ints", 4, -100000, 100000)))
        fc.addWidget(self._frame_field("platform", ("ints", 8, -100000, 100000)))
        fc.addStretch(1)

        self.fx_more_widget = self._collapsed([fa, fb, fc])
        v.addWidget(self.fx_more_widget)
        return gb

    # ---------------------------------------------------------- 攻击框设置
    def _build_attack_box(self):
        gb = _styled_group("攻击框", "attack")
        v = QVBoxLayout(gb)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        self.atk_fields = []

        # 第 1 行：颜色 + 类型 + 特殊类型 + 四个按钮
        r = self._row()
        self.sw_atk = ColorSwatch(
            self.attack_color, self._pick_attack_color,
            "点击选择攻击框框线颜色（背景色相近时换个鲜艳色）")
        r.addWidget(self.sw_atk)
        r.addWidget(_lbl("attack(#)"))
        self.sp_atk_no = NumEdit(1, 99, 35)
        self.sp_atk_no.editingFinished.connect(self._attack_changed)
        r.addWidget(self.sp_atk_no)
        r.addSpacing(16)
        # 勾选框控制“是否用特殊类型”：不勾就是普通 attack{#}
        self.ck_atk_special = QCheckBox("特殊")
        self.ck_atk_special.setToolTip(
            "勾选才使用特殊攻击类型（burn/shock/blast/freeze/steal）；\n"
            "取消勾选则回到普通 attack{#}")
        self.ck_atk_special.stateChanged.connect(self._atk_special_toggled)
        r.addWidget(self.ck_atk_special)
        self.cmb_atk_special = SafeComboBox()
        self.cmb_atk_special.addItem("无")
        self.cmb_atk_special.addItems(SPECIAL_ATTACK_TYPES)
        self.cmb_atk_special.setToolTip(
            "特殊攻击类型：选中后替代 attack{#}，类型(#) 失效")
        # 延迟提交：滚轮连换类型只在停手 / 鼠标移出后算一次修改
        self.cmb_atk_special.set_deferred(self._attack_changed)
        r.addWidget(self.cmb_atk_special)
        r.addSpacing(6)
        r.addWidget(_icon_btn("✕", "#d0342c", "清除：写入 attack 0（攻击判定结束）",
                              self.clear_attack))
        r.addWidget(_icon_btn("▶", "#2f6db3",
                              "继承：删除本帧 attack，沿用前一帧设定",
                              self.inherit_attack))
        r.addWidget(_icon_btn("⧉", "#2f6db3",
                              "复制：记下当前帧的完整 Attack 设置",
                              self.copy_attack))
        r.addWidget(_icon_btn("⤵", "#2f9267",
                              "粘贴：把复制的 Attack 设置覆盖到当前帧",
                              self.paste_attack))
        r.addSpacing(6)

        # 第 1 行（续）：坐标 / 伤害 / 标志位 / pause / z
        r.addWidget(_lbl("x/y/w/h"))
        self.sp_atk = []
        for nm in ("x", "y", "w", "h"):    
            s = NumEdit(w=40)
            s.setToolTip(f"攻击框 {nm}")
            s.editingFinished.connect(self._attack_changed)
            r.addWidget(s)
            self.sp_atk.append(s)
        r.addWidget(_lbl("伤害"))
        self.sp_atk_dmg = NumEdit(0, 100000, 35, double=True)
        self.sp_atk_dmg.editingFinished.connect(self._attack_changed)
        r.addWidget(self.sp_atk_dmg)
        r.addSpacing(16)
        r.addStretch(1)
        v.addLayout(r)
        
        r = self._row()
        self.ck_atk_kd = QCheckBox("击倒")
        self.ck_atk_kd.setToolTip("knockdown（freeze=冰冻秒数；blast 无此项）")
        self.ck_atk_block = QCheckBox("无法格挡")
        self.ck_atk_noflash = QCheckBox("无Flash")
        for ck in (self.ck_atk_kd, self.ck_atk_block, self.ck_atk_noflash):
            ck.stateChanged.connect(self._attack_changed)
            r.addWidget(ck)
        r.addSpacing(16)
        r.addWidget(_lbl("pause"))
        self.sp_atk_pause = NumEdit(0, 100000, 35)
        self.sp_atk_pause.editingFinished.connect(self._attack_changed)
        r.addWidget(self.sp_atk_pause)
        r.addWidget(_lbl("z"))
        self.sp_atk_z = NumEdit(0, 100000, 35)
        self.sp_atk_z.editingFinished.connect(self._attack_changed)
        r.addWidget(self.sp_atk_z)
        r.addStretch(1)
        v.addLayout(r)

        # 展开更多：攻击框附加命令
        r = self._row()
        self.btn_ax_more = self._more_btn(self._toggle_ax_more)
        r.addWidget(self.btn_ax_more)
        r.addStretch(1)
        v.addLayout(r)

        aa = self._row()
        aa.addWidget(self._atk_field("grabin", ("grabin",)))
        aa.addWidget(self._atk_field("jugglecost", ("int", 0, 100000)))
        aa.addWidget(self._atk_field("otg", ("combo", ["0", "1", "2"])))
        aa.addWidget(self._atk_field("dropv", ("ints", 3, -100000, 100000)))
        aa.addWidget(self._atk_field("nokill", ("bi",)))
        aa.addStretch(1)

        ab = self._row()
        ab.addWidget(self._atk_field("staydown", ("ints", 2, -100000, 100000)))
        ab.addWidget(self._atk_field("forcedirection", ("combo", ["-2", "-1", "0", "1", "2"])))
        ab.addWidget(self._atk_field("guardcost", ("int", 0, 100000)))
        ab.addWidget(self._atk_field("seal", ("ints", 2, 0, 100000)))
        ab.addWidget(self._atk_field("damageonlanding", ("int", -100000, 100000)))
        ab.addStretch(1)

        ac = self._row()
        ac.addWidget(self._atk_field("stun", ("int", 0, 100000)))
        ac.addWidget(self._atk_field("noreflect", ("bi",)))
        ac.addWidget(self._atk_field("forcemap", ("ints", 2, 0, 100000)))
        ac.addWidget(self._atk_field("dot", ("ints", 5, 0, 100000)))
        ac.addStretch(1)

        self.ax_more_widget = self._collapsed([aa, ab, ac])
        v.addWidget(self.ax_more_widget)
        return gb

    # ---------------------------------------------------------- 跳帧命令
    def _build_jump_box(self):
        gb = _styled_group("跳帧", "jump")
        r = self._row()
        gb.setLayout(r)
        r.setContentsMargins(10, 8, 10, 10)
        self._jf_rows = {}

        # ---- jumpframe：帧 / vy / vx / vz ----
        ck = QCheckBox("jumpframe")
        ck.stateChanged.connect(self._frame_cmd_changed)
        r.addWidget(ck)
        spins = []
        for nm in ("帧", "vy", "vx", "vz"):
            r.addWidget(_lbl(nm))
            s = _mk_spin(0, 100000, 46, deferred=True)
            s.setToolTip(f"jumpframe {nm}")
            s.setEnabled(False)
            s.set_deferred(self._frame_cmd_changed)   # 滚轮连改只算一次
            r.addWidget(s)
            spins.append(s)
        ck.toggled.connect(lambda on, ss=spins:
                           [w.setEnabled(on) for w in ss])
        self._jf_rows["jumpframe"] = (ck, spins)
        r.addSpacing(16)

        # ---- dropframe：帧 ----
        ck = QCheckBox("dropframe")
        ck.stateChanged.connect(self._frame_cmd_changed)
        r.addWidget(ck)
        spins = []
        r.addWidget(_lbl("帧"))
        s = _mk_spin(0, 100000, 46, deferred=True)
        s.setToolTip("dropframe")
        s.setEnabled(False)
        s.set_deferred(self._frame_cmd_changed)
        r.addWidget(s)
        spins.append(s)
        ck.toggled.connect(lambda on, ss=spins:
                           [w.setEnabled(on) for w in ss])
        self._jf_rows["dropframe"] = (ck, spins)
        r.addSpacing(16)

        # ---- landframe：帧 / 尘 ----
        ck = QCheckBox("landframe")
        ck.stateChanged.connect(self._frame_cmd_changed)
        r.addWidget(ck)
        spins = []
        for nm in ("帧", "尘"):
            r.addWidget(_lbl(nm))
            s = _mk_spin(0, 100000, 46, deferred=True)
            s.setToolTip(f"landframe {nm}")
            s.setEnabled(False)
            s.set_deferred(self._frame_cmd_changed)
            r.addWidget(s)
            spins.append(s)
        ck.toggled.connect(lambda on, ss=spins:
                           [w.setEnabled(on) for w in ss])
        self._jf_rows["landframe"] = (ck, spins)
        r.addSpacing(16)

        r.addStretch(1)
        return gb

    # ---------------------------------------------------------- DrawMethod
    # ---- DrawMethod 子命令面板 ----
    # 每个子命令一行：启用框 + 带合法范围校验的控件。
    # 规格：(子命令, 控件规格, 提示)
    #   ("f", lo, hi) / ("f2"/"f3", lo, hi)   1/2/3 个浮点（倍数，1=原样）
    #   ("i", lo, hi) / ("i4", lo, hi)        1/4 个整数
    #   ("chk",)                              勾选框：勾=写 1，不勾=不写这行
    #   ("combo",)                            下拉（alpha 0-6 / tintmode 1-6）
    #   ("color",)                            色表索引 或 R_G_B（手动校验）
    _DM_DEFS = (
        ("scale", ("f2", -8.0, 8.0), "整体缩放（1=原大，负数=镜像翻转）"),
        ("scalex", ("f", -8.0, 8.0), "只缩放宽；和 scale 同开时覆盖它的 x"),
        ("scaley", ("f", -8.0, 8.0), "只缩放高；和 scale 同开时覆盖它的 y"),
        ("shiftx", ("f", -8.0, 8.0), "倾斜（lean），1=顶部横移一个图宽"),
        ("rotate", ("i", 0, 359), "旋转角度（度）"),
        ("alpha", ("combo",), "混合模式（1=半透明 … 6=五成）"),
        ("remap", ("i", 0, 255), "换色表编号"),
        ("fillcolor", ("color",), "整图填充色：色表索引 或 R_G_B"),
        ("flipx", ("chk",), "水平翻转"),
        ("flipy", ("chk",), "垂直翻转"),
        ("fliprotate", ("chk",), "先翻转再旋转"),
        ("tintcolor", ("color",), "染色颜色：色表索引 或 R_G_B"),
        ("tintmode", ("combo",), "染色强度（要和 tintcolor 配套）"),
        ("channel", ("f3", 0.0, 1.0), "RGB 三通道乘数（0~1，1=不变）"),
        ("channelr", ("f", 0.0, 1.0), "红通道乘数"),
        ("channelg", ("f", 0.0, 1.0), "绿通道乘数"),
        ("channelb", ("f", 0.0, 1.0), "蓝通道乘数"),
        ("clip", ("i4", -99999, 99999), "裁剪窗口 x y w h（图内坐标）"),
        ("clipx", ("i", -99999, 99999), "裁剪窗口的 x"),
        ("clipy", ("i", -99999, 99999), "裁剪窗口的 y"),
        ("clipw", ("i", 0, 99999), "裁剪窗口的宽"),
        ("cliph", ("i", 0, 99999), "裁剪窗口的高"),
    )
    _DM_SPEC = {d[0]: d[1] for d in _DM_DEFS}
    # GUI 管得着的子命令（重写时其余子命令行原样保留）
    _DM_GUI_SUBS = tuple(d[0] for d in _DM_DEFS)
    # 值全是这些时就不再写这行（免得保存出一堆“等于没设”的行）
    _DM_SKIP_WHEN = {
        "scale": ("1", "1"), "scalex": ("1",), "scaley": ("1",),
        "shiftx": ("0",), "rotate": ("0",), "alpha": ("0",),
        "remap": ("0",), "fillcolor": ("0",), "tintcolor": ("0",),
        "tintmode": ("1",),
        "channel": ("1", "1", "1"), "channelr": ("1",), "channelg": ("1",),
        "channelb": ("1",),
        "clip": ("0", "0", "0", "0"), "clipx": ("0",), "clipy": ("0",),
        "clipw": ("0",), "cliph": ("0",),
    }
    # 子命令没出现在文件里时控件显示的默认值
    _DM_DEFVALS = {
        "scale": ("1", "1"), "scalex": ("1",), "scaley": ("1",),
        "shiftx": ("0",), "rotate": ("0",), "remap": ("0",),
        "channel": ("1", "1", "1"), "channelr": ("1",), "channelg": ("1",),
        "channelb": ("1",),
        "clip": ("0", "0", "0", "0"), "clipx": ("0",), "clipy": ("0",),
        "clipw": ("0",), "cliph": ("0",),
    }

    def _build_dm_box(self):
        gb = _styled_group("DrawMethod", "dm")
        v = QVBoxLayout(gb)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.ck_nodm = QCheckBox("nodrawmethod")
        self.ck_nodm.setToolTip("忽略 drawmethod")
        self.ck_nodm.stateChanged.connect(self._nodm_changed)
        top.addWidget(self.ck_nodm)
        # 写法切换：子命令（新，一行一项）/ 一整行 10 个整数（老）
        self.btn_dm_mode = QPushButton("写法：子命令")
        self.btn_dm_mode.setToolTip(
            "drawmethod 的两种写法（二者择一）：\n"
            "子命令 —— drawmethod scale 1.5 / drawmethod flipx 1 …"
            "一行一项（默认，推荐）\n"
            "一串数 —— drawmethod 256 256 1 0 0 0 0 0 0 0（老写法）\n"
            "检测到老写法的帧会自动切成老写法；\n"
            "手动切换时，本帧已有的另一种写法会被删除重写（会提示）")
        self.btn_dm_mode.clicked.connect(self._dm_mode_clicked)
        top.addWidget(self.btn_dm_mode)
        top.addStretch(1)
        # 文件里超出控件合法范围的值：显示时被夹取，保存时改写成夹取值
        self.lbl_dm_warn = QLabel("")
        self.lbl_dm_warn.setStyleSheet("color:#b04040;")
        self.lbl_dm_warn.setToolTip(
            "文件里的值超出了控件的合法范围：\n"
            "显示时按夹取后的值来，保存时这一行会被改写。")
        top.addWidget(self.lbl_dm_warn)
        v.addLayout(top)
        # 两列网格：每个子命令一行（启用框 + 值控件）
        self._dm_rows = {}          # 子命令 -> (启用框, [值控件...])
        self._dm_widgets = []       # nodrawmethod 时整体禁用
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(3)
        for i, (sub, spec, tip) in enumerate(self._DM_DEFS):
            grid.addWidget(self._mk_dm_row(sub, spec, tip), i % 11, i // 11)
        v.addLayout(grid)
        self.ck_nodm.toggled.connect(lambda _on: self._dm_sync_enable())
        return gb

    def _mk_dm_row(self, sub, spec, tip):
        """一个 drawmethod 子命令的编辑行（启用框 + 值控件）。"""
        kind = spec[0]
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5)
        ck = QCheckBox(sub)
        ck.setToolTip(tip)
        ctrls = []
        if kind == "chk":
            # 勾选框本身就是取值：勾上 = drawmethod sub 1
            ck.setToolTip(tip + "（勾上 = drawmethod %s 1）" % sub)
            ck.stateChanged.connect(lambda _s: self._dm_changed(True))
            self._dm_rows[sub] = (ck, [])
            self._dm_widgets.append(ck)
            h.addWidget(ck)
            h.addStretch(1)
            return box
        h.addWidget(ck)
        if kind in ("f", "i"):
            e = NumEdit(spec[1], spec[2], 54, double=(kind == "f"))
            ctrls.append(e)
        elif kind in ("f2", "f3"):
            n = 2 if kind == "f2" else 3
            for k in range(n):
                e = NumEdit(spec[1], spec[2], 54, double=True)
                ctrls.append(e)
        elif kind == "i4":
            for _k in range(4):
                e = NumEdit(spec[1], spec[2], 54)
                ctrls.append(e)
        elif kind == "combo":
            cb = SafeComboBox()
            cb.addItems(self._dm_combo_items(sub))
            cb.setFixedWidth(96)
            cb.set_deferred(self._dm_changed)
            ctrls.append(cb)
        elif kind == "color":
            e = QLineEdit()
            e.setFixedWidth(88)
            e.setPlaceholderText("索引 / R_G_B")
            e.editingFinished.connect(lambda e=e: self._dm_color_check(e))
            e.editingFinished.connect(self._dm_changed)
            ctrls.append(e)
        if kind == "f2":
            for k, lab in ((0, "x"), (1, "y")):
                h.addWidget(ctrls[k])
                h.addWidget(_lbl(lab))
        elif kind == "f3":
            for k, lab in ((0, "R"), (1, "G"), (2, "B")):
                h.addWidget(ctrls[k])
                h.addWidget(_lbl(lab))
        elif kind == "i4":
            for k, lab in ((0, "x"), (1, "y"), (2, "w"), (3, "h")):
                h.addWidget(ctrls[k])
                h.addWidget(_lbl(lab))
        else:
            for e in ctrls:
                h.addWidget(e)
        ck.toggled.connect(lambda on, c=tuple(ctrls):
                           [w.setEnabled(on) for w in c])
        for w in ctrls:
            if isinstance(w, NumEdit):
                w.editingFinished.connect(lambda: self._dm_changed(False))
        ck.toggled.connect(lambda _on: self._dm_changed(True))
        self._dm_rows[sub] = (ck, ctrls)
        self._dm_widgets.append(ck)
        self._dm_widgets.extend(ctrls)
        h.addStretch(1)
        return box

    @staticmethod
    def _dm_combo_items(sub):
        if sub == "alpha":
            items = ["0 关"]
            for k in (1, 2, 3, 4, 5, 6):
                items.append("%d %s" % (k, bor_dm.ALPHA_MODES[k][0]))
            return items
        return ["%d %s" % (k, bor_dm.ALPHA_MODES[k][0])
                for k in (1, 2, 3, 4, 5, 6)]      # tintmode

    @staticmethod
    def _dm_color_ok(t):
        """fillcolor / tintcolor 的合法格式：色表索引（非负整数）
        或 R_G_B（三个 0~255）。"""
        t = (t or "").strip()
        if not t:
            return False
        if "_" in t:
            parts = t.split("_")
            if len(parts) != 3:
                return False
            return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
        return t.isdigit()

    def _dm_color_check(self, e):
        t = e.text().strip()
        bad = bool(t) and not self._dm_color_ok(t)
        e.setStyleSheet("background:%s;" % WARN_BG if bad else "")
        e.setToolTip("合法写法：色表索引（如 15）或 R_G_B（如 255_128_0）"
                     if bad else "")

    def _dm_sync_enable(self):
        """按“子命令启用框 + nodrawmethod”刷新值控件的可用状态。"""
        nodm = self.ck_nodm.isChecked()
        for ck, ctrls in self._dm_rows.values():
            for w in ctrls:
                w.setEnabled(ck.isChecked() and not nodm)

    # ---------------- 播放（按 delay 还原游戏速度） ----------------
    def start_play(self, mode):
        a = self.current_anim()
        if a is None or not a.frames():
            return
        self._play_mode = mode
        self._play_start = self.frame_idx      # 播完自动回到这里
        self._play_tick()

    def stop_play(self, restore=False):
        self._play_mode = None
        self.play_timer.stop()
        # 播放结束：位移 / 生成物一律从画布上撤掉
        self.canvas.set_motion(None)
        self.canvas.set_overlay([], [])
        if restore and self._play_start is not None:
            self.frame_idx = self._play_start
            self._play_start = None
            self._refresh_frame_ui()
        self._play_start = None
        self._refresh_frame_ui()      # 播放结束后恢复编辑区可用性
        self.timeline.update()

    def _loop_start(self):
        a = self.current_anim()
        if a:
            _, ln = a.find_cmd("loop")
            if ln and ln.args() and ln.args()[0] == "1" and len(ln.args()) >= 2:
                return _to_int(ln.args()[1])
        return 0

    def _play_tick(self):
        if self._play_mode is None:
            return
        a = self.current_anim()
        frames = a.frames() if a else []
        if not frames:
            self.stop_play()
            return
        i = min(self.frame_idx, len(frames) - 1)
        self.frame_idx = i
        self._refresh_frame_ui()
        self._update_sim()          # 位移 / 跳跃 / 生成物跟着播
        self._play_frame_sound(frames[i])
        d = _to_int(frames[i]["delay"][0]) if frames[i]["delay"] else 10
        self.play_timer.start(max(10, d * 10))

    # ---- 帧音效（sound 命令） ----
    def _sound_path(self, rel):
        if not rel or rel.lower() == "none":
            return None
        rel = rel.replace("\\", "/")
        for root in self.root_dirs:
            p = os.path.join(root, rel.replace("/", os.sep))
            if os.path.exists(p):
                return p
            for e in (".wav", ".WAV", ".ogg", ".mp3"):
                if os.path.exists(p + e):
                    return p + e
        return None

    def _play_sound(self, path):
        """真正发声：QSound(仅 wav) → QMediaPlayer(ogg/mp3) → winsound(wav)。

        之前只依赖 QSound，遇到 ogg/mp3 或 QtMultimedia 缺失时完全无声，
        静音按钮看起来“没有作用”。这里补上多级回退。
        """
        is_wav = path.lower().endswith(".wav")
        if QSound is not None and is_wav:
            try:
                QSound.play(path)
                return True
            except Exception:
                pass
        if QMediaPlayer is not None:
            try:
                if getattr(self, "_player", None) is None:
                    self._player = QMediaPlayer(self)
                self._player.setMedia(QMediaContent(QUrl.fromLocalFile(path)))
                self._player.play()
                return True
            except Exception:
                pass
        if os.name == "nt" and is_wav:
            try:
                import winsound
                winsound.PlaySound(
                    path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return True
            except Exception:
                pass
        return False

    def _play_frame_sound(self, fr):
        if self._muted:
            return
        for _, c, args in fr["extras"]:
            if c == "sound" and args:
                path = self._sound_path(args[0])
                if path:
                    self._play_sound(path)
                return

    def _play_advance(self):
        if self._play_mode is None:
            return
        a = self.current_anim()
        frames = a.frames() if a else []
        if not frames:
            self.stop_play()
            return
        nxt = self.frame_idx + 1
        if nxt >= len(frames):
            if self._play_mode == "loop":
                nxt = self._loop_start()
            else:
                self.stop_play(restore=True)     # 自然播完 → 光标回到起点
                return
        self.frame_idx = nxt
        self._play_tick()

    # ---------------- 攻击预览 ----------------
    def open_attack_preview(self):
        """时间轴右下角按钮：开一个“关卡场景试打”窗口。"""
        a = self.current_anim()
        if a is None:
            QMessageBox.information(self, "攻击预览", "先选一个动画。")
            return
        if not anim_has_attack(a):
            QMessageBox.information(
                self, "攻击预览", f"动画 {a.name} 里没有攻击框，没法试打。")
            return
        self.stop_play()
        try:
            from bor_view import AttackPreview
        except Exception as ex:
            QMessageBox.warning(self, "攻击预览", f"加载失败：{ex}")
            return
        if self._preview_win is not None:
            try:
                self._preview_win.close()
            except Exception:
                pass
            self._preview_win = None
        win = AttackPreview(self, a)
        self._preview_win = win
        win.destroyed.connect(lambda _=None: setattr(self, "_preview_win",
                                                     None))
        win.show()

    # ---------------- 编辑模式（动画 / 文本 / 对照） ----------------
    def _on_mode_clicked(self, mid):
        """三个互斥按钮：0=动画编辑 1=文本编辑 2=对照模式。"""
        want = (self.MODE_ANIM, self.MODE_TEXT, self.MODE_CMP)[mid]
        if want == self._mode:
            return
        if not self._leave_mode(want):
            self._sync_mode_buttons()      # 放弃切换 → 按钮弹回
            return
        self._enter_mode(want)

    def _sync_mode_buttons(self):
        btn = {self.MODE_ANIM: self.btn_mode_anim,
               self.MODE_TEXT: self.btn_mode_text,
               self.MODE_CMP: self.btn_mode_cmp}.get(self._mode)
        if btn is not None:
            btn.setChecked(True)

    def _leave_mode(self, want):
        """离开当前模式前确认；返回 False 表示取消切换。"""
        if self._mode == self.MODE_TEXT and self._txt_modified():
            r = self._ask_three(
                "未保存修改", "文本编辑有未保存的修改。",
                "保存并切换", "放弃修改", "取消")
            if r == 2:
                return False
            if r == 0 and not self.save_text():
                return False
        return True

    def _enter_mode(self, mode):
        self.stop_play()
        self._mode = mode
        text_on = mode != self.MODE_ANIM
        anim_on = mode != self.MODE_TEXT
        self.anim_area.setVisible(anim_on)
        self.text_area.setVisible(text_on)
        # 分隔条：单栏时把另一栏压成 0 宽，双栏时用记住的比例
        if mode == self.MODE_CMP:
            self._load_text_into_editor()
            self.cmp_split.setSizes(list(self._cmp_sizes))
        else:
            total = max(1, self.cmp_split.width())
            self.cmp_split.setSizes(
                [0, total] if mode == self.MODE_TEXT else [total, 0])
        # 对照模式同样可以直接改文本，按钮行与文本模式完全一致
        self.txt_edit.setReadOnly(False)
        self.txt_bar.setVisible(mode != self.MODE_ANIM)
        self.lbl_cmp_hint.setVisible(mode == self.MODE_CMP)
        self._sync_mode_buttons()
        if mode == self.MODE_TEXT:
            # 进文本模式前先把当前文档的文本倒进编辑器，并把基准一并重置：
            # 否则编辑器里可能还是上一次（甚至上一个实体）的文本，
            # 一动实体列表就会被当成“文本有未保存修改”。
            self._load_text_into_editor()
            self.lbl_txt_dirty.setText("")
            self._refresh_text_lists()
        elif mode == self.MODE_ANIM:
            self._refresh_all()
        else:
            self._refresh_all()

    def _cmp_split_moved(self, pos, index):
        """记住对照模式的分栏比例，下次进入时恢复。"""
        if self._mode == self.MODE_CMP:
            sizes = self.cmp_split.sizes()
            if len(sizes) == 2 and min(sizes) > 40:
                self._cmp_sizes = sizes
                self._save_pref("cmp_sizes", list(sizes))

    @property
    def _text_mode(self):
        """是否处于“文本编辑”模式（对照模式不算：它左侧仍是动画编辑）。"""
        return self._mode == self.MODE_TEXT

    @property
    def _cmp_mode(self):
        return self._mode == self.MODE_CMP

    def _load_text_into_editor(self):
        if self.doc is None:
            return
        self._txt_original = self.doc.to_text()
        self.txt_edit.blockSignals(True)
        self.txt_edit.setPlainText(self._txt_original)
        self.txt_edit.blockSignals(False)
        # setPlainText 会把光标带回开头，这里同步一下，免得误判成“换行了”
        self._txt_line = self.txt_edit.textCursor().blockNumber()

    # ---------------- 播放锁：播放中禁止任何修改 ----------------
    @property
    def _editing_locked(self):
        """时间轴正在播放动画：此时不接受任何编辑。"""
        return self._play_mode is not None

    def _guard_edit(self, silent=False):
        """播放中拦截修改。返回 False 表示本次编辑应放弃。"""
        if not self._editing_locked:
            return True
        if not silent:
            QMessageBox.information(
                self, "播放中", "请先停止播放（空格）再改。")
        return False

    # ---------------- 对照模式：左侧编辑 → 右侧文本跳转 + 着色闪烁 ----------------
    @staticmethod
    def _diff_marks(old, new):
        """两份文本的差异行：增=绿 / 删=红 / 改=黄。"""
        a = old.splitlines()
        b = new.splitlines()
        marks = []
        for tag, i1, _i2, j1, j2 in difflib.SequenceMatcher(
                a=a, b=b, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            if tag == "insert":
                marks += [(j, FLASH_ADD) for j in range(j1, j2)]
            elif tag == "delete":
                marks.append((min(i1, max(0, len(b) - 1)), FLASH_DEL))
            else:                                  # replace
                marks += [(j, FLASH_MOD) for j in range(j1, j2)]
        return [m for m in marks if m[0] >= 0]

    def _sync_compare(self):
        """对照模式：把最新文本刷到右侧，并闪烁标注改动行（不抢焦点）。"""
        if not self._cmp_mode or self.doc is None:
            return
        old = self.txt_edit.toPlainText()
        new = self.doc.to_text()
        if old == new:
            return
        marks = self._diff_marks(old, new)
        self.txt_edit.blockSignals(True)
        self.txt_edit.setPlainText(new)
        self.txt_edit.blockSignals(False)
        self._txt_line = self.txt_edit.textCursor().blockNumber()
        # 程序替它换了文本，就得同时把“已存盘基准”挪过来。
        # 不挪的话 _txt_modified() 会一直为真，于是什么都没改也弹“未保存”。
        self._txt_original = new
        if marks:
            self.txt_edit.flash_lines(marks, focus=False)

    # ---------------- 右侧文本 → 文档（txt 才是最终依据） ----------------
    @staticmethod
    def _multi_line_selection(cur):
        """选区是否跨了多行（跨行时不移动动画编辑区）。"""
        if not cur.hasSelection():
            return False
        d = cur.document()
        return (d.findBlock(cur.selectionStart()).blockNumber()
                != d.findBlock(cur.selectionEnd()).blockNumber())

    def _on_txt_cursor(self):
        """光标换到别的行 → 上一行改完了：定位 + 把文本同步回文档。"""
        if self.doc is None or self._mode == self.MODE_ANIM:
            return
        cur = self.txt_edit.textCursor()
        line = cur.blockNumber()
        if line == self._txt_line:
            return
        self._txt_line = line
        if self._cmp_mode and not self._multi_line_selection(cur):
            self._goto_line_location(line)
        self._sync_text_to_doc()

    def _line_location(self, line_no):
        """文本第 line_no 行（0 起）属于哪个动画的第几帧。

        帧的归属：数光标之前出现过几个 frame 行。
        frame 行前面的空行与设置行跟着它**后面**那一帧（它们本来就是给那一帧用的），
        只有光标正落在某个 frame 行上时才算这一帧自己。
        返回 (动画下标, 帧下标)；落在 Header 区则动画下标为 None。
        """
        anim_i = -1
        n_frame = 0
        for i, raw in enumerate(self.txt_edit.toPlainText().splitlines()):
            if i > line_no:
                break
            t = raw.split()
            if not t:
                continue
            c = t[0].lower()
            if c == "anim":
                anim_i += 1
                n_frame = 0
                continue
            if i == line_no:
                break                   # 光标所在行本身不计数
            if c == "frame":
                n_frame += 1
        if anim_i < 0:
            return None, 0
        return anim_i, n_frame

    def _goto_line_location(self, line_no):
        """对照模式：把动画编辑区跳到光标所在的动画 / 帧。"""
        ai, fi = self._line_location(line_no)
        if ai is None or ai >= len(self.doc.anims):
            return False                # Header 区：不动动画编辑区
        a = self.doc.anims[ai]
        fi = min(fi, max(0, len(a.frames()) - 1))
        if ai == self.anim_idx and fi == self.frame_idx:
            return False
        self.anim_idx = ai
        self.frame_idx = fi
        if 0 <= ai < self.anim_list.count():
            self.anim_list.blockSignals(True)
            self.anim_list.setCurrentRow(ai)
            self.anim_list.blockSignals(False)
        self.canvas.clear_pending()
        self._refresh_frame_ui()
        return True

    def _flash_row(self, view, row, color="#ffe680"):
        """给列表 / 树的第 row 行闪一下底色（结构变动时提示）。

        QTreeWidget 用 topLevelItem(row) 且底色要带列号，QListWidget 用 item(row)，
        两者签名不同，这里分一下。
        """
        tree = hasattr(view, "topLevelItem")
        it = view.topLevelItem(row) if tree else view.item(row)
        if it is None:
            return
        old = it.background(0) if tree else it.background()
        col = QColor(color)

        def back():
            try:
                it2 = view.topLevelItem(row) if tree else view.item(row)
                if it2 is None:
                    return
                if tree:
                    it2.setBackground(0, old)
                else:
                    it2.setBackground(old)
            except RuntimeError:
                pass
        if tree:
            it.setBackground(0, col)
        else:
            it.setBackground(col)
        QTimer.singleShot(700, back)

    def _flash_widget(self, w, color="#ffe680"):
        """给整个控件闪一下底色（时间轴没有“某一格”的概念，整条闪）。"""
        old = w.styleSheet()
        w.setStyleSheet((old + "\n" if old else "") + f"* {{background:{color};}}")

        def back():
            try:
                w.setStyleSheet(old)
            except RuntimeError:
                pass
        QTimer.singleShot(700, back)

    def _sync_text_to_doc(self):
        """把编辑器里的文本倒回文档：txt 才是最终依据，界面只是辅助。

        anim / frame / Header 字段有增删时，时间轴、动画列表、Header 列表
        一并重建，并给变动处闪一下。
        """
        if self.doc is None:
            return False
        txt = self.txt_edit.toPlainText()
        if txt == self.doc.to_text():
            return False
        a = self.current_anim()
        old_name = a.name if a else None
        old_nf = len(a.frames()) if a else 0
        old_na = len(self.doc.anims)
        # 连“值”一起比：只比字段名字的话，改了个值列表就不会刷新，
        # 于是左列表里还显示着旧值（第 9 条反馈的那个现象）。
        old_hdr = [(c.lower(), tuple(v))
                   for _i, c, v in self.doc.header_entries()]
        self.doc.load_text(txt, self.doc.path)
        self.anim_idx = self._anim_index_by_name(old_name)
        new_a = self.current_anim()
        new_nf = len(new_a.frames()) if new_a else 0
        new_hdr = [(c.lower(), tuple(v))
                   for _i, c, v in self.doc.header_entries()]
        ch_frame = new_nf != old_nf
        ch_anim = len(self.doc.anims) != old_na
        ch_hdr = new_hdr != old_hdr
        self.frame_idx = min(self.frame_idx, max(0, new_nf - 1))
        self.timeline.clear_cache()
        if ch_anim or ch_hdr:
            self._refresh_all()          # 动画列表 / Header 列表重建
        else:
            self._refresh_frame_ui()
        self._undo_touch()               # 文本改动同样要能 Ctrl+Z 撤回
        self.lbl_dirty.setText("● 未保存" if self.dirty else "")
        # ---- 结构变了：对应列表闪一下 ----
        if ch_anim and self.anim_idx is not None:
            self._flash_row(self.anim_list, self.anim_idx)
        if ch_hdr:
            for i, h in enumerate(new_hdr):
                if i >= len(old_hdr) or old_hdr[i] != h:
                    self._flash_row(self.header_tree, i)
                    break
        if ch_frame:
            self._flash_widget(self.timeline)
        return True

    def _flash_anim_line(self, name):
        """对照模式：把右侧文本跳到某个 anim 所在行。"""
        if not self._cmp_mode or not name:
            return
        anims, _ = self._text_scan()
        for nm, line, _m in anims:
            if nm == name:
                self.txt_edit.flash_line(line, focus=False)
                return

    # ---------------- 撤销 / 重做 ----------------
    SEAL_MS = 400          # 连续修改停手多久算“一次”可回溯操作

    def _undo_state(self, label="", kind=MOD):
        a = self.current_anim()
        return State(self.doc.path if self.doc else "",
                     self.doc.to_text() if self.doc else "",
                     a.name if a else "", self.frame_idx, label, kind)

    def _undo_rebase(self):
        """切换实体 / 动画后：撤销栈保留，只把基准挪到新文本。

        切换本身不产生撤销记录，但栈里旧实体的记录仍在——
        回溯到别的实体时会自动切回去（见 _apply_state）。
        """
        if self.doc is None:
            self._undo_base = None
        else:
            self._undo.set_current(self._undo_state())
            self._undo_base = self._undo.current
        self._undo_pre = None
        self._seal_timer.stop()

    def _undo_reset(self):
        """重载 / 文本另存：文本换了一份，撤销栈整体作废。"""
        self._undo.clear(None)
        self._undo_rebase()

    def _undo_touch(self):
        """编辑刚发生：记下前态，并预约停手后封口。

        控件层（下拉框 / 数字滚轮）已经做了“鼠标移开才算一次”的延迟提交，
        这里的定时器再兜一层底：连续输入不会产生几十条撤销记录。
        """
        if self.doc is None:
            return
        if self._undo_pre is None:
            if self._undo_base is None:
                self._undo_base = self._undo_state()
            self._undo_pre = self._undo_base
        self._seal_timer.start(self.SEAL_MS)

    def _undo_seal(self):
        """把这一段修改封成一条撤销记录（内容没变则不记）。"""
        pre = self._undo_pre
        self._undo_pre = None
        if pre is None or self.doc is None:
            return
        self._undo.begin(pre)
        self._undo.commit(self._undo_state())
        self._undo_base = self._undo.current

    def _undo_available(self):
        return (not self._text_mode and not self._editing_locked
                and self.doc is not None)

    def undo_edit(self):
        if not self._undo_available():
            return
        self._undo_seal()
        st = self._undo.undo()
        if st is None:
            return
        self._undo_pre = None
        self._undo_base = st
        self._apply_state(st)

    def redo_edit(self):
        if not self._undo_available():
            return
        self._undo_seal()
        st = self._undo.redo()
        if st is None:
            return
        self._undo_pre = None
        self._undo_base = st
        self._apply_state(st)

    def _anim_index_by_name(self, name):
        """回溯时定位动画：找不到就退回第一个。"""
        if self.doc is None or not self.doc.anims:
            return None
        if not name:
            if self.anim_idx is not None and self.anim_idx < len(self.doc.anims):
                return self.anim_idx
            return 0
        for i, a in enumerate(self.doc.anims):
            if a.name == name:
                return i
        return 0

    def _row_of_path(self, path):
        for r, i in enumerate(self._ent_order):
            if 0 <= i < len(self.entities) and self.entities[i][1] == path:
                return r
        return None

    def _apply_state(self, st):
        """把撤销栈里的状态装回界面（必要时自动切换实体 / 动画）。"""
        if st is None or not st.path:
            return
        if self.doc is None or self.doc.path != st.path:
            row = self._row_of_path(st.path)
            if row is None:
                QMessageBox.information(self, "撤回", "目标实体已不在列表。")
                return
            idx = self._ent_order[row]
            name, path, issues = self.entities[idx]
            try:
                self.doc = EntityDoc(path)          # 先切实体，再倒回文本
            except Exception as ex:
                QMessageBox.warning(self, "解析失败", str(ex))
                return
            self._ent_name = name
            self._cur_issues = issues
            self.stop_play()
            self.timeline.clear_cache()
            self.ent_list.blockSignals(True)
            self.ent_list.setCurrentRow(row)
            self.ent_list.blockSignals(False)
        self.doc.load_text(st.text, st.path)
        self.anim_idx = self._anim_index_by_name(st.anim)
        a = self.current_anim()
        n = len(a.frames()) if a else 0
        self.frame_idx = min(max(0, st.frame), max(0, n - 1))
        # 脏标记不在这里置位：dirty 是属性，会拿当前文本和存盘快照逐字符比
        self.canvas.clear_pending()
        self.timeline.clear_cache()
        self._refresh_all()
        # 撤回 / 重做改的是文档，右侧文本必须跟着走：
        # 少了这一步就会出现“框已经没了，文本里还写着 attack”的不同步假象。
        self._sync_compare()

    def _txt_modified(self):
        return self.txt_edit.toPlainText() != self._txt_original

    def close_text(self):
        if not self._text_mode:
            return
        if self._txt_modified():
            r = QMessageBox.question(
                self, "未保存修改", "文本编辑有未保存的修改，确定放弃？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        self._exit_text_mode()

    def save_text(self):
        if self.doc is None:
            return False
        try:
            with open(self.doc.path, "w", encoding="utf-8") as fp:
                fp.write(self.txt_edit.toPlainText())
        except Exception as ex:
            QMessageBox.warning(self, "保存失败", str(ex))
            return False
        self._txt_original = self.txt_edit.toPlainText()
        self.lbl_txt_dirty.setText("")
        try:
            self.doc.load(self.doc.path)     # 先按存盘后的文件重建文档
        except Exception as ex:
            QMessageBox.warning(self, "重读失败", str(ex))
        self.dirty = False                   # 再以这份文本立为新基准
        self.timeline.clear_cache()
        self._refresh_all()
        self._undo_reset()         # 文本另存了一份，撤销栈作废
        return True

    def save_close_text(self):
        if self.save_text():
            self._exit_text_mode()

    def optimize_text(self):
        """一键优化：只在编辑器里规整格式，写文件仍需“保存”。"""
        if self.doc is None:
            return
        txt = self.txt_edit.toPlainText()
        new = format_bor_text(txt)
        if new == txt:
            QMessageBox.information(self, "一键优化", "文本已经是规整格式。")
            return
        cur = self.txt_edit.textCursor()
        self._txt_line = 0          # setPlainText 会把光标带回开头，先同步行号
        self.txt_edit.setPlainText(new)
        self.txt_edit.setTextCursor(cur)
        if self._cmp_mode:
            self._sync_text_to_doc()    # 对照模式下改动直接落回文档
        self.lbl_txt_dirty.setText("● 未保存（已优化）")
        QMessageBox.information(
            self, "一键优化",
            "已规整缩进/制表符/空行。\n这是编辑器内的改动，点“保存”才写入。")

    def save_hotkey(self):
        """Ctrl+S：在 OpenBOR 编辑器里保存当前角色 TXT（有改动必弹确认）。"""
        if self.doc is None:
            return
        if self._cmp_mode:
            self._sync_text_to_doc()     # 刚敲完还没换行的字也要算进去
        if self._text_mode:
            # 两个都干净才跳过：文本没动过、但动画面板改过文档时也要能存
            # （右侧文本此时与文档一致，存它就是存文档）。
            if not self._txt_modified() and not self.dirty:
                return                      # 无改动：不弹任何提示
            r = QMessageBox.question(
                self, "保存确认",
                f"将把修改写回：\n{self.doc.path}\n\n是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
            self.save_text()
            return
        if not self.dirty:
            return                          # 无改动：不弹任何提示
        self.save_entity()

    def _exit_text_mode(self):
        """退出文本编辑模式（内部用）：回到动画编辑。"""
        if self._mode != self.MODE_TEXT:
            return
        self._enter_mode(self.MODE_ANIM)

    # ---------------- 导入 / 扫描 ----------------
    def import_folder(self, folder=None):
        """导入 data 目录；folder 为空则弹窗选择。路径会记到本地，下次自动载入。"""
        if getattr(self, "_importing", False):
            return                     # 防止扫描中的 processEvents 触发重入
        if self._text_mode:
            QMessageBox.information(self, "文本编辑中", "请先关闭文本编辑。")
            return
        d = folder or QFileDialog.getExistingDirectory(
            self, "选择 OpenBOR DATA 文件夹")
        if not d:
            return
        mp = os.path.join(d, "models.txt")
        if not os.path.exists(mp):
            if folder:                 # 自动载入失败时静默跳过
                return
            QMessageBox.warning(self, "导入失败",
                                "没有 models.txt，已拒绝导入。")
            return
        self._last_dir = os.path.abspath(d)
        self._save_pref("last_dir", self._last_dir)
        self.data_dir = os.path.abspath(d)
        self.root_dirs = [os.path.dirname(self.data_dir), self.data_dir]
        self.models = ModelsTxt(mp)
        self.entities = []
        self.ent_list.clear()
        self._ent_order = []
        self._ent_type = {}          # 实体下标 -> type 字段（给过滤下拉用）
        self._deep_done = False
        self.doc = None
        self._ent_name = None
        self._cur_issues = []
        self._refresh_all()
        self._importing = True
        self.progress.setVisible(True)
        total = len(self.models.entities)
        self.progress.setRange(0, max(1, total))
        try:
            self._scan_entities(total)
        finally:
            self._importing = False
            self.progress.setVisible(False)
        self._rebuild_type_combo()
        if self._sort_alpha or getattr(self, "_type_filter", ""):
            # 字典序 / 类别过滤下扫描完再统一重排
            self._rebuild_ent_list()
        else:
            self._update_scan_label()

    def _scan_entities(self, total):
        """逐个解析实体；每扫完一个就加进列表，已扫描的实体立即可点击。"""
        for i, (kind, name, rel) in enumerate(self.models.entities):
            path = None
            for root in self.root_dirs:
                p = os.path.join(root, rel.replace("/", os.sep))
                if os.path.exists(p):
                    path = p
                    break
            typ = ""
            if path is None:
                issues = [(ERR, f"实体文件不存在: {rel}", None)]
            else:
                try:
                    doc = EntityDoc(path)
                    issues = validate_entity(doc, self.models, self.root_dirs)
                    t = doc.header_get("type")
                    typ = (t[0].lower() if t and t[0] else "")
                except Exception as ex:
                    issues = [(ERR, f"解析失败: {ex}", None)]
            self.entities.append((name, path, issues))
            self._ent_type[i] = typ
            self._ent_order.append(i)
            self.ent_list.addItem(self._ent_item(i))
            self.progress.setValue(i + 1)
            self.lbl_scan.setText(
                f"扫描中 {i + 1}/{total} …（已扫描的实体可直接点击）")
            QApplication.processEvents()

    # ---------------- 实体列表 ----------------
    def _ent_item(self, i):
        """按实体下标生成列表项（颜色反映错误/警告，UserRole 存下标）。"""
        name, path, issues = self.entities[i]
        it = QListWidgetItem(name)
        errs = [m for s, m, _ in issues if s == ERR]
        warns = [m for s, m, _ in issues if s == WARN]
        if errs:
            it.setBackground(ERR_BG)
            it.setForeground(ERR_FG)
        elif warns:
            it.setBackground(WARN_BG)
            it.setForeground(WARN_FG)
        it.setToolTip("\n".join(m for _, m, _ in issues) or "无问题")
        it.setData(Qt.UserRole, i)
        return it

    def _rebuild_ent_list(self):
        """按当前排序方式与类别过滤重建实体列表；项里存实体下标（UserRole）。"""
        order = list(range(len(self.entities)))
        f = getattr(self, "_type_filter", "") or ""
        if f:
            order = [i for i in order
                     if (self._ent_type.get(i, "") or "（无）") == f]
        if self._sort_alpha:
            order.sort(key=lambda i: (self.entities[i][0] or "").lower())
        self._ent_order = order
        self.ent_list.blockSignals(True)
        sel = self.doc.path if self.doc else None
        self.ent_list.clear()
        sel_row = -1
        for r, i in enumerate(order):
            self.ent_list.addItem(self._ent_item(i))
            if self.entities[i][1] == sel:
                sel_row = r
        self.ent_list.blockSignals(False)
        if sel_row >= 0:
            self.ent_list.blockSignals(True)
            self.ent_list.setCurrentRow(sel_row)
            self.ent_list.blockSignals(False)
        self._update_scan_label()

    def _update_scan_label(self):
        total = len(self.entities)
        shown = self.ent_list.count()
        f = getattr(self, "_type_filter", "") or ""
        self.lbl_scan.setText(
            f"共 {total} 个实体" if not f
            else f"{f}：{shown} 个（共 {total} 个实体）")

    def _rebuild_type_combo(self):
        """按扫出来的 type 重建过滤下拉（保留当前选择）。"""
        cur = getattr(self, "_type_filter", "") or ""
        types = sorted({(t or "（无）") for t in self._ent_type.values()})
        self.cmb_type.blockSignals(True)
        self.cmb_type.clear()
        self.cmb_type.addItem("全部")
        for t in types:
            self.cmb_type.addItem(t)
        idx = self.cmb_type.findText(cur) if cur else 0
        self.cmb_type.setCurrentIndex(max(0, idx))
        self.cmb_type.blockSignals(False)

    def _apply_type_filter(self):
        """过滤下拉：只看某一类实体（「全部」= 不过滤）。"""
        t = self.cmb_type.currentText()
        self._type_filter = "" if t in ("全部", "", None) else t
        self._rebuild_ent_list()

    def reload_folder(self):
        """重新载入当前 data 目录：重扫 models.txt + 重新检错。"""
        d = self.data_dir
        if not d:
            QMessageBox.information(self, "刷新", "还没导入 data 目录。")
            return
        if not os.path.exists(os.path.join(d, "models.txt")):
            QMessageBox.warning(self, "刷新",
                                f"目录里没有 models.txt 了：\n{d}")
            return
        if self._text_mode:
            QMessageBox.information(self, "刷新",
                                    "请先关闭文本编辑再刷新。")
            return
        # 有未保存改动先问一句，免得一键把辛苦改的东西冲掉
        if self.dirty:
            r = QMessageBox.question(
                self, "刷新",
                "当前实体有未保存的修改，刷新会重新载入并丢弃它们。继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                return
        keep = self.doc.path if self.doc else None
        self.import_folder(d)
        self._rebuild_type_combo()
        self._apply_type_filter()
        if keep is not None:
            for r in range(self.ent_list.count()):
                it = self.ent_list.item(r)
                i = it.data(Qt.UserRole)
                if i is not None and self.entities[i][1] == keep:
                    self.ent_list.setCurrentRow(r)
                    break

    def _toggle_sort(self):
        self._sort_alpha = not self._sort_alpha
        self.btn_sort.setChecked(self._sort_alpha)
        self._rebuild_ent_list()
        n = len(self.entities)
        self.lbl_scan.setText(
            f"共 {n} 个实体（{'字典序' if self._sort_alpha else '文件顺序'}）")

    def deep_check(self):
        """深度检错：文件引用存在性 + @cmd 函数来源解析（较慢）。"""
        if not self.entities:
            QMessageBox.information(self, "深度检错", "还没导入实体。")
            return
        r = QMessageBox.question(
            self, "深度检错",
            "逐个实体检查：\n"
            "① 引用的图片是否存在（缺失＝错误）\n"
            "② 引用的音频是否存在（缺失＝警告）\n"
            "③ @cmd 调用的函数能否在内置函数或实体脚本（含 #include\n"
            "　 递归子脚本）里找到（找不到＝错误）\n\n"
            "实体较多时较慢，是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        n = len(self.entities)
        self.progress.setVisible(True)
        self.progress.setRange(0, n)
        nerr = nwarn = 0
        for k in range(n):
            name, path, _ = self.entities[k]
            if path is None:
                self.progress.setValue(k + 1)
                continue
            try:
                doc = EntityDoc(path)
                issues = validate_entity(doc, self.models, self.root_dirs) \
                    + deep_validate(doc, self.root_dirs, self.models)
            except Exception as ex:
                issues = [(ERR, f"深度检错失败: {ex}", None)]
            self.entities[k] = (name, path, issues)
            nerr += sum(1 for s, _, _ in issues if s == ERR)
            nwarn += sum(1 for s, _, _ in issues if s == WARN)
            self.progress.setValue(k + 1)
            QApplication.processEvents()
        self.progress.setVisible(False)
        self._deep_done = True
        self._rebuild_ent_list()
        if self.doc is not None:
            for nm, p, issues in self.entities:
                if p == self.doc.path:
                    self._cur_issues = issues
                    break
        self._refresh_all()
        self.lbl_scan.setText(
            f"共 {n} 个实体　深度检错：❌ {nerr} / ⚠️ {nwarn}")
        QMessageBox.information(
            self, "深度检错完成",
            f"共 {n} 个实体：错误 {nerr} / 警告 {nwarn}。")

    # ---------------- 实体选择 ----------------
    def _revert_entity_row(self):
        self.ent_list.blockSignals(True)
        try:
            p = self.doc.path if self.doc else None
            for r, i in enumerate(self._ent_order):
                if self.entities[i][1] == p:
                    self.ent_list.setCurrentRow(r)
                    break
        finally:
            self.ent_list.blockSignals(False)

    def _ask_three(self, title, text, b1, b2, b3="取消"):
        """三选一询问：返回 0/1/2 分别对应 b1/b2/b3。"""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Question)
        ba = box.addButton(b1, QMessageBox.AcceptRole)
        bb = box.addButton(b2, QMessageBox.DestructiveRole)
        box.addButton(b3, QMessageBox.RejectRole)
        box.setDefaultButton(ba)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is ba:
            return 0
        if clicked is bb:
            return 1
        return 2

    def _on_entity_selected(self, row):
        if not (0 <= row < len(self._ent_order)):
            return
        idx = self._ent_order[row]
        if self._text_mode:
            if self._txt_modified():
                r = self._ask_three(
                    "未保存修改",
                    f"文本编辑有未保存的修改（{os.path.basename(self.doc.path)}）。",
                    "保存并切换", "放弃修改", "取消")
                if r == 2:
                    self._revert_entity_row()
                    return
                if r == 0 and not self.save_text():
                    self._revert_entity_row()
                    return
            self._enter_mode(self.MODE_ANIM)
        else:
            if self._cmp_mode:
                self._sync_text_to_doc()   # 刚敲完还没换行的字也算改动
            self._undo_seal()          # 切换实体前先把未封口的改动封上
        if self.dirty:
            r = QMessageBox.question(
                self, "未保存修改",
                "当前实体有未保存的修改，切换后将丢弃。是否继续？")
            if r != QMessageBox.Yes:
                self._revert_entity_row()
                return
        self._load_entity_row(idx)

    @staticmethod
    def _default_anim_index(doc):
        """默认选中 idle 动画（没有就取第一个）。"""
        if not doc or not doc.anims:
            return None
        for i, a in enumerate(doc.anims):
            if a.name.lower() in ("idle", "idle1"):
                return i
        return 0

    def _load_entity_row(self, idx):
        """装载第 idx 个实体（不含“未保存”提示，供选择 / 撤回共用）。"""
        name, path, issues = self.entities[idx]
        self._ent_name = name
        self._cur_issues = issues
        self.stop_play()
        if path is None:
            self.doc = None
            self.anim_idx = None
            self.frame_idx = 0
            self.dirty = False
            self._refresh_all()
            self._undo_rebase()
            return
        try:
            self.doc = EntityDoc(path)
        except Exception as ex:
            QMessageBox.warning(self, "解析失败", str(ex))
            self.doc = None
            self._refresh_all()
            self._undo_rebase()
            return
        self.anim_idx = self._default_anim_index(self.doc)
        self.frame_idx = 0
        self.dirty = False
        self.canvas.clear_pending()
        self.timeline.clear_cache()
        self._refresh_all()
        self._undo_rebase()
        if self._cmp_mode:
            self._load_text_into_editor()

    # ---------------- 刷新 ----------------
    def _show_issues(self, issues, none_text="✅ 无问题"):
        """错误信息红色加粗，警告琥珀色。"""
        if not issues:
            self.issues.setHtml('<div style="color:#3f8f5f">%s</div>'
                                % _esc(none_text))
            return
        self.issues.setHtml("".join(_sev_html(s, m) for s, m, _ in issues))

    @staticmethod
    def _has_atk_box(args):
        """攻击框 x,y,w,h 里是否有非零值。"""
        vals = list(args or [])[:4]
        try:
            return any(float(v) != 0.0 for v in vals)
        except ValueError:
            return bool(vals)

    def _text_scan(self):
        """把编辑器文本扫成两个列表（用于文本模式下的双向同步）。

        anims:   [(名称, 行号, 标记)]     标记含 A=有攻击框 / f=有脚本
        headers: [(字段名, 值, 行号)]
        """
        lines = self.txt_edit.toPlainText().splitlines()
        anims, headers = [], []
        cur = None
        cur_atk = cur_scr = False
        in_script = False
        for i, raw in enumerate(lines):
            low = raw.strip().lower()
            tk = low.split()
            if in_script:
                if tk and is_script_end(tk[0]):
                    in_script = False
                continue
            # 块开头：@script，或 script @script（整块是 script 字段的值）
            if tk and is_script_start(tk[0], tk[1:]):
                in_script = True
                if cur is not None:
                    cur_scr = True
                continue
            t = raw.split()
            if not t:
                continue
            w = t[0].lower()
            if w == "anim":
                if cur is not None:
                    cur[2] = ("A" if cur_atk else "") + ("f" if cur_scr else "")
                cur = [t[1] if len(t) > 1 else "", i, ""]
                anims.append(cur)
                cur_atk = cur_scr = False
            elif cur is None:
                headers.append((t[0], " ".join(t[1:]), i))
            else:
                if low.startswith("@cmd"):
                    cur_scr = True
                elif re.match(r"attack\d*$", w) or w in SPECIAL_ATTACK_TYPES:
                    if self._has_atk_box(t[1:5]):
                        cur_atk = True
        if cur is not None:
            cur[2] = ("A" if cur_atk else "") + ("f" if cur_scr else "")
        return anims, headers

    def _refresh_text_lists(self):
        """文本模式：动画列表 / Header 列表直接由编辑器文本生成。"""
        anims, headers = self._text_scan()
        self.anim_list.blockSignals(True)
        self.anim_list.clear()
        for nm, line, marks in anims:
            it = QListWidgetItem(nm)
            it.setData(Qt.UserRole, marks)
            it.setData(Qt.UserRole + 1, line)
            self.anim_list.addItem(it)
        if self.anim_idx is not None and 0 <= self.anim_idx < len(anims):
            self.anim_list.setCurrentRow(self.anim_idx)
        self.anim_list.blockSignals(False)
        self.header_tree.blockSignals(True)
        self.header_tree.clear()
        for cmd, val, line in headers:
            it = QTreeWidgetItem(self.header_tree, [cmd, val])
            it.setData(0, Qt.UserRole, line)
        self.header_tree.blockSignals(False)

    def _refresh_all(self):
        doc = self.doc
        self.lbl_dirty.setText("● 未保存" if self.dirty else "")
        if self._text_mode and doc is not None:
            self._refresh_text_lists()
            return
        if doc is None:
            if self._ent_name:
                self.lbl_entity.setText(f"<b>{self._ent_name}</b>"
                                         f"（实体文件不存在）")
                self._show_issues(self._cur_issues, "无问题")
            else:
                self.lbl_entity.setText("（未选择实体）")
                self._show_issues([], "")
            self.anim_list.clear()
            self.header_tree.clear()
            self.canvas.show_frame(None, None)
            self.timeline.set_attack_enabled(False)
            self.timeline.update()
            self._refresh_frame_ui()
            return
        name = doc.header_get("name")
        self.lbl_entity.setText(
            f"<b>{name[0] if name else '（缺）'}　</b>"
            f"<b>({os.path.basename(doc.path)})</b>"
            )
        try:
            issues = validate_entity(doc, self.models, self.root_dirs)
        except Exception as ex:
            issues = [(ERR, f"校验失败: {ex}", None)]
        self._cur_issues = issues
        # 每校验一次就同步一次实体列表行色，否则“问题栏已无问题、
        # 列表还高亮”会一直挂着。
        self._apply_validation_colors(issues)
        anim_sev, header_sev = {}, {}
        for sev, m, key in issues:
            if key:
                kind, k = key
                d = anim_sev if kind == "anim" else header_sev
                if d.get(k) != ERR:
                    d[k] = sev
        self.anim_list.blockSignals(True)
        self.anim_list.clear()
        for a in doc.anims:
            it = QListWidgetItem(a.name)
            sev = anim_sev.get(a.name.lower())
            if sev == ERR:
                it.setBackground(ERR_BG)
                it.setForeground(ERR_FG)
            elif sev == WARN:
                it.setBackground(WARN_BG)
                it.setForeground(WARN_FG)
            frames = a.frames()
            marks = ""
            if any(fr["attack"] and self._has_atk_box(fr["attack"][1])
                   for fr in frames):
                marks += "A"
            if any(fr.get("script") or fr.get("atcmd") for fr in frames):
                marks += "f"
            it.setData(Qt.UserRole, marks)
            self.anim_list.addItem(it)
        if self.anim_idx is not None and 0 <= self.anim_idx < len(doc.anims):
            self.anim_list.setCurrentRow(self.anim_idx)
        self.anim_list.blockSignals(False)
        self.header_tree.clear()
        for idx, cmd, args in doc.header_entries():
            it = QTreeWidgetItem(self.header_tree, [cmd, " ".join(args)])
            it.setData(0, Qt.UserRole, idx)     # 文本里的行号，供跳转 / 判定用
            sev = header_sev.get(cmd.lower())
            if sev == ERR:
                it.setBackground(0, ERR_BG)
                it.setForeground(0, ERR_FG)
            elif sev == WARN:
                it.setBackground(0, WARN_BG)
                it.setForeground(0, WARN_FG)
        self._show_issues(issues)
        self._refresh_frame_ui()

    def current_anim(self):
        if self.doc is None or self.anim_idx is None:
            return None
        if 0 <= self.anim_idx < len(self.doc.anims):
            return self.doc.anims[self.anim_idx]
        return None

    def current_frame_info(self):
        a = self.current_anim()
        if a is None:
            return None
        frames = a.frames()
        if 0 <= self.frame_idx < len(frames):
            return frames[self.frame_idx]
        return None

    def anim_markers(self):
        out = {"jumpframe": [], "dropframe": [], "landframe": [],
               "sound": set(), "spawn": set()}
        a = self.current_anim()
        if a is None:
            return out
        in_script = False
        for ln in a.lines:
            low = ln.raw.strip().lower()
            if in_script:                       # 脚本内容不参与标记解析
                if low.startswith("@end_script"):
                    in_script = False
                continue
            if low.startswith("@script"):
                in_script = True
                continue
            c, args = ln.cmd(), ln.args()
            if c in ("jumpframe", "dropframe", "landframe") and args:
                try:
                    out[c].append(int(float(args[0])))
                except ValueError:
                    pass
        for i, fr in enumerate(a.frames()):
            if any(c == "sound" for _, c, _ in fr["extras"]):
                out["sound"].add(i)
        out["spawn"] = spawn_index(a)
        return out

    # ---------------- 动画显示增强：位移 / 跳帧 / 生成物 ----------------
    def _sim_image(self, rel):
        """按帧路径取图（带缓存）；失败返回 None。"""
        if not rel or rel.lower() == "none":
            return None
        path = resolve_frame_path(self.root_dirs, rel)
        if not path:
            return None
        img = self._sim_imgs.get(path)
        if img is None:
            try:
                raw = iu.load(path)
                # OpenBOR：8 位图透明色 = 调色板第 1 个色（index 0）。
                # 判据是“第 1 个色有没有被设成透明”，见 image_utils.openbor_rgba
                img = iu.pil_to_qimage(iu.openbor_rgba(raw))
            except Exception:
                img = None
            self._sim_imgs[path] = img
            if len(self._sim_imgs) > 400:
                self._sim_imgs.clear()
        return img

    def _entity_doc(self, name):
        """按名字在已导入的实体里找 txt 并解析（带缓存）。"""
        if not name:
            return None
        key = (name or "").lower()
        if key in self._ext_docs:
            return self._ext_docs[key]
        doc = None
        for n, p, _ in self.entities:
            if (n or "").lower() == key and p:
                try:
                    doc = EntityDoc(p)
                except Exception:
                    doc = None
                break
        self._ext_docs[key] = doc
        return doc

    @staticmethod
    def _find_anim(doc, name):
        if doc is None or not name:
            return None
        key = (name or "").lower()
        for a in doc.anims:
            if a.name.lower() == key:
                return a
        for a in doc.anims:                     # 退而求其次：前缀匹配
            if a.name.lower().startswith(key):
                return a
        return None

    def _actor_pose(self, doc, anim_name, idx):
        """取“某个实体的某个动画第 idx 帧”的 (图, ox, oy)；没有就 None。"""
        if doc is None:
            return None
        a = self._find_anim(doc, anim_name)
        if a is None and anim_name:
            return None
        if a is None:
            a = doc.anims[0] if doc.anims else None
        if a is None:
            return None
        frames = a.frames()
        if not frames:
            return None
        fr = frames[idx % len(frames)]
        img = self._sim_image(fr["path"])
        if img is None:
            return None
        off = fr["offset"] or []
        try:
            ox = int(float(off[0])) if off else 0
            oy = int(float(off[1])) if len(off) > 1 else 0
        except (ValueError, IndexError):
            ox, oy = 0, 0
        return img, ox, oy

    def _spawn_pos(self, tk, fr, sp, fidx):
        """生成物在场景里的落点 (x, z)：主实体生成那一帧的 Offset 点 +
        当时的位移 + spawn 命令给的 x / z 偏移（y 是离地高度，往上减）。"""
        off = fr["offset"] or []
        try:
            ox = int(float(off[0])) if off else 0
            oy = int(float(off[1])) if len(off) > 1 else 0
        except (ValueError, IndexError):
            ox, oy = 0, 0
        mdx = mdz = 0.0
        if tk is not None and self._play_mode is not None:
            s = tk.at(fidx)
            mdx, mdz = s[0], s[1]
        return (ox + mdx + sp.get("x", 0.0),
                oy + mdz + sp.get("z", 0.0) - sp.get("y", 0.0))

    def _update_sim(self):
        """按当前帧刷新画布：位移 / 跳跃高度 / 生成物。

        拖动时间轴（没在播放）时只看当前帧：命中生成帧就标个位置 +
        半透明画生成物的 idle 首帧。播放时则完整模拟：该生成就生成、
        循环回到起点或停止播放就全部消失。
        """
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None:
            self.canvas.set_motion(None)
            self.canvas.set_overlay([], [])
            return
        frames = a.frames()
        i = max(0, min(self.frame_idx, len(frames) - 1))
        playing = self._play_mode is not None
        tk = MotionTrack(a, self._sim_gravity)
        dx, dz, dy = tk.at(i)[:3]
        # 播放**和**拖时间轴都显示位移：拖帧时也要能看到当前帧因为前面
        # 的 move / movea / movez / jumpframe 偏移到了哪里（含那条粗箭头）。
        self.canvas.set_motion((dx, dz, dy))
        spawns = spawn_frames(a)
        if not spawns:
            self.canvas.set_overlay([], [])
            return
        loop0 = self._loop_start() if self._play_mode == "loop" else 0
        ghosts, marks = [], []
        for f in sorted(spawns):
            if playing:
                if f > i or f < loop0:
                    continue
            elif f != i:
                continue
            sp_fr = frames[f] if f < len(frames) else fr
            for sp in spawns[f]:
                gx, gz = self._spawn_pos(tk, sp_fr, sp, f)
                marks.append((gx, gz, sp["name"]))
                doc = self._entity_doc(sp["name"])
                if doc is None:
                    continue
                # 播放中按帧推进它的 idle；只是拖到这一帧就只画第一帧
                step = max(0, i - f) if playing else 0
                pose = self._actor_pose(doc, "idle", step)
                if pose is None:
                    continue
                img, ox, oy = pose
                ghosts.append({"img": img, "ox": ox, "oy": oy,
                               "x": gx, "z": gz,
                               "alpha": 0.9 if playing else 0.5,
                               "label": sp["name"]})
        self.canvas.set_overlay(ghosts, marks)

    def _set_spins(self, spins, values, yellow=False):
        for s, v in zip(spins, values):
            s.setValue(v)
            s.setStyleSheet(YELLOW_BG if yellow else "")

    def _set_ctrl_enabled(self, on):
        """未选中动画 / 播放中：编辑区每个控件（含勾选框）逐个禁用/恢复。"""
        on = bool(on) and not self._editing_locked
        self.ctrl_scroll.setEnabled(on)
        root = self.ctrl_scroll.widget()
        if root is not None:
            for w in root.findChildren(QWidget):
                if w.isEnabled() != on:      # 只在状态变化时才改，减少抖动
                    w.setEnabled(on)
        for i in range(self.tbar.count()):
            w = self.tbar.itemAt(i).widget()
            if w is not None:
                w.setEnabled(on)

    # ---- 框线颜色 ----
    def _pick_bbox_color(self, c):
        self.bbox_color = QColor(c)
        self._save_pref("bbox_color", self.bbox_color.name())
        self.canvas.update()

    def _pick_attack_color(self, c):
        self.attack_color = QColor(c)
        self._save_pref("attack_color", self.attack_color.name())
        self.canvas.update()

    def _refresh_frame_ui(self):
        self._loading = True
        # 整段回写都放进临界区：期间所有“值变了”都不是用户改的，
        # 延时提交控件不许登记、不许提交（否则定时器一响就把同样的值
        # 写回文档，文本被规范化后就算一次修改）。
        refresh_begin()
        try:
            fr = self.current_frame_info()
            a = self.current_anim()
            has = fr is not None
            anim_ok = a is not None
            self._set_ctrl_enabled(anim_ok)
            n_frames = len(a.frames()) if a else 0
            # delay 同样有继承性：本帧没写、沿用前面帧的值 → 淡黄底
            delay_inherit = has and fr["delay"] is not None \
                and not fr["own"]["delay"]
            with blocked(self.sp_delay):
                self.sp_delay.setValue(
                    _to_int(fr["delay"][0]) if has and fr["delay"] else 0)
                self.sp_delay.setStyleSheet(YELLOW_BG if delay_inherit else "")
            off_inherit = has and fr["offset"] is not None \
                and not fr["own"]["offset"]
            self._set_spins([self.sp_ox, self.sp_oy],
                            [_to_int(fr["offset"][0]) if has and fr["offset"] else 0,
                             _to_int(fr["offset"][1]) if has and fr["offset"] else 0],
                            off_inherit)
            bb = [_to_int(v) for v in (fr["bbox"] or [])[:5]] if has else []
            bb += [0] * (5 - len(bb))
            bb_inherit = has and fr["bbox"] is not None and not fr["own"]["bbox"]
            self._set_spins(self.sp_bbox, bb, bb_inherit)
            _, ln = a.find_cmd("loop") if a else (None, None)
            looping = ln is not None and ln.args() and ln.args()[0] == "1"
            self.btn_loop.setChecked(looping)
            self.btn_loop.setText("loop ✔" if looping else "loop ❌")
            largs = (ln.args() + ["0", "0"])[1:3] if ln else ["0", "0"]
            self.sp_loop_s.setValue(_to_int(largs[0]))
            self.sp_loop_e.setValue(_to_int(largs[1]))
            self.sp_loop_s.setEnabled(looping)
            self.sp_loop_e.setEnabled(looping)
            for f in self.anim_fields:
                _, ln = a.find_cmd(f.cmd) if a else (None, None)
                f.refresh(ln.args() if ln else None)
            ac, aa = (fr["attack"] if has and fr["attack"] else (None, None))
            special = ac if ac in SPECIAL_ATTACK_TYPES else None
            cleared = ac == "attack" and aa == ["0"]
            # 回写期间屏蔽信号：避免在滚轮事件里把下拉框索引改回去
            self.cmb_atk_special.blockSignals(True)
            self.cmb_atk_special.setCurrentText(special or "无")
            self.cmb_atk_special.blockSignals(False)
            with blocked(self.ck_atk_special):
                self.ck_atk_special.setChecked(special is not None)
            self.cmb_atk_special.setEnabled(special is not None)
            num = "".join(ch for ch in (ac or "") if ch.isdigit())
            self.sp_atk_no.setValue(int(num) if num else 1)
            self.sp_atk_no.setEnabled(special is None)
            self.ck_atk_kd.setEnabled(special != "blast")
            vals = (["0"] * 10 if cleared or aa is None
                    else (list(aa) + ["0"] * 10)[:10])
            atk_vals = [_to_int(v) for v in vals[:4]]
            atk_inherit = has and fr["attack"] is not None and not cleared \
                and not fr["own"]["attack"]
            self._set_spins(self.sp_atk, atk_vals, atk_inherit)
            self.sp_atk_dmg.setStyleSheet(YELLOW_BG if atk_inherit else "")
            self.sp_atk_z.setStyleSheet(YELLOW_BG if atk_inherit else "")
            try:
                self.sp_atk_dmg.setValue(float(vals[4]))
            except ValueError:
                self.sp_atk_dmg.setValue(0)
            self.ck_atk_kd.setChecked(_to_int(vals[5]) > 0)
            self.ck_atk_block.setChecked(_to_int(vals[6]) > 0)
            self.ck_atk_noflash.setChecked(_to_int(vals[7]) > 0)
            self.sp_atk_pause.setValue(_to_int(vals[8]))
            self.sp_atk_z.setValue(_to_int(vals[9]))
            dm_map = (fr.get("dm") or {}) if has else {}
            legacy = "" in dm_map              # "" 键 = 一整行 10 个整数
            self._dm_new = not legacy          # 检测到老写法才切成老写法
            self._dm_mode_look()
            self.ck_nodm.setChecked(bool(has and fr["nodm"]))
            _dmwarns = []
            # 回写期间屏蔽信号：不然延时提交控件会把回写的值记成
            # “用户改值”，定时器一响就写回文档（“没动过却提示未保存”）
            with blocked(*self._dm_widgets):
                if legacy:
                    self._dm_fill_legacy(
                        (fr["drawmethod"] or []) if has else [], _dmwarns)
                else:
                    self._dm_fill_new(dm_map, _dmwarns)
            self.lbl_dm_warn.setText(
                "⚠ " + "；".join(_dmwarns[:3]) if _dmwarns else "")
            self._dm_sync_enable()
            for cmd, (ck, spins) in self._jf_rows.items():
                _, ln = a.find_cmd(cmd) if a else (None, None)
                args = (ln.args() + ["0"] * len(spins))[:len(spins)] if ln \
                    else ["0"] * len(spins)
                # 回写期间屏蔽信号：否则 setValue / setChecked 会被延时提交
                # 控件记成“待提交”，定时器一响就把同样的值写回文档并置脏，
                # 表现为“什么都没改却提示未保存”。
                with blocked(ck, *spins):
                    ck.setChecked(ln is not None)
                    spins[0].setRange(0, max(0, n_frames))
                    for s, v in zip(spins, args):
                        s.setValue(_to_int(v))
                        s.setEnabled(ln is not None)
            extras = {}
            if fr:
                for _, c, args in fr["extras"]:
                    extras[c] = args
            for f in self.frame_fields + self.atk_fields:
                if f.frame_level:
                    # 帧级附加命令（move / movea / movez …）和 delay 一样
                    # 会往后继承：本帧没写但前面帧写了 → 刷淡黄底
                    allv = fr["extras_all"] if fr else {}
                    own = fr["extras_own"] if fr else set()
                    f.refresh(allv.get(f.cmd),
                              inherit=f.cmd in allv and f.cmd not in own)
                else:
                    f.refresh(extras.get(f.cmd))
        finally:
            refresh_end()
            self._loading = False
        if fr:
            rel = fr["path"]
            resolved = resolve_frame_path(self.root_dirs, rel) \
                if rel.lower() != "none" else None
            self.canvas.show_frame(rel, resolved)
        else:
            self.canvas.show_frame(None, None)
        self.timeline.ensure_visible(self.frame_idx)
        self.timeline.set_attack_enabled(anim_has_attack(a))
        self.timeline.update()

    def _on_anim_selected(self, row):
        if self.doc is None or row < 0:
            return
        if self._text_mode:
            # 文本模式：只把光标跳到对应 anim 行并闪烁提示
            it = self.anim_list.item(row)
            if it is None:
                return
            self.anim_idx = row
            line = it.data(Qt.UserRole + 1)
            if line is not None:
                self.txt_edit.flash_line(int(line))
            return
        self._undo_seal()          # 切换动画本身不进撤销栈，只给上一段封口
        self.stop_play()
        self.anim_idx = row
        self.frame_idx = 0
        self.canvas.clear_pending()
        self._refresh_frame_ui()
        a = self.current_anim()
        if a is not None:
            self._flash_anim_line(a.name)

    # ---- 文本模式：点击 Header 字段跳到对应行 ----
    def _on_header_item_activated(self):
        if self._mode == self.MODE_ANIM:
            return
        it = self.header_tree.currentItem()
        if it is None:
            return
        line = it.data(0, Qt.UserRole)
        if line is not None:
            self.txt_edit.flash_line(int(line), focus=not self._cmp_mode)

    def _on_txt_changed(self):
        if not self._text_mode:
            return
        self.lbl_txt_dirty.setText("● 未保存" if self._txt_modified() else "")
        self._refresh_text_lists()

    def set_frame_idx(self, i):
        self.frame_idx = i
        self._refresh_frame_ui()
        self._update_sim()

    # ---------------- 脏标记：一律真的比对文本 ----------------
    @property
    def dirty(self):
        """当前实体是否真有未保存修改：把文档重新序列化，与上次装载 / 存盘
        时留下的快照逐字符比对。

        绝不能只用一个布尔开关 —— 刷新界面回写控件时也会把它点成 True，
        于是鼠标在实体列表上划一下都弹“未保存”确认。凡是要弹窗的地方都
        读这个属性，读到的一定是“确实改了”。
        """
        if self.doc is None:
            return False
        try:
            return self.doc.to_text() != self._saved_text
        except Exception:
            return False

    @dirty.setter
    def dirty(self, on):
        """False = 把当前文档文本立为新基准（装载 / 存盘 / 撤销栈作废时）。
        True 只是写法上的兼容：真实结果仍由 getter 逐字符比对决定。"""
        if not on:
            self._saved_text = self.doc.to_text() if self.doc is not None else ""

    def _mark_dirty(self):
        """所有文档改动的统一出口：先真的比对一次，确实变了才标脏、
        记撤销点、同步对照模式。界面回写控件同样会走到这里，靠这一步
        把“什么都没改”挡回去。"""
        if not self.dirty:              # 属性：真的比对，不是布尔开关
            return
        self.lbl_dirty.setText("● 未保存")
        if self._text_mode:
            self.lbl_txt_dirty.setText("")
        self._undo_touch()
        self._sync_compare()

    # ---------------- 低层编辑 ----------------
    def _set_frame_prop(self, cmdname, args):
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None or not self._guard_edit(silent=True):
            return False
        fi = fr["line_idx"]
        prev = -1
        for i in range(fi - 1, -1, -1):
            if a.lines[i].cmd() == "frame":
                prev = i
                break
        for i in range(fi - 1, prev, -1):
            if a.lines[i].cmd() == cmdname:
                # in_anim=True：按“一键优化”的规范重排制表符与空格
                a.lines[i].set_args(cmdname, args, in_anim=True)
                self._mark_dirty()
                return True
        a.lines.insert(fi, Line(canon_line(cmdname, args, in_anim=True)))
        self._mark_dirty()
        return True

    def _del_frame_cmd(self, cmdname):
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None or not self._guard_edit(silent=True):
            return
        fi = fr["line_idx"]
        prev = -1
        for i in range(fi - 1, -1, -1):
            if a.lines[i].cmd() == "frame":
                prev = i
                break
        for i in range(fi - 1, prev, -1):
            if a.lines[i].cmd() == cmdname:
                del a.lines[i]
                self._mark_dirty()
                return

    def _set_anim_cmd(self, cmd, args):
        a = self.current_anim()
        if a is None or not self._guard_edit(silent=True):
            return
        idx, ln = a.find_cmd(cmd)
        if ln is not None:
            if args is None:
                del a.lines[idx]
            else:
                ln.set_args(cmd, args, in_anim=True)
        elif args is not None:
            a.lines.insert(0, Line(canon_line(cmd, args, in_anim=True)))
        self._mark_dirty()

    # ---------------- 字段应用 ----------------
    def apply_offset(self, x, y):
        if self._set_frame_prop("offset", [str(x), str(y)]):
            self._refresh_frame_ui()

    def apply_pending_offset(self):
        if self.canvas._point is None or not self._guard_edit():
            return
        x, y = self.canvas._point
        if self._set_frame_prop("offset", [str(x), str(y)]):
            self.canvas.clear_pending()
            self._refresh_frame_ui()

    def apply_pending_bbox(self):
        if self.canvas._rect is None or not self._guard_edit():
            return
        x, y, w, h = self.canvas._rect
        z = self.sp_bbox[4].value()
        if self._set_frame_prop("bbox", [str(x), str(y), str(w), str(h), str(z)]):
            self.canvas.clear_pending()
            self._refresh_frame_ui()

    def apply_pending_attack(self):
        if self.canvas._rect is None or not self._guard_edit():
            return
        x, y, w, h = self.canvas._rect
        for s, v in zip(self.sp_atk, (x, y, w, h)):
            s.setValue(v)
        self._write_attack()
        self.canvas.clear_pending()

    def _offset_changed(self):
        if self._loading or not self.current_frame_info():
            return
        self.apply_offset(self.sp_ox.value(), self.sp_oy.value())

    def _delay_changed(self):
        if self._loading or not self.current_frame_info():
            return
        if self._set_frame_prop("delay", [str(self.sp_delay.value())]):
            self._refresh_frame_ui()

    def _bbox_changed(self):
        if self._loading or not self.current_frame_info():
            return
        vals = [str(s.value()) for s in self.sp_bbox]
        if self._set_frame_prop("bbox", vals):
            self._refresh_frame_ui()

    def _attack_changed(self):
        if self._loading:
            return
        self._write_attack()

    def _atk_special_toggled(self, _state=None):
        """勾选框一变就立刻生效：勾上用下拉框里的类型，取消则回普通 attack{#}。"""
        if self._loading:
            return
        on = self.ck_atk_special.isChecked()
        self.cmb_atk_special.setEnabled(on)
        self.sp_atk_no.setEnabled(not on)
        # 刚勾上却还没挑类型：直接给第一项，免得勾了半天没变化
        if on and self.cmb_atk_special.currentText() in ("无", "", None) \
                and self.cmb_atk_special.count() > 1:
            self.cmb_atk_special.setCurrentIndex(1)
        self._write_attack()

    def _set_frame_attack(self, cmd, args):
        """写本帧的攻击框：整个 attack 家族**只保留一行**。

        改类型（attack# → blast，或 blast → freeze）时是把原来那一行
        **换掉**，而不是在下面再加一行——同一个帧只能有一种攻击框。
        """
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None or not self._guard_edit(silent=True):
            return False
        fi = fr["line_idx"]
        prev = -1
        for i in range(fi - 1, -1, -1):
            if a.lines[i].cmd() == "frame":
                prev = i
                break
        hits = [i for i in range(fi - 1, prev, -1)
                if a.lines[i].cmd().startswith("attack")
                or a.lines[i].cmd() in SPECIAL_ATTACK_TYPES]
        if hits:
            # 第一行改写成新命令，其余同族的删掉
            a.lines[hits[0]].set_args(cmd, args, in_anim=True)
            for i in hits[1:]:
                del a.lines[i]
        else:
            a.lines.insert(fi, Line(canon_line(cmd, args, in_anim=True)))
        self._mark_dirty()
        return True

    def _write_attack(self):
        if not self.current_frame_info():
            return
        sp = self.cmb_atk_special.currentText() \
            if self.ck_atk_special.isChecked() else "无"
        cmd = sp if sp != "无" else f"attack{self.sp_atk_no.value()}"
        x, y, w, h = [str(s.value()) for s in self.sp_atk]
        dmg = f"{self.sp_atk_dmg.value():g}"
        kd = "1" if self.ck_atk_kd.isChecked() else "0"
        blk = "1" if self.ck_atk_block.isChecked() else "0"
        nfl = "1" if self.ck_atk_noflash.isChecked() else "0"
        pause = str(self.sp_atk_pause.value())
        z = str(self.sp_atk_z.value())
        if cmd == "blast":
            args = [x, y, w, h, dmg, blk, nfl, pause, z]
        else:
            args = [x, y, w, h, dmg, kd, blk, nfl, pause, z]
        if self._set_frame_attack(cmd, args):
            self._refresh_frame_ui()

    def clear_attack(self):
        if self._set_frame_prop("attack", ["0"]):
            self._refresh_frame_ui()

    def inherit_attack(self):
        """删除本帧的 attack 类命令行，使其继承前一帧的攻击框。"""
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None:
            return
        s, e = self._frame_block(a, fr)
        for i in range(e - 1, s - 1, -1):
            c = a.lines[i].cmd()
            if c.startswith("attack") or c in SPECIAL_ATTACK_TYPES:
                del a.lines[i]
                self._mark_dirty()
                self._refresh_frame_ui()
                return

    def clear_bbox(self):
        if self._set_frame_prop("bbox", ["0", "0", "0", "0", "0"]):
            self._refresh_frame_ui()

    def inherit_bbox(self):
        self._del_frame_cmd("bbox")
        self._mark_dirty()
        self._refresh_frame_ui()

    # ---- BBox / Attack 复制 · 粘贴（无内容时静默不操作）----
    def copy_bbox(self):
        if not self.current_frame_info():
            return
        self._bbox_clip = [str(s.value()) for s in self.sp_bbox]

    def paste_bbox(self):
        if not self.current_frame_info() or not self._bbox_clip:
            return
        for s, v in zip(self.sp_bbox, self._bbox_clip):
            s.setValue(_to_int(v))
        self._bbox_changed()

    def copy_attack(self):
        if not self.current_frame_info():
            return
        self._atk_clip = {
            "special": self.cmb_atk_special.currentText(),
            "no": self.sp_atk_no.value(),
            "xywh": [s.value() for s in self.sp_atk],
            "dmg": self.sp_atk_dmg.value(),
            "kd": self.ck_atk_kd.isChecked(),
            "blk": self.ck_atk_block.isChecked(),
            "nfl": self.ck_atk_noflash.isChecked(),
            "pause": self.sp_atk_pause.value(),
            "z": self.sp_atk_z.value(),
        }

    def paste_attack(self):
        if not self.current_frame_info() or not self._atk_clip:
            return
        c = self._atk_clip
        self.cmb_atk_special.blockSignals(True)
        self.cmb_atk_special.setCurrentText(c.get("special", "无"))
        self.cmb_atk_special.blockSignals(False)
        sp = c.get("special", "无")
        with blocked(self.ck_atk_special):
            self.ck_atk_special.setChecked(sp not in ("无", "", None))
        self.cmb_atk_special.setEnabled(sp not in ("无", "", None))
        self.sp_atk_no.setEnabled(sp in ("无", "", None))
        self.sp_atk_no.setValue(c.get("no", 1))
        for s, v in zip(self.sp_atk, c.get("xywh", [0, 0, 0, 0])):
            s.setValue(v)
        self.sp_atk_dmg.setValue(c.get("dmg", 0))
        self.ck_atk_kd.setChecked(c.get("kd", False))
        self.ck_atk_block.setChecked(c.get("blk", False))
        self.ck_atk_noflash.setChecked(c.get("nfl", False))
        self.sp_atk_pause.setValue(c.get("pause", 0))
        self.sp_atk_z.setValue(c.get("z", 0))
        self._write_attack()

    def _nodm_changed(self):
        if self._loading:
            return
        if self.ck_nodm.isChecked():
            if self._set_frame_prop("nodrawmethod", []):
                self._refresh_frame_ui()
        else:
            self._del_frame_cmd("nodrawmethod")
            self._mark_dirty()
            self._refresh_frame_ui()

    def _dm_changed(self, from_toggle=False):
        """控件值变了。from_toggle=True 表示是**启用框被勾上**触发的：
        这时即使值还是默认也照写一行（否则一勾就被刷新取消掉，
        勾选框永远勾不上）；值编辑触发时默认值不写，免得文件里
        出一堆“等于没设”的行。"""
        if self._loading or not self.current_frame_info():
            return
        if self.ck_nodm.isChecked():
            return
        if not self._dm_new:
            # 老写法：一整行 10 个整数（scale / shiftx 以 256 为 100%）
            if self._replace_frame_dm([("", self._dm_legacy_args())]):
                self._refresh_frame_ui()
            return
        # 新写法：一行一个子命令；只重写 GUI 管得着的那些，
        # GUI 之外的子命令行原样保留在原处
        if self._replace_frame_dm(
                self._dm_new_lines(skip_defaults=not from_toggle),
                only_subs=self._DM_GUI_SUBS):
            self._refresh_frame_ui()

    # ---------------- DrawMethod 子命令面板：读 / 写 ----------------
    @staticmethod
    def _to_f(v, d=0.0):
        try:
            return float(v)
        except (ValueError, TypeError):
            return d

    def _dm_fill_new(self, dm_map, warns):
        """新写法 → 控件：按子命令逐行回填，超范围值夹取并记警告。"""
        for sub in self._dm_rows:
            vals = dm_map.get(sub)
            if vals is None and sub in ("clipx", "clipy", "clipw", "cliph"):
                # clip 和 clipx/y/w/h 是同一件事的两种写法：
                # 文件里只有 clip 时也把分量框填上（写回时仍各归各）
                c = dm_map.get("clip")
                if c and len(c) == 4:
                    vals = [c[{"clipx": 0, "clipy": 1,
                               "clipw": 2, "cliph": 3}[sub]]]
            self._dm_set_row(sub, vals, warns)

    def _dm_fill_legacy(self, dm, warns):
        """老写法（10 个整数）→ 控件；scale / shiftx 按 256=100% 换算。"""
        a = [str(x) for x in (dm or [])] + ["0"] * 10
        self._dm_set_row("scale", [a[0], a[1]], warns, div=256.0)
        self._dm_set_row("flipx", ["1" if _to_int(a[2]) else "0"], warns)
        self._dm_set_row("flipy", ["1" if _to_int(a[3]) else "0"], warns)
        self._dm_set_row("shiftx", [a[4]], warns, div=256.0)
        self._dm_set_row("alpha", [a[5]], warns)
        self._dm_set_row("remap", [a[6]], warns)
        self._dm_set_row("fillcolor", [a[7]], warns)
        self._dm_set_row("rotate", [a[8]], warns)
        self._dm_set_row("fliprotate",
                         ["1" if _to_int(a[9]) else "0"], warns)
        # 老写法里没有的子命令：清成默认（未启用）
        for sub in ("scalex", "scaley", "tintcolor", "tintmode",
                    "channel", "channelr", "channelg", "channelb",
                    "clip", "clipx", "clipy", "clipw", "cliph"):
            self._dm_set_row(sub, None, warns)

    def _dm_set_row(self, sub, vals, warns, div=1.0):
        """把控件设成文件里的值（div=256 时整数换算成倍数显示）。

        vals=None → 取消启用并显示默认值；超出控件合法范围的值被
        夹取并记进 warns（保存时这行会被改写成夹取后的值）。
        """
        ck, ctrls = self._dm_rows[sub]
        kind = self._DM_SPEC[sub][0]
        if kind == "chk":
            ck.setChecked(bool(vals) and _to_int(vals[0]) == 1)
            return
        ck.setChecked(vals is not None)
        if vals is None:
            if kind == "combo":
                ctrls[0].setCurrentIndex(0)
                return
            if kind == "color":
                ctrls[0].setText("")
                return
            dvs = self._DM_DEFVALS.get(sub, ("0",) * len(ctrls))
            for k, e in enumerate(ctrls):
                e.setValue(self._to_f(dvs[k]) if k < len(dvs) else 0.0)
            return
        if kind == "combo":
            v = _to_int(vals[0]) if vals else 0
            lo, hi = (0, 6) if sub == "alpha" else (1, 6)
            if not (lo <= v <= hi):
                warns.append("%s %d 超出 %d~%d（按 %d 显示）"
                             % (sub, v, lo, hi, lo))
                v = lo
            ctrls[0].setCurrentIndex(max(0, v - lo))
            return
        if kind == "color":
            ctrls[0].setText(str(vals[0]).strip() if vals else "")
            self._dm_color_check(ctrls[0])
            return
        lo, hi = self._DM_SPEC[sub][1], self._DM_SPEC[sub][2]
        dvs = self._DM_DEFVALS.get(sub, ("0",) * len(ctrls))
        for k, e in enumerate(ctrls):
            if k < len(vals) and str(vals[k]).strip():
                want = self._to_f(vals[k]) / div
            else:
                want = self._to_f(dvs[k]) if k < len(dvs) else 0.0
            v = max(lo, min(hi, want))
            if abs(v - want) > 1e-9:
                warns.append("%s %g 超出 %g~%g（按 %g 显示）"
                             % (sub, want, lo, hi, v))
            e.setValue(v)

    def _dm_row_values(self, sub):
        """控件当前值（字符串参数表）；没启用 / 非法值 → None。"""
        ck, ctrls = self._dm_rows[sub]
        kind = self._DM_SPEC[sub][0]
        if kind == "chk":
            return ["1"] if ck.isChecked() else None
        if not ck.isChecked():
            return None
        if kind == "combo":
            lo = 0 if sub == "alpha" else 1
            return [str(lo + ctrls[0].currentIndex())]
        if kind == "color":
            t = ctrls[0].text().strip()
            if not t or not self._dm_color_ok(t):
                return None
            return [t]
        return ["%g" % e.value() for e in ctrls]

    # 写出的行序（和面板上的排列一致）
    _DM_ROWS_ORDER = tuple(d[0] for d in _DM_DEFS)

    def _dm_new_lines(self, skip_defaults=True):
        """按当前控件生成新写法 drawmethod 行 [(子命令, 参数)]。

        skip_defaults=True（值编辑触发）时，值等于默认（scale 1 /
        rotate 0 …）的行不写；False（启用框勾上触发）时照写，
        保住刚勾上的启用状态。
        """
        lines = []
        for sub in self._DM_ROWS_ORDER:
            vals = self._dm_row_values(sub)
            if vals is None:
                continue
            if skip_defaults and tuple(vals) == self._DM_SKIP_WHEN.get(sub):
                continue
            lines.append((sub, vals))
        return lines

    def _dm_val(self, sub, k=0):
        """数值控件的当前值；没启用返回 None。"""
        ck, ctrls = self._dm_rows[sub]
        if not ck.isChecked() or k >= len(ctrls):
            return None
        return ctrls[k].value()

    def _dm_legacy_args(self):
        """按当前控件生成老写法的 10 个整数（scale / shiftx 以 256=100%）。"""
        sx = self._dm_val("scale", 0)
        sy = self._dm_val("scale", 1)
        if sx is None:
            sx = 1.0
        if sy is None:
            sy = 1.0
        sxv = self._dm_val("scalex")
        syv = self._dm_val("scaley")
        if sxv is not None:
            sx = sxv
        if syv is not None:
            sy = syv
        sh = self._dm_val("shiftx") or 0.0
        ck_al, al_ctrl = self._dm_rows["alpha"]
        alpha = al_ctrl[0].currentIndex() if ck_al.isChecked() else 0
        remap = int(self._dm_val("remap") or 0)
        ck_fc, fc_ctrl = self._dm_rows["fillcolor"]
        fill = 0
        if ck_fc.isChecked() and self._dm_color_ok(fc_ctrl[0].text()):
            fill = _to_int(fc_ctrl[0].text())
        rot = int(self._dm_val("rotate") or 0) % 360
        return [str(int(round(sx * 256))), str(int(round(sy * 256))),
                "1" if self._dm_rows["flipx"][0].isChecked() else "0",
                "1" if self._dm_rows["flipy"][0].isChecked() else "0",
                str(int(round(sh * 256))),
                str(alpha), str(remap), str(fill), str(rot),
                "1" if self._dm_rows["fliprotate"][0].isChecked() else "0"]

    def _dm_block_lines(self):
        """当前帧块内的 drawmethod 行下标（按文件顺序）。"""
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None:
            return []
        fi = fr["line_idx"]
        prev = -1
        for i in range(fi - 1, -1, -1):
            if a.lines[i].cmd() == "frame":
                prev = i
                break
        return [i for i in range(prev + 1, fi)
                if a.lines[i].cmd() == "drawmethod"]

    def _replace_frame_dm(self, pairs, only_subs=None):
        """重写当前帧块内的 drawmethod 行为 pairs [(""=老写法/子命令, 参数)]。

        only_subs=None：块里**所有** drawmethod 行都删掉重写（切换写法用，
        已在 _dm_mode_clicked 里弹过覆盖确认）；给了就只删这些子命令的行，
        fillcolor / tint 等 GUI 之外的行原样留在原处。
        """
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None or not self._guard_edit(silent=True):
            return False
        fi = fr["line_idx"]
        prev = -1
        for i in range(fi - 1, -1, -1):
            if a.lines[i].cmd() == "frame":
                prev = i
                break
        for i in range(fi - 1, prev, -1):          # 倒序删，行号不乱
            ln = a.lines[i]
            if ln.cmd() != "drawmethod":
                continue
            sub = dm_sub_cmd(ln.args())
            if only_subs is not None and sub and sub not in only_subs:
                continue                            # GUI 不管的子命令：保留
            del a.lines[i]
            fi -= 1
        for sub, args in pairs:                    # 插在 frame 行之前
            full = list(args) if not sub else [sub] + list(args)
            a.lines.insert(fi, Line(canon_line("drawmethod", full,
                                               in_anim=True)))
            fi += 1
        self._mark_dirty()
        return True

    def _dm_mode_look(self):
        self.btn_dm_mode.setText("写法：子命令" if self._dm_new
                                 else "写法：一串数")

    def _dm_mode_clicked(self):
        """切换 drawmethod 写法；本帧已有另一种写法时先确认覆盖。"""
        if self._loading:
            return
        want_new = not self._dm_new
        a = self.current_anim()
        own = self._dm_block_lines()
        if a is not None and own:
            has_new = any(dm_sub_cmd(a.lines[i].args()) for i in own)
            has_leg = any(not dm_sub_cmd(a.lines[i].args()) for i in own)
            # 已有**另一种**写法的行才提示（无论多少行）
            if (want_new and has_leg) or (not want_new and has_new):
                r = QMessageBox.question(
                    self, "切换 drawmethod 写法",
                    f"本帧已有{'老写法（一串数）' if has_leg else '新写法（子命令）'}"
                    f"的 drawmethod，共 {len(own)} 行。\n\n"
                    f"切换后这些行会被**删除**，并按"
                    f"{'子命令' if want_new else '一串数'}写法重写当前设置。\n"
                    f"是否继续？")
                if r != QMessageBox.Yes:
                    return
        self._dm_new = want_new
        self._dm_mode_look()
        if own:
            # 确认过覆盖了：把当前控件的值按新写法重写（旧行全删）
            if want_new:
                self._replace_frame_dm(self._dm_new_lines())
            else:
                self._replace_frame_dm([("", self._dm_legacy_args())])
            self._refresh_frame_ui()

    def _frame_cmd_changed(self):
        if self._loading:
            return
        for cmd, (ck, spins) in self._jf_rows.items():
            args = [str(s.value()) for s in spins] if ck.isChecked() else None
            self._set_anim_cmd(cmd, args)
        self.timeline.update()

    def _loop_toggled(self, checked):
        if self._loading:
            return
        self.btn_loop.setText("loop ✔" if checked else "loop ❌")
        self.sp_loop_s.setEnabled(checked)
        self.sp_loop_e.setEnabled(checked)
        a = self.current_anim()
        if a is None:
            return
        if checked:
            self._set_anim_cmd("loop", ["1", str(self.sp_loop_s.value()),
                                        str(self.sp_loop_e.value())])
        else:
            _, ln = a.find_cmd("loop")
            if ln is not None:
                largs = (ln.args() + ["0", "0"])[1:3]
                self._set_anim_cmd("loop", ["0", largs[0], largs[1]])
        self._refresh_frame_ui()

    def _loop_param_changed(self):
        if self._loading or not self.btn_loop.isChecked():
            return
        n = self.anim_frame_count()
        if n is not None:
            for w in (self.sp_loop_s, self.sp_loop_e):
                if w.value() > n:
                    QMessageBox.warning(
                        self, "帧号超出范围",
                        f"帧号 {w.value()} 超出总帧数 {n}，已拒绝。")
                    w.revert()
                    return
        self._set_anim_cmd("loop", ["1", str(self.sp_loop_s.value()),
                                    str(self.sp_loop_e.value())])
        self._refresh_frame_ui()

    def anim_frame_count(self):
        a = self.current_anim()
        return len(a.frames()) if a else None

    def _rel_path(self, path, keep_ext=False):
        rel = None
        for root in self.root_dirs:
            try:
                r = os.path.relpath(path, root)
                if not r.startswith(".."):
                    rel = r
                    break
            except ValueError:
                continue
        if rel is None:
            rel = path
        if not keep_ext:
            rel = os.path.splitext(rel)[0]
        return rel.replace(os.sep, "/")

    # ---------------- 帧操作 ----------------
    def _frame_block(self, a, frame_entry):
        fi = frame_entry["line_idx"]
        prev = -1
        for i in range(fi - 1, -1, -1):
            if a.lines[i].cmd() == "frame":
                prev = i
                break
        return prev + 1, fi

    def add_frame(self):
        a = self.current_anim()
        if a is None or not self._guard_edit():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择帧图片", self.data_dir or "",
            "图片 (*.gif *.bmp *.pcx *.png)")
        if not path:
            return
        rel = self._rel_path(path)
        frames = a.frames()
        if frames:
            s, e = self._frame_block(a, frames[self.frame_idx])
            a.lines.insert(e + 1, Line(canon_line("frame", [rel], True)))
        else:
            a.lines.append(Line(canon_line("frame", [rel], True)))
        self._mark_dirty()
        self.frame_idx = min(self.frame_idx + 1, len(a.frames()) - 1)
        self._refresh_frame_ui()

    def del_frame(self):
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None or not self._guard_edit():
            return
        del a.lines[fr["line_idx"]]
        self._mark_dirty()
        n = len(a.frames())
        self.frame_idx = max(0, min(self.frame_idx, n - 1))
        self._refresh_frame_ui()

    def replace_frame(self):
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None or not self._guard_edit():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "替换帧图片", self.data_dir or "",
            "图片 (*.gif *.bmp *.pcx *.png)")
        if not path:
            return
        a.lines[fr["line_idx"]].set_args("frame", [self._rel_path(path)],
                                         in_anim=True)
        self._mark_dirty()
        self._refresh_frame_ui()

    def dup_frame(self):
        """只复制帧图片，不复制该帧附带设置 / Attack 框等。"""
        a = self.current_anim()
        fr = self.current_frame_info()
        if a is None or fr is None or not self._guard_edit():
            return
        a.lines.insert(fr["line_idx"] + 1,
                       Line(canon_line("frame", [fr["path"]], True)))
        self._mark_dirty()
        self.frame_idx += 1
        self._refresh_frame_ui()

    def edit_frame_script(self, frame_idx):
        """编辑该帧之前的 @cmd 行 / @script 块内容。"""
        a = self.current_anim()
        if a is None or not self._guard_edit():
            return
        frames = a.frames()
        if not (0 <= frame_idx < len(frames)):
            return
        fr = frames[frame_idx]
        if fr.get("script"):
            s, e = fr["script"]
            title = "@script 脚本"
        elif fr.get("atcmd"):
            s = e = fr["atcmd"][-1]
            title = "@cmd 命令"
        else:
            return
        old = "\n".join(a.lines[i].raw for i in range(s, e + 1))
        dlg = QDialog(self)
        dlg.setWindowTitle(f"编辑帧 {frame_idx} 的 {title}")
        dlg.resize(620, 420)
        v = QVBoxLayout(dlg)
        ed = QPlainTextEdit()
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(10)
        ed.setFont(f)
        ed.setPlainText(old)
        v.addWidget(ed, 1)
        row = QHBoxLayout()
        b_ok = QPushButton("确定")
        b_ok.setObjectName("primaryBtn")
        b_no = QPushButton("取消")
        b_ok.clicked.connect(dlg.accept)
        b_no.clicked.connect(dlg.reject)
        row.addStretch(1)
        row.addWidget(b_ok)
        row.addWidget(b_no)
        v.addLayout(row)
        if dlg.exec_() != QDialog.Accepted:
            return
        new = ed.toPlainText()
        if new == old:
            return
        a.lines[s:e + 1] = [Line(t) for t in new.splitlines()]
        self._mark_dirty()
        self._refresh_all()

    # ---------------- 文本模式同步用的小工具 ----------------
    def _txt_lines(self):
        return self.txt_edit.toPlainText().split("\n")

    def _txt_set_lines(self, lines):
        self.txt_edit.setPlainText("\n".join(lines))

    def _value_dialog(self, title, label, default=""):
        """宽输入对话框（≥100 字符宽度），替代窄小的 QInputDialog。"""
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(label))
        ed = QLineEdit(default)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(10)
        ed.setFont(f)
        ed.setMinimumWidth(760)
        ed.selectAll()
        v.addWidget(ed)
        row = QHBoxLayout()
        row.addStretch(1)
        b_ok = QPushButton("确定")
        b_ok.setObjectName("primaryBtn")
        b_no = QPushButton("取消")
        b_ok.clicked.connect(dlg.accept)
        b_no.clicked.connect(dlg.reject)
        row.addWidget(b_ok)
        row.addWidget(b_no)
        v.addLayout(row)
        ed.setFocus()
        if dlg.exec_() != QDialog.Accepted:
            return None
        return ed.text()

    # ---------------- 动画操作 ----------------
    def add_anim(self):
        if self.doc is None:
            return
        name, ok = QInputDialog.getText(self, "添加动画", "动画名（如 attack5）:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if self._text_mode:
            anims, _ = self._text_scan()
            if any(a[0].lower() == name.lower() for a in anims):
                QMessageBox.warning(self, "添加动画", "同名动画已存在。")
                return
            lines = self._txt_lines()
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ["", "", "anim " + name, "\tdelay 10", "\toffset 0 0"]
            self._txt_set_lines(lines)
            self.anim_idx = len(anims)
            self.txt_edit.flash_line(len(lines) - 3)
            self._refresh_all()
            return
        if any(a.name.lower() == name.lower() for a in self.doc.anims):
            QMessageBox.warning(self, "添加动画", "同名动画已存在。")
            return
        a = Anim(name)
        a.lines = [Line("\tdelay 10"), Line("\toffset 0 0")]
        self.doc.anims.append(a)
        self._mark_dirty()
        self.anim_idx = len(self.doc.anims) - 1
        self.frame_idx = 0
        self._refresh_all()

    def del_anim(self):
        if self.doc is None:
            return
        if self._text_mode:
            anims, _ = self._text_scan()
            if not (isinstance(self.anim_idx, int)
                    and 0 <= self.anim_idx < len(anims)):
                return
            nm = anims[self.anim_idx][0]
            r = QMessageBox.question(self, "删除动画",
                                     f"确定删除动画 “{nm}”？")
            if r != QMessageBox.Yes:
                return
            lines = self._txt_lines()
            s = anims[self.anim_idx][1]
            e = anims[self.anim_idx + 1][1] \
                if self.anim_idx + 1 < len(anims) else len(lines)
            del lines[s:e]
            self._txt_set_lines(lines)
            self.anim_idx = min(self.anim_idx, max(0, len(anims) - 2))
            doc = self.txt_edit.document()
            self.txt_edit.flash_line(max(0, min(s, doc.blockCount() - 1)))
            self._refresh_all()
            return
        a = self.current_anim()
        if a is None:
            return
        r = QMessageBox.question(self, "删除动画",
                                 f"确定删除动画 “{a.name}”？")
        if r != QMessageBox.Yes:
            return
        del self.doc.anims[self.anim_idx]
        self._mark_dirty()
        self.anim_idx = min(self.anim_idx, len(self.doc.anims) - 1) \
            if self.doc.anims else None
        self.frame_idx = 0
        self._refresh_all()

    # ---------------- header 操作 ----------------
    def _is_script_block_item(self, it):
        """该 header 项是不是全局 @script … @end_script 块（只能文本模式改）。"""
        if self.doc is None or it is None:
            return False
        if self._text_mode:
            return False
        idx = it.data(0, Qt.UserRole)
        return idx is not None and self.doc.is_script_block(int(idx))

    def add_header_field(self):
        if self.doc is None:
            return
        # load / alternatepal / remap / com 允许出现多次，所以永远可选
        choices = [c for c in HEADER_CMDS
                   if self.doc.header_get(c) is None
                   or c in HEADER_MULTI_CMDS]
        if not choices:
            QMessageBox.information(self, "新建字段", "字段已全部设置。")
            return
        cmd, ok = QInputDialog.getItem(self, "新建字段",
                                       "选择字段（★=可重复添加）:",
                                       [c + (" ★" if c in HEADER_MULTI_CMDS
                                             else "")
                                        for c in choices], 0, False)
        if not ok or not cmd:
            return
        cmd = cmd.replace(" ★", "").strip()
        cmd_is_multi = cmd in HEADER_MULTI_CMDS
        val = self._value_dialog("新建字段", f"{cmd} 的值:", "")
        if val is None:
            return
        if self._text_mode:
            _, headers = self._text_scan()
            lines = self._txt_lines()
            pos = (headers[-1][2] + 1) if headers else 0
            lines.insert(pos, canon_line(cmd, val.split(), in_anim=False))
            self._txt_set_lines(lines)
            self.txt_edit.flash_line(pos)
            self._refresh_all()
            return
        # 多值字段无条件追加一行；其余字段走 header_set（已有则改值）
        self.doc.header_add(cmd, val.split()) if cmd_is_multi \
            else self.doc.header_set(cmd, val.split())
        self._mark_dirty()
        self._refresh_all()

    def edit_header_field(self):
        if self.doc is None:
            return
        it = self.header_tree.currentItem()
        if it is None:
            return
        cmd = it.text(0)
        if self._is_script_block_item(it):
            QMessageBox.information(
                self, "修改字段",
                "这是脚本块，请到“文本编辑”模式改。")
            return
        val = self._value_dialog("修改字段", f"{cmd} 的新值：", it.text(1))
        if val is None:
            return
        if self._text_mode:
            line = it.data(0, Qt.UserRole)
            if line is not None:
                lines = self._txt_lines()
                lines[line] = canon_line(cmd, val.split(), in_anim=False)
                self._txt_set_lines(lines)
                self.txt_edit.flash_line(line)
            self._refresh_all()
            return
        self.doc.header_set(cmd, val.split())
        self._mark_dirty()
        self._refresh_all()

    def del_header_field(self):
        if self.doc is None:
            return
        it = self.header_tree.currentItem()
        if it is None:
            return
        cmd = it.text(0)
        if self._is_script_block_item(it):
            QMessageBox.information(
                self, "删除字段",
                "这是 @script … @end_script 脚本块，请切到“文本编辑”模式删除。")
            return
        r = QMessageBox.question(self, "删除字段", f"确定删除 header 字段 {cmd}？")
        if r != QMessageBox.Yes:
            return
        if self._text_mode:
            line = it.data(0, Qt.UserRole)
            if line is not None:
                lines = self._txt_lines()
                del lines[line]
                self._txt_set_lines(lines)
                doc = self.txt_edit.document()
                self.txt_edit.flash_line(max(0, min(line, doc.blockCount() - 1)))
            self._refresh_all()
            return
        self.doc.header_del(cmd)
        self._mark_dirty()
        self._refresh_all()

    # ---------------- 保存 / 重载 ----------------
    def _apply_validation_colors(self, issues):
        """把当前实体的校验结果同步回“实体列表”那一行的底色 / 提示。

        以前只有“保存”时才刷一次，于是改对了问题、下方问题栏已经显示
        “无问题”，左边实体列表却还红着/黄着。现在只要重新校验过就同步。
        """
        if self.doc is None:
            return
        path = self.doc.path
        for i, (n, p, old) in enumerate(self.entities):
            if p != path:
                continue
            self.entities[i] = (n, p, issues)
            if i not in self._ent_order:
                return
            it = self.ent_list.item(self._ent_order.index(i))
            if it is None:
                return
            errs = [m for s, m, _ in issues if s == ERR]
            warns = [m for s, m, _ in issues if s == WARN]
            if errs:
                it.setBackground(ERR_BG)
                it.setForeground(ERR_FG)
            elif warns:
                it.setBackground(WARN_BG)
                it.setForeground(WARN_FG)
            else:
                # 真没问题了：底色和字色都要还原，不能只清底色留着红字
                it.setBackground(QBrush())
                it.setForeground(QBrush())
            it.setToolTip("\n".join(m for _, m, _ in issues) or "无问题")
            return

    def _sync_entity_issues(self):
        """用当前 _cur_issues 刷新实体列表行颜色（每次重新校验后调用）。"""
        self._apply_validation_colors(self._cur_issues)

    def save_entity(self):
        if self.doc is None:
            return
        # 对照模式下可能刚敲完字还没换行：先把文本并回文档再存，
        # 保证写出去的就是屏幕上看到的。
        if self._cmp_mode:
            self._sync_text_to_doc()
        r = QMessageBox.question(
            self, "保存确认",
            f"将把修改写回：\n{self.doc.path}\n\n是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        try:
            self.doc.save()
        except Exception as ex:
            QMessageBox.warning(self, "保存失败", str(ex))
            return
        self.dirty = False
        issues = validate_entity(self.doc, self.models, self.root_dirs)
        self._apply_validation_colors(issues)
        self.timeline.clear_cache()
        self._undo_seal()          # 保存前把未封口的改动收成一条记录
        self._refresh_all()
        QMessageBox.information(self, "保存完成", "已写回 TXT。")

    def reload_entity(self):
        """重载 TXT：直接复用“装载实体”的流程，再作废撤销栈。"""
        if self.doc is None:
            return
        if self._cmp_mode:
            self._sync_text_to_doc()
        if self.dirty:
            r = QMessageBox.question(
                self, "重载确认",
                "重载将丢弃未保存的修改，是否继续？")
            if r != QMessageBox.Yes:
                return
        row = self._row_of_path(self.doc.path)
        try:
            self.doc.load(self.doc.path)
        except Exception as ex:
            QMessageBox.warning(self, "重载失败", str(ex))
            return
        if row is None:
            self.anim_idx = self._default_anim_index(self.doc)
            self.frame_idx = 0
            self.dirty = False
            self.canvas.clear_pending()
            self.timeline.clear_cache()
            self._refresh_all()
        else:
            self._load_entity_row(self._ent_order[row])
        self._undo_reset()         # 重载 = 换了一份文本，撤销栈作废