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
INCLUDE_MANAGERS = True
INCLUDE_SUBSTITUTES = True
PLAYER_AGE_CUTOFF = 18
REFETCH_PLAYERS = False
EVENT_LABELS = {
    "sb-aus": "Subbed out",
    "sb-ein": "Subbed in",
    "sb-gelb": "Yellow",
    "sb-gelb-rot": "2nd yellow",
    "sb-rot": "Red card",
    "sb-tor": "Goal",
    "sb-verletzung": "Injury",
}
OUTPUT_DIR = SCRIPT_DIR
LOG_DIR = os.path.join(SCRIPT_DIR, "Logs")
MAX_FILES = 10
MAX_LOGS = 10
LIGOR_PATH = os.path.join(SCRIPT_DIR, "Ligor.txt")
MATCHER_FILE = os.path.join(SCRIPT_DIR, "Matcher.json")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "Settings.ini")


def load_settings():
    global DELAY_MS, RETRIES, INCLUDE_MANAGERS, INCLUDE_SUBSTITUTES, EVENT_LABELS, PLAYER_AGE_CUTOFF, REFETCH_PLAYERS
    if not os.path.exists(SETTINGS_PATH):
        save_settings()
        return
    cfg = configparser.ConfigParser()
    cfg.read(SETTINGS_PATH)
    try:
        DELAY_MS = cfg.getint("Settings", "delay_ms", fallback=2000)
        RETRIES = cfg.getint("Settings", "retries", fallback=10)
        INCLUDE_MANAGERS = cfg.getboolean("Settings", "include_managers", fallback=True)
        INCLUDE_SUBSTITUTES = cfg.getboolean("Settings", "include_substitutes", fallback=True)
        PLAYER_AGE_CUTOFF = cfg.getint("Settings", "player_age_cutoff", fallback=18)
        REFETCH_PLAYERS = cfg.getboolean("Settings", "refetch_players", fallback=False)
    except Exception:
        pass
    if cfg.has_section("EventLabels"):
        for key, val in cfg.items("EventLabels"):
            EVENT_LABELS[key] = val


def save_settings():
    cfg = configparser.ConfigParser()
    cfg["Settings"] = {
        "delay_ms": str(DELAY_MS),
        "retries": str(RETRIES),
        "include_managers": str(INCLUDE_MANAGERS),
        "include_substitutes": str(INCLUDE_SUBSTITUTES),
        "player_age_cutoff": str(PLAYER_AGE_CUTOFF),
        "refetch_players": str(REFETCH_PLAYERS),
    }
    cfg["EventLabels"] = EVENT_LABELS
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
                parts = line.split(",", 2)
                if len(parts) == 3:
                    user, name, url = parts
                else:
                    user, name, url = parts[0], parts[0], parts[1]
                leagues.append((user, name, url))
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
                number, name, player_id, role, salary, age, nationality, events = parsed
                if compact:
                    rows.append([section, team, number, name, player_id, role, salary, age, nationality, events])
                else:
                    fixture = f"{heim_name} vs {gast_name}" if heim_name and gast_name else ""
                    rows.append(
                        [country, liga, matchday, team, datum, zeit, ergebnis_text, section, number, name, player_id, role, salary, age, nationality, fixture, match_url, events]
                    )

    return rows, meta


def parse_player_row(row):
    num_div = row.select_one(".rn_nummer")
    number = num_div.get_text(strip=True) if num_div else "-"

    name_tag = row.select_one("a.wichtig")
    name = name_tag.get_text(strip=True) if name_tag else "?"
    player_id = ""
    if name_tag:
        href = name_tag.get("href", "")
        m = re.search(r"/spieler/(\d+)", href)
        if m:
            player_id = m.group(1)

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

    event_spans = rows[0].find_all("span", class_=lambda c: c and "sb-sprite" in c)
    events = " ".join(s.get("class")[-1] for s in event_spans)

    return [number, name, player_id, role, salary, age, nationality, events]


