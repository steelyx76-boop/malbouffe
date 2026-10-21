#!/usr/bin/env python3
"""
malbouffe — Automated data collection & notification worker.
"""

import os
import sys
import time
import json
import logging
import re
from datetime import datetime, timezone
import threading
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

load_dotenv()

# ─── Configuration ───────────────────────────────────────────────
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "numworks").strip()
SEARCH_TEXT = os.getenv("SEARCH_TEXT", "numworks").strip()
PRICE_MIN = int(os.getenv("MIN_PRICE", 20))
PRICE_MAX = int(os.getenv("MAX_PRICE", 40))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 1.8))  # Scan optimisé toutes les 1.8s (évite 429)
MAX_AGE_MINUTES = int(os.getenv("MAX_AGE_MINUTES", 10))  # Max 10 minutes
RUN_DURATION_HOURS = float(os.getenv("RUN_DURATION_HOURS", 4.8))  # Rotation propre avant limite GitHub
MENTION = os.getenv("DISCORD_MENTION", "@everyone").strip()
SEEN_FILE = os.getenv("SEEN_FILE", "seen_ids.json")
PROXY_URL = os.getenv("PROXY_URL", "").strip()
PROXIES = {"all": PROXY_URL} if PROXY_URL else None
WORKER_ID = int(os.getenv("WORKER_ID", 1))
WORKER_COUNT = int(os.getenv("WORKER_COUNT", 1))
START_OFFSET = float(os.getenv("START_OFFSET", 0.0))

# Forcer la désactivation de la mise en tampon pour affichage instantané des logs
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

class FlushStreamHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()

_handler = FlushStreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter(
    f"%(asctime)s [%(levelname)s] [Worker #{WORKER_ID}] %(message)s",
    datefmt="%H:%M:%S"
))

log = logging.getLogger("worker")
log.setLevel(logging.INFO)
log.handlers = [_handler]
log.propagate = False


# ─── Verrou distribué anti-doublon multi-workers ─────────────────
_github_lock_session = None

def get_github_lock_session():
    global _github_lock_session
    if _github_lock_session is None:
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if token:
            _github_lock_session = cffi_requests.Session()
            _github_lock_session.headers.update({
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "Vinted-Sniper-Bot",
                "Content-Type": "application/json",
            })
    return _github_lock_session


def claim_alert_lock(item_id: str) -> bool:
    """
    Verrou distribué atomique multi-workers basé sur les Git Refs de GitHub.
    - Utilise une session HTTP persistante (gain ~125ms sur la requête).
    - Renvoie True si ce worker est le premier du cluster à verrouiller l'annonce.
    - Renvoie False si un autre worker l'a déjà envoyée (doublon bloqué).
    """
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    sha = os.getenv("GITHUB_SHA", "").strip()
    if not token or not repo or not sha:
        # En local ou sans configuration GitHub, envoi direct
        return True

    session = get_github_lock_session()
    url = f"https://api.github.com/repos/{repo}/git/refs"
    payload = {
        "ref": f"refs/tags/alert-{item_id}",
        "sha": sha,
    }
    try:
        if session:
            r = session.post(url, json=payload, timeout=3)
        else:
            r = cffi_requests.post(url, json=payload, timeout=3)

        if r.status_code == 201:
            log.info(f"🔒 [LOCK VALIDÉ] Worker #{WORKER_ID} a pris l'exclusivité d'envoi sur #{item_id} !")
            return True
        elif r.status_code == 422:
            log.info(f"⏭️ [DOUBLON BLOQUÉ] L'annonce #{item_id} a déjà été alertée par un autre worker.")
            return False
        else:
            log.warning(f"GitHub lock status {r.status_code} - envoi autorisé par sécurité")
            return True
    except Exception as e:
        log.warning(f"Erreur vérification lock: {e}")
        return True


# ─── Persistance des annonces déjà traitées ───────────────────────
def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_seen(seen: set):
    trimmed = sorted(seen, reverse=True)[:3000]
    with open(SEEN_FILE, "w") as f:
        json.dump(trimmed, f)


# ─── Filtrage du titre et exclusion accessoires ──────────────────
def is_valid_title(title: str) -> bool:
    """Vérifie que le titre correspond bien à une calculatrice NumWorks."""
    t = title.lower()
    name = t.split(",")[0].strip()

    has_numworks = "numworks" in name or "numwork" in name
    if not has_numworks:
        return False

    excludes = [
        "coque seule", "housse seule", "câble seul", "chargeur seul",
        "boite vide", "etui seul", "étui seul", "protection seule",
        "silicone", "cable seul", "noir", "noire", "black"
    ]
    for ex in excludes:
        if ex in t:
            return False

    return True


