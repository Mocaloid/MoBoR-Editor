# -*- coding: utf-8 -*-
"""OpenBOR 的**数据**层：手册关键字表、实体 TXT 解析与写回、
关卡（levels.txt / level 文件）解析与写回、drawmethod 解析、
动画位移与攻击模拟。

（由 bor_manual_data.py、openbor.py、bor_level.py、bor_dm.py、
bor_sim.py 合并而来）

两条铁律：
1. txt 是最终依据 —— 改一处只重写那一行，注释 / 空行 / 脚本块原样保留
2. 实体 txt 与关卡 txt 排版规则不同，各自的 canon / write_line 不要混用"""

import difflib
import os
import re
import sys

from PyQt5.QtCore import (QRectF)
from PyQt5.QtGui import (QColor, QPainter, QTransform)

# 本项目的其它模块
from core import clamp, iu



# ====================================================================
# 原 bor_manual_data.py
# ====================================================================

BUILTIN_FUNCS = frozenset([
    "NULL",
    "allocscreen",
    "bindentity",
    "changedrawmethod",
    "changelevelproperty",
    "changelight",
    "changepalette",
    "changeshadowcolor",
    "checkrange",
    "clearglobalvar",
    "clearlocalvar",
    "clearspawnentry",
    "damageentity",
    "drawbox",
    "drawdot",
    "drawline",
    "drawlinetoscreen",
    "drawscreen",
    "drawsprite",
    "drawspritetoscreen",
    "drawstring",
    "executeanimation",
    "findtarget",
    "free",
    "getentity",
    "getentityvar",
    "getglobalvar",
    "getindexedvar",
    "getlocalvar",
    "getplayerproperty",
    "getscriptvar",
    "isempty",
    "jumptobranch",
    "killentity",
    "loadmodel",
    "loadsprite",
    "openborconstant",
    "openborvariant",
    "performattack",
    "playerkeys",
    "playgif",
    "playmusic",
    "playsample",
    "playwebm",
    "rand",
    "rgbcolor",
    "setdrawmethod",
    "setentityvar",
    "setglobalvar",
    "setidle",
    "setindexedvar",
    "setlocalvar",
    "setscriptvar",
    "setspawnentry",
    "spawn",
    "srand",
    "tossentity",
    "updateframe",
])

ENTITY_EVENT_SCRIPTS = frozenset([
    "animationscript",
    "didblockscript",
    "didhitscript",
    "inholescript",
    "keyscript",
    "onblockascript",
    "onblockoscript",
    "onblockpscript",
    "onblocksscript",
    "onblockzscript",
    "ondeathscript",
    "ondrawscript",
    "onfallscript",
    "onkillscript",
    "onmodelcopyscript",
    "onmoveascript",
    "onmovexscript",
    "onpainscript",
    "onspawnscript",
    "takedamagescript",
    "thinkscript",
])

HEADER_FIELDS = frozenset([
    "aggression",
    "aimove",
    "aironly",
    "alpha",
    "alternatepal",
    "animal",
    "antigrab",
    "antigravity",
    "atchain",
    "attackthrottle",
    "backpain",
    "bflash",
    "blockodds",
    "blockpain",
    "bomb",
    "boomerang",
    "boomerangvalues",
    "bounce",
    "branch",
    "candamage",
    "cantgrab",
    "chargerate",
    "cmd",
    "com",
    "combostyle",
    "counter",
    "credit",
    "defense",
    "diesound",
    "dust",
    "edelay",
    "escapehits",
    "facing",
    "falldie",
    "flash",
    "fmap",
    "gfxshadow",
    "globalmap",
    "grabback",
    "grabdistance",
    "grabfinish",
    "grabflip",
    "grabforce",
    "grabturn",
    "grabwalk",
    "guardpoints",
    "guardrate",
    "health",
    "height",
    "hitenemy",
    "hmap",
    "holdblock",
    "hostile",
    "icon",
    "icondie",
    "iconget",
    "iconmphalf",
    "iconmphigh",
    "iconmplow",
    "iconpain",
    "iconposition",
    "iconw",
    "instantitemdeath",
    "jugglepoints",
    "jumpheight",
    "jumpmove",
    "jumpspeed",
    "jumpspeedf",
    "knife",
    "knockdowncount",
    "lifebarstatus",
    "lifeposition",
    "lifespan",
    "load",
    "makeinv",
    "modelflag",
    "mp",
    "mprate",
    "mpset",
    "name",
    "nameposition",
    "no_adjust_base",
    "noatflash",
    "nodieblink",
    "nodrop",
    "nohithead",
    "nolife",
    "nomove",
    "nopain",
    "nopassiveblock",
    "noquake",
    "offense",
    "offscreen_noatk_factor",
    "offscreenkill",
    "paingrab",
    "palette",
    "parrow",
    "parrow2",
    "playshot",
    "playshotno",
    "project",
    "projectilehit",
    "reload",
    "remap",
    "remove",
    "rider",
    "riseattacktype",
    "riseinv",
    "risetime",
    "running",
    "score",
    "scroll",
    "secret",
    "setlayer",
    "shadow",
    "shootnum",
    "sleepwait",
    "speed",
    "speedf",
    "star",
    "stealth",
    "subject_to_basemap",
    "subject_to_gravity",
    "subject_to_hole",
    "subject_to_maxz",
    "subject_to_minz",
    "subject_to_obstacle",
    "subject_to_platform",
    "subject_to_screen",
    "subject_to_wall",
    "subtype",
    "summonkill",
    "thold",
    "throw",
    "throwdamage",
    "throwframewait",
    "toflip",
    "turndelay",
    "type",
    "typeshot",
    "walkoffmove",
    "weaploss",
    "weapnum",
    "weapons",
])


# 会被当作图片引用检查的 header 字段
IMAGE_HEADER_FIELDS = frozenset([
    "icon", "iconpain", "icondie", "iconget", "iconw",
    "iconmphigh", "iconmphalf", "iconmplow",
])

# 会被当作音频引用检查的 header 字段
SOUND_HEADER_FIELDS = frozenset(["diesound"])



# ====================================================================
# 原 openbor.py
# ====================================================================

FRAME_EXTS = (".gif", ".bmp", ".pcx", ".png")
AUDIO_EXTS = (".wav", ".ogg", ".mp3")

# 一行里“字段名与值之间”用几个制表符：字段名长于这个数就只用一个
# （界面上“一键优化”与动画编辑写回文本共用这一条规则，只此一份）
HEADER_TAB_WIDTH = 9


def canon_line(cmd, args, in_anim=False, comment=""):
    """按“一键优化”的规范拼出一行命令。

    anim 内：  1 个制表符缩进 + 命令 + 1 个制表符 + 值（值之间 1 个空格）
    Header：   命令 + （字段名超过 9 字符用 1 个制表符，否则 2 个） + 值

    动画编辑区把改动写回 txt 时，凡是被改到的行都用这套规范重排，
    保证“GUI 里看到的”落到文件里也是规范、一致的文本。
    """
    args = [str(a) for a in (args or [])]
    if in_anim:
        body = "\t" + cmd + ("\t" + " ".join(args) if args else "")
    else:
        sep = "\t" if len(cmd) > HEADER_TAB_WIDTH else "\t\t"
        body = cmd + (sep + " ".join(args) if args else "")
    c = (comment or "").strip()
    return body + (" " + c if c else "")

# ---------------- 保留动画名 ----------------
_BASE_ANIMS = {
    "waiting", "select", "spawn", "respawn", "idle", "faint", "sleep",
    "walk", "walkoff", "backwalk", "turn", "up", "down", "duck", "ducking",
    "duckrise", "land", "run", "backrun", "jump", "jumpdelay", "jumpland",
    "forwardjump", "runjump", "dodge", "get", "jumpcant", "charge", "cant",
    "grab", "grabwalk", "grabbackwalk", "grabwalkup", "grabwalkdown",
    "grabturn", "slide", "runslide", "edge", "backedge", "victory", "lose",
    "chargeattack", "attackboth", "upper", "runattack", "runjumpattack",
    "jumpattack", "jumpforward", "jumpattack2", "jumpattack3", "jumpspecial",
    "attackup", "attackdown", "attackforward", "attackbackward", "special",
    "special2", "special3", "grabattack", "grabattack2", "grabforward", "grabforward2",
    "grabup", "grabup2", "grabdown", "grabdown2", "grabbackward",
    "grabbackward2", "throw", "duckattack",
    "spain", "bpain", "shock", "burn", "bdie", "sdie", "chipdeath",
    "block", "guardbreak", "grabbed", "grabbedwalk", "grabbedbackwalk",
    "grabbedwalkup", "grabbedwalkdown", "grabbedturn", "hitwall",
    "hitplatform", "hitobstacle", "riseb", "rises", "riseattackb",
    "riseattacks",
}
# 可带编号的动画族: (前缀, models.txt 上限字段, 默认上限)
_NUMBERED_ANIMS = {
    "idle": ("maxidles", 1), "walk": ("maxwalks", 1),
    "backwalk": ("maxbackwalks", 1), "up": ("maxups", 1),
    "down": ("maxdowns", 1),
    "attack": ("maxattacks", 4), "follow": ("maxfollows", 4),
    "freespecial": ("maxfreespecials", 8),
    "pain": ("maxattacktypes", 10), "fall": ("maxattacktypes", 10),
    "rise": ("maxattacktypes", 10), "death": ("maxattacktypes", 10),
    "blockpain": ("maxattacktypes", 10), "riseattack": ("maxattacktypes", 10),
    "backpain": ("maxattacktypes", 10), "backfall": ("maxattacktypes", 10),
    "backrise": ("maxattacktypes", 10), "backdeath": ("maxattacktypes", 10),
    "backriseattack": ("maxattacktypes", 10),
}

_FRAME_PROP_CMDS = {
    "delay", "offset", "bbox", "frame", "alphamask", "attack", "blast",
    "shock", "burn", "freeze", "steal", "move", "movea", "movez", "seta",
    "platform", "sound", "fshadow", "shadowcoords", "itembox", "drawmethod",
    "nodrawmethod", "hitfx", "hitflash", "idle", "@cmd", "grabin",
    "forcedirection", "damageonlanding", "dropv", "nokill", "stun", "seal",
    "forcemap", "noreflect", "jugglecost", "otg", "guardcost", "dot",
    "staydown",
}

_ENTITY_TYPES = {"player", "enemy", "npc", "item", "none", "steamer",
                 "obstacle", "text", "trap", "endlevel", "pshot", "panel"}

# 特殊攻击类型（替代 attack{#} 的独立命令）
_ATTACK_CMDS = {"blast", "shock", "burn", "freeze", "steal"}

# drawmethod 的“新写法”子命令：`drawmethod rotate 30`、`drawmethod scale 1.5`…
# 老写法是一整行 10 个整数：`drawmethod 256 256 1 0 0 0 0 0 0 0`
DM_SUB_CMDS = {
    "scale", "scalex", "scaley", "flipx", "flipy", "shiftx", "rotate",
    "fliprotate", "fillcolor", "remap", "alpha", "channel", "channelr",
    "channelg", "channelb", "tintmode", "tintcolor", "clip", "clipx",
    "clipy", "clipw", "cliph",
}


def dm_sub_cmd(args):
    """这一行 drawmethod 的子命令；老写法（一整行整数）返回 ""。"""
    if args and str(args[0]).lower() in DM_SUB_CMDS:
        return str(args[0]).lower()
    return ""

# 允许**重复出现**的 header 字段：即使已经有了，也还能再新建一条。
# load（加载实体）、alternatepal / remap（换色表）、com（注释）都是多值的。
HEADER_MULTI_CMDS = {"load", "alternatepal", "remap", "com"}

