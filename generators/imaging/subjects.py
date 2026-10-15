"""Built-in subject table: one theme -> a list of distinct, generic, original subjects.

Every pack needs a different subject per page (or per sticker), derived only from our own
brief's theme and keywords. Order of the pool:
    1. curated subjects of every table entry the theme matches (round-robin across entries)
    2. generic scene templates filled with the theme ("a {t} picnic", ...)
    3. pose variants of the curated subjects ("a friendly cow, sleeping peacefully")
Nothing repeats. All entries are generic nouns: no characters, brands or franchises, and
tests check the whole table against config/trademark_blocklist.txt (which hard-blocks some
everyday words such as "cars", "frozen" and "stitch", so the table avoids them).
"""
from __future__ import annotations

import re

from core.compliance import normalize

TABLE: dict[str, tuple[str, ...]] = {
    "farm": (
        "a friendly cow", "a happy pig in a mud puddle", "a chicken with her chicks", "a horse in a field",
        "a fluffy sheep", "a goat on a rock", "a duck on a pond", "a rooster on a fence", "a donkey",
        "a red barn with a silo", "a tractor", "a farmer with a basket of eggs", "a bunny in a carrot patch",
        "a turkey", "a llama", "a cat on a hay bale", "a sheepdog", "a scarecrow in a corn field",
        "a beehive with bees", "a farm windmill",
    ),
    "ocean": (
        "a smiling whale", "a dolphin jumping", "an octopus", "a sea turtle", "a seahorse", "a starfish",
        "a crab on the sand", "a jellyfish", "a striped fish", "a friendly shark", "a narwhal",
        "a pufferfish", "a lobster", "a stingray", "a coral reef", "a submarine", "a seal on a rock",
        "a lighthouse by the sea", "a sailboat", "a shell on the beach",
    ),
    "dinosaurs": (
        "a friendly t-rex", "a triceratops", "a stegosaurus", "a long-necked brachiosaurus", "a pterodactyl",
        "a velociraptor", "an ankylosaurus", "a parasaurolophus", "a spinosaurus",
        "a baby dinosaur hatching from an egg", "a nest of dinosaur eggs", "a volcano with palm trees",
        "a diplodocus", "an iguanodon", "a pachycephalosaurus", "dinosaur footprints and fossils",
        "a kid digging up dinosaur bones", "a dinosaur skeleton in a museum",
    ),
    "space": (
        "a rocket ship", "an astronaut floating", "a planet with rings", "the moon with craters",
        "a friendly alien", "a flying saucer", "a space shuttle", "a satellite", "a telescope", "a comet",
        "a smiling sun", "a rover on a rocky planet", "a star constellation", "a space station",
        "a robot astronaut", "a spiral galaxy", "planet earth", "an astronaut planting a flag",
    ),
    "vehicles": (
        "a race car", "a fire truck", "a police car", "a school bus", "a tractor", "a dump truck",
        "a steam train", "an airplane", "a helicopter", "a sailboat", "a submarine", "a hot air balloon",
        "a motorcycle", "a bicycle", "an ambulance", "a garbage truck", "a cement mixer", "a tow truck",
        "a monster truck", "a rocket ship",
    ),
    "construction": (
        "an excavator", "a bulldozer", "a dump truck", "a tall crane", "a cement mixer", "a road roller",
        "a forklift", "a backhoe loader", "a hard hat and tools", "a construction worker",
        "traffic cones and a barrier", "a wheelbarrow with bricks", "a toolbox", "a front loader",
    ),
    "jungle": (
        "a lion", "an elephant", "a giraffe", "a zebra", "a monkey on a vine", "a hippo", "a rhino",
        "a cheetah", "a tiger", "a parrot", "a toucan", "a gorilla", "a crocodile", "a snake on a branch",
        "a sloth", "a flamingo", "a chameleon", "a lemur", "a meerkat", "a koala",
    ),
    "woodland": (
        "a fox", "a bear", "a deer", "an owl on a branch", "a raccoon", "a hedgehog", "a squirrel",
        "a rabbit", "a badger", "a skunk", "a moose", "a beaver", "a chipmunk", "a mushroom house",
        "a woodpecker", "a wolf", "a porcupine", "a log cabin in the woods",
    ),
    "pets": (
        "a puppy", "a kitten", "a hamster", "a goldfish in a bowl", "a bunny", "a parrot", "a guinea pig",
        "a turtle", "a dog with a ball", "a cat with a ball of yarn", "a budgie", "a puppy in a basket",
        "a dog house", "a pony", "a ferret", "a kitten in a box",
    ),
    "bugs": (
        "a butterfly", "a ladybug", "a bumblebee", "a caterpillar", "a dragonfly", "an ant", "a grasshopper",
        "a beetle", "a snail", "a firefly", "a spider in a web", "a praying mantis", "a worm", "a moth",
        "a bee on a flower", "a ladybug on a leaf",
    ),
    "birds": (
        "an owl", "a parrot", "a penguin", "a flamingo", "a peacock", "a toucan", "an eagle", "a robin",
        "a hummingbird", "a swan", "a duck", "a pelican", "an ostrich", "a woodpecker", "a cardinal",
        "a puffin", "a bird in a nest", "a bird house",
    ),
    "polar": (
        "a polar bear", "a penguin", "a seal", "a walrus", "an arctic fox", "a snowy owl", "a reindeer",
        "a husky", "an orca", "a narwhal", "an igloo", "a snowman", "a puffin", "a beluga whale",
    ),
    "flowers": (
        "a sunflower", "a rose", "a tulip", "a daisy", "a flower pot", "a watering can", "a garden gnome",
        "a bird house in a garden", "a tree", "a cactus", "a lotus", "a lily", "a hibiscus", "a bouquet",
        "a mushroom", "a bee on a flower", "a butterfly on a flower", "a wheelbarrow of flowers",
    ),
    "food": (
        "an apple", "a banana", "a strawberry", "a watermelon slice", "a pineapple", "two cherries",
        "a bunch of grapes", "a carrot", "broccoli", "an ear of corn", "a pizza slice", "a cupcake",
        "an ice cream cone", "a donut", "a taco", "a sandwich", "an avocado", "a pear", "a lemon",
        "a pumpkin", "a bowl of noodles", "a cup of hot cocoa",
    ),
    "sweets": (
        "a cupcake", "a donut", "an ice cream cone", "a slice of cake", "a cookie", "a lollipop",
        "a wrapped candy", "a macaron", "a popsicle", "a milkshake", "a pie", "a waffle",
        "a stack of pancakes", "a gingerbread house", "cotton candy", "a candy jar",
    ),
    "fantasy": (
        "a unicorn", "a friendly dragon", "a fairy", "a mermaid", "a castle", "a wizard hat", "a magic wand",
        "a rainbow", "a winged horse", "a garden gnome", "a crystal ball", "a treasure chest", "a phoenix",
        "a knight", "a princess", "a crown", "an enchanted tree", "a fairy house", "a potion bottle",
    ),
    "unicorns": (
        "a unicorn", "a baby unicorn", "a unicorn with a rainbow", "a unicorn in the clouds",
        "a unicorn with flowers in its mane", "a sleeping unicorn", "a unicorn cupcake", "a unicorn castle",
        "a unicorn and a star", "a unicorn in a meadow",
    ),
    "princess": (
        "a princess with a crown", "a castle", "a crown", "a royal carriage", "a royal throne", "a knight",
        "a friendly dragon", "a frog prince", "a ballgown", "a jewelry box", "a tiara", "a royal cake",
        "a princess and her pony", "a castle tower",
    ),
    "mermaids": (
        "a mermaid", "a mermaid tail", "a seashell", "a pearl in an oyster", "an underwater castle",
        "a seahorse", "a starfish", "a treasure chest", "coral", "a mermaid with a fish friend",
        "a sea turtle", "a dolphin",
    ),
    "pirates": (
        "a pirate ship", "a treasure chest", "a treasure map", "a parrot on a perch", "a pirate hat",
        "an anchor", "an island with a palm tree", "a spyglass", "a compass", "a ship wheel",
        "a pirate captain", "a message in a bottle", "an octopus", "a pirate flag",
    ),
    "robots": (
        "a friendly robot", "a robot dog", "a robot arm", "gears and cogs", "a retro robot",
        "a robot with an antenna", "a robot reading a book", "a robot gardener", "a flying robot",
        "a robot chef",
    ),
    "christmas": (
        "a christmas tree", "santa claus", "a reindeer", "a snowman", "a gingerbread man", "a candy cane",
        "a christmas stocking", "a wrapped present", "a wreath", "a holiday elf", "a christmas ornament",
        "a sleigh", "jingle bells", "a polar bear in a scarf", "a nutcracker soldier", "holly leaves",
    ),
    "halloween": (
        "a jack-o-lantern", "a friendly ghost", "a bat", "a black cat", "a witch hat", "a cauldron",
        "candy corn", "a spider web", "a haunted house", "an owl on a branch", "a friendly skeleton",
        "a mummy", "a broomstick", "a candy bucket", "a friendly monster", "a full moon with bats",
        "a scarecrow",
    ),
    "easter": (
        "an easter bunny", "easter eggs", "an easter basket", "a chick hatching", "a lamb", "a carrot",
        "spring flowers", "a bunny with a basket", "a decorated egg", "a duckling",
    ),
    "valentines": (
        "a heart", "a teddy bear holding a heart", "a love letter", "a cupid with a bow", "a bunch of roses",
        "a heart-shaped box of chocolates", "two lovebirds", "a heart balloon",
    ),
    "thanksgiving": (
        "a turkey", "a pumpkin pie", "a horn of plenty", "autumn leaves", "a scarecrow", "a harvest basket",
        "a pilgrim hat", "an ear of corn",
    ),
    "birthday": (
        "a birthday cake", "balloons", "a wrapped present", "a party hat", "a cupcake with a candle",
        "a party horn", "confetti and streamers", "a pinata",
    ),
    "seasons": (
        "a sun with sunglasses", "a beach ball", "a sandcastle", "a snowflake", "a snowman", "an umbrella",
        "autumn leaves", "a pumpkin", "spring flowers", "a kite", "a rainbow", "a mitten",
    ),
    "sports": (
        "a soccer ball", "a basketball and hoop", "a baseball and bat", "a football", "a tennis racket",
        "a trophy", "a medal", "a skateboard", "roller skates", "a swimmer", "a gymnast", "a bicycle",
    ),
    "music": (
        "a guitar", "a piano", "a drum set", "a violin", "a trumpet", "a xylophone", "a microphone",
        "music notes", "a ukulele", "a tambourine",
    ),
    "weather": (
        "a smiling sun", "a cloud", "a rainbow", "an umbrella in the rain", "a snowflake",
        "a lightning cloud", "a tornado", "a wind sock",
    ),
    "camping": (
        "a tent", "a campfire", "a marshmallow on a stick", "a backpack", "a lantern", "a canoe",
        "a compass", "a fishing rod", "a sleeping bag", "a camper van", "a mountain lake",
    ),
    "animals": (
        "a lion", "an elephant", "a cow", "a puppy", "a kitten", "a fox", "an owl", "a penguin",
        "a giraffe", "a bear", "a bunny", "a turtle", "a whale", "a monkey", "a pig", "a duck",
    ),
}

