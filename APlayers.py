import re
import os
import sys
import csv
import json
import time
import glob
import threading
import configparser
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from tqdm import tqdm

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DELAY_MS = 2000
RETRIES = 10
OUTPUT_DIR = SCRIPT_DIR
LOG_DIR = os.path.join(SCRIPT_DIR, "Logs")
MAX_FILES = 10
MAX_LOGS = 10
LIGOR_PATH = os.path.join(SCRIPT_DIR, "Ligor.txt")
MATCHER_FILE = os.path.join(SCRIPT_DIR, "Matcher.json")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "Settings.ini")


def load_settings():
    global DELAY_MS, RETRIES
    if not os.path.exists(SETTINGS_PATH):
        save_settings()
        return
    cfg = configparser.ConfigParser()
    cfg.read(SETTINGS_PATH)
    try:
        DELAY_MS = cfg.getint("Settings", "delay_ms", fallback=2000)
        RETRIES = cfg.getint("Settings", "retries", fallback=10)
    except Exception:
        pass


def save_settings():
    cfg = configparser.ConfigParser()
    cfg["Settings"] = {"delay_ms": str(DELAY_MS), "retries": str(RETRIES)}
    with open(SETTINGS_PATH, "w") as f:
        cfg.write(f)


abort_check = lambda: False
_match_urls = {}

log_handle = None

try:
    os.system("")
except Exception:
    pass
C = {
    "R": "\033[91m",
    "G": "\033[92m",
    "Y": "\033[93m",
    "B": "\033[94m",
    "M": "\033[95m",
    "C": "\033[96m",
    "W": "\033[97m",
    "BR": "\033[1;91m",
    "BG": "\033[1;92m",
    "BY": "\033[1;93m",
    "BB": "\033[1;94m",
    "BC": "\033[1;96m",
    "BW": "\033[1;97m",
    "X": "\033[0m",
}


def log(msg, color=None):
    is_tty = sys.stdout is not None and sys.stdout.isatty()
    text = f"{C.get(color, '')}{msg}{C['X']}" if color and is_tty else msg
    if sys.stdout:
        tqdm.write(text)
    if log_handle:
        log_handle.write(msg + "\n")
        log_handle.flush()


def fetch(url, timeout=30, retries=None):
    if retries is None:
        retries = RETRIES

    def interruptible_sleep(seconds):
        if seconds <= 0:
            return True
        end = time.time() + seconds
        while time.time() < end:
            if abort_check():
                return False
            time.sleep(min(0.2, end - time.time()))
        return True

    def do_request():
        result = [None]
        err = [None]
        def worker():
            try:
                result[0] = requests.get(url, headers=HEADERS, timeout=timeout)
            except Exception as e:
                err[0] = e
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while t.is_alive():
            if abort_check():
                return None
            t.join(0.2)
        if err[0]:
            raise err[0]
        return result[0]

    for attempt in range(retries):
        if abort_check():
            return None
        try:
            if attempt > 0:
                wait = 2 ** attempt
                log(f"  Retry {attempt} after {wait}s...", "Y")
                if not interruptible_sleep(wait):
                    return None
            else:
                if not interruptible_sleep(DELAY_MS / 1000):
                    return None
            resp = do_request()
            if resp is None:
                return None
            if resp.status_code in (429, 503):
                log(f"  Server returned {resp.status_code}, retrying...", "Y")
                if not interruptible_sleep(5 * (attempt + 1)):
                    return None
                continue
            return resp
        except requests.exceptions.RequestException as e:
            log(f"  Request failed (attempt {attempt + 1}/{retries}): {e}", "Y")
    return None


def load_leagues(path=LIGOR_PATH):
    leagues = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                name, url = line.split(",", 1)
                leagues.append((name, url))
    return leagues


def extract_result_links(name, url):
    _match_urls[name] = url
    log(f"[{name}] Hämtar matcher...")
    resp = fetch(url)
    if resp is None or resp.status_code != 200:
        log(f"[{name}] Misslyckades")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    links = []

    for td in soup.find_all("td", class_="zentriert"):
        text = td.get_text(strip=True)
        if re.match(r"^\d+:\d+$", text):
            a = td.find("a", href=True)
            if a and "spielbericht" in a["href"]:
                links.append("https://www.transfermarkt.com" + a["href"])

    return links


