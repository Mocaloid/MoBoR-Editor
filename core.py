# -*- coding: utf-8 -*-
"""底层通用：图片读写 / 索引色 / 工程数据模型 / 撤销栈 / 界面配色。

（由 image_utils.py、models.py、undo.py、bor_undo.py、bor_style.py 合并而来）"""

import copy
import os
import sys
import tempfile
import uuid
from PIL import Image, ImageChops
from collections import namedtuple

from PyQt5.QtCore import (Qt)
from PyQt5.QtGui import (QColor, QImage)



# ====================================================================
# 原 image_utils.py
# ====================================================================

def clamp(v, lo, hi):
    """把 v 夹在 [lo, hi] 之间（画布缩放 / 裁剪 / 颜色分量都在用）。"""
    return max(lo, min(hi, v))


IMG_EXTS = {".png", ".gif", ".bmp", ".tga", ".webp",
            ".jpg", ".jpeg", ".tif", ".tiff", ".ico"}

try:
    DITHER_NONE = Image.Dither.NONE
except AttributeError:  # Pillow < 9.1
    DITHER_NONE = Image.NONE

ISO_ALPHA = 70  # 颜色隔离时，非选中颜色像素的 alpha（0-255）


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMG_EXTS


# ---------------- .pal 配色文件（游戏开发常见 JASC-PAL 格式） ----------------

def load_pal_file(path):
    """读取 .pal 文件，返回 256 色 (r,g,b) 列表。
    支持 JASC-PAL 文本格式与 768 字节原始 RGB 二进制格式。
    """
    with open(path, "rb") as fp:
        data = fp.read()
    if data[:8] == b"JASC-PAL":
        colors = []
        for ln in data.decode("ascii", errors="replace").splitlines()[3:]:
            t = ln.split()
            if len(t) >= 3:
                colors.append(tuple(max(0, min(255, int(v))) for v in t[:3]))
        if not colors:
            raise ValueError("pal 文件中没有颜色数据")
        colors += [(0, 0, 0)] * (256 - len(colors))
        return colors[:256]
    if len(data) >= 768:                      # 原始二进制：每色 3 字节
        return [tuple(data[3 * i:3 * i + 3]) for i in range(256)]
    # 兜底：纯文本 “r g b” 行
    colors = []
    for ln in data.decode("ascii", errors="replace").splitlines():
        t = ln.split()
        if len(t) >= 3:
            colors.append(tuple(max(0, min(255, int(v))) for v in t[:3]))
    if colors:
        colors += [(0, 0, 0)] * (256 - len(colors))
        return colors[:256]
    raise ValueError("无法识别的 .pal 格式")


def save_pal_file(colors, path):
    """把 256 色写为 JASC-PAL 文本格式。"""
    lines = ["JASC-PAL", "0100", "256"]
    for r, g, b in list(colors)[:256]:
        lines.append(f"{int(r)} {int(g)} {int(b)}")
    with open(path, "w", newline="\r\n") as fp:
        fp.write("\r\n".join(lines) + "\r\n")


def load(path):
    """加载图片（取第 1 帧），并脱离文件句柄。"""
    with Image.open(path) as im0:
        try:
            im0.seek(0)
        except EOFError:
            pass
        im0.load()
        return im0.copy()   # copy 会保留 info（含 transparency）


def image_size(path):
    with Image.open(path) as im:
        return im.size


def openbor_rgba(im):
    """按 OpenBOR 的 8 位图规则转成 RGBA：**调色板第 1 个色（index 0）就是透明色**。

    判据是“第 1 个色有没有被设成透明”，不是“整张色表里有没有透明色”：
    很多素材要么压根没写 transparency 标志，要么把它写在了别的索引上，
    结果 index 0 的那块底色会糊成一整片纯色。这里一律把 index 0 抠掉
    （同时保留文件自己声明的那个透明索引），**只影响显示，不改色表、不写回文件**。
    """
    if im is None:
        return None
    if im.mode != "P":
        return im.convert("RGBA")
    rgba = im.convert("RGBA")
    lut = [255] * 256
    lut[0] = 0                                    # index 0 恒透明
    t = im.info.get("transparency")
    if isinstance(t, int) and 0 <= t <= 255:
        lut[t] = 0                                # 文件里声明的也照旧透明
    elif isinstance(t, bytes):
        for i, a in enumerate(t[:256]):
            if a < 128:
                lut[i] = 0
    try:
        idx = Image.frombytes("L", im.size, im.tobytes())
        rgba.putalpha(idx.point(lut))
    except Exception:
        pass
    return rgba


