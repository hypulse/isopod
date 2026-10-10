#!/usr/bin/env python3
"""Keep the Korean translations from the English Wiktionary's English entries.

usage: curl -sL https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl \\
           | extract.py en-ko-translations.jsonl
"""
import json
import sys


def main(out_path):
    kept = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for line in sys.stdin:
            if '"ko"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("lang_code") != "en":
                continue
            rows = list(entry.get("translations") or [])
            for sense in entry.get("senses") or []:
                rows += sense.get("translations") or []
            ko = [{"sense": t.get("sense", ""), "word": t.get("word", "")} for t in rows if t.get("code") == "ko"]
            if ko:
                out.write(json.dumps({"word": entry.get("word"), "pos": entry.get("pos"), "tr": ko},
                                     ensure_ascii=False) + "\n")
                kept += 1
    print(f"{kept} entries with Korean translations", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
