import os
import re
import sys
import time
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from tkinter import font as tkfont
from datetime import datetime

import customtkinter as ctk
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

DROPDOWN_BG = "#FFF3B0"
DROPDOWN_BG_HOVER = "#F6E27A"
DROPDOWN_TEXT = "#333333"
DROPDOWN_WIDTH = 130
DROPDOWN_HEIGHT = 22

try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("APlayers.App")
except Exception:
    pass

import APlayers

# --- Redirect APlayers.log to GUI console ---
_gui_queue = queue.Queue()
_abort_event = threading.Event()

APlayers.abort_check = lambda: _abort_event.is_set()


def gui_log(msg, color=None):
    _gui_queue.put(("log", msg, color))


APlayers.log = gui_log


def put_progress(current, total, text=""):
    _gui_queue.put(("progress", current, total, text))


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SUCCESS_WAV = os.path.join(SCRIPT_DIR, "Resources", "success.wav")
ICON_PATH = os.path.join(SCRIPT_DIR, "Resources", "icon.ico")


def play_success():
    if not APlayers.SOUND:
        return
    if os.path.exists(SUCCESS_WAV):
        try:
            import winsound
            winsound.PlaySound(SUCCESS_WAV, winsound.SND_ASYNC)
        except Exception:
            pass


# --- Color map for ScrolledText tags ---
COLOR_MAP = {
    "R": "#cc0000", "G": "#00aa00", "Y": "#cccc00",
    "B": "#0066cc", "M": "#cc00cc", "C": "#009999",
    "W": "#cccccc", "BR": "#ff3333", "BG": "#33ff33",
    "BY": "#ffff33", "BB": "#3399ff", "BC": "#33ffff",
    "BW": "#ffffff",
}


class MultiSelectDropdown:
    def __init__(self, parent, options=None, callback=None, width=DROPDOWN_WIDTH, font=None):
        self._selected = set()
        self._options = []
        self._callback = callback
        self._popup = None
        self._vars = {}
        self.btn = ctk.CTkButton(parent, text="All  ▾", width=width, height=DROPDOWN_HEIGHT,
                                 corner_radius=6, anchor="w",
                                 fg_color=DROPDOWN_BG, hover_color=DROPDOWN_BG_HOVER,
                                 text_color=DROPDOWN_TEXT, command=self._toggle_popup)
        if options:
            self.set_options(options)

    def set_options(self, options):
        self._options = list(options)
        self._selected &= set(options)
        self._update_text()

    def set_selected(self, values):
        self._selected = set(values)
        self._update_text()

    def get_selected(self):
        return list(self._selected)

    def _update_text(self):
        suffix = "  ▾"
        budget = 112

        if not self._selected:
            label = "All"
        else:
            names = sorted(self._selected)
            if len(names) == 1:
                label = names[0]
            else:
                label = ", ".join(names[:3])
                if len(names) > 3:
                    label += f" (+{len(names) - 3})"

        font = self.btn.cget("font")
        if font.measure(label + suffix) > budget:
            if len(self._selected) > 1:
                label = f"{len(self._selected)} valda"
            else:
                while label and font.measure(label + "…" + suffix) > budget:
                    label = label[:-1]
                label += "…"

        self.btn.configure(text=label + suffix)

    def _toggle_popup(self):
        if self._popup is not None:
            self._close_popup()
        else:
            self._open_popup()

    def _open_popup(self):
        self._close_popup()
        popup = ctk.CTkToplevel(self.btn)
        popup.overrideredirect(True)
        try:
            popup.transient(self.btn.winfo_toplevel())
        except Exception:
            pass
        self._popup = popup

        longest = max([len(str(o)) for o in self._options] or [4])
        popup_w = max(190, min(380, longest * 11 + 70))
        list_h = min(240, max(64, len(self._options) * 36 + 12))

        x = self.btn.winfo_rootx()
        y = self.btn.winfo_rooty() + self.btn.winfo_height()
        popup.geometry(f"+{x}+{y}")
        popup.attributes("-topmost", True)

        frame = ctk.CTkScrollableFrame(popup, width=popup_w, height=list_h)
        frame.pack(fill="both", expand=True, padx=4, pady=(4, 0))

        self._vars = {}
        for opt in self._options:
            var = tk.BooleanVar(value=(opt in self._selected))
            self._vars[opt] = var
            cb = ctk.CTkCheckBox(frame, text=str(opt), variable=var,
                                 fg_color=DROPDOWN_BG, hover_color=DROPDOWN_BG_HOVER,
                                 text_color=DROPDOWN_TEXT,
                                 command=lambda o=opt: self._on_toggle(o))
            cb.pack(anchor="w", fill="x", padx=10, pady=4)

        btn_row = ctk.CTkFrame(popup, fg_color="transparent")
        btn_row.pack(fill="x", padx=4, pady=(2, 6))
        ctk.CTkButton(btn_row, text="Rensa", width=64, height=24,
                      fg_color=DROPDOWN_BG, hover_color=DROPDOWN_BG_HOVER,
                      text_color=DROPDOWN_TEXT,
                      command=self._clear).pack(side="left", padx=6)
        ctk.CTkButton(btn_row, text="Klar", width=64, height=24,
                      fg_color=DROPDOWN_BG, hover_color=DROPDOWN_BG_HOVER,
                      text_color=DROPDOWN_TEXT,
                      command=self._close_popup).pack(side="right", padx=6)

        popup.bind("<ButtonPress-1>", self._on_popup_press)
        popup.bind("<Escape>", lambda e: self._close_popup())
        popup.lift()
        try:
            popup.grab_set()
        except Exception:
            pass

    def _on_toggle(self, opt):
        if self._vars[opt].get():
            self._selected.add(opt)
        else:
            self._selected.discard(opt)
        self._update_text()
        if self._callback:
            self._callback()

    def _clear(self):
        self._selected.clear()
        for var in self._vars.values():
            var.set(False)
        self._update_text()
        if self._callback:
            self._callback()

    def _on_popup_press(self, event):
        if self._popup is None:
            return
        px = self._popup.winfo_rootx()
        py = self._popup.winfo_rooty()
        pw = self._popup.winfo_width()
        ph = self._popup.winfo_height()
        if not (px <= event.x_root <= px + pw and py <= event.y_root <= py + ph):
            self._close_popup()

    def _close_popup(self):
        if self._popup is not None:
            try:
                self._popup.grab_release()
            except Exception:
                pass
            self._popup.destroy()
            self._popup = None
        self._update_text()


class APlayersGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("APlayers")
        win_w, win_h = APlayers.load_window_size()
        self.root.geometry(f"{win_w}x{win_h}")
        self.root.minsize(700, 500)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if os.path.exists(ICON_PATH):
            self.root.iconbitmap(ICON_PATH)
        self._running = False
        self._task_start = None
        self._last_current = 0
        self._last_total = 1

        self._columns = APlayers.load_columns()
        self._lista_generation = 0
        self._lista_columns = []
        self._lista_rows = []
        self._lista_sort_col = None
        self._lista_sort_reverse = False
        self._lista_queue = queue.Queue()
        self._lista_url_by_iid = {}

        # --- Menu bar ---
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        files_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Filer", menu=files_menu)
        files_menu.add_command(label="Öppna mapp", command=self._open_app_folder)
        files_menu.add_separator()
        files_menu.add_command(label="Ta bort matcher", command=self._delete_matcher)

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Visa", menu=view_menu)
        view_menu.add_command(label="Matcher i kö", command=self._view_pending)
        view_menu.add_command(label="Ligastatistik", command=self._view_stats)

        ligor_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Ligor", menu=ligor_menu)
        ligor_menu.add_command(label="Hantera ligor", command=self._manage_ligor)

        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Inställningar", menu=settings_menu)
        settings_menu.add_command(label="Fördröjning...", command=self._settings_delay)
        settings_menu.add_command(label="Försök...", command=self._settings_retries)
        settings_menu.add_command(label="Spelar ålder-cutoff...", command=self._settings_age_cutoff)
        self._refetch_players_var = tk.BooleanVar(value=APlayers.REFETCH_PLAYERS)
        settings_menu.add_checkbutton(label="Hämta spelare på nytt", variable=self._refetch_players_var,
                                      command=self._toggle_refetch_players)
        settings_menu.add_separator()

        self._sound_var = tk.BooleanVar(value=APlayers.SOUND)
        settings_menu.add_checkbutton(label="Ljud", variable=self._sound_var,
                                      command=self._toggle_sound)
        settings_menu.add_command(label="Event labels...", command=self._settings_event_labels)
        settings_menu.add_command(label="Kolumner...", command=self._settings_columns)

        about_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Om", menu=about_menu)
        about_menu.add_command(label="Github", command=self._about_github)

        # --- Progress bar area ---
        prog_frame = tk.Frame(self.root, height=60)
        prog_frame.pack(fill=tk.X, padx=10, pady=(10, 0))
        prog_frame.pack_propagate(False)

        self.prog_label = tk.Label(prog_frame, text="Redo", anchor="w", font=("", 9))
        self.prog_label.pack(fill=tk.X)

        self.prog_canvas = tk.Canvas(prog_frame, height=26, bg="#222", highlightthickness=0)
        self.prog_canvas.pack(fill=tk.X, pady=(2, 0))
        self.prog_bar_bg = self.prog_canvas.create_rectangle(0, 0, 0, 26, fill="#333", outline="")
        self.prog_bar_fg = self.prog_canvas.create_rectangle(0, 0, 0, 26, fill="#009999", outline="")
        self.prog_text = self.prog_canvas.create_text(450, 13, text="", fill="#fff", font=("", 9, "bold"))

        # --- Filters + info fields, grouped into sections ---
        saved_filters = APlayers.load_filters()
        self._sel_dropdowns = {}
        self._info_vars = {}

        # --- Section: Ligor & Matcher ---
        lm_frame = tk.LabelFrame(self.root, text="Ligor & Matcher", padx=8, pady=4)
        lm_frame.pack(fill=tk.X, padx=10, pady=(6, 0))
        for col in (1, 3, 5, 7):
            lm_frame.columnconfigure(col, uniform="box")
        for col in (0, 2, 4, 6):
            lm_frame.columnconfigure(col, uniform="lbl")

        filters = [("responsible", "Ansvarig"), ("liga", "Liga"), ("year", "År"), ("country", "Land")]
        for i, (key, text) in enumerate(filters):
            tk.Label(lm_frame, text=text, font=("", 9), anchor="e").grid(
                row=0, column=i * 2, padx=(0 if i == 0 else 12, 4), sticky="ew")
            dd = MultiSelectDropdown(lm_frame, callback=self._on_filter_change, width=DROPDOWN_WIDTH)
            dd.set_selected(saved_filters.get(key, []))
            dd.btn.grid(row=0, column=i * 2 + 1, sticky="ew")
            self._sel_dropdowns[key] = dd

        info_items = [("leagues", "Ligor"), ("matches", "Matcher"), ("lineups", "Lineups")]
        for i, (key, text) in enumerate(info_items):
            tk.Label(lm_frame, text=text, font=("", 9), anchor="e").grid(
                row=1, column=i * 2, padx=(0 if i == 0 else 12, 4), pady=(4, 0), sticky="ew")
            var = tk.StringVar(value="-")
            self._info_vars[key] = var
            ent = tk.Entry(lm_frame, textvariable=var, font=("", 9), width=18,
                           state="readonly", relief="solid", bd=1, readonlybackground="white")
            ent.grid(row=1, column=i * 2 + 1, pady=(4, 0), sticky="ew")

        # --- Section: Spelare ---
        sp_frame = tk.LabelFrame(self.root, text="Spelare", padx=8, pady=4)
        sp_frame.pack(fill=tk.X, padx=10, pady=(6, 0))
        for col in (1, 3, 5):
            sp_frame.columnconfigure(col, uniform="box")
        for col in (0, 2, 4):
            sp_frame.columnconfigure(col, uniform="lbl")

        player_filters = [("section", "Lineup"), ("position", "Position"), ("age", "Ålder")]
        for i, (key, text) in enumerate(player_filters):
            tk.Label(sp_frame, text=text, font=("", 9), anchor="e").grid(
                row=0, column=i * 2, padx=(0 if i == 0 else 12, 4), sticky="ew")
            dd = MultiSelectDropdown(sp_frame, callback=self._on_filter_change, width=DROPDOWN_WIDTH)
            dd.set_selected(saved_filters.get(key, []))
            dd.btn.grid(row=0, column=i * 2 + 1, sticky="ew")
            self._sel_dropdowns[key] = dd

        tk.Label(sp_frame, text="Spelare", font=("", 9), anchor="e").grid(
            row=1, column=0, padx=(0, 4), pady=(4, 0), sticky="ew")
        var = tk.StringVar(value="-")
        self._info_vars["lineup_players"] = var
        ent = tk.Entry(sp_frame, textvariable=var, font=("", 9), width=18,
                       state="readonly", relief="solid", bd=1, readonlybackground="white")
        ent.grid(row=1, column=1, pady=(4, 0), sticky="ew")

        tk.Label(sp_frame, text="Rader", font=("", 9), anchor="e").grid(
            row=1, column=2, padx=(12, 4), pady=(4, 0), sticky="ew")
        var = tk.StringVar(value="-")
        self._info_vars["rows"] = var
        ent = tk.Entry(sp_frame, textvariable=var, font=("", 9), width=18,
                       state="readonly", relief="solid", bd=1, readonlybackground="white")
        ent.grid(row=1, column=3, pady=(4, 0), sticky="ew")

        # --- Section: Spelare totalt ---
        tot_frame = tk.LabelFrame(self.root, text="Spelare totalt", padx=8, pady=4)
        tot_frame.pack(fill=tk.X, padx=10, pady=(6, 0))
        tot_frame.columnconfigure(0, uniform="lbl")
        tot_frame.columnconfigure(1, uniform="box")

        tk.Label(tot_frame, text="Spelare", font=("", 9), anchor="e").grid(
            row=0, column=0, padx=(0, 4), sticky="ew")
        var = tk.StringVar(value="-")
        self._info_vars["players"] = var
        ent = tk.Entry(tot_frame, textvariable=var, font=("", 9), width=18,
                       state="readonly", relief="solid", bd=1, readonlybackground="white")
        ent.grid(row=0, column=1, sticky="ew")

        self._refresh_dropdowns()

        # --- Buttons ---
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=8)

        self.btn_reset = tk.Button(btn_frame, text="Nollställ filter", command=self._reset_filters,
                                   padx=14, pady=4, font=("", 9))
        self.btn_reset.pack(side=tk.LEFT, padx=4)

        self.btn_ligor = tk.Button(btn_frame, text="Hämta matcher", command=self.start_hamta_ligor,
                                   padx=14, pady=4, font=("", 9, "bold"))
        self.btn_ligor.pack(side=tk.LEFT, padx=4)

        self.btn_matcher = tk.Button(btn_frame, text="Hämta lineups", command=self.start_hamta_matcher,
                                     padx=14, pady=4, font=("", 9, "bold"))
        self.btn_matcher.pack(side=tk.LEFT, padx=4)

        self.btn_spelare = tk.Button(btn_frame, text="Hämta spelare", command=self.start_hamta_spelare,
                                     padx=14, pady=4, font=("", 9, "bold"))
        self.btn_spelare.pack(side=tk.LEFT, padx=4)

        self.btn_excel = tk.Button(btn_frame, text="Export excel", command=self.start_export_excel,
                                   padx=14, pady=4, font=("", 9, "bold"))
        self.btn_excel.pack(side=tk.LEFT, padx=4)

        self.btn_abort = tk.Button(btn_frame, text="Avbryt", command=self.abort,
                                   padx=14, pady=4, font=("", 9, "bold"),
                                   state=tk.DISABLED)
        self.btn_abort.pack(side=tk.LEFT, padx=4)

        # --- Tabs: Lista + Konsol ---
        tab_frame = tk.Frame(self.root)
        tab_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))

        self.notebook = ttk.Notebook(tab_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # --- Lista tab ---
        lista_tab = tk.Frame(self.notebook)
        self.notebook.add(lista_tab, text="Lista")

        lista_container = tk.Frame(lista_tab)
        lista_container.pack(fill=tk.BOTH, expand=True)

        self.lista_tree = ttk.Treeview(lista_container, columns=(), show="headings")
        vsb = ttk.Scrollbar(lista_container, orient="vertical", command=self.lista_tree.yview)
        hsb = ttk.Scrollbar(lista_container, orient="horizontal", command=self.lista_tree.xview)
        self.lista_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.lista_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._lista_style = ttk.Style()
        self._lista_style.configure("Grey.Treeview", background="#cccccc", fieldbackground="#cccccc")
        self.lista_tree.bind("<Button-1>", self._on_lista_header_click)
        self.lista_tree.bind("<Button-1>", self._on_lista_cell_click, add="+")

        # --- Konsol tab ---
        console_tab = tk.Frame(self.notebook)
        self.notebook.add(console_tab, text="Konsol")

        self.console = scrolledtext.ScrolledText(
            console_tab, bg="#1a1a1a", fg="#cccccc",
            insertbackground="white", font=("Consolas", 9), wrap=tk.WORD,
            state=tk.DISABLED
        )
        self.console.pack(fill=tk.BOTH, expand=True)

        # Setup color tags
        for tag, hex_color in COLOR_MAP.items():
            self.console.tag_config(tag, foreground=hex_color)

        self.console.tag_config("link", foreground="#33ccff", underline=True)
        self.console.tag_bind("link", "<Button-1>", self._open_link)
        self.console.tag_bind("link", "<Enter>", lambda e: self.console.config(cursor="hand2"))
        self.console.tag_bind("link", "<Leave>", lambda e: self.console.config(cursor=""))
        self._link_ranges = {}

        # Start queue poller
        self._poll_queue()
        self._poll_lista()

    # --- Console / Progress helpers ---
    def log_console(self, msg, color=None):
        self.console.config(state=tk.NORMAL)
        tag = color if color and color in COLOR_MAP else None
        pos = 0
        for m in re.finditer(r"\b\d{7,10}\b|https?://\S+|\[([^]]+)\]", msg):
            before = msg[pos:m.start()]
            if before:
                if tag:
                    self.console.insert(tk.END, before, tag)
                else:
                    self.console.insert(tk.END, before)
            mid = m.group()
            lineup_url = APlayers._match_urls.get(mid, "")
            if mid.startswith("["):
                inner = m.group(1)
                link_url = APlayers._match_urls.get(inner, "")
                display = mid
            elif mid.startswith("http"):
                link_url = mid.rstrip(".,;:!?")
                display = APlayers._match_urls.get(link_url, link_url)
            else:
                link_url = lineup_url
                display = mid
            if link_url:
                start = self.console.index(tk.END + "-1c")
                if tag:
                    self.console.insert(tk.END, display, ("link", tag))
                else:
                    self.console.insert(tk.END, display, "link")
                end = self.console.index(tk.END + "-1c")
                self._link_ranges[(start, end)] = link_url
            else:
                if tag:
                    self.console.insert(tk.END, mid, tag)
                else:
                    self.console.insert(tk.END, mid)
            pos = m.end()
        if pos < len(msg):
            remaining = msg[pos:]
            if tag:
                self.console.insert(tk.END, remaining, tag)
            else:
                self.console.insert(tk.END, remaining)
        self.console.insert(tk.END, "\n")
        self.console.see(tk.END)
        self.console.config(state=tk.DISABLED)

    def set_progress(self, current, total, text=""):
        self._last_current = current
        self._last_total = total
        if self.prog_canvas.winfo_width() <= 1:
            return
        w = self.prog_canvas.winfo_width()
        frac = min(current / total, 1.0) if total > 0 else 0
        fill_w = int(w * frac)
        self.prog_canvas.coords(self.prog_bar_fg, 0, 0, fill_w, 26)
        self.prog_canvas.coords(self.prog_bar_bg, 0, 0, w, 26)

        if current > 0:
            if self._task_start:
                elapsed = time.time() - self._task_start
                eta = (elapsed / current) * (total - current)
                total_est = elapsed + eta
                timing = f"  {self._fmt_time(elapsed)} / {self._fmt_time(total_est)}"
            else:
                timing = ""
            label_text = f"{current} / {total}  ({int(frac * 100)}%){timing}"
        else:
            label_text = ""
        self.prog_canvas.itemconfig(self.prog_text, text=label_text)
        if text:
            self.prog_label.config(text=text)

    @staticmethod
    def _fmt_time(seconds):
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def _poll_queue(self):
        has_update = False
        try:
            while True:
                item = _gui_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log_console(item[1], item[2])
                elif kind == "progress":
                    self.set_progress(item[1], item[2], item[3] if len(item) > 3 else "")
                has_update = True
        except queue.Empty:
            pass
        if self._task_start and not has_update:
            self.set_progress(self._last_current, self._last_total)
        self.root.after(250, self._poll_queue)

    def _open_app_folder(self):
        if sys.platform == "win32":
            os.startfile(SCRIPT_DIR)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", SCRIPT_DIR])
        else:
            subprocess.Popen(["xdg-open", SCRIPT_DIR])

    def _open_url(self, url):
        if sys.platform == "win32":
            os.startfile(url)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])

    def _about_github(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Github")
        dlg.resizable(False, False)
        self._show_dialog(dlg, modal=True)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        url = "https://github.com/opheophe/APlayers"
        frm = tk.Frame(dlg, padx=20, pady=16)
        frm.pack()
        tk.Label(frm, text="APlayers", font=("", 11, "bold")).pack(pady=(0, 8))
        link = tk.Label(frm, text=url, fg="#33ccff", cursor="hand2", font=("", 9, "underline"))
        link.pack()
        link.bind("<Button-1>", lambda e: self._open_url(url))
        tk.Button(frm, text="Stäng", command=dlg.destroy, padx=16, pady=4, font=("", 9)).pack(pady=(12, 0))

        dlg.update_idletasks()
        w = dlg.winfo_width()
        h = dlg.winfo_height()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"+{x}+{y}")

    def _delete_matcher(self):
        if not messagebox.askyesno("Bekräfta", "Är du säker på att du vill ta bort alla matcher?\n\nDetta raderar Data.json.", parent=self.root):
            return
        try:
            os.remove(APlayers.DATA_FILE)
            APlayers.log("Data.json borttagen.", "BG")
        except FileNotFoundError:
            APlayers.log("Data.json finns inte.", "Y")
        except Exception as e:
            APlayers.log(f"Fel vid borttagning: {e}", "BR")
        self._update_counts()
        self._reload_lista()

    def _open_link(self, event):
        idx = self.console.index(f"@{event.x},{event.y}")
        for (start, end), url in self._link_ranges.items():
            if self.console.compare(start, "<=", idx) and self.console.compare(idx, "<=", end):
                os.startfile(url)
                return

    def _get_active_ids(self):
        return {l["id"] for l in APlayers.load_ligor() if l.get("active")}

    def _get_selected_ids(self):
        ligor = APlayers.load_ligor()
        sel = self._current_filter()
        matching = APlayers.filter_ligor(ligor, sel)
        return {l["id"] for l in matching if l.get("active")}

    def _current_filter(self):
        sel = {}
        for key, dd in self._sel_dropdowns.items():
            vals = dd.get_selected()
            sel[key] = vals if vals else None
        return sel

    def _on_filter_change(self, event=None):
        self._refresh_dropdowns()
        self._update_counts()
        self._save_filters()
        self._reload_lista()

    def _save_filters(self):
        APlayers.save_filters({key: dd.get_selected() for key, dd in self._sel_dropdowns.items()})

    def _reset_filters(self):
        for dd in self._sel_dropdowns.values():
            dd.set_selected([])
        self._refresh_dropdowns()
        self._update_counts()
        self._save_filters()
        self._reload_lista()

    def _set_lista_loading(self, loading):
        self.lista_tree.configure(style="Grey.Treeview" if loading else "")

    def _reload_lista(self):
        self._lista_generation += 1
        gen = self._lista_generation
        sel = self._current_filter()
        active_ids = self._get_active_ids()
        columns = APlayers.visible_columns(self._columns, "lista")
        self._set_lista_loading(True)
        t = threading.Thread(target=self._lista_worker, args=(gen, sel, active_ids, columns), daemon=True)
        t.start()

    def _lista_worker(self, gen, sel, active_ids, columns):
        data = APlayers.load_data()
        indices = [APlayers.COLUMN_KEYS.index(k) for k in columns]
        rows = []
        for full in APlayers.iter_export_rows(data, active_ids, sel):
            if gen != self._lista_generation:
                return
            rows.append([full[i] for i in indices])
        self._lista_queue.put((gen, columns, rows))

    def _poll_lista(self):
        try:
            while True:
                gen, columns, rows = self._lista_queue.get_nowait()
                if gen == self._lista_generation:
                    self._apply_lista(gen, columns, rows)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_lista)

    def _apply_lista(self, gen, columns, rows):
        if gen != self._lista_generation:
            return
        self._set_lista_loading(False)
        self._lista_columns = columns
        self._lista_rows = rows
        self.lista_tree["columns"] = columns
        for key in columns:
            self.lista_tree.heading(key, text=APlayers.COLUMN_LABELS[key])
            self.lista_tree.column(key, minwidth=20, stretch=False)
        self._populate_lista()
        self._autosize_lista_columns()

    def _autosize_lista_columns(self):
        font = tkfont.nametofont("TkDefaultFont")
        for key in self._lista_columns:
            idx = self._lista_columns.index(key)
            max_w = font.measure(APlayers.COLUMN_LABELS[key]) + 24
            for r in self._lista_rows:
                v = r[idx]
                text = "🔗" if (key == "url" and v) else str(v)
                w = font.measure(text)
                if w > max_w:
                    max_w = w
            self.lista_tree.column(key, width=max_w + 16, minwidth=20, stretch=False)

    def _populate_lista(self):
        tree = self.lista_tree
        tree.delete(*tree.get_children())
        rows = self._lista_rows
        col = self._lista_sort_col
        if col in self._lista_columns:
            idx = self._lista_columns.index(col)

            def key(r):
                v = r[idx]
                try:
                    return (0, float(v))
                except (ValueError, TypeError):
                    return (1, str(v).lower())

            rows = sorted(rows, key=key, reverse=self._lista_sort_reverse)
        url_idx = self._lista_columns.index("url") if "url" in self._lista_columns else None
        self._lista_url_by_iid = {}
        for r in rows:
            values = list(r)
            url = ""
            if url_idx is not None:
                url = values[url_idx]
                values[url_idx] = "🔗" if url else ""
            iid = tree.insert("", "end", values=values)
            if url:
                self._lista_url_by_iid[iid] = url

    def _on_lista_header_click(self, event):
        if self.lista_tree.identify("region", event.x, event.y) != "heading":
            return
        col_id = self.lista_tree.identify_column(event.x)
        idx = int(col_id[1:]) - 1
        if idx < 0 or idx >= len(self._lista_columns):
            return
        key = self._lista_columns[idx]
        if self._lista_sort_col == key:
            self._lista_sort_reverse = not self._lista_sort_reverse
        else:
            self._lista_sort_col = key
            self._lista_sort_reverse = False
        self._populate_lista()

    def _on_lista_cell_click(self, event):
        if self.lista_tree.identify("region", event.x, event.y) != "cell":
            return
        row = self.lista_tree.identify_row(event.y)
        col_id = self.lista_tree.identify_column(event.x)
        idx = int(col_id[1:]) - 1
        if not row or idx < 0 or idx >= len(self._lista_columns):
            return
        if self._lista_columns[idx] == "url":
            url = self._lista_url_by_iid.get(row, "")
            if url:
                self._open_url(url)

    def _show_dialog(self, dlg, modal=False):
        dlg.transient(self.root)
        dlg.lift()
        try:
            dlg.attributes("-topmost", True)
        except Exception:
            pass
        if modal:
            dlg.grab_set()
            dlg.focus_force()
        else:
            dlg.focus_force()
            dlg.after(150, lambda: self._release_topmost(dlg))

    @staticmethod
    def _release_topmost(dlg):
        try:
            if dlg.winfo_exists():
                dlg.attributes("-topmost", False)
        except Exception:
            pass

    def _refresh_dropdowns(self):
        ligor = APlayers.load_ligor()
        active = [l for l in ligor if l.get("active")]
        fields = ("responsible", "liga", "year", "country")
        attr_map = {"responsible": "responsible", "liga": "name",
                    "year": "year", "country": "country"}
        for key in fields:
            sel = {}
            for other in fields:
                if other == key:
                    continue
                vals = self._sel_dropdowns[other].get_selected()
                sel[other] = vals if vals else None
            matches = APlayers.filter_ligor(active, sel)
            attr = attr_map[key]
            values = sorted({l.get(attr, "") for l in matches if l.get(attr)})
            self._sel_dropdowns[key].set_options(values)

        self._refresh_player_dropdowns()

    def _matching_rows(self, sel):
        ligor = APlayers.load_ligor()
        active_ids = {l["id"] for l in ligor if l.get("active")}
        data = APlayers.load_data()
        for lid, league in data.get("Leagues", {}).items():
            if not APlayers.match_filter_league(league, active_ids, sel):
                continue
            for gid, game in league.get("Games", {}).items():
                if game.get("Status") != "success":
                    continue
                for p in game.get("Lineup", []):
                    if len(p) >= 10 and APlayers.match_filter_player(p, sel):
                        yield p

    def _refresh_player_dropdowns(self):
        sections = ["Starting Line-up", "Substitutes", "Manager"]
        self._sel_dropdowns["section"].set_options(sections)

        base = self._current_filter()

        pos_sel = dict(base)
        pos_sel["position"] = None
        pos_sel["age"] = None
        positions = sorted({p[5] for p in self._matching_rows(pos_sel) if p[5] and p[5] != "Manager"})
        self._sel_dropdowns["position"].set_options(positions)

        base = self._current_filter()
        age_sel = dict(base)
        age_sel["age"] = None
        ages = sorted({p[7] for p in self._matching_rows(age_sel) if p[7]},
                      key=lambda a: int(a) if a.isdigit() else 0)
        self._sel_dropdowns["age"].set_options(ages)

    def _update_counts(self):
        data = APlayers.load_data()
        ligor = APlayers.load_ligor()
        sel = self._current_filter()
        matching = APlayers.filter_ligor(ligor, sel)
        total_ligor = len(matching)
        active_ligor = sum(1 for l in matching if l.get("active"))
        active_ids = {l["id"] for l in matching if l.get("active")}

        total = 0
        pending = 0
        success = 0
        for lid, league in data.get("Leagues", {}).items():
            if league.get("Id") not in active_ids:
                continue
            for gid, game in league.get("Games", {}).items():
                total += 1
                status = game.get("Status")
                if status == "pending":
                    pending += 1
                elif status == "success":
                    success += 1

        lineup_entries = 0
        distinct_players = set()
        for p in self._matching_rows(sel):
            lineup_entries += 1
            if len(p) >= 10 and p[4]:
                distinct_players.add(p[4])

        players = data.get("Players", {})
        detailed = sum(1 for p in players.values() if p.get("Detailed"))
        self._info_vars["leagues"].set(f"{active_ligor}/{total_ligor} aktiva")
        self._info_vars["matches"].set(f"{total} (i kö: {pending})")
        self._info_vars["lineups"].set(f"{success} hämtade")
        self._info_vars["lineup_players"].set(f"{len(distinct_players)}")
        self._info_vars["rows"].set(f"{lineup_entries}")
        self._info_vars["players"].set(f"{len(players)} (i kö: {len(players) - detailed})")

    def _view_pending(self):
        data = APlayers.load_data()
        pending = []
        for lid, league in data.get("Leagues", {}).items():
            for gid, game in league.get("Games", {}).items():
                if game.get("Status") == "pending":
                    pending.append((league.get("Name", "?"), game.get("Url", "")))
        if not pending:
            messagebox.showinfo("Matcher i kö", "Inga matcher i kö.", parent=self.root)
            return

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Matcher i kö ({len(pending)})")
        dlg.geometry("750x400")
        self._show_dialog(dlg)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        text = scrolledtext.ScrolledText(dlg, bg="#1a1a1a", fg="#cccccc",
                                         font=("Consolas", 9), wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        text.tag_config("link", foreground="#33ccff", underline=True)
        text.insert(tk.END, f"{'Liga':<25} {'URL'}\n")
        text.insert(tk.END, "-" * 70 + "\n")
        for name, url in pending:
            text.insert(tk.END, f"{name:<25} ")
            text.insert(tk.END, url, "link")
            text.insert(tk.END, "\n")
        text.config(state=tk.DISABLED)

    def _view_stats(self):
        data = APlayers.load_data()
        stats = {}
        for lid, league in data.get("Leagues", {}).items():
            name = league.get("Name", "?")
            if name not in stats:
                stats[name] = {"total": 0, "pending": 0, "success": 0}
            for gid, game in league.get("Games", {}).items():
                stats[name]["total"] += 1
                if game.get("Status") == "pending":
                    stats[name]["pending"] += 1
                elif game.get("Status") == "success":
                    stats[name]["success"] += 1

        dlg = tk.Toplevel(self.root)
        dlg.title("Ligastatistik")
        dlg.geometry("500x400")
        self._show_dialog(dlg)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        text = scrolledtext.ScrolledText(dlg, bg="#1a1a1a", fg="#cccccc",
                                         font=("Consolas", 9), wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        text.insert(tk.END, f"{'Liga':<25} {'Matcher':>7} {'I kö':>7} {'Hämtade':>8}\n")
        text.insert(tk.END, "-" * 50 + "\n")
        for name in sorted(stats):
            s = stats[name]
            text.insert(tk.END, f"{name:<25} {s['total']:>7} {s['pending']:>7} {s['success']:>8}\n")
        total_all = sum(s["total"] for s in stats.values())
        total_pend = sum(s["pending"] for s in stats.values())
        total_succ = sum(s["success"] for s in stats.values())
        text.insert(tk.END, "-" * 50 + "\n")
        text.insert(tk.END, f"{'Totalt':<25} {total_all:>7} {total_pend:>7} {total_succ:>8}\n")
        text.config(state=tk.DISABLED)

    def _manage_ligor(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Hantera ligor")
        dlg.geometry("1020x600")
        self._show_dialog(dlg)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        self._ligor_list = APlayers.load_ligor()
        self._edit_id = None

        tree_frame = tk.Frame(dlg)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))

        cols = ("id", "url", "name", "country", "year", "responsible", "active")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=14)
        headers = {"id": "Id", "url": "URL", "name": "Namn", "country": "Land",
                   "year": "År", "responsible": "Ansvarig", "active": "Aktiv"}
        widths = {"id": 45, "url": 330, "name": 170, "country": 140, "year": 45,
                  "responsible": 110, "active": 60}
        for c in cols:
            tree.heading(c, text=headers[c])
            anchor = "center" if c in ("id", "year", "active") else "w"
            tree.column(c, width=widths[c], anchor=anchor)
        tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=sb.set)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        def refresh_tree():
            tree.delete(*tree.get_children())
            for l in self._ligor_list:
                tree.insert("", "end", iid=str(l["id"]),
                            values=(l["id"], l.get("url", ""), l.get("name", ""),
                                    l.get("country", ""), l.get("year", ""),
                                    l.get("responsible", ""), "✓" if l.get("active", True) else ""))
        refresh_tree()

        form = tk.LabelFrame(dlg, text="Liga", padx=10, pady=8)
        form.pack(fill=tk.X, padx=10, pady=(10, 0))

        url_var = tk.StringVar()
        name_var = tk.StringVar()
        country_var = tk.StringVar()
        year_var = tk.StringVar()
        responsible_var = tk.StringVar(value=APlayers.DEFAULT_RESPONSIBLE)
        active_var = tk.BooleanVar(value=True)

        tk.Label(form, text="URL:", font=("", 9)).grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)
        url_entry = tk.Entry(form, textvariable=url_var, width=60, font=("", 9))
        url_entry.grid(row=0, column=1, columnspan=3, sticky="w", pady=2)
        tk.Button(form, text="Hämta info", command=lambda: autofetch()).grid(row=0, column=4, padx=8)

        tk.Label(form, text="Namn:", font=("", 9)).grid(row=1, column=0, sticky="e", padx=(0, 6), pady=2)
        tk.Entry(form, textvariable=name_var, width=24, font=("", 9)).grid(row=1, column=1, sticky="w", pady=2)
        tk.Label(form, text="Land:", font=("", 9)).grid(row=1, column=2, sticky="e", padx=(12, 6), pady=2)
        tk.Entry(form, textvariable=country_var, width=20, font=("", 9)).grid(row=1, column=3, sticky="w", pady=2)

        tk.Label(form, text="År:", font=("", 9)).grid(row=2, column=0, sticky="e", padx=(0, 6), pady=2)
        tk.Entry(form, textvariable=year_var, width=8, font=("", 9)).grid(row=2, column=1, sticky="w", pady=2)
        tk.Label(form, text="Ansvarig:", font=("", 9)).grid(row=2, column=2, sticky="e", padx=(12, 6), pady=2)
        tk.Entry(form, textvariable=responsible_var, width=20, font=("", 9)).grid(row=2, column=3, sticky="w", pady=2)

        tk.Checkbutton(form, text="Aktiv", variable=active_var, font=("", 9)).grid(row=1, column=4, sticky="w", padx=8)

        def clear_form():
            self._edit_id = None
            url_var.set("")
            name_var.set("")
            country_var.set("")
            year_var.set("")
            responsible_var.set(APlayers.DEFAULT_RESPONSIBLE)
            active_var.set(True)

        def autofetch():
            url = url_var.get().strip()
            if not url:
                return

            def worker():
                name, country, year = "", "", ""
                try:
                    name, country, year = APlayers.fetch_league_info(url)
                except Exception:
                    pass
                if not year:
                    m = re.search(r"saison_id/(\d{4})", url)
                    year = m.group(1) if m else ""

                def apply():
                    if name:
                        name_var.set(name)
                    if country:
                        country_var.set(country)
                    if year:
                        year_var.set(year)
                dlg.after(0, apply)
            threading.Thread(target=worker, daemon=True).start()

        def save_liga():
            url = url_var.get().strip()
            if not url:
                messagebox.showwarning("Saknas", "Ange en URL.", parent=dlg)
                return
            liga = {
                "url": url,
                "name": name_var.get().strip(),
                "country": country_var.get().strip(),
                "year": year_var.get().strip(),
                "responsible": responsible_var.get().strip() or APlayers.DEFAULT_RESPONSIBLE,
                "active": active_var.get(),
            }
            if self._edit_id is not None:
                for l in self._ligor_list:
                    if l["id"] == self._edit_id:
                        l.update(liga)
                        break
            else:
                liga["id"] = APlayers.next_liga_id(self._ligor_list)
                self._ligor_list.append(liga)
            APlayers.save_ligor(self._ligor_list)
            refresh_tree()
            clear_form()
            self._refresh_dropdowns()
            self._update_counts()

        def delete_liga():
            sel = tree.selection()
            if not sel:
                return
            if not messagebox.askyesno("Ta bort", "Ta bort vald liga?", parent=dlg):
                return
            self._ligor_list = [l for l in self._ligor_list if str(l["id"]) != sel[0]]
            APlayers.save_ligor(self._ligor_list)
            refresh_tree()
            if self._edit_id is not None and str(self._edit_id) == sel[0]:
                clear_form()
            self._refresh_dropdowns()
            self._update_counts()

        def on_select(event):
            sel = tree.selection()
            if not sel:
                return
            liga = next((l for l in self._ligor_list if str(l["id"]) == sel[0]), None)
            if not liga:
                return
            self._edit_id = liga["id"]
            url_var.set(liga.get("url", ""))
            name_var.set(liga.get("name", ""))
            country_var.set(liga.get("country", ""))
            year_var.set(liga.get("year", ""))
            responsible_var.set(liga.get("responsible", APlayers.DEFAULT_RESPONSIBLE))
            active_var.set(bool(liga.get("active", True)))

        tree.bind("<<TreeviewSelect>>", on_select)

        btn_bar = tk.Frame(dlg)
        btn_bar.pack(fill=tk.X, padx=10, pady=10)
        tk.Button(btn_bar, text="Ny", command=clear_form, padx=16, pady=4, font=("", 9, "bold")).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_bar, text="Spara", command=save_liga, padx=16, pady=4, font=("", 9, "bold")).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_bar, text="Ta bort", command=delete_liga, padx=16, pady=4, font=("", 9)).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_bar, text="Stäng", command=dlg.destroy, padx=16, pady=4, font=("", 9)).pack(side=tk.RIGHT, padx=4)

    def _toggle_refetch_players(self):
        APlayers.REFETCH_PLAYERS = self._refetch_players_var.get()
        APlayers.save_settings()

    def _settings_columns(self):
        cols = APlayers.load_columns()
        dlg = tk.Toplevel(self.root)
        dlg.title("Kolumner")
        dlg.resizable(False, False)
        self._show_dialog(dlg, modal=True)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        frm = tk.Frame(dlg, padx=12, pady=8)
        frm.pack()

        tk.Label(frm, text="Kolumn", font=("", 9, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 16))
        tk.Label(frm, text="Order", font=("", 9, "bold")).grid(row=0, column=1, padx=6)
        tk.Label(frm, text="Lista", font=("", 9, "bold")).grid(row=0, column=2, padx=8)
        tk.Label(frm, text="Excel", font=("", 9, "bold")).grid(row=0, column=3, padx=8)

        vars_map = {}
        for i, key in enumerate(APlayers.COLUMN_KEYS):
            tk.Label(frm, text=APlayers.COLUMN_LABELS[key], font=("", 9)).grid(
                row=i + 1, column=0, sticky="w", pady=1)
            vorder = tk.StringVar(value=str(cols[key]["order"]))
            vl = tk.BooleanVar(value=cols[key]["lista"])
            ve = tk.BooleanVar(value=cols[key]["excel"])
            vars_map[key] = (vorder, vl, ve)
            tk.Entry(frm, textvariable=vorder, width=4, font=("", 9), justify="center").grid(
                row=i + 1, column=1, pady=1)
            tk.Checkbutton(frm, variable=vl).grid(row=i + 1, column=2)
            tk.Checkbutton(frm, variable=ve).grid(row=i + 1, column=3)

        def save_columns():
            for key, (vorder, vl, ve) in vars_map.items():
                cols[key]["lista"] = vl.get()
                cols[key]["excel"] = ve.get()
                try:
                    order = int(vorder.get().strip())
                    if order < 1:
                        order = 1
                except ValueError:
                    order = (APlayers.COLUMN_KEYS.index(key) + 1) * 10
                cols[key]["order"] = order
            APlayers.save_columns(cols)
            self._columns = cols
            dlg.destroy()
            self._reload_lista()

        tk.Button(frm, text="Spara", command=save_columns, padx=16, pady=4,
                  font=("", 9, "bold")).grid(row=len(APlayers.COLUMN_KEYS) + 1, column=0, columnspan=4, pady=(10, 0))

        dlg.update_idletasks()
        w = dlg.winfo_width()
        h = dlg.winfo_height()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"+{x}+{y}")

    def _toggle_sound(self):
        APlayers.SOUND = self._sound_var.get()
        APlayers.save_settings()

    def _settings_event_labels(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Event labels")
        dlg.resizable(False, False)
        self._show_dialog(dlg, modal=True)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        frm = tk.Frame(dlg, padx=12, pady=8)
        frm.pack()

        tk.Label(frm, text="Labels for sb-sprite events in exports:", font=("", 9, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        entries = {}
        for i, (key, label) in enumerate(APlayers.EVENT_LABELS.items()):
            tk.Label(frm, text=f"{key}:", font=("", 9)).grid(row=i + 1, column=0, sticky="e", padx=(0, 6), pady=2)
            var = tk.StringVar(value=label)
            entries[key] = var
            tk.Entry(frm, textvariable=var, width=16, font=("", 9)).grid(row=i + 1, column=1, pady=2)

        def save_labels():
            for key, var in entries.items():
                APlayers.EVENT_LABELS[key] = var.get()
            APlayers.save_settings()
            dlg.destroy()

        btn_frm = tk.Frame(frm)
        btn_frm.grid(row=len(entries) + 1, column=0, columnspan=2, pady=(10, 0))
        tk.Button(btn_frm, text="Save", command=save_labels, padx=16, pady=4,
                  font=("", 9, "bold")).pack()

        dlg.update_idletasks()
        w = dlg.winfo_width()
        h = dlg.winfo_height()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"+{x}+{y}")

    def _settings_delay(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Fördröjning (ms)")
        dlg.geometry("250x100")
        dlg.resizable(False, False)
        self._show_dialog(dlg, modal=True)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        tk.Label(dlg, text="Fördröjning mellan anrop (ms):").pack(pady=(10, 4))
        var = tk.StringVar(value=str(APlayers.DELAY_MS))
        entry = tk.Entry(dlg, textvariable=var, width=10, justify="center")
        entry.pack()
        entry.select_range(0, tk.END)

        def save_delay():
            try:
                val = int(var.get())
                if val < 500:
                    val = 500
                APlayers.DELAY_MS = val
                APlayers.save_settings()
                dlg.destroy()
            except ValueError:
                pass

        btn = tk.Button(dlg, text="Spara", command=save_delay)
        btn.pack(pady=8)
        entry.bind("<Return>", lambda e: save_delay())
        entry.focus_set()

    def _settings_retries(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Antal försök")
        dlg.geometry("250x100")
        dlg.resizable(False, False)
        self._show_dialog(dlg, modal=True)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        tk.Label(dlg, text="Max antal försök:").pack(pady=(10, 4))
        var = tk.StringVar(value=str(APlayers.RETRIES))
        entry = tk.Entry(dlg, textvariable=var, width=10, justify="center")
        entry.pack()
        entry.select_range(0, tk.END)

        def save_retries():
            try:
                val = int(var.get())
                if val < 1:
                    val = 1
                if val > 50:
                    val = 50
                APlayers.RETRIES = val
                APlayers.save_settings()
                dlg.destroy()
            except ValueError:
                pass

        btn = tk.Button(dlg, text="Save", command=save_retries)
        btn.pack(pady=8)
        entry.bind("<Return>", lambda e: save_retries())
        entry.focus_set()

    def _settings_age_cutoff(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Spelar ålder-cutoff")
        dlg.geometry("250x100")
        dlg.resizable(False, False)
        self._show_dialog(dlg, modal=True)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        tk.Label(dlg, text="Ålder-cutoff (år):").pack(pady=(10, 4))
        var = tk.StringVar(value=str(APlayers.PLAYER_AGE_CUTOFF))
        entry = tk.Entry(dlg, textvariable=var, width=10, justify="center")
        entry.pack()
        entry.select_range(0, tk.END)

        def save_age_cutoff():
            try:
                val = int(var.get())
                APlayers.PLAYER_AGE_CUTOFF = val
                APlayers.save_settings()
                dlg.destroy()
            except ValueError:
                pass

        btn = tk.Button(dlg, text="Spara", command=save_age_cutoff)
        btn.pack(pady=8)
        entry.bind("<Return>", lambda e: save_age_cutoff())
        entry.focus_set()

    # --- Button state management ---
    def _set_buttons(self, running):
        self._running = running
        state = tk.DISABLED if running else tk.NORMAL
        self.btn_ligor.config(state=state)
        self.btn_matcher.config(state=state)
        self.btn_spelare.config(state=state)
        self.btn_excel.config(state=state)
        self.btn_abort.config(state=tk.NORMAL if running else tk.DISABLED)

    def abort(self):
        _abort_event.set()
        APlayers.log("Avbryter...", "BR")
        self.btn_abort.config(state=tk.DISABLED)

    # --- Thread launchers ---
    def start_hamta_ligor(self):
        self._task_start = time.time()
        self._set_buttons(True)
        _abort_event.clear()
        t = threading.Thread(target=self._run_hamta_ligor, daemon=True)
        t.start()

    def start_hamta_matcher(self):
        self._task_start = time.time()
        self._set_buttons(True)
        _abort_event.clear()
        t = threading.Thread(target=self._run_hamta_matcher, daemon=True)
        t.start()

    def start_hamta_spelare(self):
        self._set_buttons(True)
        _abort_event.clear()
        t = threading.Thread(target=self._run_hamta_spelare, daemon=True)
        t.start()

    @staticmethod
    def _open_folder(filepath):
        folder = os.path.dirname(os.path.abspath(filepath))
        if sys.platform == "win32":
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def _ask_open_folder(self, filepath):
        dlg = tk.Toplevel(self.root)
        dlg.title("Export klar")
        dlg.resizable(False, False)
        self._show_dialog(dlg, modal=True)

        frm = tk.Frame(dlg, padx=16, pady=12)
        frm.pack()

        tk.Label(frm, text="Filen sparades.\n\nVill du...", font=("", 10)).pack(pady=(0, 10))

        btn_frm = tk.Frame(frm)
        btn_frm.pack()

        def open_file():
            dlg.destroy()
            if sys.platform == "win32":
                os.startfile(filepath)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", filepath])
            else:
                subprocess.Popen(["xdg-open", filepath])

        def open_folder():
            dlg.destroy()
            self._open_folder(filepath)

        tk.Button(btn_frm, text="Öppna Filen", command=open_file, padx=12, pady=4,
                  font=("", 9, "bold")).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frm, text="Öppna Mappen", command=open_folder, padx=12, pady=4,
                  font=("", 9, "bold")).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_frm, text="Nej", command=dlg.destroy, padx=16, pady=4,
                  font=("", 9)).pack(side=tk.LEFT, padx=4)

        dlg.update_idletasks()
        w = dlg.winfo_width()
        h = dlg.winfo_height()
        x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
        dlg.geometry(f"+{x}+{y}")

        dlg.wait_window()

    # --- Worker: Hamta ligor ---
    def _run_hamta_ligor(self):
        try:
            APlayers.log("=== Hämtar ligor ===", "C")
            ligor = [l for l in APlayers.filter_ligor(APlayers.load_ligor(), self._current_filter()) if l.get("active")]
            if not ligor:
                APlayers.log("Inga aktiva ligor.", "Y")
                return
            APlayers.log(f"Laddade {len(ligor)} aktiva ligor")

            data = APlayers.load_data()
            total_leagues = len(ligor)
            new_games = 0

            for i, liga in enumerate(ligor):
                if _abort_event.is_set():
                    APlayers.log("Avbruten av användare.", "BR")
                    break
                name = liga.get("name") or liga.get("country") or "?"
                put_progress(i + 1, total_leagues, f"Hämtar {name}...")
                league = APlayers.ensure_league(data, liga)
                match_urls = APlayers.extract_result_links(name, liga.get("url", ""))
                new_count = 0
                games = league.setdefault("Games", {})
                for m in match_urls:
                    gid = m.rstrip("/").rsplit("/", 1)[-1]
                    if gid not in games:
                        games[gid] = {"Url": m, "Status": "pending"}
                        new_games += 1
                        new_count += 1
                tag = f"{new_count} nya" if new_count else "0 nya"
                APlayers.log(f"  {len(match_urls)} på sida, {tag}", "B")

            APlayers.save_data(data)
            APlayers.log(f"{new_games} nya matcher tillagda.", "C")
            APlayers.log("Klart.", "BG")
            play_success()
        except Exception as e:
            APlayers.log(f"Fel: {e}", "BR")
        finally:
            self.root.after(0, lambda: self._set_buttons(False))
            self.root.after(0, lambda: setattr(self, '_task_start', None))
            self.root.after(0, self._refresh_dropdowns)
            self.root.after(0, self._update_counts)
            self.root.after(0, self._reload_lista)
            put_progress(0, 1, "Redo")

    # --- Worker: Hamta matcher ---
    def _run_hamta_matcher(self):
        try:
            APlayers.log("=== Hämtar matcher ===", "C")
            data = APlayers.load_data()
            active_ids = self._get_selected_ids()

            pending = []
            for lid, league in data.get("Leagues", {}).items():
                if league.get("Id") not in active_ids:
                    continue
                for gid, game in league.get("Games", {}).items():
                    if game.get("Status") == "pending":
                        pending.append((league, game))

            if not pending:
                APlayers.log("Inga matcher i kö.", "BG")
                return

            total = len(pending)
            success = 0
            fail = 0

            for i, (league, game) in enumerate(pending):
                if _abort_event.is_set():
                    APlayers.log("Avbruten av användare.", "BR")
                    break

                country = league.get("Country", "?")
                url = game.get("Url", "")
                name = league.get("Name", "?")
                put_progress(i + 1, total, f"Behandlar {name}...")

                rows, meta = APlayers.parse_match(country, url, compact=True)

                if meta is None:
                    fail += 1
                    APlayers.log("  MISSLYCKADES", "Y")
                    continue

                if rows:
                    game["Status"] = "success"
                    game["Meta"] = meta
                    game["Lineup"] = rows
                    APlayers.add_players_from_lineup(data, rows)
                    success += 1
                else:
                    fail += 1
                    APlayers.log("  Ingen data", "Y")

                APlayers.save_data(data)

            APlayers.log(f"Resultat: {success} lyckade, {fail} misslyckades (av {total}).", "BG" if fail == 0 else "Y")
            play_success()
        except Exception as e:
            APlayers.log(f"Fel: {e}", "BR")
        finally:
            self.root.after(0, lambda: self._set_buttons(False))
            self.root.after(0, lambda: setattr(self, '_task_start', None))
            self.root.after(0, self._refresh_dropdowns)
            self.root.after(0, self._update_counts)
            self.root.after(0, self._reload_lista)
            put_progress(0, 1, "Redo")

    # --- Worker: Hamta spelare ---
    def _run_hamta_spelare(self):
        try:
            data = APlayers.load_data()
            added = APlayers.collect_players(data)
            APlayers.save_data(data)
            if added:
                APlayers.log(f"Lade till {added} spelare i spellistan.", "BG")
            else:
                APlayers.log("Inga nya spelare.", "Y")

            cutoff = APlayers.PLAYER_AGE_CUTOFF
            fetched = APlayers.fetch_player_details(
                data, cutoff,
                progress_cb=lambda cur, tot, name: put_progress(cur, tot, f"Hämtar detaljer: {name}"),
                refetch=APlayers.REFETCH_PLAYERS
            )
            APlayers.save_data(data)
            if fetched:
                APlayers.log(f"Hämtade detaljer för {fetched} spelare.", "BG")
            else:
                APlayers.log("Inga spelare att hämta detaljer för.", "Y")
        except Exception as e:
            APlayers.log(f"Fel: {e}", "BR")
        finally:
            self.root.after(0, lambda: self._set_buttons(False))
            self.root.after(0, self._refresh_dropdowns)
            self.root.after(0, self._update_counts)
            self.root.after(0, self._reload_lista)
            put_progress(0, 1, "Redo")

    # --- Worker: Export excel ---
    def start_export_excel(self):
        data = APlayers.load_data()
        active_ids = self._get_active_ids()
        sel = self._current_filter()
        success_count = sum(
            1 for lid, league in data.get("Leagues", {}).items()
            if APlayers.match_filter_league(league, active_ids, sel)
            for gid, game in league.get("Games", {}).items() if game.get("Status") == "success"
        )
        if success_count == 0:
            APlayers.log("Inga hämtade matcher att exportera.", "Y")
            return

        now = datetime.now()
        default_name = f"Output {now.strftime('%Y-%m-%d')}.xlsx"
        filepath = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save Excel",
            initialfile=default_name,
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")]
        )

        if not filepath:
            APlayers.log("Excel-export avbruten.", "Y")
            return

        if os.path.exists(filepath):
            if not messagebox.askyesno("Skriv över?", f"'{os.path.basename(filepath)}' finns redan.\nSkriv över?", parent=self.root):
                APlayers.log("Excel-export avbruten.", "Y")
                return

        self._set_buttons(True)
        _abort_event.clear()
        t = threading.Thread(target=self._run_export_excel, args=(data, active_ids, sel, filepath), daemon=True)
        t.start()

    def _run_export_excel(self, data, active_ids, sel, filepath):
        try:
            written = APlayers.export_excel(
                data, filepath, active_ids=active_ids, sel=sel,
                columns=APlayers.visible_columns(self._columns, "excel")
            )
            APlayers.log(f"Exporterade {written} rader till {filepath}", "BG")
            self.root.after(0, lambda fp=filepath: self._ask_open_folder(fp))
        except ImportError as e:
            APlayers.log(str(e), "BR")
        except PermissionError:
            self.root.after(0, lambda: messagebox.showerror(
                "Åtkomst nekad",
                "Kan inte spara filen!\n\n"
                "Filen är troligtvis öppen i ett annat program.\n"
                "Stäng filen och försök igen.", parent=self.root))
            APlayers.log("Fel: Filen är upptagen eller skrivskyddad.", "BR")
        except Exception as e:
            APlayers.log(f"Fel: {e}", "BR")
        finally:
            self.root.after(0, lambda: self._set_buttons(False))

    def _on_close(self):
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            if w > 100 and h > 100:
                APlayers.save_window_size(w, h)
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def run():
    APlayers.load_settings()
    app = APlayersGUI()
    app.log_console("=== APlayers redo ===", "C")
    app._update_counts()
    app._reload_lista()
    app.run()


if __name__ == "__main__":
    run()