def load_rgba(path):
    """读一张图 → QImage（按 OpenBOR 规则把 index 0 当透明）。"""
    return pil_to_qimage(openbor_rgba(load(path)))


def palette_colors(im):
    """索引图返回 (256 色列表, 透明索引或 None)；非索引图返回 None。"""
    if im.mode != "P":
        return None
    pal = im.getpalette() or []
    n = len(pal) // 3
    colors = [(pal[3 * i], pal[3 * i + 1], pal[3 * i + 2]) if i < n else (0, 0, 0)
              for i in range(256)]
    t = im.info.get("transparency")
    if isinstance(t, bytes):
        tidx = next((i for i, a in enumerate(t) if a < 128), None)
    elif isinstance(t, int):
        tidx = t
    else:
        tidx = None
    return colors, tidx


def apply_palette(im, palette):
    """把当前色表颜色写入 P 图（不动像素索引）。"""
    if palette is None or im.mode != "P":
        return
    flat = []
    for r, g, b in palette.colors:
        flat += [r, g, b]
    im.putpalette(flat)
    if palette.transparent_index is not None:
        im.info["transparency"] = palette.transparent_index


def _quantize_to(rgb, pal_im):
    """就近映射到指定色表；兼容没有 dither 参数的旧版 Pillow。"""
    try:
        return rgb.quantize(palette=pal_im, dither=DITHER_NONE)
    except TypeError:
        return rgb.quantize(palette=pal_im)


def quantize_to_indexed(im):
    """任意图 -> 索引图（中位切分量化；alpha<128 的像素归入透明索引 255）。"""
    rgba = im.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] < 128:
        q = rgba.convert("RGB").quantize(colors=255)
        mask = alpha.point(lambda v: 255 if v < 128 else 0)
        q = Image.composite(Image.new("P", q.size, 255), q, mask)
        q.info["transparency"] = 255
        return q
    return rgba.convert("RGB").quantize(colors=256)


def map_to_palette(im, colors, transparent_index=None):
    """把任意图按“就近颜色”映射到指定色表（不抖动）。"""
    pal_im = Image.new("P", (1, 1))
    flat = []
    for r, g, b in colors:
        flat += [r, g, b]
    pal_im.putpalette(flat)
    rgba = im.convert("RGBA")
    q = _quantize_to(rgba.convert("RGB"), pal_im)
    alpha = rgba.getchannel("A")
    if transparent_index is not None:
        if alpha.getextrema()[0] < 128:
            mask = alpha.point(lambda v: 255 if v < 128 else 0)
            q = Image.composite(Image.new("P", q.size, transparent_index), q, mask)
        q.info["transparency"] = transparent_index
    return q


def bresenham(x0, y0, x1, y1):
    """两点间整数直线插值（铅笔连续笔画用）。"""
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        yield x, y
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


# ---------------- 渲染 ----------------

def pil_to_qimage(im):
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    q = QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888)
    return q.copy()


def transparent_qimage(size):
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    q = QImage(w, h, QImage.Format_RGBA8888)
    q.fill(0)
    return q


def render_frame(frame, palette):
    """帧 -> QImage（应用当前色表颜色、裁剪、清空）。"""
    im = load(frame.source_path)
    if im.mode == "P" and palette is not None:
        im = im.copy()
        apply_palette(im, palette)
    if frame.crop:
        x, y, w, h = frame.crop
        im = im.crop((x, y, x + w, y + h))
    if frame.cleared:
        return transparent_qimage(im.size)
    return pil_to_qimage(im)


