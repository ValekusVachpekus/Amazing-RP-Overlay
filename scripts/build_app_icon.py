"""Package the generated artwork as a multi-resolution Windows ICO."""
import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "amazing_overlay/assets"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def main():
    artwork = QImage(str(ASSETS / "amazing-helper.png"))
    if artwork.isNull() or artwork.width() != artwork.height():
        raise ValueError("Icon artwork must be a readable square image")
    frames = []
    for size in SIZES:
        image = artwork.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation).convertToFormat(QImage.Format_ARGB32)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError("Cannot encode icon frame")
        frames.append(bytes(buffer.data()))
    offset = 6 + 16 * len(frames)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(frames)))
    for size, frame in zip(SIZES, frames):
        directory.extend(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(frame), offset))
        offset += len(frame)
    output = ASSETS / "amazing-helper.ico"
    output.write_bytes(directory + b"".join(frames))
    print("ICO ready:", output, "; sizes:", ", ".join(map(str, SIZES)))


if __name__ == "__main__":
    main()
