#!/usr/bin/env python3
"""
Scraper AideDD (Règles D&D 5e) pour Coffre Obsidian avec Numérotation Hiérarchique X.Y
======================================================================================
- Page d'accueil : regles.md
- Sections racines : 1.0-nom-de-section.md, 2.0-nom.md, etc.
- Sous-pages : 1.1-sous-page.md, 1.2-..., 2.1-..., etc.
"""

import os
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse, urldefrag
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

BASE_URL = "https://www.aidedd.org/dnd-5/unearthed-arcana"
ALLOWED_PREFIXES = [
    "https://www.aidedd.org/dnd-5/unearthed-arcana/",
]

OUTPUT_DIR = "./AideDD_Obsidian_Vault/Regles"
REQUEST_DELAY = 0.3
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

def clean_target_url(url: str) -> str:
    """Nettoie les ancres et query strings pour éviter les boucles."""
    clean, _ = urldefrag(url)
    parsed = urlparse(clean)
    path = parsed.path
    if path.endswith((".php", ".html")):
        path = re.sub(r'\.(php|html)$', '', path)
    return f"{parsed.scheme}://{parsed.netloc}{path}"

def is_valid_regle_url(url: str) -> bool:
    clean = clean_target_url(url)
    return any(clean.startswith(prefix) for prefix in ALLOWED_PREFIXES)

def sanitize_slug(text: str) -> str:
    """Génère un slug propre pour le nom du fichier."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r'[^\w\s-]', '', text).strip().lower()
    return re.sub(r'[-\s]+', '-', text) or "page"

def extract_main_content(soup: BeautifulSoup):
    for selector in [
        "nav", "header", "footer", ".menu", ".menuglobal", ".pub", ".ads",
        "#sidebar", ".sidebar", ".share", ".social", ".ariane", ".fil-ariane",
        "script", "style", "noscript", ".noprint"
    ]:
        for el in soup.select(selector):
            el.decompose()

    return (
            soup.find("div", class_=re.compile(r"content|bloc|article|main|col1|regles", re.I))
            or soup.find("article")
            or soup.find("main")
            or soup.find("body")
    )

def build_hierarchical_filenames(discovered_urls: list) -> dict:
    """
    Génère la table de correspondance { URL: 'X.Y-nom-de-page' }
    """
    url_to_filename = {}

    # 1. Isoler les segments relatifs par rapport à /regles/
    parsed_base = urlparse(BASE_URL).path.strip('/')

    # Structure pour mémoriser l'ordre des sections de premier niveau
    category_order = []
    subpages_by_cat = {}

    for url in discovered_urls:
        parsed_path = urlparse(url).path.strip('/')
        # Retirer le préfixe 'regles'
        rel_path = parsed_path
        if rel_path.startswith(parsed_base):
            rel_path = rel_path[len(parsed_base):].strip('/')

        # Cas particulier de la racine
        if not rel_path:
            url_to_filename[url] = "regles"
            continue

        segments = [sanitize_slug(seg) for seg in rel_path.split('/') if seg]
        cat = segments[0]

        if cat not in category_order:
            category_order.append(cat)
            subpages_by_cat[cat] = []

        # Stocker les sous-pages associées à cette catégorie
        if len(segments) > 1:
            subpages_by_cat[cat].append((url, segments[1:]))
        else:
            subpages_by_cat[cat].insert(0, (url, [])) # La page racine de la catégorie (X.0)

    # 2. Attribuer les numéros X.Y
    for cat_idx, cat in enumerate(category_order, start=1):
        items = subpages_by_cat[cat]
        sub_counter = 1

        for url, sub_segments in items:
            if not sub_segments:
                # Page principale de la catégorie -> X.0-nom
                filename = f"{cat_idx}.0-{cat}"
            else:
                # Sous-page -> X.Y-nom ou X.Y.Z-nom
                sub_name = "-".join(sub_segments)
                filename = f"{cat_idx}.{sub_counter}-{sub_name}"
                sub_counter += 1

            url_to_filename[url] = filename

    return url_to_filename

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)

    visited_urls = set()
    queue = [clean_target_url(BASE_URL)]
    ordered_discovered_urls = []
    cached_soups = {}

    print("🔍 [1/3] Exploration des pages de règles...")

    while queue:
        current_url = queue.pop(0)
        if current_url in visited_urls:
            continue

        visited_urls.add(current_url)
        ordered_discovered_urls.append(current_url)
        print(f"  [{len(visited_urls)}] Découverte : {current_url}")

        try:
            resp = session.get(current_url, timeout=8)
            if resp.status_code != 200:
                continue

            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            cached_soups[current_url] = soup

            # Recherche des sous-liens
            for a in soup.find_all("a", href=True):
                target = clean_target_url(urljoin(current_url, a["href"]))
                if is_valid_regle_url(target) and target not in visited_urls and target not in queue:
                    queue.append(target)

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"  -> Erreur lors de la requête sur {current_url}: {e}")

    print("\n🗂️  [2/3] Calcul des numérotations X.Y...")
    url_to_filename_map = build_hierarchical_filenames(ordered_discovered_urls)

    for u, f in url_to_filename_map.items():
        print(f"  - {u}  ==>  {f}.md")

    print(f"\n📝 [3/3] Génération des fichiers Markdown...")

    for page_url, file_name in url_to_filename_map.items():
        soup = cached_soups.get(page_url)
        if not soup:
            continue

        h1 = soup.find("h1")
        page_title = h1.get_text().strip() if h1 else file_name

        # Conversion des liens internes vers les Wikilinks Obsidian préfixés
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("#"):
                continue
            clean_href = clean_target_url(urljoin(page_url, href))
            if is_valid_regle_url(clean_href) and clean_href in url_to_filename_map:
                target_file = url_to_filename_map[clean_href]
                link_text = a.get_text().strip() or target_file
                a.replace_with(f"[[{target_file}|{link_text}]]")

        content_el = extract_main_content(soup)
        if not content_el:
            continue

        md_content = md(
            str(content_el),
            heading_style="ATX",
            bullets="-",
            strip=["script", "style"]
        ).strip()
        md_content = re.sub(r'\n{3,}', '\n\n', md_content)

        frontmatter = f"""---
title: "{page_title}"
source: "{page_url}"
tags:
  - dnd5
  - regles
---

# {page_title}

"""
        full_md = frontmatter + md_content
        file_path = os.path.join(OUTPUT_DIR, f"{file_name}.md")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_md)

    print(f"\nTerminé avec succès ! {len(url_to_filename_map)} fichiers créés dans '{OUTPUT_DIR}'.")

if __name__ == "__main__":
    main()