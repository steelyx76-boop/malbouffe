#!/usr/bin/env python3
"""
⚡ Dual Real-Time Sniper : Vinted + Leboncoin
Surveille simultanément les deux plateformes pour les calculatrices NumWorks (20€ - 40€).
Envoi instantané sur Discord via Webhook.
"""

import threading
import logging
import sys
import time

from vinted_numworks_discord import run_sniper as run_vinted
from leboncoin_numworks_discord import run_leboncoin_sniper as run_lbc

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("DualSniper")

def main():
    log.info("=" * 60)
    log.info("🚀 DÉMARRAGE DU DOUBLE SNIPER : VINTED + LEBONCOIN")
    log.info("   Calculatrices NumWorks (20€ - 40€)")
    log.info("   Ancienneté max : 10 minutes")
    log.info("=" * 60)

    # Thread 1 : Vinted Sniper
    t_vinted = threading.Thread(target=run_vinted, name="Thread-Vinted", daemon=True)

    # Thread 2 : Leboncoin Sniper
    t_lbc = threading.Thread(target=run_lbc, name="Thread-Leboncoin", daemon=True)

    t_vinted.start()
    t_lbc.start()

    log.info("✅ Les deux snipers tournent en parallèle !")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Arrêt demandé par l'utilisateur.")

if __name__ == "__main__":
    main()
