#!/usr/bin/env python3
"""Build the Word Wise glossaries in isopod/data/wordwise.

usage: build.py NLTK_DATA SOURCES OUT_DIR

NLTK_DATA holds Princeton WordNet 3.0 as corpora/wordnet.zip. SOURCES holds three
Wiktextract extracts from kaikki.org:
  en-ko-translations.jsonl  Korean translations in the English Wiktionary's English
                            entries, filtered from its English dump by extract.py
  ko-en.jsonl               the English Wiktionary's Korean entries
  kowiktionary-en.jsonl     the Korean Wiktionary's English entries
Word frequencies come from the wordfreq package. Each output line is
"word<TAB>hint<TAB>zipf x 10" for words rare enough to deserve a hint.
"""
import json
import os
import re
import sys

import nltk
from wordfreq import top_n_list, zipf_frequency

RAREST = 1.8   # below this a "word" is usually a name or a typo
COMMON = 4.3   # above this nobody needs a hint
HINT_CHARS = 22  # a hint wider than its word pushes the line apart
KO_CHARS = 10

WORD = re.compile(r"^[a-z][a-z'-]{3,}$")
HANGUL = re.compile(r"^[가-힣]+(?: [가-힣]+){0,2}$")
TOKEN = re.compile(r"[a-z]+")
RUDE = {"arse", "ass", "bastard", "bitch", "bloody", "boob", "cock", "crap", "damn", "dick", "fag", "fuck",
        "horny", "piss", "pissed", "prick", "screw", "sexy", "shit", "slut", "sucks", "tits", "twat", "whore"}
RUDE_KO = ("지랄", "씨발", "시발", "좆", "병신", "새끼", "염병", "존나", "썅", "등신", "빌어먹을", "젠장", "니미")
STOP = {"a", "an", "the", "of", "to", "or", "and", "in", "on", "for", "with", "by", "as", "at", "that",
        "which", "who", "is", "be", "something", "someone", "one", "used", "especially", "usually"}


def candidates():
    words = {}
    for w in top_n_list("en", 150000):
        if WORD.match(w):
            z = zipf_frequency(w, "en")
            if RAREST <= z < COMMON:
                words[w] = z
    return words


# English: a plainer word, else a short definition --------------------------------

LEADS = ("being ", "having ", "or ", "a ", "an ", "the ", "in a ", "to ", "marked by ", "characterized by ",
         "showing ", "full of ", "any of ", "one of ")


def short_definition(wn, text):
    text = re.sub(r"\(.*?\)", "", text).split(";")[0].strip()
    changed = True
    while changed:
        changed = False
        for lead in LEADS:
            if text.startswith(lead) and len(text) > len(lead) + 3:
                text, changed = text[len(lead):], True
    if text.startswith(("of ", "relating to", "used of", "a person who", "used ", "in ", "not ")):
        return None
    for cut in (", ", " or "):
        head = text.split(cut)[0]
        # "censure severely or angrily" loses its tail; "tart red or black berries" keeps it
        if len(text) > HINT_CHARS and len(head.split()) >= 2 and \
                (cut == ", " or not wn.synsets(head.split()[-1], wn.ADJ)):
            text = head
    return text if 1 <= len(text.split()) <= 4 and len(text) <= HINT_CHARS else None


def main_senses(wn, word):
    """WordNet's senses, those of the part of speech tagged most in SemCor first (peep is
    mostly a verb), in WordNet's own order of use within it."""
    senses = wn.synsets(word)
    own = [s for s in senses if word in (lemma.name().lower() for lemma in s.lemmas())]
    senses = own or senses  # perturbed is an adjective before it is perturb
    order = [_pos(s) for s in senses]
    totals = {}
    for s in senses:
        totals[_pos(s)] = totals.get(_pos(s), 0) + sum(
            lemma.count() for lemma in s.lemmas() if lemma.name().lower() == word)
    first = max(totals, key=lambda p: (totals[p], -order.index(p)), default=None)
    return sorted(senses, key=lambda s: _pos(s) != first)


