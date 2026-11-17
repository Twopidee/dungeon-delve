# Dungeon Delve

A d20 fantasy dungeon crawler for the terminal. Build a hero, fight your way down five
floors of the crypts beneath Ashenmoor, and defeat Morgrath, the Hollow King.

```
   ____                                      ____       _
  |  _ \ _   _ _ __   __ _  ___  ___  _ __  |  _ \  ___| |_   _____
  | | | | | | | '_ \ / _` |/ _ \/ _ \| '_ \ | | | |/ _ \ \ \ / / _ \
  | |_| | |_| | | | | (_| |  __/ (_) | | | || |_| |  __/ |\ V /  __/
  |____/ \__,_|_| |_|\__, |\___|\___/|_| |_||____/ \___|_| \_/ \___|
                     |___/
```

- 8 races and 7 classes, with ability scores rolled 4d6-drop-lowest
- Turn-based d20 combat with spells, class features, crits, saving throws and boss fights
- Five randomly generated floors with traps, treasure, shrines, a merchant and stair guardians
- A live map, arrow-key / WASD movement and arrow-key menus
- Permadeath: when you die, your save is erased

## Requirements

- **Python 3.8 or newer**. That's all: the game uses only the standard library, so there is
  nothing to `pip install`.
- A terminal at least **80 columns** wide. **111+ columns** shows the big map beside the room
  description; 71–110 columns puts it underneath.

The whole game is a single file, `dungeon_delve.py`. Installing it means putting that file
somewhere and running it with Python.

---

## Linux

### 1. Install Python

Most distributions already ship Python 3. Check with:

```bash
python3 --version
```

If it's missing, install it with your package manager:

| Distribution            | Command                          |
|-------------------------|----------------------------------|
| Debian / Ubuntu / Mint  | `sudo apt install python3`       |
| Fedora                  | `sudo dnf install python3`       |
| Arch / Manjaro / CachyOS| `sudo pacman -S python`          |
| openSUSE                | `sudo zypper install python3`    |

### 2. Get the game

Copy the `dungeon-delve` folder anywhere you like, for example `~/dungeon-delve`.

### 3. Play

```bash
cd ~/dungeon-delve
python3 dungeon_delve.py
```

Or make it executable and run it directly:

```bash
chmod +x dungeon_delve.py launch.sh
./dungeon_delve.py
```

### Optional: add it to your app menu

`launch.sh` opens the game in its own 100×40 terminal window. It uses Konsole, then Alacritty,
then xterm, whichever it finds first. To get a menu entry with an icon:

1. Open `dungeon-delve.desktop` and change the `Exec=` and `Icon=` lines to point at your copy
   of the folder:

   ```ini
   Exec=/home/YOUR_NAME/dungeon-delve/launch.sh
   Icon=/home/YOUR_NAME/dungeon-delve/icon.svg
   ```

2. Install it:

   ```bash
   cp dungeon-delve.desktop ~/.local/share/applications/
   ```

   For a desktop icon too, copy it to `~/Desktop/` as well. Some desktops ask you to
   right-click it and choose "Allow Launching".

If you use a different terminal (GNOME Terminal, Kitty, WezTerm…), edit `launch.sh` and add it,
e.g. `exec gnome-terminal --title "Dungeon Delve" -- bash -c "$RUN"`.

---

## macOS

### 1. Install Python

macOS doesn't come with a usable Python 3 out of the box. Pick one way to get it:

- **python.org installer (easiest):** download the latest macOS installer from
  <https://www.python.org/downloads/macos/> and run it.
- **Homebrew:** `brew install python`
- **Xcode Command Line Tools:** run `xcode-select --install`. This includes `python3`.

Check it worked:

```bash
python3 --version
```

### 2. Get the game

Copy the `dungeon-delve` folder somewhere, for example your home folder.

### 3. Play

Open **Terminal** (or iTerm2) and run:

```bash
cd ~/dungeon-delve
python3 dungeon_delve.py
```

Arrow keys, WASD and arrow-key menus all work on macOS. For the big side-by-side map, make
the Terminal window wider than the default: drag the corner, or go to
**Terminal → Settings → Profiles → Window** and set the columns to 120.

### Optional: double-click to play

Create a file called `Dungeon Delve.command` in the game folder containing:

```bash
#!/bin/bash
cd "$(dirname "$0")"
python3 dungeon_delve.py
```

Make it executable with `chmod +x "Dungeon Delve.command"`. Double-clicking it opens Terminal
and starts the game. The first time, macOS may block it: right-click it and choose **Open**.

---

## Windows

### Easiest: download the .exe

1. Go to the **[latest release](https://github.com/Twopidee/dungeon-delve/releases/latest)**
   and download `Dungeon-Delve.exe`.
2. Double-click it. You don't need to install Python.

Windows may show **"Windows protected your PC"** the first time, because the exe isn't
code-signed. Click **More info → Run anyway**.

The exe is built automatically from this repository's source by GitHub Actions (see
`.github/workflows/build-windows.yml`). The [Windows limitations](#windows-limitations) below
apply to the exe too.

### Or run it with Python

#### 1. Install Python

- Install Python from the **Microsoft Store** (search "Python 3"), **or**
- Download the installer from <https://www.python.org/downloads/windows/>. On the first
  screen, tick **"Add python.exe to PATH"**.

Check it worked in PowerShell or Command Prompt:

```powershell
python --version
```

(If `python` isn't found, try `py --version`. The python.org installer also adds the `py`
launcher.)

#### 2. Get the game

Copy the `dungeon-delve` folder somewhere, for example `C:\Users\YOU\dungeon-delve`.

#### 3. Play

Use **Windows Terminal**, which is built into Windows 11 and free from the Microsoft Store on
Windows 10. It shows the colors and the box-drawing map correctly:

```powershell
cd $HOME\dungeon-delve
python dungeon_delve.py
```

If you see junk like `←[31m` instead of colors (the old Command Prompt window can do this),
switch to Windows Terminal, or run without colors:

```powershell
python dungeon_delve.py --no-color
```

### Windows limitations

The instant controls rely on a Unix terminal feature that Windows doesn't have. On Windows
the game automatically uses typed controls instead:

- Move by typing a direction and pressing Enter: `n`, `s`, `e`, `w` (or `go north`).
  Arrow keys and WASD don't move you instantly.
- Pick menu options by typing the number or letter and pressing Enter.
- Type `y` or `n` for yes/no questions.

Everything else plays the same. For the full instant controls on Windows, run the game inside
**WSL** (Windows Subsystem for Linux) and follow the Linux instructions.

### Optional: double-click to play

Create `Dungeon Delve.bat` in the game folder containing:

```bat
@echo off
cd /d "%~dp0"
python dungeon_delve.py
pause
```

Double-click it to play. To open it in Windows Terminal instead, set Windows Terminal as your
default terminal app in its settings.

---

## Building the Windows .exe yourself

On a Windows PC with Python installed:

```powershell
python -m pip install pyinstaller
pyinstaller --onefile --console --name Dungeon-Delve --icon icon.ico dungeon_delve.py
```

The exe appears in `dist\Dungeon-Delve.exe`. PyInstaller can't build Windows programs from
Linux or macOS. To make a new release, push a version tag (`git tag v1.1.0 && git push origin
v1.1.0`) and GitHub Actions builds the exe and attaches it to the release.

## Command-line options

```
python3 dungeon_delve.py              play
python3 dungeon_delve.py --seed 42    the same dungeon every time (handy for testing)
python3 dungeon_delve.py --no-color   plain text, no colors
python3 dungeon_delve.py --fast       skip the pauses between combat rolls
```

Setting the `NO_COLOR` environment variable also turns colors off.

## How to play

Type `help` in game for the full command list.

### Exploring

| Keys / command        | What it does                                                  |
|-----------------------|---------------------------------------------------------------|
| Arrow keys or W A S D | Move instantly (no Enter needed)                              |
| `n`, `e`, `go west`…  | Typed directions                                              |
| `look` / `l`          | Describe the room again                                       |
| `map` / `m`           | Show the full map with a legend                               |
| `map on` / `map off`  | Show or hide the map beside each room                         |
| `map big` / `map small` | Big room boxes (default) or the compact `[@]` map           |
| `/search`             | Search the room for hidden loot (once per room)               |
| `/stats` / `c`        | Character sheet                                               |
| `inv` / `i`           | Show your pack                                                |
| `use` / `/drink`      | Drink a potion                                                |
| `rest`                | Short rest: spend Hit Dice, recharge features (3 per floor)   |
| `pray`                | Long rest at a shrine: full recovery (once per shrine)        |
| `lay`                 | Paladins: Lay on Hands outside combat                         |
| `/shop`               | Trade with Pell the merchant                                  |
| `/down`               | Take the stairs to the next floor                             |
| `/save`               | Save your game                                                |
| `quit` / `q`          | Save and quit                                                 |

**Why the slash?** W, A, S and D move you instantly, so a command that starts with one of
those letters needs a `/` in front (`/search`, `/shop`, `/down`…). A slash works in front of
any command. On Windows, where WASD doesn't move you instantly, you can type these commands
with or without the slash.

### Combat and menus

- **Action bar** (`[a]ttack  [c]ast  [i]tem  [r]un` …): use ← → to highlight an action and
  Enter to choose it, or type its letter and press Enter.
- **Lists** (targets, spells, items, the shop, character creation): use ↑ ↓ and Enter, or
  type the number.
- **Yes/No questions** (Divine Smite, Hellish Rebuke…): use ← → and Enter, or type `y` / `n`.

Potions, Second Wind, Rage and Hunter's Mark are bonus actions, so you still get to act that
turn. Guardians block the stairs on floors 2 and 4.

### Map legend

```
@ you   ? unexplored   ! enemies   > stairs   + shrine   $ shop   B boss
```

## Saving

The game saves automatically when you take the stairs and when you quit. You can also save
any time with `/save`. There is one save slot, stored in your home folder:

| OS      | Save file                                      |
|---------|------------------------------------------------|
| Linux   | `~/.dungeon_delve_save.json`                   |
| macOS   | `~/.dungeon_delve_save.json`                   |
| Windows | `C:\Users\YOU\.dungeon_delve_save.json`        |

Death is permanent: if your hero dies, the save is deleted.

## Uninstalling

Delete the `dungeon-delve` folder and the save file above. On Linux, also remove
`~/.local/share/applications/dungeon-delve.desktop` (and the copy on your Desktop) if you
installed the launcher.

## Files

| File                    | Purpose                                         |
|-------------------------|-------------------------------------------------|
| `dungeon_delve.py`      | The game: everything is in this one file        |
| `launch.sh`             | Linux: opens the game in its own terminal window|
| `dungeon-delve.desktop` | Linux: app-menu / desktop launcher entry        |
| `icon.svg`              | Launcher icon                                   |
| `icon.ico`              | Windows .exe icon                               |
| `.github/workflows/build-windows.yml` | Builds `Dungeon-Delve.exe` on GitHub for each release |
