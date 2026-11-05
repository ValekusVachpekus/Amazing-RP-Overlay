# Amazing Helper application icon

`amazing-helper.png` is the original artwork, generated with the built-in image_gen
tool for this project. `amazing-helper.ico` contains 16, 20, 24, 32, 40, 48, 64, 128
and 256 pixel frames. Regenerate the ICO with the project environment:

```powershell
.venv/Scripts/python.exe scripts/build_app_icon.py
```

The artwork is used by the Windows EXE, the Inno Setup installer and Qt windows.
Format conversion only resizes the original artwork; it does not redraw it.

Generation prompt:

> Use case: logo-brand. Create a finished square Windows desktop application icon for Amazing Helper. A solid dark graphite charcoal background (#24272C), flat front-facing design filling the entire square canvas edge to edge, subtle refined lighter graphite inner border. The only content is exactly two centered lines of large bold clean condensed sans-serif lettering: line 1 "Amazing", line 2 "Helper". Preserve this exact spelling and capitalization. Bright warm white lettering with excellent contrast, both lines optically balanced, close line spacing, text block occupying most of the icon width with comfortable narrow safe margins. Crisp minimal professional typography suitable for a Windows taskbar and desktop icon; prioritize readability at small sizes. No pictures, no symbols, no monogram, no extra words, no watermark, no mockup, no perspective, no surrounding background, no decorative glossy effects. Opaque graphite background. Produce a single square icon.
