"""GTK 4 / Libadwaita control center for TuxFlow."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from tuxflow import APP_ID
from tuxflow.config import ConfigStore, Replacement, Snippet
from tuxflow.history import HistoryStore
from tuxflow.ipc import send_command
from tuxflow.paths import socket_file
from tuxflow.system import is_macos, os_label

MODELS = ["tiny", "base", "small", "medium", "large-v3", "turbo"]
# Populated on macOS only; Linux shortcuts are owned by the desktop portal.
MAC_HOTKEYS = [
    ("Hold 🌐 fn", "fn"),
    ("Hold right ⌘ Command", "right_command"),
    ("Hold right ⌥ Option", "right_option"),
    ("Hold right ⌃ Control", "right_control"),
]
LANGUAGES = [
    ("Auto detect", "auto"),
    ("English", "en"),
    ("French", "fr"),
    ("Spanish", "es"),
    ("German", "de"),
    ("Italian", "it"),
    ("Portuguese", "pt"),
    ("Dutch", "nl"),
    ("Polish", "pl"),
    ("Ukrainian", "uk"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Chinese", "zh"),
]


def _background(function: Callable[[], object], callback: Callable[[object], None]) -> None:
    def work() -> None:
        try:
            result: object = function()
        except Exception as error:
            result = error
        GLib.idle_add(callback, result)

    threading.Thread(target=work, daemon=True).start()


# States during which the daemon owns the microphone or the CPU. Anything else is
# treated as "not busy" so a state this build has never heard of (the daemon may
# grow new ones) still leaves the window usable.
BUSY_STATES = frozenset({"recording", "processing"})
READY_STATES = frozenset({"idle", ""})
GENERIC_ERROR = "TuxFlow's background service reported a problem."
DAEMON_UNAVAILABLE = (
    "TuxFlow's background service could not be started. Try running: tuxflow daemon"
)


def _probe_daemon(timeout: float = 0.4) -> bool:
    """Return True when something is actually listening on the control socket.

    The socket file outlives a crashed daemon — only a clean shutdown unlinks it —
    so its mere existence says nothing about whether commands will be answered.
    """
    path = socket_file()
    if not path.exists():
        return False
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
    except OSError:
        return False
    else:
        return True
    finally:
        client.close()


def _ensure_daemon_running(*, wait: float = 6.0) -> bool:
    """Start the daemon unless one already answers. Blocking; call off the UI thread."""
    if _probe_daemon():
        return True
    # Nothing answered, so whatever is on disk is a leftover from a crash and would
    # make asyncio.open_unix_connection fail forever. Clear it before respawning.
    try:
        socket_file().unlink(missing_ok=True)
    except OSError:
        pass
    try:
        subprocess.Popen(
            [sys.executable, "-m", "tuxflow", "daemon"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    deadline = time.monotonic() + wait
    while True:
        if _probe_daemon():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _history_signature(items: Sequence[object]) -> tuple[int, int]:
    """Cheap identity for a history page: (row count, newest id)."""
    newest = getattr(items[0], "id", -1) if items else -1
    return len(items), int(newest)


def _status_message(state: str, shortcut: str) -> tuple[str, str, bool, bool]:
    """Map a daemon state to (status text, button label, button enabled, destructive)."""
    if state == "recording":
        return f"● Listening — {shortcut}", "Stop and transcribe", True, True
    if state == "processing":
        return "Transcribing locally…", "Working…", False, False
    if state in READY_STATES:
        return f"Ready — {shortcut}", "Start dictation", True, False
    if state == "offline":
        return "Background service is not running", "Start dictation", True, False
    # Unknown or newly added state (for example "starting"): name it rather than
    # claiming the service is ready.
    return f"{state.replace('_', ' ').capitalize()} — {shortcut}", "Start dictation", True, False


def _error_text(error: object) -> str:
    """Never let an empty or whitespace-only error blank the UI."""
    text = str(error).strip() if error is not None else ""
    return text or GENERIC_ERROR


class EditorDialog(Gtk.Window):
    def __init__(
        self,
        *,
        parent: Gtk.Window,
        title: str,
        first_label: str,
        second_label: str,
        multiline: bool,
        on_save: Callable[[str, str], None],
    ) -> None:
        super().__init__(title=title, transient_for=parent, modal=True)
        self.set_default_size(480, 260 if multiline else 180)
        self.on_save = on_save
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        self.set_child(box)

        first_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        first_box.append(Gtk.Label(label=first_label, xalign=0))
        self.first = Gtk.Entry()
        first_box.append(self.first)
        box.append(first_box)

        second_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        second_box.append(Gtk.Label(label=second_label, xalign=0))
        if multiline:
            self.second = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
            self.second.set_vexpand(True)
            frame = Gtk.Frame()
            frame.set_child(self.second)
            second_box.append(frame)
        else:
            self.second = Gtk.Entry()
            second_box.append(self.second)
        box.append(second_box)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_args: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        controls.append(cancel)
        controls.append(save)
        box.append(controls)

    def _save(self, *_args: object) -> None:
        first = self.first.get_text().strip()
        if isinstance(self.second, Gtk.TextView):
            buffer = self.second.get_buffer()
            second = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip()
        else:
            second = self.second.get_text().strip()
        if first and second:
            self.on_save(first, second)
            self.close()


class TuxFlowWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="TuxFlow")
        self.set_default_size(920, 640)
        self.config_store = ConfigStore()
        self.settings = self.config_store.load()
        self.history_store = HistoryStore()
        self._last_state: str | None = None
        self._history_signature: tuple[int, int] | None = None
        self._history_pending = False
        self._error_message = ""

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(title="TuxFlow", subtitle=f"Private dictation on {os_label()}")
        )
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toolbar.set_content(content)
        self.set_content(toolbar)

        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            hexpand=True,
            vexpand=True,
        )
        sidebar = Gtk.StackSidebar(stack=self.stack)
        sidebar.set_size_request(190, -1)
        sidebar.add_css_class("navigation-sidebar")
        content.append(sidebar)
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        content.append(separator)
        content.append(self.stack)

        self.stack.add_titled(self._build_home(), "home", "Home")
        self.stack.add_titled(self._build_settings(), "settings", "Settings")
        self.stack.add_titled(self._build_dictionary(), "dictionary", "Dictionary")
        self.stack.add_titled(self._build_snippets(), "snippets", "Snippets")
        self.stack.add_titled(self._build_privacy(), "privacy", "Privacy")

        GLib.timeout_add_seconds(1, self._poll_status)
        self._refresh_history(force=True)

    @staticmethod
    def _page() -> Gtk.ScrolledWindow:
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        return scroller

    @staticmethod
    def _page_box() -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(28)
        box.set_margin_bottom(28)
        box.set_margin_start(32)
        box.set_margin_end(32)
        return box

    def _build_home(self) -> Gtk.Widget:
        page = self._page()
        box = self._page_box()
        page.set_child(box)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hero.add_css_class("card")
        hero.set_margin_bottom(8)
        title = Gtk.Label(label="Speak anywhere. Your words stay here.", xalign=0)
        title.add_css_class("title-1")
        title.set_wrap(True)
        hero.append(title)
        subtitle = Gtk.Label(
            label="Hold the global shortcut while speaking, or use the button below. "
            "TuxFlow records, transcribes locally, and pastes into the active app.",
            xalign=0,
            wrap=True,
        )
        subtitle.add_css_class("dim-label")
        hero.append(subtitle)
        self.status_label = Gtk.Label(label="Checking background service…", xalign=0)
        hero.append(self.status_label)
        hero.append(self._build_error_banner())
        self.toggle_button = Gtk.Button(label="Start dictation")
        self.toggle_button.add_css_class("suggested-action")
        self.toggle_button.add_css_class("pill")
        self.toggle_button.set_halign(Gtk.Align.START)
        self.toggle_button.connect("clicked", lambda *_args: self._command("toggle"))
        hero.append(self.toggle_button)
        box.append(hero)

        history_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        history_title = Gtk.Label(label="Recent dictations", xalign=0, hexpand=True)
        history_title.add_css_class("title-2")
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.connect("clicked", lambda *_args: self._refresh_history(force=True))
        history_header.append(history_title)
        history_header.append(refresh)
        box.append(history_header)
        self.history_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.history_list.add_css_class("boxed-list")
        box.append(self.history_list)
        return page

    def _build_error_banner(self) -> Gtk.Widget:
        # Adw.Banner needs libadwaita 1.3; fall back to a plain label elsewhere so
        # the error is still visible on older runtimes.
        if hasattr(Adw, "Banner"):
            banner = Adw.Banner(revealed=False)
            banner.set_button_label("Dismiss")
            banner.connect("button-clicked", lambda *_args: self.show_error(""))
        else:
            banner = Gtk.Label(xalign=0, wrap=True)
            banner.add_css_class("error")
            banner.set_visible(False)
        self.error_banner = banner
        return banner

    def show_error(self, message: str) -> None:
        """Show a daemon error, or hide the banner when the message is empty."""
        text = message.strip() if message else ""
        if text == self._error_message:
            return
        self._error_message = text
        banner = getattr(self, "error_banner", None)
        if banner is None:
            return
        if isinstance(banner, Gtk.Label):
            banner.set_text(text)
            banner.set_visible(bool(text))
            return
        if text:
            banner.set_title(text)
        banner.set_revealed(bool(text))

    def _build_settings(self) -> Gtk.Widget:
        page = self._page()
        box = self._page_box()
        page.set_child(box)
        title = Gtk.Label(label="Transcription", xalign=0)
        title.add_css_class("title-1")
        box.append(title)

        group = Adw.PreferencesGroup()
        group.set_description("Models download once, then run entirely on this computer.")
        model_store = Gtk.StringList.new(MODELS)
        self.model_row = Adw.ComboRow(title="Whisper model", model=model_store)
        self.model_row.set_subtitle(
            "small is a good CPU balance; turbo favors quality and speed on a GPU"
        )
        try:
            self.model_row.set_selected(MODELS.index(self.settings.model))
        except ValueError:
            self.model_row.set_selected(2)
        self.model_row.connect("notify::selected", self._model_changed)
        group.add(self.model_row)

        language_store = Gtk.StringList.new([label for label, _code in LANGUAGES])
        self.language_row = Adw.ComboRow(title="Language", model=language_store)
        language_codes = [code for _label, code in LANGUAGES]
        self.language_row.set_selected(
            language_codes.index(self.settings.language)
            if self.settings.language in language_codes
            else 0
        )
        self.language_row.connect("notify::selected", self._language_changed)
        group.add(self.language_row)
        group.add(self._build_microphone_row())
        box.append(group)

        if is_macos():
            box.append(self._build_hotkey_group())

        behavior = Adw.PreferencesGroup(title="Behavior")
        behavior.add(
            self._switch_row(
                "Paste automatically",
                "Otherwise the transcript is only copied to the clipboard",
                "auto_paste",
            )
        )
        behavior.add(
            self._switch_row(
                "Remove hesitation sounds",
                "Remove standalone “um”, “uh”, “erm”, and “hmm”",
                "remove_fillers",
            )
        )
        behavior.add(
            self._switch_row(
                "Spoken punctuation",
                "Convert phrases such as “new line” and “question mark”",
                "spoken_punctuation",
            )
        )
        behavior.add(
            self._switch_row(
                "Voice command: press enter",
                "Remove “press enter” at the end and submit after pasting",
                "press_enter_command",
            )
        )
        box.append(behavior)
        return page

    def _build_microphone_row(self) -> Adw.EntryRow:
        # The value is handed to whichever recorder this machine uses, so the
        # hint has to name what that recorder expects.
        hint = (
            "AVFoundation device, such as :0 or :1"
            if is_macos()
            else "PipeWire target or ALSA device, such as alsa_input.pci-0000_00_1f.3"
        )
        row = Adw.EntryRow(title="Microphone (leave empty for the system default)")
        row.set_text(self.settings.audio_device)
        row.set_tooltip_text(hint)
        row.set_show_apply_button(True)
        row.connect("apply", self._audio_device_changed)
        self.audio_device_row = row
        return row

    def _audio_device_changed(self, row: Adw.EntryRow) -> None:
        self.settings.audio_device = row.get_text().strip()
        self._save_settings()

    def _build_hotkey_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Dictation shortcut")
        group.set_description(
            "Hold the key while you speak. TuxFlow needs Accessibility access in "
            "System Settings › Privacy & Security, and the service restarts to "
            "pick up a new key."
        )
        labels = [label for label, _key in MAC_HOTKEYS]
        keys = [key for _label, key in MAC_HOTKEYS]
        self.hotkey_row = Adw.ComboRow(title="Hold to dictate", model=Gtk.StringList.new(labels))
        self.hotkey_row.set_selected(
            keys.index(self.settings.macos_hotkey) if self.settings.macos_hotkey in keys else 0
        )
        self.hotkey_row.connect("notify::selected", self._hotkey_changed)
        group.add(self.hotkey_row)
        return group

    def _hotkey_changed(self, row: Adw.ComboRow, _param: object) -> None:
        self.settings.macos_hotkey = MAC_HOTKEYS[row.get_selected()][1]
        self._save_settings()

    def _switch_row(self, title: str, subtitle: str, attribute: str) -> Adw.SwitchRow:
        row = Adw.SwitchRow(title=title, subtitle=subtitle)
        row.set_active(bool(getattr(self.settings, attribute)))
        row.connect("notify::active", self._switch_changed, attribute)
        return row

    def _build_dictionary(self) -> Gtk.Widget:
        return self._build_collection_page(
            title="Personal dictionary",
            description="Replace words Whisper tends to mishear. Matching is case-insensitive.",
            collection="dictionary",
        )

    def _build_snippets(self) -> Gtk.Widget:
        return self._build_collection_page(
            title="Voice snippets",
            description="Speak a short trigger to insert a larger block of text.",
            collection="snippets",
        )

    def _build_collection_page(
        self, *, title: str, description: str, collection: str
    ) -> Gtk.Widget:
        page = self._page()
        box = self._page_box()
        page.set_child(box)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("title-1")
        label_box.append(label)
        detail = Gtk.Label(label=description, xalign=0, wrap=True)
        detail.add_css_class("dim-label")
        label_box.append(detail)
        heading.append(label_box)
        add = Gtk.Button(label="Add")
        add.add_css_class("suggested-action")
        add.connect("clicked", lambda *_args: self._add_item(collection))
        heading.append(add)
        box.append(heading)
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        setattr(self, f"{collection}_list", listbox)
        box.append(listbox)
        GLib.idle_add(self._refresh_collection, collection)
        return page

    def _build_privacy(self) -> Gtk.Widget:
        page = self._page()
        box = self._page_box()
        page.set_child(box)
        title = Gtk.Label(label="Local by design", xalign=0)
        title.add_css_class("title-1")
        box.append(title)
        body = Gtk.Label(
            label=(
                "Audio and transcripts are processed on this computer. TuxFlow has no "
                "account system, telemetry, subscription, or paid API integration. The "
                "selected Whisper model is downloaded once from Hugging Face.\n\n"
                "Transcription history is stored in your home directory. Temporary "
                "microphone recordings are deleted after transcription by default."
            ),
            xalign=0,
            yalign=0,
            wrap=True,
        )
        body.set_max_width_chars(72)
        box.append(body)
        keep = self._switch_row(
            "Keep microphone recordings",
            "Off is recommended. When on, WAV files remain in the cache directory.",
            "keep_audio",
        )
        group = Adw.PreferencesGroup()
        group.add(keep)
        box.append(group)
        clear = Gtk.Button(label="Clear transcription history")
        clear.add_css_class("destructive-action")
        clear.set_halign(Gtk.Align.START)
        clear.connect("clicked", self._clear_history)
        box.append(clear)
        return page

    def _model_changed(self, row: Adw.ComboRow, _param: object) -> None:
        self.settings.model = MODELS[row.get_selected()]
        self._save_settings()

    def _language_changed(self, row: Adw.ComboRow, _param: object) -> None:
        self.settings.language = LANGUAGES[row.get_selected()][1]
        self._save_settings()

    def _switch_changed(self, row: Adw.SwitchRow, _param: object, attribute: str) -> None:
        setattr(self.settings, attribute, row.get_active())
        self._save_settings()

    def _save_settings(self) -> None:
        self.config_store.save(self.settings)

    def _add_item(self, collection: str) -> None:
        if collection == "dictionary":
            dialog = EditorDialog(
                parent=self,
                title="Add dictionary replacement",
                first_label="Whisper hears",
                second_label="Write instead",
                multiline=False,
                on_save=lambda first, second: self._store_item(
                    collection, Replacement(first, second)
                ),
            )
        else:
            dialog = EditorDialog(
                parent=self,
                title="Add voice snippet",
                first_label="Spoken trigger",
                second_label="Expansion",
                multiline=True,
                on_save=lambda first, second: self._store_item(collection, Snippet(first, second)),
            )
        dialog.present()

    def _store_item(self, collection: str, item: Replacement | Snippet) -> None:
        getattr(self.settings, collection).append(item)
        self._save_settings()
        self._refresh_collection(collection)

    def _remove_item(self, collection: str, index: int) -> None:
        items = getattr(self.settings, collection)
        if 0 <= index < len(items):
            items.pop(index)
            self._save_settings()
            self._refresh_collection(collection)

    def _refresh_collection(self, collection: str) -> bool:
        listbox: Gtk.ListBox = getattr(self, f"{collection}_list")
        while child := listbox.get_first_child():
            listbox.remove(child)
        for index, item in enumerate(getattr(self.settings, collection)):
            if collection == "dictionary":
                title, subtitle = item.written, f"When you say: {item.spoken}"
            else:
                title, subtitle = item.trigger, item.expansion
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            delete = Gtk.Button(
                icon_name="user-trash-symbolic",
                tooltip_text="Delete",
                valign=Gtk.Align.CENTER,
            )
            delete.add_css_class("flat")
            delete.connect(
                "clicked", lambda _button, i=index, name=collection: self._remove_item(name, i)
            )
            row.add_suffix(delete)
            listbox.append(row)
        if not getattr(self.settings, collection):
            row = Adw.ActionRow(
                title="Nothing here yet",
                subtitle="Use Add to create your first entry.",
            )
            listbox.append(row)
        return GLib.SOURCE_REMOVE

    def _command(self, command: str) -> None:
        self.toggle_button.set_sensitive(False)

        def execute() -> object:
            return asyncio.run(send_command(command))

        def finished(result: object) -> None:
            self.toggle_button.set_sensitive(True)
            if isinstance(result, Exception):
                # Timeouts arrive as RuntimeError from ipc.send_command; anything
                # else still gets a readable message rather than an empty banner.
                self.show_error(_error_text(result))
            elif isinstance(result, Mapping):
                self._render_status(result)

        _background(execute, finished)

    def _poll_status(self) -> bool:
        def execute() -> object:
            return asyncio.run(send_command("status"))

        def finished(result: object) -> None:
            if isinstance(result, Mapping):
                self._render_status(result)
            elif isinstance(result, Exception):
                self._render_status({"state": "offline", "last_error": _error_text(result)})

        _background(execute, finished)
        return GLib.SOURCE_CONTINUE

    def _render_status(self, status: Mapping[str, object]) -> None:
        state = str(status.get("state") or "offline")
        shortcut = str(status.get("shortcut") or "") or "Shortcut not configured"
        label, button_label, sensitive, destructive = _status_message(state, shortcut)
        self.status_label.set_text(label)
        self.toggle_button.set_label(button_label)
        self.toggle_button.set_sensitive(sensitive)
        if destructive:
            self.toggle_button.add_css_class("destructive-action")
        else:
            self.toggle_button.remove_css_class("destructive-action")

        # A missing key and an empty string both mean "no error"; only a non-empty
        # last_error should raise the banner.
        raw_error = status.get("last_error") or ""
        self.show_error(str(raw_error).strip())

        previous, self._last_state = self._last_state, state
        # Only touch SQLite when a dictation could have finished, instead of on
        # every one-second poll.
        if previous != state and state not in BUSY_STATES:
            self._refresh_history()

    def _refresh_history(self, *, force: bool = False) -> None:
        if not hasattr(self, "history_list") or self._history_pending:
            return
        self._history_pending = True

        def execute() -> object:
            return self.history_store.recent(20)

        def finished(result: object) -> None:
            self._history_pending = False
            if isinstance(result, Exception) or not isinstance(result, list):
                return
            signature = _history_signature(result)
            # Rebuilding the list box resets scroll position and flickers, so skip
            # it when nothing was added or removed.
            if not force and signature == self._history_signature:
                return
            self._history_signature = signature
            self._populate_history(result)

        _background(execute, finished)

    def _populate_history(self, items: Sequence[object]) -> None:
        while child := self.history_list.get_first_child():
            self.history_list.remove(child)
        for item in items:
            row = Adw.ActionRow(
                title=item.text or "(voice command)",
                subtitle=f"{item.language.upper()} · {item.duration_seconds:.1f}s · {item.model}",
            )
            copy = Gtk.Button(
                icon_name="edit-copy-symbolic",
                tooltip_text="Copy transcript",
                valign=Gtk.Align.CENTER,
            )
            copy.add_css_class("flat")
            copy.connect("clicked", lambda _button, text=item.text: self._copy(text))
            row.add_suffix(copy)
            self.history_list.append(row)
        if not items:
            self.history_list.append(
                Adw.ActionRow(
                    title="No dictations yet",
                    subtitle="Your first transcript will appear here.",
                )
            )

    def _copy(self, text: str) -> None:
        clipboard = self.get_clipboard()
        clipboard.set(text)

    def _clear_history(self, *_args: object) -> None:
        self.history_store.clear()
        self._refresh_history(force=True)


class TuxFlowApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window: TuxFlowWindow | None = None

    def do_activate(self) -> None:
        if self.window is None:
            self.window = TuxFlowWindow(self)
        self.window.present()
        self._ensure_daemon()

    def _ensure_daemon(self) -> None:
        # Probing, spawning and waiting all block, so they must not run on the GTK
        # main loop; the window is already up and the status poll fills it in.
        def finished(result: object) -> None:
            if self.window is None:
                return
            if isinstance(result, Exception):
                self.window.show_error(_error_text(result))
            elif result is False:
                self.window.show_error(DAEMON_UNAVAILABLE)

        _background(_ensure_daemon_running, finished)


def run_app() -> int:
    app = TuxFlowApplication()
    # Only the program name. Everything after it has already been consumed by
    # TuxFlow's own parser, and GApplication would read the leftover `app`
    # subcommand as a file to open and refuse to start.
    return int(app.run(sys.argv[:1]))
