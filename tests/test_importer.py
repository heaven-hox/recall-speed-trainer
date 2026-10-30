import base64
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

import zstandard

from importer import ImportError, import_apkg, import_csv, parse_field, protobuf_fields

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def varint(n):
    result = bytearray()
    while n > 127:
        result.append((n & 127) | 128)
        n >>= 7
    return bytes(result) + bytes([n])


def field(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    return varint((number << 3) | 2) + varint(len(value)) + value


def package(modern=True, corrupt=False):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "collection"
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""
                CREATE TABLE notes(id, guid, mid, flds);
                CREATE TABLE cards(id, nid, did, ord);
                INSERT INTO notes VALUES(1,'guid',7,'<img src="x.png">'||char(31)||'あんか'||char(31)||'安価');
                INSERT INTO cards VALUES(1,1,9,0);
            """)
            if modern:
                db.executescript("""
                    CREATE TABLE notetypes(id,name);
                    CREATE TABLE fields(ntid,ord,name);
                    CREATE TABLE decks(id,name);
                    INSERT INTO notetypes VALUES(7,'基本');
                    INSERT INTO fields VALUES(7,0,'表面'),(7,1,'裏面'),(7,2,'単語');
                    INSERT INTO decks VALUES(9,'V式-単語');
                """)
            else:
                db.execute("CREATE TABLE col(models,decks)")
                db.execute("INSERT INTO col VALUES(?,?)", (json.dumps({"7": {"name": "基本", "flds": [
                    {"name": "表面", "ord": 0}, {"name": "裏面", "ord": 1}, {"name": "単語", "ord": 2}]}}),
                    json.dumps({"9": {"name": "V式-単語"}})))
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as z:
            if modern:
                compress = zstandard.ZstdCompressor().compress
                z.writestr("meta", field(1, 3))
                z.writestr("collection.anki21b", compress(path.read_bytes()))
                # A legacy compatibility notice must NOT be selected as the collection.
                z.writestr("collection.anki2", b"not the actual collection")
                entry = field(1, b"x.png") + field(2, len(PNG)) + field(3, hashlib.sha1(PNG).digest())
                z.writestr("media", compress(field(1, entry)))
                z.writestr("0", compress(PNG + (b"bad" if corrupt else b"")))
            else:
                z.writestr("collection.anki2", path.read_bytes())
                z.writestr("media", json.dumps({"0": "x.png"}))
                z.writestr("0", PNG)
        return output.getvalue()


class ImporterTests(unittest.TestCase):
    def test_database_is_closed_before_temporary_directory_cleanup(self):
        for modern in (True, False):
            with self.subTest(modern=modern):
                data = package(modern)
                connections = []
                real_connect = sqlite3.connect
                real_directory = tempfile.TemporaryDirectory

                def connect(*args, **kwargs):
                    db = real_connect(*args, **kwargs)
                    connections.append(db)
                    return db

                @contextmanager
                def windows_directory(*args, **kwargs):
                    with real_directory(*args, **kwargs) as folder:
                        try:
                            yield folder
                        finally:
                            for db in connections:
                                try:
                                    db.execute("SELECT 1")
                                except sqlite3.ProgrammingError:
                                    continue
                                db.close()
                                raise PermissionError("Windows cannot remove an open SQLite database")

                with patch("importer.sqlite3.connect", side_effect=connect), \
                     patch("importer.tempfile.TemporaryDirectory", side_effect=windows_directory):
                    result = import_apkg(data)
                self.assertEqual(len(connections), 1)
                self.assertEqual(len(result["cards"]), 1)

    def test_database_is_closed_when_reading_fails(self):
        data = package()
        connections = []
        real_connect = sqlite3.connect

        def connect(*args, **kwargs):
            db = real_connect(*args, **kwargs)
            connections.append(db)
            return db

        with patch("importer.sqlite3.connect", side_effect=connect), \
             patch("importer.parse_field", side_effect=ValueError("invalid field")), \
             self.assertRaises(ImportError):
            import_apkg(data)
        self.assertEqual(len(connections), 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            connections[0].execute("SELECT 1")

    def test_temporary_database_path_can_contain_unicode_spaces_and_hash(self):
        data = package()
        with tempfile.TemporaryDirectory() as folder:
            special = Path(folder) / "日本語 # path"
            special.mkdir()
            with patch("importer.tempfile.tempdir", str(special)):
                result = import_apkg(data)
        self.assertEqual(len(result["cards"]), 1)

    def test_modern_and_legacy_field_mapping_and_media(self):
        for modern in (True, False):
            with self.subTest(modern=modern):
                result = import_apkg(package(modern))
                self.assertEqual(result["noteTypes"][0]["fields"], ["表面", "裏面", "単語"])
                self.assertEqual(result["decks"][0]["name"], "V式-単語")
                self.assertEqual(len(result["cards"]), 1)
                self.assertEqual(result["cards"][0]["fields"][1]["text"], "あんか")
                self.assertEqual(result["cards"][0]["fields"][2]["text"], "安価")
                self.assertTrue(result["media"]["x.png"].startswith("data:image/png;base64,"))

    def test_media_integrity_failure_is_rejected(self):
        with self.assertRaisesRegex(ImportError, "チェックサム"):
            import_apkg(package(corrupt=True))

    def test_truncated_metadata_is_rejected(self):
        for data in (b"\x0a\x05abc", b"\x08\x80", b"\x00"):
            with self.subTest(data=data), self.assertRaises(ImportError):
                list(protobuf_fields(data))

    def test_scripts_and_external_elements_are_inert(self):
        result = parse_field('<script>alert(1)</script><style>x</style><div>あ&amp;い</div><img src="x.png"><audio>hidden</audio>')
        self.assertEqual(result["text"], "あ&い")
        self.assertNotIn("alert", str(result))
        self.assertEqual([p["name"] for p in result["parts"] if p["type"] == "image"], ["x.png"])
        self.assertTrue(result["warnings"])

    def test_csv_and_tsv_are_plain_text(self):
        result = import_csv('\ufeffquestion,answer,extra,deck\n"<b>画像ではない</b>",apple,りんご,英語\n'.encode(), "words.csv")
        self.assertEqual(result["cards"][0]["fields"][0]["text"], "<b>画像ではない</b>")
        self.assertEqual(result["decks"][0]["name"], "英語")
        result = import_csv("question\tanswer\n安価\tあんか\n".encode(), "words.txt")
        self.assertEqual(result["cards"][0]["fields"][1]["text"], "あんか")

    def test_csv_requires_columns_and_unique_ids(self):
        for content in [b"a,b\nc,d\n", b"question,answer\na,one\na,two\n", b"question,answer\na\n"]:
            with self.subTest(content=content), self.assertRaises(ImportError):
                import_csv(content, "x.csv")
        result = import_csv(b"question,answer,id\na,one,1\na,two,2\n", "x.csv")
        self.assertEqual(len(result["cards"]), 2)

    def test_invalid_archive(self):
        with self.assertRaises(ImportError):
            import_apkg(b"not a zip")


if __name__ == "__main__":
    unittest.main()