def render_frame_isolated(frame, palette, color):
    """颜色隔离渲染：只有等于 color 的像素保持不透明，其余弱化为半透明。"""
    im = load(frame.source_path)
    if im.mode == "P" and palette is not None:
        im = im.copy()
        apply_palette(im, palette)
    rgba = im.convert("RGBA")
    if frame.crop:
        x, y, w, h = frame.crop
        rgba = rgba.crop((x, y, x + w, y + h))
    if frame.cleared:
        return transparent_qimage(rgba.size)
    tr, tg, tb = color
    r, g, b, a = rgba.split()
    mr = r.point(lambda v: 255 if v != tr else 0)
    mg = g.point(lambda v: 255 if v != tg else 0)
    mb = b.point(lambda v: 255 if v != tb else 0)
    diff = ImageChops.lighter(ImageChops.lighter(mr, mg), mb)  # 0 = 精确匹配
    iso = diff.point(lambda v: ISO_ALPHA if v else 255)
    rgba.putalpha(ImageChops.multiply(a, iso))
    return pil_to_qimage(rgba)


class ImageCache:
    def __init__(self):
        self._c = {}

    def clear(self):
        self._c.clear()

    def invalidate(self, path):
        """使某个文件的所有缓存失效（铅笔涂画后调用）。"""
        for k in [k for k in self._c if k[1] == path]:
            del self._c[k]

    def get(self, frame, project):
        w, h = frame.display_size()
        if frame.is_empty or frame.source_path is None:
            return transparent_qimage((w, h))
        pal = project.find_palette(frame.palette_id) if frame.palette_id else None
        key = ("n", frame.source_path, frame.palette_id,
               pal.version if pal else -1, frame.crop, frame.cleared)
        img = self._c.get(key)
        if img is None:
            if len(self._c) > 800:
                self._c.clear()
            try:
                img = render_frame(frame, pal)
            except Exception:  # 文件丢失等情况
                img = transparent_qimage((w, h))
            self._c[key] = img
        return img

    def get_iso(self, frame, project, color):
        """颜色隔离渲染（画布用）。"""
        w, h = frame.display_size()
        if frame.is_empty or frame.source_path is None:
            return transparent_qimage((w, h))
        pal = project.find_palette(frame.palette_id) if frame.palette_id else None
        key = ("iso", frame.source_path, frame.palette_id,
               pal.version if pal else -1, frame.crop, frame.cleared, color)
        img = self._c.get(key)
        if img is None:
            if len(self._c) > 800:
                self._c.clear()
            try:
                img = render_frame_isolated(frame, pal, color)
            except Exception:
                img = transparent_qimage((w, h))
            self._c[key] = img
        return img


def preview_qimage(path, palette=None, max_side=180):
    im = load(path)
    if im.mode == "P" and palette is not None:
        im = im.copy()
        apply_palette(im, palette)
    q = pil_to_qimage(im)
    if max(q.width(), q.height()) > max_side:
        q = q.scaled(max_side, max_side, Qt.KeepAspectRatio, Qt.FastTransformation)
    return q


