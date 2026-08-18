#!/usr/bin/env python3
"""
Scraper AideDD (Règles D&D 5e) pour Coffre Obsidian
===================================================
Ce script parcourt récursivement la section Règles d'AideDD (https://www.aidedd.org/regles/),
extrait le contenu principal de chaque page, convertit le HTML en Markdown propre,
adapte les liens internes pour le format Obsidian ([[NomDePage]]) et sauvegarde
le tout dans un dossier prêt à être ouvert comme coffre (vault) Obsidian.

Dépendances requises :
    pip install requests beautifulsoup4 markdownify urllib3
"""

import os
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse, urldefrag
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

# ================= Configuration =================
BASE_URL = "https://www.aidedd.org/regles/"
ALLOWED_PREFIXES = [
    "https://www.aidedd.org/regles/",
    "https://www.aidedd.org/dnd-5/regles/"  # Gestion d'éventuelles redirections/variantes
]

OUTPUT_DIR = "./AideDD_Obsidian_Vault/Regles"
REQUEST_DELAY = 0.5  # Pause en secondes entre chaque requête (respect du serveur)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7"
}

# ================= Fonctions Utilitaires =================

def sanitize_filename(name: str) -> str:
    """Nettoie une chaîne pour en faire un nom de fichier valide et propre."""
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = re.sub(r'\s+', " ", name).strip()
    return name or "page_sans_titre"


def url_to_filename(url: str, title: str = None) -> str:
    """Génère un nom de fichier cohérent à partir du titre ou de l'URL."""
    if title and title.strip():
        # Nettoyage des suffixes fréquents de site
        cleaned_title = re.sub(r'\s*[-–|]\s*AideDD.*$', '', title.strip(), flags=re.IGNORECASE)
        cleaned_title = re.sub(r'\s*[-–|]\s*D&D 5.*$', '', cleaned_title, flags=re.IGNORECASE)
        if cleaned_title.strip():
            return sanitize_filename(cleaned_title)

    parsed = urlparse(url)
    path = parsed.path.strip("/").split("/")[-1]
    if not path or path == "regles":
        return "Index_Regles"
    
    path = path.replace(".php", "").replace(".html", "")
    return sanitize_filename(path.capitalize())


