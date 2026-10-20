# -*- coding: utf-8 -*-
"""OpenBOR 手册查看器（自带副本 + 目录 + 搜索）。

数据位置：程序目录下 manual/OpenBORManual.html 与 manual/images/。
优先用 QtWebEngine 渲染（保真、支持原图），缺失时退回 QTextBrowser。
"""
import os
import re
import sys

from PyQt5.QtCore import QUrl, Qt
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QSplitter,
                             QTextBrowser, QVBoxLayout, QWidget)

try:                                        # 首选：完整网页渲染
    from PyQt5.QtWebEngineWidgets import QWebEngineView
except Exception:                           # pragma: no cover
    QWebEngineView = None
# WebEngine 在无窗口的 offscreen 平台上会直接崩，这种情况下退回 QTextBrowser
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    QWebEngineView = None

# 打包成 exe 后：manual 先找 exe 旁边（用户可自行替换 / 删除），
# 找不到再用打包进 _internal 里的那份
if getattr(sys, "frozen", False):
    _BASES = [os.path.dirname(os.path.abspath(sys.executable)),
              os.path.dirname(os.path.abspath(__file__))]
else:
    _BASES = [os.path.dirname(os.path.abspath(__file__))]
MANUAL_DIR = next((os.path.join(b, "manual") for b in _BASES
                   if os.path.exists(os.path.join(b, "manual"))),
                  os.path.join(_BASES[-1], "manual"))
SRC_HTML = os.path.join(MANUAL_DIR, "OpenBORManual.html")
VIEW_HTML = os.path.join(MANUAL_DIR, "_view.html")

# ---- 自制排版样式：比原始 wiki 页面清爽，层次更清楚 ----
PAGE_CSS = """
body { margin:0; padding:0 0 60px 0; background:#f7f8fa; color:#24282e;
       font-family:"Segoe UI","Microsoft YaHei",sans-serif; font-size:15px;
       line-height:1.75; }
#page { max-width:1080px; margin:0 auto; background:#ffffff; padding:26px 44px 60px;
        box-shadow:0 1px 4px rgba(0,0,0,.10); }
#top { border-bottom:3px solid #2f6db3; padding-bottom:10px; margin-bottom:18px; }
#top h1 { margin:0; font-size:27px; color:#1c4e8a; letter-spacing:.5px; }
#top p { margin:4px 0 0; color:#7a828e; font-size:13px; }
h1 { font-size:23px; margin:34px 0 12px; padding:6px 0 6px 12px;
     color:#1c4e8a; border-left:5px solid #2f6db3; background:#f2f6fc;
     border-radius:0 4px 4px 0; }
h2 { font-size:19px; margin:26px 0 10px; padding:4px 0 4px 10px;
     color:#25603f; border-left:4px solid #3fae5f; background:#f1faf4;
     border-radius:0 4px 4px 0; }
h3, h4 { font-size:16px; margin:20px 0 8px; color:#8a5a12; }
p { margin:8px 0; }
a { color:#2f6db3; text-decoration:none; }
a:hover { text-decoration:underline; }
ul, ol { margin:8px 0 8px 26px; padding:0; }
li { margin:3px 0; }
b, strong { color:#1c4e8a; }
hr { border:none; border-top:1px solid #e3e6ea; margin:22px 0; }
table { border-collapse:collapse; margin:12px 0; font-size:14px; width:100%; }
th { background:#eef2f8; color:#1c4e8a; font-weight:bold; }
th, td { border:1px solid #d3d8df; padding:5px 9px; vertical-align:top; }
tr:nth-child(even) td { background:#fafbfd; }
img { max-width:100%; height:auto; border:1px solid #e3e6ea;
      border-radius:4px; background:#ffffff; padding:2px; }
pre, code, tt { font-family:"Consolas","Courier New",monospace;
      font-size:13.5px; background:#f4f6f8; border:1px solid #e1e5ea;
      border-radius:4px; padding:6px 9px; color:#31506e; }
pre { white-space:pre-wrap; word-break:break-word; }
dl { margin:8px 0; }
dt { font-weight:bold; color:#1c4e8a; margin-top:8px; }
dd { margin:2px 0 2px 22px; }
.mw-editsection, #toc, .toc, .mw-jump, .printfooter, #catlinks,
.catlinks, .mw-empty-elt { display:none; }
"""

HEAD_RE = re.compile(r"<h([123])[^>]*>(.*?)</h\1>", re.S)
ID_RE = re.compile(r'id="([^"]+)"')


def _strip_tags(s):
    return re.sub(r"<[^>]+>", "", s).strip()


def _extract_body(html):
    """从 MediaWiki 存档页里切出正文部分。"""
    i = html.find('<div id="mw-content-text"')
    if i < 0:
        i = html.find('<div class="mw-parser-output"')
    if i < 0:
        i = 0
    j = html.find('<div class="printfooter"')
    for key in ('<div class="printfooter"', '<div id="catlinks"',
                '<div class="catlinks"'):
        p = html.find(key)
        if p > 0:
            j = p if j < 0 or p < j else j
    if j <= i:
        j = len(html)
    body = html[i:j]
    k = body.find('<div class="mw-parser-output"')
    if k >= 0:
        body = body[k:]
    return body


def _headings(body):
    """[(层级, 标题文本, 锚点id)]。"""
    out = []
    for lvl, inner in HEAD_RE.findall(body):
        if lvl == "3":
            continue
        txt = re.sub(r"\[edit\]$", "", _strip_tags(inner)).strip()
        if not txt or txt.lower() in ("contents", "openbormanual"):
            continue
        ids = ID_RE.findall(inner)
        out.append((int(lvl), txt, ids[0] if ids else ""))
    return out