class TempStore:
    """存放强制转换/涂画出的索引图的临时目录。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="fgedit_")
        self.n = 0

    def path(self, tag="conv"):
        self.n += 1
        return os.path.join(self.dir, f"{tag}_{self.n:05d}.png")


def export_frame_image(frame, palette, out_path):
    """把帧（含裁剪/清空/当前色表）烘焙为索引 PNG。"""
    if frame.is_empty or frame.source_path is None:
        im = Image.new("P", frame.display_size(), 0)
        apply_palette(im, palette)
        im.info["transparency"] = 0
        im.save(out_path)
        return
    im = load(frame.source_path)
    if im.mode == "P":
        im = im.copy()
        apply_palette(im, palette)
    else:
        im = quantize_to_indexed(im)
    if frame.cleared:
        t = palette.transparent_index if (palette and palette.transparent_index is not None) else 0
        cl = Image.new("P", im.size, t)
        apply_palette(cl, palette)
        cl.info["transparency"] = t
        im = cl
    if frame.crop:
        x, y, w, h = frame.crop
        im = im.crop((x, y, x + w, y + h))
    im.save(out_path)



# ====================================================================
# 原 models.py
# ====================================================================

def _new_id():
    return uuid.uuid4().hex[:12]


class Palette:
    """索引颜色表，固定 256 项 (r, g, b)。"""

    def __init__(self, colors, name="", transparent_index=None, pid=None):
        self.id = pid or _new_id()
        self.colors = [tuple(map(int, c)) for c in colors][:256]
        self.colors += [(0, 0, 0)] * (256 - len(self.colors))
        self.name = name
        self.transparent_index = transparent_index
        self.version = 0  # 每次修改颜色/透明索引 +1，用于渲染缓存失效

    def key(self):
        return (tuple(self.colors), self.transparent_index)

    def to_dict(self):
        return {"id": self.id, "name": self.name,
                "colors": [list(c) for c in self.colors],
                "tidx": self.transparent_index, "ver": self.version}

    @classmethod
    def from_dict(cls, d):
        p = cls(d["colors"], d.get("name", ""), d.get("tidx"), d.get("id"))
        p.version = d.get("ver", 0)
        return p


class FrameData:
    """轨道上的一帧。
    - source_path 为 None 且 is_empty=True  => 空帧（确实存在但完全透明）
    - 帧格为 None（列表里的 None）         => 空位
    """

    def __init__(self, source_path=None, palette_id=None,
                 orig_size=(64, 64), is_empty=False):
        self.source_path = source_path
        self.palette_id = palette_id
        self.offset_x = 0
        self.offset_y = 0
        self.crop = None            # None 或 (x, y, w, h)，相对原图像素
        self.cleared = False        # 清空帧：全部像素透明
        self.is_empty = is_empty
        self.orig_size = (int(orig_size[0]), int(orig_size[1]))

    def display_size(self):
        if self.crop:
            return self.crop[2], self.crop[3]
        return self.orig_size

    def clone(self):
        f = FrameData(self.source_path, self.palette_id,
                      self.orig_size, self.is_empty)
        f.offset_x, f.offset_y = self.offset_x, self.offset_y
        f.crop = tuple(self.crop) if self.crop else None
        f.cleared = self.cleared
        return f


class Track:
    def __init__(self, name="轨道"):
        self.name = name
        self.muted = False
        self.frames = []  # FrameData 或 None(空位)

    def frame_at(self, i):
        return self.frames[i] if 0 <= i < len(self.frames) else None


class Project:
    def __init__(self):
        self.tracks = [Track("轨道1")]
        self.palettes = []
        self.registry = {}       # 原始图片绝对路径 -> {"pal": palette_id, "conv": 转换后临时文件或None}
        self.folder_roots = []   # 文件夹列表中的根目录（可多个）
        self.align_mode = "center"   # topleft / center / fixed
        self.fixed_point = [0, 0]
        self.playhead = 0
        self.current_track = 0
        self.file_path = None
        self.ui = {}             # 画布缩放、轨道尺寸等界面状态

    # ---- 调色板 ----
    def find_palette(self, pid):
        for p in self.palettes:
            if p.id == pid:
                return p
        return None

    def add_palette(self, colors, tidx=None, name=None):
        """按内容去重；已存在则直接返回。返回 (palette, 是否新建)。"""
        colors = [tuple(map(int, c)) for c in colors]
        key = (tuple(colors[:256] + [(0, 0, 0)] * max(0, 256 - len(colors))), tidx)
        for p in self.palettes:
            if p.key() == key:
                return p, False
        p = Palette(colors, name or f"颜色表{len(self.palettes) + 1}", tidx)
        self.palettes.append(p)
        return p, True

    def max_frames(self):
        return max((len(t.frames) for t in self.tracks), default=0)

    # ---- 序列化 ----
    def to_dict(self, base=None):
        """base 给出时，位于其下的路径存为 rel: 相对路径（配合路径表避免重复字段）。"""
        paths, pid_of = [], {}

        def P(p):
            if p is None:
                return -1
            if base:
                try:
                    rp = os.path.relpath(p, base)
                    if not rp.startswith(".."):
                        p = "rel:" + rp
                except ValueError:
                    pass
            if p not in pid_of:
                pid_of[p] = len(paths)
                paths.append(p)
            return pid_of[p]

        pal_ids = [pal.id for pal in self.palettes]

        def PI(i):
            return pal_ids.index(i) if i in pal_ids else -1

        tracks = []
        for t in self.tracks:
            fr = []
            for f in t.frames:
                if f is None:
                    fr.append(None)
                else:
                    fr.append({"src": P(f.source_path), "pal": PI(f.palette_id),
                               "ox": f.offset_x, "oy": f.offset_y, "crop": f.crop,
                               "clr": f.cleared, "emp": f.is_empty,
                               "sz": list(f.orig_size)})
            tracks.append({"name": t.name, "muted": t.muted, "frames": fr})
        return {"version": 2, "paths": paths,
                "palettes": [p.to_dict() for p in self.palettes],
                "tracks": tracks,
                "registry": {P(k): {"pal": v["pal"],
                                    "conv": P(v.get("conv"))}
                             for k, v in self.registry.items()},
                "folder_roots": [P(p) for p in self.folder_roots],
                "align_mode": self.align_mode, "fixed_point": self.fixed_point,
                "playhead": self.playhead, "current_track": self.current_track,
                "ui": self.ui}

    @classmethod
    def from_dict(cls, d, base=None):
        def RP(i):
            if i is None or i < 0:
                return None
            p = d["paths"][i]
            if p.startswith("rel:") and base:
                return os.path.normpath(os.path.join(base, p[4:]))
            return p

        proj = cls()
        proj.palettes = [Palette.from_dict(pd) for pd in d["palettes"]]
        pal_ids = [p.id for p in proj.palettes]
        proj.tracks = []
        for td in d["tracks"]:
            t = Track(td["name"])
            t.muted = td["muted"]
            for fd in td["frames"]:
                if fd is None:
                    t.frames.append(None)
                else:
                    pid = pal_ids[fd["pal"]] if 0 <= fd["pal"] < len(pal_ids) else None
                    f = FrameData(RP(fd["src"]), pid, fd.get("sz", [64, 64]),
                                  fd.get("emp", False))
                    f.offset_x, f.offset_y = fd.get("ox", 0), fd.get("oy", 0)
                    f.crop = tuple(fd["crop"]) if fd.get("crop") else None
                    f.cleared = fd.get("clr", False)
                    t.frames.append(f)
            proj.tracks.append(t)
        if not proj.tracks:
            proj.tracks = [Track("轨道1")]
        proj.registry = {RP(int(k)): {"pal": v["pal"], "conv": RP(v.get("conv", -1))}
                         for k, v in d.get("registry", {}).items()}
        frs = d.get("folder_roots")
        if frs is not None:                          # v2 格式
            proj.folder_roots = [p for p in (RP(i) for i in frs) if p]
        else:                                        # 兼容 v1 单根目录
            r = RP(d.get("folder_root", -1))
            proj.folder_roots = [r] if r else []
        proj.align_mode = d.get("align_mode", "center")
        proj.fixed_point = d.get("fixed_point", [0, 0])
        proj.playhead = d.get("playhead", 0)
        proj.current_track = min(d.get("current_track", 0), len(proj.tracks) - 1)
        proj.ui = d.get("ui", {})
        return proj



# ====================================================================
# 原 undo.py
# ====================================================================

class UndoManager:
    def __init__(self, limit=30):
        self.undo_stack = []
        self.redo_stack = []
        self.limit = limit

    def push(self, state):
        self.undo_stack.append(copy.deepcopy(state))
        if len(self.undo_stack) > self.limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self, current):
        if not self.undo_stack:
            return None
        self.redo_stack.append(copy.deepcopy(current))
        return self.undo_stack.pop()

    def redo(self, current):
        if not self.redo_stack:
            return None
        self.undo_stack.append(copy.deepcopy(current))
        return self.redo_stack.pop()

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()



# ====================================================================
# 原 bor_undo.py
# ====================================================================

State = namedtuple("State", "path text anim frame label kind")

ADD, DEL, MOD = "add", "del", "mod"


class BorUndo:
    """撤销 / 重做栈。

    - begin(label, kind) 记录“修改前”的状态
    - commit()           在一次编辑结束后把新状态入栈
    - set_current(state) 只更新当前状态（切换实体 / 动画这种非编辑动作）
    """

    def __init__(self, limit=100):
        self.limit = limit
        self._undo = []
        self._redo = []
        self._cur = None
        self._before = None       # begin() 之后、commit() 之前的暂存

    # ---- 状态 ----
    def clear(self, state=None):
        self._undo.clear()
        self._redo.clear()
        self._cur = state
        self._before = None

    def set_current(self, state):
        """非编辑动作（切换实体 / 动画）后更新当前状态，不入栈。"""
        self._cur = state

    @property
    def current(self):
        return self._cur

    def begin(self, state):
        if self._cur is None:
            self._cur = state
        self._before = state

    def commit(self, state):
        """一次编辑完成：把 begin() 记下的前态压入撤销栈。"""
        before = self._before
        self._before = None
        if before is None:
            self._cur = state
            return
        if before.path == state.path and before.text == state.text:
            self._cur = state          # 内容没变，不算一次编辑
            return
        self._undo.append(before)
        if len(self._undo) > self.limit:
            del self._undo[0]
        self._redo.clear()
        self._cur = state

    # ---- 回溯 ----
    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if not self._undo:
            return None
        st = self._undo.pop()
        if self._cur is not None:
            self._redo.append(self._cur)
        self._cur = st
        self._before = None
        return st

    def redo(self):
        if not self._redo:
            return None
        st = self._redo.pop()
        if self._cur is not None:
            self._undo.append(self._cur)
        self._cur = st
        self._before = None
        return st

    def last_label(self, redo=False):
        st = self._redo[-1] if (redo and self._redo) else (
            self._undo[-1] if self._undo else None)
        return st.label if st else ""



# ====================================================================
# 原 bor_style.py
# ====================================================================

# ============================================================ 样式（浅色）
# 注意：不设置 min-height，高度一律由内容+padding 决定，避免文字被裁切。
QSS = """
#borRoot { background:#eceef1; }

