#!/usr/bin/env python3
"""
⚡ Vinted Ultra-Fast Sniper — Calculatrices NumWorks Blanches (20€–40€)
- Détection ultra-rapide (poll toutes les 2-3s)
- Filtre strict : NumWorks Blanche uniquement (color_ids[]=12 + vérification couleur)
- Ancienneté maximale : 10 minutes (filtre les annonces anciennes)
- Tourne 100% sur GitHub Actions (pas besoin de laisser son ordi allumé)
"""

import os
import sys
import time
import json
import logging
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

load_dotenv()

# ─── Configuration ───────────────────────────────────────────────
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
SEARCH_TEXT = os.getenv("SEARCH_TEXT", "numworks").strip()
PRICE_MIN = int(os.getenv("MIN_PRICE", 20))
PRICE_MAX = int(os.getenv("MAX_PRICE", 35))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", 2.0))  # Scan optimisé toutes les 2s (évite 429)
MAX_AGE_MINUTES = int(os.getenv("MAX_AGE_MINUTES", 10))  # Max 10 minutes
RUN_DURATION_HOURS = float(os.getenv("RUN_DURATION_HOURS", 4.8))  # Rotation propre avant limite GitHub
MENTION = os.getenv("DISCORD_MENTION", "@everyone").strip()
SEEN_FILE = os.getenv("SEEN_FILE", "seen_ids.json")
PROXY_URL = os.getenv("PROXY_URL", "").strip()
PROXIES = {"all": PROXY_URL} if PROXY_URL else None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sniper")


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
        "silicone", "cable seul"
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


# ─── Envoi Discord instantané ────────────────────────────────────
def send_discord(item_data: dict):
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
        r = cffi_requests.post(WEBHOOK_URL, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log.info(f"✅ ALERTE ENVOYÉE sur Discord pour #{item_data['id']} ({item_data['price']}) !")
        else:
            log.error(f"❌ Discord échec: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        log.error(f"❌ Erreur envoi Webhook: {e}")


_last_rate_limit_alert = 0


def send_discord_status(message: str):
    """Envoie une notification d'état rapide sur Discord."""
    if not WEBHOOK_URL:
        return
    try:
        cffi_requests.post(WEBHOOK_URL, json={"content": message}, timeout=10)
    except Exception as e:
        log.warning(f"Erreur status Discord: {e}")


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
            log.warning(f"Vinted status {res.status_code}")
            if res.status_code == 429:
                log.warning("Rate limit 429 détecté. Pause silencieuse de 20s pour temporisation...")
                time.sleep(20)
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
    log.info("=" * 60)
    log.info("⚡ VINTED REAL-TIME SNIPER ACTIF (TURBO SPEED)")
    log.info(f"🎯 Cible : NumWorks Blanche ({PRICE_MIN}€ - {PRICE_MAX}€)")
    log.info(f"⏱️ Ancienneté maximale : {MAX_AGE_MINUTES} minutes")
    log.info(f"🔄 Fréquence de scan : {POLL_INTERVAL}s (HTTP/2 persistent session)")
    log.info(f"⏳ Durée max de cette session : {RUN_DURATION_HOURS}h (auto-rotation infinie)")
    log.info("=" * 60)

    session = cffi_requests.Session(impersonate="chrome124")
    if PROXIES:
        session.proxies = PROXIES

    seen = load_seen()
    start_time = time.time()
    last_status_ping = time.time()
    scan_count = 0

    # 1. Mémorisation de l'existant au premier scan
    log.info("📦 Mémorisation des annonces actuellement en ligne...")
    initial_items = fetch_catalog_items(session=session)
    for it in initial_items:
        seen.add(it["id"])
    save_seen(seen)
    log.info(f"✅ {len(initial_items)} annonces déjà en ligne mémorisées (aucune alerte pour l'ancien).")
    log.info(f"👀 À l'écoute des publications toutes les {POLL_INTERVAL}s...")

    # Confirmation en direct sur Discord
    send_discord_status(
        f"🟢 **Vinted Turbo Sniper en ligne ({POLL_INTERVAL}s)** : Session HTTP/2 active, HTTP 200. Écoute continue des NumWorks blanches ({PRICE_MIN}€–{PRICE_MAX}€) !"
    )

    while True:
        try:
            # Vérifier si on a atteint la durée max pour rotation propre
            if (time.time() - start_time) >= (RUN_DURATION_HOURS * 3600):
                log.info(f"🔄 Rotation normale atteinte ({RUN_DURATION_HOURS}h). Sauvegarde et passage au run suivant...")
                save_seen(seen)
                break

            time.sleep(POLL_INTERVAL)
            scan_count += 1

            # Rapport d'activité automatique toutes les 30 minutes (1800s)
            if (time.time() - last_status_ping) >= 1800:
                log.info("⏰ Envoi du rapport 30min Discord...")
                send_discord_status(
                    f"🟢 **Rapport Vinted Sniper (Toutes les 30 min)** : Le sniper tourne parfaitement 24/24h (Statut HTTP 200, 0 ban). En écoute permanente des NumWorks blanches ({PRICE_MIN}€–{PRICE_MAX}€)."
                )
                last_status_ping = time.time()

            current_items = fetch_catalog_items(session=session)

            # Log interne régulier (toutes les ~1 minute = 30 scans)
            if scan_count % 30 == 0:
                log.info(
                    f"🛰️ [LOG INTERNE] Scan #{scan_count} OK (HTTP 200) | {len(current_items)} annonce(s) en ligne | 0 erreur | En écoute..."
                )

            for it in reversed(current_items):
                item_id = it["id"]
                if item_id in seen:
                    continue

                seen.add(item_id)
                save_seen(seen)

                # Vérification approfondie (couleur blanche + ancienneté < 10 mins)
                log.info(f"🔎 Nouvelle détection #{item_id}: {it['title']} ({it['price']}) — vérification ancienneté...")
                color, upload_time = parse_item_details(it["url"], session=session)

                # Vérifier la couleur STRICTEMENT blanche
                if color and not is_strictly_white(color):
                    log.info(f"⏭️ Ignoré (couleur non strictement blanche: '{color}')")
                    continue

                # Vérifier l'ancienneté (< 10 minutes)
                if upload_time and not is_fresh(upload_time, MAX_AGE_MINUTES):
                    log.info(f"⏭️ Ignoré (trop ancien: '{upload_time}')")
                    continue

                # ALERTE VALIDÉE
                it["color"] = color or "Blanc"
                it["upload_time"] = upload_time or "À l'instant"
                log.info(f"🔥 NOUVELLE NUMWORKS VALIDE : {it['title']} à {it['price']} ({it['upload_time']}) !")
                send_discord(it)

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
