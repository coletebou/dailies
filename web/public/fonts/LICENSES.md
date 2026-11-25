# Bundled fonts

Latin subsets (woff2) of three typefaces, each licensed under the SIL Open Font License, Version 1.1
(full text in `OFL.txt` next to this file). The subsets are served by Dailies as web fonts; the OFL permits
bundling and redistribution, provided the copyright notices and licence travel with them.

| File | Typeface | Copyright |
|---|---|---|
| `archivo.woff2` | Archivo (variable: wdth, wght) | Copyright 2020 The Archivo Project Authors (https://github.com/Omnibus-Type/Archivo) |
| `instrument-serif-italic.woff2` | Instrument Serif Italic | Copyright 2022 The Instrument Serif Project Authors (https://github.com/Instrument/instrument-serif) |
| `jetbrains-mono.woff2` | JetBrains Mono | Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono) |

The files were produced with fonttools:

    pyftsubset <font>.ttf --unicodes=U+0000-00FF,U+2000-206F,U+2190-2199,U+25A0-25FF --layout-features='*' --flavor=woff2