QLabel { background:transparent; color:#2b2f36; }
QLabel[role="section"] { color:#6b7280; font-weight:bold; padding-top:4px; }
QLabel[role="dim"] { color:#8a919c; font-size:12px; }
QLabel[role="title"] { color:#1c1f26; font-size:14px; }
QLabel[role="warnText"] { color:#c07a1a; font-weight:bold; }
QLabel[role="unit"] { color:#3f4650; font-size:12px; }

QPushButton { background:#f6f7f9; border:1px solid #ccd1d8; border-radius:4px;
              padding:4px 12px; color:#2b2f36; }
QPushButton:hover { background:#eaeef4; border-color:#b9c2cd; }
QPushButton:pressed { background:#dfe5ee; }
QPushButton:disabled { background:#f0f1f4; color:#a0a6b0; border-color:#d8dce2; }
QPushButton:checked { background:#d5e4f7; border-color:#2f6db3; color:#1c4e8a; }
QPushButton#primaryBtn { background:#2f6db3; border:1px solid #2f6db3;
                         color:#ffffff; font-weight:bold; }
QPushButton#primaryBtn:hover { background:#3a7cc4; }
QPushButton#primaryBtn:pressed { background:#265d99; }
QPushButton#loopBtn:checked { background:#e3f3e8; border-color:#3fae5f;
                              color:#24743f; }

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextEdit {
  background:#ffffff; border:1px solid #c9ced6; border-radius:3px;
  padding:3px 5px; color:#2b2f36;
  selection-background-color:#bcd6f5; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus { border-color:#2f6db3; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
  background:#f0f1f4; color:#9aa1ab; border-color:#d8dce2; }
QLineEdit[readOnly="true"] { background:#f4f5f7; color:#6b7280; }
QComboBox::drop-down { border:none; width:20px; }
QComboBox QAbstractItemView { background:#ffffff; color:#2b2f36;
  border:1px solid #ccd1d8; selection-background-color:#3a7bd5; }

QCheckBox { color:#2b2f36; spacing:6px; background:transparent; }

QListWidget, QTreeWidget { background:#ffffff; border:1px solid #d9dce1;
  border-radius:4px; color:#2b2f36; outline:0;
  alternate-background-color:#f6f7f9; }
QListWidget::item, QTreeWidget::item { padding:3px 4px; }
QListWidget::item:selected, QTreeWidget::item:selected {
  background:#3a7bd5; color:#ffffff; }
QListWidget::item:hover:!selected, QTreeWidget::item:hover:!selected {
  background:#eef2f8; }
QHeaderView::section { background:#f4f5f7; color:#6b7280; border:none;
  border-bottom:1px solid #d9dce1; padding:4px 6px; }

QScrollArea { background:transparent; border:none; }
QScrollBar:vertical { background:transparent; width:10px; margin:0px; }
QScrollBar::handle:vertical { background:#c3c9d2; border-radius:5px;
  min-height:26px; }
QScrollBar::handle:vertical:hover { background:#a9b1bd; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0px; }
QScrollBar:horizontal { background:transparent; height:10px; margin:0px; }
QScrollBar::handle:horizontal { background:#c3c9d2; border-radius:5px;
  min-width:26px; }
QScrollBar::handle:horizontal:hover { background:#a9b1bd; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
  background:none; }

QProgressBar { background:#ffffff; border:1px solid #d9dce1; border-radius:3px; }
QProgressBar::chunk { background:#2f6db3; border-radius:2px; }

QSplitter::handle { background:#dfe2e7; }
QSplitter::handle:horizontal { width:4px; }
QSplitter::handle:hover { background:#2f6db3; }

QToolTip { background:#ffffff; color:#2b2f36; border:1px solid #b9c2cd; }
"""

# 继承值标记（沿用名称 YELLOW_BG）：柔和琥珀底
YELLOW_BG = ("QLineEdit{background:#fff3cf;border:1px solid #d9b84a;"
             "color:#7a5b12;}")

# 列表错误 / 警告标记
ERR_BG = QColor(207, 75, 71)
ERR_FG = QColor(255, 255, 255)
WARN_BG = QColor(217, 167, 62)
WARN_FG = QColor(255, 255, 255)

MARK_COLORS = {"jumpframe": QColor(63, 174, 95),
               "dropframe": QColor(224, 149, 46),
               "landframe": QColor(58, 123, 213)}

# 框线可选色（只需最鲜艳的几种）
VIVID_COLORS = ["#ff2323", "#ff7a18", "#ffd21e", "#3ddc63",
                "#00e0ff", "#2f7bff", "#a45cff", "#ff4fd8", "#f2f4f8"]

# “展开更多”按钮：虚线蓝框，和普通按钮区分开
MORE_BTN_QSS = ("QPushButton{color:#2f6db3;background:#eef4fc;"
                "border:1px dashed #94b6df;border-radius:4px;padding:3px 10px;}"
                "QPushButton:hover{background:#ddebfb;border:1px solid #2f6db3;}"
                "QPushButton:pressed{background:#cfe1f7;}")

# 模式切换按钮（动画编辑 / 文本编辑 / 对照模式）
MODE_BTN_QSS = ("QPushButton{padding:3px 10px;}"
                "QPushButton:checked{background:#2f6db3;color:#ffffff;"
                "border:1px solid #2f6db3;font-weight:bold;}"
                "QPushButton:checked:hover{background:#3a7cc4;}")

# 对照模式里“文本跟随跳行”的闪光色（增=绿 / 删=红 / 改=黄）
FLASH_ADD = QColor(120, 220, 140)
FLASH_DEL = QColor(240, 120, 120)
FLASH_MOD = QColor(255, 226, 90)



# ====================================================================
# 兼容别名：合并前这些是独立模块，老代码按模块名调用的地方照旧可用
# ====================================================================
iu = sys.modules[__name__]