def parse_match(country, match_url, compact=False):
    lineup_url = match_url.replace("/index/", "/aufstellung/")
    match_id = match_url.rstrip("/").rsplit("/", 1)[-1]
    _match_urls[match_id] = lineup_url
    log(f"  Hämtar lineup: {country} {match_id}")
    resp = fetch(lineup_url)
    if resp is None or resp.status_code != 200:
        log("  MISSLYCKADES efter alla försök", "BR")
        return [], None

    soup = BeautifulSoup(resp.text, "html.parser")

    box = soup.select_one(".box.sb-spielbericht-head")
    if not box:
        return [], None

    liga_tag = box.select_one(".direct-headline__header a")
    liga = liga_tag.get_text(strip=True) if liga_tag else "?"

    matchday_tag = box.select_one(".sb-datum a")
    matchday = matchday_tag.get_text(strip=True) if matchday_tag else "?"

    datum_tag = box.select_one(".sb-datum a[href*='waspassiertheute']")
    datum_raw = datum_tag.get_text(strip=True) if datum_tag else ""
    datum = datum_raw
    if datum_raw:
        try:
            dt = datetime.strptime(datum_raw, "%a, %d/%m/%y")
            datum = dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    zeit_p = box.select_one(".sb-datum")
    zeit = ""
    if zeit_p:
        full_text = zeit_p.get_text(" ", strip=True)
        parts = full_text.split("|")
        zeit = parts[-1].strip().replace("\xa0", " ").replace("��", "") if len(parts) >= 3 else ""

    ergebnis = box.select_one(".sb-endstand")
    ergebnis_text = ergebnis.get_text(" ", strip=True).replace("  ", " ") if ergebnis else "?"

    heim = box.select_one(".sb-heim a.sb-vereinslink")
    heim_name = heim.get_text(strip=True) if heim else "?"

    gast = box.select_one(".sb-gast a.sb-vereinslink")
    gast_name = gast.get_text(strip=True) if gast else "?"

    meta = {
        "liga": liga,
        "home": heim_name,
        "away": gast_name,
        "matchday": matchday,
        "date": datum,
        "time": zeit,
        "result": ergebnis_text,
    }
    if compact:
        meta["country"] = country

    rows = []
    team_names = [heim_name, gast_name]
    team_toggle = 0

    for heading in soup.find_all("h2"):
        section = heading.get_text(strip=True)
        if section not in ("Starting Line-up", "Substitutes", "Manager"):
            continue

        container = heading.find_parent("div", class_="large-6")
        if not container:
            continue

        team = team_names[team_toggle]
        team_toggle = 1 - team_toggle

        table = container.select_one(".responsive-table > table")
        if not table:
            continue

        for tr in table.find_all("tr", recursive=False):
            if section == "Manager":
                parsed = parse_manager_row(tr)
            else:
                parsed = parse_player_row(tr)

            if parsed:
                number, name, role, salary, age, nationality = parsed
                if compact:
                    rows.append([section, team, number, name, role, salary, age, nationality])
                else:
                    rows.append(
                        [country, liga, matchday, team, datum, zeit, ergebnis_text, section, number, name, role, salary, age, nationality]
                    )

    return rows, meta


def parse_player_row(row):
    num_div = row.select_one(".rn_nummer")
    number = num_div.get_text(strip=True) if num_div else "-"

    name_tag = row.select_one("a.wichtig")
    name = name_tag.get_text(strip=True) if name_tag else "?"

    cells = row.find_all("td", recursive=False)
    if len(cells) < 3:
        return None

    inline_table = cells[1].find("table", class_="inline-table")
    if not inline_table:
        return None

    rows = inline_table.find_all("tr")
    if len(rows) < 2:
        return None

    text_row1 = rows[0].get_text(" ", strip=True)
    text_row2 = rows[1].get_text(strip=True)

    age_match = re.search(r"\((\d+) years old\)", text_row1)
    age = age_match.group(1) if age_match else ""

    role = ""
    salary = ""
    if "," in text_row2:
        role, salary = text_row2.split(",", 1)
        role = role.strip()
        salary = salary.strip()
    else:
        role = text_row2.strip()

    flag = cells[2].find("img", class_="flaggenrahmen") if len(cells) > 2 else None
    nationality = flag.get("title", "") if flag else ""

    return [number, name, role, salary, age, nationality]