def parse_manager_row(row):
    name_tag = row.select_one("a.wichtig")
    name = name_tag.get_text(strip=True) if name_tag else "?"
    player_id = ""
    if name_tag:
        href = name_tag.get("href", "")
        m = re.search(r"/spieler/(\d+)", href)
        if m:
            player_id = m.group(1)

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

    event_spans = rows[0].find_all("span", class_=lambda c: c and "sb-sprite" in c)
    events = " ".join(s.get("class")[-1] for s in event_spans)

    return ["-", name, player_id, "Manager", "", age, nationality, events]


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


def collect_players(matcher_data):
    if "players" not in matcher_data:
        matcher_data["players"] = []

    existing_ids = {p.get("id") for p in matcher_data["players"] if p.get("id")}
    added = 0

    for m in matcher_data["matches"]:
        if m.get("status") != "success":
            continue
        for p in m.get("players", []):
            if len(p) < 10:
                continue
            name = p[3]
            player_id = p[4]
            age = p[7]
            if not player_id or player_id in existing_ids:
                continue
            matcher_data["players"].append({"id": player_id, "name": name, "age": age})
            existing_ids.add(player_id)
            added += 1

    return added


def _slugify(name):
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"\s+", "-", slug).strip("-")
    return slug or "player"


def player_profile_url(name, player_id):
    return f"https://www.transfermarkt.com/{_slugify(name)}/profil/spieler/{player_id}"


def fetch_player_profile(name, player_id):
    resp = fetch(player_profile_url(name, player_id))
    if resp is None or resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    info = {}

    club_a = soup.select_one("span.data-header__club a")
    if club_a:
        info["club"] = club_a.get_text(strip=True) or club_a.get("title", "")

    dob = soup.select_one("span[itemprop='birthDate']")
    if dob:
        info["dob"] = dob.get_text(strip=True).split("(")[0].strip()

    nat = soup.select_one("span[itemprop='nationality']")
    if nat:
        info["country"] = nat.get_text(" ", strip=True)

    height = soup.select_one("span[itemprop='height']")
    if height:
        info["height"] = height.get_text(strip=True)

    for li in soup.select("li.data-header__label"):
        label = li.get_text(" ", strip=True)
        if label.startswith("Position:"):
            content = li.select_one("span.data-header__content")
            if content:
                info["position"] = content.get_text(strip=True)
            break

    return info


def fetch_player_performance(player_id):
    url = f"https://www.transfermarkt.com/ceapi/performance-game/{player_id}"
    resp = fetch(url)
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not data.get("success"):
        return None
    return (data.get("data") or {}).get("performance") or []


IDENTITY_FIELDS = {"shirtNumber", "positionId", "age", "primaryClubId", "injuryId",
                   "absenceId", "ageDiscrepancyDays", "grade", "pointsOnThePitch", "fairPlayPoints"}
RATIO_FIELDS = {"tacklesWonRatio", "passesReachedRatio", "scoringAttemptsOnGoalRatio"}
BOOL_FIELDS = {"isCaptain", "isStarting"}


def aggregate_performance(entries):
    groups = {}
    order = []
    for e in entries:
        gi = e.get("gameInformation") or {}
        stats = e.get("statistics") or {}
        league = gi.get("competitionId") or "?"
        season = gi.get("seasonId")
        key = (league, season)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(stats)

    result = []
    for league, season in order:
        sums = {}
        ratio_sum = {}
        ratio_cnt = {}
        last = {}
        bool_cnt = {}
        games = 0
        for stats in groups[(league, season)]:
            games += 1
            for cat, catval in stats.items():
                if not isinstance(catval, dict):
                    continue
                for k, v in catval.items():
                    if v is None:
                        continue
                    if k in BOOL_FIELDS:
                        bool_cnt[k] = bool_cnt.get(k, 0) + (1 if v else 0)
                    elif k in RATIO_FIELDS:
                        ratio_sum[k] = ratio_sum.get(k, 0.0) + v
                        ratio_cnt[k] = ratio_cnt.get(k, 0) + 1
                    elif k in IDENTITY_FIELDS:
                        last[k] = v
                    elif isinstance(v, (int, float)):
                        sums[k] = sums.get(k, 0) + v

        entry = {"league": league, "season": season, "games": games}
        for k, v in sums.items():
            entry[k] = v
        for k, v in ratio_sum.items():
            cnt = ratio_cnt.get(k, 0)
            entry[k] = round(v / cnt, 2) if cnt else None
        for k, v in bool_cnt.items():
            entry[k] = v
        for k, v in last.items():
            entry[k] = v
        result.append(entry)

    return result


