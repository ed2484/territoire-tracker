"""
Friches Cartofriches — enrichissement + découverte, gratuit, sans clé API.

Télécharge le jeu de données national "Sites référencés dans Cartofriches"
(Cerema, open data, licence ouverte, mis à jour ~1x/trimestre) et produit
deux fichiers en un seul passage sur le CSV national :

1. friches.json — friches détectées dans les communes déjà suivies
   (enrichissement, comme avant).
2. candidates.json — communes NON suivies qui contiennent des friches à
   fort potentiel industriel/logistique (découverte de nouvelles cibles).

Source des données :
https://www.data.gouv.fr/datasets/sites-references-dans-cartofriches
Documentation des champs :
https://doc-datafoncier.cerema.fr/doc/export/guide/friches

IMPORTANT — limites à connaître :
- Les noms de colonnes du CSV national peuvent évoluer d'une mise à jour à
  l'autre ; ce script les détecte par motif plutôt que par nom exact, et
  affiche un avertissement si une colonne attendue est introuvable.
- La liste COMMUNES ci-dessous doit être tenue à jour à la main si tu
  ajoutes/retires des villes dans l'outil — sinon une commune ajoutée
  dans l'outil mais absente d'ici pourrait réapparaître comme "candidate"
  alors qu'elle est déjà suivie (l'outil filtre une seconde fois côté
  client pour limiter ce risque, mais la liste ici reste la source de
  vérité pour l'exclusion).
- Le score de pertinence est une heuristique simple (type de friche +
  surface + statut + pollution), PAS le calcul officiel des "indices de
  mutabilité" de Cartofriches. Il sert à trier et à alerter, pas à
  décider seul qu'une commune ou une friche est intéressante.
- Aucune commune n'est ajoutée automatiquement au portefeuille : ce
  script ne fait que produire une liste de candidates. L'ajout reste une
  décision humaine, prise dans l'outil.
"""

import csv
import io
import json
import time
import unicodedata
from datetime import datetime, timezone

import requests

COMMUNES = [
    "Avignon", "Gannat", "Yzeure", "Toulouse", "Montataire",
    "Port-Saint-Louis-du-Rhône", "Meung-sur-Loire", "Tremblay-en-France",
    "Mauguio", "Muret", "Vémars", "Arsac", "Saint-Vulbas",
    "Livron-sur-Drôme", "Chessy", "Thiers", "Le Havre", "Saint-Priest",
    "Pertuis", "Grenoble", "Uchaud", "Lezoux", "Dunkerque", "Fos-sur-Mer",
    "Valenciennes", "Metz", "Mulhouse", "Strasbourg", "Bordeaux", "Nantes",
    "Douai", "Onnaing", "Billy-Berclau", "Thionville", "Trémery",
    "Ottmarsheim", "Reims", "Genas", "Meyzieu", "Vénissieux",
    "Andrézieux-Bouthéon", "Sorgues", "Cavaillon", "Orange", "Blagnac",
    "Colomiers", "Nîmes", "Sandouville", "Orléans", "Angers", "Beauvais",
    "Lens", "Douvrin", "Hénin-Beaumont", "Saint-Omer", "Arras",
    "Compiègne", "Laon", "Soissons", "Saint-Quentin", "Saint-Avold",
    "Forbach", "Woippy", "Sarreguemines", "Huningue", "Lauterbourg",
    "Troyes", "Saint-Dizier", "Épinal", "Chaponnay", "Communay", "Annecy",
    "Montbonnot-Saint-Martin", "Chambéry", "Clermont-Ferrand", "Montluçon",
    "Montbéliard", "Bourg-en-Bresse", "Romans-sur-Isère", "Le Pontet",
    "Carpentras", "Miramas", "Istres", "Vitrolles", "Marignane",
    "Aix-en-Provence", "Castres", "Albi", "Tarbes", "Béziers", "Narbonne",
    "Carcassonne", "Cherbourg-en-Cotentin", "Caen", "Honfleur", "Penly",
    "Ormes", "Châteauroux", "Dreux", "Blois",
]

