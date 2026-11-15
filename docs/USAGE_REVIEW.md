# 利用状況の記録 (2 週間ごと)

どの改善が使う人に効いたかを見て、次の優先度を決めるための表です (TODO.md の P1-7)。新しいデータは集めず、すでにある数を写します。
個人を特定できる数は書きません。

## 集め方

| 欄 | 出どころ | 公開 |
|---|---|---|
| GitHub の DL | `python scripts/traffic_report.py` のリリースの表 (Windows 用 EXE の zip と `.mcaddon` の合計) | 公開 (リリースのページにも出る) |
| CurseForge の DL | CurseForge のプロジェクトの管理画面 (手作業) | 公開 |
| `build-done`・`build-error` の割合・言語の上位 | GoatCounter のダッシュボード (`web/usage.js` のイベント。手作業) | 作者だけ |
| 閲覧数・参照元 | `scripts/traffic_report.py` (GitHub の Traffic は直近 14 日分) | 作者だけ。リポジトリには入れず、手元の記録に残す |

目安: `build-done` が CurseForge のお試し版の DL の 1% あれば、ブラウザ版の変換が使われている。

## 記録

GitHub の DL は、その時点での累計 (版ごと)。「未記入」は、作者の管理画面でしか見られない数。

| 日付 | GitHub の DL (最新 3 版の EXE / .mcaddon) | CurseForge の DL | build-done | build-error の割合 | 言語の上位 | メモ |
|---|---|---|---|---|---|---|
| 2026-11-15 | v6.37.1: 1 / 3、v6.37.0: 1 / 6、v6.36.0: 6 / 15 | 未記入 | 未記入 | 未記入 | 未記入 | 最初の記録。v6.37.1 はリリース当日 |

次の記録: 2026-11-29 ごろ。2 回分そろったら、メモの欄に「次に何を優先するか」を書く。