def parse_manager_row(row):
    name_tag = row.select_one("a.wichtig")
    name = name_tag.get_text(strip=True) if name_tag else "?"

    cells = row.find_all("td", recursive=False)
    if len(cells) < 2:
        return None

    inline_table = cells[0].find("table", class_="inline-table")
    if not inline_table:
        return None

    rows = inline_table.find_all("tr")
    if len(rows) < 2:
        return None

    age_text = rows[1].get_text(strip=True)
    age_match = re.search(r"(\d+) years old", age_text)
    age = age_match.group(1) if age_match else ""

    flag = cells[1].find("img", class_="flaggenrahmen") if len(cells) > 1 else None
    nationality = flag.get("title", "") if flag else ""

    return ["-", name, "Manager", "", age, nationality]


def rotate_files(pattern, max_count):
    files = sorted(glob.glob(pattern))
    while len(files) >= max_count:
        oldest = files.pop(0)
        os.remove(oldest)
        log(f"  Removed old file: {oldest}")


def process_pending_matches(matcher_data, writer, csv_file):
    pending = [m for m in matcher_data["matches"] if m["status"] == "pending"]
    if not pending:
        return 0

    success = 0
    for match in tqdm(pending, desc="Processing matches", unit="match"):
        country = match["country"]
        url = match["url"]

        try:
            rows, meta = parse_match(country, url)
        except Exception as e:
            log(f"  Skipped {url}: {e}", "Y")
            continue

        if meta is None:
            continue

        if rows:
            match["status"] = "success"
            match.update(meta)
            writer.writerows(rows)
            csv_file.flush()
            success += 1

        save_matcher(matcher_data)

    return success