def is_valid_regle_url(url: str) -> bool:
    """Vérifie si l'URL appartient bien à la section des règles à crawler."""
    clean_url, _ = urldefrag(url)
    return any(clean_url.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def extract_main_content(soup: BeautifulSoup):
    """
    Extrait la zone de contenu utile sur AideDD en éliminant les menus,
    bars latérales, pubs et pieds de page.
    """
    # Éléments indésirables à supprimer
    for selector in [
        "nav", "header", "footer", ".menu", ".menuglobal", ".pub", ".ads",
        "#sidebar", ".sidebar", ".share", ".social", ".ariane", ".fil-ariane",
        "script", "style", "noscript", ".noprint"
    ]:
        for el in soup.select(selector):
            el.decompose()

    # Recherche du conteneur de contenu principal
    main_el = (
        soup.find("div", class_=re.compile(r"content|bloc|article|main|col1|regles", re.I))
        or soup.find("article")
        or soup.find("main")
        or soup.find("div", id=re.compile(r"content|main|bloc", re.I))
        or soup.find("body")
    )
    return main_el


def convert_links_to_obsidian(soup: BeautifulSoup, current_url: str, url_map: dict):
    """
    Transforme les balises <a> HTML internes en format Wikilink [[Titre|Texte]] pour Obsidian,
    tout en préservant les liens externes normaux.
    """
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        link_text = a.get_text().strip() or "Lien"
        
        # Ignorer les ancres pures locales
        if href.startswith("#"):
            continue

        full_url = urljoin(current_url, href)
        clean_url, frag = urldefrag(full_url)

        if is_valid_regle_url(clean_url):
            target_filename = url_map.get(clean_url)
            if target_filename:
                anchor_part = f"#{frag}" if frag else ""
                # Si le texte du lien est différent du nom du fichier
                if link_text.lower() == target_filename.lower() and not anchor_part:
                    wikilink = f"[[{target_filename}]]"
                else:
                    wikilink = f"[[{target_filename}{anchor_part}|{link_text}]]"
                
                a.replace_with(soup.new_string(wikilink))


# ================= Cœur du Scraper =================

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)

    visited_urls = set()
    queue = [BASE_URL]
    
    # Dictionnaire de correspondance { URL: Nom_Fichier_Markdown }
    url_to_title_map = {}
    cached_soups = {}

    print("🔍 Étape 1 : Découverte et indexation de toutes les pages de règles...")

    while queue:
        current_url = queue.pop(0)
        clean_url, _ = urldefrag(current_url)

        if clean_url in visited_urls:
            continue

        visited_urls.add(clean_url)
        print(f"  -> Analyse : {clean_url}")

        try:
            resp = session.get(clean_url, timeout=12)
            if resp.status_code != 200:
                print(f"     [!] Erreur HTTP {resp.status_code} sur {clean_url}")
                continue

            # AideDD est souvent en UTF-8 ou ISO-8859-1
            resp.encoding = resp.apparent_encoding or "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            cached_soups[clean_url] = soup

            # Récupération du titre
            h1 = soup.find("h1")
            title_text = h1.get_text().strip() if h1 else ""
            if not title_text and soup.title:
                title_text = soup.title.get_text().strip()

            filename = url_to_filename(clean_url, title_text)
            
            # Éviter les collisions de noms de fichiers
            existing_names = list(url_to_title_map.values())
            final_name = filename
            counter = 1
            while final_name in existing_names:
                final_name = f"{filename}_{counter}"
                counter += 1

            url_to_title_map[clean_url] = final_name

            # Recherche des nouveaux liens internes
            for a in soup.find_all("a", href=True):
                target_url = urljoin(clean_url, a["href"])
                clean_target, _ = urldefrag(target_url)

                if is_valid_regle_url(clean_target) and clean_target not in visited_urls and clean_target not in queue:
                    queue.append(clean_target)

            time.sleep(REQUEST_DELAY)

        except Exception as e:
            print(f"     [!] Exception lors de l'accès à {clean_url}: {e}")

    print(f"\n✅ Indexation terminée : {len(url_to_title_map)} pages découvertes.")
    print(f"📝 Étape 2 : Conversion en Markdown et structuration Obsidian...")

    for page_url, file_base_name in url_to_title_map.items():
        soup = cached_soups.get(page_url)
        if not soup:
            continue

        # Extraction du titre
        h1 = soup.find("h1")
        page_title = h1.get_text().strip() if h1 else file_base_name

        # Conversion des liens internes vers le format Obsidian
        convert_links_to_obsidian(soup, page_url, url_to_title_map)

        # Extraction du contenu principal
        content_el = extract_main_content(soup)
        if not content_el:
            print(f"  [!] Contenu vide pour {file_base_name}")
            continue

        # Conversion HTML -> Markdown
        html_str = str(content_el)
        markdown_body = md(
            html_str,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "button"]
        ).strip()

        # Nettoyage des sauts de ligne excessifs
        markdown_body = re.sub(r'\n{3,}', '\n\n', markdown_body)

        # Ajout du Frontmatter YAML et métadonnées
        frontmatter = f"""---
title: "{page_title}"
source: "{page_url}"
tags:
  - dnd5
  - regles
---

# {page_title}

"""
        full_markdown = frontmatter + markdown_body

        file_path = os.path.join(OUTPUT_DIR, f"{file_base_name}.md")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(full_markdown)

        print(f"  [OK] Fichier généré : {file_path}")

    print(f"\n🎉 Terminé avec succès ! Vos fichiers sont disponibles dans : {OUTPUT_DIR}")
    print("👉 Vous pouvez maintenant ouvrir ce dossier directement dans Obsidian (Open folder as vault).")

if __name__ == "__main__":
    main()