DETAIL_FIELDS = ("club", "dob", "position", "country", "height", "performance", "detailed")


def fetch_player_details(matcher_data, cutoff, progress_cb=None, refetch=False):
    players = matcher_data.get("players", [])
    fetched = 0
    total = len(players)

    for i, player in enumerate(players):
        if abort_check():
            break

        age_raw = str(player.get("age", "")).strip()
        age = int(age_raw) if age_raw.isdigit() else None
        if age is None or age > cutoff:
            continue
        if player.get("detailed") and not refetch:
            continue

        pid = player.get("id", "")
        name = player.get("name", "")
        if not pid:
            continue

        if progress_cb:
            progress_cb(i + 1, total, name)

        for field in DETAIL_FIELDS:
            player.pop(field, None)

        profile = fetch_player_profile(name, pid)
        performance = fetch_player_performance(pid)

        if profile:
            player.update(profile)
            player["detailed"] = True
        if performance is not None:
            player["performance"] = aggregate_performance(performance)

        fetched += 1
        save_matcher(matcher_data)

    return fetched


def format_duration(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    elif m:
        return f"{m}m {s}s"
    return f"{s}s"


CSV_HEADER = ["country", "liga", "matchday", "team", "date", "time", "result", "section", "number", "name", "player_id", "role", "salary", "age", "nationality", "fixture", "url", "events"]


def translate_events(events_str):
    if not events_str:
        return ""
    parts = events_str.split()
    translated = [EVENT_LABELS.get(p, p) for p in parts]
    return " ".join(translated)


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
            home = m.get("home", "")
            away = m.get("away", "")
            fixture = f"{home} vs {away}" if home and away else ""
            match_url = m.get("url", "")
            for p in players:
                if len(p) >= 10:
                    section, team, number, name, player_id, role, salary, age, nationality = p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8]
                    events = translate_events(p[9] if len(p) > 9 else "")
                else:
                    section, team, number, name, role, salary, age, nationality = p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]
                    events = translate_events(p[8] if len(p) > 8 else "")
                    player_id = ""
                if section == "Manager" and not INCLUDE_MANAGERS:
                    continue
                if section == "Substitutes" and not INCLUDE_SUBSTITUTES:
                    continue
                writer.writerow([country, liga, matchday, team, date, time_val, result, section, number, name, player_id, role, salary, age, nationality, fixture, match_url, events])
                written += 1
    return written


