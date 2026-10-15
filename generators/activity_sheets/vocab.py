"""Theme words for word searches and tracing pages.

Words come from the brief's theme and keywords first, then from a small built-in
vocabulary of kid-friendly topics. Every word is a single word of letters only,
3-10 characters, and passes the trademark blocklist (flagged words are dropped too:
a puzzle word cannot wait for human review).
"""
from __future__ import annotations

import re
from typing import NamedTuple

from core.compliance import Blocklist, normalize
from generators.activity_sheets.shapes import singular

MIN_LEN, MAX_LEN = 3, 10

# Etsy keyword filler that says nothing about the theme.
STOPWORDS = frozenset("""
a an and the for with of to in on at by or from your my our you
printable printables print pages page sheet sheets worksheet worksheets activity activities
coloring colouring color book books pdf digital download downloads instant file files
pack bundle set kit game games puzzle puzzles maze mazes word words search searches find
tracing trace dot dots connect number numbers letter letters alphabet
kid kids child children toddler toddlers baby preschool prek kindergarten grade boys girls
fun cute easy simple best gift gifts favor favors homeschool classroom teacher teachers
learning educational busy quiet time travel themed theme ages age year years old
under over into onto outer inner little big small tiny mini super cozy first new more
one two three four five six seven eight nine ten
pre boy girl olds yrs svg png jpg jpeg eps clipart clip cut cricut canva etsy silhouette
commercial use license licence editable bundles mega sticker stickers planner journal
montessori homeschooling preschoolers kindergartners marker markers crossword sudoku bingo
""".split())


class Topic(NamedTuple):
    triggers: tuple[str, ...]
    words: tuple[str, ...]


VOCABULARY: dict[str, Topic] = {
    "animals": Topic(("animal", "zoo", "safari", "jungle", "wild", "wildlife", "pet", "creature"),
                     ("lion", "tiger", "zebra", "giraffe", "monkey", "elephant", "panda", "koala", "bear",
                      "hippo", "rhino", "camel", "kangaroo", "parrot", "penguin", "otter", "fox", "wolf",
                      "deer", "rabbit", "turtle", "owl")),
    "ocean": Topic(("ocean", "sea", "beach", "underwater", "marine", "fish", "coral", "reef", "shark",
                    "whale", "mermaid", "nautical", "seaside"),
                   ("whale", "shark", "dolphin", "octopus", "crab", "starfish", "seahorse", "turtle",
                    "jellyfish", "coral", "shell", "wave", "squid", "lobster", "seal", "clam", "anchor",
                    "boat", "sand", "fish")),
    "farm": Topic(("farm", "barn", "farmer", "tractor", "ranch", "countryside", "cow", "pig", "chicken"),
                  ("cow", "pig", "horse", "sheep", "goat", "chicken", "duck", "rooster", "tractor", "barn",
                   "hay", "farmer", "chick", "lamb", "donkey", "turkey", "goose", "pony", "fence", "corn")),
    "space": Topic(("space", "rocket", "planet", "astronaut", "galaxy", "moon", "star", "solar", "alien",
                    "universe", "outer", "cosmic"),
                   ("rocket", "planet", "star", "moon", "sun", "comet", "alien", "orbit", "galaxy",
                    "saturn", "mars", "earth", "jupiter", "meteor", "astronaut", "telescope", "space")),
    "dinosaurs": Topic(("dinosaur", "dino", "prehistoric", "fossil", "raptor", "reptile"),
                       ("dinosaur", "fossil", "raptor", "volcano", "bones", "egg", "claw", "swamp", "fern",
                        "roar", "horns", "tail", "nest", "teeth", "scales", "jungle")),
    "spring": Topic(("spring", "blossom", "april"),
                    ("flower", "rain", "puddle", "umbrella", "bird", "nest", "tulip", "sprout", "seed", "bee",
                     "rainbow", "garden", "butterfly", "lamb", "kite", "cloud")),
    "summer": Topic(("summer", "sunny", "vacation", "pool", "camping", "camp"),
                    ("sun", "beach", "pool", "swim", "sand", "shell", "boat", "kite", "picnic", "hat",
                     "lemonade", "tent", "camp", "wave", "bucket", "shovel")),
    "autumn": Topic(("autumn", "fall", "harvest", "leaves", "leaf"),
                    ("leaf", "acorn", "pumpkin", "apple", "scarf", "rake", "squirrel", "harvest", "corn",
                     "owl", "hay", "boots", "sweater", "maple", "wind")),
    "winter": Topic(("winter", "snow", "snowy", "snowflake", "cold", "ice", "snowman"),
                    ("snow", "snowman", "sled", "mitten", "scarf", "ice", "igloo", "penguin", "boots", "cocoa",
                     "skate", "coat", "hat", "snowball", "blizzard")),
    "weather": Topic(("season", "weather", "rain", "rainy", "storm"),
                     ("sun", "rain", "snow", "wind", "cloud", "storm", "rainbow", "thunder", "fog", "umbrella",
                      "leaf", "flower", "puddle", "frost")),
    "halloween": Topic(("halloween", "spooky", "pumpkin", "ghost", "witch", "monster"),
                       ("pumpkin", "ghost", "witch", "bat", "candy", "spider", "moon", "owl", "costume",
                        "lantern", "broom", "cat", "treat", "mask")),
    "christmas": Topic(("christmas", "xmas", "santa", "reindeer", "elf", "snowman"),
                       ("santa", "reindeer", "elf", "gift", "tree", "star", "bell", "candle", "cookie",
                        "sleigh", "snowman", "wreath", "stocking", "candy")),
    "easter": Topic(("easter", "bunny"),
                    ("bunny", "egg", "basket", "chick", "spring", "flower", "carrot", "lamb", "tulip", "hunt")),
    "valentines": Topic(("valentine", "valentines", "love", "heart"),
                        ("heart", "love", "hug", "card", "rose", "friend", "kind", "share", "candy", "cupid")),
    "holidays": Topic(("holiday", "party", "birthday", "celebration", "celebrate", "festive"),
                      ("gift", "party", "cake", "candle", "card", "balloon", "bell", "star", "family",
                       "music", "present", "cookie", "confetti")),
    "bugs": Topic(("bug", "insect", "garden", "butterfly", "bee", "ladybug", "beetle"),
                  ("ant", "bee", "beetle", "butterfly", "ladybug", "spider", "worm", "snail", "moth",
                   "cricket", "firefly", "dragonfly", "wasp", "leaf", "flower", "garden")),
    "food": Topic(("food", "fruit", "vegetable", "snack", "kitchen", "cooking", "baking", "picnic",
                   "breakfast", "dessert", "veggie"),
                  ("apple", "banana", "grape", "cherry", "lemon", "carrot", "pizza", "bread", "cheese",
                   "cookie", "muffin", "pear", "peach", "orange", "salad", "soup", "honey", "milk", "egg",
                   "corn")),
    "vehicles": Topic(("vehicle", "car", "truck", "train", "transport", "transportation", "construction",
                       "plane", "airplane", "digger", "bus"),
                      ("car", "bus", "truck", "train", "plane", "boat", "bike", "tractor", "rocket", "taxi",
                       "van", "ship", "scooter", "wagon", "digger", "crane", "helicopter", "ambulance")),
    "fantasy": Topic(("unicorn", "fairy", "dragon", "castle", "princess", "knight", "magic", "fairytale"),
                     ("unicorn", "dragon", "castle", "fairy", "wizard", "magic", "crown", "knight", "wand",
                      "rainbow", "tower", "potion")),
    "school": Topic(("school", "classroom", "teacher", "pencil", "crayon"),
                    ("pencil", "book", "ruler", "paper", "desk", "chalk", "glue", "lunch", "teacher", "friend",
                     "bus", "read", "write")),
}