# "animals" is dropped when the theme already names a kind of animal ("farm animals" ->
# farm only), but kept beside a non-animal key ("spring animals" -> flowers + animals).
GENERIC_KEYS = frozenset({"animals"})
ANIMAL_KEYS = frozenset({"farm", "ocean", "dinosaurs", "jungle", "woodland", "pets", "bugs", "birds", "polar"})

# Single-word stems (see _stem) that point at a table key. Style adjectives ("kawaii",
# "sweet", "cute") are deliberately absent: "kawaii animals" must not become a food pack.
ALIASES: dict[str, str] = {
    "farm": "farm", "barn": "farm", "barnyard": "farm", "cow": "farm", "pig": "farm",
    "ocean": "ocean", "sea": "ocean", "underwater": "ocean", "marine": "ocean", "beach": "ocean",
    "fish": "ocean", "whale": "ocean", "shark": "ocean",
    "dinosaur": "dinosaurs", "dino": "dinosaurs", "prehistoric": "dinosaurs",
    "space": "space", "planet": "space", "astronaut": "space", "rocket": "space", "galaxy": "space",
    "vehicle": "vehicles", "transportation": "vehicles", "transport": "vehicles", "car": "vehicles",
    "truck": "vehicles", "train": "vehicles", "airplane": "vehicles", "plane": "vehicles",
    "construction": "construction", "digger": "construction", "excavator": "construction",
    "jungle": "jungle", "safari": "jungle", "zoo": "jungle", "rainforest": "jungle", "wild": "jungle",
    "woodland": "woodland", "forest": "woodland",
    "pet": "pets", "dog": "pets", "puppy": "pets", "cat": "pets", "kitten": "pets", "kitty": "pets",
    "bug": "bugs", "insect": "bugs", "butterfly": "bugs", "garden": "flowers",
    "bird": "birds", "owl": "birds",
    "polar": "polar", "arctic": "polar", "antarctic": "polar", "penguin": "polar", "winter": "polar",
    "flower": "flowers", "floral": "flowers", "plant": "flowers", "spring": "flowers",
    "food": "food", "fruit": "food", "vegetable": "food", "veggie": "food", "snack": "food",
    "sweets": "sweets", "dessert": "sweets", "candy": "sweets", "cupcake": "sweets", "bakery": "sweets",
    "fantasy": "fantasy", "magic": "fantasy", "magical": "fantasy", "fairy": "fantasy",
    "dragon": "fantasy", "fairytale": "fantasy",
    "unicorn": "unicorns", "princess": "princess", "castle": "princess",
    "mermaid": "mermaids", "pirate": "pirates", "treasure": "pirates", "robot": "robots",
    "christmas": "christmas", "xmas": "christmas", "santa": "christmas",
    "halloween": "halloween", "spooky": "halloween", "easter": "easter",
    "valentine": "valentines", "love": "valentines", "thanksgiving": "thanksgiving", "fall": "thanksgiving",
    "autumn": "thanksgiving", "harvest": "thanksgiving", "birthday": "birthday", "party": "birthday",
    "summer": "seasons", "season": "seasons", "sport": "sports", "soccer": "sports",
    "music": "music", "musical": "music", "instrument": "music", "weather": "weather",
    "camping": "camping", "camp": "camping", "outdoor": "camping",
    "animal": "animals", "critter": "animals",
}

