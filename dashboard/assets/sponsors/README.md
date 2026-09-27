# Sponsor logos for the 3D view

Logos appear on trackside barrier boards (cycling through the list) and on car roofs and bonnets (car N gets sponsor N mod count).

The installed PNGs are the original artwork extracted from `finished final FTH Opening ceremony slides.pdf`: Tangerine and Ampere (gold, page 5), Ollon and TELUS (silver, page 6), Red Bull and AWAKE (community, page 7). White backgrounds were removed from the TELUS and AWAKE image assets when preparing the earlier car skins. Logos are not redrawn or generated.

Panels repeat all six sponsors in tier order. Tangerine also appears on the start/finish gantry. The artwork is fitted to the physical panel dimensions without stretching and repeated across wide panels. Sponsor colours appear as a narrow bottom stripe; AWAKE uses a dark background so its yellow lettering remains readable.

`settings.toml` → `[scene]` controls `board_gap_m`, `board_height_m`, `panel_length_m`, `railing_depth_m`, `railing_post_spacing_m` and `railing_color`. The railings have corrugated steel backs, top caps and instanced support posts. Printing is visible from the track side only. These are dashboard scene assets, not replacements for Assetto Corsa's KN5 track files.

1. Put the official logo files here, from the hackathon's sponsor/press kit. PNG with transparency works best.
2. List them in `sponsors.json`:

```json
[
  { "name": "Acme",   "file": "acme.png",   "bg": "#ffffff" },
  { "name": "Globex", "file": "globex.png", "bg": "#101010" }
]
```

- `file` is optional. Without it, or if the image fails to load, the name is drawn as text in `fg` on `bg`.
- `bg` is the board background behind the logo. Pick one that each logo is readable on.
- Reload the dashboard after editing. Textures are built once when the 3D view opens.