def _pos(synset):
    return "a" if synset.pos() == "s" else synset.pos()


def plainer(wn, word, z, synset, margin, strict=True, rank=2):
    """The most common single word naming `synset` that is clearly more common than `word`.
    A synonym must mean `synset` in one of its own two main senses, and be mostly used as
    that part of speech, so "silver" never stands for eloquent nor "trend" for swerve."""
    best, best_z = None, max(z + margin, 3.6)
    for name in synset.lemma_names():
        cand = name.lower()
        if not cand.isalpha() or cand == word or cand[:4] == word[:4]:
            continue
        cz = zipf_frequency(cand, "en")
        if cz < best_z or cand in RUDE:
            continue
        if strict:
            own = main_senses(wn, cand)
            if _pos(synset) not in {_pos(s) for s in own[:2]} or \
                    synset not in [s for s in own if _pos(s) == _pos(synset)][:rank]:
                continue
        best, best_z = cand, cz
    return best


def inflected(wn, word):
    """A plural or a verb form WordNet also lists as a noun: tumbling is tumble, not
    gymnastics, and the reader finds it by its base. Crooked stays an adjective."""
    senses = main_senses(wn, word)
    return bool(senses) and _pos(senses[0]) == "n" and \
        any(wn.morphy(word, pos) not in (None, word) for pos in (wn.NOUN, wn.VERB))


def english_hint(wn, word, z):
    """Inflected forms get no entry of their own; the reader finds them by their base."""
    if not wn.lemmas(word):
        return None
    senses = main_senses(wn, word)
    main = senses[0]
    if main.instance_hypernyms() or inflected(wn, word):
        return None
    hint = plainer(wn, word, z, main, 0.5)
    for near in (main.similar_tos() if _pos(main) == "a" else []):
        hint = hint or plainer(wn, word, z, near, 0.5, rank=1)
    if not hint:
        hint = short_definition(wn, main.definition())
        if hint and (re.search(r"\d", hint) or hint.endswith(" manner") or word[:5] in hint):
            hint = None
    for parent in main.hypernyms()[:1]:
        # only a specific kind: "flower" for daisy, never "dish" for custard nor "change" for brighten
        if _pos(parent) == "n" and parent.min_depth() >= 8:
            hint = hint or plainer(wn, word, z, parent, 0.3, strict=False)
    for other in senses[1:2]:
        hint = hint or plainer(wn, word, z, other, 0.5)
    return hint


# Korean: every candidate translated back, the one that means WordNet's main sense wins --

SKIP_SENSE = {"form-of", "alt-of", "obsolete", "archaic", "dialectal"}
PARTICLE = re.compile(r"^[을를이가에의은는와과도로]\s+")


def _tokens(text):
    return {t for t in TOKEN.findall(text.lower()) if t not in STOP}


def _clean_ko(word):
    word = PARTICLE.sub("", re.sub(r"[\(\[\{（].*?[\)\]\}）]", "", word).strip().rstrip("."))
    ok = HANGUL.match(word) and len(word) <= KO_CHARS and not any(r in word for r in RUDE_KO)
    return word if ok else None


def _jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def korean_glosses(path):
    """The English Wiktionary's Korean entries: each Korean word's English gloss words, and
    the Korean words whose whole gloss is a single English word."""
    glosses, exact = {}, {}
    for e in _jsonl(path):
        ko = _clean_ko(e.get("word") or "")
        if not ko:
            continue
        for rank, sense in enumerate((e.get("senses") or [])[:3]):
            if set(sense.get("tags") or []) & SKIP_SENSE:
                continue
            gloss = (sense.get("glosses") or [""])[0]
            glosses.setdefault(ko, set()).update(_tokens(gloss))
            whole = re.sub(r"^(to|a|an|the) ", "", re.sub(r"\(.*?\)", "", gloss).strip().lower())
            if rank < 2:
                exact.setdefault(whole, []).append(ko)
    return glosses, exact


