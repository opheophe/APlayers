import os
import re
import time
import queue
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from datetime import datetime

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

CSV_HEADER = APlayers.CSV_HEADER


class APlayersGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("APlayers")
        self.root.geometry("900x650")
        self.root.minsize(700, 500)
        if os.path.exists(ICON_PATH):
            self.root.iconbitmap(ICON_PATH)
        self._running = False
        self._task_start = None
        self._last_current = 0
        self._last_total = 1

        # --- Menu bar ---
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        files_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Filer", menu=files_menu)
        files_menu.add_command(label="Ligor.txt", command=self._open_ligor)

        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Visa", menu=view_menu)
        view_menu.add_command(label="Matcher i kö", command=self._view_pending)
        view_menu.add_command(label="Ligastatistik", command=self._view_stats)

        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Inställningar", menu=settings_menu)
        settings_menu.add_command(label="Fördröjning...", command=self._settings_delay)
        settings_menu.add_command(label="Försök...", command=self._settings_retries)

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

        # --- Info labels ---
        info_frame = tk.Frame(self.root)
        info_frame.pack(fill=tk.X, padx=10, pady=(6, 0))

        self.lbl_leagues = tk.Label(info_frame, text="Ligor: -", font=("", 9))
        self.lbl_leagues.pack(side=tk.LEFT, padx=(0, 20))
        self.lbl_matches = tk.Label(info_frame, text="Matcher: -", font=("", 9))
        self.lbl_matches.pack(side=tk.LEFT, padx=(0, 20))
        self.lbl_lineups = tk.Label(info_frame, text="Lineups: -", font=("", 9))
        self.lbl_lineups.pack(side=tk.LEFT)

        # --- Buttons ---
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill=tk.X, padx=10, pady=8)

        self.btn_ligor = tk.Button(btn_frame, text="Hämta ligor", command=self.start_hamta_ligor,
                                   bg="#0066cc", fg="white", padx=14, pady=4, font=("", 9, "bold"))
        self.btn_ligor.pack(side=tk.LEFT, padx=4)

        self.btn_matcher = tk.Button(btn_frame, text="Hämta matcher", command=self.start_hamta_matcher,
                                     bg="#009999", fg="white", padx=14, pady=4, font=("", 9, "bold"))
        self.btn_matcher.pack(side=tk.LEFT, padx=4)

        self.btn_csv = tk.Button(btn_frame, text="Hämta CSV", command=self.start_hamta_csv,
                                 bg="#00aa00", fg="white", padx=14, pady=4, font=("", 9, "bold"))
        self.btn_csv.pack(side=tk.LEFT, padx=4)

        self.btn_abort = tk.Button(btn_frame, text="Avbryt", command=self.abort,
                                   bg="#cc0000", fg="white", padx=14, pady=4, font=("", 9, "bold"),
                                   state=tk.DISABLED)
        self.btn_abort.pack(side=tk.LEFT, padx=4)

        # --- Console ---
        console_frame = tk.Frame(self.root)
        console_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))

        self.console = scrolledtext.ScrolledText(
            console_frame, bg="#1a1a1a", fg="#cccccc",
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

        if current > 0 and self._task_start:
            elapsed = time.time() - self._task_start
            eta = (elapsed / current) * (total - current)
            total_est = elapsed + eta
            timing = f"  {self._fmt_time(elapsed)} / {self._fmt_time(total_est)}"
        else:
            timing = ""

        label_text = f"{current} / {total}  ({int(frac * 100)}%){timing}"
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

    def _open_ligor(self):
        os.startfile(APlayers.LIGOR_PATH)

    def _open_link(self, event):
        idx = self.console.index(f"@{event.x},{event.y}")
        for (start, end), url in self._link_ranges.items():
            if self.console.compare(start, "<=", idx) and self.console.compare(idx, "<=", end):
                os.startfile(url)
                return

    def _update_counts(self):
        matcher = APlayers.load_matcher()
        leagues = APlayers.load_leagues()
        total = len(matcher["matches"])
        pending = sum(1 for m in matcher["matches"] if m["status"] == "pending")
        success = sum(1 for m in matcher["matches"] if m["status"] == "success")
        self.lbl_leagues.config(text=f"Ligor: {len(leagues)}")
        self.lbl_matches.config(text=f"Matcher: {total} totalt, {pending} i kö")
        self.lbl_lineups.config(text=f"Lineups: {success} hämtade")

    def _view_pending(self):
        matcher = APlayers.load_matcher()
        pending = [m for m in matcher["matches"] if m["status"] == "pending"]
        if not pending:
            messagebox.showinfo("Matcher i kö", "Inga matcher i kö.")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title(f"Matcher i kö ({len(pending)})")
        dlg.geometry("750x400")
        dlg.transient(self.root)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        text = scrolledtext.ScrolledText(dlg, bg="#1a1a1a", fg="#cccccc",
                                         font=("Consolas", 9), wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        text.tag_config("link", foreground="#33ccff", underline=True)
        text.insert(tk.END, f"{'Land':<25} {'URL'}\n")
        text.insert(tk.END, "-" * 70 + "\n")
        for m in pending:
            country = m["country"]
            url = m["url"]
            lineup_url = url.replace("/index/", "/aufstellung/")
            text.insert(tk.END, f"{country:<25} ")
            text.insert(tk.END, url, "link")
            text.insert(tk.END, "\n")
        text.config(state=tk.DISABLED)

    def _view_stats(self):
        matcher = APlayers.load_matcher()
        stats = {}
        for m in matcher["matches"]:
            country = m["country"]
            if country not in stats:
                stats[country] = {"total": 0, "pending": 0, "success": 0}
            stats[country]["total"] += 1
            if m["status"] == "pending":
                stats[country]["pending"] += 1
            elif m["status"] == "success":
                stats[country]["success"] += 1

        dlg = tk.Toplevel(self.root)
        dlg.title("Ligastatistik")
        dlg.geometry("500x400")
        dlg.transient(self.root)
        if os.path.exists(ICON_PATH):
            dlg.iconbitmap(ICON_PATH)

        text = scrolledtext.ScrolledText(dlg, bg="#1a1a1a", fg="#cccccc",
                                         font=("Consolas", 9), wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        text.insert(tk.END, f"{'Liga':<25} {'Matcher':>7} {'I kö':>7} {'Hämtade':>8}\n")
        text.insert(tk.END, "-" * 50 + "\n")
        for country in sorted(stats):
            s = stats[country]
            text.insert(tk.END, f"{country:<25} {s['total']:>7} {s['pending']:>7} {s['success']:>8}\n")
        total_all = sum(s["total"] for s in stats.values())
        total_pend = sum(s["pending"] for s in stats.values())
        total_succ = sum(s["success"] for s in stats.values())
        text.insert(tk.END, "-" * 50 + "\n")
        text.insert(tk.END, f"{'Totalt':<25} {total_all:>7} {total_pend:>7} {total_succ:>8}\n")
        text.config(state=tk.DISABLED)

    def _settings_delay(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Fördröjning (ms)")
        dlg.geometry("250x100")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()
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
        dlg.transient(self.root)
        dlg.grab_set()
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

    # --- Button state management ---
    def _set_buttons(self, running):
        self._running = running
        state = tk.DISABLED if running else tk.NORMAL
        self.btn_ligor.config(state=state)
        self.btn_matcher.config(state=state)
        self.btn_csv.config(state=state)
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

    def start_hamta_csv(self):
        self._set_buttons(True)
        _abort_event.clear()
        t = threading.Thread(target=self._run_hamta_csv, daemon=True)
        t.start()

    # --- Worker: Hamta ligor ---
    def _run_hamta_ligor(self):
        try:
            APlayers.log("=== Hämtar ligor ===", "C")
            leagues = APlayers.load_leagues()
            APlayers.log(f"Laddade {len(leagues)} ligor")

            matcher_data = APlayers.load_matcher()
            existing_urls = {m["url"] for m in matcher_data["matches"]}

            total_leagues = len(leagues)
            new_matches = []

            for i, (country, url) in enumerate(leagues):
                if _abort_event.is_set():
                    APlayers.log("Avbruten av användare.", "BR")
                    break
                put_progress(i + 1, total_leagues, f"Hämtar {country}...")
                match_urls = APlayers.extract_result_links(country, url)
                new_count = 0
                for m in match_urls:
                    if m not in existing_urls:
                        matcher_data["matches"].append({"country": country, "url": m, "status": "pending"})
                        new_matches.append((country, m))
                        new_count += 1
                tag = f"{new_count} nya" if new_count else "0 nya"
                APlayers.log(f"  {len(match_urls)} på sida, {tag}", "B")

            APlayers.save_matcher(matcher_data)
            total_pending = sum(1 for m in matcher_data["matches"] if m["status"] == "pending")
            total_done = len(matcher_data["matches"]) - total_pending

            APlayers.log(f"{len(new_matches)} nya matcher tillagda. {total_done} redan klara, {total_pending} i kö.", "C")
            APlayers.log("Klart.", "BG")
            play_success()
        except Exception as e:
            APlayers.log(f"Fel: {e}", "BR")
        finally:
            self.root.after(0, lambda: self._set_buttons(False))
            self.root.after(0, lambda: setattr(self, '_task_start', None))
            self.root.after(0, self._update_counts)
            put_progress(0, 1, "Redo")

    # --- Worker: Hamta matcher ---
    def _run_hamta_matcher(self):
        try:
            APlayers.log("=== Hämtar matcher ===", "C")
            matcher_data = APlayers.load_matcher()
            pending = [m for m in matcher_data["matches"] if m["status"] == "pending"]

            if not pending:
                APlayers.log("Inga matcher i kö.", "BG")
                return

            total = len(pending)
            success = 0
            fail = 0

            for i, match in enumerate(pending):
                if _abort_event.is_set():
                    APlayers.log("Avbruten av användare.", "BR")
                    break

                country = match["country"]
                url = match["url"]
                put_progress(i + 1, total, f"Behandlar {country}...")

                rows, meta = APlayers.parse_match(country, url, compact=True)

                if meta is None:
                    fail += 1
                    APlayers.log("  MISSLYCKADES", "Y")
                    continue

                if rows:
                    match["status"] = "success"
                    match.update(meta)
                    match["players"] = rows
                    success += 1
                else:
                    fail += 1
                    APlayers.log("  Ingen data", "Y")

                APlayers.save_matcher(matcher_data)

            APlayers.log(f"Resultat: {success} lyckade, {fail} misslyckades (av {total}).", "BG" if fail == 0 else "Y")
            play_success()
        except Exception as e:
            APlayers.log(f"Fel: {e}", "BR")
        finally:
            self.root.after(0, lambda: self._set_buttons(False))
            self.root.after(0, lambda: setattr(self, '_task_start', None))
            self.root.after(0, self._update_counts)
            put_progress(0, 1, "Redo")

    # --- Worker: Hamta CSV ---
    def _run_hamta_csv(self):
        try:
            matcher_data = APlayers.load_matcher()
            success_count = sum(1 for m in matcher_data["matches"] if m["status"] == "success")
            if success_count == 0:
                APlayers.log("Inga hämtade matcher att exportera.", "Y")
                return

            now = datetime.now()
            default_name = f"Output {now.strftime('%Y-%m-%d')}.csv"
            filepath = filedialog.asksaveasfilename(
                parent=self.root,
                title="Save CSV",
                initialfile=default_name,
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
            )

            if not filepath:
                APlayers.log("CSV-export avbruten.", "Y")
                return

            if os.path.exists(filepath):
                if not messagebox.askyesno("Skriv över?", f"'{os.path.basename(filepath)}' finns redan.\nSkriv över?"):
                    APlayers.log("CSV-export avbruten.", "Y")
                    return

            written = APlayers.export_csv(matcher_data, filepath)
            APlayers.log(f"Exporterade {written} rader till {filepath}", "BG")
        except Exception as e:
            APlayers.log(f"Fel: {e}", "BR")
        finally:
            self.root.after(0, lambda: self._set_buttons(False))

    def run(self):
        self.root.mainloop()


def run():
    APlayers.load_settings()
    app = APlayersGUI()
    app.log_console("=== APlayers redo ===", "C")
    app._update_counts()
    app.run()


if __name__ == "__main__":
    run()