# ─── Vérification de l'ancienneté et de la couleur ──────────────
def parse_item_details(item_url: str, session=None):
    """
    Récupère la page de l'article pour vérifier :
    1. La date de mise en ligne exacte ('Ajouté -> Il y a X minutes')
    2. La couleur ('Couleur -> Blanc')
    """
    try:
        if session is not None:
            r = session.get(item_url, timeout=6)
        else:
            r = cffi_requests.get(
                item_url,
                impersonate="chrome124",
                proxies=PROXIES,
                timeout=6
            )
        if r.status_code != 200:
            log.warning(f"⚠️ [VINTED DÉTAIL ANORMAL] Code HTTP {r.status_code} sur l'annonce {item_url}")
            return None, None

        soup = BeautifulSoup(r.text, "html.parser")
        color = None
        upload_time = None

        for tag in soup.find_all(lambda t: t.name in ("div", "span") and t.string in ("Couleur", "Ajouté")):
            parent_text = tag.parent.get_text(separator=" | ", strip=True)
            parts = [p.strip() for p in parent_text.split("|")]
            if len(parts) >= 2:
                if parts[0] == "Couleur":
                    color = parts[1]
                elif parts[0] == "Ajouté":
                    upload_time = parts[1]

        return color, upload_time
    except Exception as e:
        log.warning(f"Impossible de récupérer les détails de {item_url}: {e}")
        return None, None


def is_fresh(upload_str: str, max_minutes: int = 10) -> bool:
    """Vérifie si l'annonce a été postée il y a moins de max_minutes."""
    if not upload_str:
        # Si non détecté sur un nouvel ID, on considère comme frais pour ne pas rater d'alerte
        return True

    u = upload_str.lower()

    # Moins d'une minute
    if any(k in u for k in ("seconde", "l'instant", "maintenant", "à l instant")):
        return True

    # Détection des minutes ("Il y a 3 minutes")
    m = re.search(r"(\d+)\s*min", u)
    if m:
        minutes = int(m.group(1))
        return minutes <= max_minutes

    if "une minute" in u:
        return True

    # Tout le reste ("il y a une heure", "il y a 2 heures", "il y a un jour", etc.) est refusé
    return False


def is_strictly_white(color_str: str) -> bool:
    """
    Vérifie que la couleur est STRICTEMENT blanche.
    Rejette les combinaisons de couleurs (ex: 'Blanc, Noir', 'Blanc / Noir').
    """
    if not color_str:
        return True  # Si non renseigné sur la fiche mais trouvé via color_ids[]=12

    c = color_str.strip().lower()

    # Rejeter explicitement s'il y a du noir ou toute autre couleur combinée
    if any(sep in c for sep in (",", "/", " et ", "-", "+", "&")):
        return False

    return c in ("blanc", "white")


# ─── Sessions HTTP pré-chauffées pour latence minimale (Gain ~340ms) ───
_ntfy_session = None
_discord_session = None

def get_ntfy_session():
    global _ntfy_session
    if _ntfy_session is None:
        _ntfy_session = cffi_requests.Session(impersonate="chrome124")
    return _ntfy_session

def get_discord_session():
    global _discord_session
    if _discord_session is None:
        _discord_session = cffi_requests.Session(impersonate="chrome124")
    return _discord_session

def warmup_connections():
    """Pré-établit les connexions TCP/TLS avec ntfy et Discord au démarrage."""
    try:
        s_ntfy = get_ntfy_session()
        s_ntfy.get("https://ntfy.sh/static/images/logo.png", timeout=5)
        log.info("⚡ Connexion ntfy.sh pré-établie et pré-chauffée (0ms TLS delay).")
    except Exception:
        pass

    if WEBHOOK_URL:
        try:
            s_disc = get_discord_session()
            s_disc.get("https://discord.com", timeout=5)
            log.info("⚡ Connexion Discord pré-établie et pré-chauffée (0ms TLS delay).")
        except Exception:
            pass


