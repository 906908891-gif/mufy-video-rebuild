#!/usr/bin/env python3
"""Audit a static MUFY fragment against its evidence inventory; not a visual test."""
import argparse
from collections import Counter
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


class Fragment(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = Counter()
        self.refs = set()
        self.dom_ids = Counter()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("data-mufy-element"):
            self.elements[attrs["data-mufy-element"]] += 1
        if attrs.get("id"):
            self.dom_ids[attrs["id"]] += 1
        if tag in ("img", "audio", "video", "source", "track", "iframe", "script") and attrs.get("src"):
            self.refs.add(attrs["src"])
        if tag == "video" and attrs.get("poster"):
            self.refs.add(attrs["poster"])
        if tag == "link" and attrs.get("href"):
            self.refs.add(attrs["href"])

    handle_startendtag = handle_starttag


def load(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return result


def audit(args):
    inventory_path, assets_path = Path(args.inventory), Path(args.assets)
    inventory, manifest = load(inventory_path), load(assets_path)
    source = Path(args.html).read_text(encoding="utf-8")
    document = Fragment()
    document.feed(source)
    document.refs.update(unescape(url.strip()) for url in re.findall(r"url\(\s*['\"]?([^)'\"]+)['\"]?\s*\)", source))
    errors, pending = [], []
    pages = inventory.get("pages", [])
    elements, assets = inventory.get("elements", []), manifest.get("assets", [])
    if not pages or not elements:
        errors.append("Inventory requires non-empty pages and elements")
    if not isinstance(assets, list):
        raise ValueError("assets must be an array")
    page_ids = [page.get("id") for page in pages]
    element_ids = [element.get("id") for element in elements]
    asset_ids = [asset.get("id") for asset in assets]
    for name, ids in (("page", page_ids), ("element", element_ids), ("asset", asset_ids)):
        if any(not isinstance(value, str) or not value for value in ids):
            errors.append(f"Missing/invalid {name} id")
        for value, count in Counter(ids).items():
            if count > 1:
                errors.append(f"Duplicate {name} id: {value}")
    for page in pages:
        if not page.get("evidence"):
            errors.append(f"Page lacks evidence: {page.get('id')}")
        if not any(element.get("page") == page.get("id") for element in elements):
            errors.append(f"Page has no inventoried elements: {page.get('id')}")
    states = {"passed", "pending", "failed", "not-applicable"}
    for element in elements:
        key = element.get("id")
        expected = element.get("expected_count")
        if type(expected) is not int or expected < 1:
            errors.append(f"Invalid positive expected_count: {key}")
        elif document.elements[key] != expected:
            errors.append(f"Count mismatch {key}: expected {expected}, found {document.elements[key]}")
        if element.get("page") not in page_ids or not element.get("evidence"):
            errors.append(f"Missing page/evidence: {key}")
        for state_key in ("visual", "interaction"):
            state = element.get(state_key)
            if state not in states:
                errors.append(f"Invalid {state_key} state: {key}")
            elif state == "failed":
                errors.append(f"Failed {state_key}: {key}")
            elif state == "pending":
                pending.append(f"Pending {state_key}: {key}")
        if element.get("difference"):
            pending.append(f"Difference {key}: {element['difference']}")
        for asset_id in element.get("asset_ids", []):
            matching = [asset for asset in assets if asset.get("id") == asset_id]
            if not matching or key not in matching[0].get("element_ids", []):
                errors.append(f"Missing reciprocal asset mapping: {key} -> {asset_id}")
    for key in document.elements.keys() - set(element_ids):
        errors.append(f"DOM annotation absent from inventory: {key}")
    known_urls = set()
    for asset in assets:
        key = asset.get("id")
        kind, origin = asset.get("kind"), asset.get("source_type")
        if kind not in {"image", "video", "audio", "font", "script", "stylesheet", "embed"}:
            errors.append(f"Unknown asset kind: {key}")
        if origin not in {"generated", "user-owned", "licensed"} or not asset.get("provenance"):
            errors.append(f"Missing asset provenance: {key}")
        if kind == "image" and origin != "generated":
            errors.append(f"Reference replacement image is not generated: {key}")
        url = asset.get("url", "")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            errors.append(f"Asset URL must be public HTTPS: {key}")
        if parsed.query and re.search(r"(?:^|&)(?:sig|signature|se|expires|x-amz-signature|x-goog-signature)=", parsed.query, re.I):
            errors.append(f"Possibly temporary signed URL: {key}")
        known_urls.add(url)
        if url not in document.refs:
            errors.append(f"Asset URL not referenced in fragment: {key}")
        if asset.get("file") and not (assets_path.parent / asset["file"]).is_file():
            errors.append(f"Local asset file missing: {key}")
        if not asset.get("element_ids"):
            errors.append(f"Asset lacks element mappings: {key}")
        for element_id in asset.get("element_ids", []):
            matching = [element for element in elements if element.get("id") == element_id]
            if not matching or key not in matching[0].get("asset_ids", []):
                errors.append(f"Missing reciprocal element mapping: {key} -> {element_id}")
        if asset.get("verification") != "passed":
            pending.append(f"Asset not verified: {key}")
    for ref in document.refs:
        if not ref.startswith(("https://", "data:")):
            errors.append("Non-public/relative resource path present")
        elif ref.startswith("https://") and ref not in known_urls:
            errors.append("External resource absent from asset manifest")
    if any(count > 1 for count in document.dom_ids.values()):
        errors.append("Duplicate DOM IDs within fragment")
    if re.search(r"(?:example\.(?:com|org|net)|YOUR[_ -]?(?:URL|IMAGE|HOST)|PLACEHOLDER|待替换|此处省略|其余同上)", source, re.I):
        errors.append("Possible placeholder or abbreviated content remains")
    result = {"static_ok": not errors, "ready_for_review": not errors and not pending,
              "errors": errors, "pending": pending,
              "limits": "Counts and self-reported records only; independently verify all pages, visual fidelity, playback and actual MUFY behavior."}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 2 if args.strict and pending else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--html", required=True)
    parser.add_argument("--assets", required=True)
    parser.add_argument("--strict", action="store_true", help="Also fail on pending verification or differences")
    try:
        raise SystemExit(audit(parser.parse_args()))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")