# ---------------- anim 级命令全集 ----------------
# 注意：这里只列“命令名”，纯粹是解析 / 校验用的。
# 每个字段在界面上长什么样（整数框 / 下拉框 / 范围 / 放在哪一行）
# 全部写在 openbor_ui.py 里，改界面不需要动本文件。
_ANIM_LEVEL_SET = {
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
# 帧核心命令（不进入附加列表）
_CORE_FRAME_CMDS = {"delay", "offset", "bbox", "drawmethod", "nodrawmethod",
                    "frame"}

# ---- 动画对实体类型的适用性（依手册；未列出的动画不检查） ----
_PLAYER_ONLY_ANIMS = {
    "waiting", "select", "sleep", "jumpcant", "charge", "chargeattack",
    "slide", "runslide", "edge", "backedge", "victory", "lose",
    "attackboth", "runattack", "runjumpattack", "jumpattack3",
    "attackup", "attackdown", "attackforward", "attackbackward",
    "grabforward", "grabforward2", "grabup", "grabup2", "grabdown",
    "grabdown2", "grabbackward", "grabbackward2", "grabwalk", "grabwalkup",
    "grabwalkdown", "grabbackwalk", "grabturn", "dodge", "runjump", "cant",
    "land", "chipdeath", "duckattack",
}
_ENEMY_ONLY_ANIMS = {"upper"}

# header 字段最小必填参数个数（缺省视为 1；只警告“少于最小值”，
# 多出来的可选参数缺失不算问题）
_HEADER_MIN = {
    "nomove": 2, "jumpmove": 1, "walkoffmove": 2, "throw": 2, "noquake": 2,
    "hitenemy": 2, "stealth": 2, "attackthrottle": 2, "boomerangvalues": 2,
    "remap": 2, "hmap": 2, "komap": 2, "makeinv": 1, "risetime": 2,
    "riseinv": 1, "weaploss": 2, "score": 1, "secret": 2, "defense": 2,
    "running": 1, "mpset": 1, "edelay": 1, "dust": 1, "parrow": 1,
    "smartbomb": 1, "atchain": 1, "icon": 1, "noquake": 1, "nomove": 1, 
}

# header 字段 -> 参数个数（用于基本校验；None 表示不校验个数）
_HEADER_ARITY = {
    "name": 1, "type": 1, "health": 1, "mp": 1, "speed": 1, "speedf": 1,
    "running": 5, "jumpspeed": 1, "jumpspeedf": 1,
    "jumpheight": 1, "jumpmove": 2, "walkoffmove": 2, "turndelay": 1,
    "facing": 1, "chargerate": 1, "mprate": 1, "nohithead": 1,
    "backpain": 1, "summonkill": 1, "grabdistance": 1, "grabback": 1,
    "grabfinish": 1, "grabwalk": 1, "grabturn": 1, "cantgrab": 1,
    "paingrab": 1, "antigrab": 1, "grabforce": 1, "grabflip": 1,
    "throwdamage": 1, "throw": 2, "throwframewait": 1, "height": 1,
    "antigravity": 1, "bounce": 1, "no_adjust_base": 1,
    "subject_to_basemap": 1, "subject_to_wall": 1, "subject_to_hole": 1,
    "subject_to_obstacle": 1, "subject_to_platform": 1,
    "subject_to_gravity": 1, "subject_to_screen": 1, "subject_to_minz": 1,
    "subject_to_maxz": 1, "offscreenkill": 1, "aggression": 1,
    "hitenemy": 2, "shadow": 1, "aironly": 1, "gfxshadow": 2, "alpha": 1,
    "parrow": 3, "parrow2": 3, "diesound": 1, "setlayer": 1, "load": 1,
    "knife": 1, "star": 1, "boomerang": 1, "bomb": 1, "pbomb": 1,
    "playshot": 1, "playshotno": 1, "rider": 1, "flash": 1, "bflash": 1,
    "dust": 3, "toflip": 1, "noatflash": 1, "atchain": None, "combostyle": 1,
    "blockodds": 1, "thold": 1, "blockpain": 1, "nopassiveblock": 1,
    "holdblock": 1, "guardpoints": 1, "guardrate": 1,
    "offscreen_noatk_factor": 1, "nopain": 1, "nodrop": 1,
    "knockdowncount": 1, "remove": 1, "escapehits": 1, "nodieblink": 1,
    "makeinv": 2, "falldie": 1, "death": 1, "risetime": 2,
    "riseattacktype": 1, "riseinv": 2, "jugglepoints": 1,
    "instantitemdeath": 1, "palette": 1, "alternatepal": 1, "remap": 2,
    "fmap": 1, "hmap": 2, "globalmap": 1, "komap": 2, "shootnum": 1,
    "counter": 1, "reload": 1, "typeshot": 1, "animal": 1, "weapnum": 1,
    "icon": 2, "nolife": 1, "credit": 1, "sleepwait": 1, "lifespan": 1,
    "scroll": 1, "modelflag": 1, "subtype": 1, "aimove": None,
    "hostile": None, "candamage": None, "projectilehit": None,
    "stealth": 2, "mpset": 6, "edelay": 6, "weapons": None, "weaploss": 2,
    "score": 2, "smartbomb": 4, "branch": 1, "secret": 2, "project": 1,
    "offense": 2, "defense": 8, "boomerangvalues": 2, "com": None,
    "cmd": None, "iconpain": 1, "icondie": 1, "iconget": 1, "iconw": 1,
    "iconmphigh": 1, "iconmphalf": 1, "iconmplow": 1,
    "attackthrottle": 2,
}
# 允许“新建字段”选择的 header 字段
HEADER_CMDS = sorted(set(_HEADER_ARITY) | set(HEADER_FIELDS))
# 合法的 header 字段名（Entity Events 的 script 引用指令也算）
VALID_HEADER = (set(_HEADER_ARITY) | set(HEADER_FIELDS)
                | set(ENTITY_EVENT_SCRIPTS))
# Entity Events 的 script 引用指令（值为 .c / .h 脚本路径）
# 另：Header 里的 "script" 也是合法字段，同样可以指向 .c / .h 文件
SCRIPT_EVENTS = set(ENTITY_EVENT_SCRIPTS) | {"script"}
# 部分资料里出现、但手册正文没列全的 header 别名（避免误报）
_EXTRA_HEADER_OK = {
    "nospecial", "nograb", "noblast", "notime", "nomagic",
    "iconposition", "lifebarstatus", "lifeposition", "nameposition",
    "komap", "pbomb", "smartbomb", "death", "script",
}
VALID_HEADER |= _EXTRA_HEADER_OK


def is_script_start(cmd, args):
    """这一行是不是“脚本块的开头”。

    两种写法都合法（真实 mod 里都在用）：
        @script              —— 单独一行，最常见
        script   @script     —— script 字段的值就是一个脚本块，
                                整块（含 @script / @end_script）算作 script 的值

    夹在开头与 @end_script 之间的内容一律是 C 代码，
    任何时候都**不能**当成 Header 字段或 OpenBOR 命令去校验。
    """
    cmd = (cmd or "").lower()
    if cmd.startswith("@script"):
        return True
    return cmd == "script" and bool(args) \
        and str(args[0]).lower().startswith("@script")


def is_script_end(cmd):
    """这一行是不是脚本块的结尾。"""
    return (cmd or "").lower().startswith("@end_script")


def suggest_header_field(name):
    """为拼错的 header 字段给出最接近的合法字段名。"""
    m = difflib.get_close_matches(name.lower(), sorted(VALID_HEADER),
                                  n=1, cutoff=0.72)
    return m[0] if m else None


def split_comment(line):
    """拆出注释部分；返回 (代码部分, 注释部分含#或'')。"""
    i = line.find("#")
    if i < 0:
        return line, ""
    return line[:i], line[i:]


class Line:
    """一行实体文本。raw 始终为最新文本。"""
    __slots__ = ("raw",)

    def __init__(self, raw):
        self.raw = raw

    def tokens(self):
        code, _ = split_comment(self.raw)
        return code.split()

    def cmd(self):
        t = self.tokens()
        return t[0].lower() if t else ""

    def args(self):
        return self.tokens()[1:]

    def set_args(self, cmd, args, in_anim=None):
        """重写本行，保留行尾注释。

        in_anim 给 True/False 时：按“一键优化”的规范重排缩进与制表符
        （anim 内 1 个制表符缩进 + 命令后 1 个制表符；Header 按字段名长度
        用 2 个或 1 个制表符）。不给出时只替换命令与参数，缩进照旧。
        """
        code, comment = split_comment(self.raw)
        if in_anim is None:
            indent = code[:len(code) - len(code.lstrip())]
            self.raw = indent + cmd + (" " + " ".join(args) if args else "")
        else:
            self.raw = canon_line(cmd, args, in_anim, comment)
            return
        self.raw += ((" " + comment) if comment else "")

    def is_blank(self):
        return not self.raw.strip()


class Anim:
    def __init__(self, name, extra=None):
        self.name = name                  # 原始大小写
        self.extra = list(extra or [])    # anim 行上多余的参数（通常是非法空格）
        self.lines = []                   # List[Line]，含 anim 行之后的所有行

    # ---- 命令查找 ----
    def find_cmd(self, cmd):
        """最后一个 cmd 行 (index_in_lines, Line) 或 (None, None)。"""
        cmd = cmd.lower()
        for i in range(len(self.lines) - 1, -1, -1):
            ln = self.lines[i]
            if ln.cmd() == cmd:
                return i, ln
        return None, None

    def frames(self):
        """返回帧信息列表: [{line_idx, path, delay, offset, bbox, attack,
        drawmethod, nodm, own, extras, extras_all, extras_own}]
        - own: {delay/offset/bbox/attack/drawmethod: bool}，该属性是否在本帧
          块内声明（False 表示继承自前面帧）
        - nodm: 本帧块内是否声明了 nodrawmethod
        - dm: {子命令: 参数}，到本帧为止**生效中**的 drawmethod
          （新写法一行只能设一项，多项要写多行，所以这里按子命令攒成一张表；
           老写法是一整行 10 个整数，记在 "" 键下，并且会顶掉之前攒的全部）
        - extras: 本帧块内的附加命令行 [(line_idx, cmd, args)]
        - extras_all: {cmd: args}，到本帧为止**生效中**的帧级附加命令
          （move / movea / movez / sound … 和 delay 一样会往后继承）
        - extras_own: set，其中哪些是**本帧自己**写的（其余是继承来的）
        """
        out = []
        delay = offset = bbox = attack = dm = None
        own = {"delay": False, "offset": False, "bbox": False,
               "attack": False, "drawmethod": False}
        nodm = False
        dm_map = {}
        extras = []
        fex = {}                  # cmd -> args（跨帧继承）
        fex_own = set()           # 本帧自己声明的附加命令
        atcmd = []            # 本帧之前的 @cmd 行下标
        script = None         # 本帧之前的脚本块 (start_idx, end_idx)
        script_start = None
        for i, ln in enumerate(self.lines):
            c, a = ln.cmd(), ln.args()
            if script_start is not None:          # 处于脚本区块内
                if is_script_end(c):
                    script = (script_start, i)
                    script_start = None
                continue
            if is_script_start(c, a):
                script_start = i      # @script / “script @script” 都算块开头
                continue
            if c == "frame":
                out.append({"line_idx": i, "path": a[0] if a else "",
                            "delay": delay, "offset": offset, "bbox": bbox,
                            "attack": attack, "drawmethod": dm, "nodm": nodm,
                            "dm": dict(dm_map),
                            "own": dict(own), "extras": extras,
                            "extras_all": dict(fex),
                            "extras_own": set(fex_own),
                            "atcmd": atcmd, "script": script})
                own = {k: False for k in own}
                nodm = False
                extras = []
                atcmd = []
                script = None
                fex_own = set()       # 附加命令的值往后继承，只清“本帧自己写的”
            elif c == "delay" and a:
                delay = a
                own["delay"] = True
            elif c == "offset" and len(a) >= 2:
                offset = a[:2]
                own["offset"] = True
            elif c == "bbox":
                bbox = a
                own["bbox"] = True
            elif c.startswith("attack") or c in _ATTACK_CMDS:
                attack = (c, a)
                own["attack"] = True
            elif c == "drawmethod":
                k = dm_sub_cmd(a)
                if k:
                    dm_map[k] = list(a[1:])     # 新写法：只设这一项
                else:
                    dm = a                      # 老写法：一整行，顶掉全部
                    dm_map = {"": list(a)}
                own["drawmethod"] = True
            elif c == "nodrawmethod":
                dm = None
                dm_map = {}                     # 连继承来的也一并作废
                own["drawmethod"] = True
                nodm = True
            elif c == "@cmd":
                atcmd.append(i)
            elif c and c not in _CORE_FRAME_CMDS:
                # anim 级命令不算帧附加（即使写在帧块附近）
                if c not in _ANIM_LEVEL_SET:
                    extras.append((i, c, a))
                    fex[c] = a          # 往后继承（move 三兄弟尤其明显）
                    fex_own.add(c)
        return out

    def anim_cmds(self, exclude=()):
        """anim 级附加命令行 [(line_idx, cmd, args)]（按已知命令集过滤）。"""
        ex = {c.lower() for c in exclude}
        return [(i, ln.cmd(), ln.args()) for i, ln in enumerate(self.lines)
                if ln.cmd() in _ANIM_LEVEL_SET and ln.cmd() not in ex]


class EntityDoc:
    def __init__(self, path):
        self.path = path
        self.pre_lines = []               # 第一个 anim 之前的所有行（header 区）
        self.anims = []                   # List[Anim]
        self.scripts = []                 # 全局脚本块 [(start_idx, end_idx)]
        self.load(path)

    def load(self, path):
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            self.load_text(fp.read(), path)

    def load_text(self, text, path=None):
        """从给定文本重建文档（撤销 / 重做用，不触碰磁盘）。"""
        if path:
            self.path = path
        self.pre_lines, self.anims = [], []
        self.scripts = []
        raw_lines = text.splitlines()
        cur = None
        gs = None                         # 全局脚本块起始下标（None=不在脚本内）
        for raw in raw_lines:
            ln = Line(raw)
            c = ln.cmd()
            if gs is not None:
                if is_script_end(c):
                    self.pre_lines.append(ln)
                    self.scripts.append((gs, len(self.pre_lines) - 1))
                    gs = None
                elif c == "anim":
                    # 忘了写 @end_script 就直接开了 anim：块到这里为止，
                    # 块内内容照旧整体跳过，绝不拿去当字段校验
                    self.scripts.append((gs, len(self.pre_lines) - 1))
                    gs = None
                    a = ln.args()
                    cur = Anim(a[0] if a else "", a[1:] if len(a) > 1 else [])
                    self.anims.append(cur)
                else:
                    self.pre_lines.append(ln)
                continue
            if is_script_start(c, ln.args()):
                if cur is None:
                    self.pre_lines.append(ln)
                    gs = len(self.pre_lines) - 1
                else:
                    cur.lines.append(ln)  # anim 内脚本由 Anim.frames() 处理
                continue
            if cur is None and is_script_end(c):
                # 少数 mod 漏写了 @script 开头，直接就是 C 代码 + @end_script：
                # 往前回溯出整段脚本，照样整块跳过，不拿去当 Header 字段。
                st = self._implicit_script_start()
                if st is not None:
                    self.pre_lines.append(ln)
                    self.scripts.append((st, len(self.pre_lines) - 1))
                    continue
            if c == "anim":
                a = ln.args()
                cur = Anim(a[0] if a else "", a[1:] if len(a) > 1 else [])
                self.anims.append(cur)
            elif cur is None:
                self.pre_lines.append(ln)
            else:
                cur.lines.append(ln)
        if gs is not None:            # 文件结束了块还没关：同样整块跳过
            self.scripts.append((gs, max(gs, len(self.pre_lines) - 1)))

    def _implicit_script_start(self):
        """漏写 @script 开头时的兜底：从 pre_lines 末尾往前找块的起点。

        往回走，遇到**空行**、**已知 Header 字段名**、或**已经属于别的脚本块**
        的行就停下；中间这段连续内容视为脚本。返回起始下标，找不到则 None。
        """
        i = len(self.pre_lines) - 1
        if i < 0:
            return None
        skip = self._script_lines()
        while i >= 0:
            if i in skip:
                break
            s = self.pre_lines[i].raw.strip()
            if not s:
                break
            t = s.split()
            if t and t[0].lower() in VALID_HEADER:
                break                     # 撞上正常字段行，它不属于脚本块
            i -= 1
        st = i + 1
        return st if st <= len(self.pre_lines) - 1 else None

    # ---- header 操作（全局 @script 块单独作为特殊字段呈现） ----
    def _script_lines(self):
        s = set()
        for a, b in self.scripts:
            s.update(range(a, b + 1))
        return s

    def header_entries(self):
        """[(pre_idx, cmd, args)]，跳过空行与全局脚本块内部行；
        每个全局脚本块作为一个 "@script" 字段，值=其中内容。"""
        skip = self._script_lines()
        out = []
        for i, ln in enumerate(self.pre_lines):
            if i in skip or not ln.cmd():
                continue
            out.append((i, ln.tokens()[0], ln.args()))
        for a, b in self.scripts:
            body = [self.pre_lines[k].raw.strip() for k in range(a + 1, b)]
            body = [x for x in body if x]
            # Header 里允许两种 script 写法：路径（script x.c）或
            # @script … @end_script 块；块整体视为 script 字段的值
            out.append((a, "script",
                        [" ⏎ ".join(body)] if body else ["（空脚本）"]))
        out.sort(key=lambda t: t[0])
        return out

    def is_script_block(self, idx):
        """pre_lines[idx] 是否落在某个全局 @script … @end_script 块内。"""
        return any(a <= idx <= b for a, b in self.scripts)

    def header_get(self, cmd):
        cmd = cmd.lower()
        if cmd.startswith("@"):
            return None
        skip = self._script_lines()
        for i, ln in enumerate(self.pre_lines):
            if i in skip:
                continue
            if ln.cmd() == cmd:
                return ln.args()
        return None

    def header_set(self, cmd, args):
        cmd_l = cmd.lower()
        if cmd_l.startswith("@"):          # @script 由文本编辑维护，不改写
            return
        skip = self._script_lines()
        for i, ln in enumerate(self.pre_lines):
            if i in skip:
                continue
            if ln.cmd() == cmd_l:
                ln.set_args(cmd, args, in_anim=False)
                return
        # 追加到 header 区末尾（同样用规范制表符）
        ln = Line(canon_line(cmd, args, in_anim=False))
        self.pre_lines.append(ln)

    def header_add(self, cmd, args):
        """无条件**追加**一行 header（给 load / remap 这类可多值的字段用）。

        header_set 会把已有的同名行改掉，所以多值字段必须走这里。
        插在 header 区末尾（即第一个 anim 之前）。
        """
        cmd_l = cmd.lower()
        if cmd_l.startswith("@"):
            return
        idx = len(self.pre_lines)
        for i, ln in enumerate(self.pre_lines):
            if ln.cmd() == "anim":
                idx = i
                break
        self.pre_lines.insert(idx, Line(canon_line(cmd, args, in_anim=False)))

    def header_del(self, cmd):
        cmd_l = cmd.lower()
        if cmd_l.startswith("@"):
            return False
        skip = self._script_lines()
        for i, ln in enumerate(self.pre_lines):
            if i in skip:
                continue
            if ln.cmd() == cmd_l:
                del self.pre_lines[i]
                return True
        return False

    # ---- 序列化 ----
    def to_text(self):
        lines = [ln.raw for ln in self.pre_lines]
        for a in self.anims:
            lines.append("anim " + a.name +
                         (" " + " ".join(a.extra) if a.extra else ""))
            lines.extend(ln.raw for ln in a.lines)
        return "\n".join(lines) + "\n"

    def save(self, path=None):
        with open(path or self.path, "w", encoding="utf-8") as fp:
            fp.write(self.to_text())


# ---------------- models.txt ----------------
class ModelsTxt:
    def __init__(self, path):
        self.path = path
        self.entities = []                # [(kind, name, rel_path)]
        self.limits = {"maxattacks": 4, "maxattacktypes": 10, "maxfollows": 4,
                       "maxfreespecials": 8, "maxidles": 1, "maxwalks": 1,
                       "maxbackwalks": 1, "maxups": 1, "maxdowns": 1}
        if path:
            self.load(path)

    def load(self, path):
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            for raw in fp:
                code, _ = split_comment(raw)
                t = code.split()
                if len(t) >= 3 and t[0].lower() in ("load", "know"):
                    self.entities.append((t[0].lower(), t[1], t[2]))
                elif len(t) >= 2 and t[0].lower() in self.limits:
                    try:
                        self.limits[t[0].lower()] = int(t[1])
                    except ValueError:
                        pass


# ---------------- 路径解析 ----------------
def resolve_frame_path(root_dirs, rel):
    """按 OpenBOR 规则解析帧图路径：相对根目录，缺扩展名按 gif/bmp/pcx/png 搜索。"""
    rel = rel.replace("\\", "/")
    if rel.lower() == "none":
        return None
    for root in root_dirs:
        p = os.path.join(root, rel.replace("/", os.sep))
        base, ext = os.path.splitext(p)
        if ext:
            if os.path.exists(p):
                return p
        else:
            for e in FRAME_EXTS:
                if os.path.exists(base + e):
                    return base + e
    # 带扩展名但找不到时仍尝试缺省补全（容错）
    for root in root_dirs:
        p = os.path.join(root, rel.replace("/", os.sep))
        base, ext = os.path.splitext(p)
        for e in FRAME_EXTS:
            if os.path.exists(base + e):
                return base + e
    return None


# ---------------- 校验 ----------------
ERR, WARN = "error", "warning"


def classify_anim(name, limits):
    """返回 (规范名, 编号 or None, error_msg or None)。"""
    if name.lower() in _BASE_ANIMS:      # 先整体匹配（grabattack2 等含数字的独立名）
        return name.lower(), None, None
    m = re.match(r"^([a-zA-Z]+)(\d*)$", name)
    if not m:
        return name, None, f"非法动画名 “{name}”"
    base, num_s = m.group(1).lower(), m.group(2)
    num = int(num_s) if num_s else None
    if base in _NUMBERED_ANIMS:
        field, default_max = _NUMBERED_ANIMS[base]
        limit = limits.get(field, default_max)
        # 无编号视为 1（freespecial 例外：无编号=1）
        n = num if num is not None else 1
        if num is not None and num == 1 and base != "attack":
            # pain1/attack1 之类：attack1 合法；其余编号从语法上也接受
            pass
        if n > limit:
            return base, num, (f"“{name}” 超出 models.txt 的 {field} "
                               f"上限 {limit}（将报 Invalid animation name）")
        if num is not None and num == 0:
            return base, num, f"“{name}” 编号不能为 0"
        return base, num, None
    return base, num, f"未知动画名 “{name}”（引擎将报 Invalid animation name）"


def validate_entity(doc, models, root_dirs):
    """返回 [(severity, message, key)]。
    severity: 'error' / 'warning'
    key: None 或 ("anim", 动画名小写) 或 ("header", 字段名小写)
         —— 供 UI 把问题定位到动画列表 / Header 字段条目上。
    """
    issues = []

    def add(sev, m, key=None):
        issues.append((sev, m, key))

    def err(m, key=None):
        add(ERR, m, key)

    def warn(m, key=None):
        add(WARN, m, key)

    # ---- header ----
    name = doc.header_get("name")
    if not name:
        err("缺少必填字段 name（实体无法被访问）")
    elif " " in "\t".join(name):
        err(f"name “{' '.join(name)}” 含空格（访问实体时引擎崩溃）",
            ("header", "name"))
    elif len(name[0]) > 40:
        warn("name 超过 40 字符", ("header", "name"))

    typ = doc.header_get("type")
    if typ and typ[0].lower() not in _ENTITY_TYPES:
        err(f"未知 type “{typ[0]}”", ("header", "type"))

    # health 允许缺省（不警告）

    for _, cmd, args in doc.header_entries():
        cl = cmd.lower()
        if cl.startswith("@"):               # @script / @cmd 等不是普通 header 字段
            continue
        # 动画级 / 帧级命令却写在 anim 之外 → 不会生效，视为错误
        if cl in _FRAME_PROP_CMDS or cl in _CORE_FRAME_CMDS \
                or cl in _ANIM_LEVEL_SET:
            err(f"字段 “{cmd}” 是动画/帧级命令，却写在任何 anim 之外"
                f"（引擎不会采用，应放进某个 anim 块内）", ("header", cl))
            continue
        mn = _HEADER_MIN.get(cl)
        if cl in _HEADER_MIN and len(args) < mn:
            warn(f"header 字段 {cmd} 至少需要 {mn} 个参数，"
                 f"当前只有 {len(args)} 个", ("header", cmd.lower()))
        if cl not in VALID_HEADER:
            sug = suggest_header_field(cl)
            err(f"Header 字段 “{cmd}” 在 OpenBOR 中未定义，引擎会忽略它"
                + (f"（是否想写 “{sug}”？）" if sug else f"可能是误写或使用了版本过时的设置，点击右下角问号标志可查看OpenBOR手册"),
                ("header", cl))

    anims_by_name = {}
    has_death = any(a.name.lower().startswith("death") for a in doc.anims)
    fd = doc.header_get("falldie") or doc.header_get("death")
    if fd and fd[0] not in ("0",) and not has_death:
        err(f"falldie/death 设为 {fd[0]} 但没有 DEATH 动画（手册强制要求）")

    # ---- anims ----
    if not doc.anims:
        err("没有任何 anim 块")
    limits = models.limits if models else {}
    walk_count = None
    t = typ[0].lower() if typ else None
    for a in doc.anims:
        label = a.name or "(无名)"
        key = ("anim", a.name.lower()) if a.name else None
        if not a.name:
            err("anim 后缺少动画名")
            continue
        if a.extra:
            err(f"动画名与编号之间不能有空格（应写作 {a.name}"
                f"{''.join(a.extra)} 而非 {a.name} {' '.join(a.extra)}）", key)
        base, num, e = classify_anim(a.name, limits)
        if e:
            err(e, key)
        if a.name.lower() in anims_by_name:
            warn(f"动画 “{label}” 重复声明，只有最后一个生效", key)
        anims_by_name[a.name.lower()] = a

        # 动画对实体类型的适用性（引擎不报错但永远无法触发）
        if t in ("player", "enemy", "npc"):
            if base in _PLAYER_ONLY_ANIMS and t != "player":
                warn(f"动画 “{label}” 只有 player 实体能触发，"
                     f"当前实体类型 {t} 下永远不会播放", key)
            elif base in _ENEMY_ONLY_ANIMS and t == "player":
                warn(f"动画 “{label}” 只有 enemy 实体能触发，"
                     f"当前实体类型 player 下永远不会播放", key)

        frames = a.frames()
        if not frames:
            err(f"动画 “{label}” 没有任何 frame", key)
        n = len(frames)
        if base == "walk":
            walk_count = n
        elif base in ("up", "down") and walk_count is not None and n != walk_count:
            warn(f"动画 “{label}” 帧数({n})与 WALK 帧数({walk_count})不一致",
                 key)

        # 帧图存在性
        for fr in frames:
            p = fr["path"]
            if not p:
                err(f"动画 “{label}” 存在缺少路径的 frame 命令", key)
            elif p.lower() != "none" and \
                    resolve_frame_path(root_dirs, p) is None:
                warn(f"动画 “{label}” 引用的图片不存在: {p}", key)
            # 攻击框参数：全零 / attack 0 / >=4 个参数都合法；
            # 少于 4 个且存在非零值 → 错误（x,y,w,h 不全）
            if fr["attack"]:
                ac, aa = fr["attack"]
                if aa:
                    try:
                        vals = [float(v) for v in aa]
                    except ValueError:
                        vals = None
                    if vals is not None and not all(v == 0 for v in vals) \
                            and len(aa) < 4:
                        err(f"动画 “{label}” 的 {ac} 参数只有 {len(aa)} 个"
                            f"且存在非零值（x,y,w,h 四项不全）", key)

        # hitfx / hitflash 只在攻击框命中时才会触发
        if any(fr["attack"] for fr in frames):
            has_atk = True
        else:
            has_atk = False
        if not has_atk:
            for hc in ("hitfx", "hitflash"):
                _, hln = a.find_cmd(hc)
                if hln:
                    warn(f"动画 “{label}” 设置了 {hc} 但没有任何攻击框"
                         f"（永远不会被触发）", key)

        # 帧号命令越界
        for cmd in ("landframe", "dropframe", "jumpframe", "throwframe",
                    "shootframe", "tossframe", "flipframe", "weaponframe",
                    "spawnframe", "summonframe", "unsummonframe", "quakeframe"):
            _, ln = a.find_cmd(cmd)
            if ln and ln.args():
                try:
                    fi = int(ln.args()[0])
                    if fi >= n:
                        if cmd == "landframe" and fi > n:
                            err(f"动画 “{label}” 的 {cmd} 指向帧 {fi}，"
                                f"但该动画只有 {n} 帧"
                                "（落地后实体会消失！）", key)
                        else:
                            warn(f"动画 “{label}” 的 {cmd} 指向帧 {fi}，"
                                 f"超出该动画的 {n} 帧范围", key)
                except ValueError:
                    warn(f"动画 “{label}” 的 {cmd} 帧号不是整数", key)
        # jumpframe 重复
        jc = sum(1 for ln in a.lines if ln.cmd() == "jumpframe")
        if jc > 1:
            warn(f"动画 “{label}” 声明了 {jc} 个 jumpframe，只有一个生效",
                 key)

    low = {a.name.lower() for a in doc.anims}
    if t in ("player", "enemy", "npc") or t is None:
        # 仅战斗型实体检查受击/倒地/起身动画（特效/面板等不需要）
        if "idle" not in low:
            err("缺少必备动画 IDLE")
        if t in ("player", "enemy"):
            for req in ("pain", "fall", "rise"):
                if req not in low:
                    sev = err if t == "player" else warn
                    sev(f"缺少必备动画 {req.upper()}")
        if t == "player":
            for req in ("attack1", "attack2"):
                if req not in low:
                    warn(f"玩家默认应有 {req.upper()} 动画")
    return issues


# ---------------- 深度检错 ----------------
_DEF_RE = re.compile(
    r"\b(?:void|int|float|double|char|long|short|unsigned|signed|bool|"
    r"handle|pointer)\s+[\w\*\s]*?\b([A-Za-z_]\w*)\s*\(")
_INC_RE = re.compile(r"^\s*#\s*include\s*[\"<]([^\">]+)[\">]", re.M)
_SCRIPT_EXTS = (".c", ".h", ".txt", "")


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            return fp.read()
    except OSError:
        return None


def resolve_script_path(root_dirs, rel, base_dir=None):
    """解析脚本路径：先相对包含者目录，再相对各根目录 / scripts 子目录。"""
    rel = rel.replace("\\", "/")
    cands = []
    if base_dir:
        cands.append(os.path.join(base_dir, rel.replace("/", os.sep)))
    for root in root_dirs:
        cands.append(os.path.join(root, rel.replace("/", os.sep)))
        cands.append(os.path.join(root, "scripts", rel.replace("/", os.sep)))
    for c in cands:
        if os.path.isfile(c):
            return c
    for c in cands:
        for e in (".c", ".h"):
            if os.path.isfile(c + e):
                return c + e
    return None


def script_defs(path, root_dirs, cache=None, seen=None):
    """返回脚本（含 #include 递归展开）内定义的函数名集合。"""
    if cache is None:
        cache = {}
    if seen is None:
        seen = set()
    ap = os.path.normcase(os.path.abspath(path))
    if ap in cache:
        return cache[ap]
    if ap in seen:                          # 循环 include
        return set()
    seen.add(ap)
    txt = _read_text(path)
    if txt is None:
        cache[ap] = set()
        return set()
    funcs = {m.group(1) for m in _DEF_RE.finditer(txt)}
    base = os.path.dirname(ap)
    for m in _INC_RE.finditer(txt):
        sub = resolve_script_path(root_dirs, m.group(1), base)
        if sub:
            funcs |= script_defs(sub, root_dirs, cache, seen)
    cache[ap] = funcs
    return funcs


def _file_exists(root_dirs, rel, exts):
    """判断相对路径的文件是否存在（无扩展名时按 exts 依次补全）。"""
    rel = (rel or "").strip().replace("\\", "/")
    if not rel or rel.lower() == "none" or rel.startswith("@"):
        return True
    for root in root_dirs:
        p = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(p):
            return True
    base = os.path.splitext(rel)[0]
    for root in root_dirs:
        p = os.path.join(root, base.replace("/", os.sep))
        for e in exts:
            if os.path.isfile(p + e):
                return True
    return False


def deep_validate(doc, root_dirs, models=None):
    """耗时较深的检查，返回 [(severity, message, key)]。

    1) 所有非注释图片引用必须存在      -> error
    2) 所有非注释音频引用缺失          -> warning
    3) @cmd 调用的函数必须是 OpenBOR 内置函数，或实体脚本
       （animationscript / didblockscript / … 指向的文件）
       及其 #include 递归子脚本中定义的函数 -> 否则 error
    """
    issues = []

    def add(sev, m, key=None):
        issues.append((sev, m, key))

    def err(m, key=None):
        add(ERR, m, key)

    def warn(m, key=None):
        add(WARN, m, key)

    # ---- 1) / 2) Header 里的图片 / 音频引用 ----
    for _, cmd, args in doc.header_entries():
        cl = cmd.lower()
        if cl.startswith("@") or not args:
            continue
        v = args[0]
        if cl in IMAGE_HEADER_FIELDS:
            if not _file_exists(root_dirs, v, FRAME_EXTS):
                err(f"Header 字段 {cl} 引用的图片不存在: {v}", ("header", cl))
        elif cl in SOUND_HEADER_FIELDS:
            if not _file_exists(root_dirs, v, AUDIO_EXTS):
                warn(f"Header 字段 {cl} 引用的音频不存在: {v}", ("header", cl))

    # ---- 1) / 2) 动画里的图片 / 音频引用 ----
    for a in doc.anims:
        label = a.name or "(无名)"
        key = ("anim", a.name.lower()) if a.name else None
        for fr in a.frames():
            p = fr["path"]
            if p and p.lower() != "none" and \
                    not _file_exists(root_dirs, p, FRAME_EXTS):
                err(f"动画 “{label}” 引用的图片不存在: {p}", key)
        for i, ln in enumerate(a.lines):
            c = ln.cmd()
            if c == "sound" and ln.args():
                if not _file_exists(root_dirs, ln.args()[0], AUDIO_EXTS):
                    warn(f"动画 “{label}” 第 {i} 行引用的音频不存在: "
                         f"{ln.args()[0]}", key)
            elif c in ("hitfx", "blockfx") and ln.args():
                if not _file_exists(root_dirs, ln.args()[0], AUDIO_EXTS):
                    warn(f"动画 “{label}” 的 {c} 音频不存在: "
                         f"{ln.args()[0]}", key)

    # ---- 3) 脚本函数解析 ----
    cache = {}
    script_files = []          # [(指令名, 原始路径, 解析后的绝对路径 or None)]
    for ev in sorted(SCRIPT_EVENTS):
        v = doc.header_get(ev)
        if not v or not v[0]:
            continue
        rel = v[0]
        ap = resolve_script_path(root_dirs, rel)
        if ap is None:
            err(f"脚本指令 {ev} 指向的文件不存在: {rel}", ("header", ev))
        else:
            script_files.append((ev, rel, ap))

    # 实体文本里 @script 块内定义的函数也算已定义
    local_funcs = set()
    for s, e in doc.scripts:
        body = "\n".join(doc.pre_lines[k].raw for k in range(s, e + 1))
        local_funcs |= {m.group(1) for m in _DEF_RE.finditer(body)}
    for a in doc.anims:
        st = None
        for i, ln in enumerate(a.lines):
            c = ln.cmd()
            if st is None and is_script_start(c, ln.args()):
                st = i
            elif st is not None and is_script_end(c):
                body = "\n".join(a.lines[k].raw for k in range(st, i + 1))
                local_funcs |= {m.group(1) for m in _DEF_RE.finditer(body)}
                st = None

    all_funcs = set(local_funcs)
    for _, _, ap in script_files:
        all_funcs |= script_defs(ap, root_dirs, cache)

    # @cmd 调用
    for a in doc.anims:
        label = a.name or "(无名)"
        key = ("anim", a.name.lower()) if a.name else None
        for ln in a.lines:
            if ln.cmd() != "@cmd" or not ln.args():
                continue
            fn = ln.args()[0]
            fn_base = fn.split("(")[0].strip()
            if not fn_base or fn_base.startswith("@"):
                continue
            if fn_base in BUILTIN_FUNCS or fn_base in all_funcs:
                continue
            raw = ln.raw.strip()
            if "@cmd" in raw and fn_base not in BUILTIN_FUNCS:
                sug = difflib.get_close_matches(
                    fn_base, sorted(BUILTIN_FUNCS | all_funcs),
                    n=1, cutoff=0.7)
                extra = ""
                if not script_files:
                    extra = "（实体没有声明任何 script 指令，无法找到来源）"
                elif sug:
                    extra = f"（是否想写 “{sug[0]}”？）"
                err(f"动画 “{label}” 的 @cmd 调用了未定义函数 "
                    f"“{fn_base}”{extra}", key)
    return issues



# ====================================================================
# 原 bor_level.py
# ====================================================================

# 关卡里“对象块”的开始命令（它们后面都要跟 at）
OBJ_STARTS = {
    "spawn", "group", "wait", "blockade", "light", "load",
    "scrollx", "scrollz", "setpalette",
    "shadowcolor", "shadowalpha", "shadowopacity",
}
# 只在“已经进入对象区之后”才算对象（否则是关卡属性）
OBJ_LATE = {"music", "musicfade"}
# 关卡属性（非对象）—— 只是给界面分组显示用，未知命令也一样会被原样保留
HEADER_GRAPHICS = ("music", "bossmusic", "background", "bglayer", "fglayer",
                   "frontpanel", "panel", "order", "palette", "water",
                   "rock", "bgspeed", "loadingbg", "mirror", "void")
HEADER_SETTINGS = ("type", "direction", "settime", "notime", "noreset",
                   "noslow", "nofadeout", "cameratype", "cameraoffset",
                   "scrollspeed", "allowselect", "spawn1", "spawn2",
                   "spawn3", "spawn4", "levelscript", "scene", "endhole",
                   "typemp", "aggressive", "speed", "blocked", "chargerate",
                   "setweap")
HEADER_TERRAIN = ("wall", "hole", "basemap")


# ---- 视频模式（引擎 openbor.c 的 video N 表，逐字核对过 v6391）----
# N → (屏宽, 屏高, PLAYER_MIN_Z, PLAYER_MAX_Z, BGHEIGHT)
# 16bit / 32bit 下关卡就按这个分辨率渲染（hScale/vShift 那套只管 HUD）
VIDEO_MODES = {
    0: (320, 240, 160, 232, 160),
    1: (480, 272, 182, 264, 182),
    2: (640, 480, 321, 465, 321),
    3: (720, 480, 321, 465, 321),
    4: (800, 480, 321, 465, 321),
    5: (800, 600, 401, 582, 401),
    6: (960, 540, 362, 524, 362),
}
_video_cache = {}


def read_video_txt(root_dirs):
    """读 data/video.txt 的 `video N` → (w, h, zmin, zmax, BGheight)。

    找不到文件 / 没写 video 行 / N 超范围 → 模式 0（320×240），
    和引擎的处理一致。结果按 root_dirs 缓存。
    """
    key = tuple(root_dirs or [])
    v = _video_cache.get(key)
    if v is not None:
        return v
    mode = None
    for rd in key:
        if not rd:
            continue
        for rel in ("video.txt", "data/video.txt"):
            p = os.path.join(rd, rel)
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8",
                          errors="replace") as fp:
                    for ln in fp:
                        t = ln.split()
                        if len(t) >= 2 and t[0].lower() == "video":
                            mode = _i(t[1], 0)
                            break
            except Exception:
                pass
            if mode is not None:
                break
        if mode is not None:
            break
    v = VIDEO_MODES.get(mode if mode is not None else 0, VIDEO_MODES[0])
    _video_cache[key] = v
    return v