def export_excel(matcher_data, output_path):
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ImportError("openpyxl krävs för Excel-export. Installera med: pip install openpyxl")

    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell_alignment = Alignment(vertical="center")
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )
    header_border = Border(
        left=Side(style="thin", color="2F5496"),
        right=Side(style="thin", color="2F5496"),
        top=Side(style="thin", color="2F5496"),
        bottom=Side(style="thin", color="2F5496"),
    )

    TAB_COLORS = ["FF4444", "FF8C00", "FFD700", "44CC44", "4488FF", "CC44CC", "00CCCC", "FF69B4"]

    EXCEL_HEADER = ["Country", "League", "Matchday", "Team", "Date", "Time", "Result",
                    "Section", "Number", "Name", "Player ID", "Role", "Salary", "Age", "Nationality",
                    "Fixture", "URL", "Events"]

    country_to_user = {}
    try:
        for user, name, _url in load_leagues():
            country_to_user[name] = user
    except Exception:
        pass

    matches_by_user = {}
    for m in matcher_data["matches"]:
        if m.get("status") != "success":
            continue
        country = m.get("country", "?")
        user = country_to_user.get(country, country.split(",")[0].strip() if "," in country else country)
        if user not in matches_by_user:
            matches_by_user[user] = []
        matches_by_user[user].append(m)

    total_written = 0
    sheet_count = 0

    for user in sorted(matches_by_user.keys()):
        safe_name = user[:31]
        ws = wb.create_sheet(title=safe_name)
        ws.sheet_properties.tabColor = TAB_COLORS[(sheet_count) % len(TAB_COLORS)]
        sheet_count += 1

        for col_idx, header in enumerate(EXCEL_HEADER, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = header_border

        ws.freeze_panes = "A2"

        row_num = 2
        col_max = [len(h) for h in EXCEL_HEADER]
        for m in matches_by_user[user]:
            country_val = m.get("country", "?")
            liga = m.get("liga", "?")
            matchday = m.get("matchday", "?")
            date = m.get("date", "?")
            time_val = m.get("time", "?")
            result = m.get("result", "?")
            players = m.get("players", [])
            home = m.get("home", "")
            away = m.get("away", "")
            fixture = f"{home} vs {away}" if home and away else ""
            match_url = m.get("url", "")

            for p in players:
                if len(p) >= 10:
                    section, team, number, name, player_id, role, salary, age, nationality = p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8]
                    events = translate_events(p[9] if len(p) > 9 else "")
                else:
                    section, team, number, name, role, salary, age, nationality = p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]
                    events = translate_events(p[8] if len(p) > 8 else "")
                    player_id = ""
                if section == "Manager" and not INCLUDE_MANAGERS:
                    continue
                if section == "Substitutes" and not INCLUDE_SUBSTITUTES:
                    continue
                values = [country_val, liga, matchday, team, date, time_val, result,
                          section, number, name, player_id, role, salary, age, nationality,
                          fixture, match_url, events]
                for col_idx, value in enumerate(values):
                    cell = ws.cell(row=row_num, column=col_idx + 1, value=value)
                    cell.alignment = cell_alignment
                    cell.border = thin_border
                    v_len = len(str(value)) if value is not None else 0
                    if v_len > col_max[col_idx]:
                        col_max[col_idx] = v_len
                if match_url:
                    ws.cell(row=row_num, column=17).hyperlink = match_url
                row_num += 1
                total_written += 1

        for col_idx, max_w in enumerate(col_max):
            col_letter = get_column_letter(col_idx + 1)
            ws.column_dimensions[col_letter].width = min(max_w + 3, 150)

        ws.auto_filter.ref = f"A1:{get_column_letter(len(EXCEL_HEADER))}{row_num - 1}"

    summary_ws = wb.create_sheet(title="Summary")
    summary_ws.sheet_properties.tabColor = "548235"
    wb.move_sheet(summary_ws, offset=-sheet_count)

    summary_header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    summary_header_fill = PatternFill(start_color="548235", end_color="548235", fill_type="solid")
    for col_idx, header in enumerate(["User", "Matches", "Players"], 1):
        cell = summary_ws.cell(row=1, column=col_idx, value=header)
        cell.font = summary_header_font
        cell.fill = summary_header_fill
        cell.alignment = header_alignment
        cell.border = header_border

    summary_row = 2
    for user in sorted(matches_by_user.keys()):
        user_matches = matches_by_user[user]
        count = sum(len(m.get("players", [])) for m in user_matches)
        summary_ws.cell(row=summary_row, column=1, value=user).border = thin_border
        summary_ws.cell(row=summary_row, column=2, value=len(user_matches)).border = thin_border
        summary_ws.cell(row=summary_row, column=3, value=count).border = thin_border
        summary_row += 1

    summary_ws.column_dimensions["A"].width = 18
    summary_ws.column_dimensions["B"].width = 10
    summary_ws.column_dimensions["C"].width = 12
    summary_ws.freeze_panes = "A2"

    wb.save(output_path)
    return total_written


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
    for user, country, url in tqdm(leagues, desc="Fetching fixtures", unit="league"):
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
