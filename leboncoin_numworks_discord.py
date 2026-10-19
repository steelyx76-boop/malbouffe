#!/usr/bin/env python3
"""
⚡ Leboncoin Real-Time Sniper — Calculatrices NumWorks Blanches (20€ - 40€)
- Mode de remise : LIVRAISON obligatoire (shippable=1 + vérification attributs)
- Couleur : Blanche uniquement (exclusion des coques/housses/câbles/noir/livres)
- Ancienneté maximale : 10 minutes (date exacte first_publication_date)
- Alerte instantanée Discord Webhook avec lien direct
"""

import os
import sys
import time
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

load_dotenv()

# ─── Configuration ───────────────────────────────────────────────
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
SEARCH_TEXT = os.getenv("SEARCH_TEXT_LBC", "numworks").strip()
PRICE_MIN = int(os.getenv("MIN_PRICE", 20))
PRICE_MAX = int(os.getenv("MAX_PRICE", 40))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_LBC", 8))  # 8s recommandé pour Leboncoin
MAX_AGE_MINUTES = int(os.getenv("MAX_AGE_MINUTES", 10))
MENTION = os.getenv("DISCORD_MENTION", "@everyone").strip()
SEEN_FILE = os.getenv("SEEN_FILE_LBC", "seen_lbc_ids.json")
PROXY_URL = os.getenv("PROXY_URL", "").strip()
PROXIES = {"all": PROXY_URL} if PROXY_URL else None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("LBC-Sniper")


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


def is_valid_title(title: str) -> bool:
    """Vérifie que l'annonce correspond bien à une calculatrice NumWorks blanche."""
    t = title.lower()
    has_numworks = "numworks" in t or "numwork" in t
    if not has_numworks:
        return False

    # Exclusion accessoires, livres, et mention de couleur noire
    excludes = [
        "coque", "housse", "câble", "cable", "chargeur",
        "boite vide", "boîte vide", "etui", "étui", "protection",
        "silicone", "noir", "noire", "livre"
    ]
    for ex in excludes:
        if ex in t:
            return False

    return True


def is_fresh_date(date_str: str, max_minutes: int = 10) -> bool:
    """Vérifie si l'annonce a été publiée il y a moins de max_minutes."""
    if not date_str:
        return True
    try:
        pub_dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        diff = now - pub_dt
        return diff.total_seconds() <= (max_minutes * 60)
    except Exception as e:
        log.warning(f"Erreur parsing date '{date_str}': {e}")
        return True