def resolve_path(root_dirs, rel):
    """按 data 根目录解析相对路径（找不到就原样返回 None）。"""
    if not rel:
        return None
    rel = rel.replace("\\", "/")
    for root in root_dirs:
        p = os.path.join(root, rel.replace("/", os.sep))
        if os.path.exists(p):
            return p
    return None


def canon(cmd, args, sep="\t"):
    """关卡文件的规范行：**命令 + 1 个制表符 + 参数**（值之间 1 空格）。

    注意关卡 txt 的写法和实体 txt 不一样（实体 Header 是短字段名 2 个制表符），
    所以这里不能用 openbor.canon_line，否则改一处就把整篇排版统一掉了。
    """
    a = [str(x) for x in (args or [])]
    return cmd + (sep + " ".join(a) if a else "")


def _sep_of(code, cmd):
    """原行里「命令」与「第一个参数」之间的分隔符（tab / 空格串）。

    levels.txt 里 `z 196 277 196` 用空格、`file\tdata/...` 用制表符，
    是混着的；改一行时沿用它原来的写法才不会破坏排版。
    """
    head = code[:len(code) - len(code.lstrip())]
    rest = code[len(head):]
    if not rest.lower().startswith(cmd.lower()):
        return None
    tail = rest[len(cmd):]
    s = tail[:len(tail) - len(tail.lstrip())]
    return s or None


