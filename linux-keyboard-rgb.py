#!/usr/bin/env python3
"""GTK4/libadwaita GUI that sets the ASUS TUF keyboard RGB colour, mode, speed and brightness via asus-wmi sysfs."""

import colorsys
import json
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

APP_ID = "io.github.fayazsadiq1997_sys.LinuxKeyboardRgb"
ICON_NAME = "linux-keyboard-rgb"  # installed into the hicolor theme by install.sh
LED_DIR = Path("/sys/class/leds/asus::kbd_backlight")
MODE_FILE = LED_DIR / "kbd_rgb_mode"
BRIGHTNESS_FILE = LED_DIR / "brightness"
MAX_BRIGHTNESS_FILE = LED_DIR / "max_brightness"
SETTINGS_FILE = Path(GLib.get_user_config_dir()) / "linux-keyboard-rgb" / "settings.json"

MODE_STATIC = 0
MODE_CYCLE = 2
# 10 is the driver's catch-all mode (it maps 9 and >=12 to it); on TUF keyboards it strobes.
MODE_STROBE = 10
# (label, asus-wmi mode number), in dropdown order.
MODES = [("Static", MODE_STATIC), ("Breathing", 1), ("Colour cycle", MODE_CYCLE), ("Strobe", MODE_STROBE)]
MODE_NUMBERS = [number for _, number in MODES]
# List index equals the asus-wmi speed number.
SPEEDS = ["Slow", "Medium", "Fast"]

CMD_PREVIEW = 0  # applied until reboot, firmware untouched
CMD_SAVE = 1  # also persisted in keyboard firmware

PREVIEW_INTERVAL_MS = 100
FIRMWARE_SAVE_DELAY_MS = 5000
SV_AREA_HEIGHT = 180
DEFAULTS = {"red": 0, "green": 0, "blue": 139, "mode": MODE_STATIC, "speed": 0}

PRESETS = [
    ("Red", (255, 0, 0)),
    ("Orange", (255, 96, 0)),
    ("Yellow", (255, 200, 0)),
    ("Green", (0, 255, 0)),
    ("Cyan", (0, 255, 255)),
    ("Blue", (0, 0, 255)),
    ("Dark blue", (0, 0, 139)),
    ("Purple", (128, 0, 255)),
    ("Magenta", (255, 0, 255)),
    ("White", (255, 255, 255)),
]

STATIC_CSS = """
.hue-bar trough { min-width: 14px; border-radius: 7px;
  background-image: linear-gradient(to bottom, #f00, #ff0, #0f0, #0ff, #00f, #f0f, #f00); }
.hue-bar highlight { background: none; }
.swatch { min-width: 26px; min-height: 26px; padding: 0; border-radius: 50%;
  box-shadow: inset 0 0 0 1px alpha(black, 0.2); }
.current-swatch { min-width: 32px; min-height: 32px; border-radius: 8px;
  box-shadow: inset 0 0 0 1px alpha(black, 0.2); }
""" + "".join(
    f".swatch-{i} {{ background: #{r:02x}{g:02x}{b:02x}; }}\n" for i, (_, (r, g, b)) in enumerate(PRESETS)
)


def load_settings():
    """Return the last-used settings, falling back to DEFAULTS per key.

    kbd_rgb_mode is write-only, so this file is the only record of the current
    colour. Missing, unreadable or invalid values are replaced by defaults and
    reported on stderr rather than raised.
    """
    settings = dict(DEFAULTS)
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except FileNotFoundError:
        return settings
    except (OSError, ValueError) as err:
        print(f"linux-keyboard-rgb: ignoring unreadable settings file: {err}", file=sys.stderr)
        return settings

    if not isinstance(stored, dict):
        return settings
    limits = {"red": 255, "green": 255, "blue": 255, "speed": len(SPEEDS) - 1}
    for key, upper in limits.items():
        value = stored.get(key)
        if isinstance(value, int) and 0 <= value <= upper:
            settings[key] = value
    if stored.get("mode") in MODE_NUMBERS:
        settings["mode"] = stored["mode"]
    return settings


