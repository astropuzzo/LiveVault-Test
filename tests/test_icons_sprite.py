import xml.dom.minidom
from pathlib import Path

SPRITE = Path(__file__).resolve().parents[1] / "app" / "static" / "icons.svg"


def test_icon_sprite_is_valid_xml_with_nsfw_symbols():
    # One malformed symbol breaks every icon of the UI.
    doc = xml.dom.minidom.parse(str(SPRITE))
    ids = {node.getAttribute("id") for node in doc.getElementsByTagName("symbol")}
    assert {"nsfw-breast", "nsfw-butt", "nsfw-anus", "nsfw-vulva", "nsfw-phallus", "vault"} <= ids
