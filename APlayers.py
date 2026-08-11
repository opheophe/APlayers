import re
import os
import csv
import time
import glob
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from tqdm import tqdm

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

DELAY_MS = 2000
MAX_MATCHES = 999999
OUTPUT_DIR = "."
LOG_DIR = "Logs"
MAX_FILES = 10
MAX_LOGS = 10

log_handle = None


def log(msg):
    tqdm.write(msg)
    if log_handle:
        log_handle.write(msg + "\n")
        log_handle.flush()


def fetch(url, timeout=30, retries=10):
    for attempt in range(retries):
        try:
            if attempt > 0:
                wait = 2 ** attempt
                log(f"  Retry {attempt} after {wait}s...")
                time.sleep(wait)
            else:
                time.sleep(DELAY_MS / 1000)
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            if resp.status_code in (429, 503):
                log(f"  Server returned {resp.status_code}, retrying...")
                time.sleep(5 * (attempt + 1))
                continue
            return resp
        except requests.exceptions.RequestException as e:
            log(f"  Request failed (attempt {attempt + 1}/{retries}): {e}")
    return None
    return requests.Response()



def load_leagues(path="Ligor.txt"):
    leagues = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                name, url = line.split(",", 1)
                leagues.append((name, url))
    return leagues


def extract_result_links(name, url):
    log(f"[{name}] Fetching fixtures...")
    resp = fetch(url)
    if resp is None or resp.status_code != 200:
        log(f"[{name}] Failed")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    links = []

    for td in soup.find_all("td", class_="zentriert"):
        text = td.get_text(strip=True)
        if re.match(r"^\d+:\d+$", text):
            a = td.find("a", href=True)
            if a and "spielbericht" in a["href"]:
                links.append("https://www.transfermarkt.com" + a["href"])

    log(f"[{name}] {len(links)} matches found")
    return links


def parse_match(country, match_url):
    lineup_url = match_url.replace("/index/", "/aufstellung/")
    log(f"  Fetching lineup: {lineup_url}")
    resp = fetch(lineup_url)
    if resp is None or resp.status_code != 200:
        log(f"  Failed after all retries: {match_url}")
        answer = input("  Skip this match? (y/n): ").strip().lower()
        if answer == "n":
            log("  Aborting run.")
            raise SystemExit(1)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    box = soup.select_one(".box.sb-spielbericht-head")
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
                rows.append(
                    [country, liga, matchday, team, datum, zeit, ergebnis_text, section, number, name, role, salary, age, nationality]
                )

    return rows


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


def main():
    global log_handle

    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M")
    csv_path = os.path.join(OUTPUT_DIR, f"{timestamp}.csv")

    os.makedirs(LOG_DIR, exist_ok=True)
    log_date = now.strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"{log_date}.log")

    log_handle = open(log_path, "a", encoding="utf-8")

    log(f"=== Run started at {now.strftime('%Y-%m-%d %H:%M:%S')} ===")

    rotate_files(os.path.join(OUTPUT_DIR, "*.csv"), MAX_FILES)
    rotate_files(os.path.join(LOG_DIR, "*.log"), MAX_LOGS)

    leagues = load_leagues()
    log(f"Loaded {len(leagues)} league(s)")

    all_matches = []
    for country, url in tqdm(leagues, desc="Fetching fixtures", unit="league"):
        match_urls = extract_result_links(country, url)
        for m in match_urls:
            all_matches.append((country, m))

    total = len(all_matches)
    all_matches = all_matches[:MAX_MATCHES]

    log(f"Total matches to process: {len(all_matches)}")

    header = ["country", "liga", "matchday", "team", "date", "time", "result", "section", "number", "name", "role", "salary", "age", "nationality"]

    success = 0
    fail = 0

    with open(csv_path, "w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)

        for country, match_url in tqdm(all_matches, desc="Processing matches", unit="match"):
            try:
                lines = parse_match(country, match_url)
            except SystemExit:
                raise
            except Exception as e:
                log(f"  Skipped {match_url}: {e}")
                lines = []
            if lines:
                success += 1
            else:
                fail += 1
            writer.writerows(lines)
            csv_file.flush()

    log(f"Done. {success} fetched, {fail} failed (of {len(all_matches)}).")
    log(f"Output: {csv_path}")
    log_handle.close()


if __name__ == "__main__":
    main()