def save_settings(settings):
    """Atomically write settings to SETTINGS_FILE; errors are logged, not raised."""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2))
        os.replace(tmp, SETTINGS_FILE)
    except OSError as err:
        print(f"linux-keyboard-rgb: could not save settings: {err}", file=sys.stderr)


def write_rgb_mode(settings, cmd):
    """Write 'cmd mode r g b speed' to kbd_rgb_mode. Raises OSError on failure."""
    line = f"{cmd} {settings['mode']} {settings['red']} {settings['green']} {settings['blue']} {settings['speed']}\n"
    MODE_FILE.write_text(line)


def read_int(path, fallback):
    """Read an integer from a sysfs file, returning fallback if unreadable."""
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return fallback


def describe_error(err):
    """Turn a sysfs write error into a user-facing hint."""
    if isinstance(err, PermissionError):
        return "No write access. Run install.sh, then reboot."
    if isinstance(err, FileNotFoundError):
        return "Keyboard RGB interface not found (is the asus_wmi module loaded?)."
    return f"Write failed: {err}"


def rgb_to_hex(r, g, b):
    """Format 0-255 channels as '#rrggbb'."""
    return f"#{r:02x}{g:02x}{b:02x}"


def parse_hex(text):
    """Parse '#rrggbb' or 'rrggbb' into an (r, g, b) tuple, or None if invalid."""
    text = text.strip().removeprefix("#")
    if len(text) != 6:
        return None
    try:
        value = int(text, 16)
    except ValueError:
        return None
    return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF


def make_slider(lower, upper, step, labels=None):
    """Create a horizontal Gtk.Scale; labels (one per step) are drawn as marks below it."""
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lower, upper, step)
    scale.set_draw_value(False)
    # Fixed width so every slider starts at the same x, whatever the row title length.
    scale.set_size_request(270, -1)
    if labels:
        for value, label in enumerate(labels, start=int(lower)):
            scale.add_mark(value, Gtk.PositionType.BOTTOM, label)
    return scale


def slider_row(title, scale):
    """Wrap a slider in an Adw.ActionRow with the given title."""
    row = Adw.ActionRow(title=title)
    row.add_suffix(scale)
    return row


def snap(scale):
    """Snap a stepped slider to the nearest integer.

    Returns the snapped value, or None if the value was moved; the move fires
    value-changed again, so callers should just return on None.
    """
    value = scale.get_value()
    rounded = round(value)
    if rounded != value:
        scale.set_value(rounded)
        return None
    return rounded


def make_rgba(r, g, b, a=1.0):
    """Build a Gdk.RGBA from 0-1 float channels."""
    rgba = Gdk.RGBA()
    rgba.red, rgba.green, rgba.blue, rgba.alpha = r, g, b, a
    return rgba


def make_stop(offset, rgba):
    """Build a Gsk.ColorStop for gradient nodes."""
    stop = Gsk.ColorStop()
    stop.offset, stop.color = offset, rgba
    return stop


def append_circle(snapshot, x, y, radius, rgba):
    """Draw a filled circle centred on (x, y)."""
    clip = Gsk.RoundedRect()
    clip.init_from_rect(Graphene.Rect().init(x - radius, y - radius, 2 * radius, 2 * radius), radius)
    snapshot.push_rounded_clip(clip)
    snapshot.append_color(rgba, clip.bounds)
    snapshot.pop()


