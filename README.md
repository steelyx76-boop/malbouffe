# ⚡ Dual Sniper : Vinted + Leboncoin — NumWorks (20€ - 40€)

Ce système surveille en temps réel **Vinted ET Leboncoin** et envoie immédiatement une alerte sur Discord dès qu'une calculatrice **NumWorks** est mise en vente entre **20€ et 40€**.

---

## 🎯 Fonctionnalités

- **Double plateforme :** Vinted + Leboncoin en parallèle.
- **Vinted :** Filtre officiel couleur blanche (`color_ids[]=12`) + vérification stricte.
- **Leboncoin :** Extraction directe des annonces via `__NEXT_DATA__` triées par date récente (`sort=time`).
- **Ancienneté max :** 10 minutes (rejet automatique de tout article plus ancien).
- **Zéro faux positif :** Élimine coques, câbles, pochettes et étuis vendus seuls.
- **Alertes Discord instantanées :** 
  - 🟢 Badge Vert pour Vinted
  - 🟠 Badge Orange pour Leboncoin
  - Photos, prix exact, ville (Leboncoin) et bouton d'achat direct cliquable.
- **100% Cloud (GitHub Actions) :** Tourne 24h/24 sans nécessiter d'ordinateur allumé.
