import re
from typing import Optional

WORD = re.compile(r"\w+", re.UNICODE)
NOISE_WORDS = frozenset(
    "в во и на не что с со а по из к у за от до для о об при это как том так же бы или его ее "
    "их но да the a an of to in on for and or is are was were at by with from this that".split()
)
STEM_LEN = 6
DUP_THRESHOLD = 0.33
# 5 = one unambiguous marker, or one strong plus one weak. Two weak signals (e.g. a news
# story that merely mentions advertising and a bookmaker) must not be enough.
AD_THRESHOLD = 5
URL = re.compile(r"https?://\S+|t\.me/\S+", re.I)
MENTION = re.compile(r"@[A-Za-z0-9_]{4,}")
EMOJI_TAIL = re.compile(r"[\s•|·—–-]*$")

# Weights are tuned so that only unambiguous markers (5) block a post on their own at
# AD_THRESHOLD. News legitimately discusses advertising, discounts and casinos, so those
# score low and must combine with something else before the post is dropped.
AD_PATTERNS = [
    (re.compile(r"\berid\b\s*[:=]?\s*\w", re.I), 5, "маркировка erid"),
    (re.compile(r"на правах рекламы", re.I), 5, "на правах рекламы"),
    (re.compile(r"партн[её]рск\w+ (материал|пост|публикац)", re.I), 5, "партнёрский материал"),
    (re.compile(r"рекламодател", re.I), 5, "указан рекламодатель"),
    (re.compile(r"промокод", re.I), 4, "промокод"),
    (re.compile(r"\bинн\b\s*\d{10}", re.I), 4, "ИНН рекламодателя"),
    (re.compile(r"utm_(source|campaign|medium)", re.I), 3, "utm-метки"),
    (re.compile(r"реклам[аоуые]\w*", re.I), 2, "слово «реклама»"),
    (re.compile(r"скидк[аиуе]\s*\d+\s*%|-\d+\s*%\s*(?:по|на)\b", re.I), 2, "скидка в процентах"),
    (re.compile(r"успей|торопись|только сегодня|осталось мест", re.I), 2, "скам-срочность"),
    (re.compile(r"подпис\w+ на (наш|канал)", re.I), 2, "призыв подписаться"),
    (re.compile(r"\b(казино|букмекер|крипт[оа]инвест|ставк[аи] на спорт)", re.I), 2, "азартная тематика"),
    (re.compile(r"перейд[иа]\w*\s+по\s+ссылк|жми\s+сюда|регистрируйся", re.I), 3, "призыв перейти"),
]

SELF_PROMO_TAIL = re.compile(
    r"(?:\n|^)[^\n]{0,80}?(?:подпис\w+|прислать новость|наш канал|читать далее|источник)"
    r"[^\n]{0,80}$",
    re.I,
)


def normalize(text: str) -> str:
    text = URL.sub(" ", text)
    text = MENTION.sub(" ", text)
    return " ".join(WORD.findall(text.lower()))


def tokens(text: str) -> set[str]:
    """Content words trimmed to a crude stem, so different word forms still match."""
    return {
        word[:STEM_LEN]
        for word in normalize(text).split()
        if len(word) > 2 and word not in NOISE_WORDS
    }


def fingerprint(text: str) -> str:
    return " ".join(sorted(tokens(text)))


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def find_duplicate(
    current: str, known: list[tuple[int, str]], threshold: float = DUP_THRESHOLD
) -> Optional[int]:
    mine = set(current.split())
    if len(mine) < 4:
        return None
    for post_id, other in known:
        if other and jaccard(mine, set(other.split())) >= threshold:
            return post_id
    return None


def stopword_hit(text: str, stopwords: str) -> Optional[str]:
    haystack = text.lower()
    for word in (w.strip().lower() for w in stopwords.split(",")):
        if word and word in haystack:
            return word
    return None


def ad_score(text: str) -> tuple[int, list[str]]:
    score, reasons = 0, []
    for pattern, weight, label in AD_PATTERNS:
        if pattern.search(text):
            score += weight
            reasons.append(label)
    return score, reasons


def strip_source_artifacts(text: str) -> str:
    text = MENTION.sub("", text)
    text = URL.sub("", text)
    for _ in range(3):
        stripped = SELF_PROMO_TAIL.sub("", text).strip()
        if stripped == text.strip():
            break
        text = stripped
    lines = [EMOJI_TAIL.sub("", line).rstrip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
