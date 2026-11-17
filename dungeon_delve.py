#!/usr/bin/env python3
"""
DUNGEON DELVE - a d20 fantasy dungeon crawler for your terminal.

    python3 dungeon_delve.py              play
    python3 dungeon_delve.py --seed 42    repeatable dungeon
    python3 dungeon_delve.py --no-color   plain text output
    python3 dungeon_delve.py --fast       no pauses between combat rolls

Type 'help' in game for commands.
"""

import json
import os
import random
import re
import shutil
import sys
import textwrap
import time
from collections import deque
from contextlib import contextmanager

try:  # for live arrow-key movement (Unix terminals)
    import termios
    import tty
except ImportError:
    termios = tty = None

SAVE_PATH = os.path.expanduser("~/.dungeon_delve_save.json")
def enable_windows_ansi():
    """Windows consoles need ANSI color codes switched on explicitly. True if colors will work."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ and enable_windows_ansi()
FINAL_FLOOR = 5
MAX_LEVEL = 8
XP_LEVELS = [0, 100, 300, 700, 1300, 2100, 3200, 4600]  # XP needed to reach level (index + 1)
GRID_W, GRID_H = 7, 5
COMBAT_DELAY = 1.5  # seconds between combat rolls; --fast turns it off

# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else str(text)


def red(t): return paint(t, "31")
def green(t): return paint(t, "32")
def yellow(t): return paint(t, "33")
def blue(t): return paint(t, "34")
def magenta(t): return paint(t, "35")
def cyan(t): return paint(t, "36")
def bold(t): return paint(t, "1")
def dim(t): return paint(t, "2")


def strip_ansi(t):
    return ANSI_RE.sub("", t)


def say(text=""):
    """Print prose, word-wrapped."""
    for para in str(text).split("\n"):
        print(textwrap.fill(para, 78) if para and not ANSI_RE.search(para) else para)


def ask(prompt="> ", keep_case=False):
    try:
        answer = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nFarewell, adventurer.")
        sys.exit(0)
    return answer if keep_case else answer.lower()


KEY_NAMES = {"A": "up", "B": "down", "C": "right", "D": "left"}  # ESC [ A..D
ARROW_DIRS = {"up": "n", "down": "s", "right": "e", "left": "w"}
WASD_DIRS = {"w": "n", "a": "w", "s": "s", "d": "e"}


def interactive():
    """True when we can read single keypresses (a real Unix terminal)."""
    return termios is not None and sys.stdin.isatty() and sys.stdout.isatty()


def out(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def farewell():
    print("\nFarewell, adventurer.")
    sys.exit(0)


def _keys(fd):
    """Yield keypresses: 'up'/'down'/'left'/'right', 'enter', 'backspace', 'eof', or a character."""
    while True:
        chunk = os.read(fd, 64).decode("utf-8", errors="ignore")
        i = 0
        while i < len(chunk):
            ch = chunk[i]
            i += 1
            if ch == "\x1b":  # escape sequence (arrow keys and friends)
                if i < len(chunk) and chunk[i] in "[O":
                    i += 1
                    while i < len(chunk) and not chunk[i].isalpha() and chunk[i] != "~":
                        i += 1
                    code = chunk[i] if i < len(chunk) else ""
                    i += 1
                    if code in KEY_NAMES:
                        yield KEY_NAMES[code]
                continue
            if ch in ("\r", "\n"):
                yield "enter"
            elif ch in ("\x7f", "\x08"):
                yield "backspace"
            elif ch == "\x04":  # Ctrl-D
                yield "eof"
            elif ch.isprintable():
                yield ch


@contextmanager
def raw_keys():
    """Put the terminal in cbreak mode (keys arrive one at a time, no echo) for the block."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield _keys(fd)
    except KeyboardInterrupt:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        farewell()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_command(prompt="> "):
    """Read a command line, but return instantly when an arrow key or W/A/S/D is pressed on an
    empty line. Start with '/' to type a command beginning with w, a, s or d (e.g. /search).
    Returns (text, instant): instant is True if text is a direction from a single keypress."""
    if not interactive():
        return ask(prompt).lstrip("/").strip(), False
    out(prompt)
    buf = ""
    with raw_keys() as keys:
        for k in keys:
            if k in ARROW_DIRS or (not buf and k.lower() in WASD_DIRS):
                if not buf:
                    d = ARROW_DIRS.get(k) or WASD_DIRS[k.lower()]
                    out(dim(DIR_NAMES[d]) + "\n")
                    return d, True
            elif k == "enter":
                out("\n")
                return buf.strip().lower().lstrip("/").strip(), False
            elif k == "backspace":
                if buf:
                    buf = buf[:-1]
                    out("\b \b")
            elif k == "eof":
                if not buf:
                    farewell()
            else:
                buf += k
                out(k)


