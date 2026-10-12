#!/usr/bin/env python3
"""DocTriage Ledger: local, preliminary PDF inventory. NOT an accessibility audit."""
from __future__ import annotations
import argparse
import csv
import hashlib
import sys
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:
    sys.exit('Missing dependency: python -m pip install "pypdf>=5,<7"')

FIELDS = [
    'file', 'sha256', 'bytes', 'pages', 'encrypted', 'tag_tree_present',
    'language_present', 'title_present', 'text_pages', 'no_text_pages',
    'image_xobjects_observed', 'annotation_count', 'review_priority',
    'review_reasons', 'processing_error',
]

def safe_cell(value):
    """Neutralize CSV formula injection when opened in spreadsheet software."""
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value

def object_value(value):
    try:
        return value.get_object() if hasattr(value, 'get_object') else value
    except Exception:
        return None

def image_count(page):
    """Direct page XObject images only; not images nested inside Form XObjects."""
    try:
        resources = object_value(page.get('/Resources')) or {}
        xobjects = object_value(resources.get('/XObject')) or {}
        return sum(1 for x in xobjects.values()
                   if (object_value(x) or {}).get('/Subtype') == '/Image')
    except Exception:
        return 0

def hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def inspect(path, root):
    relative = safe_cell(path.relative_to(root).as_posix())
    row = dict.fromkeys(FIELDS, '')
    row.update(file=relative, bytes=path.stat().st_size)
    try:
        row['sha256'] = hash_file(path)
        reader = PdfReader(str(path), strict=False)
        row['encrypted'] = 'yes' if reader.is_encrypted else 'no'
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt('')
            except Exception:
                unlocked = 0
            if not unlocked:
                row.update(review_priority='needs_access', review_reasons='encrypted_no_access')
                return row
        pages = len(reader.pages)
        row['pages'] = pages
        root_dict = object_value(reader.trailer.get('/Root')) or {}
        marks = object_value(root_dict.get('/MarkInfo')) or {}
        row['tag_tree_present'] = 'yes' if bool(marks.get('/Marked')) and root_dict.get('/StructTreeRoot') else 'no'
        row['language_present'] = 'yes' if root_dict.get('/Lang') else 'no'
        metadata = reader.metadata
        row['title_present'] = 'yes' if metadata and str(metadata.title or '').strip() else 'no'
        text_pages = 0
        image_objects = 0
        annotations = 0
        for page in reader.pages:
            try:
                text = page.extract_text() or ''
                if text.strip():
                    text_pages += 1
            except Exception:
                pass
            image_objects += image_count(page)
            try:
                annotations += len(object_value(page.get('/Annots')) or [])
            except Exception:
                pass
        row.update(text_pages=text_pages, no_text_pages=pages - text_pages,
                   image_xobjects_observed=image_objects, annotation_count=annotations)
        reasons = []
        if row['tag_tree_present'] == 'no':
            reasons.append('no_tag_tree_detected')
        if pages and text_pages == 0:
            reasons.append('no_extractable_text_detected')
        elif pages and text_pages < pages:
            reasons.append('some_pages_lack_extractable_text')
        if row['language_present'] == 'no':
            reasons.append('language_not_declared')
        if row['title_present'] == 'no':
            reasons.append('document_title_not_declared')
        row['review_reasons'] = ';'.join(reasons) or 'human_review_still_required'
        row['review_priority'] = (
            'high' if ('no_tag_tree_detected' in reasons or
                       'no_extractable_text_detected' in reasons) else
            'medium' if reasons else 'baseline'
        )
    except Exception as exc:
        row.update(review_priority='needs_access',
                   review_reasons='parse_error',
                   processing_error=safe_cell(type(exc).__name__ + ': ' + str(exc)[:160]))
    return row

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path, help='Folder of PDF files; scanned recursively')
    parser.add_argument('--output', type=Path, default=Path('pdf_inventory.csv'))
    args = parser.parse_args(argv)
    root = args.folder.expanduser().resolve()
    if not root.is_dir():
        parser.error('folder must exist and be a directory')
    files = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() == '.pdf')
    if not files:
        parser.error('no PDF files found')
    if args.output.resolve() in files:
        parser.error('output cannot overwrite an input PDF')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for file in files:
            writer.writerow(inspect(file, root))
    print(f'Inventoried {len(files)} PDFs -> {args.output}')
    print('Preliminary indicators only; never a compliance determination.')

if __name__ == '__main__':
    main()