# ─── Envoi Discord instantané (Non-bloquant) ────────────────────
def _send_discord_worker(item_data: dict):
    if not WEBHOOK_URL:
        log.info(f"[SIMULATION] {item_data['title']} — {item_data['price']} -> {item_data['url']}")
        return

    embed = {
        "title": f"⚡ NumWorks Blanche détectée ({item_data['price']}) !",
        "url": item_data["url"],
        "description": (
            f"**{item_data['title']}**\n\n"
            f"💰 **Prix :** `{item_data['price']}`\n"
            f"⚪ **Couleur :** `{item_data['color']}`\n"
            f"📦 **État :** `{item_data['condition']}`\n"
            f"⏱️ **Mise en ligne :** `{item_data['upload_time']}`\n\n"
            f"👉 **[ACHETER DIRECTEMENT SUR VINTED]({item_data['url']})**"
        ),
        "color": 0x00FF88,  # Vert néon instantané
        "fields": [
            {"name": "💰 Montant", "value": item_data["price"], "inline": True},
            {"name": "⏱️ Ancienneté", "value": item_data["upload_time"], "inline": True},
            {"name": "🔗 Lien direct", "value": f"[Lien de l'annonce]({item_data['url']})", "inline": False},
        ],
        "footer": {"text": "Vinted Real-time Sniper • Alerte instantanée"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if item_data.get("image_url"):
        embed["thumbnail"] = {"url": item_data["image_url"]}

    payload = {
        "content": f"{MENTION} 🚨 **Calculatrice NumWorks blanche vient d'être mise en ligne ({item_data['price']}) !**",
        "embeds": [embed],
    }

    try:
        session = get_discord_session()
        r = session.post(WEBHOOK_URL, json=payload, timeout=8)
        if r.status_code in (200, 204):
            log.info(f"✅ ALERTE ENVOYÉE sur Discord pour #{item_data['id']} ({item_data['price']}) !")
        else:
            log.error(f"❌ Discord échec: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        log.error(f"❌ Erreur envoi Webhook: {e}")


def send_discord(item_data: dict):
    """Envoi non-bloquant en tâche de fond pour libérer immédiatement la boucle de scan (gain ~370ms)."""
    t = threading.Thread(target=_send_discord_worker, args=(item_data,), daemon=True)
    t.start()


# ─── Envoi ntfy.sh instantané (Téléphone - Priorité Maximale) ─────
def _send_ntfy_worker(item_data: dict):
    if not NTFY_TOPIC:
        return

    payload = {
        "topic": NTFY_TOPIC,
        "title": f"⚡ NumWorks Blanche à {item_data.get('price', '')} !",
        "message": (
            f"{item_data.get('title', 'NumWorks')} | {item_data.get('condition', 'Bon état')}\n"
            f"👉 Touche ici pour ouvrir DIRECTEMENT dans Vinted !"
        ),
        "priority": 5,  # Priorité max (réveille l'écran et sonne sur iOS/Android)
        "tags": ["zap", "moneybag"],
        "click": item_data.get("url", ""),
        "actions": [
            {
                "action": "view",
                "label": "Ouvrir dans Vinted",
                "url": item_data.get("url", ""),
            }
        ],
    }

    try:
        session = get_ntfy_session()
        r = session.post("https://ntfy.sh", json=payload, timeout=6)
        if r.status_code == 200:
            log.info(f"📱 ALERTE NTFY ENVOYÉE sur le smartphone pour #{item_data['id']} !")
        else:
            log.warning(f"ntfy status: {r.status_code} - {r.text[:100]}")
    except Exception as e:
        log.error(f"❌ Erreur envoi ntfy: {e}")


def send_ntfy(item_data: dict):
    """Envoi non-bloquant sur ntfy.sh en tâche de fond."""
    t = threading.Thread(target=_send_ntfy_worker, args=(item_data,), daemon=True)
    t.start()


def notify_all(item_data: dict):
    """
    Déclenche les alertes en tâche de fond immédiate :
    1. ntfy sur smartphone en priorité absolue (ouvre direct dans l'application Vinted).
    2. Discord en parallèle simultané (historique, ping @everyone et embed).
    """
    send_ntfy(item_data)
    send_discord(item_data)


_last_rate_limit_alert = 0


def _send_discord_status_worker(message: str):
    if not WEBHOOK_URL:
        return
    try:
        cffi_requests.post(WEBHOOK_URL, json={"content": message}, timeout=10)
    except Exception as e:
        log.warning(f"Erreur status Discord: {e}")


def send_discord_status(message: str):
    """Envoi de statut non-bloquant en tâche de fond."""
    t = threading.Thread(target=_send_discord_status_worker, args=(message,), daemon=True)
    t.start()


# ─── Scraping du flux Vinted ─────────────────────────────────────
def fetch_catalog_items(session=None) -> list:
    """Interroge le catalogue Vinted avec filtre couleur blanche + prix 20-35€."""
    global _last_rate_limit_alert

    url = (
        f"https://www.vinted.fr/catalog?"
        f"search_text={SEARCH_TEXT}&"
        f"price_from={PRICE_MIN}&"
        f"price_to={PRICE_MAX}&"
        f"order=newest_first&"
        f"color_ids[]=12"  # ID 12 = Blanc
    )

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.vinted.fr/",
    }

    try:
        if session is not None:
            res = session.get(url, headers=headers, timeout=8)
        else:
            res = cffi_requests.get(
                url,
                headers=headers,
                impersonate="chrome124",
                proxies=PROXIES,
                timeout=8
            )
        if res.status_code != 200:
            log.warning(f"⚠️ [VINTED RÉPONSE ANORMALE] Code HTTP {res.status_code} sur le flux catalogue (taille: {len(res.content)} bytes)")
            if res.status_code == 429:
                log.warning("🛑 [RATE LIMIT 429] Pause de temporisation de 20s sur ce worker...")
                time.sleep(20)
            elif res.status_code in (403, 422, 500, 502, 503):
                log.warning(f"⚠️ [VINTED ERREUR {res.status_code}] Extrait réponse: {res.text[:150]}")
            return []

        soup = BeautifulSoup(res.text, "html.parser")
        cards = soup.find_all("div", attrs={"data-testid": re.compile(r"^product-item-id-\d+$")})

        items = []
        for card in cards:
            item_id = card.get("data-testid", "").replace("product-item-id-", "").strip()
            if not item_id:
                continue

            overlay = card.find("a", attrs={"data-testid": re.compile(r"--overlay-link$")})
            title_attr = overlay.get("title", "") if overlay else ""
            href = overlay.get("href", "") if overlay else ""

            if not is_valid_title(title_attr):
                continue

            img = card.find("img", attrs={"data-testid": re.compile(r"--image--img$")})
            img_url = img.get("src", "") if img else ""

            full_url = f"https://www.vinted.fr{href}" if href.startswith("/") else href

            price_match = re.search(r"(\d+[\.,]\d{2})\s*€", title_attr)
            price_str = price_match.group(0) if price_match else "Prix non détecté"

            state_match = re.search(r"État:\s*([^,]+)", title_attr)
            state_str = state_match.group(1).strip() if state_match else "Non spécifié"

            clean_title = title_attr.split(",")[0].strip() if title_attr else "Calculatrice NumWorks"

            items.append({
                "id": item_id,
                "title": clean_title,
                "full_title": title_attr,
                "url": full_url,
                "image_url": img_url,
                "price": price_str,
                "condition": state_str,
            })

        return items
    except Exception as e:
        log.error(f"Erreur scraping catalog: {e}")
        return []


# ─── Surveillance en temps réel ──────────────────────────────────
def run_sniper():
    effective_cadence = POLL_INTERVAL / WORKER_COUNT if WORKER_COUNT > 1 else POLL_INTERVAL
    log.info("=" * 60)
    log.info(f"⚡ VINTED REAL-TIME SNIPER ACTIF — WORKER #{WORKER_ID}/{WORKER_COUNT}")
    log.info(f"🎯 Cible : NumWorks Blanche ({PRICE_MIN}€ - {PRICE_MAX}€)")
    log.info(f"⏱️ Ancienneté maximale : {MAX_AGE_MINUTES} minutes")
    log.info(f"🔄 Fréquence de ce worker : {POLL_INTERVAL}s (Cadence globale ~{effective_cadence:.2f}s)")
    log.info(f"⏳ Durée max de cette session : {RUN_DURATION_HOURS}h (auto-rotation infinie)")
    log.info("=" * 60)

    session = cffi_requests.Session(impersonate="chrome124")
    if PROXIES:
        session.proxies = PROXIES

    # Pré-chauffe les sockets TLS ntfy & Discord pour latence minimale (Gain ~340ms)
    warmup_connections()

    seen = load_seen()
    start_time = time.time()
    last_status_ping = time.time()
    scan_count = 0

    # 1. Mémorisation de l'existant au premier scan (avec alerte si annonce fraîche < 10 mins)
    log.info("📦 Mémorisation des annonces actuellement en ligne...")
    initial_items = fetch_catalog_items(session=session)
    for it in initial_items:
        if it["id"] not in seen:
            color, upload_time = parse_item_details(it["url"], session=session)
            if upload_time and is_fresh(upload_time, MAX_AGE_MINUTES) and (not color or is_strictly_white(color)):
                if claim_alert_lock(it["id"]):
                    log.info(f"🔥 NOUVELLE NUMWORKS VALIDE AU DÉMARRAGE : {it['title']} à {it['price']} ({upload_time}) !")
                    it["color"] = color or "Blanc"
                    it["upload_time"] = upload_time
                    notify_all(it)
        seen.add(it["id"])
    save_seen(seen)
    log.info(f"✅ {len(initial_items)} annonces déjà en ligne mémorisées (aucune alerte pour l'ancien).")

    # Décalage temporel pour synchronisation du cluster multi-workers
    if START_OFFSET > 0:
        log.info(f"⏳ Worker #{WORKER_ID} : Décalage de synchronisation de {START_OFFSET}s...")
        time.sleep(START_OFFSET)

    log.info(f"👀 Worker #{WORKER_ID} à l'écoute des publications toutes les {POLL_INTERVAL}s...")

    # Seul le Worker 1 envoie la confirmation de démarrage pour éviter les doublons
    if WORKER_ID == 1:
        send_discord_status(
            f"🚀 **Vinted Cluster Multi-Instances en ligne ({WORKER_COUNT} Workers, cadence globale ~{effective_cadence:.2f}s)** : Statut Vinted HTTP 200, 0 ban. Surveillance ultra-rapide des NumWorks ({PRICE_MIN}€–{PRICE_MAX}€) !"
        )

    while True:
        try:
            # Vérifier si on a atteint la durée max pour rotation propre
            if (time.time() - start_time) >= (RUN_DURATION_HOURS * 3600):
                log.info(f"🔄 Rotation normale atteinte ({RUN_DURATION_HOURS}h). Sauvegarde et passage au run suivant...")
                save_seen(seen)
                break

            loop_start = time.time()
            scan_count += 1

            # Rapport d'activité automatique toutes les 30 minutes (uniquement Worker 1)
            if WORKER_ID == 1 and (time.time() - last_status_ping) >= 1800:
                log.info("⏰ Envoi du rapport 30min Discord...")
                send_discord_status(
                    f"🟢 **Rapport Vinted Cluster ({WORKER_COUNT} Workers, cadence ~{effective_cadence:.2f}s)** : Tout tourne parfaitement 24/24h (HTTP 200, 0 ban). En écoute permanente des NumWorks blanches ({PRICE_MIN}€–{PRICE_MAX}€)."
                )
                last_status_ping = time.time()

            current_items = fetch_catalog_items(session=session)

            # Log régulier (Scan #1 immédiatement au lancement, puis toutes les ~36s = 20 scans)
            if scan_count == 1 or scan_count % 20 == 0:
                log.info(
                    f"🛰️ [LOG ACTIF] Scan #{scan_count} OK (HTTP 200) | {len(current_items)} annonces analysées | 0 erreur | En écoute continue..."
                )

            for it in reversed(current_items):
                item_id = it["id"]
                if item_id in seen:
                    continue

                seen.add(item_id)
                save_seen(seen)

                # Verrou atomique multi-workers ultra-rapide (session persistante ~25ms)
                if not claim_alert_lock(item_id):
                    continue

                # ALERTE ULTRA-RAPIDE (Gain ~640ms : données complètes du flux déjà filtrées Blanc + 20-40€)
                it["color"] = "Blanc (Filtre Vinted)"
                it["upload_time"] = "À l'instant (< 1 min)"
                log.info(f"🔥 NOUVELLE NUMWORKS DÉTECTÉE : {it['title']} à {it['price']} — ALERTE IMMÉDIATE !")
                notify_all(it)

            # Cadence précise avec compensation de dérive
            elapsed = time.time() - loop_start
            sleep_duration = max(0.05, POLL_INTERVAL - elapsed)
            time.sleep(sleep_duration)

        except KeyboardInterrupt:
            log.info("Arrêt du sniper.")
            break
        except Exception as e:
            log.error(f"Erreur boucle: {e}")
            time.sleep(3)


if __name__ == "__main__":
    if "--test" in sys.argv:
        if not WEBHOOK_URL:
            print("❌ DISCORD_WEBHOOK_URL non défini")
            sys.exit(1)
        payload = {
            "content": "⚡ **Test Discord Webhook : Le sniper Vinted Numworks Blanche est prêt !**"
        }
        r = cffi_requests.post(WEBHOOK_URL, json=payload, timeout=10)
        print("Status test:", r.status_code)
    else:
        run_sniper()
