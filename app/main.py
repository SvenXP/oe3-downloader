from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, GRP1, TALB, TDRC, TDOR, TSOP, TSO2, TPOS, TRCK, TMED, TXXX, APIC, error
from bs4 import BeautifulSoup
import time
import re
import subprocess
import os
import yt_dlp
import json
import urllib.request
import urllib.parse
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
    
    url = base_url
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

        tags.delall('TSSE')

        # Basis-Informationen setzen
        tags.add(TIT2(encoding=3, text=titel))
        tags.add(TPE1(encoding=3, text=interpret))
        tags.add(GRP1(encoding=3, text=["Ö3 Hörercharts"]))
        
        mb_data = get_musicbrainz_data(titel, interpret)
        if not mb_data:
            print(f"ℹ️ Fallback auf iTunes für: {titel}")
            mb_data = get_itunes_data(titel, interpret)

        if mb_data:
            if mb_data.get('album'):
                tags.add(TALB(encoding=3, text=mb_data['album']))
            if mb_data.get('release_date'):
                tags.add(TDRC(encoding=3, text=mb_data['release_date'][:4]))
            if mb_data.get('album_artist'):
                tags.add(TPE2(encoding=3, text=mb_data['album_artist']))
            if mb_data.get('artist_sort'):
                tags.add(TSOP(encoding=3, text=mb_data['artist_sort']))
                tags.add(TSO2(encoding=3, text=mb_data['artist_sort']))
            disc_num = mb_data.get('disc_number')
            total_discs = mb_data.get('total_discs') or mb_data.get('disc_count')
            if disc_num and total_discs:
                tags.add(TPOS(encoding=3, text=f"{disc_num}/{total_discs}"))
            elif disc_num:
                tags.add(TPOS(encoding=3, text=str(disc_num)))
            total_tracks = mb_data.get('total_tracks') or mb_data.get('track_count')
            if total_tracks:
                tags.add(TRCK(encoding=3, text=str(total_tracks)))
            if mb_data.get('media'):
                tags.add(TMED(encoding=3, text=mb_data['media']))
            if mb_data.get('original_release_date'):
                tags.add(TDOR(encoding=3, text=mb_data['original_release_date']))
            if mb_data.get('release_country'):
                tags.add(TXXX(encoding=3, desc='RELEASECOUNTRY', text=mb_data['release_country']))
            if mb_data.get('release_status'):
                tags.add(TXXX(encoding=3, desc='RELEASESTATUS', text=mb_data['release_status']))
            if mb_data.get('release_type'):
                tags.add(TXXX(encoding=3, desc='RELEASETYPE', text=mb_data['release_type']))

            try:
                cover_data = None
                if mb_data.get('mbid_release'):
                    try:
                        cover_data = musicbrainzngs.get_image_front(mb_data['mbid_release'], size=500)
                    except Exception:
                        print(f"⚠️ Kein MusicBrainz-Cover, versuche iTunes...")

                if not cover_data:
                    cover_url = mb_data.get('cover_url')
                    if not cover_url:
                        itunes = get_itunes_data(titel, interpret)
                        cover_url = itunes.get('cover_url') if itunes else None
                    if cover_url:
                        with urllib.request.urlopen(cover_url) as r:
                            cover_data = r.read()

                if cover_data:
                    tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Front Cover', data=cover_data))
            except Exception as e:
                print(f"⚠️ Cover konnte nicht geladen werden: {e}")

        # SPEICHERN: v2_version=3 ist der entscheidende Fix für Windows!
        tags.save(mp3_name, v2_version=3)
        save_song(titel, interpret)
        print(f"✅ Tags erfolgreich gespeichert: {titel}")

    except Exception as e:
        print(f"❌ Fehler beim Taggen von {titel}: {e}")

COMPILATION_TYPES = {'compilation', 'soundtrack', 'mixtape/street', 'dj-mix'}

def is_compilation(rel):
    rg = rel.get('release-group', {})
    rg_type = rg.get('type', '').lower()
    sec_types = [s.lower() for s in rg.get('secondary-type-list', [])]
    return rg_type in COMPILATION_TYPES or any(t in COMPILATION_TYPES for t in sec_types)