def write_line(ln, cmd, args, keep_sep=True):
    """重写一行：尽量沿用原行的排版 —— 缩进、命令后的分隔符、
    **参数与参数之间的分隔符**都原样保留，行尾注释也不动。

    参数个数对不上（新增 / 删减了参数）时才退回规范写法：
    `命令 + 1 个制表符 + 参数（空格相连）`。
    """
    code, comment = split_comment(ln.raw)
    a = [str(x) for x in (args or [])]
    head = code[:len(code) - len(code.lstrip())]
    rest = code[len(head):]
    # 注释与其前空白之间的空隙也尽量原样保留
    gap = code[len(code.rstrip()):] or " "
    comment = (gap + comment) if comment else ""
    if not rest.lower().startswith(cmd.lower()):
        ln.raw = head + canon(cmd, a) + comment
        return
    tail = rest[len(cmd):]
    sep0 = tail[:len(tail) - len(tail.lstrip())]      # 命令 → 第一个参数
    body_tail = tail.strip()                          # 注释前的空白一并去掉
    if not keep_sep:
        sep0 = "\t" if a else ""
    if a and body_tail:
        parts = re.split(r"(\s+)", body_tail)
        vals, seps = parts[0::2], parts[1::2]
        if len(vals) == len(a):                       # 逐位替换，分隔符全保留
            out = [head + cmd + sep0 + a[0]]
            for i, s in enumerate(seps):
                out.append(s)
                out.append(a[i + 1])
            ln.raw = "".join(out) + comment
            return
    if not a:
        body = cmd
    elif keep_sep and sep0:
        body = cmd + sep0 + " ".join(a)
    else:
        body = canon(cmd, a)
    ln.raw = head + body + comment


