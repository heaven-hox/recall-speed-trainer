"""Optional end-to-end check. Run with Playwright + Chromium; no app dependency.

python tests/browser_check.py [--apkg PATH --apkg PATH] [--screenshot /tmp/ui.png]
User-provided decks are inspected locally and never copied into the repository.
"""

import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright, expect

from importer import import_apkg
from server import Handler, ThreadingHTTPServer

ROMAJI = dict(zip("あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわ",
                   "a i u e o ka ki ku ke ko sa shi su se so ta chi tsu te to na ni nu ne no ha hi fu he ho ma mi mu me mo ya yu yo ra ri ru re ro wa".split()))
ROMAJI.update({"あんか": "anka", "かんさ": "kansa", "さんた": "santa", "たんな": "tanna", "なんあ": "nanna",
               "あんな": "anna", "さかな": "sakana", "かなた": "kanata", "たんか": "tanka"})


def run_check(files, screenshot):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    checks = []
    with tempfile.TemporaryDirectory(prefix="recall-browser-") as temporary, sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.locator("#saved-status")).to_have_text("ブラウザに保存済み")

        def upload(path):
            page.locator("#deck-file").set_input_files(str(path))
            expect(page.locator("#import-dialog")).to_be_visible()
            expect(page.locator("#confirm-import")).to_be_enabled()
            page.locator("#confirm-import").click()
            expect(page.locator("#import-dialog")).not_to_be_visible()

        def exported():
            with page.expect_download() as result:
                page.locator("#export-backup").click()
            destination = Path(temporary) / "backup.json"
            result.value.save_as(str(destination))
            return json.loads(destination.read_text()), destination

        def select_decks(names):
            for label in page.locator("#decks label").all():
                label.locator("input").set_checked(label.locator("span").inner_text() in names)

        def wait_question(ordinal):
            expect(page.locator("#progress")).to_have_text(f"{ordinal} / {total}")
            expect(page.locator("#answer")).to_be_enabled()

        expected_count = 0
        image_answers = {}
        deck_names = []
        for file in files:
            result = import_apkg(file.read_bytes())
            expected_count += len(result["cards"])
            deck_names.extend(d["name"] for d in result["decks"])
            for card in result["cards"]:
                field = card["fields"][0]
                image = next(part["name"] for part in field["parts"] if part["type"] == "image")
                image_answers[result["media"][image]] = card["fields"][1]["text"]
            upload(file)
        if files:
            expect(page.locator("#selected-count")).to_have_text(f"選択中：{expected_count}枚")
            checks.append(f"実デック {expected_count}枚の取り込み・画像表示")
            page.locator('[name=order][value=rounds]').check()
            page.locator("#rounds").fill("1")
            page.locator("#delay").fill("0")
            page.locator("#start").click()
            total = expected_count
            for ordinal in range(1, total + 1):
                wait_question(ordinal)
                source = page.locator("#question img").get_attribute("src")
                answer = image_answers[source]
                page.locator("#answer").press_sequentially("1" if ordinal == 1 else ROMAJI[answer])
                expect(page.locator("#metric-count")).to_have_text(str(ordinal))
            expect(page.locator("#start")).to_be_enabled()
            data, backup_path = exported()
            assert len(data["attempts"]) == total
            assert len({a["cardId"] for a in data["attempts"]}) == total
            assert sum(a["correct"] for a in data["attempts"]) == total - 1
            assert all(0 <= a["firstMs"] <= a["totalMs"] for a in data["attempts"])
            assert all(a["keys"] and a["keys"][0]["atMs"] >= 0 for a in data["attempts"])
            original_backup = json.dumps(data)
            checks.append("全カード1周・逐次の即時不正解・0ms待機・打鍵/時間ログ")
            page.reload()
            expect(page.locator("#metric-count")).to_have_text(str(total))
            checks.append("再読み込み後のデック・成績の保持")
            select_decks([deck_names[0]])
            page.locator("#open-reset").click()
            page.locator("#confirm-reset").click()
            expect(page.locator("#metric-count")).to_have_text("0")
            reset_data, _ = exported()
            assert len(reset_data["attempts"]) == total
            assert len(reset_data["resets"]) == 1
            select_decks(deck_names)
            remaining = total - len(reset_data["resets"][0]["cardIds"])
            expect(page.locator("#metric-count")).to_have_text(str(remaining))
            checks.append("選択デックだけの成績リセット・生ログ保持")
            select_decks([deck_names[0]])
            page.locator('[name=order][value=weak]').check()
            page.locator("#count").fill("2")
            page.locator("#start").click()
            total = 2
            seen = []
            for ordinal in (1, 2):
                wait_question(ordinal)
                source = page.locator("#question img").get_attribute("src")
                seen.append(source)
                page.locator("#answer").press_sequentially(ROMAJI[image_answers[source]])
            expect(page.locator("#start")).to_be_enabled()
            assert len(set(seen)) == 2
            checks.append("リセット後の苦手優先は未学習を重複なしで出題")

        sample = Path(temporary) / "sample.csv"
        sample.write_text("question,answer,extra,deck,id\n英語のりんご,Apple,りんご,English,a\n末尾のん,ん,区切り確認,N,n\n", encoding="utf-8")
        upload(sample)
        select_decks(["English"])
        page.locator('[name=order][value=random]').check()
        page.locator('[name=judge][value=enter]').check()
        page.locator("#case-sensitive").check()
        page.locator("#count").fill("3")
        page.locator("#delay").fill("0")
        page.locator("#start").click()
        total = 3
        wait_question(1)
        page.locator("#answer").press_sequentially("apple")
        page.locator("#answer").press("Enter")
        wait_question(2)
        page.locator("#answer").press_sequentially("AppleX")
        page.locator("#answer").press("Backspace")
        page.locator("#answer").press("Enter")
        wait_question(3)
        page.locator("#answer").press("Enter")
        expect(page.locator("#start")).to_be_enabled()
        expect(page.locator("#metric-count")).to_have_text("3")
        data, _ = exported()
        recent = sorted(data["attempts"], key=lambda a: a["endedAt"])[-3:]
        assert [a["correct"] for a in recent] == [False, True, False]
        assert recent[-1]["firstMs"] is None
        assert any(k["key"] == "Backspace" for k in recent[1]["keys"])
        checks.append("CSV・英字大小区別・Enter判定・修正キー・空回答")

        select_decks(["N"])
        page.locator('[name=judge][value=sequential]').check()
        page.locator("#count").fill("1")
        page.locator("#start").click()
        total = 1
        wait_question(1)
        page.locator("#answer").press_sequentially("n")
        expect(page.locator("#answer")).to_be_enabled()
        expect(page.locator("#metric-count")).to_have_text("0")
        page.evaluate("window.dispatchEvent(new Event('blur'))")
        expect(page.locator("#pause")).to_have_text("再開")
        page.locator("#pause").click()
        expect(page.locator("#answer")).to_have_value("")
        expect(page.locator("#answer")).to_be_enabled()
        page.locator("#answer").press_sequentially("nn")
        expect(page.locator("#start")).to_be_enabled()
        expect(page.locator("#metric-count")).to_have_text("1")
        data, _ = exported()
        n_attempts = [a for a in data["attempts"] if a["answer"] == "ん"]
        assert len(n_attempts) == 2 and sum(a["outcome"] == "interrupted" for a in n_attempts) == 1
        checks.append("末尾んの保留・フォーカス喪失中断・出し直し・中断を集計から除外")

        # A failed write must preserve the answer in memory and retry without duplication.
        page.locator("#start").click()
        wait_question(1)
        page.evaluate("""() => {
          const original = IDBObjectStore.prototype.put;
          IDBObjectStore.prototype.put = function(...args) {
            if (this.name === 'attempts') {
              IDBObjectStore.prototype.put = original;
              throw new DOMException('test quota failure', 'QuotaExceededError');
            }
            return original.apply(this, args);
          };
        }""")
        page.locator("#answer").press_sequentially("nn")
        expect(page.locator("#pause")).to_have_text("保存を再試行")
        page.locator("#pause").click()
        expect(page.locator("#start")).to_be_enabled()
        expect(page.locator("#metric-count")).to_have_text("2")
        checks.append("保存失敗後の再試行・回答の重複防止")

        # Feedback is shown, pausing it resumes at the next question, not a replay.
        select_decks(["English"])
        page.locator('[name=judge][value=enter]').check()
        page.locator("#count").fill("2")
        page.locator("#delay").fill("500")
        page.locator("#start").click()
        total = 2
        wait_question(1)
        page.locator("#answer").press_sequentially("Apple")
        page.locator("#answer").press("Enter")
        expect(page.locator("#feedback")).to_contain_text("○ 正解")
        page.locator("#pause").click()
        expect(page.locator("#pause")).to_have_text("再開")
        page.locator("#pause").click()
        wait_question(2)
        page.locator("#stop").click()
        expect(page.locator("#start")).to_be_enabled()
        checks.append("500msの結果表示・表示中の中断再開・途中終了")

        for button, marker in [("export-raw", b'"keys"'), ("export-csv", b"first_ms"), ("export-stats", b"history_id")]:
            with page.expect_download() as result:
                page.locator("#" + button).click()
            output = Path(temporary) / button
            result.value.save_as(str(output))
            assert marker in output.read_bytes()
        checks.append("生ログJSON・回答CSV・集計CSVの書き出し")

        if files:
            backup_path.write_text(original_backup)
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#backup-file").set_input_files(str(backup_path))
            expect(page.locator("#message")).to_have_text("バックアップを読み込みました。")
            expect(page.locator("#metric-count")).to_have_text(str(expected_count))
            restored, _ = exported()
            assert len(restored["attempts"]) == expected_count and not restored["resets"]
            invalid = dict(restored, version=999)
            backup_path.write_text(json.dumps(invalid))
            page.locator("#backup-file").set_input_files(str(backup_path))
            expect(page.locator("#message")).to_contain_text("読み込めませんでした")
            expect(page.locator("#metric-count")).to_have_text(str(expected_count))
            checks.append("バックアップ復元・不正バックアップで既存データを保護")
        if screenshot:
            page.reload()
            expect(page.locator("#saved-status")).to_have_text("ブラウザに保存済み")
            page.screenshot(path=str(screenshot), full_page=True)
        assert not errors, errors
        browser.close()
    server.shutdown()
    print("Browser checks passed:")
    for check in checks:
        print("  -", check)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apkg", type=Path, action="append", default=[])
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    run_check(args.apkg, args.screenshot)
