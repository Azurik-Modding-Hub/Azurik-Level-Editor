"""Read and replace the existing bitmap dialogue panels in retail Azurik XBRs.

Dialogue text is baked into SURF textures. There is no plaintext text table
to recover here: users see the original image and author replacement text.
Replacements preserve resource indices, pointers and extents. Text edits keep
the original frame and footer; image imports deliberately replace the complete
panel. Only supported original pixel blocks are changed. No game files bundled.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import io
import math
from pathlib import Path
import struct


MAX_POPUP_PNG_BYTES = 16 * 1024 * 1024
MAX_POPUP_IMAGE_DIMENSION = 4096
MAX_POPUP_IMAGE_PIXELS = 4096 * 4096


@dataclass(frozen=True)
class Section:
    index: int
    tag: str
    offset: int
    size: int
    flags: int


def read_sections(data: bytes) -> list[Section]:
    if len(data) < 64 or data[:4] != b"xobx" or struct.unpack_from("<I", data, 4)[0] != 4:
        raise ValueError("Banque XBR retail version 4 requise.")
    count, payload = struct.unpack_from("<2I", data, 12)
    if not 1 <= count <= 100000 or 64 + (count + 1) * 16 > payload or payload > len(data):
        raise ValueError("Table des ressources XBR invalide.")
    sections, previous_end = [], 0
    for index in range(count):
        size, tag, flags, end = struct.unpack_from("<I4sII", data, 64 + index * 16)
        start = payload + previous_end
        if not all(32 <= c < 127 for c in tag) or not size or start + size > len(data):
            raise ValueError(f"Ressource XBR {index} hors du fichier.")
        # Retail banks use zero as the final cumulative END sentinel; the
        # last payload still has a normal bounded size and start.
        if not (end == 0 and index == count - 1) and (end < previous_end + size or payload + end > len(data)):
            raise ValueError(f"Étendue XBR {index} invalide.")
        sections.append(Section(index, tag.decode("ascii"), start, size, flags))
        previous_end = end
    return sections


def resource_names(data: bytes) -> dict[int, str]:
    sections = read_sections(data)
    count, table, pool, size = struct.unpack_from("<4I", data, 0x2C)
    if count > len(sections) or not 64 <= table <= pool or table + count * 8 > pool or pool + size > len(data):
        raise ValueError("Catalogue des noms XBR invalide.")
    names = {}
    for row in range(count):
        index, relative = struct.unpack_from("<2I", data, table + row * 8)
        if index >= len(sections) or relative >= size or index in names:
            raise ValueError("Référence de ressource dupliquée ou invalide.")
        end = data.find(b"\0", pool + relative, pool + size)
        if end < 0:
            raise ValueError("Nom XBR sans terminateur.")
        try:
            name = data[pool + relative:end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Nom de ressource non ASCII.") from exc
        if not name or len(name) > 1024 or name in names.values():
            raise ValueError("Nom XBR vide ou dupliqué.")
        names[index] = name
    return names


def _surface(data, section):
    if section.tag != "surf" or section.size < 128:
        raise ValueError("Le dialogue ne référence pas une surface image.")
    base, stop = section.offset, section.offset + section.size
    flags = struct.unpack_from("<I", data, base)[0]
    fmt = flags & 0xFFFF
    if fmt not in (0x10, 0x20, 0x40) or flags & ~0x1000FFFF:
        raise ValueError("Format de texture 2D non pris en charge.")
    wf, hf = struct.unpack_from("<2f", data, base + 28)
    if not all(math.isfinite(v) and v.is_integer() and 1 <= v <= 1024 for v in (wf, hf)):
        raise ValueError("Dimensions de surface invalides.")
    width, height = int(wf), int(hf)
    pixels = base + 40 + struct.unpack_from("<i", data, base + 40)[0]
    if not base + 44 <= pixels < stop:
        raise ValueError("Pointeur de pixels invalide.")
    levels = max(width, height).bit_length() if flags & 0x10000000 else 1
    if levels > 1 and any(v & (v - 1) for v in (width, height)):
        raise ValueError("Dimensions des mipmaps invalides.")
    mipmaps = []
    for level in range(levels):
        extent = width * height * 4 if fmt == 0x10 else max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * (8 if fmt == 0x20 else 16)
        if pixels + extent > stop:
            raise ValueError("La chaîne de mipmaps dépasse la ressource.")
        mipmaps.append((pixels, width, height, extent))
        pixels += extent
        width, height = max(1, width // 2), max(1, height // 2)
    return fmt, mipmaps


def _decode_image(data, section):
    from PIL import Image
    fmt, mips = _surface(data, section)
    offset, width, height, size = mips[0]
    pixels = data[offset:offset + size]
    if fmt == 0x10:
        return Image.frombytes("RGBA", (width, height), pixels, "raw", "BGRA")
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<7I", header, 4, 124, 0x81007, height, width, size, 0, 1)
    struct.pack_into("<II4s5I", header, 76, 32, 4, b"DXT1" if fmt == 0x20 else b"DXT3", 0, 0, 0, 0, 0)
    struct.pack_into("<I", header, 108, 0x1000)
    with Image.open(io.BytesIO(bytes(header) + pixels)) as image:
        return image.convert("RGBA")


def _popup(data, resource):
    names = resource_names(data)
    if isinstance(resource, str):
        matches = [index for index, name in names.items() if name == resource]
        if len(matches) != 1:
            raise ValueError("Dialogue introuvable dans cette banque.")
        resource = matches[0]
    if type(resource) is not int or resource not in names:
        raise ValueError("Identifiant du dialogue invalide.")
    name = names[resource]
    if "/popups/" not in name.casefold() or not name.casefold().endswith(".tif"):
        raise ValueError("Seuls les panneaux de dialogue existants sont modifiables.")
    section = read_sections(data)[resource]
    _surface(data, section)
    return section, name


def scan_dialogues(data: bytes, language: str | None = None) -> list[dict]:
    sections, names = read_sections(data), resource_names(data)
    result = []
    for index, name in names.items():
        if sections[index].tag != "surf" or "/popups/" not in name.casefold() or not name.casefold().endswith(".tif"):
            continue
        section = sections[index]
        fmt, mips = _surface(data, section)
        token = name.split("/")[1] if name.startswith("loc/") else "unknown"
        if language and token.casefold() != language.casefold():
            continue
        voice = name[:-4] + "-snd"
        sound_index = next((i for i, value in names.items() if value == voice), None)
        result.append({"index": index, "resource": name, "language": token,
                       "width": mips[0][1], "height": mips[0][2],
                       "format": {0x10: "BGRA8", 0x20: "DXT1", 0x40: "DXT3"}[fmt],
                       "mipLevels": len(mips), "voiceResource": voice if sound_index is not None else None,
                       "voiceIndex": sound_index, "sourceKind": "bitmap", "text": None})
    return result


def decode_popup_png(data: bytes, resource, *, display: bool = True) -> bytes:
    from PIL import Image
    section, _ = _popup(data, resource)
    image = _decode_image(data, section)
    if display:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def default_text_options(width=256, height=256) -> dict:
    return {"fontSize": 20, "fontFamily": "serif", "color": "#EEEDBE", "align": "center",
            "rect": [max(4, width * 3 // 32), max(4, height * 5 // 32),
                     max(4, width * 29 // 32), max(4, height * 53 // 64)]}


def _font(size, family):
    from PIL import ImageFont
    if family not in ("serif", "sans"):
        raise ValueError("Choisissez une police serif ou sans.")
    candidates = ([r"C:\Windows\Fonts\times.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "DejaVuSerif.ttf"]
                  if family == "serif" else [r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"])
    for filename in candidates:
        try:
            return ImageFont.truetype(filename, size=size)
        except OSError:
            continue
    raise ValueError("Installez une police Times New Roman/Arial ou DejaVu pour écrire le texte.")


def _render(data, resource, text, options=None):
    from PIL import Image, ImageDraw, ImageColor
    if not isinstance(text, str) or len(text) > 4000 or not text.strip() or any(ord(c) < 32 and c != "\n" for c in text):
        raise ValueError("Saisissez un texte de 1 à 4 000 caractères sans caractères de contrôle.")
    section, name = _popup(data, resource)
    raw = _decode_image(data, section)
    image = raw.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    config = default_text_options(*image.size)
    if options is not None:
        if not isinstance(options, dict) or set(options) - set(config):
            raise ValueError("Options de texte inconnues.")
        config.update(options)
    size = config["fontSize"]
    if type(size) is not int or not 8 <= size <= 48 or config["align"] not in ("left", "center", "right"):
        raise ValueError("Taille de police ou alignement invalide.")
    rect = config["rect"]
    if not isinstance(rect, (list, tuple)) or len(rect) != 4 or any(type(v) is not int for v in rect):
        raise ValueError("Rectangle de texte invalide.")
    x0, y0, x1, y1 = rect
    if not 0 <= x0 < x1 <= image.width or not 0 <= y0 < y1 <= image.height or x1 - x0 < 8 or y1 - y0 < 8:
        raise ValueError("Le rectangle de texte dépasse l’image.")
    try:
        color = ImageColor.getrgb(config["color"])
        if len(color) != 3:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("Couleur RGB invalide.") from exc
    font = _font(size, config["fontFamily"])
    draw = ImageDraw.Draw(image)
    width, height = x1 - x0, y1 - y0
    lines = []
    for paragraph in text.split("\n"):
        words, current = paragraph.split(), ""
        if not words:
            lines.append("")
            continue
        for word in words:
            if draw.textlength(word, font=font) > width:
                raise ValueError("Un mot est trop long pour le panneau. Réduisez la police ou ajoutez un retour à la ligne.")
            candidate = f"{current} {word}" if current else word
            if current and draw.textlength(candidate, font=font) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    line_height = sum(font.getmetrics()) + max(1, size // 6)
    total_height = len(lines) * line_height
    if total_height > height:
        raise ValueError("Ce texte dépasse le panneau. Réduisez la police ou raccourcissez le texte.")
    # Keep the original panel's dominant background (usually black alpha 204),
    # rather than filling with opaque black or erasing its transparency.
    background = Counter(image.crop(rect).getdata()).most_common(1)[0][0]
    draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=background)
    y = y0 + (height - total_height) // 2
    for line in lines:
        length = draw.textlength(line, font=font)
        x = x0 if config["align"] == "left" else x1 - length if config["align"] == "right" else x0 + (width - length) / 2
        draw.text((x, y), line, font=font, fill=(*color, 255), anchor="lt")
        y += line_height
    return image, tuple(rect), config, section, name


def render_popup_text(data: bytes, resource, text: str, options: dict | None = None) -> bytes:
    image, *_ = _render(data, resource, text, options)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _render_imported_image(data, resource, png_bytes):
    """Validate an authored PNG and fit it to the existing complete panel.

    Input is upright, like decode_popup_png's default display orientation. The
    whole image, including its frame/footer and alpha, is supplied by the user.
    No image file is changed and no external service is used.
    """
    from PIL import Image, UnidentifiedImageError
    if not isinstance(png_bytes, (bytes, bytearray, memoryview)):
        raise ValueError("L’image importée doit être un fichier PNG.")
    if not 0 < len(png_bytes) <= MAX_POPUP_PNG_BYTES:
        raise ValueError("Le fichier PNG doit faire au maximum 16 Mio.")
    png_bytes = bytes(png_bytes)
    if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Seuls les fichiers PNG sont acceptés pour les panneaux.")
    section, name = _popup(data, resource)
    _, mips = _surface(data, section)
    width, height = mips[0][1:3]
    try:
        with Image.open(io.BytesIO(png_bytes)) as source:
            if source.format != "PNG" or getattr(source, "n_frames", 1) != 1:
                raise ValueError("Importez un PNG statique, sans animation.")
            input_width, input_height = source.size
            if not (1 <= input_width <= MAX_POPUP_IMAGE_DIMENSION and
                    1 <= input_height <= MAX_POPUP_IMAGE_DIMENSION and
                    input_width * input_height <= MAX_POPUP_IMAGE_PIXELS):
                raise ValueError("Les dimensions du PNG ne doivent pas dépasser 4 096 × 4 096 pixels.")
            # Validate all chunks before decoding instead of accepting a
            # truncated image that happens to expose a valid first header.
            source.verify()
        with Image.open(io.BytesIO(png_bytes)) as source:
            image = source.convert("RGBA")
            image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Le fichier PNG est invalide ou incomplet.") from exc
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return image, section, name, {
        "imageSha256": hashlib.sha256(png_bytes).hexdigest(),
        "inputWidth": input_width, "inputHeight": input_height,
        "width": width, "height": height,
        "resized": (input_width, input_height) != (width, height),
    }


def render_popup_image(data: bytes, resource, png_bytes: bytes) -> bytes:
    """Return the imported whole panel as a native-sized, upright RGBA PNG.

    Resizing uses Lanczos. This preview precedes the existing surface format's
    compression/alpha quantization, which can be seen by decoding the patched
    bank with decode_popup_png.
    """
    image, *_ = _render_imported_image(data, resource, png_bytes)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _rgb565(rgb):
    return ((rgb[0] * 31 + 127) // 255 << 11) | ((rgb[1] * 63 + 127) // 255 << 5) | ((rgb[2] * 31 + 127) // 255)


def _rgb888(value):
    return (((value >> 11) & 31) * 255 // 31, ((value >> 5) & 63) * 255 // 63, (value & 31) * 255 // 31)


def _encode_block(block, fmt):
    opaque = [p for p in block if fmt != 0x20 or p[3] >= 128]
    if opaque:
        seed = min(opaque, key=lambda p: sum(p[:3]))
        high = max(opaque, key=lambda p: sum((p[c] - seed[c]) ** 2 for c in range(3)))
        low = max(opaque, key=lambda p: sum((p[c] - high[c]) ** 2 for c in range(3)))
    else:
        low = high = (0, 0, 0)
    a, b = _rgb565(high), _rgb565(low)
    transparent = fmt == 0x20 and len(opaque) < 16
    if transparent:
        a, b = min(a, b), max(a, b)
    else:
        a, b = max(a, b), min(a, b)
        if a == b:
            if a < 65535:
                a += 1
            else:
                b -= 1
    colors = [_rgb888(a), _rgb888(b)]
    if transparent:
        colors.extend([tuple((colors[0][c] + colors[1][c]) // 2 for c in range(3)), (0, 0, 0)])
    else:
        colors.extend([tuple((2 * colors[0][c] + colors[1][c]) // 3 for c in range(3)), tuple((colors[0][c] + 2 * colors[1][c]) // 3 for c in range(3))])
    bits = 0
    for index, pixel in enumerate(block):
        choice = 3 if transparent and pixel[3] < 128 else min(range(3 if transparent else 4), key=lambda c: sum((pixel[n] - colors[c][n]) ** 2 for n in range(3)))
        bits |= choice << (2 * index)
    alpha = b""
    if fmt == 0x40:
        alpha = struct.pack("<Q", sum(((p[3] * 15 + 127) // 255) << (4 * i) for i, p in enumerate(block)))
    return alpha + struct.pack("<HHI", a, b, bits)


def replace_popup_text(data: bytes, resource, text: str, options: dict | None = None) -> bytes:
    """Return same-length XBR bytes; preserve all nonselected pixel blocks.

    Text length may vary within the existing panel. This does not create new
    conversations or change triggers. Rendering is vertically flipped for
    the existing game UVs. Every original mip level is updated.
    """
    from PIL import Image
    displayed, rect, config, section, name = _render(data, resource, text, options)
    raw = displayed.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    fmt, mips = _surface(data, section)
    original_width, original_height = raw.size
    x0, y0, x1, y1 = rect
    raw_rect = (x0, original_height - y1, x1, original_height - y0)
    result = bytearray(data)
    for offset, width, height, extent in mips:
        current = raw if raw.size == (width, height) else raw.resize((width, height), Image.Resampling.LANCZOS)
        rx0 = max(0, math.floor(raw_rect[0] * width / original_width) - (2 if width < original_width else 0))
        ry0 = max(0, math.floor(raw_rect[1] * height / original_height) - (2 if height < original_height else 0))
        rx1 = min(width, math.ceil(raw_rect[2] * width / original_width) + (2 if width < original_width else 0))
        ry1 = min(height, math.ceil(raw_rect[3] * height / original_height) + (2 if height < original_height else 0))
        if fmt == 0x10:
            blob = current.tobytes("raw", "BGRA")
            for y in range(ry0, ry1):
                start, end = (y * width + rx0) * 4, (y * width + rx1) * 4
                result[offset + start:offset + end] = blob[start:end]
        else:
            pixels = current.load()
            block_bytes, blocks_w = (8 if fmt == 0x20 else 16), max(1, (width + 3) // 4)
            for by in range(ry0 // 4 * 4, ry1, 4):
                for bx in range(rx0 // 4 * 4, rx1, 4):
                    block = [pixels[min(bx + x, width - 1), min(by + y, height - 1)] for y in range(4) for x in range(4)]
                    position = offset + (by // 4 * blocks_w + bx // 4) * block_bytes
                    result[position:position + block_bytes] = _encode_block(block, fmt)
    return bytes(result)


def _replace_whole_image(data, displayed, section):
    from PIL import Image
    # Retail UVs address a vertically inverted source. Only the stored pixels
    # change: every descriptor, resource index and following extent is retained.
    raw = displayed.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    fmt, mips = _surface(data, section)
    result = bytearray(data)
    for offset, width, height, extent in mips:
        current = raw if raw.size == (width, height) else raw.resize((width, height), Image.Resampling.LANCZOS)
        if fmt == 0x10:
            encoded = current.tobytes("raw", "BGRA")
        else:
            pixels = current.load()
            encoded = bytearray()
            for by in range(0, height, 4):
                for bx in range(0, width, 4):
                    block = [pixels[min(bx + x, width - 1), min(by + y, height - 1)]
                             for y in range(4) for x in range(4)]
                    encoded.extend(_encode_block(block, fmt))
        if len(encoded) != extent:
            raise AssertionError("Encoded image does not fit its original mip extent")
        result[offset:offset + extent] = encoded
    if len(result) != len(data):
        raise AssertionError("An image replacement changed the bank length")
    return bytes(result)


def replace_popup_image(data: bytes, resource, png_bytes: bytes) -> bytes:
    """Replace every pixel/mip of one existing panel, keeping the XBR size.

    The input PNG supplies the entire visible panel. Its dimensions are fitted
    to the original resource, and the original BGRA8/DXT1/DXT3 format remains
    intact. Other resources, names, pointers and headers are byte-identical.
    """
    displayed, section, *_ = _render_imported_image(data, resource, png_bytes)
    return _replace_whole_image(data, displayed, section)


def popup_image_change(data: bytes, resource, png_bytes: bytes) -> tuple[bytes, dict]:
    """Image replacement and source-free project/export verification metadata."""
    displayed, section, name, details = _render_imported_image(data, resource, png_bytes)
    patched = _replace_whole_image(data, displayed, section)
    return patched, {"kind": "dialogue-image", "resource": name, "resourceIndex": section.index,
                     **details, "beforeSha256": hashlib.sha256(data).hexdigest(),
                     "afterSha256": hashlib.sha256(patched).hexdigest(),
                     "sameLength": len(data) == len(patched), "sourceKind": "bitmap",
                     "changesOnlyExistingResource": True, "replacesWholePanel": True}


def popup_change(data: bytes, resource, text: str, options: dict | None = None) -> tuple[bytes, dict]:
    """Replacement plus project/export metadata; no raw game bytes in metadata."""
    patched = replace_popup_text(data, resource, text, options)
    section, name = _popup(data, resource)
    rendered, rect, config, *_ = _render(data, resource, text, options)
    return patched, {"kind": "dialogue-text", "resource": name, "resourceIndex": section.index,
                     "text": text, "options": config, "beforeSha256": hashlib.sha256(data).hexdigest(),
                     "afterSha256": hashlib.sha256(patched).hexdigest(), "sameLength": len(data) == len(patched),
                     "sourceKind": "bitmap", "changesOnlyExistingResource": True}
