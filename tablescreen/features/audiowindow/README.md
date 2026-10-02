# Audiowindow

The audiowindow feature is the original core of the project: it shows images on a window and plays music. It is the simplest feature and the one most sessions start with, so its commands have no prefix.

## Images

`show <file>` shows an image from `assets/images` on the window this feature is configured to use (`mainscreen` by default). The image is scaled to fit the window with its proportions kept, so black bars can appear at the sides or at the top and bottom. Any format Pillow can read is supported. Subfolders work, and tab completion helps with the file names.

The combat screen uses the same window by default. Whichever was shown last is visible: `show` takes the window over from the combat screen, and `combat show` gives it back.

## Music

`play <file>` plays a music file or sound effect from `assets/audio`. MP3, WAV and OGG are supported. Starting a new file replaces the one that is playing.

`volume` prints the current volume; `volume <0-100>` sets it. The audio system starts with the first `play`, so the volume can only be read or set after something has been played. Once set, it is kept for the rest of the session, also when a new file is started. `stop` stops playback.

Audio is played through pygame-ce and does not depend on the windows; it keeps playing when a window is hidden or minimised.

## Window controls

`fullscreen` makes the window borderless and fills the monitor it is on. `minimize` minimises it. `restore` returns it to its default size from the configuration, and also brings it back after it has been closed with its close button; its contents are kept while it is closed.

## Commands

| Command                     | Description                                          |
| --------------------------- | ---------------------------------------------------- |
| `show <file>`               | Show an image from `assets/images`                   |
| `play <file>`               | Play a file from `assets/audio`                      |
| `stop`                      | Stop playback                                        |
| `volume` / `volume <0-100>` | Print or set the volume                              |
| `fullscreen`                | Borderless fullscreen on the current monitor         |
| `minimize`                  | Minimise the window                                  |
| `restore`                   | Default size; reopens the window after it was closed |

## Files

| File          | Contents                                                  |
| ------------- | --------------------------------------------------------- |
| `__init__.py` | The feature: commands, window controls and tab completion |
| `view.py`     | Draws an image, scaled to fit, into its frame             |
| `audio.py`    | Music playback and volume                                 |
