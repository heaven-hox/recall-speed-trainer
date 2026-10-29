"""Read Anki exports without executing templates or modifying the source package."""

import base64
import csv
import hashlib
import io
import json
import re
import sqlite3
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import zstandard

MAX_UPLOAD = 50 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_CARDS = 10000


class ImportError(ValueError):
    pass


def protobuf_fields(data):
    """Read protobuf wire fields; ignore schema fields not needed by this importer."""
    pos = 0

    def varint():
        nonlocal pos
        result = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ImportError("パッケージのメタデータが途中で切れています。")
            byte = data[pos]
            pos += 1
            result |= (byte & 127) << shift
            if byte < 128:
                return result
        raise ImportError("パッケージのメタデータが不正です。")

    while pos < len(data):
        tag = varint()
        field, wire = tag >> 3, tag & 7
        if not field:
            raise ImportError("パッケージのメタデータが不正です。")
        if wire == 0:
            yield field, varint()
            continue
        if wire == 2:
            size = varint()
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
        else:
            raise ImportError("未対応のメタデータ形式です。")
        if size > len(data) - pos:
            raise ImportError("パッケージのメタデータ長が不正です。")
        value = data[pos:pos + size]
        pos += size
        yield field, value


class FieldParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
        self.warnings = set()

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "audio", "video", "iframe", "object"):
            self.hidden += 1
            self.warnings.add("スクリプト・音声などの未対応要素を除外しました。")
        elif not self.hidden:
            if tag == "img":
                src = dict(attrs).get("src", "")
                self.parts.append({"type": "image", "name": src})
            elif tag in ("br", "div", "p", "li"):
                self.parts.append({"type": "text", "value": "\n"})

    def handle_endtag(self, tag):
        if tag in ("script", "style", "audio", "video", "iframe", "object"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append({"type": "text", "value": data})


def parse_field(value):
    parser = FieldParser()
    parser.feed(value)
    parts = parser.parts
    text = "".join(p["value"] for p in parts if p["type"] == "text").strip()
    if re.search(r"\[sound:[^]]+\]", text):
        parser.warnings.add("音声は対象外です。")
        text = re.sub(r"\[sound:[^]]+\]", "", text).strip()
        for part in parts:
            if part["type"] == "text":
                part["value"] = re.sub(r"\[sound:[^]]+\]", "", part["value"])
    return {"text": text, "parts": parts, "warnings": sorted(parser.warnings)}


def image_url(data):
    if data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        return None
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def import_apkg(data):
    if len(data) > MAX_UPLOAD:
        raise ImportError("ファイルは50MiB以下にしてください。")
    try:
        return _import_apkg(data)
    except ImportError:
        raise
    except (zipfile.BadZipFile, sqlite3.Error, zstandard.ZstdError, UnicodeError,
            KeyError, TypeError, IndexError, ValueError, OSError, RuntimeError, NotImplementedError) as exc:
        raise ImportError("Ankiパッケージを読み取れません。形式やファイルの破損を確認してください。") from exc


def _import_apkg(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) > 20050 or len(names) != len(set(names)):
            raise ImportError("パッケージのファイル数または重複エントリーが不正です。")
        if sum(i.file_size for i in archive.infolist()) > MAX_EXPANDED:
            raise ImportError("展開後のパッケージが大きすぎます。")
        expanded = 0

        def read(name, compressed=False):
            nonlocal expanded
            content = archive.read(name)
            if compressed:
                with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(content)) as reader:
                    content = reader.read(MAX_EXPANDED - expanded + 1)
            expanded += len(content)
            if expanded > MAX_EXPANDED:
                raise ImportError("展開後のパッケージが大きすぎます。")
            return content

        modern = "collection.anki21b" in names
        if modern:
            meta = dict(protobuf_fields(read("meta")))
            if meta.get(1) != 3:
                raise ImportError("未対応のAnkiパッケージバージョンです。")
            collection = read("collection.anki21b", compressed=True)
        elif "collection.anki21" in names:
            collection = read("collection.anki21")
        elif "collection.anki2" in names:
            collection = read("collection.anki2")
        else:
            raise ImportError("Ankiのコレクションが見つかりません。")

        media = {}
        if "media" in names:
            manifest = read("media", compressed=modern)
            if modern:
                entries = [dict(protobuf_fields(v)) for f, v in protobuf_fields(manifest) if f == 1]
                for index, entry in enumerate(entries):
                    content = read(str(index), compressed=True)
                    name = entry[1].decode("utf-8")
                    if len(content) != entry[2] or hashlib.sha1(content).digest() != entry[3]:
                        raise ImportError("同梱メディアのサイズ・チェックサムが一致しません。")
                    media[name] = image_url(content)
            else:
                for number, name in json.loads(manifest).items():
                    media[name] = image_url(read(number))

    with tempfile.TemporaryDirectory(prefix="recall-import-") as folder:
        path = Path(folder) / "collection.sqlite"
        path.write_bytes(collection)
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only = ON")
            db.execute("PRAGMA trusted_schema = OFF")
            db.create_collation("unicase", lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
            # Bound malicious/corrupt database queries as well as archive expansion.
            steps = 0

            def progress():
                nonlocal steps
                steps += 1
                return steps > 10000

            db.set_progress_handler(progress, 1000)
            count = db.execute("SELECT count(*) FROM cards").fetchone()[0]
            if count > MAX_CARDS:
                raise ImportError("1回の取り込みは10,000カードまでです。")
            rows = db.execute("""SELECT c.id, c.ord, c.did, n.guid, n.mid, n.flds
                                 FROM cards c JOIN notes n ON n.id = c.nid ORDER BY c.id""").fetchall()
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "notetypes" in tables:
                models = {str(mid): {"id": str(mid), "name": name, "fields": []}
                          for mid, name in db.execute("SELECT id,name FROM notetypes")}
                for mid, name in db.execute("SELECT ntid,name FROM fields ORDER BY ntid,ord"):
                    if str(mid) in models:
                        models[str(mid)]["fields"].append(name)
                decks = {str(did): name.replace("\x1f", "::") for did, name in db.execute("SELECT id,name FROM decks")}
            else:
                old_models, old_decks = db.execute("SELECT models,decks FROM col").fetchone()
                models = {str(mid): {"id": str(mid), "name": model["name"],
                                    "fields": [f["name"] for f in sorted(model["flds"], key=lambda f: f["ord"])]}
                          for mid, model in json.loads(old_models).items()}
                decks = {str(did): deck["name"] for did, deck in json.loads(old_decks).items()}
            used_models, used_decks = set(), set()
            cards = []
            for cid, ordinal, did, guid, mid, values in rows:
                mid, did = str(mid), str(did)
                fields = values.split("\x1f")
                if mid not in models or did not in decks or len(fields) != len(models[mid]["fields"]):
                    raise ImportError("カードとフィールドの定義に不整合があります。")
                used_models.add(mid)
                used_decks.add(did)
                cards.append({"id": f"anki:{guid}:{ordinal}", "sourceId": str(cid),
                              "deckId": "anki:" + did, "noteTypeId": mid,
                              "fields": [parse_field(f) for f in fields]})
    if not cards:
        raise ImportError("カードが含まれていません。")
    return {"noteTypes": [models[mid] for mid in sorted(used_models)],
            "decks": [{"id": "anki:" + did, "name": decks[did]} for did in sorted(used_decks)],
            "cards": cards, "media": {k: v for k, v in media.items() if v is not None}}


def import_csv(data, filename):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ImportError("CSV/TXTはUTF-8で保存してください。") from exc
    delimiter = "\t" if filename.lower().endswith((".tsv", ".txt")) else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames or "question" not in reader.fieldnames or "answer" not in reader.fieldnames:
        raise ImportError("列名 question,answer が必要です。任意列は extra,deck,id です。TXTはタブ区切りです。")
    cards, decks = [], {}
    for index, row in enumerate(reader, 2):
        if index > MAX_CARDS + 1:
            raise ImportError("1回の取り込みは10,000カードまでです。")
        if None in row or row.get("answer") is None:
            raise ImportError(f"{index}行目の列数が不正です。")
        deck_name = row.get("deck") or Path(filename).stem
        deck_id = "csv:" + hashlib.sha256(deck_name.encode()).hexdigest()[:24]
        decks[deck_id] = {"id": deck_id, "name": deck_name}
        identity = row.get("id") or row["question"]
        card_id = "csv:" + hashlib.sha256((deck_id + "\0" + identity).encode()).hexdigest()
        def plain(value):
            return {"text": value, "parts": [{"type": "text", "value": value}], "warnings": []}
        cards.append({"id": card_id, "sourceId": str(index), "deckId": deck_id, "noteTypeId": "csv",
                      "fields": [plain(row["question"]), plain(row["answer"]), plain(row.get("extra") or "")]})
    if not cards:
        raise ImportError("カードが含まれていません。")
    if len({c["id"] for c in cards}) != len(cards):
        raise ImportError("同じデック内に重複する問題があります。id列で別のIDを指定してください。")
    return {"noteTypes": [{"id": "csv", "name": "CSV/TXT", "fields": ["question", "answer", "extra"]}],
            "decks": list(decks.values()), "cards": cards, "media": {}}
