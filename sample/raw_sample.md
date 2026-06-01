# 強制的にHTML、外部ライブラリを使う
@rawと--head、--bottomオプションを使って直接HTMLの操作を行う例。
この例の様にグラフを画像ではなくjsで書きたいなどの時の参考。

## グラフ
@rawでグラフ用のHTMLを書く。
[morris.js](https://morrisjs.github.io/morris.js/)を使った。

```@raw
<div id="myfirstchart" style="height: 250px;"></div>
```

--headでcdnを記述したファイル、--bottomでjsのコードを読み込む。
コマンドラインは次の通り。
```shell
$ ./ManualMarkdown.py sample/raw_sample.md --head sample/raw_sample.cdn --bottom sample/raw_sample.js.html -o ./sample
```