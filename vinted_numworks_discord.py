#!/usr/bin/env python3
"""
Vinted Sniper & Discord Alert Bot - NumWorks (20€ - 40€)
Bypasses Cloudflare using Chrome TLS impersonation (curl_cffi).
"""

import os
import re
import sys
import time
import json
import logging
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
from curl_cffi import requests
from dotenv import load_dotenv

# Charger les variables depuis le fichier .env
load_dotenv()

# Configuration
CONFIG = {
    "discord_webhook_url": os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
    "search_text": os.getenv("SEARCH_TEXT", "numworks").strip(),
    "price_from": float(os.getenv("MIN_PRICE", 20)),
    "price_to": float(os.getenv("MAX_PRICE", 40)),
    "check_interval": int(os.getenv("CHECK_INTERVAL", 15)),
    "mention": os.getenv("DISCORD_MENTION", "@everyone").strip(),
    "proxy_url": os.getenv("PROXY_URL", "").strip(),
    "seen_items_file": os.getenv("SEEN_ITEMS_FILE", "seen_items.json").strip(),
    # Mots-clés requis (au moins un doit être présent)
    "require_keywords": [k.strip().lower() for k in os.getenv("REQUIRE_KEYWORDS", "calculatrice,numworks,numwork").split(",") if k.strip()],
    # Mots-clés exclus (évite les fausses alertes comme les coques ou câbles seuls)
    "exclude_keywords": [k.strip().lower() for k in os.getenv("EXCLUDE_KEYWORDS", "coque seule,housse seule,boite vide,câble seul,chargeur seul").split(",") if k.strip()],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("VintedNumworks")

class VintedNumworksScraper:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.seen_file = config.get("seen_items_file", "seen_items.json")
        self.seen_items = self._load_seen_items()
        self.webhook_url = config.get("discord_webhook_url", "")
        self.proxy_url = config.get("proxy_url", "")
        self.proxies = {"all": self.proxy_url} if self.proxy_url else None

    def _load_seen_items(self) -> set:
        if os.path.exists(self.seen_file):
            try:
                with open(self.seen_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return set(data)
            except Exception as e:
                logger.warning(f"Impossible de charger {self.seen_file}: {e}")
        return set()

    def _save_seen_items(self):
        try:
            with open(self.seen_file, "w", encoding="utf-8") as f:
                json.dump(list(self.seen_items), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Erreur d'écriture dans {self.seen_file}: {e}")

    def is_valid_item(self, title: str) -> bool:
        """Vérifie si le titre correspond aux critères d'inclusion et d'exclusion."""
        lower_title = title.lower()

        # 1. Exclusion de faux positifs (ex: coque vendue seule)
        for excl in self.config.get("exclude_keywords", []):
            if excl in lower_title:
                return False

        # 2. Extraire le nom du produit (avant la première virgule)
        #    Format typique : "Calculatrice numworks, Marque: NUMWORKS, État: ..."
        product_name = lower_title.split(",")[0].strip()

        # 3. Le nom du produit doit contenir "numworks" ou "numwork"
        has_numworks = "numworks" in product_name or "numwork" in product_name

        # 4. Le nom du produit doit aussi contenir un mot lié à calculatrice
        has_calc = any(w in product_name for w in ("calculatrice", "calculette", "calc numworks", "calc"))

        # On accepte si le titre du produit mentionne clairement numworks
        # ET qu'il s'agit d'une calculatrice (ou que le titre est juste "numworks")
        if not has_numworks:
            return False

        # Si le titre contient juste "numworks" sans autre qualificatif, on accepte aussi
        if has_numworks and (has_calc or product_name.strip() in ("numworks", "numwork")):
            return True

        # Accepter aussi les titres courts comme "Numworks" ou "Numworks N0110"
        if has_numworks:
            return True

        return False

    def fetch_listings(self) -> List[Dict[str, Any]]:
        """Scrape la recherche Vinted via curl_cffi avec émulation TLS Chrome."""
        url = (
            f"https://www.vinted.fr/catalog?"
            f"search_text={self.config['search_text']}&"
            f"price_from={int(self.config['price_from'])}&"
            f"price_to={int(self.config['price_to'])}&"
            f"order=newest_first"
        )

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.vinted.fr/",
        }

        try:
            res = requests.get(
                url,
                headers=headers,
                impersonate="chrome124",
                proxies=self.proxies,
                timeout=15
            )

            if res.status_code != 200:
                logger.error(f"Réponse HTTP anormale de Vinted: {res.status_code}")
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

                if not self.is_valid_item(title_attr):
                    continue

                img = card.find("img", attrs={"data-testid": re.compile(r"--image--img$")})
                img_url = img.get("src", "") if img else ""

                full_url = f"https://www.vinted.fr{href}" if href.startswith("/") else href

                # Récupération du prix (ex: "25.00 €")
                price_match = re.search(r"(\d+[\.,]\d{2})\s*€", title_attr)
                price_str = price_match.group(0) if price_match else "Prix non détecté"

                # Récupération de l'état (ex: "Très bon état", "Neuf avec étiquette")
                state_match = re.search(r"État:\s*([^,]+)", title_attr)
                state_str = state_match.group(1).strip() if state_match else "Non spécifié"

                # Nom nettoyé
                clean_title = title_attr.split(",")[0].strip() if title_attr else "Calculatrice NumWorks"

                items.append({
                    "id": item_id,
                    "title": clean_title,
                    "full_info": title_attr,
                    "url": full_url,
                    "image_url": img_url,
                    "price": price_str,
                    "condition": state_str,
                })

            return items

        except Exception as e:
            logger.error(f"Erreur de scraping Vinted: {e}")
            return []

    def send_discord_alert(self, item: Dict[str, Any]):
        """Envoie une alerte riche sur Discord via Webhook."""
        if not self.webhook_url:
            logger.info(f"🔔 [SIMULATION SANS WEBHOOK] Nouvelle Numworks: {item['title']} ({item['price']}) -> {item['url']}")
            return

        embed = {
            "title": f"⚡ Nouvelle NumWorks en ligne : {item['price']} !",
            "url": item["url"],
            "description": f"**Titre :** {item['title']}\n**État :** {item['condition']}\n**Prix :** {item['price']}",
            "color": 0x09B1BA,  # Cyan signature Vinted
            "fields": [
                {"name": "💰 Montant", "value": f"`{item['price']}`", "inline": True},
                {"name": "📦 Condition", "value": f"`{item['condition']}`", "inline": True},
                {"name": "🚀 Achat immédiat", "value": f"[Ouvrir sur Vinted]({item['url']})", "inline": False},
            ],
            "footer": {
                "text": "Antigravity Vinted Sniper • Alerte instantanée"
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }

        if item.get("image_url"):
            embed["image"] = {"url": item["image_url"]}

        content_msg = f"{self.config.get('mention', '')} 🚨 **Calculatrice NumWorks disponible entre {int(self.config['price_from'])}€ et {int(self.config['price_to'])}€ !**".strip()

        payload = {
            "content": content_msg,
            "embeds": [embed]
        }

        try:
            r = requests.post(self.webhook_url, json=payload, timeout=10)
            if r.status_code in (200, 204):
                logger.info(f"✅ Notification Discord envoyée pour l'annonce #{item['id']} ({item['price']})")
            else:
                logger.error(f"❌ Échec envoi Discord (HTTP {r.status_code}): {r.text}")
        except Exception as e:
            logger.error(f"❌ Exception envoi Webhook: {e}")

    def test_discord_webhook(self):
        """Envoie un message test sur Discord pour vérifier le Webhook."""
        if not self.webhook_url:
            logger.error("Veuillez renseigner DISCORD_WEBHOOK_URL dans votre fichier .env")
            return False

        payload = {
            "content": "✅ **Test de connexion réussi !** Le bot d'alerte NumWorks Vinted est prêt à envoyer des alertes."
        }
        try:
            r = requests.post(self.webhook_url, json=payload, timeout=10)
            if r.status_code in (200, 204):
                logger.info("Message test Discord envoyé avec succès !")
                return True
            else:
                logger.error(f"Échec test Discord (HTTP {r.status_code}): {r.text}")
                return False
        except Exception as e:
            logger.error(f"Erreur test Discord: {e}")
            return False

    def start(self):
        """Démarre la boucle de surveillance en temps réel."""
        interval = self.config.get("check_interval", 15)
        logger.info("=" * 60)
        logger.info(f"🎯 Surveillance Vinted lancée pour : '{self.config['search_text']}'")
        logger.info(f"💶 Plage de prix : {self.config['price_from']}€ - {self.config['price_to']}€")
        logger.info(f"⏱️ Intervalle de vérification : {interval} secondes")
        if self.webhook_url:
            logger.info("🔗 Discord Webhook configuré.")
        else:
            logger.warning("⚠️ Aucun DISCORD_WEBHOOK_URL configuré (mode simulation dans la console).")
        logger.info("=" * 60)

        # Première passe : mémoriser l'existant sans spammer Discord
        logger.info("📦 Mémorisation des annonces actuellement en ligne...")
        initial_items = self.fetch_listings()
        for it in initial_items:
            self.seen_items.add(it["id"])
        self._save_seen_items()
        logger.info(f"✅ {len(initial_items)} annonces existantes mémorisées. En écoute des nouvelles publications...")

        # Boucle d'alerte
        while True:
            try:
                time.sleep(interval)
                current_items = self.fetch_listings()
                for it in reversed(current_items):
                    if it["id"] not in self.seen_items:
                        logger.info(f"🔥 NOUVELLE ANNONCE DÉTECTÉE : {it['title']} à {it['price']} !")
                        self.seen_items.add(it["id"])
                        self._save_seen_items()
                        self.send_discord_alert(it)
            except KeyboardInterrupt:
                logger.info("Arrêt de la surveillance.")
                break
            except Exception as e:
                logger.error(f"Erreur dans la boucle: {e}")
                time.sleep(5)

if __name__ == "__main__":
    scraper = VintedNumworksScraper(CONFIG)

    if "--test" in sys.argv:
        scraper.test_discord_webhook()
    else:
        scraper.start()