def send_discord(ad: dict):
    if not WEBHOOK_URL:
        log.info(f"[SIMULATION] {ad['title']} — {ad['price']}€ -> {ad['url']}")
        return

    embed = {
        "title": f"⚡ [Leboncoin] NumWorks Blanche détectée ({ad['price']}€) !",
        "url": ad["url"],
        "description": (
            f"**{ad['title']}**\n\n"
            f"💰 **Prix :** `{ad['price']}€`\n"
            f"🚚 **Mode de remise :** `Livraison disponible ({ad.get('shipping_type', 'colis')})`\n"
            f"⚪ **Couleur :** `Blanche`\n"
            f"📍 **Ville :** `{ad.get('city', 'France')}`\n"
            f"⏱️ **Mise en ligne :** `{ad.get('pub_date', 'À l instant')}`\n\n"
            f"👉 **[ACHETER SUR LEBONCOIN AVEC LIVRAISON]({ad['url']})**"
        ),
        "color": 0xFF6E14,  # Orange officiel Leboncoin
        "fields": [
            {"name": "💰 Prix", "value": f"{ad['price']} €", "inline": True},
            {"name": "🚚 Remise", "value": "Livraison acceptée", "inline": True},
            {"name": "📍 Ville", "value": ad.get("city", "France"), "inline": True},
            {"name": "🔗 Lien direct", "value": f"[Accéder à l'annonce]({ad['url']})", "inline": False},
        ],
        "footer": {"text": "Leboncoin Real-time Sniper • Livraison disponible"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if ad.get("image_url"):
        embed["thumbnail"] = {"url": ad["image_url"]}

    payload = {
        "content": f"{MENTION} 🚨 **Calculatrice NumWorks blanche avec LIVRAISON sur Leboncoin ({ad['price']}€) !**",
        "embeds": [embed],
    }

    try:
        r = cffi_requests.post(WEBHOOK_URL, json=payload, timeout=10)
        if r.status_code in (200, 204):
            log.info(f"✅ ALERTE LEBONCOIN ENVOYÉE sur Discord pour #{ad['id']} ({ad['price']}€) !")
        else:
            log.error(f"❌ Discord échec: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        log.error(f"❌ Erreur envoi Webhook: {e}")


def fetch_leboncoin_ads() -> list:
    """Interroge la recherche Leboncoin avec filtre livraison obligatoire (shippable=1) et tri récent."""
    url = f"https://www.leboncoin.fr/recherche?text={SEARCH_TEXT}&price={PRICE_MIN}-{PRICE_MAX}&sort=time&shippable=1"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.leboncoin.fr/",
    }

    try:
        res = cffi_requests.get(
            url,
            headers=headers,
            impersonate="chrome124",
            proxies=PROXIES,
            timeout=12
        )
        if res.status_code != 200:
            log.warning(f"Leboncoin HTTP status {res.status_code}")
            return []

        soup = BeautifulSoup(res.text, "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if not script or not script.string:
            log.warning("Script __NEXT_DATA__ non trouvé sur Leboncoin")
            return []

        data = json.loads(script.string)
        ads_raw = data.get("props", {}).get("pageProps", {}).get("searchData", {}).get("ads", [])

        items = []
        for ad in ads_raw:
            ad_id = str(ad.get("list_id", ""))
            subject = ad.get("subject", "")

            # 1. Validation du titre (calculatrice blanche, pas d'accessoires)
            if not is_valid_title(subject):
                continue

            # 2. Validation prix
            prices = ad.get("price", [])
            price = prices[0] if prices else 0
            if not (PRICE_MIN <= price <= PRICE_MAX):
                continue

            # 3. Validation MODE DE REMISE : LIVRAISON OBLIGATOIRE
            attrs = {att["key"]: att.get("value") for att in ad.get("attributes", [])}
            is_shippable = (attrs.get("shippable") == "true")
            shipping_type = attrs.get("shipping_type", "")
            if not is_shippable or shipping_type == "face_to_face":
                continue

            # 4. Vérification que l'achat n'est pas déjà en cours ou clôturé
            transaction_status = attrs.get("transaction_status")
            if transaction_status in ("pending", "sold", "closed"):
                continue

            pub_date = ad.get("first_publication_date") or ad.get("index_date") or ""
            city = ad.get("location", {}).get("city", "France")
            ad_url = ad.get("url") or f"https://www.leboncoin.fr/ad/{ad_id}"

            images = ad.get("images", {})
            img_url = images.get("thumb_url") or (images.get("urls", [""])[0] if images.get("urls") else "")

            items.append({
                "id": ad_id,
                "title": subject,
                "price": price,
                "pub_date": pub_date,
                "city": city,
                "url": ad_url,
                "image_url": img_url,
                "shipping_type": shipping_type,
            })

        return items
    except Exception as e:
        log.error(f"Erreur scraping Leboncoin: {e}")
        return []


def run_leboncoin_sniper():
    log.info("=" * 60)
    log.info("⚡ LEBONCOIN REAL-TIME SNIPER ACTIF")
    log.info(f"🎯 Cible : NumWorks Blanche ({PRICE_MIN}€ - {PRICE_MAX}€)")
    log.info("🚚 Mode de remise : LIVRAISON UNIQUEMENT")
    log.info(f"⏱️ Ancienneté maximale : {MAX_AGE_MINUTES} minutes")
    log.info(f"🔄 Fréquence de scan : {POLL_INTERVAL}s")
    log.info("=" * 60)

    seen = load_seen()

    log.info("📦 Mémorisation des annonces Leboncoin existantes avec livraison...")
    initial_ads = fetch_leboncoin_ads()
    for a in initial_ads:
        seen.add(a["id"])
    save_seen(seen)
    log.info(f"✅ {len(initial_ads)} annonces Leboncoin existantes avec livraison mémorisées.")
    log.info("👀 À l'écoute des nouvelles publications Leboncoin à la seconde près...")

    while True:
        try:
            time.sleep(POLL_INTERVAL)
            current_ads = fetch_leboncoin_ads()

            for ad in reversed(current_ads):
                ad_id = ad["id"]
                if ad_id in seen:
                    continue

                seen.add(ad_id)
                save_seen(seen)

                # Vérifier l'ancienneté (<= 10 minutes)
                if ad.get("pub_date") and not is_fresh_date(ad["pub_date"], MAX_AGE_MINUTES):
                    log.info(f"⏭️ Annonce Leboncoin #{ad_id} ignorée (trop ancienne: {ad['pub_date']})")
                    continue

                log.info(f"🔥 NOUVELLE NUMWORKS LEBONCOIN (LIVRAISON) : {ad['title']} à {ad['price']}€ ({ad['pub_date']}) !")
                send_discord(ad)

        except KeyboardInterrupt:
            log.info("Arrêt du sniper Leboncoin.")
            break
        except Exception as e:
            log.error(f"Erreur boucle Leboncoin: {e}")
            time.sleep(5)


if __name__ == "__main__":
    if "--test" in sys.argv:
        if not WEBHOOK_URL:
            print("❌ DISCORD_WEBHOOK_URL non configuré")
            sys.exit(1)
        r = cffi_requests.post(
            WEBHOOK_URL,
            json={"content": "⚡ **Test : Le sniper Leboncoin NumWorks (Livraison uniquement) est connecté !**"},
            timeout=10
        )
        print("Test Discord Leboncoin:", r.status_code)
    else:
        run_leboncoin_sniper()