def load_matcher():
    if not os.path.exists(MATCHER_FILE):
        now = datetime.now()
        return {"date": now.strftime("%Y-%m-%d"), "matches": []}
    with open(MATCHER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_matcher(data):
    with open(MATCHER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def format_duration(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    elif m:
        return f"{m}m {s}s"
    return f"{s}s"


CSV_HEADER = ["country", "liga", "matchday", "team", "date", "time", "result", "section", "number", "name", "role", "salary", "age", "nationality"]


def export_csv(matcher_data, output_path):
    written = 0
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        for m in matcher_data["matches"]:
            if m.get("status") != "success":
                continue
            country = m.get("country", "?")
            liga = m.get("liga", "?")
            matchday = m.get("matchday", "?")
            date = m.get("date", "?")
            time_val = m.get("time", "?")
            result = m.get("result", "?")
            players = m.get("players", [])
            for p in players:
                section, team, number, name, role, salary, age, nationality = p
                writer.writerow([country, liga, matchday, team, date, time_val, result, section, number, name, role, salary, age, nationality])
                written += 1
    return written


def main():
    global log_handle

    t_start = datetime.now()
    now = t_start
    date_str = now.strftime("%Y-%m-%d")
    csv_path = os.path.join(OUTPUT_DIR, f"Output {date_str}.csv")

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"{date_str}.log")
    log_handle = open(log_path, "a", encoding="utf-8")

    log(f"=== Run started at {now.strftime('%Y-%m-%d %H:%M:%S')} ===", "C")

    rotate_files(os.path.join(OUTPUT_DIR, "Output *.csv"), MAX_FILES)
    rotate_files(os.path.join(LOG_DIR, "*.log"), MAX_LOGS)

    # --- Phase 0: Matcher.json setup ---
    if os.path.exists(MATCHER_FILE):
        log(f"{MATCHER_FILE} already exists.", "Y")
        answer = input(f"{C['BY']}[A]ppend — keep existing + add new\n[N]ew    — archive old, start fresh\nChoice: {C['X']}").strip().lower()
        if answer == "n":
            bak = "Matcher.old.json"
            os.replace(MATCHER_FILE, bak)
            log(f"  Archived to {bak}")
            matcher_data = {"date": date_str, "matches": []}
        else:
            matcher_data = load_matcher()
            log("  Appending to existing file.")
    else:
        matcher_data = {"date": date_str, "matches": []}

    leagues = load_leagues()
    log(f"Loaded {len(leagues)} league(s)")

    existing_urls = {m["url"] for m in matcher_data["matches"]}
    new_matches = []
    for country, url in tqdm(leagues, desc="Fetching fixtures", unit="league"):
        match_urls = extract_result_links(country, url)
        new_count = 0
        for m in match_urls:
            if m not in existing_urls:
                matcher_data["matches"].append({"country": country, "url": m, "status": "pending"})
                new_matches.append((country, m))
                new_count += 1
        if match_urls:
            tag = f"{new_count} new" if new_count else "0 new"
            log(f"[{country}] {len(match_urls)} on page, {tag}", "B")

    save_matcher(matcher_data)
    total_pending = sum(1 for m in matcher_data["matches"] if m["status"] == "pending")

    log(f"{len(new_matches)} new matches added.")
    log(f"Total in queue: {total_pending} pending, {len(matcher_data['matches']) - total_pending} already done.", "C")

    if total_pending == 0:
        log("Nothing to do. Exiting.", "BG")
        log_handle.close()
        return

    # --- CSV setup ---
    csv_mode = "a" if os.path.exists(csv_path) else "w"

    with open(csv_path, csv_mode, encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        if csv_mode == "w":
            writer.writerow(CSV_HEADER)

        # --- Phase 1: process all pending ---
        log("--- Phase 1: processing pending matches ---", "C")
        succeeded = process_pending_matches(matcher_data, writer, csv_file)

        pending_after = sum(1 for m in matcher_data["matches"] if m["status"] == "pending")
        log(f"Phase 1 done. {succeeded} succeeded, {pending_after} still pending.", "BG" if pending_after == 0 else "Y")

        # --- Phase 2: auto-rerun once ---
        if pending_after > 0:
            log(f"\n--- Phase 2: auto-rerun {pending_after} pending ---", "C")
            succeeded2 = process_pending_matches(matcher_data, writer, csv_file)

            pending_after2 = sum(1 for m in matcher_data["matches"] if m["status"] == "pending")
            log(f"Phase 2 done. {succeeded2} recovered, {pending_after2} still pending.", "BG" if pending_after2 == 0 else "Y")
            pending_after = pending_after2

        # --- Phase 3: user-prompted reruns ---
        while pending_after > 0:
            pending_list = [m for m in matcher_data["matches"] if m["status"] == "pending"]
            log(f"\n{pending_after} match(es) still failed:", "BR")
            for m in pending_list:
                log(f"  [{m['country']}] {m['url']}")

            answer = input(f"{C['BR']}Rerun these {pending_after} matches? (y/n): {C['X']}").strip().lower()
            if answer != "y":
                log("Skipping rerun.", "Y")
                break

            log("Rerunning...", "C")
            succeeded3 = process_pending_matches(matcher_data, writer, csv_file)
            pending_after = sum(1 for m in matcher_data["matches"] if m["status"] == "pending")
            log(f"{succeeded3} recovered, {pending_after} still pending.")

    # --- Summary ---
    total_success = sum(1 for m in matcher_data["matches"] if m["status"] == "success")
    total_pending = sum(1 for m in matcher_data["matches"] if m["status"] == "pending")
    total = len(matcher_data["matches"])

    log(f"\nFinal: {total_success} success, {total_pending} failed (of {total}).", "BG" if total_pending == 0 else "Y")
    log(f"Output: {csv_path}")
    log(f"Matcher: {MATCHER_FILE}")
    log(f"Total time: {format_duration((datetime.now() - t_start).total_seconds())}", "M")

    log_handle.close()


if __name__ == "__main__":
    main()