def select(render, count, prompt, resolve, labels, start=0, horizontal=False):
    """Arrow-key menu drawn in place. render(sel) returns the menu's lines with item `sel`
    highlighted. Arrows move the highlight and Enter picks it; typing then Enter is passed to
    resolve(text), which returns an index or None. Once chosen, the menu is redrawn without
    the highlight and with labels[i] after the prompt, leaving a tidy log. Returns the index."""
    prev_key, next_key = ("left", "right") if horizontal else ("up", "down")
    sel, buf, err, drawn = start % count, "", "", 0

    def draw(chosen=None):
        nonlocal drawn
        cols = shutil.get_terminal_size((80, 24)).columns
        if drawn > 1:
            out(f"\033[{drawn - 1}A")
        if chosen is not None:
            lines = render(-1) + [prompt + cyan(labels[chosen])]
        else:
            hint = dim("  (arrows + Enter, or type)") if not buf and not err else ""
            lines = render(sel) + [prompt + buf + (red("  " + err) if err else hint)]
        out("\r\033[J" + "\n".join(lines))
        if chosen is None and not err and not buf:  # park the cursor after the prompt, before the hint
            out("\r" + ("\033[%dC" % vis_len(prompt) if vis_len(prompt) else ""))
        drawn = sum(max(1, -(-vis_len(l) // cols)) for l in lines)

    def finish(i):
        draw(chosen=i)
        out("\n")
        return i

    with raw_keys() as keys:
        draw()
        for k in keys:
            err = ""
            if k in (prev_key, "up" if horizontal else "left"):
                sel, buf = (sel - 1) % count, ""
            elif k in (next_key, "down" if horizontal else "right"):
                sel, buf = (sel + 1) % count, ""
            elif k == "enter":
                if not buf:
                    return finish(sel)
                i = resolve(buf.strip().lower())
                if i is not None:
                    return finish(i)
                buf, err = "", "?"
            elif k == "backspace":
                buf = buf[:-1]
            elif k == "eof":
                if not buf:
                    out("\n")
                    farewell()
            else:
                buf += k
            draw()


def clear_screen():
    if sys.stdout.isatty():
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.flush()


def choose(prompt, options):
    """Show a numbered list and return the chosen index (arrow keys work in a terminal)."""
    def resolve(a):
        if a.isdigit() and 1 <= int(a) <= len(options):
            return int(a) - 1
        matches = [i for i, o in enumerate(options) if a and strip_ansi(o).lower().startswith(a)]
        return matches[0] if len(matches) == 1 else None

    if interactive():
        def render(sel):
            return [f"  {yellow(bold('▸')) if i == sel else ' '} {cyan(i + 1)}) "
                    f"{bold(opt) if i == sel else opt}" for i, opt in enumerate(options)]
        labels = [strip_ansi(o).split("  ")[0].strip()[:40] for o in options]
        return select(render, len(options), prompt, resolve, labels)

    for i, opt in enumerate(options, 1):
        print(f"  {cyan(i)}) {opt}")
    while True:
        a = ask(prompt)
        if a.isdigit() and 1 <= int(a) <= len(options):
            return int(a) - 1
        matches = [i for i, o in enumerate(options) if a and strip_ansi(o).lower().startswith(a)]
        if len(matches) == 1:
            return matches[0]
        print("  Choose a number from the list.")


def confirm(question, default=False):
    """Ask a yes/no question. In a terminal, arrows toggle Yes/No and Enter picks;
    typing y/n works too. Enter on its own takes the default."""
    def resolve(a):
        return 0 if a in ("y", "yes") else 1 if a in ("n", "no") else None

    if interactive():
        def render(sel):
            opts = [paint(" Yes ", "7;1") if sel == 0 else " Yes ",
                    paint(" No ", "7;1") if sel == 1 else " No "]
            return [f"{question}  {opts[0]}  {opts[1]}"]
        return select(render, 2, "  > ", resolve, ["Yes", "No"], start=0 if default else 1,
                      horizontal=True) == 0
    a = ask(f"{question} {'[Y/n]' if default else '[y/N]'} ")
    return default if a == "" else a in ("y", "yes")


def hp_bar(cur, mx, width=16):
    cur = max(cur, 0)
    filled = round(width * cur / mx) if mx else 0
    ratio = cur / mx if mx else 0
    col = green if ratio > 0.5 else yellow if ratio > 0.25 else red
    return col("█" * filled + "░" * (width - filled)) + f" {cur}/{mx}"


def pause():
    ask(dim("  [press Enter]"))


def beat(scale=1.0):
    """Short dramatic pause between combat rolls (skipped when output isn't a terminal)."""
    if COMBAT_DELAY and sys.stdout.isatty():
        sys.stdout.flush()
        time.sleep(COMBAT_DELAY * scale)


# --------------------------------------------------------------------------
# Dice
# --------------------------------------------------------------------------

DICE_RE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$")


def roll(expr, crit=False):
    """Roll an expression like '2d6+3'. A crit doubles the number of dice."""
    m = DICE_RE.match(str(expr).replace(" ", ""))
    if not m:
        return int(expr)
    n = int(m.group(1) or 1) * (2 if crit else 1)
    sides, bonus = int(m.group(2)), int(m.group(3) or 0)
    return sum(random.randint(1, sides) for _ in range(n)) + bonus


def roll_shown(expr, crit=False):
    """Like roll(), but also returns the dice shown, e.g. (11, '2d6+4 [3+4]+4 = 11')."""
    m = DICE_RE.match(str(expr).replace(" ", ""))
    if not m:
        return int(expr), str(expr)
    n = int(m.group(1) or 1) * (2 if crit else 1)
    sides, bonus = int(m.group(2)), int(m.group(3) or 0)
    dice = [random.randint(1, sides) for _ in range(n)]
    total = sum(dice) + bonus
    label = f"{n}d{sides}{fmt_mod(bonus) if bonus else ''}"
    return total, f"{label} [{'+'.join(map(str, dice))}]{fmt_mod(bonus) if bonus else ''} = {total}"


def d20(adv=False, dis=False, lucky=False):
    def one():
        r = random.randint(1, 20)
        if lucky and r == 1:
            r = random.randint(1, 20)
        return r
    a, b = one(), one()
    if adv and not dis:
        return max(a, b)
    if dis and not adv:
        return min(a, b)
    return a


def mod(score):
    return (score - 10) // 2


def fmt_mod(m):
    return f"+{m}" if m >= 0 else str(m)


# --------------------------------------------------------------------------
# Game data
# --------------------------------------------------------------------------

ABILITIES = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]

RACES = {
    "Human": {"bonus": {a: 1 for a in ABILITIES},
              "desc": "+1 to every ability. Versatile and ambitious."},
    "Elf": {"bonus": {"DEX": 2, "INT": 1},
            "desc": "+2 DEX, +1 INT. Keen Senses (traps), Fey Ancestry: advantage on initiative, can't be paralyzed."},
    "Dwarf": {"bonus": {"CON": 2, "STR": 1},
              "desc": "+2 CON, +1 STR. Toughness: +1 max HP per level."},
    "Halfling": {"bonus": {"DEX": 2, "CHA": 1},
                 "desc": "+2 DEX, +1 CHA. Lucky: reroll natural 1s on d20s."},
    "Half-Orc": {"bonus": {"STR": 2, "CON": 1},
                 "desc": "+2 STR, +1 CON. Relentless: survive a killing blow at 1 HP (once per floor). "
                         "Savage crits."},
    "Gnome": {"bonus": {"INT": 2, "CON": 1},
              "desc": "+2 INT, +1 CON. Gnome Cunning: advantage on INT, WIS and CHA saves."},
    "Dragonborn": {"bonus": {"STR": 2, "CHA": 1},
                   "desc": "+2 STR, +1 CHA. Breath Weapon blasts all foes once per rest. Resists fire."},
    "Tiefling": {"bonus": {"CHA": 2, "INT": 1},
                 "desc": "+2 CHA, +1 INT. Hellish Rebuke burns an attacker once per rest. Resists fire."},
}

CLASSES = {
    "Fighter": {
        "hit_die": 10, "priority": ["STR", "CON", "DEX", "WIS", "CHA", "INT"],
        "weapon": {"name": "Longsword", "dice": "1d8", "stat": "STR"},
        "armor": {"name": "Chain Mail", "base": 16, "dex_cap": 0}, "shield": True,
        "caster": None, "saves": ["STR", "CON"],
        "desc": "Armored warrior. Second Wind heals, crits on 19-20 at level 3, Extra Attack at 4.",
    },
    "Rogue": {
        "hit_die": 8, "priority": ["DEX", "CON", "INT", "WIS", "CHA", "STR"],
        "weapon": {"name": "Rapier", "dice": "1d8", "stat": "DEX"},
        "armor": {"name": "Studded Leather", "base": 12, "dex_cap": 99}, "shield": False,
        "caster": None, "saves": ["DEX", "INT"],
        "desc": "Sneak Attack adds bonus dice to every turn's first hit. Escapes easily, finds more loot.",
    },
    "Wizard": {
        "hit_die": 6, "priority": ["INT", "CON", "DEX", "WIS", "CHA", "STR"],
        "weapon": {"name": "Dagger", "dice": "1d4", "stat": "DEX"},
        "armor": {"name": "Robes (Mage Armor)", "base": 13, "dex_cap": 99}, "shield": False,
        "caster": "INT", "saves": ["INT", "WIS"],
        "desc": "Fragile but deadly. Arcane Ward soaks hits; Fire Bolt, Magic Missile, Thunderwave.",
    },
    "Cleric": {
        "hit_die": 8, "priority": ["WIS", "CON", "STR", "DEX", "CHA", "INT"],
        "weapon": {"name": "Mace", "dice": "1d6", "stat": "STR"},
        "armor": {"name": "Scale Mail", "base": 14, "dex_cap": 2}, "shield": True,
        "caster": "WIS", "saves": ["WIS", "CHA"],
        "desc": "Armored healer. Sacred Flame, Guiding Bolt, Cure Wounds and Turn Undead.",
    },
    "Barbarian": {
        "hit_die": 12, "priority": ["STR", "CON", "DEX", "WIS", "CHA", "INT"],
        "weapon": {"name": "Greataxe", "dice": "1d12", "stat": "STR"},
        "armor": {"name": "Unarmored Defense", "base": 10, "dex_cap": 99, "con": True}, "shield": False,
        "caster": None, "saves": ["STR", "CON"],
        "desc": "Huge HP. Rage halves weapon damage and adds damage; Reckless Attack trades defense for accuracy.",
    },
    "Paladin": {
        "hit_die": 10, "priority": ["STR", "CHA", "CON", "WIS", "DEX", "INT"],
        "weapon": {"name": "Warhammer", "dice": "1d8", "stat": "STR"},
        "armor": {"name": "Chain Mail", "base": 16, "dex_cap": 0}, "shield": True,
        "caster": "CHA", "saves": ["WIS", "CHA"],
        "desc": "Holy knight. Spend spell slots on Divine Smite for burst damage; Lay on Hands heals.",
    },
    "Ranger": {
        "hit_die": 10, "priority": ["DEX", "WIS", "CON", "STR", "INT", "CHA"],
        "weapon": {"name": "Longbow", "dice": "1d8", "stat": "DEX"},
        "armor": {"name": "Scale Mail", "base": 14, "dex_cap": 2}, "shield": False,
        "caster": "WIS", "saves": ["STR", "DEX"],
        "desc": "Archer (+2 to hit). Hunter's Mark adds 1d6 to every hit; spots traps and loot.",
    },
}

SPELLS = {
    "Fire Bolt": {"classes": ["Wizard"], "slot": 0, "kind": "attack", "dice": "1d10", "cantrip": True,
                  "desc": "Spell attack, 1d10 + INT fire. Free to cast."},
    "Magic Missile": {"classes": ["Wizard"], "slot": 1, "kind": "missile", "dice": "1d4+1",
                      "desc": "Three glowing darts that never miss, 1d4+1 each."},
    "Thunderwave": {"classes": ["Wizard"], "slot": 1, "kind": "save", "dice": "2d8", "aoe": True, "half": True,
                    "desc": "Hits ALL foes for 2d8 thunder. Save for half."},
    "Sacred Flame": {"classes": ["Cleric"], "slot": 0, "kind": "save", "dice": "1d8", "cantrip": True, "half": False,
                     "desc": "Target saves or takes 1d8 + WIS radiant. Free to cast."},
    "Guiding Bolt": {"classes": ["Cleric"], "slot": 1, "kind": "attack", "dice": "4d6", "guide": True,
                     "desc": "Spell attack, 4d6 radiant. Your next attack has advantage."},
    "Cure Wounds": {"classes": ["Cleric", "Ranger"], "slot": 1, "kind": "heal", "dice": "2d8",
                    "desc": "Heal 2d8 + WIS (3d8 + WIS at level 3)."},
    "Fireball": {"classes": ["Wizard"], "slot": 1, "kind": "save", "dice": "8d6", "aoe": True, "half": True,
                 "min_level": 5, "desc": "Level 5+. ALL foes take 8d6 fire. Save for half."},
    "Spirit Guardians": {"classes": ["Cleric"], "slot": 1, "kind": "aura", "dice": "3d8", "min_level": 5,
                         "desc": "Level 5+. Spirits circle you: every foe takes 3d8 radiant on each of its turns."},
    "Hunter's Mark": {"classes": ["Ranger"], "slot": 1, "kind": "mark", "dice": "1d6",
                      "desc": "Bonus action: your hits deal +1d6 for the rest of the fight."},
}

ITEMS = {
    "Healing Potion": {"price": 50, "heal": "2d4+2", "desc": "Restores 2d4+2 HP."},
    "Greater Healing Potion": {"price": 150, "heal": "4d4+4", "desc": "Restores 4d4+4 HP."},
    "Scroll of Fireball": {"price": 150, "combat": True,
                           "desc": "All foes take 6d6 fire (DEX save DC 15 for half)."},
    "Smoke Bomb": {"price": 40, "combat": True, "desc": "Escape any fight (except the final one)."},
}

MONSTERS = {
    # floor 1
    "Giant Rat": dict(hp="2d6", ac=12, hit=4, dmg="1d4+2", xp=25, save=0, init=2),
    "Kobold": dict(hp="2d6", ac=12, hit=4, dmg="1d4+2", xp=25, save=0, init=2),
    "Goblin": dict(hp="2d6", ac=15, hit=4, dmg="1d6+2", xp=50, save=1, init=2),
    "Skeleton": dict(hp="2d8+4", ac=13, hit=4, dmg="1d6+2", xp=50, save=0, init=2, undead=True),
    # floor 2
    "Orc": dict(hp="2d8+6", ac=13, hit=5, dmg="1d12+3", xp=100, save=1, init=1),
    "Zombie": dict(hp="3d8+9", ac=8, hit=3, dmg="1d6+1", xp=50, save=0, init=-2, undead=True),
    "Giant Spider": dict(hp="4d10+4", ac=14, hit=5, dmg="1d8+3", xp=200, save=2, init=3, special="poison"),
    "Goblin Warchief": dict(hp="6d6", ac=17, hit=4, dmg="2d6+2", xp=200, save=1, init=2),
    # floor 3
    "Ghoul": dict(hp="5d8", ac=12, hit=4, dmg="2d4+2", xp=200, save=1, init=2, undead=True, special="paralyze"),
    "Ogre": dict(hp="7d10+21", ac=11, hit=6, dmg="2d8+4", xp=450, save=0, init=-1),
    "Wight": dict(hp="6d8+18", ac=14, hit=4, dmg="1d8+2", xp=700, save=2, init=2, undead=True, special="drain"),
    # floor 4
    "Troll": dict(hp="6d10+24", ac=15, hit=7, dmg="2d6+4", xp=500, save=2, init=1, special="regen"),
    "Minotaur": dict(hp="6d10+18", ac=14, hit=6, dmg="2d12+4", xp=450, save=2, init=0),
    "Gargoyle": dict(hp="7d8+21", ac=15, hit=5, dmg="2d6+2", xp=350, save=2, init=0),
    "Wraith": dict(hp="8d8+16", ac=13, hit=6, dmg="3d8+3", xp=600, save=2, init=3, undead=True, special="drain"),
    "Hell Hound": dict(hp="7d8+14", ac=15, hit=5, dmg="1d8+3", xp=450, save=2, init=1,
                       breath=["FIRE BREATH", "6d6", "DEX", 12, "fire"]),
    # floor 5
    "Vampire Spawn": dict(hp="11d8+33", ac=15, hit=7, dmg="2d6+4", xp=900, save=3, init=3,
                          undead=True, special="drain"),
    "Bone Knight": dict(hp="10d10+30", ac=18, hit=7, dmg="2d8+4", xp=900, save=3, init=0, undead=True),
    "Black Drake": dict(hp="10d10+20", ac=17, hit=7, dmg="2d10+4", xp=1100, save=3, init=2,
                        breath=["ACID BREATH", "8d6", "DEX", 14, "acid"]),
    # guardians of the stairs
    "Ruk the Goblin King": dict(hp="30", ac=15, hit=4, dmg="2d6+1", xp=300, save=2, init=3,
                                intro="A goblin in a crown of bent spoons bangs his sword on a shield. "
                                      "\"MINE stairs! Pay toll with your LIFE!\""),
    "The Gravewarden": dict(hp="70", ac=17, hit=6, dmg="2d6+4", xp=1500, save=4, init=1, undead=True,
                            breath=["SOUL REAP", "4d8", "WIS", 14, "necrotic"],
                            intro="An armored corpse twice your height rises from a sarcophagus before the "
                                  "stairs, a scythe dragging sparks across the stone. \"None descend.\""),
    # final boss
    "Morgrath, the Hollow King": dict(hp="125", ac=17, hit=8, dmg="2d10+4", xp=5000, save=6, init=2,
                                      undead=True, boss=True,
                                      breath=["GRAVE BOLT", "8d8", "DEX", 16, "necrotic"]),
}

GUARDIANS = {2: "Ruk the Goblin King", 4: "The Gravewarden"}

ENCOUNTERS = {
    1: [("Giant Rat", 1, 2), ("Kobold", 1, 2), ("Goblin", 1, 1), ("Skeleton", 1, 1), ("Kobold", 1, 1)],
    2: [("Orc", 1, 1), ("Zombie", 1, 2), ("Giant Rat", 2, 3), ("Goblin Warchief", 1, 1),
        ("Skeleton", 2, 2), ("Goblin", 2, 2)],
    3: [("Ghoul", 1, 2), ("Ogre", 1, 1), ("Wight", 1, 1), ("Orc", 2, 2), ("Giant Spider", 1, 2)],
    4: [("Troll", 1, 1), ("Minotaur", 1, 1), ("Gargoyle", 1, 1), ("Wraith", 1, 1), ("Hell Hound", 1, 1),
        ("Ghoul", 2, 3)],
    5: [("Vampire Spawn", 1, 1), ("Bone Knight", 1, 1), ("Black Drake", 1, 1), ("Troll", 1, 2),
        ("Wraith", 1, 2)],
}

FLOOR_NAMES = {1: "The Upper Crypts", 2: "The Goblin Warrens", 3: "The Sunken Halls",
               4: "The Bone Gardens", 5: "The Hollow Throne"}

FLAVOR = [
    "A damp stone chamber. Water drips somewhere in the dark.",
    "Cobwebs hang like curtains from a vaulted ceiling.",
    "Broken crates and rotting barrels litter this old storeroom.",
    "Faded murals of forgotten kings peel from the walls.",
    "Bones crunch underfoot. Something has been eating here.",
    "A narrow hall lined with empty sconces and old scorch marks.",
    "Moss glows faintly green across the cracked flagstones.",
    "An old guard post, its weapon racks long since looted.",
    "The air is cold enough that your breath mists before you.",
    "A collapsed shrine to some nameless god, its idol shattered.",
    "Roots have broken through the ceiling, twisting down like grasping fingers.",
    "A round chamber with a dry fountain at its center.",
    "Rusted chains dangle from the walls of this former prison cell.",
    "Strange runes cover every surface, worn nearly smooth by time.",
    "A long-abandoned kitchen. A cauldron sits cold over dead ashes.",
    "Tattered banners of a fallen house hang from iron hooks.",
]

TRAPS = [
    ("a pressure plate that fires poisoned darts from the walls", "DEX"),
    ("a scything blade hidden in the doorframe", "DEX"),
    ("vents that hiss with choking green gas", "CON"),
    ("a section of floor that collapses into a spiked pit", "DEX"),
    ("a rune that erupts in a burst of crackling energy", "DEX"),
]

DIRS = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0)}
OPP = {"n": "s", "s": "n", "e": "w", "w": "e"}
DIR_NAMES = {"n": "north", "s": "south", "e": "east", "w": "west"}
DIR_ALIASES = {"north": "n", "south": "s", "east": "e", "west": "w",
               "n": "n", "s": "s", "e": "e", "w": "w"}