TEMPLATES: tuple[str, ...] = (
    "a big friendly {t} character smiling", "a {t} scene in a sunny meadow", "a {t} parade",
    "a {t} picnic", "a {t} birthday party with balloons", "a {t} adventure in the forest",
    "a {t} day at the beach", "a snowy winter {t} scene", "a rainy day {t} scene with puddles",
    "a {t} treehouse", "a {t} hot air balloon ride", "a {t} garden with big flowers",
    "a {t} bedtime scene with the moon and stars", "a {t} band playing music", "a {t} bakery with cupcakes",
    "a {t} boat trip on gentle waves", "a {t} train ride", "a {t} camping trip with a tent",
    "a {t} school day", "a {t} sports day", "a {t} castle", "a {t} scene under the sea",
    "a {t} trip to outer space", "a {t} rainbow scene", "a {t} snack time", "a {t} gardening day",
    "a {t} art class with paint brushes", "a {t} kite flying day", "a {t} bubble bath",
    "a {t} hero with a cape", "a {t} pirate ship", "a {t} construction site", "a {t} market stall with fruit",
    "a {t} ice cream shop", "a {t} library with tall bookshelves", "a {t} tea party",
)

POSES: tuple[str, ...] = (
    "sleeping peacefully", "waving hello", "wearing a tiny party hat", "holding a flower",
    "under an umbrella in the rain", "with a big heart", "reading a book", "with a balloon",
    "wearing a scarf", "having a picnic", "with a butterfly friend", "dancing",
    "wearing sunglasses", "with a present", "eating a snack", "looking at the stars",
)