GENERIC: tuple[str, ...] = ("sun", "star", "tree", "flower", "happy", "smile", "friend", "play", "house",
                            "ball", "kite", "book", "cloud", "heart", "rainbow", "family", "music", "dance",
                            "garden", "bird")


def tokens(*texts: str) -> list[str]:
    """Lowercase letter-only tokens, in order of appearance."""
    out: list[str] = []
    for t in texts:
        out += re.findall(r"[a-z]+", normalize(t or ""))
    return out


# Umbrella topics lose ties to specific ones: "farm animals" means farm, not zoo animals.
BROAD_TOPICS = frozenset({"animals", "holidays", "weather"})


def _score(name: str, theme: str, keywords: list[str]) -> float:
    trig = {singular(t) for t in VOCABULARY[name].triggers}
    theme_tok = {singular(t) for t in tokens(theme)}
    kw_tok = {singular(t) for t in tokens(*keywords)}
    score = 2 * len(trig & theme_tok) + len(trig & kw_tok)
    return score - 0.5 if score and name in BROAD_TOPICS else score


def topics_for(theme: str, keywords: list[str]) -> list[str]:
    """Vocabulary topics matching the theme (weighted x2) and keywords, best first."""
    scored = [(-_score(name, theme, keywords), i, name) for i, name in enumerate(VOCABULARY)]
    return [name for score, _, name in sorted(scored) if score < 0]


def is_kid_word(word: str, blocklist: Blocklist) -> bool:
    if not (word.isascii() and word.isalpha() and MIN_LEN <= len(word) <= MAX_LEN):
        return False
    result = blocklist.check(word)
    return result.ok and not result.needs_review


def theme_words(theme: str, keywords: list[str], blocklist: Blocklist, min_pool: int = 12) -> list[str]:
    """Ordered, de-duplicated word pool: brief words, then the best-matching topics.

    Weaker topics and generic words are added only while the pool is smaller than
    `min_pool`, so a "farm animals" pack repeats farm words rather than drifting to lions.
    """
    pool: list[str] = []
    seen: set[str] = set()

    def add(word: str) -> None:
        w = word.lower()
        key = singular(w)
        if key not in seen and is_kid_word(w, blocklist):
            seen.add(key)
            pool.append(w)

    for w in tokens(theme) + tokens(*keywords):
        if w not in STOPWORDS:          # Etsy keyword filler is not a puzzle word
            add(w)
    ranked = topics_for(theme, keywords)
    best = [n for n in ranked if _score(n, theme, keywords) == _score(ranked[0], theme, keywords)] if ranked else []
    for name in best:
        for w in VOCABULARY[name].words:
            add(w)
    for words in [VOCABULARY[n].words for n in ranked if n not in best] + [GENERIC]:
        if len(pool) >= min_pool:
            break
        for w in words:
            add(w)
    return pool
