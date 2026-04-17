from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from mutagen.id3 import ID3, TIT2, TPE1, GRP1, TALB, TDRC, TPE2, APIC, error
from bs4 import BeautifulSoup
import time
import re
import subprocess
import os
import yt_dlp
import json
import musicbrainzngs

musicbrainzngs.set_rate_limit(limit_or_interval=1.0, new_requests=1)
musicbrainzngs.set_useragent("Sven", "1.1", "sven@schaider.net")

base_url = "https://oe3dabei.orf.at/index.php?pageID=202"
full_path = os.environ.get("MP3_OUTPUT_DIR", "/output")
os.makedirs(full_path, exist_ok=True)

DB_FILE = os.path.join(full_path, "songs.json") 

def load_songs_db():
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()
    
def check_song_in_db(titel, interpret):
    entry = f"{titel}|{interpret}"
    downloaded = load_songs_db()
    if entry in downloaded:
        print(f"⏭️ Bereits vorhanden: {titel} - {interpret}")
        return False
    else:
        return True

def save_song(titel, interpret):
    entry = f"{titel}|{interpret}"
    downloaded = load_songs_db()
    if entry in downloaded:
        print(f"⏭️ Bereits vorhanden: {titel} - {interpret}")
        return False
    
    downloaded.add(entry)
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(downloaded), f, indent=2, ensure_ascii=False)
    print(f"💾 Gespeichert: {titel} - {interpret}")
    return True

def get_songs():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")  # crucial for Docker
    options.add_argument("--ignore-certificate-errors")

    driver = webdriver.Chrome(options=options)  # no Service() needed in container
    
    songs = []
    
    url = f"{base_url}"
    print(f"🔍 {url}")
    driver.get(url)
    time.sleep(4)
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    driver.quit()
    items = soup.find_all('tr')
    
    print(f"📊 {len(items)} Items auf Seite")
    
    for item in items[1:]:
        titel_elem = item.find(class_=re.compile(r'chart_col_7'))
        interpret_elem = item.find(class_=re.compile(r'chart_col_9'))

        if titel_elem and interpret_elem:
            titel = titel_elem.text.strip()
            interpret = interpret_elem.text.strip()            
            songs.append((titel, interpret))
            #print(f"⭐ {titel} - {interpret}")
    return songs


def set_MP3_Tags(mp3_name, titel, interpret):
    # Kurz warten, damit subprocess die Datei sicher freigegeben hat
    time.sleep(1) 
    
    try:
        # ID3-Objekt direkt mit der Datei laden
        try:
            tags = ID3(mp3_name)
        except error:
            # Falls gar kein Header da ist
            tags = ID3()

        # Basis-Informationen setzen
        tags.add(TIT2(encoding=3, text=titel))
        tags.add(TPE1(encoding=3, text=interpret))
        tags.add(GRP1(encoding=3, text=["Ö3 Hörercharts"]))
        
        # MusicBrainz Daten abrufen
        mb_data = get_musicbrainz_data(titel, interpret)
        if mb_data:
            if mb_data.get('album'): 
                tags.add(TALB(encoding=3, text=mb_data['album']))
            if mb_data.get('release_date'): 
                tags.add(TDRC(encoding=3, text=mb_data['release_date'][:4]))
            if mb_data.get('album_artist'): 
                tags.add(TPE2(encoding=3, text=mb_data['album_artist']))
            
            # Cover einbetten (falls vorhanden)
            try:
                # Hier nehmen wir die Daten direkt von MusicBrainz
                cover_data = musicbrainzngs.get_image_front(mb_data['mbid_release'], size=500)
                tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Front Cover', data=cover_data))
            except:
                pass

        # SPEICHERN: v2_version=3 ist der entscheidende Fix für Windows!
        tags.save(mp3_name, v2_version=3)
        save_song(titel, interpret)
        print(f"✅ Tags erfolgreich gespeichert: {titel}")

    except Exception as e:
        print(f"❌ Fehler beim Taggen von {titel}: {e}")

