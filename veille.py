"""
Veille territoriale — collecte quotidienne gratuite.

Interroge le flux RSS d'actualité Bing (public, sans clé API) pour chacune
des communes suivies, filtre sur des mots-clés pertinents pour la
prospection territoriale, et écrit le résultat dans veille.json.

Ce fichier est ensuite lu par un outil externe (fetch côté client).
Aucun appel à un modèle IA ici : uniquement de l'agrégation de flux RSS.

IMPORTANT : si tu ajoutes/retires des communes ailleurs, mets aussi à jour
la liste COMMUNES ci-dessous à la main — ce script ne lit aucun état
externe automatiquement.
"""

import json
import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import requests
import feedparser

COMMUNES = [
    "Arles", "Laval", "Cannes", "Le Mans", "Les Pennes-Mirabeau", "Nice",
    "Avignon", "Gannat", "Toulouse", "Montataire",
    "Port-Saint-Louis-du-Rhône", "Muret", "Saint-Vulbas", "Le Havre",
    "Saint-Priest", "Grenoble", "Lezoux", "Dunkerque", "Fos-sur-Mer",
    "Valenciennes", "Metz", "Mulhouse", "Strasbourg", "Bordeaux", "Nantes",
    "Thionville", "Reims", "Orléans", "Angers", "Saint-Dizier",
    "Pontoise", "Le Raincy", "Lampertheim", "Grasse", "Courbevoie",
    "Argenteuil", "Saint-Étienne", "Alès",
    "Meung-sur-Loire", "Tremblay-en-France", "Mauguio", "Livron-sur-Drôme",
    "Onnaing", "Billy-Berclau", "Ottmarsheim", "Meyzieu",
    "Andrézieux-Bouthéon", "Blagnac", "Nîmes", "Beauvais",
    "La Grande-Motte", "Amiens",
    "Yzeure", "Vémars", "Arsac", "Chessy", "Uchaud", "Genas", "Vénissieux",
    "Sorgues", "Orange", "Sandouville", "Lens", "Douvrin", "Saint-Omer",
    "Arras", "Compiègne", "Laon", "Soissons", "Saint-Quentin",
    "Saint-Avold", "Forbach", "Woippy", "Sarreguemines", "Huningue",
    "Troyes", "Épinal", "Annecy", "Chambéry", "Clermont-Ferrand",
    "Montluçon", "Bourg-en-Bresse", "Romans-sur-Isère", "Miramas",
    "Istres", "Vitrolles", "Marignane", "Aix-en-Provence", "Castres",
    "Albi", "Tarbes", "Béziers", "Narbonne", "Carcassonne",
    "Cherbourg-en-Cotentin", "Caen", "Honfleur", "Ormes", "Blois",
    "Trémery", "Hénin-Beaumont", "Lauterbourg", "Chaponnay", "Communay",
    "Montbonnot-Saint-Martin", "Montbéliard",
]

KEYWORDS = (
    '("zone d\'activité" OR "conseil municipal" OR PLUi OR ZAN OR '
    '"arrêté municipal" OR "permis de construire" OR délibération OR '
    '"implantation industrielle" OR "entrepôt logistique" OR '
    '"parc d\'activités")'
)
MAX_ITEMS_PER_COMMUNE = 8
OUTPUT_FILE = "veille.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; VeilleBot/1.0)"}
REQUEST_DELAY_SECONDS = 1.2  # reste poli avec le serveur, évite les blocages


def fetch_news(commune: str):
    query = f'"{commune}" {KEYWORDS}'
    url = (
        "https://www.bing.com/news/search?q=" + urllib.parse.quote(query)
        + "&format=RSS&mkt=fr-FR&setLang=fr"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[WARN] {commune} : échec de la requête ({exc})")
        return []

    feed = feedparser.parse(resp.content)
    items = []
    for entry in feed.entries[:MAX_ITEMS_PER_COMMUNE]:
        source = entry.get("source", "")
        if isinstance(source, dict):
            source = source.get("title", "")
        items.append({
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", "").strip(),
            "source": source,
            "pubDate": entry.get("published", ""),
        })
    return items


def load_existing():
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("communes", {})
    except Exception:
        return {}


def main():
    existing = load_existing()
    result = {}

    for commune in COMMUNES:
        new_items = fetch_news(commune)
        prev_items = existing.get(commune, {}).get("items", [])
        seen_links = {it["link"] for it in new_items if it.get("link")}
        merged = new_items + [it for it in prev_items if it.get("link") not in seen_links]
        merged = merged[:MAX_ITEMS_PER_COMMUNE]

        result[commune] = {
            "items": merged,
            "last_checked": datetime.now(timezone.utc).isoformat(),
        }
        time.sleep(REQUEST_DELAY_SECONDS)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "communes": result,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = sum(len(v["items"]) for v in result.values())
    print(f"Terminé : {len(result)} communes traitées, {total} articles en base.")


if __name__ == "__main__":
    main()
