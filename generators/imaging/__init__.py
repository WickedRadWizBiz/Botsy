"""Shared machinery for the AI image generators (coloring_pages, stickers).

    client.py   ImageClient protocol, GeminiImageClient (google-genai generate_content,
                retries, per-run image cap) and the offline FakeImageClient
    subjects.py built-in theme -> distinct subject table
    prompts.py  prompts from our own brief fields, blocklist-checked before any API call
    cleanup.py  line-art cleanup + potrace/vtracer, sticker background removal + die-cut border
    pdf.py      page placement (SVG or raster) and sticker-sheet grids
    common.py   item setup, batch generation with spare subjects, manifest basics
    cli.py      the image generators' entry to the shared CLI (generators/cli.py)
Brief checks, item-folder rules and Etsy file-name limits shared with the activity
sheets live in generators/shared.py.
"""
