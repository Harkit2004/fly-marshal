# Sponsor logos for the 3D view

Logos appear on trackside barrier boards (cycling through the list) and on car roofs and bonnets (car N gets sponsor N mod count).

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
