# Tablescreen

Tablescreen is a command-line driven toolkit for running tabletop roleplaying sessions. The game master types commands into a shell; the results appear on screens the players can see. Typical uses are showing an image or playing music to set the scene, tracking initiative and hit points on a combat screen, and displaying a battlemap with a calibrated grid on a TV lying flat under a sheet of glass, with physical miniatures placed on top.

The project was previously named AudioWindowCMD. It was renamed once image and audio playback became one feature among several.

## Features

The application is split into independent features. Each one can be switched on or off in `config.toml`, and a disabled feature is never loaded at all. A session for a system other than D&D 5e, for example, can run without the combat tracker.

| Feature     | Purpose                                                                                                                               | Documentation                                                      |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| audiowindow | Shows images on a window and plays music                                                                                              | [features/audiowindow](tablescreen/features/audiowindow/README.md) |
| combat      | D&D 5e initiative and hit point tracker with a player-facing screen                                                                   | [features/combat](tablescreen/features/combat/README.md)           |
| battlemap   | Battlemap backdrop (image or looping video) with a grid calibrated in physical inches, coordinate labels, sprites and areas of effect | [features/battlemap](tablescreen/features/battlemap/README.md)     |

How the features fit together, and how to write a new one, is described in [the core documentation](tablescreen/core/README.md).

## Requirements

- Python 3.14. The code uses features from Python 3.11 onwards, but development and testing are done on 3.14.
- Windows or Linux. macOS has not been tested and is not taken into account.
- For the battlemap: a display whose physical size is known, so the grid can be drawn in real inches.

The Python dependencies are listed in `requirements.txt`:

| Package       | Used for                                                                                                                               |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `pillow`      | Loading, scaling and rotating images                                                                                                   |
| `pygame-ce`   | Music playback. This is the community edition of pygame; the import name is still `pygame`, but `pygame-ce` is the package to install. |
| `screeninfo`  | Detecting monitors, for fullscreen on the right screen and for grid calibration                                                        |
| `av` (PyAV)   | Decoding animated `.webm` battlemap backdrops. FFmpeg is bundled with the package; nothing else needs to be installed.                 |
| `pyreadline3` | Tab completion in the shell. Windows only; installed automatically there and skipped elsewhere.                                        |

Tkinter, used for all windows, is part of the standard Python installation on Windows. On some Linux distributions it is a separate package (for example `python3-tk`).

## Installation

Clone the repository and create a virtual environment inside it.

On Windows (PowerShell):

```powershell
git clone https://github.com/stijndns/tablescreen.git
cd tablescreen
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell refuses to run `Activate.ps1`, script execution has to be allowed for the current user once with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

On Linux:

```bash
git clone https://github.com/stijndns/tablescreen.git
cd tablescreen
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Running

From the repository root, with the virtual environment active:

```
python -m tablescreen
```

The shell starts in the terminal and one window opens per screen defined in the configuration. Type `help` for a list of commands grouped by feature, `help <command>` for details on one command, and `exit` to close the application.

## Configuration

`config.toml` in the repository root controls which features are loaded and how they behave.

```toml
[features]
enabled = ["audiowindow", "combat", "battlemap"]

[features.audiowindow]
window = "mainscreen"

[features.combat]
window = "mainscreen"

[features.battlemap]
window = "battlemap"
screen_diagonal_in = 50     # table TV, in inches
cell_size_in = 1.0
# pixels_per_inch = 88.1    # override if the measured grid is off
grid_color = "#ffffff"
grid_width = 1
coords_style = "letters"    # letters (E8) | numbers (5,8)
coords_location = "sides"   # sides | cells
# coords_color = "#ffffff"  # defaults to grid_color

[windows]
mainscreen = "800x600"
battlemap = "1200x900"
```

- `enabled` lists the features to load. Anything not listed is not imported.
- Each feature names the window it draws on. Features that share a window take turns: whichever showed something last is visible. That is how the combat screen replaces the image on the main screen, and how an image takes its place again afterwards.
- `[windows]` sets the default size of each window, in pixels. The `restore` commands return a window to this size.
- The battlemap settings are explained in the [battlemap documentation](tablescreen/features/battlemap/README.md#calibration).

Invalid values are reported when the application starts and replaced by their defaults; a typo never stops a feature from loading.

On Windows, the application runs in per-monitor DPI-aware mode. Window sizes in the configuration are therefore physical pixels: on a display scaled to 150 %, a `1200x900` window looks smaller than it would in an application that Windows scales up.

## Files and folders

| Path                        | Contents                                                                                          |
| --------------------------- | ------------------------------------------------------------------------------------------------- |
| `assets/images/`            | Images and animated `.webm` backdrops for `show`, `map show` and sprites. Subfolders are allowed. |
| `assets/images/combatants/` | Portraits for the combat screen                                                                   |
| `assets/audio/`             | Music and sound effects for `play` (`.mp3`, `.wav`, `.ogg`)                                       |
| `combatants/`               | Saved combat rosters (`combat export` / `combat import`)                                          |
| `logs/`                     | Saved combat logs                                                                                 |
| `config.toml`               | Configuration                                                                                     |
| `tablescreen/core/`         | The application framework: shell, windows, message bus                                            |
| `tablescreen/features/`     | One package per feature                                                                           |

All file names given to commands are relative to the matching folder, so `show tavern.jpg` shows `assets/images/tavern.jpg`. Tab completion works for file names, including subfolders.

`.webm` files and the contents of `logs/` are excluded from version control.