# --------------------------------------------------------------------------
# The hero
# --------------------------------------------------------------------------

class Hero:
    def __init__(self, name, race, cls, scores):
        self.name, self.race, self.cls, self.scores = name, race, cls, scores
        self.level, self.xp, self.gold = 1, 0, 20
        self.weapon_bonus = self.armor_bonus = 0
        self.inventory = {"Healing Potion": 3}
        # Lone-hero rule: you start with double your hit die, since you have no party to lean on
        self.max_hp = max(1, CLASSES[cls]["hit_die"] * 2 + self.m("CON") + (1 if race == "Dwarf" else 0))
        self.hp = self.max_hp
        self.hit_dice = 1
        self.slots = self.max_slots()
        self.ward = self.max_ward()
        self.lay_pool = self.max_lay()
        self.feature_ready = True
        self.next_adv = False
        self.skip_turn = False
        self.raging = self.marked = self.reckless = self.aura = False
        self.race_ready = True  # once-per-rest racial trait
        self.kills = 0

    @classmethod
    def from_dict(cls, d):
        h = cls.__new__(cls)
        h.__dict__.update({"raging": False, "marked": False, "reckless": False, "aura": False, "race_ready": True, "lay_pool": 0, "ward": 0})
        h.__dict__.update(d)
        return h

    # --- derived stats ---
    def m(self, stat): return mod(self.scores[stat])
    def prof(self): return 2 + (self.level - 1) // 4
    def caster(self): return CLASSES[self.cls]["caster"]
    def weapon(self): return CLASSES[self.cls]["weapon"]
    def lucky(self): return self.race == "Halfling"

    def ac(self):
        c = CLASSES[self.cls]
        dex = min(self.m("DEX"), c["armor"]["dex_cap"]) if c["armor"]["dex_cap"] else 0  # heavy armor ignores DEX
        return (c["armor"]["base"] + dex
                + (self.m("CON") if c["armor"].get("con") else 0)
                + (2 if c["shield"] else 0) + self.armor_bonus)

    def attack_bonus(self):
        archery = 2 if self.cls == "Ranger" else 0
        return self.prof() + self.m(self.weapon()["stat"]) + self.weapon_bonus + archery

    def damage_mod(self):
        rage = (2 if self.level < 6 else 3) if getattr(self, "raging", False) else 0
        return self.m(self.weapon()["stat"]) + self.weapon_bonus + rage

    def spell_attack(self): return self.prof() + self.m(self.caster())
    def spell_dc(self): return 8 + self.prof() + self.m(self.caster())
    def max_slots(self):
        if not self.caster():
            return 0
        if self.cls in ("Paladin", "Ranger"):  # half casters
            return min((self.level + 3) // 2, 5)
        return min(self.level + 2, 7)

    def max_lay(self): return 5 * self.level if self.cls == "Paladin" else 0
    def extra_attack(self):
        return (self.cls == "Fighter" and self.level >= 4) or \
               (self.cls in ("Barbarian", "Paladin", "Ranger") and self.level >= 5)
    def sneak_dice(self): return (self.level + 1) // 2

    def spells(self):
        return [n for n, s in SPELLS.items() if self.cls in s["classes"] and self.level >= s.get("min_level", 1)]

    def max_ward(self):
        return 2 * self.level + self.m("INT") if self.cls == "Wizard" else 0

    def take_damage(self, dmg, physical=False, kind=None):
        if kind == "fire" and self.race in ("Dragonborn", "Tiefling") and dmg > 1:
            print(cyan("  Your blood resists the flames - half damage!"))
            dmg //= 2
        if physical and getattr(self, "raging", False) and dmg > 1:
            print(red("  Your rage shrugs off half the blow!"))
            dmg //= 2
        ward = getattr(self, "ward", 0)
        if ward > 0 and dmg > 0:
            absorbed = min(ward, dmg)
            self.ward = ward - absorbed
            dmg -= absorbed
            print(cyan(f"  Your Arcane Ward absorbs {absorbed} damage ({self.ward} left)."))
        self.hp -= dmg
        if self.hp <= 0 and self.race == "Half-Orc" and getattr(self, "race_ready", False):
            self.race_ready = False
            dmg -= 1 - self.hp
            self.hp = 1
            print(red(bold("  RELENTLESS ENDURANCE! You refuse to fall, and stagger on at 1 HP!")))
        return dmg

    def heal(self, amount):
        before = self.hp
        self.hp = min(self.max_hp, self.hp + amount)
        return self.hp - before

    def add_item(self, name, qty=1):
        self.inventory[name] = self.inventory.get(name, 0) + qty

    def consume(self, name):
        self.inventory[name] -= 1
        if self.inventory[name] <= 0:
            del self.inventory[name]

    # --- checks ---
    def check(self, stat, dc, label, adv=False, proficient=False, extra=0):
        r = d20(adv=adv, lucky=self.lucky())
        bonus = self.m(stat) + (self.prof() if proficient else 0) + extra
        total = r + bonus
        ok = total >= dc
        tag = green("SUCCESS") if ok else red("FAIL")
        print(f"  {label}: d20({r}){fmt_mod(bonus)} = {total} vs DC {dc} - {tag}")
        return ok

    def save(self, stat, dc):
        extra = 0
        if self.cls == "Paladin" and self.level >= 6:
            extra = max(1, self.m("CHA"))
        if self.cls == "Fighter" and self.level >= 6:
            extra = 2
        adv = self.race == "Gnome" and stat in ("INT", "WIS", "CHA")
        label = f"{stat} save" + (" (Gnome Cunning)" if adv else "")
        return self.check(stat, dc, label, adv=adv, proficient=stat in CLASSES[self.cls]["saves"], extra=extra)

    # --- progression ---
    def gain_xp(self, amount):
        self.xp += amount
        print(f"  You gain {yellow(amount)} XP.")
        while self.level < MAX_LEVEL and self.xp >= XP_LEVELS[self.level]:
            self.level_up()

    def level_up(self):
        self.level += 1
        hd = CLASSES[self.cls]["hit_die"]
        gain = max(1, hd // 2 + 1 + self.m("CON")) + (1 if self.race == "Dwarf" else 0)
        self.max_hp += gain
        self.hp += gain
        self.hit_dice += 1
        self.slots = self.max_slots()
        if self.cls == "Paladin":
            self.lay_pool = getattr(self, "lay_pool", 0) + 5
        print()
        print(yellow(bold(f"  *** LEVEL UP! You are now level {self.level}! ***")))
        print(f"  Max HP +{gain} (now {self.max_hp}).")
        if self.caster():
            print(f"  Spell slots: {self.max_slots()} (refreshed).")
        if self.level in (4, 8):
            primary = CLASSES[self.cls]["priority"][0]
            self.scores[primary] = min(20, self.scores[primary] + 2)
            print(f"  Ability Score Improvement: {primary} +2 (now {self.scores[primary]}).")
        perks = {
            ("Fighter", 3): "Improved Critical: you now crit on a 19 or 20.",
            ("Fighter", 4): "Extra Attack: you attack twice per turn.",
            ("Cleric", 3): "Cure Wounds now heals 3d8 + WIS.",
            ("Rogue", 3): "Sneak Attack is now 2d6.",
            ("Wizard", 5): "You learn FIREBALL! And Fire Bolt now deals 2d10.",
            ("Cleric", 5): "You learn SPIRIT GUARDIANS! And Sacred Flame now deals 2d8.",
            ("Rogue", 7): "Sneak Attack is now 4d6.",
            ("Barbarian", 3): "Brutal Rage: Rage now also gives advantage on your first attack each turn.",
            ("Barbarian", 5): "Extra Attack: you attack twice per turn.",
            ("Barbarian", 6): "Rage damage bonus is now +3.",
            ("Paladin", 5): "Extra Attack: you attack twice per turn.",
            ("Paladin", 6): "Aura of Protection: add your CHA modifier to all saving throws.",
            ("Ranger", 5): "Extra Attack: you fire twice per turn.",
            ("Fighter", 6): "Indomitable: +2 to all saving throws.",
            ("Fighter", 7): "Improved Extra Attack: you attack three times per turn.",
            ("Rogue", 5): "Uncanny Dodge: halve the damage of the first hit you take each round. Sneak Attack is now 3d6.",
            ("Wizard", 6): "Empowered Evocation: add INT to the damage of all your spells.",
            ("Cleric", 6): "Potent Spellcasting: Guiding Bolt deals +WIS damage.",
        }
        if (self.cls, self.level) in perks:
            print(f"  New feature - {perks[(self.cls, self.level)]}")
        if self.level in (5, 9):
            print(f"  Proficiency bonus is now +{self.prof()}.")


# --------------------------------------------------------------------------
# Character creation
# --------------------------------------------------------------------------

def roll_scores():
    rolls = []
    for _ in range(6):
        dice = sorted(random.randint(1, 6) for _ in range(4))
        rolls.append(sum(dice[1:]))
    return sorted(rolls, reverse=True)


def create_hero():
    print(bold("\n=== CREATE YOUR HERO ===\n"))
    name = ask("What is your name, adventurer? ", keep_case=True)[:24] or "Nameless"

    print(bold("\nChoose your race:"))
    races = list(RACES)
    race = races[choose("Race> ", [f"{bold(f'{r:<10}')} {RACES[r]['desc']}" for r in races])]

    print(bold("\nChoose your class:"))
    classes = list(CLASSES)
    cls = classes[choose("Class> ", [f"{bold(f'{c:<10}')} {CLASSES[c]['desc']}" for c in classes])]

    while True:
        rolled = roll_scores()
        scores = {}
        for stat, value in zip(CLASSES[cls]["priority"], rolled):
            scores[stat] = value + RACES[race]["bonus"].get(stat, 0)
        print(bold("\nRolling ability scores (4d6, drop the lowest), arranged for your class:"))
        print("  " + "   ".join(f"{a} {scores[a]:>2} ({fmt_mod(mod(scores[a]))})" for a in ABILITIES))
        if confirm("Keep these scores?", default=True):
            break

    hero = Hero(name, race, cls, scores)
    show_sheet(hero)
    return hero


def show_sheet(hero):
    c = CLASSES[hero.cls]
    print()
    print(bold(f"  {hero.name} - Level {hero.level} {hero.race} {hero.cls}"))
    print(f"  HP {hp_bar(hero.hp, hero.max_hp)}   AC {hero.ac()}   Proficiency +{hero.prof()}")
    nxt = f" / {XP_LEVELS[hero.level]} for next level" if hero.level < MAX_LEVEL else " (max level)"
    print(f"  XP {hero.xp}{nxt}   Gold {yellow(hero.gold)}")
    print("  " + "  ".join(f"{a} {hero.scores[a]:>2} ({fmt_mod(hero.m(a))})" for a in ABILITIES))
    w = hero.weapon()
    wb = f" +{hero.weapon_bonus}" if hero.weapon_bonus else ""
    print(f"  Weapon: {w['name']}{wb}  ({fmt_mod(hero.attack_bonus())} to hit, {w['dice']}{fmt_mod(hero.damage_mod())} damage)")
    ab = f" +{hero.armor_bonus}" if hero.armor_bonus else ""
    print(f"  Armor:  {c['armor']['name']}{ab}{' and Shield' if c['shield'] else ''}")
    if hero.caster() and hero.cls == "Paladin":
        print(f"  Spell slots {hero.slots}/{hero.max_slots()} (fuel for Divine Smite)")
    elif hero.caster():
        print(f"  Spell slots {hero.slots}/{hero.max_slots()}   Spell attack {fmt_mod(hero.spell_attack())}   Spell save DC {hero.spell_dc()}")
        for s in hero.spells():
            cost = "cantrip" if SPELLS[s]["slot"] == 0 else "1 slot"
            print(f"    - {s} ({cost}): {SPELLS[s]['desc']}")
    ready = green("ready") if hero.feature_ready else dim("used - recharges on rest")
    feats = {
        "Fighter": [f"Second Wind: heal 1d10+{2 * hero.level} as a bonus action ({ready})"]
                   + (["Improved Critical: crit on 19-20"] if hero.level >= 3 else [])
                   + (["Extra Attack: two attacks per turn"] if 4 <= hero.level < 7 else [])
                   + (["Extra Attack: three attacks per turn"] if hero.level >= 7 else []),
        "Rogue": [f"Sneak Attack: +{hero.sneak_dice()}d6 on your first hit each turn",
                  "Cunning Action: advantage when fleeing",
                  "Keen Eye: proficient at searching and spotting traps"]
                 + (["Uncanny Dodge: halve the first hit you take each round"] if hero.level >= 5 else []),
        "Wizard": ["Arcane Recovery: regain a spell slot on a short rest",
                   f"Arcane Ward: absorbs damage before your HP ({getattr(hero, 'ward', 0)}/{hero.max_ward()}, refills on rest)"],
        "Cleric": [f"Turn Undead: undead foes must make a WIS save or flee ({ready})"],
        "Barbarian": [f"Rage: bonus action, +{2 if hero.level < 6 else 3} damage and half damage from attacks "
                      f"for the fight ({ready})",
                      "Reckless Attack: advantage on your attacks, but enemies get advantage on you that round"]
                     + (["Brutal Rage: advantage on your first attack each turn while raging"] if hero.level >= 3 else []),
        "Paladin": ["Divine Smite: after a hit, spend a slot for +2d8 radiant (+1d8 vs undead)",
                    f"Lay on Hands: heal from a pool of {getattr(hero, 'lay_pool', 0)}/{hero.max_lay()} HP "
                    "(in combat, or 'lay' outside it)"]
                   + (["Aura of Protection: +CHA to saving throws"] if hero.level >= 6 else []),
        "Ranger": ["Archery: +2 to hit with your bow",
                   "Natural Explorer: proficient at spotting traps and finding hidden loot"],
    }[hero.cls]
    if hero.extra_attack() and hero.cls != "Fighter":
        feats.append("Extra Attack: two attacks per turn")
    rr = green("ready") if getattr(hero, "race_ready", True) else dim("used - recharges on rest")
    traits = {
        "Human": "Versatile: +1 to every ability score",
        "Elf": "Keen Senses: advantage when spotting traps; Fey Ancestry: advantage on initiative, immune to paralysis",
        "Dwarf": "Toughness: +1 max HP per level",
        "Halfling": "Lucky: reroll natural 1s on d20s",
        "Half-Orc": f"Relentless Endurance: drop to 1 HP instead of 0, once per floor ({rr}); "
                    "Savage Attacks: crits roll an extra weapon die",
        "Gnome": "Gnome Cunning: advantage on INT, WIS and CHA saving throws",
        "Dragonborn": f"Breath Weapon: {'3d6' if hero.level < 5 else '5d6'}+{hero.level} fire to all foes, "
                      f"once per rest ({rr}); resist fire",
        "Tiefling": f"Hellish Rebuke: 4d10+{hero.level} fire to an attacker after it hits you, "
                    f"once per rest ({rr}); resist fire",
    }
    print(f"  {hero.race} trait: {traits[hero.race]}")
    print("  Features:")
    for f in feats:
        print(f"    - {f}")
    inv = ", ".join(f"{n} x{q}" for n, q in hero.inventory.items()) or "nothing"
    print(f"  Pack: {inv}")


# --------------------------------------------------------------------------
# Dungeon generation
# --------------------------------------------------------------------------

def key(x, y): return f"{x},{y}"
def unkey(k): return tuple(int(v) for v in k.split(","))


def spawn(name):
    m = dict(MONSTERS[name])
    m["name"] = name
    m["hp"] = m["max_hp"] = max(1, roll(m["hp"]))
    return m


def random_encounter(depth):
    name, lo, hi = random.choice(ENCOUNTERS[min(depth, FINAL_FLOOR)])
    group = [spawn(name) for _ in range(random.randint(lo, hi))]
    if len(group) > 1:
        for i, m in enumerate(group):
            m["name"] = f"{name} {chr(65 + i)}"
    return group


def new_room():
    return {"type": "empty", "exits": [], "visited": False, "seen": False,
            "cleared": False, "searched": False, "desc": random.choice(FLAVOR), "monsters": []}


def connect(rooms, a, d):
    x, y = unkey(a)
    dx, dy = DIRS[d]
    b = key(x + dx, y + dy)
    if d not in rooms[a]["exits"]:
        rooms[a]["exits"].append(d)
    if OPP[d] not in rooms[b]["exits"]:
        rooms[b]["exits"].append(OPP[d])


def generate_floor(depth):
    n_rooms = 8 + depth * 2
    start = (random.randrange(GRID_W), random.randrange(GRID_H))
    rooms = {key(*start): new_room()}
    coords = [start]
    while len(coords) < n_rooms:
        x, y = random.choice(coords)
        d = random.choice("nsew")
        nx, ny = x + DIRS[d][0], y + DIRS[d][1]
        if not (0 <= nx < GRID_W and 0 <= ny < GRID_H):
            continue
        k = key(nx, ny)
        if k in rooms:
            if random.random() < 0.15:
                connect(rooms, key(x, y), d)  # occasional loop
            continue
        rooms[k] = new_room()
        connect(rooms, key(x, y), d)
        coords.append((nx, ny))

    # farthest room from the start holds the stairs (or the boss)
    start_k = key(*start)
    dist = {start_k: 0}
    q = deque([start_k])
    while q:
        cur = q.popleft()
        cx, cy = unkey(cur)
        for d in rooms[cur]["exits"]:
            nk = key(cx + DIRS[d][0], cy + DIRS[d][1])
            if nk not in dist:
                dist[nk] = dist[cur] + 1
                q.append(nk)
    far = max(dist, key=dist.get)

    rooms[start_k].update(visited=True, cleared=True, desc="The stairs you came down are behind you.")
    if depth == FINAL_FLOOR:
        rooms[far].update(type="boss", monsters=[spawn("Morgrath, the Hollow King")],
                          desc="A vast throne room. Cold blue fire burns in iron braziers around a throne of fused bone.")
    else:
        rooms[far].update(type="stairs", cleared=True)
        if depth in GUARDIANS:
            rooms[far]["monsters"] = [spawn(GUARDIANS[depth])]

    others = [k for k in rooms if k not in (start_k, far)]
    random.shuffle(others)
    rooms[others.pop()]["type"] = "shrine"
    if depth >= 3:
        rooms[others.pop()]["type"] = "shrine"
    rooms[others.pop()]["type"] = "merchant"
    for k in others:
        t = random.choices(["monster", "treasure", "trap", "empty"], weights=[40, 20, 10, 30])[0]
        rooms[k]["type"] = t
        if t == "monster":
            rooms[k]["monsters"] = random_encounter(depth)
        if t == "empty":
            rooms[k]["cleared"] = True
    for t in ("shrine", "merchant"):
        for k in rooms:
            if rooms[k]["type"] == t:
                rooms[k]["cleared"] = True
    return rooms, list(start)


# --------------------------------------------------------------------------
# Combat
# --------------------------------------------------------------------------

def alive(monsters):
    return [m for m in monsters if m["hp"] > 0]


def show_combat(hero, monsters):
    print()
    slots = f"  Slots {hero.slots}/{hero.max_slots()}" if hero.caster() else ""
    if hero.max_ward():
        slots += f"  Ward {getattr(hero, 'ward', 0)}"
    print(f"  {bold(f'{hero.name:<24}')} {hp_bar(hero.hp, hero.max_hp)}  AC {hero.ac()}{slots}")
    for m in alive(monsters):
        name = f"{m['name']:<24}"
        print(f"  {red(name)} {hp_bar(m['hp'], m['max_hp'])}  AC {m['ac']}")


def pick_target(monsters):
    living = alive(monsters)
    if len(living) == 1:
        return living[0]
    i = choose("  Target> ", [f"{m['name']} ({m['hp']}/{m['max_hp']} HP)" for m in living] + ["Back"])
    return None if i == len(living) else living[i]


def hurt_monster(hero, m, dmg):
    m["hp"] -= dmg
    if m["hp"] <= 0:
        hero.kills += 1
        print(f"  {green('>>')} {bold(m['name'])} is slain!")


def hero_attack(hero, target, monsters):
    w = hero.weapon()
    attacks = 2 if hero.extra_attack() else 1
    if hero.cls == "Fighter" and hero.level >= 7:
        attacks = 3
    crit_on = 19 if hero.cls == "Fighter" and hero.level >= 3 else 20
    sneak_used = False
    first = True
    for i in range(attacks):
        if i:
            beat()
        if target["hp"] <= 0:
            living = alive(monsters)
            if not living:
                return
            target = living[0]
        adv = hero.next_adv or hero.reckless or (first and hero.raging and hero.level >= 3)
        hero.next_adv = False
        first = False
        r = d20(adv=adv, lucky=hero.lucky())
        total = r + hero.attack_bonus()
        roll_txt = f"d20({r}){fmt_mod(hero.attack_bonus())} = {total}{' (advantage)' if adv else ''}"
        if r == 1:
            print(f"  You attack {target['name']} with your {w['name']}: {roll_txt} - {red('natural 1, miss!')}")
            continue
        crit = r >= crit_on
        if crit or total >= target["ac"]:
            dmg = roll(w["dice"], crit) + hero.damage_mod()
            if crit and hero.race == "Half-Orc":
                dmg += roll(w["dice"])
            extra = ""
            if hero.cls == "Rogue" and not sneak_used:
                sa = roll(f"{hero.sneak_dice()}d6", crit)
                dmg += sa
                sneak_used = True
                extra = f" (incl. {sa} Sneak Attack)"
            if hero.marked:
                hm = roll("1d6", crit)
                dmg += hm
                extra += f" (incl. {hm} Hunter's Mark)"
            dmg = max(1, dmg)
            hit = yellow(bold("CRITICAL HIT!")) if crit else green("hit!")
            print(f"  You strike {target['name']} with your {w['name']}: {roll_txt} vs AC {target['ac']} - {hit} "
                  f"{bold(dmg)} damage{extra}.")
            if hero.cls == "Paladin" and hero.slots > 0 and target["hp"] > dmg:
                if confirm(f"  Divine Smite? ({hero.slots} slots)"):
                    hero.slots -= 1
                    smite = roll("3d8" if target.get("undead") else "2d8", crit)
                    dmg += smite
                    print(yellow(f"  Your weapon blazes with holy light! +{smite} radiant - {bold(dmg)} total."))
            hurt_monster(hero, target, dmg)
        else:
            print(f"  You attack {target['name']}: {roll_txt} vs AC {target['ac']} - {red('miss.')}")


def monster_save(m, dc):
    r = random.randint(1, 20)
    total = r + m["save"]
    ok = total >= dc
    print(f"    {m['name']} saves: d20({r}){fmt_mod(m['save'])} = {total} vs DC {dc} - "
          f"{'resists' if ok else 'fails'}")
    return ok


def cast_spell(hero, monsters, bonus_used=False):
    """Returns False (cancelled), True (used your action) or 'bonus' (used a bonus action)."""
    spells = hero.spells()
    labels = []
    for s in spells:
        cost = "cantrip" if SPELLS[s]["slot"] == 0 else f"1 slot ({hero.slots} left)"
        labels.append(f"{s:<14} [{cost}] {SPELLS[s]['desc']}")
    i = choose("  Spell> ", labels + ["Back"])
    if i == len(spells):
        return False
    name = spells[i]
    sp = SPELLS[name]
    if sp["slot"] and hero.slots < sp["slot"]:
        print(red("  You have no spell slots left! Rest to recover them."))
        return False
    dice = sp["dice"]
    if sp.get("cantrip") and hero.level >= 5:
        dice = "2" + dice[1:]
    if name == "Cure Wounds" and hero.level >= 3:
        dice = "3d8"
    bonus = hero.m(hero.caster()) if sp.get("cantrip") else 0
    if hero.level >= 6 and (hero.cls == "Wizard" or name == "Guiding Bolt"):
        bonus = hero.m(hero.caster())

    if sp["kind"] == "aura":
        if hero.aura:
            print("  Your guardians already circle you.")
            return False
        hero.slots -= sp["slot"]
        hero.aura = True
        print(cyan("  Spectral warriors with radiant blades swirl around you!"))
        return True

    if sp["kind"] == "mark":
        if hero.marked:
            print("  Your quarry is already marked.")
            return False
        hero.slots -= sp["slot"]
        hero.marked = True
        print(green("  You mark your quarry. Every hit this fight deals +1d6."))
        if not bonus_used:
            print(dim("  (bonus action - you can still act)"))
            return "bonus"
        return True

    if sp["kind"] == "heal":
        if hero.hp >= hero.max_hp:
            print("  You're already at full health.")
            return False
        hero.slots -= sp["slot"]
        healed = hero.heal(roll(dice) + hero.m("WIS"))
        print(f"  {cyan('Warm light knits your wounds.')} You heal {green(healed)} HP.")
        return True

    if sp.get("aoe"):
        targets = alive(monsters)
    else:
        t = pick_target(monsters)
        if t is None:
            return False
        targets = [t]
    hero.slots -= sp["slot"]

    if sp["kind"] == "missile":
        t = targets[0]
        darts = [roll(dice) + (bonus if hero.level >= 6 else 0) for _ in range(3)]
        print(f"  {cyan('Three bolts of force streak from your fingers!')} {' + '.join(map(str, darts))} = "
              f"{bold(sum(darts))} damage to {t['name']}.")
        hurt_monster(hero, t, sum(darts))
    elif sp["kind"] == "attack":
        t = targets[0]
        adv = hero.next_adv
        hero.next_adv = False
        r = d20(adv=adv, lucky=hero.lucky())
        total = r + hero.spell_attack()
        txt = f"d20({r}){fmt_mod(hero.spell_attack())} = {total}"
        if r != 1 and (r == 20 or total >= t["ac"]):
            dmg = roll(dice, r == 20) + bonus
            crit = yellow(bold(" CRITICAL!")) if r == 20 else ""
            print(f"  {cyan(name)} at {t['name']}: {txt} vs AC {t['ac']} - {green('hit!')}{crit} {bold(dmg)} damage.")
            hurt_monster(hero, t, dmg)
            if sp.get("guide") and alive(monsters):
                hero.next_adv = True
                print(dim("  The target glimmers with holy light - your next attack has advantage."))
        else:
            print(f"  {cyan(name)} at {t['name']}: {txt} vs AC {t['ac']} - {red('miss.')}")
    elif sp["kind"] == "save":
        dmg = roll(dice) + bonus
        print(f"  {cyan(name)}! (DC {hero.spell_dc()}, {dmg} damage)")
        for t in targets:
            if monster_save(t, hero.spell_dc()):
                taken = dmg // 2 if sp.get("half") else 0
            else:
                taken = dmg
            if taken:
                print(f"    {t['name']} takes {bold(taken)} damage.")
                hurt_monster(hero, t, taken)
    return True


def turn_undead(hero, monsters):
    hero.feature_ready = False
    print(cyan(f"  You raise your holy symbol and command the dead to flee! (DC {hero.spell_dc()})"))
    for m in alive(monsters):
        if not m.get("undead"):
            continue
        if m.get("boss"):
            print(f"    {m['name']} laughs. \"Your god has no power here.\"")
            continue
        if not monster_save(m, hero.spell_dc()):
            print(f"    {green(m['name'] + ' flees in terror and crumbles to dust!')}")
            m["hp"] = 0
            hero.kills += 1


def lay_on_hands(hero):
    missing = hero.max_hp - hero.hp
    if hero.cls != "Paladin":
        print("  Only a paladin can do that.")
        return False
    if hero.lay_pool <= 0 or missing <= 0:
        print("  You have no healing to give, or no wounds to heal.")
        return False
    amount = min(missing, hero.lay_pool)
    hero.lay_pool -= amount
    hero.heal(amount)
    print(green(f"  You press a glowing hand to your wounds and heal {amount} HP. (pool: {hero.lay_pool} left)"))
    return True


def dragon_breath(hero, monsters):
    hero.race_ready = False
    dice = "3d6" if hero.level < 5 else "5d6"
    dc = 8 + hero.m("CON") + hero.prof()
    dmg = roll(dice) + hero.level
    print(yellow(bold(f"  You draw a deep breath and exhale a roaring gout of flame! (DC {dc}, {dmg} damage)")))
    for m in alive(monsters):
        taken = dmg // 2 if monster_save(m, dc) else dmg
        print(f"    {m['name']} takes {bold(taken)} fire damage.")
        hurt_monster(hero, m, taken)


def end_combat(hero):
    hero.next_adv = hero.skip_turn = False
    hero.raging = hero.marked = hero.reckless = hero.aura = False


def item_menu(hero, combat=False):
    names = [n for n in hero.inventory if combat or not ITEMS[n].get("combat")]
    if not names:
        print("  You have nothing you can use right now.")
        return None
    i = choose("  Item> ", [f"{n} x{hero.inventory[n]} - {ITEMS[n]['desc']}" for n in names] + ["Back"])
    return None if i == len(names) else names[i]


def use_item(hero, name, monsters=None, can_flee=True):
    """Returns 'used', 'fled', or None if nothing happened."""
    it = ITEMS[name]
    if "heal" in it:
        if hero.hp >= hero.max_hp:
            print("  You're already at full health.")
            return None
        hero.consume(name)
        healed = hero.heal(roll(it["heal"]))
        print(f"  You drink the {name}. You heal {green(healed)} HP. {hp_bar(hero.hp, hero.max_hp)}")
        return "used"
    if monsters is None:
        print("  That can only be used in combat.")
        return None
    if name == "Scroll of Fireball":
        hero.consume(name)
        dmg = roll("6d6")
        print(yellow(bold("  You read the scroll - a bead of fire blooms into a roaring explosion!")) + f" ({dmg} damage)")
        for m in alive(monsters):
            taken = dmg // 2 if monster_save(m, 15) else dmg
            print(f"    {m['name']} takes {bold(taken)} damage.")
            hurt_monster(hero, m, taken)
        return "used"
    if name == "Smoke Bomb":
        if not can_flee:
            print(red("  A cold gaze pierces the smoke. There is no escape!"))
            return None
        hero.consume(name)
        print(dim("  You hurl the smoke bomb and vanish in a choking grey cloud."))
        return "fled"
    return None


def hero_turn(game, monsters, can_flee):
    hero = game.hero
    if hero.skip_turn:
        hero.skip_turn = hero.reckless = False
        print(magenta("  You are paralyzed and cannot act this turn!"))
        return None
    bonus_used = False
    while True:
        show_combat(hero, monsters)
        opts = ["a"]
        menu = [f"[{cyan('a')}]ttack"]
        hero.reckless = False
        if hero.spells():
            opts.append("c"); menu.append(f"[{cyan('c')}]ast")
        if hero.cls == "Barbarian":
            opts.append("x"); menu.append(f"[{cyan('x')}] reckless attack")
            if hero.feature_ready and not hero.raging and not bonus_used:
                opts.append("b"); menu.append(f"[{cyan('b')}]erserker rage")
        if hero.cls == "Paladin" and hero.lay_pool > 0 and hero.hp < hero.max_hp:
            opts.append("l"); menu.append(f"[{cyan('l')}]ay on hands ({hero.lay_pool})")
        if hero.cls == "Fighter" and hero.feature_ready and not bonus_used and hero.hp < hero.max_hp:
            opts.append("s"); menu.append(f"[{cyan('s')}]econd wind")
        if hero.cls == "Cleric" and hero.feature_ready and any(m.get("undead") for m in alive(monsters)):
            opts.append("t"); menu.append(f"[{cyan('t')}]urn undead")
        if hero.race == "Dragonborn" and hero.race_ready:
            opts.append("d"); menu.append(f"[{cyan('d')}]ragon breath")
        if hero.inventory:
            opts.append("i"); menu.append(f"[{cyan('i')}]tem")
        if can_flee:
            opts.append("r"); menu.append(f"[{cyan('r')}]un")
        if interactive():
            def render(sel):
                return ["  " + "  ".join(paint(strip_ansi(m), "7;1") if i == sel else m
                                         for i, m in enumerate(menu))]
            a = opts[select(render, len(opts), "  Action> ",
                            lambda t: opts.index(t[0]) if t[:1] in opts else None,
                            [strip_ansi(m).replace("[", "").replace("]", "") for m in menu],
                            horizontal=True)]
        else:
            print("  " + "  ".join(menu))
            a = ask("  Action> ")[:1]
        if a not in opts:
            print("  Pick one of the bracketed letters.")
            continue
        if a == "a":
            t = pick_target(monsters)
            if t:
                hero_attack(hero, t, monsters)
                return None
        elif a == "c":
            res = cast_spell(hero, monsters, bonus_used)
            if res == "bonus":
                bonus_used = True
                continue
            if res:
                return None
        elif a == "x":
            t = pick_target(monsters)
            if t:
                hero.reckless = True
                print(red("  You throw caution to the wind!"))
                hero_attack(hero, t, monsters)
                return None
        elif a == "b":
            hero.feature_ready = False
            hero.raging = True
            bonus_used = True
            print(red(bold("  You fly into a RAGE! (bonus action - you can still act)")))
        elif a == "l":
            lay_on_hands(hero)
            return None
        elif a == "d":
            dragon_breath(hero, monsters)
            return None
        elif a == "s":
            hero.feature_ready = False
            bonus_used = True
            healed = hero.heal(roll("1d10") + 2 * hero.level)
            print(green(f"  You catch your breath and steel yourself. Second Wind heals {healed} HP. "
                        "(bonus action - you can still act)"))
        elif a == "t":
            turn_undead(hero, monsters)
            return None
        elif a == "i":
            name = item_menu(hero, combat=True)
            if name:
                result = use_item(hero, name, monsters, can_flee)
                if result == "used" and "heal" in ITEMS[name] and not bonus_used:
                    bonus_used = True
                    print(dim("  (Drinking a potion is a bonus action - you can still act.)"))
                    continue
                if result:
                    return result
        elif a == "r":
            dc = 10 + game.depth
            if hero.check("DEX", dc, "Escape (DEX)", adv=hero.cls == "Rogue"):
                print("  You break away and flee back the way you came!")
                return "fled"
            print(red("  You can't get away!"))
            return None


def monsters_turn(hero, monsters):
    dodge = hero.cls == "Rogue" and hero.level >= 5
    for m in alive(monsters):
        if hero.hp <= 0:
            return
        beat()
        if hero.aura:
            sg = roll("3d8")
            print(cyan(f"  Your spirit guardians slash {m['name']} for {sg} radiant damage."))
            hurt_monster(hero, m, sg)
            if m["hp"] <= 0:
                continue
        if m.get("special") == "regen" and m["hp"] < m["max_hp"]:
            healed = min(5, m["max_hp"] - m["hp"])
            m["hp"] += healed
            print(magenta(f"  {m['name']}'s wounds knit together. (+{healed} HP)"))
        if m.get("breath"):
            m["turns"] = m.get("turns", 0) + 1
            if m["turns"] % 3 == 0:
                label, dice, stat, dc, kind = m["breath"]
                dmg, dmg_txt = roll_shown(dice)
                print(magenta(bold(f"  {m['name']} unleashes {label}!")))
                beat(0.6)
                print(f"  Damage roll: {dmg_txt}")
                if hero.save(stat, dc):
                    dmg //= 2
                taken = hero.take_damage(dmg, kind=kind)
                print(f"  You take {red(bold(taken))} {kind} damage.")
                continue
        r = random.randint(1, 20)
        if hero.reckless:
            r = max(r, random.randint(1, 20))
        total = r + m["hit"]
        txt = f"d20({r}){fmt_mod(m['hit'])} = {total} vs AC {hero.ac()}"
        if r == 1 or (r != 20 and total < hero.ac()):
            print(f"  {m['name']} attacks: {txt} - {dim('misses.')}")
            continue
        crit = r == 20
        dmg, dmg_txt = roll_shown(m["dmg"], crit)
        tag = red(bold("CRITICAL HIT!")) if crit else red("hits!")
        print(f"  {m['name']} attacks: {txt} - {tag}")
        beat(0.6)
        print(f"  {m['name']} rolls damage: {dmg_txt}")
        if dodge:
            dodge = False
            dmg //= 2
            print(cyan("  Uncanny Dodge! You twist away from the worst of it."))
        taken = hero.take_damage(dmg, physical=True)
        print(f"  You take {red(bold(taken))} damage.")
        if hero.race == "Tiefling" and hero.race_ready and hero.hp > 0:
            if confirm(f"  Hellish Rebuke {m['name']}? (once per rest)"):
                hero.race_ready = False
                rb = roll("4d10") + hero.level
                dc = 8 + hero.m("CHA") + hero.prof()
                print(red(f"  Hellfire engulfs {m['name']}! (DC {dc}, {rb} damage)"))
                taken_rb = rb // 2 if monster_save(m, dc) else rb
                print(f"    {m['name']} takes {bold(taken_rb)} fire damage.")
                hurt_monster(hero, m, taken_rb)
                if m["hp"] <= 0:
                    continue
        sp = m.get("special")
        if hero.hp > 0 and sp == "poison":
            print("  Venom burns in your veins...")
            extra, extra_txt = roll_shown("2d8")
            print(f"  Poison roll: {extra_txt}")
            if hero.save("CON", 11):
                extra //= 2
            hero.take_damage(extra)
            print(f"  You take {red(extra)} poison damage.")
        elif hero.hp > 0 and sp == "paralyze" and hero.race == "Elf":
            print(cyan("  Its claws try to numb you, but your fey blood shrugs off the paralysis."))
        elif hero.hp > 0 and sp == "paralyze":
            print("  Its filthy claws numb your flesh...")
            if not hero.save("CON", 10):
                hero.skip_turn = True
                print(magenta("  You are paralyzed!"))
        elif sp == "drain":
            healed = min(m["max_hp"] - m["hp"], dmg // 2)
            m["hp"] += healed
            if healed:
                print(magenta(f"  {m['name']} drinks your life force and heals {healed} HP."))


def combat(game, monsters, can_flee=True):
    """Returns 'won', 'fled', or 'dead'."""
    hero = game.hero
    print()
    print(red(bold("  ⚔  COMBAT!  ")) + ", ".join(m["name"] for m in alive(monsters)))
    hi = d20(adv=hero.race == "Elf", lucky=hero.lucky()) + hero.m("DEX")
    mi = random.randint(1, 20) + max(m["init"] for m in alive(monsters))
    hero_first = hi >= mi
    print(f"  Initiative: you {hi}, enemies {mi} - {'you act first!' if hero_first else 'they act first!'}")
    order = ["hero", "monsters"] if hero_first else ["monsters", "hero"]
    while True:
        for actor in order:
            if not alive(monsters):
                break
            if actor == "hero":
                if hero_turn(game, monsters, can_flee) == "fled":
                    end_combat(hero)
                    return "fled"
            else:
                print()
                beat(0.5)
                monsters_turn(hero, monsters)
                if hero.hp <= 0:
                    end_combat(hero)
                    return "dead"
        if not alive(monsters):
            break

    xp = sum(m["xp"] for m in monsters)
    gold = sum(random.randint(0, m["xp"] // 5) for m in monsters)
    end_combat(hero)
    print(green(bold("\n  VICTORY!")))
    hero.gold += gold
    if gold:
        print(f"  You loot {yellow(gold)} gold.")
    if random.random() < 0.3:
        hero.add_item("Healing Potion")
        print(f"  You find a {cyan('Healing Potion')}!")
    hero.gain_xp(xp)
    return "won"


# --------------------------------------------------------------------------
# Game state & exploration
# --------------------------------------------------------------------------

class Game:
    def __init__(self, hero):
        self.hero = hero
        self.depth = 1
        self.rooms, self.pos = generate_floor(1)
        self.prev = list(self.pos)
        self.short_rests = 0
        self.auto_map = True
        self.big_map = True
        self.mark_seen()

    def to_dict(self):
        return {"hero": self.hero.__dict__, "depth": self.depth, "rooms": self.rooms,
                "pos": self.pos, "prev": self.prev, "short_rests": self.short_rests,
                "auto_map": self.auto_map, "big_map": self.big_map}

    @classmethod
    def from_dict(cls, d):
        g = cls.__new__(cls)
        g.hero = Hero.from_dict(d["hero"])
        g.depth, g.rooms, g.pos, g.prev = d["depth"], d["rooms"], d["pos"], d["prev"]
        g.short_rests = d["short_rests"]
        g.auto_map = d.get("auto_map", True)
        g.big_map = d.get("big_map", True)
        return g

    @property
    def room(self):
        return self.rooms[key(*self.pos)]

    def mark_seen(self):
        x, y = self.pos
        self.room["seen"] = True
        for d in self.room["exits"]:
            self.rooms[key(x + DIRS[d][0], y + DIRS[d][1])]["seen"] = True


def save_game(game):
    with open(SAVE_PATH, "w") as f:
        json.dump(game.to_dict(), f)
    print(green(f"  Game saved to {SAVE_PATH}"))


def delete_save():
    if os.path.exists(SAVE_PATH):
        os.remove(SAVE_PATH)


MAP_INNER = GRID_W * 4          # characters per map row
MAP_WIDTH = MAP_INNER + 4       # including the box border and padding
MAP_LEGEND = [dim("@ you   ? unexplored   ! enemies"),
              dim("> stairs   + shrine   $ shop   B boss")]


def vis_len(text):
    return len(strip_ansi(text))


def pad(text, width):
    return text + " " * max(0, width - vis_len(text))


def room_symbol(game, x, y):
    """The map symbol for a room and the color function to draw it in (None = plain)."""
    r = game.rooms[key(x, y)]
    if not r["visited"]:
        return "?", dim
    guarded = alive(r["monsters"])
    if r["type"] == "stairs":
        return ">", (lambda t: red(bold(t))) if guarded else cyan
    if r["type"] == "boss":
        return "B", lambda t: red(bold(t))
    if r["type"] == "shrine":
        return "+", green if not r.get("used") else dim
    if r["type"] == "merchant":
        return "$", yellow
    return ("!", red) if guarded else (" ", None)


def map_cell(game, x, y):
    if [x, y] == game.pos:
        return yellow(bold("[@]"))
    ch, col = room_symbol(game, x, y)
    return col(f"[{ch}]") if col else f"[{ch}]"


def map_lines(game):
    """The current floor as a boxed grid, one string per line."""
    title = f" Floor {game.depth} "
    lines = [dim("┌" + title + "─" * (MAP_WIDTH - 2 - len(title)) + "┐")]
    for y in range(GRID_H):
        row, below = "", ""
        for x in range(GRID_W):
            r = game.rooms.get(key(x, y))
            if not r or not r["seen"]:
                row += "    "
                below += "    "
                continue
            row += map_cell(game, x, y)
            east = game.rooms.get(key(x + 1, y))
            south = game.rooms.get(key(x, y + 1))
            row += "-" if "e" in r["exits"] and (r["visited"] or east["visited"]) else " "
            below += " |  " if "s" in r["exits"] and (r["visited"] or south["visited"]) else "    "
        lines.append(dim("│ ") + pad(row, MAP_INNER) + dim(" │"))
        if y < GRID_H - 1:
            lines.append(dim("│ ") + pad(below, MAP_INNER) + dim(" │"))
    lines.append(dim("└" + "─" * (MAP_WIDTH - 2) + "┘"))
    return lines


BIG_CELL = 7                                  # width of one room box
BIG_STEP = BIG_CELL + 2                       # room box plus the corridor to its east
BIG_INNER = GRID_W * BIG_STEP
BIG_WIDTH = BIG_INNER + 4


def center(text, width):
    space = max(0, width - vis_len(text))
    return " " * (space // 2) + text + " " * (space - space // 2)


def big_map_lines(game):
    """The current floor with each room drawn as a box, one string per line."""
    title = f" Floor {game.depth} "
    lines = [dim("┌" + title + "─" * (BIG_WIDTH - 2 - len(title)) + "┐")]
    blank = " " * BIG_STEP
    shown = [y for y in range(GRID_H)
             if any(game.rooms.get(key(x, y), {}).get("seen") for x in range(GRID_W))]
    for y in range(shown[0], shown[-1] + 1):  # skip rows you haven't discovered yet
        top = mid = bot = below = ""
        for x in range(GRID_W):
            r = game.rooms.get(key(x, y))
            if not r or not r["seen"]:
                top, mid, bot, below = top + blank, mid + blank, bot + blank, below + blank
                continue
            ch, col = room_symbol(game, x, y)
            if [x, y] == game.pos:
                border = lambda t: yellow(bold(t))
                inside = yellow(bold("@")) + (f" {col(ch)}" if col and ch not in ("!", "?") else "")
            else:
                border = col or (lambda t: t)
                inside = col(ch) if col else ch
            east = game.rooms.get(key(x + 1, y))
            south = game.rooms.get(key(x, y + 1))
            door_e = "e" in r["exits"] and (r["visited"] or east["visited"])
            door_s = "s" in r["exits"] and (r["visited"] or south["visited"])
            top += border("┌" + "─" * (BIG_CELL - 2) + "┐") + "  "
            mid += border("│") + center(inside, BIG_CELL - 2) + border("│") + ("──" if door_e else "  ")
            bot += border("└" + "─" * (BIG_CELL - 2) + "┘") + "  "
            below += center("│" if door_s else " ", BIG_CELL) + "  "
        for row in (top, mid, bot):
            lines.append(dim("│ ") + pad(row, BIG_INNER) + dim(" │"))
        if y < shown[-1]:
            lines.append(dim("│ ") + pad(below, BIG_INNER) + dim(" │"))
    lines.append(dim("└" + "─" * (BIG_WIDTH - 2) + "┘"))
    return lines


def floor_map(game, cols=None):
    """(lines, width) of the map: big room boxes if chosen ('map big') and the terminal is
    wide enough, else compact."""
    cols = cols or shutil.get_terminal_size((100, 40)).columns
    if getattr(game, "big_map", True) and cols >= BIG_WIDTH + 4:
        return big_map_lines(game), BIG_WIDTH
    return map_lines(game), MAP_WIDTH


def draw_map(game):
    print()
    print(bold(f"  Floor {game.depth}: {FLOOR_NAMES[game.depth]}"))
    for line in floor_map(game)[0]:
        print("  " + line)
    for line in MAP_LEGEND:
        print("  " + line)


def wrap(text, width, color=None):
    lines = textwrap.wrap(text, max(20, width))
    return [color(l) for l in lines] if color else lines


def room_text(game, width):
    """The room description as a list of lines wrapped to `width`."""
    r = game.room
    out = [bold(f"Floor {game.depth}: {FLOOR_NAMES[game.depth]}"), ""]
    out += wrap(r["desc"], width)
    t = r["type"]
    extra = None
    if t == "stairs":
        extra = ("A spiral staircase descends into darkness. (type '/down' to descend)", cyan)
    elif t == "shrine":
        if r["cleared"] and not r.get("used"):
            extra = ("A softly glowing altar hums with old, kind magic. (type 'pray' to take a long rest)", green)
        else:
            extra = ("The altar's light has faded.", dim)
    elif t == "merchant":
        extra = ("A hooded trader named Pell sits on a threadbare rug, lantern swinging. "
                 "\"Coin for comfort, friend?\" (type '/shop')", yellow)
    elif t == "treasure" and r["cleared"]:
        extra = ("An emptied chest lies open.", dim)
    elif t == "trap" and r["cleared"]:
        extra = ("The remains of a trap litter the floor.", dim)
    if extra:
        out += wrap(extra[0], width, extra[1])
    living = alive(r["monsters"])
    if living:
        out += wrap("Enemies here: " + ", ".join(m["name"] for m in living), width, red)
    exits = ", ".join(DIR_NAMES[d] for d in "nsew" if d in r["exits"])
    out += ["", f"Exits: {cyan(exits)}"]
    return out


def describe(game):
    cols = shutil.get_terminal_size((100, 40)).columns
    print()
    if not getattr(game, "auto_map", True):
        for line in room_text(game, min(cols, 80) - 4):
            print("  " + line)
        return
    left, width = floor_map(game, cols)
    side_by_side = cols >= width + 44
    if not side_by_side:  # narrow terminal: description, then the map underneath
        for line in room_text(game, cols - 4):
            print("  " + line)
        for line in left:
            print("  " + line)
        return
    right = room_text(game, min(cols - width - 6, 60))
    height = max(len(left), len(right) + len(MAP_LEGEND) + 1)
    right += [""] * (height - len(right) - len(MAP_LEGEND)) + MAP_LEGEND
    left += [""] * (height - len(left))
    for l, r in zip(left, right):
        print(("  " + pad(l, width) + "  " + r).rstrip())


def spring_trap(game):
    hero, depth = game.hero, game.depth
    desc, stat = random.choice(TRAPS)
    dc = 11 + depth
    print()
    if hero.check("WIS", dc, "Perception (WIS)", adv=hero.race == "Elf", proficient=hero.cls in ("Rogue", "Ranger")):
        say(green(f"  You spot {desc} and carefully avoid it."))
        hero.gain_xp(25 * depth)
    else:
        say(red(f"  TRAP! You trigger {desc}!"))
        dmg = roll(f"{depth + 1}d6")
        if hero.save(stat, dc):
            dmg //= 2
        hero.take_damage(dmg)
        print(f"  You take {red(bold(dmg))} damage. {hp_bar(hero.hp, hero.max_hp)}")


def loot_treasure(game):
    hero, depth = game.hero, game.depth
    gold = roll("3d10") * depth
    if hero.cls == "Rogue":
        gold = gold * 3 // 2  # rogues know where the good stuff is hidden
    print()
    say(yellow("  You find a battered treasure chest and pry it open..."))
    hero.gold += gold
    print(f"  Inside: {yellow(gold)} gold.")
    pick = random.random()
    if pick < 0.35:
        item = "Greater Healing Potion" if depth >= 2 and random.random() < 0.4 else "Healing Potion"
    elif pick < 0.5:
        item = "Scroll of Fireball"
    elif pick < 0.65:
        item = "Smoke Bomb"
    else:
        item = None
    if item:
        hero.add_item(item)
        print(f"  You also find a {cyan(item)}!")
    if random.random() < 0.1 + 0.05 * depth:
        if hero.weapon_bonus < 3 and (hero.armor_bonus >= 3 or random.random() < 0.5):
            hero.weapon_bonus += 1
            print(magenta(bold(f"  Runes flare along your {hero.weapon()['name']} - it is now +{hero.weapon_bonus}!")))
        elif hero.armor_bonus < 3:
            hero.armor_bonus += 1
            print(magenta(bold(f"  A warding sigil sinks into your armor - it is now +{hero.armor_bonus}! (AC {hero.ac()})")))


def enter_room(game):
    """Handle arriving in a room. Returns 'dead', 'won', or None."""
    r = game.room
    first = not r["visited"]
    r["visited"] = True
    game.mark_seen()
    describe(game)
    hero = game.hero
    if first and r["type"] not in ("boss",):
        hero.xp += 10 * game.depth  # quiet exploration XP; level-ups are checked below
        if hero.level < MAX_LEVEL and hero.xp >= XP_LEVELS[hero.level]:
            hero.gain_xp(0)

    if alive(r["monsters"]):
        if r["type"] == "boss" and first:
            print()
            say(magenta("  Atop the throne, a gaunt figure in a crown of black iron lifts its head. "
                        "Pale light kindles in its empty eye sockets."))
            say(magenta("  \"Another hero. The Hollow King thanks you for your bones.\""))
        for m in alive(r["monsters"]):
            if m.get("intro") and first:
                print()
                say(magenta("  " + m["intro"]))
        result = combat(game, r["monsters"], can_flee=r["type"] != "boss")
        if result == "dead":
            return "dead"
        if result == "fled":
            game.pos = list(game.prev)
            game.mark_seen()
            describe(game)
            return None
        r["cleared"] = True
        if r["type"] == "boss":
            return "won"

    if not r["cleared"]:
        if r["type"] == "trap":
            spring_trap(game)
            if hero.hp <= 0:
                return "dead"
        elif r["type"] == "treasure":
            loot_treasure(game)
        r["cleared"] = True
    return None


def search_room(game):
    r, hero = game.room, game.hero
    if alive(r["monsters"]):
        print("  Not with enemies watching!")
        return
    if r["searched"]:
        print("  You've already searched this room thoroughly.")
        return
    r["searched"] = True
    if hero.check("INT", 12, "Investigation (INT)", proficient=hero.cls in ("Rogue", "Ranger")):
        gold = roll("1d10") * game.depth + 2
        hero.gold += gold
        found = f"{yellow(gold)} gold"
        if random.random() < 0.25:
            hero.add_item("Healing Potion")
            found += f" and a {cyan('Healing Potion')}"
        print(f"  Hidden in a crevice you find {found}!")
    else:
        print("  You find nothing but dust.")


def short_rest(game):
    hero = game.hero
    if alive(game.room["monsters"]):
        print("  You can't rest with enemies nearby!")
        return None
    if game.short_rests >= 3:
        print("  You're too on edge to rest again on this floor. Find a shrine, or press on.")
        return None
    game.short_rests += 1
    say(dim("  You sit with your back to the wall and rest for an hour..."))
    if random.random() < 0.2:
        say(red("  Footsteps! Something found you while you rested!"))
        result = combat(game, random_encounter(game.depth))
        if result == "dead":
            return "dead"
        if result == "fled":
            say("  You escape, but your rest is ruined.")
        return None
    hd = CLASSES[hero.cls]["hit_die"]
    healed = 0
    while hero.hit_dice > 0 and hero.hp < hero.max_hp:
        hero.hit_dice -= 1
        healed += hero.heal(max(1, roll(f"1d{hd}") + hero.m("CON")))
    msg = f"  You recover {green(healed)} HP." if healed else "  You feel rested."
    if hero.hp < hero.max_hp and hero.hit_dice == 0:
        msg += dim(" (You're out of Hit Dice - only a long rest restores them.)")
    print(msg)
    hero.feature_ready = True
    if hero.race != "Half-Orc":  # Relentless Endurance recharges only between floors
        hero.race_ready = True
    if hero.cls == "Wizard":
        hero.ward = hero.max_ward()
        print(cyan(f"  Your Arcane Ward shimmers back to full strength ({hero.ward})."))
    if hero.cls == "Wizard" and hero.slots < hero.max_slots():
        hero.slots += 1
        print(cyan("  Arcane Recovery: you regain a spell slot."))
    print(f"  {hp_bar(hero.hp, hero.max_hp)}   Short rests left this floor: {3 - game.short_rests}")
    return None


def long_rest(game):
    hero, r = game.hero, game.room
    if r["type"] != "shrine":
        print("  There's no shrine here.")
        return
    if r.get("used"):
        print("  The altar is dark now. Its blessing is spent.")
        return
    r["used"] = True
    hero.hp = hero.max_hp
    hero.slots = hero.max_slots()
    hero.ward = hero.max_ward()
    hero.hit_dice = hero.level
    hero.lay_pool = hero.max_lay()
    hero.feature_ready = True
    if hero.race != "Half-Orc":  # Relentless Endurance recharges only between floors
        hero.race_ready = True
    game.short_rests = 0
    say(green("  You kneel before the altar. Warmth washes over you, and you sleep "
              "deeply and safely for the first time in days."))
    print(green(f"  Fully restored! {hp_bar(hero.hp, hero.max_hp)}"))


def shop(game):
    hero = game.hero
    if game.room["type"] != "merchant":
        print("  There's nobody here to trade with.")
        return
    while True:
        stock = [(n, ITEMS[n]["price"], ITEMS[n]["desc"]) for n in ITEMS]
        if hero.weapon_bonus < 3:
            stock.append(("weapon", [200, 500, 1000][hero.weapon_bonus],
                          f"Enchant your {hero.weapon()['name']} to +{hero.weapon_bonus + 1}."))
        if hero.armor_bonus < 3:
            stock.append(("armor", [300, 700, 1200][hero.armor_bonus],
                          f"Enchant your armor to +{hero.armor_bonus + 1} (+1 AC)."))
        print(bold(f"\n  Pell's Wares") + f"   (you have {yellow(hero.gold)} gold)")
        labels = []
        for n, price, desc in stock:
            label = {"weapon": "Weapon Enchantment", "armor": "Armor Enchantment"}.get(n, n)
            labels.append(f"{label:<24} {yellow(f'{price:>4}g')}  {desc}")
        i = choose("  Buy> ", labels + ["Leave"])
        if i == len(stock):
            print('  "Safe travels. Try not to die - bad for business."')
            return
        n, price, _ = stock[i]
        if hero.gold < price:
            print(red("  \"You're a little short, friend.\""))
            continue
        hero.gold -= price
        if n == "weapon":
            hero.weapon_bonus += 1
            print(magenta(f"  Pell etches glowing runes into your weapon. It is now +{hero.weapon_bonus}."))
        elif n == "armor":
            hero.armor_bonus += 1
            print(magenta(f"  Pell stitches a warding sigil into your armor. AC is now {hero.ac()}."))
        else:
            hero.add_item(n)
            print(f"  You buy a {cyan(n)}.")


def descend(game):
    if game.room["type"] != "stairs":
        print("  There are no stairs here.")
        return
    if alive(game.room["monsters"]):
        print("  Something stands between you and the stairs!")
        return
    game.depth += 1
    game.rooms, game.pos = generate_floor(game.depth)
    game.prev = list(game.pos)
    game.short_rests = 0
    game.mark_seen()
    hero = game.hero
    hero.hit_dice = hero.level
    hero.ward = hero.max_ward()
    hero.lay_pool = max(hero.lay_pool, hero.max_lay() // 2)
    hero.heal((hero.max_hp - hero.hp + 1) // 2)
    hero.feature_ready = True
    hero.race_ready = True
    print()
    say(dim("  You catch your breath on the quiet stair landing. (Hit Dice restored, half your missing HP recovered.)"))
    print(bold(cyan(f"  You descend the winding stairs to Floor {game.depth}: {FLOOR_NAMES[game.depth]}...")))
    if game.depth == FINAL_FLOOR:
        say(magenta("  A cold wind rises from below, carrying a whisper: \"Come, little hero. Come to the throne.\""))
    save_game(game)
    describe(game)


HELP = f"""
  {bold('Exploring')}
    arrows or W A S D  move instantly (no Enter needed)
    n / e / go north   typed directions
    /command           start with / to type a command beginning with w, a, s or d
                       (/search, /stats, /save, /shop, /down, /drink); / works on any command
    look   (l)         describe the room again
    map    (m)         show the full map with a legend
    map on / map off   show or hide the map beside each room (on by default)
    map big / small    big room boxes (default) or the compact [@] map
    /search            search the room for hidden loot (once per room)
    /stats (c)         character sheet
    inv    (i)         show your pack
    use / /drink       use a potion
    rest               short rest: spend Hit Dice, recharge features (3 per floor, risky)
    pray               long rest at a shrine: full recovery (once per shrine)
    lay                Paladins: Lay on Hands to heal outside combat
    /shop              trade with the merchant
    /down              take the stairs to the next floor
    /save              save your game
    quit   (q)         save and quit

  {bold('Combat')}  choose an action each turn:
    [a]ttack  [c]ast  [i]tem  [r]un, plus class actions such as [s]econd wind,
    [t]urn undead, [b]erserker rage, [x] reckless attack, [l]ay on hands
    and [d]ragon breath (Dragonborn).
    Potions, Second Wind, Rage and Hunter's Mark are bonus actions: you still get to act.
    Guardians block the stairs on floors 2 and 4.

  Attacks roll d20 + bonus against the target's Armor Class (AC). Natural 20s are
  critical hits (double dice). Defeat Morgrath, the Hollow King, on Floor {FINAL_FLOOR} to win.
  Death is permanent - your save is erased.
"""


def status_line(game):
    h = game.hero
    parts = [f"{bold(h.name)} L{h.level}", f"HP {hp_bar(h.hp, h.max_hp, 10)}"]
    if h.caster():
        parts.append(f"Slots {h.slots}/{h.max_slots()}")
    parts += [f"Gold {yellow(h.gold)}", f"Floor {game.depth}"]
    return "  " + " | ".join(parts)


def play(game):
    describe(game)
    while True:
        print()
        print(status_line(game))
        text, arrow = read_command("> ")
        words = text.split()
        if not words:
            continue
        verb, rest = words[0], words[1:]
        if verb == "go" and rest:
            verb, rest = rest[0], rest[1:]
        result = None

        if verb in DIR_ALIASES:
            d = DIR_ALIASES[verb]
            if d not in game.room["exits"]:
                print("  You can't go that way.")
                continue
            if arrow:
                clear_screen()  # redraw in place so arrow-key movement feels live
            dx, dy = DIRS[d]
            game.prev = list(game.pos)
            game.pos = [game.pos[0] + dx, game.pos[1] + dy]
            print(dim(f"  You head {DIR_NAMES[d]}..."))
            result = enter_room(game)
        elif verb in ("look", "l"):
            describe(game)
        elif verb in ("map", "m"):
            if rest and rest[0] in ("on", "off"):
                game.auto_map = rest[0] == "on"
                print(f"  Automatic map {'on' if game.auto_map else 'off'}.")
            elif rest and rest[0] in ("big", "small"):
                game.big_map = rest[0] == "big"
                cols = shutil.get_terminal_size((100, 40)).columns
                if game.big_map and cols < BIG_WIDTH + 4:
                    print(f"  Big map on - but your terminal is only {cols} columns wide, so the small map "
                          f"is shown until it's at least {BIG_WIDTH + 4}.")
                else:
                    print(f"  Map size: {rest[0]}.")
                describe(game)
            else:
                draw_map(game)
        elif verb == "search":
            search_room(game)
        elif verb in ("stats", "c", "sheet", "char"):
            show_sheet(game.hero)
        elif verb in ("inv", "i", "inventory", "pack"):
            inv = ", ".join(f"{n} x{q}" for n, q in game.hero.inventory.items()) or "nothing"
            print(f"  Pack: {inv}   Gold: {yellow(game.hero.gold)}")
        elif verb in ("use", "drink", "quaff"):
            hero = game.hero
            arg = " ".join(rest)
            matches = [n for n in hero.inventory if arg and arg in n.lower()]
            name = matches[0] if matches else item_menu(hero)
            if name:
                use_item(hero, name)
        elif verb == "rest":
            result = short_rest(game)
        elif verb in ("lay", "layonhands"):
            lay_on_hands(game.hero)
        elif verb == "pray":
            long_rest(game)
        elif verb in ("shop", "buy", "trade"):
            shop(game)
        elif verb in ("down", "descend", "stairs"):
            descend(game)
        elif verb == "save":
            save_game(game)
        elif verb in ("help", "h", "?"):
            print(HELP)
        elif verb in ("quit", "q", "exit"):
            save_game(game)
            print("  Until next time, adventurer.")
            return
        else:
            print("  Unknown command. Type 'help' for a list.")

        if result == "dead":
            game_over(game)
            return
        if result == "won":
            victory(game)
            return


def game_over(game):
    h = game.hero
    print()
    print(red(bold("  ☠  YOU HAVE DIED  ☠")))
    say(f"  {h.name} the {h.race} {h.cls} fell on Floor {game.depth} at level {h.level}, "
        f"having slain {h.kills} foes.")
    say(dim("  Their bones join the countless others beneath the ruins of Ashenmoor."))
    delete_save()
    pause()


def victory(game):
    h = game.hero
    print()
    print(yellow(bold("  ✦✦✦  VICTORY  ✦✦✦")))
    say("  The Hollow King's crown cracks in two. With a sound like a thousand sighs, the "
        "restless dead of Ashenmoor finally fall still.")
    say(f"  {h.name} the {h.race} {h.cls} climbs back into the daylight - level {h.level}, "
        f"{h.kills} foes slain, {h.gold} gold richer. The bards will sing of this.")
    delete_save()
    pause()


TITLE = r"""
   ____                                      ____       _
  |  _ \ _   _ _ __   __ _  ___  ___  _ __  |  _ \  ___| |_   _____
  | | | | | | | '_ \ / _` |/ _ \/ _ \| '_ \ | | | |/ _ \ \ \ / / _ \
  | |_| | |_| | | | | (_| |  __/ (_) | | | || |_| |  __/ |\ V /  __/
  |____/ \__,_|_| |_|\__, |\___|\___/|_| |_||____/ \___|_| \_/ \___|
                     |___/
"""

INTRO = ("Beneath the ruined keep of Ashenmoor, something old has woken. The dead walk the "
         "crypts, and the villagers whisper of a crowned skeleton on a throne of bone - "
         "Morgrath, the Hollow King. You have taken the village's last coins and their last "
         "hope. Now you stand at the top of the crypt stairs, torch in hand.")


def main():
    global USE_COLOR, COMBAT_DELAY
    args = sys.argv[1:]
    if "--no-color" in args:
        USE_COLOR = False
    if "--fast" in args:
        COMBAT_DELAY = 0
    if "--seed" in args:
        random.seed(int(args[args.index("--seed") + 1]))

    while True:
        print(red(bold(TITLE)))
        print(dim("            a d20 dungeon crawler for the command line\n"))
        has_save = os.path.exists(SAVE_PATH)
        options = ["New adventure"] + (["Continue saved game"] if has_save else []) + ["How to play", "Quit"]
        pick = options[choose("> ", options)]
        if pick == "New adventure":
            if has_save and not confirm("  This will overwrite your saved game. Continue?"):
                continue
            hero = create_hero()
            game = Game(hero)
            print()
            say(INTRO)
            pause()
            play(game)
        elif pick == "Continue saved game":
            try:
                with open(SAVE_PATH) as f:
                    game = Game.from_dict(json.load(f))
            except (OSError, ValueError, KeyError) as e:
                print(red(f"  Couldn't load save: {e}"))
                continue
            print(green(f"  Welcome back, {game.hero.name}."))
            play(game)
        elif pick == "How to play":
            print(HELP)
            pause()
        else:
            print("Farewell, adventurer.")
            return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if getattr(sys, "frozen", False):  # Windows .exe: keep the window open so the error can be read
            import traceback
            traceback.print_exc()
            input("\nDungeon Delve crashed. Press Enter to close...")
        raise
