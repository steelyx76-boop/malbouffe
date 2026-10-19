# ⚡ Bot d'alerte Discord Vinted - Calculatrice NumWorks (20€ - 40€)

Ce bot surveille en continu Vinted et envoie immédiatement une notification riche sur votre serveur Discord dès qu'une calculatrice **NumWorks** est mise en vente entre **20€ et 40€**.

---

## 🔍 Pourquoi cette implémentation ?

Vinted a mis en place des protections Cloudflare (TLS Fingerprinting, Turnstile, challenges JS) qui bloquent les scrapers classiques et renvoient des erreurs 403 / 404 sur les endpoints `/api/v2/`.

Ce bot utilise **`curl_cffi`** pour reproduire l'empreinte TLS exacte d'un navigateur Google Chrome moderne (`impersonate="chrome124"`). Il récupère le flux HTML/SSR de Vinted en toute transparence et extrait les annonces en temps réel sans blocage.

---

## 🚀 Démarrage rapide en 2 minutes

### 1. Créer un Webhook Discord (30 secondes)
1. Sur Discord, faites un clic droit sur votre salon textuel dédié aux alertes (ou Paramètres du salon `⚙️`).
2. Allez dans **Intégrations** > **Webhooks** > **Nouveau Webhook**.
3. Nommez-le par exemple `NumWorks Sniper` et cliquez sur **Copier l'URL du Webhook**.

### 2. Configurer le bot
Ouvrez le fichier `.env` dans ce dossier et collez votre URL :
```env
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

### 3. Tester la connexion Discord
Exécutez la commande suivante pour envoyer un message de test :
```bash
python3 vinted_numworks_discord.py --test
```
Vous devriez recevoir instantanément une confirmation sur Discord.

### 4. Lancer la surveillance en continu
```bash
python3 vinted_numworks_discord.py
```

---

## ⚙️ Paramètres disponibles dans `.env`

| Variable | Description | Valeur par défaut |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | Lien de votre Webhook Discord | `""` |
| `SEARCH_TEXT` | Terme de recherche | `numworks` |
| `MIN_PRICE` | Prix minimum en euros | `20` |
| `MAX_PRICE` | Prix maximum en euros | `40` |
| `CHECK_INTERVAL` | Intervalle de scan en secondes | `15` |
| `DISCORD_MENTION` | Mention lors d'une alerte (`@everyone`, `@here`) | `@everyone` |
| `PROXY_URL` | Proxy HTTP/SOCKS5 optionnel (ex: `http://user:pass@ip:port`) | `""` |

---

## 📦 Garder le bot allumé 24h/24 en arrière-plan

Sur macOS / Linux :
```bash
nohup python3 vinted_numworks_discord.py > vinted.log 2>&1 &
```
Pour suivre les logs :
```bash
tail -f vinted.log
```
Pour l'arrêter :
```bash
pkill -f vinted_numworks_discord.py
```