# ============================================================ levels.txt
# set 行之后、第一条结构性命令（branch/z/file/scene/next/end）之前的命令行
# 都算这个 set 的「基本设置」（lives / nosame / credits / completebg …）
SET_STRUCT_STOPS = frozenset(
    ("set", "branch", "z", "file", "scene", "next", "end"))

# 右键「添加设置字段」的常用候选（可输入列表外的自定义字段）
SET_FIELD_PRESETS = [
    "lives", "nosame", "credits", "completebg", "cansave", "typemp",
    "equalairpause", "innerspace", "unlockbg", "showplayers", "noreset",
    "crossfade", "norotate", "load", "skipselect",
]


class SetBlock:
    """levels.txt 里一个 set 段：set 名 + 基本设置（lives/credits…）。"""

    __slots__ = ("name", "line", "settings", "entries")

    def __init__(self, name, line):
        self.name = name
        self.line = line            # set 行下标
        self.settings = {}          # cmd（小写）-> [行下标, args]
        self.entries = []           # 属于这个 set 的 LevelRef（引用）


class LevelRef:
    """levels.txt 里的一条关卡记录。"""

    __slots__ = ("path", "branch", "zmin", "zmax", "bgheight", "set_name",
                 "line", "z_line", "branch_line")

    def __init__(self, path, line=0):
        self.path = path
        self.line = line          # file 行下标
        self.z_line = None        # 决定这条关卡 z 范围的 z 行下标（可改）
        self.branch_line = None   # 它上面的 branch 行下标（删除时连带）
        self.branch = ""
        self.set_name = ""
        self.zmin = 160
        self.zmax = 232
        self.bgheight = 160

    @property
    def name(self):
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def label(self):
        return self.branch or self.name


class LevelsTxt:
    """levels.txt：关卡的播放顺序表（同时能改每条关卡的 z 范围）。"""

    def __init__(self, path=None):
        self.path = path
        self.lines = []
        self.entries = []
        self.sets = []
        self.set_blocks = []
        self._trailing = "\n"
        if path:
            self.load(path)

    def load(self, path):
        self.path = path
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            self.load_text(fp.read(), path)

    def load_text(self, text, path=None):
        if path:
            self.path = path
        self._trailing = "\n" if text.endswith("\n") else ""
        self.lines = [Line(x) for x in text.splitlines()]
        self._parse()

    def _parse(self):
        self.entries = []
        self.sets = []
        self.set_blocks = []
        block = None
        taking = False        # 还在收 set 的基本设置吗（遇结构性命令就停）
        branch = ""
        branch_line = None
        z_line = None
        zmin = zmax = bg = None
        for i, ln in enumerate(self.lines):
            c, t = ln.cmd(), ln.args()
            if not c:
                continue
            if c == "set":
                block = SetBlock(" ".join(t), i)
                self.set_blocks.append(block)
                if block.name not in self.sets:
                    self.sets.append(block.name)
                taking = True
                branch = ""
                branch_line = None
            elif c == "branch":
                branch = " ".join(t)
                branch_line = i
                taking = False
            elif c == "z" and len(t) >= 2:
                z_line = i
                zmin, zmax = _i(t[0], 160), _i(t[1], 232)
                bg = _i(t[2], 160) if len(t) > 2 else 160
                taking = False
            elif c == "file" and t:
                e = LevelRef(t[0], i)
                e.set_name = block.name if block else ""
                e.branch = branch
                e.branch_line = branch_line
                e.z_line = z_line
                if zmin is not None:
                    e.zmin, e.zmax, e.bgheight = zmin, zmax, bg
                self.entries.append(e)
                if block is not None:
                    block.entries.append(e)
                branch = ""
                branch_line = None
                z_line = None          # z 只对紧接着的这条生效
                taking = False
            elif taking and block is not None:
                # set 基本设置行（lives 10 / nosame 1 …）
                block.settings.setdefault(c, [i, list(t)])

    def block_by_name(self, name):
        """按 set 名找 SetBlock（改完重解析后旧对象会失效，用它拿新的）。"""
        for b in self.set_blocks:
            if b.name == name:
                return b
        return None

    def to_text(self):
        return "\n".join(ln.raw for ln in self.lines) + self._trailing

    def save(self, path=None):
        with open(path or self.path, "w", encoding="utf-8") as fp:
            fp.write(self.to_text())

    def set_entry_z(self, e, zmin, zmax, bg=None):
        """改写这条关卡的 z 范围：`z {zmin} {zmax} {BGheight}`。

        本来就有一行 z 就直接改；没有就在它的 file 行前面插一行。
        """
        args = [str(int(zmin)), str(int(zmax)),
                str(int(bg if bg is not None else e.bgheight))]
        if e.z_line is not None and 0 <= e.z_line < len(self.lines) \
                and self.lines[e.z_line].cmd() == "z":
            write_line(self.lines[e.z_line], "z", args)
        else:
            idx = min(e.line, len(self.lines))
            self.lines.insert(idx, Line(canon("z", args, self._z_sep())))
            e.line += 1
        self._parse()

    def _z_sep(self):
        """新插的 z 行用哪种分隔：看文件里已有的 z 行（没有就用制表符）。"""
        for ln in self.lines:
            if ln.cmd() == "z":
                return _sep_of(ln.raw, "z") or "\t"
        return "\t"

    def _file_sep(self):
        for ln in self.lines:
            if ln.cmd() == "file":
                return _sep_of(ln.raw, "file") or "\t"
        return "\t"

    # ---------------- set 段编辑 ----------------
    def set_setting(self, block, cmd, args):
        """改 / 加一条 set 基本设置（没有就插在 set 行后面）。"""
        st = block.settings.get(cmd.lower())
        if st is not None:
            write_line(self.lines[st[0]], cmd, args)
        else:
            self.lines.insert(block.line + 1,
                              Line(canon(cmd, args, "\t")))
        self._parse()

    def del_setting(self, block, cmd):
        """删掉一条 set 基本设置；没有则返回 False。"""
        st = block.settings.get(cmd.lower())
        if st is None or not (0 <= st[0] < len(self.lines)):
            return False
        del self.lines[st[0]]
        self._parse()
        return True

    def add_set(self, name):
        """文件末尾新建一个 set 段（带空行分隔），返回新的 SetBlock。"""
        self.lines.append(Line(""))
        self.lines.append(Line(canon("set", [name], "\t")))
        self._parse()
        return self.block_by_name(name)

    def _set_insert_idx(self, block):
        """往这个 set 末尾插子段的位置：最后一条 file 后面（没有 file
        就在设置区后面）。"""
        if block.entries:
            return block.entries[-1].line + 1
        idx = block.line + 1
        for st in block.settings.values():
            idx = max(idx, st[0] + 1)
        return idx

    def add_file(self, block, relpath):
        """给 set 加一条 file 行（relpath 写成相对 data 的路径），
        返回新的 LevelRef。"""
        idx = self._set_insert_idx(block)
        self.lines.insert(idx, Line(canon("file", [relpath],
                                          self._file_sep())))
        self._parse()
        for e in self.entries:
            if e.line == idx:
                return e
        return None

    def add_sub(self, block, cmd, args):
        """往 set 末尾插一条额外子段（branch / scene / next / end…），
        返回插入行下标。"""
        idx = self._set_insert_idx(block)
        self.lines.insert(idx, Line(canon(cmd, args, "\t")))
        self._parse()
        return idx

    def del_entry(self, e):
        """删掉一条关卡记录：连带只属于它的 branch / z 行。"""
        doomed = [e.line]
        for k in (e.z_line, e.branch_line):
            if k is not None and 0 <= k < len(self.lines) \
                    and k != e.line:
                doomed.append(k)
        for i in sorted(set(doomed), reverse=True):
            del self.lines[i]
        self._parse()


# ============================================================ 单个关卡
class LevelObject:
    """一个关卡对象块：spawn / group / wait / light / …"""

    def __init__(self, cmd, args, line):
        self.cmd = cmd
        self.args = list(args or [])
        self.line = line            # 起始行下标
        self.props = {}             # cmd -> [行下标, args]
        self.order = []             # 属性出现顺序 [(行下标, cmd, args)]
        self.script = None          # (start, end) 脚本块

    # ---- 常用取值 ----
    @property
    def group_label(self):
        """group 对象的显示名（`group 2 1` → “group 2/1”）；非 group 返回 ''。"""
        if self.cmd != "group":
            return ""
        return "group " + " ".join(self.args[:2])

    @property
    def name(self):
        """spawn 的实体名；其它对象返回 ''。"""
        return self.args[0] if self.cmd == "spawn" and self.args else ""

    @property
    def at(self):
        p = self.props.get("at")
        return _i(p[1][0]) if p and p[1] else None

    @property
    def coords(self):
        """(x, z, a)；没有 coords 行返回 None。x 是相对屏幕（滚动位置）的。"""
        p = self.props.get("coords")
        if not p:
            return None
        a = p[1]
        return (_f(a[0]), _f(a[1]), _f(a[2]) if len(a) > 2 else 0.0)

    def prop(self, cmd, default=None):
        p = self.props.get(cmd)
        return (p[1] if p else default)

    def prop_int(self, cmd, default=0):
        p = self.props.get(cmd)
        return _i(p[1][0]) if p and p[1] else default

    def z_out(self, zmin, zmax):
        """z 是否落在可走范围之外（关卡编辑里最常见的错误）。"""
        z = self.z
        return z is not None and not (zmin - 0.5 <= z <= zmax + 0.5)

    @property
    def abs_x(self):
        """在关卡长度方向上的绝对 x（= at + coords.x）。"""
        c = self.coords
        if c is None:
            return None
        return (self.at or 0) + c[0]

    @property
    def z(self):
        c = self.coords
        return None if c is None else c[1]

    @property
    def title(self):
        if self.cmd == "spawn":
            return self.name or "（无名）"
        return self.cmd + (" " + " ".join(self.args) if self.args else "")

    def add(self, idx, cmd, args):
        self.order.append((idx, cmd, args))
        if cmd and cmd not in self.props:
            self.props[cmd] = [idx, args]

    @property
    def end_line(self):
        """本对象最后一行（含属性行，不含后面的对象）。"""
        last = self.line
        for i, _, _ in self.order:
            last = max(last, i)
        if self.script:
            last = max(last, self.script[1])
        return last


