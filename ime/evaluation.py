"""Deterministic surface-text metrics; no recognition or networking imports."""
import re
import unicodedata

NORMALIZATION = {
    "unicode": "NFC (not NFKC)", "case": "preserved", "punctuation": "preserved",
    "script": "preserved; no simplified/traditional conversion",
    "cer_whitespace": "removed", "wer": "case-sensitive ASCII English words and signed numeric tokens",
    "empty_reference": "rate is null when reference units are zero; insertion count remains reported",
}
ENGLISH_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*|[+-]?[0-9]+(?:[.,][0-9]+)*")


def normalized(text):
    if not isinstance(text, str):
        raise ValueError("Transcripts must be strings")
    return unicodedata.normalize("NFC", text)


def edit_distance(reference, hypothesis):
    """Levenshtein distance with O(min(n, m)) memory."""
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for index, wanted in enumerate(reference, 1):
        current = [index]
        for position, actual in enumerate(hypothesis, 1):
            current.append(min(current[-1] + 1, previous[position] + 1,
                               previous[position - 1] + (wanted != actual)))
        previous = current
    return previous[-1]


def contains_term(text, term):
    text, term = normalized(text), normalized(term)
    if not term:
        raise ValueError("Required terms must be nonempty")
    if re.fullmatch(r"[+-]?[0-9]+(?:[.,][0-9]+)*", term):
        for match in ENGLISH_TOKEN.finditer(text):
            if match.group() != term:
                continue
            before = text[match.start() - 1] if match.start() else " "
            after = text[match.end()] if match.end() < len(text) else " "
            if any(char == "_" or char.isdigit() or (char.isascii() and char.isalpha()) for char in (before, after)):
                continue
            return True
        return False
    if term.isascii() and any(char.isalnum() for char in term):
        return re.search(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", text) is not None
    return term in text


def score_transcript(reference, hypothesis, terms=()):
    reference, hypothesis = normalized(reference), normalized(hypothesis)
    ref_chars = "".join(reference.split())
    hyp_chars = "".join(hypothesis.split())
    ref_words, hyp_words = ENGLISH_TOKEN.findall(reference), ENGLISH_TOKEN.findall(hypothesis)
    chars = edit_distance(ref_chars, hyp_chars)
    words = edit_distance(ref_words, hyp_words)
    if not isinstance(terms, (list, tuple)) or any(not isinstance(term, str) or not term for term in terms):
        raise ValueError("terms must be a list of nonempty strings")
    if any(not contains_term(reference, term) for term in terms):
        raise ValueError("Every required term must occur in its reference transcript")
    matched = sum(contains_term(hypothesis, term) for term in terms)
    return {"cer": chars / len(ref_chars) if ref_chars else None,
            "character_errors": chars, "reference_characters": len(ref_chars),
            "english_token_wer": words / len(ref_words) if ref_words else None,
            "english_token_errors": words, "reference_english_tokens": len(ref_words),
            "required_term_accuracy": matched / len(terms) if terms else None,
            "matched_terms": matched, "required_terms": len(terms)}


def aggregate_scores(rows):
    character_errors = sum(row["character_errors"] for row in rows)
    characters = sum(row["reference_characters"] for row in rows)
    word_errors = sum(row["english_token_errors"] for row in rows)
    words = sum(row["reference_english_tokens"] for row in rows)
    terms = sum(row["required_terms"] for row in rows)
    matched = sum(row["matched_terms"] for row in rows)
    return {"cases": len(rows), "cer": character_errors / characters if characters else None,
            "character_errors": character_errors, "reference_characters": characters,
            "english_token_wer": word_errors / words if words else None,
            "english_token_errors": word_errors, "reference_english_tokens": words,
            "required_term_accuracy": matched / terms if terms else None,
            "matched_terms": matched, "required_terms": terms}