# URL stable de téléchargement du CSV national Cartofriches (data.gouv.fr)
FRICHES_CSV_URL = "https://www.data.gouv.fr/api/1/datasets/r/74feb3ed-5f9f-4ef8-8fab-b0128d569a99"
FRICHES_OUTPUT_FILE = "friches.json"
CANDIDATES_OUTPUT_FILE = "candidates.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FrichesBot/1.0)"}

TYPES_PERTINENTS = {"industrielle", "logistique", "agro-industrielle", "commerciale", "ferroviaire"}
MAX_FRICHES_PAR_COMMUNE = 6

# Seuils pour qu'une commune NON suivie remonte comme "candidate"
CANDIDATE_MIN_SCORE = 4
CANDIDATE_MIN_SURFACE_M2 = 5000
MAX_CANDIDATES = 40


def normalize(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return s.lower().strip()


def resolve_insee(commune):
    try:
        res = requests.get(
            "https://api-adresse.data.gouv.fr/search/",
            params={"q": commune, "type": "municipality", "limit": 1},
            headers=HEADERS, timeout=15,
        )
        res.raise_for_status()
        feats = res.json().get("features", [])
        if feats:
            return feats[0]["properties"].get("citycode")
    except Exception as exc:
        print(f"[WARN] impossible de résoudre le code INSEE de {commune} : {exc}")
    return None


def find_column(fieldnames, candidates):
    lower_map = {f.lower(): f for f in fieldnames}
    for cand in candidates:
        for lower_name, original in lower_map.items():
            if cand in lower_name:
                return original
    return None


def score_pertinence(site_type, surface, statut, pollution):
    score = 0
    t = normalize(site_type)
    if any(k in t for k in TYPES_PERTINENTS):
        score += 3

    s = normalize(statut)
    if "reconvert" in s:
        score -= 4  # terminé : aucun intérêt pour une stratégie de positionnement en amont
    elif "projet" in s:
        score -= 2  # un porteur de projet est déjà engagé : probablement trop tard, mais pas à exclure
    elif "potentiel" in s:
        score += 2  # rien n'est encore engagé : c'est là que le lobbying en amont a le plus de valeur

    surf_value = None
    try:
        surf_value = float(str(surface).replace(",", "."))
        if surf_value >= 15000:
            score += 2
        elif surf_value >= 5000:
            score += 1
    except (TypeError, ValueError):
        pass
    if pollution and "avere" in normalize(pollution):
        score -= 1
    return score, surf_value


def department_code(insee):
    if not insee:
        return ""
    return insee[:3] if insee.startswith("97") else insee[:2]


def main():
    print("Résolution des codes INSEE des communes déjà suivies...")
    insee_by_commune = {}
    for commune in COMMUNES:
        code = resolve_insee(commune)
        if code:
            insee_by_commune[code] = commune
        time.sleep(0.3)
    tracked_insee = set(insee_by_commune.keys())
    print(f"{len(tracked_insee)}/{len(COMMUNES)} communes suivies résolues en code INSEE.")

    print("Téléchargement du jeu de données national Cartofriches (~25 Mo)...")
    resp = requests.get(FRICHES_CSV_URL, headers=HEADERS, timeout=180)
    resp.raise_for_status()
    raw_text = resp.content.decode("utf-8", errors="replace")

    try:
        dialect = csv.Sniffer().sniff(raw_text[:5000], delimiters=";,\t")
        print(f"[INFO] Séparateur détecté : {dialect.delimiter!r}")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
        print("[INFO] Détection automatique échouée, séparateur ';' utilisé par défaut.")

    reader = csv.DictReader(io.StringIO(raw_text), dialect=dialect)
    fieldnames = reader.fieldnames or []
    print(f"[INFO] {len(fieldnames)} colonnes détectées, aperçu : {fieldnames[:10]}")

    col_insee = find_column(fieldnames, ["comm_insee", "insee"])
    col_commnom = find_column(fieldnames, ["comm_nom"])
    col_nom = find_column(fieldnames, ["site_nom"])
    col_type = find_column(fieldnames, ["site_type"])
    col_statut = find_column(fieldnames, ["site_statut"])
    col_adresse = find_column(fieldnames, ["site_adresse"])
    col_surface = find_column(fieldnames, ["unite_fonciere_surface", "site_surface", "surface"])
    col_pollution = find_column(fieldnames, ["sol_pollution_existe", "pollution"])
    col_lat = find_column(fieldnames, ["lat"])
    col_lng = find_column(fieldnames, ["long", "lng", "lon"])
    col_url = find_column(fieldnames, ["site_url", "source_url"])

    required = {"code INSEE": col_insee, "nom commune": col_commnom, "nom du site": col_nom,
                "type": col_type, "statut": col_statut, "surface": col_surface}
    missing = [name for name, col in required.items() if col is None]
    if missing:
        print(f"[ATTENTION] colonnes introuvables dans le CSV, à vérifier à la main : {missing}")
    if not col_insee:
        print("[ERREUR] impossible de trouver la colonne code INSEE, arrêt.")
        return

    tracked_result = {}
    candidate_communes = {}  # insee -> {nom, dept, friches: [...]}
    total_matched = 0
    total_rows = 0
    sample_insee_values = []

    for row in reader:
        total_rows += 1
        code = (row.get(col_insee) or "").strip()
        if total_rows <= 3:
            sample_insee_values.append(code)
        if not code:
            continue

        entry = {
            "nom": (row.get(col_nom) or "").strip() if col_nom else "",
            "type": (row.get(col_type) or "").strip() if col_type else "",
            "statut": (row.get(col_statut) or "").strip() if col_statut else "",
            "adresse": (row.get(col_adresse) or "").strip() if col_adresse else "",
            "surface_m2": (row.get(col_surface) or "").strip() if col_surface else "",
            "pollution": (row.get(col_pollution) or "").strip() if col_pollution else "",
            "lat": (row.get(col_lat) or "").strip() if col_lat else "",
            "lng": (row.get(col_lng) or "").strip() if col_lng else "",
            "source_url": (row.get(col_url) or "").strip() if col_url else "",
        }
        score, surf_value = score_pertinence(entry["type"], entry["surface_m2"], entry["statut"], entry["pollution"])
        entry["score_pertinence"] = score

        if code in tracked_insee:
            commune = insee_by_commune[code]
            tracked_result.setdefault(commune, []).append(entry)
            total_matched += 1
            continue

        # Commune non suivie : ne remonte en "candidate" que si le site est vraiment intéressant
        if score >= CANDIDATE_MIN_SCORE and (surf_value or 0) >= CANDIDATE_MIN_SURFACE_M2:
            commune_nom = (row.get(col_commnom) or "").strip() if col_commnom else ""
            bucket = candidate_communes.setdefault(code, {
                "commune": commune_nom,
                "insee": code,
                "departement": department_code(code),
                "friches": [],
            })
            bucket["friches"].append(entry)

    print(f"[INFO] {total_rows} lignes lues au total. Exemples de codes INSEE lus : {sample_insee_values}")

    for commune, friches in tracked_result.items():
        friches.sort(key=lambda f: f["score_pertinence"], reverse=True)
        tracked_result[commune] = friches[:MAX_FRICHES_PAR_COMMUNE]

    candidates_list = []
    for bucket in candidate_communes.values():
        bucket["friches"].sort(key=lambda f: f["score_pertinence"], reverse=True)
        bucket["meilleure_friche"] = bucket["friches"][0]
        bucket["nb_friches_pertinentes"] = len(bucket["friches"])
        bucket["score_commune"] = sum(f["score_pertinence"] for f in bucket["friches"])
        bucket["friches"] = bucket["friches"][:3]  # on garde un aperçu, pas tout
        candidates_list.append(bucket)

    candidates_list.sort(key=lambda b: b["score_commune"], reverse=True)
    candidates_list = candidates_list[:MAX_CANDIDATES]

    friches_output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "communes": tracked_result,
    }
    with open(FRICHES_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(friches_output, f, ensure_ascii=False, indent=2)

    candidates_output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidates": candidates_list,
    }
    with open(CANDIDATES_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(candidates_output, f, ensure_ascii=False, indent=2)

    print(f"Terminé : {total_matched} friches sur communes suivies, "
          f"{len(candidates_list)} communes candidates hors portefeuille détectées.")


if __name__ == "__main__":
    main()