class LevelDoc:
    """一个 level 文件。"""

    def __init__(self, path=None):
        self.path = path
        self.lines = []
        self.props = {}             # cmd -> [(行下标, args)]（可多条：panel/wall/hole…）
        self.objects = []
        self.ref = None             # 关联的 levels.txt 记录（可选）
        self._trailing = "\n"
        self._rev = 0               # 每次重解析 +1（界面缓存靠它失效）
        if path:
            self.load(path)

    # ---------------- 读写 ----------------
    def load(self, path):
        with open(path, "r", encoding="utf-8", errors="replace") as fp:
            raw = fp.read()
        self.load_text(raw, path)

    def load_text(self, text, path=None):
        if path:
            self.path = path
        self._trailing = "\n" if text.endswith("\n") else ""
        self.lines = [Line(x) for x in text.splitlines()]
        self._parse()

    def to_text(self):
        return "\n".join(ln.raw for ln in self.lines) + self._trailing

    def save(self, path=None):
        p = path or self.path
        with open(p, "w", encoding="utf-8") as fp:
            fp.write(self.to_text())
        self.path = p

    # ---------------- 解析 ----------------
    def _parse(self):
        self.props = {}
        self.objects = []
        self._rev += 1
        cur = None
        in_obj = False
        script = None
        for i, ln in enumerate(self.lines):
            c, a = ln.cmd(), ln.args()
            if script is not None:
                if c.startswith("@end_script"):
                    if cur is not None:
                        cur.script = (script, i)
                    script = None
                continue
            if c.startswith("@script") or (
                    c == "script" and a and str(a[0]).lower()
                    .startswith("@script")):
                script = i
                continue
            if c in OBJ_STARTS or (in_obj and c in OBJ_LATE):
                in_obj = True
                cur = LevelObject(c, a, i)
                self.objects.append(cur)
                continue
            if cur is not None:
                cur.add(i, c, a)
            elif c:
                self.props.setdefault(c, []).append((i, a))

    # ---------------- 属性 ----------------
    def prop(self, cmd):
        v = self.props.get(cmd)
        return v[0][1] if v else None

    def prop_all(self, cmd):
        return self.props.get(cmd, [])

    def set_prop(self, cmd, args):
        """改写关卡属性；没有就追加一行到对象区之前。"""
        v = self.props.get(cmd)
        if v:
            write_line(self.lines[v[0][0]], cmd, args)
        else:
            idx = self._header_insert_idx()
            self.lines.insert(idx, Line(canon(cmd, args)))
        self._parse()

    # ---------------- 地形（wall / hole）编辑 ----------------
    def set_line_args(self, idx, cmd, args):
        """按行号重写一行（地形行专用：行号就是身份，重写不挪位置）。"""
        if 0 <= idx < len(self.lines) \
                and self.lines[idx].cmd() == cmd:
            write_line(self.lines[idx], cmd, args)
            self._parse()
            return True
        return False

    def add_terrain(self, cmd, args):
        """在对象区之前追加一条地形命令（跟在已有的 wall / hole 后面）。"""
        idx = self._header_insert_idx()
        for k in ("wall", "hole"):
            for i, _a in self.props.get(k, []):
                idx = max(idx, i + 1)
        self.lines.insert(idx, Line(canon(cmd, args)))
        self._parse()
        return idx

    def del_line(self, idx):
        """删掉一行（地形行删除专用）。"""
        if 0 <= idx < len(self.lines):
            del self.lines[idx]
            self._parse()
            return True
        return False

    def _header_insert_idx(self):
        """新属性插在哪：第一个对象之前（没有对象就插到文件末尾）。"""
        if self.objects:
            return self.objects[0].line
        return len(self.lines)

    # ---------------- 对象编辑 ----------------
    def _refind(self, obj):
        """改完重解析后按起始行号把对象找回来（插入 / 删除属性都不会
        动到对象的起始行，所以行号是可靠的身份）。"""
        if obj is None:
            return None
        for o in self.objects:
            if o.line == obj.line:
                return o
        return obj

    def set_obj_prop(self, obj, cmd, args):
        """改写对象里的一个属性；没有就插一行（插在 at 之前，没有 at 就插到块尾）。

        返回**重解析后**的对象（行号可能位移，后续编辑要用返回值）。
        """
        p = obj.props.get(cmd)
        if p:
            write_line(self.lines[p[0]], cmd, args)
        else:
            idx = obj.end_line + 1
            at = obj.props.get("at")
            if at:
                idx = at[0]
            self.lines.insert(idx, Line(canon(cmd, args)))
        self._parse()
        return self._refind(obj)

    def del_obj_prop(self, obj, cmd):
        """删掉对象里的一个属性行；返回重解析后的对象。"""
        p = obj.props.get(cmd)
        if not p:
            return obj
        del self.lines[p[0]]
        self._parse()
        return self._refind(obj)

    def delete_object(self, obj):
        """删掉整个对象块（含它的属性行与脚本块）。"""
        idxs = {obj.line}
        for i, _, _ in obj.order:
            idxs.add(i)
        if obj.script:
            idxs.update(range(obj.script[0], obj.script[1] + 1))
        for i in sorted(idxs, reverse=True):
            if 0 <= i < len(self.lines):
                del self.lines[i]
        self._parse()

    def add_spawn(self, name, coords=(320.0, 200.0, 0.0), at=0):
        """在文件末尾追加一个 spawn 块，返回新对象。"""
        block = ["spawn\t" + str(name),
                 "coords\t%d %d %d" % (int(coords[0]), int(coords[1]),
                                       int(coords[2])),
                 "at\t%d" % int(at)]
        while self.lines and not self.lines[-1].raw.strip():
            self.lines.pop()
        start = len(self.lines)
        self.lines.extend(Line(x) for x in block)
        self._parse()
        for o in self.objects:
            if o.line >= start:
                return o
        return None

    def add_marker(self, cmd, args, at):
        """插一条 wait / group（带 at），按 at 升序插到该在的位置。

        生成顺序跟着滚动位置走，所以新标记要插在「第一个 at 比它大的
        对象」之前；大家都一样大就排在同位置的后面。返回新对象下标。
        """
        block = [Line(canon(cmd, args)),
                 Line(canon("at", [str(int(at))]))]
        idx = len(self.lines)
        for o in self.objects:
            if o.at is not None and o.at > at:
                idx = o.line
                break
        while idx > 0 and not self.lines[idx - 1].raw.strip():
            idx -= 1                  # 别把空行堆挤到对象中间去
        self.lines[idx:idx] = block + [Line("")]
        self._parse()
        for k, o in enumerate(self.objects):
            if o.cmd == cmd and idx <= o.line < idx + len(block):
                return k
        return None

    def add_spawn_to_group(self, gidx, name, coords, at):
        """在 group 对象 gidx 那一组的**末尾**追加一个 spawn（归这个组管）。"""
        if not (0 <= gidx < len(self.objects)) \
                or self.objects[gidx].cmd != "group":
            return None
        g = self.objects[gidx]
        members = self.group_members(gidx)
        if members:
            # 紧跟本组最后一个成员之后（中间隔着 wait 也不跨过去）
            idx = self.objects[members[-1]].end_line + 1
        else:
            # 空组：插到下一个对象块之前
            idx = len(self.lines)
            for o in self.objects:
                if o.line > g.line:
                    idx = o.line
                    break
            while idx > g.end_line + 1 and not self.lines[idx - 1].raw.strip():
                idx -= 1
        block = [Line("spawn\t" + str(name)),
                 Line(canon("coords", [str(int(coords[0])),
                                       str(int(coords[1])),
                                       str(int(coords[2]))])),
                 Line(canon("at", [str(int(at))])),
                 Line("")]
        self.lines[idx:idx] = block
        self._parse()
        for k, o in enumerate(self.objects):
            if o.cmd == "spawn" and idx <= o.line < idx + len(block):
                return k
        return None

    def duplicate_object(self, i, at_shift=0):
        """把对象 i 的整个块原样复制一份，插在它后面（归同一个组管）。

        at_shift 非 0 时副本的 at 顺移，免得两个生成点完全重叠。
        返回副本的下标。
        """
        if not (0 <= i < len(self.objects)):
            return None
        o = self.objects[i]
        block = [Line(self.lines[k].raw)
                 for k in range(o.line, o.end_line + 1)]
        if at_shift and o.at is not None:
            for ln in block:
                if ln.cmd() == "at" and ln.args():
                    write_line(ln, "at", [str(int(o.at + at_shift))])
                    break
        idx = o.end_line + 1
        self.lines[idx:idx] = [Line("")] + block
        self._parse()
        for k, o2 in enumerate(self.objects):
            if o2.line == idx + 1:
                return k
        return None

    def clear_group(self, gidx):
        """删掉 group gidx 名下的全部 spawn（group 行自己保留）。返回删了几个。"""
        if not (0 <= gidx < len(self.objects)) \
                or self.objects[gidx].cmd != "group":
            return 0
        own = dict(self.group_owner())
        victims = [self.objects[i] for i, g in own.items()
                   if g == gidx and self.objects[i].cmd == "spawn"]
        # 一次性删：逐个 delete_object 会因重解析让后面的对象行号错位
        idxs = set()
        for o in victims:
            idxs.add(o.line)
            for i, _, _ in o.order:
                idxs.add(i)
            if o.script:
                idxs.update(range(o.script[0], o.script[1] + 1))
        for i in sorted(idxs, reverse=True):
            if 0 <= i < len(self.lines):
                del self.lines[i]
        self._parse()
        return len(victims)

    def group_members(self, gidx):
        """group gidx 名下的 spawn 下标列表。"""
        own = dict(self.group_owner())
        return [i for i, g in own.items()
                if g == gidx and self.objects[i].cmd == "spawn"]

    def group_no(self, gidx):
        """group 的序号（按它在文件里出现的顺序，从 1 起）。"""
        n = 0
        for k, o in enumerate(self.objects):
            if k >= gidx:
                break
            if o.cmd == "group":
                n += 1
        return n

    # ---------------- 场景信息 ----------------
    def panel_paths(self):
        return [(i, a[0]) for i, a in self.props.get("panel", []) if a]

    def panel_order(self):
        out = ""
        for _, a in self.props.get("order", []):
            out += "".join(a)
        return out

    def bg_path(self):
        for key in ("background", "bglayer"):
            v = self.props.get(key)
            if v and v[0][1]:
                return v[0][1][0]
        return None

    def bg_info(self):
        """背景图层 (path, xratio)：视差比例默认 0.5（手册：0.5 = 和背景同速）。

        background / bglayer 的参数表：path xratio zratio xpos zpos …
        """
        for key in ("background", "bglayer"):
            v = self.props.get(key)
            if v and v[0][1]:
                a = v[0][1]
                return (a[0], _f(a[1], 0.5) if len(a) > 1 else 0.5)
        return None

    def front_path(self):
        v = self.props.get("frontpanel")
        return v[0][1][0] if v and v[0][1] else None

    def frontpanel_paths(self):
        """所有 frontpanel 图路径（按声明顺序，引擎要按序排开）。"""
        return [a[0] for _i, a in self.props.get("frontpanel", [])
                if a and a[0]]

    def layer_infos(self, player_max_z=264.0):
        """所有非 panel / frontpanel 图层，按引擎 draw_scrolled_bg 的规则。

        引擎逐字核对（v6391 openbor.c）：
        - background / bglayer / layer：参数从 path 后第 1 个开始
          （path xratio zratio xpos zpos xspacing zspacing xrepeat
          zrepeat transparency alpha …）；
          fglayer 在 path 后**先多一个 z**，其余顺延
        - 缺省：ratio = 0.5/0.5（fglayer 1.5/1.5）；offset / spacing = 0；
          repeat = -1（无限）；transparency = 0
        - background / bglayer 画在 panel 之下（z=MIN_INT）；
          layer 的 z = 第 1 个参数（缺 0）；fglayer 的 z = 参数 + PLAYER_MAX_Z+50

        每项 dict：kind path xr zr xoff zoff xs zs xrep zrep transbg sortz。
        sortz 用来决定画在 panel 之前（< zmin-50）还是之后。
        """
        def af(a, i, d):
            return _f(a[i], d) if len(a) > i and str(a[i]).strip() else d

        def ai(a, i, d):
            return _i(a[i], d) if len(a) > i and str(a[i]).strip() else d

        out = []
        for key, shift, rdef in (("background", 0, 0.5),
                                  ("bglayer", 0, 0.5),
                                  ("layer", 1, 0.5),
                                  ("fglayer", 1, 1.5)):
            for _ln, a in self.props.get(key, []):
                if not a or not a[0]:
                    continue
                if key == "background":
                    sortz = -2.0e18          # MIN_INT：永远在最底
                elif key == "bglayer":
                    sortz = -1.0e18
                elif key == "layer":
                    sortz = af(a, 1, 0.0)
                else:                        # fglayer：z + FRONTPANEL_Z
                    sortz = af(a, 1, 0.0) + float(player_max_z) + 50.0
                out.append(dict(kind=key, path=a[0],
                                xr=af(a, 1 + shift, rdef),
                                zr=af(a, 2 + shift, rdef),
                                xoff=af(a, 3 + shift, 0.0),
                                zoff=af(a, 4 + shift, 0.0),
                                xs=af(a, 5 + shift, 0.0),
                                zs=af(a, 6 + shift, 0.0),
                                xrep=ai(a, 7 + shift, -1),
                                zrep=ai(a, 8 + shift, -1),
                                transbg=ai(a, 9 + shift, 0),
                                sortz=sortz))
        return out

    def walls(self):
        return [(i, a) for i, a in self.props.get("wall", [])]

    def holes(self):
        return [(i, a) for i, a in self.props.get("hole", [])]

    def z_range(self):
        """(zmin, zmax, BGheight)：levels.txt 里没写就用手册默认值。"""
        r = self.ref
        if r is not None:
            return (r.zmin, r.zmax, r.bgheight)
        return (160, 232, 160)

    def group_owner(self):
        """[(对象下标, 所属 group 对象下标或 None)]。

        OpenBOR 里 `group …` 后面的 spawn 都归它管，直到下一个 group 为止；
        group 之前的算“无组”。wait 不分割组。
        """
        out = []
        cur = None
        for i, o in enumerate(self.objects):
            if o.cmd == "group":
                cur = i
            out.append((i, cur if o.cmd == "spawn" else None))
        return out

    def out_of_band(self):
        """z 越界的生成对象下标列表。"""
        zmin, zmax, _ = self.z_range()
        return [i for i, o in enumerate(self.objects)
                if o.cmd == "spawn" and o.z_out(zmin, zmax)]

    # ---------------- 卷轴方向 ----------------
    def scroll_dir(self):
        """(方向, 是否纵向)：手册 `direction {dir}`，没写默认 right。

        right / left / both（横向），up / down（纵向），
        in / out / inout / outin（纵深处的推拉）。
        真实数据里还有 `rightleft` `leftright` 两种别名，都当 both。
        """
        v = self.prop("direction")
        d = ""
        if v:
            d = str(v[0]).strip().lower()
            for sep in ("#", ";", " "):
                if sep in d:
                    d = d.split(sep)[0]
        d = {"rightleft": "both", "leftright": "both"}.get(d, d)
        if d not in ("right", "left", "both", "up", "down",
                     "in", "out", "inout", "outin"):
            d = "right"
        return (d, d in ("up", "down", "in", "out", "inout", "outin"))

    def scroll_arrow(self):
        """卷轴方向的箭头（画在画布上），比如 "→" "←" "↔" "↑" "↓"。"""
        d, _ = self.scroll_dir()
        return {"right": "→", "left": "←", "both": "↔",
                "up": "↑", "down": "↓", "in": "⇲",
                "out": "⇱", "inout": "⇅", "outin": "⇅"}.get(d, "→")

    # ---------------- 生成顺序 ----------------
    def _obj_at_line(self, line):
        """按起始行号找对象（返回 (下标, 对象)）。"""
        for i, o in enumerate(self.objects):
            if o.line == line:
                return i, o
        return None, None

    def swap_objects(self, a, b):
        """交换两个对象块在文件里的位置（生成顺序前移 / 后移）。

        两个块的行数可以不同：[A 块][中间的行][B 块] → [B 块][中间的行][A 块]，
        中间的行（空行、wait 之类）留在两者之间不动。
        返回 (a 的新下标, b 的新下标)；不能交换则返回 None。
        """
        if a is None or b is None or a is b:
            return None
        ia, ja = a.line, a.end_line
        ib, jb = b.line, b.end_line
        if ia > ib:                       # 保证 a 在前面
            a, b = b, a
            ia, ja, ib, jb = ib, jb, ia, ja
        if ja >= ib or jb >= len(self.lines) or ia < 0:
            return None
        A = self.lines[ia:ja + 1]
        mid = self.lines[ja + 1:ib]
        B = self.lines[ib:jb + 1]
        self.lines[ia:jb + 1] = B + mid + A
        self._parse()
        na = ia + len(B) + len(mid)
        nb = ia
        ia2, _ = self._obj_at_line(na)
        ib2, _ = self._obj_at_line(nb)
        return (ia2, ib2)

    def move_object_order(self, i, delta):
        """把生成点 i 在**同一个 group 内**往前 / 后挪一位。

        delta = -1 往前（更早生成）/ +1 往后。返回移动后的下标（没动则原值）。
        跨组不给动：那会把实体挪进别的组，游戏里的生成时机就变了。
        """
        if not (0 <= i < len(self.objects)):
            return i
        o = self.objects[i]
        if o.cmd != "spawn":
            return i
        sps = [k for k, x in enumerate(self.objects) if x.cmd == "spawn"]
        if i not in sps:
            return i
        pos = sps.index(i)
        tgt = pos + delta
        if not (0 <= tgt < len(sps)):
            return i
        own = dict(self.group_owner())
        if own.get(sps[pos]) != own.get(sps[tgt]):
            return i                      # 不同组，不动
        r = self.swap_objects(self.objects[sps[pos]],
                              self.objects[sps[tgt]])
        if r is None:
            return i
        return r[0]

    def player_spawns(self):
        """[(玩家号, x, z, a)]：spawn1 / spawn2 …"""
        out = []
        for k in (1, 2, 3, 4):
            v = self.props.get("spawn%d" % k)
            if not v or not v[0][1]:
                continue
            a = v[0][1]
            out.append((k, _f(a[0]), _f(a[1]),
                        _f(a[2]) if len(a) > 2 else 300.0))
        return out

    def length(self, root_dirs):
        """关卡总长度（像素）：按 panel 图宽 × order 的字母顺序累加。"""
        paths = self.panel_paths()
        order = self.panel_order()
        if not paths or not order:
            return self._fallback_length()
        total = 0
        for ch in order.lower():
            k = ord(ch) - 97
            if 0 <= k < len(paths):
                p = resolve_path(root_dirs, paths[k][1])
                if p:
                    try:
                        total += iu.image_size(p)[0]
                        continue
                    except Exception:
                        pass
            total += 320
        return max(320, total)

    def _fallback_length(self):
        m = 320
        for o in self.objects:
            if o.abs_x is not None:
                m = max(m, o.abs_x)
            if o.at is not None:
                m = max(m, o.at)
        return m + 320