def korean_hints(sources, words, wn):
    """Candidates come from three places, most trusted first: the English Wiktionary's
    translation tables, the Korean Wiktionary's English entries, and Korean entries whose
    gloss is the word. Each is translated back through what it means elsewhere; one whose
    meanings miss WordNet's main sense altogether is dropped (젖가슴 for mamma, 지랄 for
    commotion), and among the rest trust decides, then how much of that sense they share,
    then a word beats a description (어리석음 for folly, not 모조 건축물)."""
    glosses, exact = korean_glosses(f"{sources}/ko-en.jsonl")
    found = {}

    def add(word, ko, trust, sense=None):
        ko = _clean_ko(ko)
        if ko:
            found.setdefault(word, []).append((ko, trust, sense))

    for e in _jsonl(f"{sources}/en-ko-translations.jsonl"):
        word = e.get("word") or ""
        for t in e["tr"]:
            ko = _clean_ko(t["word"])
            if ko:
                glosses.setdefault(ko, set()).update({word.lower()} | _tokens(t.get("sense") or ""))
            if word in words:  # names are capitalised, and stay out
                add(word, t["word"], 3, (e.get("pos") or "", t.get("sense") or ""))
    for e in _jsonl(f"{sources}/kowiktionary-en.jsonl"):
        word = e.get("word") or ""
        if word in words:
            for sense in (e.get("senses") or [])[:4]:
                gloss = re.sub(r"[\(\[\{（].*?[\)\]\}）]", "", (sense.get("glosses") or [""])[0])
                for alt in re.split(r"[,;.。/·:]", gloss)[:3]:
                    add(word, alt, 2)
    for word, kos in exact.items():
        if word in words:
            for ko in kos:
                add(word, ko, 1)

    pos_name = {"n": "noun", "v": "verb", "a": "adj", "r": "adv"}
    hints = {}
    for word, rows in found.items():
        if inflected(wn, word):
            continue
        want, pos = set(), None
        if wn.lemmas(word):
            main = main_senses(wn, word)[0]
            pos = pos_name.get(_pos(main))
            want = _tokens(main.definition()) | {n.lower() for n in main.lemma_names()} | \
                {n.lower() for h in main.hypernyms() for n in h.lemma_names()}
        want.discard(word)

        def score(i):
            ko, trust, sense = rows[i]
            elsewhere = glosses.get(ko, set()) - {word}
            shared = len(elsewhere & want)
            if sense:
                shared += len(_tokens(sense[1]) & want) + (sense[0] == pos)
            if want and elsewhere and not shared:
                return None
            return (trust, shared, -ko.count(" "), -i)
        scored = [(score(i), rows[i][0]) for i in range(len(rows))]
        scored = [(sc, ko) for sc, ko in scored if sc]
        if scored:
            hints[word] = max(scored)[1]
    return hints


def write(path, words, hints):
    with open(path, "w", encoding="utf-8") as f:
        for word in sorted(hints):
            f.write(f"{word}\t{hints[word]}\t{round(words[word] * 10)}\n")


def main(nltk_data, sources, out_dir):
    words = candidates()
    nltk.data.path.insert(0, os.path.abspath(nltk_data))
    from nltk.corpus import wordnet as wn
    en = {}
    for word, z in words.items():
        hint = english_hint(wn, word, z)
        if hint:
            en[word] = hint
    write(f"{out_dir}/en-en.tsv", words, en)
    with open(f"{out_dir}/common-en.txt", "w", encoding="utf-8") as f:
        common = [w for w in top_n_list("en", 30000) if WORD.match(w)]
        f.write("\n".join(sorted(common)) + "\n")
    ko = korean_hints(sources, words, wn)
    write(f"{out_dir}/en-ko.tsv", words, ko)
    print(f"{len(words)} candidates, {len(en)} English hints, {len(ko)} Korean hints")


if __name__ == "__main__":
    main(*sys.argv[1:4])