def find_best_release(recordings):
    best = None
    for rec in recordings:
        for rel in rec.get('release-list', []):
            if is_compilation(rel):
                continue
            if rel.get('cover-art-archive', {}).get('artwork', 'false') == 'true':
                return rel
            if not best:
                best = rel
    return best

def get_musicbrainz_data(titel, interpret):
    try:
        safe_titel = sanitize_filename(titel)
        safe_interpret = sanitize_filename(interpret)

        print(f"🔍 Suche nach Single: {titel}")
        result = musicbrainzngs.search_recordings(
            query=f'recording:"{safe_titel}" AND artist:"{safe_interpret}" AND type:single', limit=20)
        recordings = result.get('recording-list', [])
        best_release = find_best_release(recordings)

        if not best_release:
            print(f"ℹ️ Keine Single gefunden, suche Album: {titel}")
            result = musicbrainzngs.search_recordings(
                query=f'recording:"{safe_titel}" AND artist:"{safe_interpret}" AND type:album', limit=20)
            recordings = result.get('recording-list', [])
            best_release = find_best_release(recordings)

        if not best_release:
            print(f"❌ Keine MusicBrainz-Daten gefunden für: {titel}")
            return None

        mbid = best_release['id']
        data = {
            'album': best_release.get('title'),
            'release_date': best_release.get('date', ''),
            'album_artist': best_release.get('artist-credit', [{}])[0].get('artist', {}).get('name', interpret),
            'mbid_release': mbid,
        }

        try:
            detail = musicbrainzngs.get_release_by_id(mbid, includes=['artist-credits', 'media', 'release-groups'])['release']
            artist_credit = detail.get('artist-credit', [{}])
            if artist_credit:
                data['artist_sort'] = artist_credit[0].get('artist', {}).get('sort-name', '')
            medium = detail.get('medium-list', [{}])[0]
            data['disc_number'] = medium.get('position', '')
            data['total_discs'] = detail.get('medium-count', '')
            data['total_tracks'] = medium.get('track-count', '')
            data['media'] = medium.get('format', '')
            data['release_country'] = detail.get('country', '')
            data['release_status'] = detail.get('status', '')
            rg = detail.get('release-group', {})
            data['release_type'] = rg.get('primary-type', '')
            data['original_release_date'] = rg.get('first-release-date', '')
        except Exception as e:
            print(f"⚠️ MusicBrainz Detailabruf fehlgeschlagen: {e}")

        return data
    except Exception as e:
        print(f"❌ MusicBrainz Fehler: {e}")
        return None

    
def get_itunes_data(titel, interpret):
    try:
        query = urllib.parse.quote(f"{titel} {interpret}")
        url = f"https://itunes.apple.com/search?term={query}&entity=song&limit=5"
        with urllib.request.urlopen(url) as r:
            data = json.loads(r.read())
        results = data.get('results', [])
        if not results:
            print(f"❌ Keine iTunes-Daten gefunden für: {titel}")
            return None
        hit = results[0]
        cover_url = hit.get('artworkUrl100', '').replace('100x100', '600x600')
        print(f"✅ iTunes-Daten gefunden für: {titel}")
        return {
            'album': hit.get('collectionName'),
            'release_date': hit.get('releaseDate', '')[:4],
            'album_artist': hit.get('artistName', interpret),
            'cover_url': cover_url,
            'track_count': hit.get('trackCount'),
            'disc_number': hit.get('discNumber'),
            'disc_count': hit.get('discCount'),
            'release_country': hit.get('country'),
            'release_type': hit.get('collectionType'),
        }
    except Exception as e:
        print(f"❌ iTunes Fehler: {e}")
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
    
    message = f"From: {email_to}\nTo: {email_to}\nSubject: {subject}\n\n{body}"
    
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
        downloaded = load_songs_db()
        if f"{titel}|{interpret}" in downloaded:
            print(f"⏭️ Bereits vorhanden: {titel} - {interpret}")
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