# ====================================================================
# 原 bor_dm.py
# ====================================================================

# alpha 模式 → (名字, 不透明度, Qt 混合模式)
_HL = getattr(QPainter, "CompositionMode_HardLight",
              QPainter.CompositionMode_Overlay)
ALPHA_MODES = {
    1: ("半透明", 0.5, QPainter.CompositionMode_SourceOver),
    2: ("负片", 1.0, QPainter.CompositionMode_Multiply),
    3: ("叠加", 1.0, QPainter.CompositionMode_Overlay),
    4: ("强光", 1.0, _HL),
    5: ("减淡", 1.0, QPainter.CompositionMode_ColorDodge),
    6: ("五成", 0.5, QPainter.CompositionMode_SourceOver),
}


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def _scale_val(v, legacy):
    """缩放值 → 倍数（负数保留，代表翻转）。"""
    x = _num(v, 0.0)
    if legacy:
        return x / 256.0 if x else 1.0
    if abs(x) > 8:                # 有人按老习惯写 512 表示 2 倍
        return x / 256.0
    return x if x else 1.0


def _shear_val(v, legacy):
    x = _num(v, 0.0)
    if legacy:
        return x / 256.0
    return x / 256.0 if abs(x) > 8 else x


def _chan_val(v):
    x = _num(v, 1.0)
    return x / 255.0 if x > 1.0 else x


def _color(v, palette):
    """fillcolor / tintcolor → (QColor, 缓存用的键)。

    整数 = 色表索引（8 位模式）；`R_G_B` = 三个分量。
    """
    s = str(v).strip()
    if "_" in s:
        p = [int(clamp(_num(x, 0), 0, 255)) for x in s.split("_")]
        while len(p) < 3:
            p.append(0)
        return QColor(p[0], p[1], p[2]), (p[0], p[1], p[2])
    i = _int(v, -1)
    if i <= 0:
        return None, None        # 手册：0 = 不填充 / 不染色
    if palette and i < len(palette):
        r, g, b = palette[i][:3]
        return QColor(int(clamp(r, 0, 255)), int(clamp(g, 0, 255)), int(clamp(b, 0, 255))), i
    # 没有色表可查（非 8 位图）：按手册最常见的用法当白色闪光处理
    return QColor(255, 255, 255), i


def parse(dm_map, palette=None):
    """{子命令: 参数} → 渲染规格 dict。"""
    spec = {"on": False, "sx": 1.0, "sy": 1.0, "flipx": False, "flipy": False,
            "shift": 0.0, "alpha": 0, "rot": 0.0, "fliprot": False,
            "fill": None, "fill_key": None, "tint": None, "tint_key": None,
            "tint_amt": 0.5, "chan": None, "remap": 0, "clip": None,
            "legacy": False}
    m = dict(dm_map or {})
    if not m:
        return spec
    if "" in m:                                   # 老写法：一整行 10 个整数
        spec["legacy"] = True
        a = [str(x) for x in m[""]] + ["0"] * 10
        spec["sx"] = _scale_val(a[0], True)
        spec["sy"] = _scale_val(a[1], True)
        spec["flipx"] = _int(a[2]) == 1
        spec["flipy"] = _int(a[3]) == 1
        spec["shift"] = _shear_val(a[4], True)
        spec["alpha"] = _int(a[5])
        spec["remap"] = _int(a[6])
        spec["fill"], spec["fill_key"] = _color(a[7], palette)
        spec["rot"] = _num(a[8]) % 360.0
        spec["fliprot"] = _int(a[9]) == 1
    else:
        if "scale" in m:
            v = m["scale"]
            spec["sx"] = _scale_val(v[0], False)
            spec["sy"] = _scale_val(v[1], False) if len(v) > 1 else spec["sx"]
        if "scalex" in m:
            spec["sx"] = _scale_val(m["scalex"][0], False)
        if "scaley" in m:
            spec["sy"] = _scale_val(m["scaley"][0], False)
        for k in ("flipx", "flipy", "fliprotate"):
            if k in m:
                spec["fliprot" if k == "fliprotate" else k] = \
                    _int((m[k] or ["0"])[0]) == 1
        if "shiftx" in m:
            spec["shift"] = _shear_val(m["shiftx"][0], False)
        if "alpha" in m:
            spec["alpha"] = _int(m["alpha"][0])
        if "remap" in m:
            spec["remap"] = _int(m["remap"][0])
        if "rotate" in m:
            spec["rot"] = _num(m["rotate"][0]) % 360.0
        if "fillcolor" in m:
            spec["fill"], spec["fill_key"] = _color(m["fillcolor"][0], palette)
        if "tintcolor" in m:
            spec["tint"], spec["tint_key"] = _color(m["tintcolor"][0], palette)
        tm = _int(m["tintmode"][0], 6) if "tintmode" in m else 6
        spec["tint_amt"] = ALPHA_MODES.get(tm, (None, 0.5, None))[1] or 0.5
        if "channel" in m:
            v = m["channel"]
            ch = [_chan_val(x) for x in v[:3]]
            if len(ch) == 1:
                ch = [ch[0]] * 3
            while len(ch) < 3:
                ch.append(1.0)
            spec["chan"] = tuple(ch)
        for i, k in enumerate(("channelr", "channelg", "channelb")):
            if k in m:
                c = list(spec["chan"] or (1.0, 1.0, 1.0))
                c[i] = _chan_val(m[k][0])
                spec["chan"] = tuple(c)
        if "clip" in m:
            v = [_int(x) for x in m["clip"][:4]]
            if len(v) == 4:
                spec["clip"] = tuple(v)
        ks = ("clipx", "clipy", "clipw", "cliph")
        if any(k in m for k in ks):
            c = list(spec["clip"] or (0, 0, 0, 0))
            for i, k in enumerate(ks):
                if k in m:
                    c[i] = _int(m[k][0])
            spec["clip"] = tuple(c)
    # 负数缩放 = 翻转（老写法里就是这么用的）
    if spec["sx"] < 0:
        spec["sx"], spec["flipx"] = -spec["sx"], not spec["flipx"]
    if spec["sy"] < 0:
        spec["sy"], spec["flipy"] = -spec["sy"], not spec["flipy"]
    spec["on"] = bool(
        abs(spec["sx"] - 1.0) > 1e-6 or abs(spec["sy"] - 1.0) > 1e-6
        or spec["flipx"] or spec["flipy"] or abs(spec["shift"]) > 1e-6
        or spec["alpha"] in ALPHA_MODES or abs(spec["rot"]) > 1e-6
        or spec["fill"] is not None or spec["tint"] is not None
        or spec["chan"] is not None or spec["clip"] is not None)
    return spec


def describe(spec):
    """画布角标用的一句话说明。"""
    if not spec or not spec["on"]:
        return ""
    out = []
    if abs(spec["sx"] - 1.0) > 1e-6 or abs(spec["sy"] - 1.0) > 1e-6:
        out.append("scale %.2f×%.2f" % (spec["sx"], spec["sy"]))
    if spec["flipx"]:
        out.append("flipx")
    if spec["flipy"]:
        out.append("flipy")
    if abs(spec["shift"]) > 1e-6:
        out.append("lean %.2f" % spec["shift"])
    if abs(spec["rot"]) > 1e-6:
        out.append("rotate %.0f°" % spec["rot"])
    a = spec["alpha"]
    if a in ALPHA_MODES:
        out.append("alpha %d(%s)" % (a, ALPHA_MODES[a][0]))
    elif a:
        out.append("alpha %d" % a)
    if spec["fill"] is not None:
        out.append("fill %s" % (spec["fill_key"] if isinstance(
            spec["fill_key"], int) else "rgb"))
    if spec["tint"] is not None:
        out.append("tint")
    if spec["chan"]:
        out.append("channel")
    if spec["clip"]:
        out.append("clip")
    if spec["remap"]:
        out.append("remap %d" % spec["remap"])
    return " · ".join(out)


def geometry(spec, pivot):
    """缩放 / 翻转 / 倾斜 / 旋转 → 绕 pivot 的 QTransform（没有则 None）。"""
    if not spec or not spec["on"]:
        return None
    sx = spec["sx"] * (-1.0 if spec["flipx"] else 1.0)
    sy = spec["sy"] * (-1.0 if spec["flipy"] else 1.0)
    sh = spec["shift"]
    rot = spec["rot"]
    if abs(sx - 1.0) < 1e-6 and abs(sy - 1.0) < 1e-6 and abs(sh) < 1e-6 \
            and abs(rot) < 1e-6:
        return None
    t = QTransform()
    t.translate(pivot.x(), pivot.y())
    if abs(sx - 1.0) > 1e-6 or abs(sy - 1.0) > 1e-6:
        t.scale(sx, sy)
    if abs(sh) > 1e-6:
        t.shear(-sh, 0)       # 以 offset 点为支点“往右倾”
    if abs(rot) > 1e-6:
        t.rotate(rot)
    t.translate(-pivot.x(), -pivot.y())
    return t


def color_key(spec):
    """颜色特效的缓存键（几何变换不用缓存）。"""
    if not spec or not spec["on"]:
        return None
    if spec["fill"] is None and spec["tint"] is None and not spec["chan"]:
        return None
    return ("fill", spec["fill_key"], spec["tint_key"],
            round(spec["tint_amt"], 3), spec["chan"])


def color_image(pil_rgba, spec):
    """把填充色 / 染色 / 通道特效烘进一张 RGBA 图（调用方负责缓存）。"""
    from PIL import Image
    im = pil_rgba
    if im is None:
        return None
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    ch = spec.get("chan")
    if ch:
        r, g, b, a = im.split()
        r = r.point(lambda v, k=ch[0]: int(max(0, min(255, v * k))))
        g = g.point(lambda v, k=ch[1]: int(max(0, min(255, v * k))))
        b = b.point(lambda v, k=ch[2]: int(max(0, min(255, v * k))))
        im = Image.merge("RGBA", (r, g, b, a))
    tc = spec.get("tint")
    if tc is not None:
        rgb = im.convert("RGB")
        solid = Image.new("RGB", im.size, (tc.red(), tc.green(), tc.blue()))
        im = Image.merge("RGBA",
                         (*Image.blend(rgb, solid, spec["tint_amt"]).split(),
                          im.getchannel("A")))
    fc = spec.get("fill")
    if fc is not None:
        solid = Image.new("RGB", im.size, (fc.red(), fc.green(), fc.blue()))
        im = Image.merge("RGBA", (*solid.split(), im.getchannel("A")))
    return im


