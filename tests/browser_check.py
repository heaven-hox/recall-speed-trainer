"""Optional end-to-end check. Run with Playwright + Chromium; no app dependency.

python tests/browser_check.py [--apkg PATH --apkg PATH] [--screenshot /tmp/ui.png]
User-provided decks are inspected locally and never copied into the repository.
"""

import argparse
import json
import sys
import tempfile
import threading
from math import floor
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
        # Use an independent browser store to exercise per-card eligibility end to end.
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, accept_downloads=True)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        expect(page.locator("#saved-status")).to_have_text("ブラウザに保存済み")
        if files:
            source = files[0]
            source_data = import_apkg(source.read_bytes())
            deck_name = source_data["decks"][0]["name"]
            card_count = len(source_data["cards"])
        else:
            source = Path(temporary) / "cards.csv"
            answers = [answer for answer in ROMAJI if len(answer) == 1]
            source.write_text("question,answer,deck\n" + "".join(f"symbol:{a},{a},CardSelection\n" for a in answers), encoding="utf-8")
            deck_name, card_count = "CardSelection", len(answers)
        page.locator("#deck-file").set_input_files(str(source))
        expect(page.locator("#import-dialog")).to_be_visible()
        expect(page.locator("#import-preview tr")).to_have_count(card_count)
        page.locator("#import-disable-all").click()
        a_group, u_group = set("あかさたな"), set("うくすつぬ")
        for answer in a_group:
            page.locator("#import-dialog").get_by_role("checkbox", name=f"{answer}の出題を有効にする", exact=True).check()
        page.locator('#field-mappings select[name="answer"]').dispatch_event("change")
        expect(page.locator("#import-preview input:checked")).to_have_count(5)
        page.locator("#confirm-import").click()
        expect(page.locator("#import-dialog")).not_to_be_visible()
        upload(sample)  # Other decks must remain unaffected by the primary deck's editor.
        select_decks([deck_name])
        expect(page.locator("#selected-count")).to_have_text(f"選択中：5枚（無効：{card_count - 5}枚）")
        initial, _ = exported()
        deck_id = next(d["id"] for d in initial["decks"] if d["name"] == deck_name)
        history_ids = {c["id"]: c["historyId"] for c in initial["cards"]}

        def active_answers(data):
            return {c["answer"] for c in data["cards"] if c["deckId"] == deck_id and c.get("enabled", True)}

        assert active_answers(initial) == a_group
        checks.append("取り込み時の個別チェック・30枚より後も選択可能・他デックとの独立")
        page.locator("#delay").fill("0")
        for mode in ["rounds", "random", "weak"]:
            page.locator(f'[name=order][value={mode}]').check()
            if mode == "rounds":
                page.locator("#rounds").fill("1")
            else:
                page.locator("#count").fill("8")
            total = 5 if mode == "rounds" else 8
            page.locator("#start").click()
            expect(page.get_by_role("button", name=f"{deck_name}のカードを選択", exact=True)).to_be_disabled()
            seen = []
            for ordinal in range(1, total + 1):
                wait_question(ordinal)
                image = page.locator("#question img")
                answer = image_answers[image.get_attribute("src")] if image.count() else page.locator("#question").inner_text().split(":", 1)[1]
                assert answer in a_group, answer
                seen.append(answer)
                page.locator("#answer").press_sequentially(ROMAJI[answer])
            expect(page.locator("#start")).to_be_enabled()
            if mode == "rounds":
                assert set(seen) == a_group
        trained, _ = exported()
        assert len(trained["attempts"]) == 21
        assert all(a["answer"] in a_group for a in trained["attempts"])
        assert all(len(s["cardIds"]) == 5 for s in trained["sessions"])
        checks.append("有効な5枚だけを全3方式で出題・無効な未学習カードも除外")

        def open_cards():
            page.get_by_role("button", name=f"{deck_name}のカードを選択", exact=True).click()
            expect(page.locator("#cards-dialog")).to_be_visible()

        open_cards()
        page.locator("#cards-disable-all").click()
        page.locator("#cancel-cards").click()
        cancelled, _ = exported()
        assert active_answers(cancelled) == a_group
        open_cards()
        page.locator("#cards-disable-all").click()
        for answer in u_group:
            page.locator("#cards-dialog").get_by_role("checkbox", name=f"{answer}の出題を有効にする", exact=True).check()
        page.locator("#cards-search").fill("ぬ")
        expect(page.locator("#cards-body tr")).to_have_count(1)
        page.locator("#cards-disable-all").click()
        page.locator("#cards-search").fill("")
        expect(page.locator("#cards-body input:checked")).to_have_count(4)
        page.locator("#cards-dialog").get_by_role("checkbox", name="ぬの出題を有効にする", exact=True).check()
        page.evaluate("""() => {
          const original = IDBObjectStore.prototype.put;
          IDBObjectStore.prototype.put = function(...args) {
            if (this.name === 'cards') {
              IDBObjectStore.prototype.put = original;
              throw new DOMException('test quota failure', 'QuotaExceededError');
            }
            return original.apply(this, args);
          };
        }""")
        page.locator("#save-cards").click()
        expect(page.locator("#cards-error")).to_be_visible()
        expect(page.locator("#cards-body input:checked")).to_have_count(5)
        page.locator("#save-cards").click()
        expect(page.locator("#cards-dialog")).not_to_be_visible()
        changed, _ = exported()
        assert active_answers(changed) == u_group
        assert {c["id"]: c["historyId"] for c in changed["cards"]} == history_ids
        assert len(changed["attempts"]) == 21
        assert not changed["resets"]
        assert all(c.get("enabled", True) for c in changed["cards"] if c["deckId"] != deck_id)
        checks.append("あ段からう段へ切替・検索中の一括操作・キャンセル・保存再試行・履歴保持")

        expect(page.locator("#stats-body tr")).to_have_count(card_count)
        expect(page.locator("#metric-count")).to_have_text("21")
        page.locator("#history-scope").select_option("enabled")
        expect(page.locator("#stats-body tr")).to_have_count(5)
        expect(page.locator("#metric-count")).to_have_text("0")
        for metric in ["metric-accuracy", "metric-first", "metric-total"]:
            expect(page.locator("#" + metric)).to_have_text("—")
        page.reload()
        expect(page.locator("#history-scope")).to_have_value("enabled")
        expect(page.locator("#stats-body tr")).to_have_count(5)
        expect(page.locator("#metric-count")).to_have_text("0")
        page.locator("#history-scope").select_option("all")
        expect(page.locator("#stats-body tr")).to_have_count(card_count)
        expect(page.locator("#metric-count")).to_have_text("21")
        page.locator("#history-scope").select_option("enabled")
        expect(page.locator("#selected-count")).to_have_text(f"選択中：5枚（無効：{card_count - 5}枚）")
        page.locator("#deck-file").set_input_files(str(source))
        expect(page.locator("#import-dialog")).to_be_visible()
        for answer in u_group:
            expect(page.locator("#import-dialog").get_by_role("checkbox", name=f"{answer}の出題を有効にする", exact=True)).to_be_checked()
        for answer in a_group:
            expect(page.locator("#import-dialog").get_by_role("checkbox", name=f"{answer}の出題を有効にする", exact=True)).not_to_be_checked()
        page.locator("#confirm-import").click()
        expect(page.locator("#import-dialog")).not_to_be_visible()
        selected_backup, _ = exported()
        assert active_answers(selected_backup) == u_group
        assert {c["id"]: c["historyId"] for c in selected_backup["cards"]} == history_ids
        checks.append("再読み込み・同じデックの再取り込みで選択と履歴を保持")

        open_cards()
        page.locator("#cards-disable-all").click()
        page.locator("#save-cards").click()
        expect(page.locator("#start")).to_be_disabled()
        expect(page.locator("#selected-count")).to_have_text(f"選択中：0枚（無効：{card_count}枚）")
        expect(page.locator("#stats-body")).to_contain_text("表示対象の有効なカードがありません")
        expect(page.locator("#metric-count")).to_have_text("0")

        def restore(data):
            destination = Path(temporary) / "selection-backup.json"
            destination.write_text(json.dumps(data))
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#backup-file").set_input_files(str(destination))
            expect(page.locator("#message")).to_have_text("バックアップを読み込みました。")

        restore(selected_backup)
        expect(page.locator("#history-scope")).to_have_value("enabled")
        expect(page.locator("#stats-body tr")).to_have_count(5)
        expect(page.locator("#selected-count")).to_have_text(f"選択中：5枚（無効：{card_count - 5}枚）")
        legacy = json.loads(json.dumps(selected_backup))
        for card in legacy["cards"]:
            card.pop("enabled", None)
        legacy["settings"].pop("historyScope", None)
        restore(legacy)
        expect(page.locator("#history-scope")).to_have_value("all")
        expect(page.locator("#selected-count")).to_have_text(f"選択中：{card_count}枚")
        expect(page.locator("#start")).to_be_enabled()
        restore(selected_backup)
        checks.append("全無効で開始不可・バックアップで選択を復元・旧データは全有効")
        page.locator('[name=order][value=weak]').check()
        page.locator("#count").fill("5")
        total, seen = 5, []
        page.locator("#start").click()
        for ordinal in range(1, 6):
            wait_question(ordinal)
            image = page.locator("#question img")
            answer = image_answers[image.get_attribute("src")] if image.count() else page.locator("#question").inner_text().split(":", 1)[1]
            assert answer in u_group, answer
            seen.append(answer)
            page.locator("#answer").press_sequentially("1" if ordinal == 1 else ROMAJI[answer])
        expect(page.locator("#start")).to_be_enabled()
        assert set(seen) == u_group
        checks.append("う段への切替後は新しい未学習5枚を1回ずつ出題")
        final_data, _ = exported()
        assert final_data["settings"]["historyScope"] == "enabled"
        expect(page.locator("#metric-count")).to_have_text("5")
        expect(page.locator("#metric-accuracy")).to_have_text("80.0%")
        for scope, count, accuracy in [("all", "26", "96.2%"), ("enabled", "5", "80.0%")]:
            page.locator("#history-scope").select_option(scope)
            expect(page.locator("#metric-count")).to_have_text(count)
            expect(page.locator("#metric-accuracy")).to_have_text(accuracy)
            correct = [a for a in final_data["attempts"] if a["correct"] and (scope == "all" or a["answer"] in u_group)]
            for metric, field in [("metric-first", "firstMs"), ("metric-total", "totalMs")]:
                values = [a[field] for a in correct if a[field] is not None]
                expected = f"{floor(sum(values) / len(values) + 0.5)} ms"
                expect(page.locator("#" + metric)).to_have_text(expected)
        assert len(final_data["attempts"]) == 26
        checks.append("履歴の全カード／有効のみ切替・4指標の集計・空表示・再読込と復元で保持")
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