class SvSquare(Gtk.Widget):
    """Saturation (x) / intensity (y) square for the current hue, with a position marker.

    Drawn with GtkSnapshot so it needs no pycairo bridge. get_hsv is a callable
    returning the (hue, sat, val) to render, each 0-1.
    """

    def __init__(self, get_hsv):
        super().__init__(hexpand=True)
        self._get_hsv = get_hsv
        self.set_cursor_from_name("crosshair")

    def do_measure(self, orientation, _for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return SV_AREA_HEIGHT, SV_AREA_HEIGHT, -1, -1
        return 120, 240, -1, -1

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        hue, sat, val = self._get_hsv()
        bounds = Graphene.Rect().init(0, 0, width, height)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(bounds, 8)

        snapshot.push_rounded_clip(clip)
        snapshot.append_color(make_rgba(*colorsys.hsv_to_rgb(hue, 1, 1)), bounds)
        snapshot.append_linear_gradient(
            bounds, Graphene.Point().init(0, 0), Graphene.Point().init(width, 0),
            [make_stop(0, make_rgba(1, 1, 1, 1)), make_stop(1, make_rgba(1, 1, 1, 0))],
        )
        snapshot.append_linear_gradient(
            bounds, Graphene.Point().init(0, 0), Graphene.Point().init(0, height),
            [make_stop(0, make_rgba(0, 0, 0, 0)), make_stop(1, make_rgba(0, 0, 0, 1))],
        )
        snapshot.pop()

        # Dark halo under a white ring keeps the marker visible on any colour.
        x, y = sat * width, (1 - val) * height
        append_circle(snapshot, x, y, 10, make_rgba(0, 0, 0, 0.45))
        append_circle(snapshot, x, y, 9, make_rgba(1, 1, 1))
        append_circle(snapshot, x, y, 7, make_rgba(*colorsys.hsv_to_rgb(hue, sat, val)))


class LinuxKeyboardRgbWindow(Adw.ApplicationWindow):
    """Main window: inline colour picker, effect, speed and brightness.

    Every change is written to the keyboard as a preview (throttled to
    PREVIEW_INTERVAL_MS). Once changes stop for FIRMWARE_SAVE_DELAY_MS, or the
    window is closed with a save pending, the state is persisted to firmware.
    """

    def __init__(self, app):
        super().__init__(application=app, title="Keyboard RGB", default_width=460, resizable=False)
        self.settings = load_settings()
        self._preview_id = 0
        self._firmware_id = 0
        # Suppresses writes while widgets are populated from saved state.
        self._loading = True
        # Suppresses feedback loops between the picker and the hex entry.
        self._syncing = False
        # Picker state in 0-1 HSV; kept separately so hue survives greys/black.
        self._hue = self._sat = self._val = 0.0
        self._drag_origin = (0.0, 0.0)

        self._dynamic_css = Gtk.CssProvider()
        static_css = Gtk.CssProvider()
        static_css.load_from_string(STATIC_CSS)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, static_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.StyleContext.add_provider_for_display(display, self._dynamic_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.banner = Adw.Banner()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                          margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)
        content.append(self._build_colour_group())
        content.append(self._build_effect_group())

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.add_top_bar(self.banner)
        toolbar.set_content(content)
        self.set_content(toolbar)
        self.connect("close-request", self._on_close_request)

        self._sync_colour_widgets(update_picker=True, update_entry=True)
        self._update_sensitivity()
        self._loading = False

    def _build_colour_group(self):
        """Build the colour section: preview + hex entry, presets, 2D picker with hue bar."""
        self.colour_group = Adw.PreferencesGroup(title="Colour")

        self.current_swatch = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["current-swatch"])
        self.hex_entry = Gtk.Entry(max_length=7, width_chars=8, valign=Gtk.Align.CENTER)
        self.hex_entry.connect("changed", self._on_hex_changed)
        hex_row = Adw.ActionRow(title="Current")
        hex_row.add_suffix(self.current_swatch)
        hex_row.add_suffix(self.hex_entry)
        self.colour_group.add(hex_row)

        swatches = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=10)
        for index, (name, rgb) in enumerate(PRESETS):
            button = Gtk.Button(tooltip_text=name, css_classes=["swatch", f"swatch-{index}"])
            button.connect("clicked", self._on_preset_clicked, rgb)
            swatches.append(button)
        presets_row = Adw.PreferencesRow(activatable=False)
        presets_row.set_child(swatches)
        self.colour_group.add(presets_row)

        self.sv_area = SvSquare(lambda: (self._hue, self._sat, self._val))
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_sv_drag_begin)
        drag.connect("drag-update", self._on_sv_drag_update)
        self.sv_area.add_controller(drag)

        self.hue_scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, 0, 360, 1)
        self.hue_scale.set_draw_value(False)
        self.hue_scale.add_css_class("hue-bar")
        self.hue_scale.set_tooltip_text("Hue")
        self.hue_scale.connect("value-changed", self._on_hue_changed)

        picker = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        picker.append(self.sv_area)
        picker.append(self.hue_scale)
        picker_row = Adw.PreferencesRow(activatable=False)
        picker_row.set_child(picker)
        self.colour_group.add(picker_row)
        return self.colour_group

    def _build_effect_group(self):
        """Build the effect section: mode dropdown, speed and brightness sliders."""
        group = Adw.PreferencesGroup(title="Effect")

        self.mode_row = Adw.ComboRow(title="Effect", model=Gtk.StringList.new([label for label, _ in MODES]))
        self.mode_row.set_selected(MODE_NUMBERS.index(self.settings["mode"]))
        self.mode_row.connect("notify::selected", self._on_mode_changed)
        group.add(self.mode_row)

        self.speed_scale = make_slider(0, len(SPEEDS) - 1, 1, labels=SPEEDS)
        self.speed_scale.set_value(self.settings["speed"])
        self.speed_scale.connect("value-changed", self._on_speed_changed)
        self.speed_row = slider_row("Speed", self.speed_scale)
        group.add(self.speed_row)

        max_brightness = read_int(MAX_BRIGHTNESS_FILE, 3)
        self.brightness_scale = make_slider(0, max_brightness, 1, labels=[str(n) for n in range(max_brightness + 1)])
        self._last_brightness = read_int(BRIGHTNESS_FILE, 0)
        self.brightness_scale.set_value(self._last_brightness)
        self.brightness_scale.connect("value-changed", self._on_brightness_changed)
        group.add(slider_row("Brightness", self.brightness_scale))
        return group

    def _update_sensitivity(self):
        """Grey out controls the current effect ignores."""
        mode = self.settings["mode"]
        self.speed_row.set_sensitive(mode != MODE_STATIC)
        self.colour_group.set_sensitive(mode != MODE_CYCLE)

    def _show_error(self, err):
        """Show a write error in the banner; None hides the banner."""
        if err is None:
            self.banner.set_revealed(False)
            return
        self.banner.set_title(describe_error(err))
        self.banner.set_revealed(True)

    def _sync_colour_widgets(self, update_picker, update_entry):
        """Refresh the preview swatch and picker from settings; optionally re-derive picker HSV and the hex text."""
        r, g, b = self.settings["red"], self.settings["green"], self.settings["blue"]
        self._syncing = True
        try:
            if update_picker:
                h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                # Hue is undefined for greys and black; keep the previous one.
                if s > 0 and v > 0:
                    self._hue = h
                self._sat, self._val = s, v
                self.hue_scale.set_value(self._hue * 360)
            if update_entry:
                self.hex_entry.set_text(rgb_to_hex(r, g, b))
                self.hex_entry.remove_css_class("error")
        finally:
            self._syncing = False
        self.sv_area.queue_draw()
        self._dynamic_css.load_from_string(f".current-swatch {{ background: {rgb_to_hex(r, g, b)}; }}\n")

    def _set_rgb(self, rgb, update_picker, update_entry):
        """Store a new colour, refresh the colour widgets and push it to the keyboard."""
        self.settings["red"], self.settings["green"], self.settings["blue"] = rgb
        self._sync_colour_widgets(update_picker, update_entry)
        self._schedule_write()

    def _apply_hsv(self):
        """Convert the picker's HSV state to RGB and apply it."""
        r, g, b = colorsys.hsv_to_rgb(self._hue, self._sat, self._val)
        self._set_rgb((round(r * 255), round(g * 255), round(b * 255)), update_picker=False, update_entry=True)

    def _pick_sv(self, x, y):
        """Set saturation/intensity from a point in the square, clamped to its edges."""
        width, height = self.sv_area.get_width(), self.sv_area.get_height()
        if width <= 0 or height <= 0:
            return
        self._sat = min(max(x / width, 0.0), 1.0)
        self._val = 1.0 - min(max(y / height, 0.0), 1.0)
        self._apply_hsv()

    def _schedule_write(self):
        """Throttle preview writes and (re)start the firmware-save countdown."""
        if self._loading:
            return
        # Throttle rather than debounce so dragging a slider updates the keyboard live.
        if not self._preview_id:
            self._preview_id = GLib.timeout_add(PREVIEW_INTERVAL_MS, self._apply_preview)
        if self._firmware_id:
            GLib.source_remove(self._firmware_id)
        self._firmware_id = GLib.timeout_add(FIRMWARE_SAVE_DELAY_MS, self._save_to_firmware)

    def _write(self, cmd):
        """Write current settings with the given cmd, update the banner and local settings file."""
        try:
            write_rgb_mode(self.settings, cmd)
        except OSError as err:
            self._show_error(err)
            return
        self._show_error(None)
        save_settings(self.settings)

    def _apply_preview(self):
        """Timer callback: write the latest settings without touching firmware."""
        self._preview_id = 0
        self._write(CMD_PREVIEW)
        return GLib.SOURCE_REMOVE

    def _save_to_firmware(self):
        """Timer callback: persist the latest settings to firmware (cmd=1 also applies them)."""
        self._firmware_id = 0
        if self._preview_id:
            GLib.source_remove(self._preview_id)
            self._preview_id = 0
        self._write(CMD_SAVE)
        return GLib.SOURCE_REMOVE

    def _on_close_request(self, _window):
        """Flush a pending firmware save before the window closes; never blocks closing."""
        if self._firmware_id:
            GLib.source_remove(self._firmware_id)
            self._save_to_firmware()
        return False

    def _on_hue_changed(self, scale):
        if self._syncing:
            return
        self._hue = scale.get_value() / 360
        self._apply_hsv()

    def _on_sv_drag_begin(self, _gesture, x, y):
        self._drag_origin = (x, y)
        self._pick_sv(x, y)

    def _on_sv_drag_update(self, _gesture, dx, dy):
        self._pick_sv(self._drag_origin[0] + dx, self._drag_origin[1] + dy)

    def _on_hex_changed(self, entry):
        if self._syncing:
            return
        rgb = parse_hex(entry.get_text())
        if rgb is None:
            entry.add_css_class("error")
            return
        entry.remove_css_class("error")
        self._set_rgb(rgb, update_picker=True, update_entry=False)

    def _on_preset_clicked(self, _button, rgb):
        self._set_rgb(rgb, update_picker=True, update_entry=True)

    def _on_mode_changed(self, row, _pspec):
        self.settings["mode"] = MODE_NUMBERS[row.get_selected()]
        self._update_sensitivity()
        self._schedule_write()

    def _on_speed_changed(self, scale):
        value = snap(scale)
        if value is None or value == self.settings["speed"]:
            return
        self.settings["speed"] = value
        self._schedule_write()

    def _on_brightness_changed(self, scale):
        # Brightness is a separate sysfs file and not part of the firmware save.
        value = snap(scale)
        if value is None or self._loading or value == self._last_brightness:
            return
        try:
            BRIGHTNESS_FILE.write_text(f"{value}\n")
        except OSError as err:
            self._show_error(err)
            return
        self._last_brightness = value
        self._show_error(None)


class LinuxKeyboardRgbApp(Adw.Application):
    """Single-instance application wrapper; re-activating focuses the existing window."""

    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        # GNOME takes the icon from the .desktop file; other desktops use this.
        Gtk.Window.set_default_icon_name(ICON_NAME)
        window = self.get_active_window() or LinuxKeyboardRgbWindow(self)
        window.present()


if __name__ == "__main__":
    sys.exit(LinuxKeyboardRgbApp().run(sys.argv))