def paint(p, img, tl, scale, spec):
    """按规格画一张精灵（几何变换 + alpha 混合 + 裁剪）。

    tl 是**未经 drawmethod 变换**的左上角（画布坐标）；clip 用图片自身坐标。
    """
    if img is None:
        return
    w, h = img.width(), img.height()
    src = None
    dst = QRectF(tl.x(), tl.y(), w * scale, h * scale)
    clip = spec["clip"] if spec.get("on") else None
    if clip:
        cx, cy, cw, chh = clip
        cx = max(0, min(w, cx))
        cy = max(0, min(h, cy))
        cw = max(0, min(w - cx, cw))
        chh = max(0, min(h - cy, chh))
        if cw <= 0 or chh <= 0:
            return
        src = QRectF(cx, cy, cw, chh)
        dst = QRectF(tl.x() + cx * scale, tl.y() + cy * scale,
                     cw * scale, chh * scale)
    a = spec.get("alpha", 0)
    mode = ALPHA_MODES.get(a)
    old_op = p.opacity()
    old_cm = p.compositionMode()
    if mode:
        p.setOpacity(mode[1])
        p.setCompositionMode(mode[2])
    if src is not None:
        p.drawImage(dst, img, src)
    else:
        p.drawImage(dst, img)
    if mode:
        p.setOpacity(old_op)
        p.setCompositionMode(old_cm)



# ====================================================================
# 原 bor_sim.py
# ====================================================================

# 每帧速度衰减的默认重力（px / 帧²）。没有 jumpframe 时用不到。
DEFAULT_GRAVITY = 0.8
# 有 jumpframe 时按初速定标，让典型滞空时间落在 20~26 帧左右
_AIR_FRAMES = 12.0

_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
# C 脚本里的 spawn("Name") / spawn(Name)
_SPAWN_CALL_RE = re.compile(r"\bspawn\s*\(\s*[\"']?([\w\-.]+)")
# @cmd 里表示“生成实体”的函数名
_SPAWN_FUNCS = {"spawn", "spawnentity", "summon", "projectile",
                "throwentity"}


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _i(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def anim_cmd_args(anim, cmd):
    """anim 级命令的参数（同名取最后一条）；没有则返回 None。"""
    _, ln = anim.find_cmd(cmd)
    return list(ln.args()) if ln is not None else None


def delay_of(fr):
    """这一帧停多少个“游戏刻”（delay 的单位是厘秒，一刻 = 10ms）。"""
    if fr and fr["delay"]:
        return max(1, _i(fr["delay"][0], 10))
    return 10


def loop_start(anim):
    """loop 1 <起始帧> 的起始帧；没写就是 0。"""
    a = anim_cmd_args(anim, "loop")
    if a and a[0] == "1" and len(a) >= 2:
        return _i(a[1])
    return 0


def anim_of(doc, name):
    """在实体文档里找动画：先全名（忽略大小写），再前缀匹配。"""
    if doc is None or not name:
        return None
    key = str(name).lower()
    for a in doc.anims:
        if a.name.lower() == key:
            return a
    for a in doc.anims:
        if a.name.lower().startswith(key):
            return a
    return None


# ------------------------------------------------------------ move
def frame_velocities(anim):
    """逐帧速度 [(vx, vz), ...]，长度 = 帧数。

    move / movea / movez 和 delay / offset 一样是“帧属性”：写在哪个
    frame 之前，就从那一帧开始生效，直到被下一条改写。
    move  x 的水平速度、z 的深度速度
    movea 同 move（OpenBOR 里表示不受朝向翻转影响，预览里等价）
    movez 只改 z
    """
    out = []
    vx = vz = 0.0
    for ln in anim.lines:
        c, a = ln.cmd(), ln.args()
        if c in ("move", "movea"):
            vx = _f(a[0]) if len(a) >= 1 else 0.0
            vz = _f(a[1]) if len(a) >= 2 else 0.0
        elif c == "movez":
            vz = _f(a[0]) if len(a) >= 1 else 0.0
        elif c == "frame":
            out.append((vx, vz))
    return out


def has_move(anim):
    return any(abs(v[0]) > 1e-9 or abs(v[1]) > 1e-9
               for v in frame_velocities(anim))


# ------------------------------------------------------------ 跳帧
def jump_cmds(anim):
    """{'jump': (帧, vy, vx, vz) | None, 'drop': 帧 | None,
        'land': (帧, 尘) | None}"""
    out = {"jump": None, "drop": None, "land": None}
    a = anim_cmd_args(anim, "jumpframe")
    if a:
        out["jump"] = (_i(a[0]), _f(a[1]) if len(a) > 1 else 0.0,
                       _f(a[2]) if len(a) > 2 else 0.0,
                       _f(a[3]) if len(a) > 3 else 0.0)
    a = anim_cmd_args(anim, "dropframe")
    if a:
        out["drop"] = _i(a[0])
    a = anim_cmd_args(anim, "landframe")
    if a:
        out["land"] = (_i(a[0]), _i(a[1]) if len(a) > 1 else 0)
    return out


class MotionTrack:
    """把动画逐帧展开成位移 / 跳跃高度。

    steps[i] = (dx, dz, dy, airborne, landed)
        dx / dz 相对动画起点的水平、深度位移
        dy       离地高度（画在屏幕上要往上减）
        airborne 本帧是否还在空中
        landed   本帧是否刚好落地（可以用来触发落地尘土）
    """

    def __init__(self, anim, gravity=None):
        self.anim = anim
        frames = anim.frames()
        n = len(frames)
        vels = frame_velocities(anim)
        j = jump_cmds(anim)
        self.jump = j
        jf = j["jump"]
        drop, land = j["drop"], j["land"]
        vy0 = jf[1] if jf else 0.0
        if gravity is None:
            gravity = (max(0.05, vy0 / _AIR_FRAMES) if vy0 > 0
                       else DEFAULT_GRAVITY)
        self.gravity = gravity
        x = z = y = 0.0
        vy = 0.0
        air = False
        just_landed = False
        steps = []
        for i in range(n):
            # —— 本帧显示之前的事件 ——
            if jf is not None and i == jf[0]:
                vy = vy0
                air = True
            if drop is not None and i == drop and vy > 0:
                vy = 0.0                     # 到顶就往下掉
            if land is not None and i == land[0]:
                if air:
                    just_landed = True
                y = 0.0
                vy = 0.0
                air = False
            # 记录本帧的显示状态：第 0 帧永远在起点
            steps.append((x, z, y, air, just_landed))
            just_landed = False
            # —— 本帧持续期间走掉的路 ——
            vx, vz = vels[i] if i < len(vels) else (0.0, 0.0)
            if air and jf is not None:
                # 空中：jumpframe 的 vx / vz 覆盖 move 系（OpenBOR 行为）
                if jf[2]:
                    vx = jf[2]
                if jf[3]:
                    vz = jf[3]
            x += vx
            z += vz
            y += vy
            vy -= gravity
            if y <= 0.0:
                if air:
                    just_landed = True
                y = 0.0
                vy = 0.0
                air = False
        self.steps = steps

    def __len__(self):
        return len(self.steps)

    def at(self, i):
        """第 i 帧的 (dx, dz, dy, airborne, landed)；越界返回 0 位移。"""
        if not self.steps:
            return (0.0, 0.0, 0.0, False, False)
        return self.steps[max(0, min(len(self.steps) - 1, i))]

    @property
    def total(self):
        """整段动画走完的总位移 (dx, dz)。"""
        if not self.steps:
            return (0.0, 0.0)
        s = self.steps[-1]
        return (s[0], s[1])

    @property
    def max_height(self):
        return max((s[2] for s in self.steps), default=0.0)


# ------------------------------------------------------------ 框
def bbox_of(fr):
    """(x, y, w, h, depth)；没有 bbox 返回 None。depth 缺省按 100 算。"""
    if not fr or not fr["bbox"]:
        return None
    a = fr["bbox"]
    try:
        x, y, w, h = (int(float(v)) for v in a[:4])
    except (ValueError, IndexError):
        return None
    d = _i(a[4], 100) if len(a) > 4 else 100
    if w <= 0 or h <= 0:
        return None
    return (x, y, w, h, d)


def attack_box(fr):
    """(x, y, w, h, damage, power, z)；attack 全 0（无框）返回 None。

    OpenBOR 的 attack 写法：attack x y w h damage power block pause z ...
    """
    if not fr or not fr["attack"]:
        return None
    cmd, a = fr["attack"]
    if len(a) < 4:
        return None
    try:
        x, y = int(float(a[0])), int(float(a[1]))
        w, h = int(float(a[2])), int(float(a[3]))
    except ValueError:
        return None
    if cmd != "attack" and (w <= 0 and h <= 0 and x == 0 and y == 0):
        return None
    if w == 0 and h == 0 and x == 0 and y == 0:
        return None                              # attack 0 = 没有攻击框
    dmg = _i(a[4]) if len(a) > 4 else 0
    power = _i(a[5]) if len(a) > 5 else 0
    z = _i(a[9]) if len(a) > 9 else 0
    return (x, y, w, h, dmg, power, z)


def anim_has_attack(anim):
    """这个动画里有没有任何一帧带攻击框。"""
    if anim is None:
        return False
    return any(attack_box(fr) is not None for fr in anim.frames())


def anim_hit_fx(anim):
    """(hitfx 名, hitflash 名)：anim 级命令，可能缺省。"""
    a = anim_cmd_args(anim, "hitfx")
    b = anim_cmd_args(anim, "hitflash")
    return (a[0] if a else None, b[0] if b else None)


# ------------------------------------------------------------ 生成实体
def _atcmd_spec(tokens):
    """@cmd spawn Name 10 0 0 → {'name':..., 'x':..., 'y':..., 'z':...}"""
    rest = tokens[1:]
    name = None
    nums = []
    for t in rest:
        t = t.strip().strip('"').strip("'")
        if not t:
            continue
        if name is None and not _NUM_RE.match(t):
            name = t
        elif _NUM_RE.match(t):
            nums.append(_f(t))
    if not name:
        return None
    return {"name": name, "x": nums[0] if len(nums) > 0 else 0.0,
            "y": nums[1] if len(nums) > 1 else 0.0,
            "z": nums[2] if len(nums) > 2 else 0.0}


def spawn_frames(anim):
    """{帧号: [生成说明, ...]} —— 哪些帧会生成新 entity。

    认三种写法：
      ① @cmd spawn <名字> [x] [y] [z]        （帧内 @cmd）
      ② 帧脚本里的 spawn("名字") / spawn(名字)
      ③ anim 级 spawnframe <帧> [x] [y] [z] + subentity / custentity <名字>
    脚本块内容一律按原样扫，不改动文档。
    """
    out = {}
    frames = anim.frames()
    for i, fr in enumerate(frames):
        specs = []
        for li in fr["atcmd"]:
            a = anim.lines[li].args()
            if not a:
                continue
            if a[0].lower().strip() not in _SPAWN_FUNCS:
                continue
            sp = _atcmd_spec(a)
            if sp:
                sp["src"] = "@cmd " + a[0]
                specs.append(sp)
        if fr["script"]:
            s, e = fr["script"]
            for li in range(s, min(e + 1, len(anim.lines))):
                code, _ = split_comment(anim.lines[li].raw)
                for m in _SPAWN_CALL_RE.finditer(code):
                    specs.append({"name": m.group(1), "x": 0.0, "y": 0.0,
                                  "z": 0.0, "src": "spawn()"})
        if specs:
            out[i] = specs
    sp = anim_cmd_args(anim, "spawnframe")
    if sp:
        ent = (anim_cmd_args(anim, "subentity")
               or anim_cmd_args(anim, "custentity"))
        if ent:
            f = _i(sp[0])
            out.setdefault(f, []).append(
                {"name": ent[0], "x": _f(sp[1]) if len(sp) > 1 else 0.0,
                 "y": _f(sp[2]) if len(sp) > 2 else 0.0,
                 "z": _f(sp[3]) if len(sp) > 3 else 0.0,
                 "src": "spawnframe"})
    return out


def spawn_index(anim):
    """{帧号: True}：只用来在时间轴上打标记（比 spawn_frames 便宜）。"""
    return set(spawn_frames(anim).keys())


# ------------------------------------------------------------ 碰撞
def scene_box(box, px, py, ox, oy, z):
    """把精灵局部框换算成场景框 (x0, x1, y0, y1, depth, zc)。

    px / py：实体“原点”在场景里的水平位置与屏幕纵向位置（py 已含 z 与高度）
    ox / oy：当前帧 Offset（精灵左上角 = 原点 - Offset，与编辑器画布一致）
    box    ：(x, y, w, h, depth)，x/y 相对精灵左上角
    z      ：实体的深度（用于“厚度”那一维的判定）
    """
    bx, by, w, h, d = box
    left = px - ox + bx
    top = py - oy + by
    return (left, left + w, top, top + h, d, z)


def boxes_overlap(a, b, z_tol=None):
    """两个场景框是否真的撞上。

    三件事都要成立：水平相交、屏幕纵向相交、深度够近
    （|z1 - z2| <= 两者厚度之和的一半）。z_tol 给定时覆盖默认厚度。
    """
    if z_tol is None:
        z_tol = (a[4] + b[4]) * 0.5
    if a[0] >= b[1] or b[0] >= a[1]:
        return False
    if a[2] >= b[3] or b[2] >= a[3]:
        return False
    return abs(a[5] - b[5]) <= max(1.0, z_tol)


def overlap_point(a, b):
    """两个相交场景框的重心（用来放命中特效）。"""
    x = (max(a[0], b[0]) + min(a[1], b[1])) * 0.5
    y = (max(a[2], b[2]) + min(a[3], b[3])) * 0.5
    return (x, y, (a[5] + b[5]) * 0.5)



# ====================================================================
# 兼容别名：合并前这些是独立模块，老代码按模块名调用的地方照旧可用
# ====================================================================
openbor = bl = bs = bmd = bor_dm = sys.modules[__name__]
