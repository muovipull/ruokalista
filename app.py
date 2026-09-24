import os
import csv
import re
import xml.etree.ElementTree as ET
from flask import Flask, Response, jsonify, send_from_directory
import requests
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__, static_folder='.')

AROMI_BASE_URL = "https://aromimenu.cgisaas.fi/ISSAromieMenus/FI/Default/iss/HKIViikinNormaalikoulu/api/Common/Restaurant/GetRssFeed/2d1d4df3-5ab7-46e2-b634-87446c067a6f"
CSV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ruokalista_arkisto.csv")

# ---------------------------------------------------------------------------
# CSV-TALLENNUSLOGIIKKA
# ---------------------------------------------------------------------------

def ensure_csv_exists():
    """Luo CSV-tiedoston otsikkorivin, jos tiedostoa ei ole vielä olemassa."""
    if not os.path.exists(CSV_FILE_PATH):
        with open(CSV_FILE_PATH, mode="w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["Päivämäärä", "Päivä", "Lounas", "Kasvislounas"])

def get_existing_dates_from_csv():
    """Lukee CSV:ssä jo olevat päivämäärät duplikaattien välttämiseksi."""
    ensure_csv_exists()
    dates = set()
    with open(CSV_FILE_PATH, mode="r", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter=";")
        next(reader, None)  # Ohitetaan otsikkorivi
        for row in reader:
            if row:
                dates.add(row[0].strip())
    return dates

def save_rss_items_to_csv(xml_content):
    """Parsii RSS XML-datan ja lisää uudet päiväkohtaiset ruoat CSV-tiedostoon."""
    try:
        root = ET.fromstring(xml_content)
        existing_dates = get_existing_dates_from_csv()
        new_rows = []

        for item in root.findall('.//item'):
            title_elem = item.find('title')
            desc_elem = item.find('description')
            
            if title_elem is None or title_elem.text is None:
                continue

            full_title = title_elem.text.strip()
            desc = desc_elem.text if (desc_elem is not None and desc_elem.text) else ''

            # Eristetään päivämäärä (esim. "21.9.2026")
            date_match = re.search(r'\d+\.\d+\.\d+', full_title)
            date_str = date_match.group(0) if date_match else full_title

            # Jos päivä on jo tallennettu arkistoon, skipataan
            if date_str in existing_dates:
                continue

            # Parsitaan Lounas ja Kasvislounas description-kentästä
            parts = desc.split('<br><br>')
            lounas = ""
            kasvislounas = ""

            for part in parts:
                p_clean = part.replace('<br>', ' ').strip()
                if p_clean.startswith("Lounas:"):
                    lounas = p_clean.replace("Lounas:", "").strip()
                elif p_clean.startswith("Kasvislounas:"):
                    kasvislounas = p_clean.replace("Kasvislounas:", "").strip()
                else:
                    if not lounas:
                        lounas = p_clean
                    elif not kasvislounas:
                        kasvislounas = p_clean

            new_rows.append([date_str, full_title, lounas, kasvislounas])
            existing_dates.add(date_str)

        # Kirjoitetaan uudet rivit CSV-tiedostoon
        if new_rows:
            with open(CSV_FILE_PATH, mode="a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerows(new_rows)
            app.logger.info(f"Tallennettu {len(new_rows)} uutta ruokalistapäivää CSV-arkistoon.")

    except Exception as e:
        app.logger.error(f"Virhe CSV-tallennuksessa: {e}")

def update_all_weeks_job():
    """Ajastettu tehtävä: Hakee viikkojen 1, 2 ja 3 syötteet ja tallentaa ne CSV-tiedostoon."""
    app.logger.info("Suoritetaan automaattinen ruokalistojen taustapäivitys (CSV)...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    for week in [1, 2, 3]:
        url = f"{AROMI_BASE_URL}/{week}"
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                save_rss_items_to_csv(resp.content)
        except Exception as e:
            app.logger.error(f"Taustapäivitys epäonnistui viikolle {week}: {e}")

# ---------------------------------------------------------------------------
# TAUSTAAJAJASTIN (APScheduler)
# ---------------------------------------------------------------------------

scheduler = BackgroundScheduler(daemon=True)
# Ajetaan automaattisesti 1 tunnin välein (3600s)
scheduler.add_job(update_all_weeks_job, 'interval', hours=1)
scheduler.start()

# Ajetaan ensimmäinen haku heti kun sovellus käynnistyy
update_all_weeks_job()

# ---------------------------------------------------------------------------
# FLASK REITTIOHJAUKSET (ENDPOINTS)
# ---------------------------------------------------------------------------

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

@app.route('/api/rss/<int:week_id>')
def get_rss_feed(week_id):
    if week_id not in [1, 2, 3]:
        return jsonify({"error": "Virheellinen viikko. Sallitut arvot: 1, 2, 3"}), 400

    target_url = f"{AROMI_BASE_URL}/{week_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        response = requests.get(target_url, headers=headers, timeout=10)
        if response.status_code == 200:
            # Tallennetaan samalla CSV-arkistoon selaimen tehdessä pyynnön
            save_rss_items_to_csv(response.content)
            return Response(response.content, content_type='application/rss+xml; charset=utf-8')
        else:
            return jsonify({"error": f"AromiMenu palautti virheen: {response.status_code}"}), response.status_code
    except Exception as e:
        app.logger.error(f"Virhe haettaessa syötettä osoitteesta {target_url}: {e}")
        return jsonify({"error": "Virhe yhteydessä AromiMenu-palvelimeen"}), 500

@app.route('/api/archive/csv')
def download_csv():
    """Rajapinta, jolla CSV-tiedosto voidaan ladata suoraan selaimeen."""
    ensure_csv_exists()
    return send_from_directory('.', 'ruokalista_arkisto.csv', as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