def get_musicbrainz_data(titel, interpret):
    try:
        # Escaping für Sonderzeichen in der Suche
        safe_titel = sanitize_filename(titel)
        safe_interpret = sanitize_filename(interpret)
        
        # 1. Wir suchen nach SINGLES (ohne caa:true Zwang in der Query)
        query = f'recording:"{safe_titel}" AND artist:"{safe_interpret}" AND type:single'
        
        print(f"🔍 Suche nach Single: {query}")
        result = musicbrainzngs.search_recordings(query=query, limit=20)
        
        recordings = result.get('recording-list', [])
        if not recordings:
            print(f"❌ Keine Single gefunden für: {titel}")
            return None

        best_release = None
        
        # 2. Wir priorisieren innerhalb der Ergebnisse die mit Bild
        for rec in recordings:
            for rel in rec.get('release-list', []):
                has_art = rel.get('cover-art-archive', {}).get('artwork', 'false') == 'true'
                if has_art:
                    best_release = rel
                    break
            if best_release: break

        # 3. FALLBACK: Wenn keine Single mit Bild da ist, nimm die allererste Single aus der Liste
        if not best_release:
            for rec in recordings:
                if rec.get('release-list'):
                    best_release = rec['release-list'][0]
                    print(f"ℹ️ Single gefunden, aber leider ohne Cover-Art bei MusicBrainz.")
                    break

        if not best_release: return None

        return {
            'album': best_release.get('title'),
            'release_date': best_release.get('date', ''),
            'album_artist': best_release.get('artist-credit', [{}])[0].get('artist', {}).get('name', interpret),
            'mbid_release': best_release['id'],
            'release_type': 'single'
        }
    except Exception as e:
        print(f"❌ MusicBrainz Fehler: {e}")
        return None

    
def sanitize_filename(name):
    # Ersetzt alle für Windows ungültigen Zeichen durch ein Leerzeichen oder Unterstrich
    # Ungültig sind: < > : " / \ | ? *
    return re.sub(r'[<>:"/\\|?*]', '_', name)

def send_email(new_songs):
    email_to = os.environ.get("EMAIL_TO", "sven@schaider.net")
    
    if not new_songs:
        subject = "Ö3 Downloader: Keine neuen Songs"
        body = "Es wurden keine neuen Songs heruntergeladen."
    else:
        subject = f"Ö3 Downloader: {len(new_songs)} neue Songs"
        body = "Folgende Songs wurden heruntergeladen:\n\n"
        body += "\n".join([f"- {titel} - {interpret}" for titel, interpret in new_songs])
    
    message = f"To: {email_to}\nSubject: {subject}\n\n{body}"
    
    try:
        result = subprocess.run(
            ["ssmtp", email_to],
            input=message,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print(f"📧 E-Mail gesendet an {email_to}")
        else:
            print(f"❌ E-Mail Fehler: {result.stderr}")
    except Exception as e:
        print(f"❌ E-Mail Fehler: {e}")

def get_YT_URL(songs):
    ydl_opts = {'quiet': True, 'extract_flat': True}
    new_songs = []

    for titel, interpret in songs:
        if not check_song_in_db(titel, interpret):
            continue
        query = f"{titel} {interpret}"
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
            video = info['entries'][0]
            yt_url = f"https://youtube.com/watch?v={video['id']}"

            stitel = sanitize_filename(titel)
            sinterpret = sanitize_filename(interpret)
            
            # Download mit yt-dlp
            mp3_name_template = os.path.join(full_path, f"{stitel}-{sinterpret}.%(ext)s")
            mp3_name = os.path.join(full_path, f"{stitel}-{sinterpret}.mp3")
            
            cmd = [
                "yt-dlp",
                "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
                "--ffmpeg-location", "/usr/bin/ffmpeg",  # ← neu
                "-o", mp3_name_template,
                yt_url
            ]
            print(f"⬇️ Lade {titel} - {interpret} ...")

            result = subprocess.run(cmd, capture_output=True, text=True)

            if os.path.exists(mp3_name):
                set_MP3_Tags(mp3_name, titel, interpret)
                new_songs.append((titel,interpret))

    return new_songs

songs = get_songs()
new_songs = get_YT_URL(songs)
send_email(new_songs)
print("\nAlle Downloads fertig")
