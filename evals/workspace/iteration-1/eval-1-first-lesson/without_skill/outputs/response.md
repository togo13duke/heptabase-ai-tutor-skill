今日は15分で「辞書を作る・値を取り出す」を学び、余裕があれば更新まで進めます。全体は **作成と取得 → 更新と追加 → 存在しないキー → 削除と繰り返し** の順です。

まず3分ほどの小さな例から始めましょう。

C#の `Dictionary<string, int>` と同じように、Pythonの辞書も「キー」と「値」を対応させます。

```python
stock = {"apple": 3, "banana": 2}
print(stock["apple"])  # 3
```

- `{...}` で辞書を作ります。
- `"apple": 3` は、キー `"apple"` に値 `3` が対応するという意味です。
- `stock["apple"]` は、C#と同じようにキーで値を取り出します。

この例では、変数や辞書の型を明示する必要はありません。

では一つだけ確認です。**`stock["banana"]` は何を返すでしょうか？**

[学習計画](evals/workspace/iteration-1/eval-1-first-lesson/without_skill/outputs/learning/dicts/PLAN.md)と[進捗](evals/workspace/iteration-1/eval-1-first-lesson/without_skill/outputs/learning/dicts/PROGRESS.md)を保存しました。現在は「説明済み・理解は未確認」として、回答を待つところまで記録しています。
