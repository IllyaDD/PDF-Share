import re

HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")

MAX_RECTS = 200
MAX_NOTE_LENGTH = 2000
MAX_SELECTED_TEXT_LENGTH = 5000
RECT_KEYS = ("x0", "y0", "x1", "y1")
LINE_TOLERANCE = 1.0
JOIN_TOLERANCE = 0.25


class PayloadError(ValueError):
    pass


def _number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(f"{field}: очікується число")
    if value != value or value in (float("inf"), float("-inf")):
        raise PayloadError(f"{field}: некоректне число")
    return float(value)



def _line_groups(rects):
    groups = []
    for rect in sorted(rects, key=lambda r: (-r["y0"], r["x0"])):
        for anchor, members in groups:
            if (abs(anchor[0] - rect["y0"]) <= LINE_TOLERANCE
                    and abs(anchor[1] - rect["y1"]) <= LINE_TOLERANCE):
                members.append(rect)
                break
        else:
            groups.append(((rect["y0"], rect["y1"]), [rect]))
    return groups


def merge_rects(rects):
    merged = []
    for _, members in _line_groups(rects):
        current = None
        for rect in sorted(members, key=lambda r: r["x0"]):
            if current is not None and rect["x0"] - current["x1"] <= JOIN_TOLERANCE:
                current["x0"] = min(current["x0"], rect["x0"])
                current["x1"] = max(current["x1"], rect["x1"])
                current["y0"] = min(current["y0"], rect["y0"])
                current["y1"] = max(current["y1"], rect["y1"])
                continue
            current = dict(rect)
            merged.append(current)
    return sorted(merged, key=lambda r: (-r["y0"], r["x0"]))


def parse_coords(raw):
    if not isinstance(raw, list) or not raw:
        raise PayloadError("coords: очікується непорожній список прямокутників")
    if len(raw) > MAX_RECTS:
        raise PayloadError(f"coords: забагато прямокутників (максимум {MAX_RECTS})")

    rects = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PayloadError(f"coords[{index}]: очікується об'єкт")
        if set(item) != set(RECT_KEYS):
            raise PayloadError(f"coords[{index}]: потрібні саме ключі {', '.join(RECT_KEYS)}")

        rect = {key: _number(item[key], f"coords[{index}].{key}") for key in RECT_KEYS}
        if rect["x1"] <= rect["x0"] or rect["y1"] <= rect["y0"]:
            raise PayloadError(f"coords[{index}]: порожній прямокутник")
        rects.append(rect)

    return merge_rects(rects)


def parse_color(raw):
    if not isinstance(raw, str) or not HEX_COLOR.match(raw):
        raise PayloadError("color: очікується колір у форматі #RRGGBB")
    return raw.upper()


def parse_page_number(raw, page_count):
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PayloadError("page_number: очікується ціле число")
    if raw < 1 or (page_count and raw > page_count):
        raise PayloadError(f"page_number: поза межами документа (1..{page_count})")
    return raw


def parse_text(raw, field, limit):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise PayloadError(f"{field}: очікується рядок")
    if len(raw) > limit:
        raise PayloadError(f"{field}: задовгий текст (максимум {limit})")
    return raw


def parse_new_annotation(payload, page_count):
    if not isinstance(payload, dict):
        raise PayloadError("очікується JSON-об'єкт")

    return {
        "page_number": parse_page_number(payload.get("page_number"), page_count),
        "coords": parse_coords(payload.get("coords")),
        "color": parse_color(payload.get("color")),
        "selected_text": parse_text(
            payload.get("selected_text"), "selected_text", MAX_SELECTED_TEXT_LENGTH
        ),
        "note_text": parse_text(payload.get("note_text"), "note_text", MAX_NOTE_LENGTH),
    }


def parse_annotation_update(payload):
    if not isinstance(payload, dict):
        raise PayloadError("очікується JSON-об'єкт")

    allowed = {"color", "note_text"}
    unknown = set(payload) - allowed
    if unknown:
        raise PayloadError(f"поля не можна змінювати: {', '.join(sorted(unknown))}")
    if not payload:
        raise PayloadError("нема що оновлювати")

    fields = {}
    if "color" in payload:
        fields["color"] = parse_color(payload["color"])
    if "note_text" in payload:
        fields["note_text"] = parse_text(payload["note_text"], "note_text", MAX_NOTE_LENGTH)
    return fields