def build_page():
    """生成带自排版式的 _view.html，返回 (html文本, 目录列表)。"""
    try:
        with open(SRC_HTML, "r", encoding="utf-8", errors="replace") as fp:
            html = fp.read()
    except OSError:
        return None, []
    body = _extract_body(html)
    toc = _headings(body)
    title = "OpenBOR 手册"
    page = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        "<title>%s</title><style>%s</style></head><body><div id=\"page\">"
        '<div id="top"><h1>%s</h1>'
        "<p>OpenBOR 官方手册（本地副本）　·　左侧目录可跳转，"
        "上方搜索框可查找关键字</p>"
        "</div>%s</div></body></html>"
        % (title, PAGE_CSS, title, body))
    try:
        with open(VIEW_HTML, "w", encoding="utf-8") as fp:
            fp.write(page)
    except OSError:
        pass
    return page, toc


class ManualWindow(QWidget):
    """手册窗口（独立窗口，非模态，同时只开一个）。"""

    _opened = None

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("OpenBOR 手册（本地副本）")
        self.resize(1150, 800)
        self.use_web = QWebEngineView is not None
        self._zoom = 1.0
        page, toc = build_page()
        self.toc = toc
        self._build_ui()
        if page is None:
            self._set_html("<h2>手册缺失</h2><p>未找到 "
                           "<code>manual/OpenBORManual.html</code>，"
                           "请把手册文件放回该目录。</p>")
            return
        if self.use_web:
            self.view.setUrl(QUrl.fromLocalFile(VIEW_HTML))
        else:
            self.view.setSearchPaths([MANUAL_DIR])
            self.view.setHtml(page)

    def _set_html(self, html):
        if self.use_web:
            self.view.setHtml(html)
        else:
            self.view.setHtml(html)

    # ---- 界面 ----
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        if self.use_web:
            for text, tip, fn in (("←", "后退", self._back),
                                  ("→", "前进", self._forward)):
                b = QPushButton(text)
                b.setFixedWidth(34)
                b.setToolTip(tip)
                b.clicked.connect(fn)
                bar.addWidget(b)
        for text, tip, z in (("A-", "缩小文字", -0.1), ("A+", "放大文字", 0.1)):
            b = QPushButton(text)
            b.setFixedWidth(38)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, d=z: self._zoom_by(d))
            bar.addWidget(b)
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText("搜索关键字，回车查找下一个…")
        self.ed_find.setClearButtonEnabled(True)
        self.ed_find.returnPressed.connect(self._find_next)
        bar.addWidget(self.ed_find, 1)
        b_find = QPushButton("查找")
        b_find.clicked.connect(self._find_next)
        bar.addWidget(b_find)
        root.addLayout(bar)

        sp = QSplitter(Qt.Horizontal)
        self.toc_list = QListWidget()
        self.toc_list.setMinimumWidth(150)
        for lvl, txt, anchor in self.toc:
            it = QListWidgetItem(("　　" if lvl == 2 else "") + txt)
            it.setData(Qt.UserRole, anchor)
            f = it.font()
            f.setBold(lvl == 1)
            if lvl == 1:
                f.setPointSize(max(9, f.pointSize() + 1))
            it.setFont(f)
            self.toc_list.addItem(it)
        self.toc_list.itemClicked.connect(self._goto)
        sp.addWidget(self.toc_list)

        self.view = QWebEngineView() if self.use_web else QTextBrowser()
        sp.addWidget(self.view)
        sp.setSizes([250, 900])
        sp.setStretchFactor(0, 0)
        sp.setStretchFactor(1, 1)
        root.addWidget(sp, 1)

        self.lbl = QLabel("共 %d 个章节" % len(self.toc))
        self.lbl.setStyleSheet("color:#7a828e;font-size:12px;")
        root.addWidget(self.lbl)

    # ---- 交互 ----
    def _goto(self, it):
        anchor = it.data(Qt.UserRole)
        if not anchor:
            return
        if self.use_web:
            self.view.page().runJavaScript(
                "var e=document.getElementById('%s');"
                "if(e){e.scrollIntoView({block:'start'});}" % anchor)
        else:
            self.view.scrollToAnchor(anchor)
        self.view.setFocus()

    def _find_next(self):
        t = self.ed_find.text().strip()
        if not t:
            return
        if self.use_web:
            self.view.page().findText(t)
        else:
            self.view.find(t)

    def _zoom_by(self, d):
        self._zoom = min(2.4, max(0.5, round(self._zoom + d, 2)))
        if self.use_web:
            self.view.setZoomFactor(self._zoom)
        else:
            f = self.view.font()
            f.setPointSizeF(max(8.0, 10.5 * self._zoom))
            self.view.setFont(f)

    def _back(self):
        if self.use_web:
            self.view.back()

    def _forward(self):
        if self.use_web:
            self.view.forward()

    def closeEvent(self, e):
        ManualWindow._opened = None
        super().closeEvent(e)


def show_manual(parent=None):
    """打开（或唤醒）手册窗口。"""
    w = ManualWindow._opened
    if w is not None:
        try:
            w.raise_()
            w.activateWindow()
            return w
        except RuntimeError:
            ManualWindow._opened = None
    w = ManualWindow(parent)
    ManualWindow._opened = w
    w.show()
    return w