_SKIP_SINGULAR = ("ss", "us", "is", "mas", "ics")


def _stem(word: str) -> str:
    """Crude singular form, enough to match table aliases ("puppies" -> "puppy")."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(_SKIP_SINGULAR):
        return w[:-1]
    return w


def _article(text: str) -> str:
    """Fix "a" before a vowel after filling a template ("a abstract ..." -> "an abstract ...")."""
    return re.sub(r"\ba (?=[aeiou])", "an ", text)


def singular_phrase(theme: str) -> str:
    """Theme as an adjective: "farm animals" -> "farm animal", "dinosaurs" -> "dinosaur"."""
    words = theme.split()
    return " ".join(words[:-1] + [_stem(words[-1])]) if words else theme


def matched_keys(theme: str, keywords: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Table keys for the theme (in word order); keywords count only if the theme matched nothing."""
    def keys_for(text: str) -> list[str]:
        out = []
        for token in re.findall(r"[a-z]+", normalize(text)):
            key = ALIASES.get(_stem(token)) or ALIASES.get(token)
            if key and key not in out:
                out.append(key)
        return out

    keys = keys_for(theme)
    if not keys:
        for kw in keywords:
            keys += [k for k in keys_for(kw) if k not in keys]
    if any(k in ANIMAL_KEYS for k in keys):
        keys = [k for k in keys if k not in GENERIC_KEYS]
    return keys


def subject_pool(theme: str, keywords: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Every distinct subject this theme can offer, best first. See module docstring."""
    curated: list[str] = []
    lists = [TABLE[k] for k in matched_keys(theme, keywords)]
    for i in range(max((len(x) for x in lists), default=0)):
        for x in lists:
            if i < len(x) and x[i] not in curated:
                curated.append(x[i])
    adj = singular_phrase(theme.strip().lower())
    pool = list(curated)
    pool += [_article(t.format(t=adj)) for t in TEMPLATES]
    bases = curated or [f"a cute {adj} character"]
    pool += [f"{b}, {pose}" for pose in POSES for b in bases]
    return list(dict.fromkeys(pool))


def pick_subjects(theme: str, keywords: list[str] | tuple[str, ...], n: int) -> list[str]:
    """The first n subjects of the pool; raises if the theme cannot fill n distinct subjects."""
    pool = subject_pool(theme, keywords)
    if n > len(pool):
        raise ValueError(f"theme {theme!r} has only {len(pool)} distinct subjects; {n} were asked for")
    return pool[:n]
