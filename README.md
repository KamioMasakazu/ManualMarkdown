# ManualMarkdown
マニュアル用MarkdownをHTMLに変換するツール。  
- コードブロック以外はHTMLエスケープを行う。
- 空行を置きたい場合は2以上の空白文字だけの行を書く。
- 画像その他でマークダウンに指定したパスはinputファイルのディレクトリからの相対パスとして処理する。
- 1つのHTMLだけで配布可能な様にcssやjsは埋め込む。画像埋め込みはオプション。
- @csv、@box拡張書式を持つ。
- 改行までを段落として扱う。改行のために文末にスペース2個は不要。
- ヘッダ行毎に階層化された<section>になる。

## 使い方
### 入力ファイルと出力先だけ指定
カレントディレクトリにdefault.cssが必要。  
画像ファイルは「出力先ディレクトリ/assets」以下にコピーされる。  

```shell
ManualMarkdown.py sample/sample.md -o ./html
```

### CSS/JSを埋め込む
指定したパスのCSSとJavaScriptをHTMLに展開する。

```shell
ManualMarkdown.py sample/sample.md -o ./html --css ./sample/css/ --js ./sample/jslib/
```

### 画像をdata URLで埋め込む
画像データもHTMLに埋め込んでしまいたい場合。完全にHTMLだけで完結したドキュメントになる。

```shell
ManualMarkdown.py sample/sample.md -o ./html --css ./sample/css/ --js ./sample/jslib/ --embed-image
```

## 拡張書式
枠で囲われた文章（注釈とか）を作るための書式。  
サンプルにnoticeとinfoクラスがあるので参照。

### @box
````markdown
```@box:class_name[titile]
何か文章。
・・・
```
````

次のHTMLに変換される。
```html
<div class="class_name">
	<div>
		title
	</div>
	<div>
		<p>何か文章。</p>
		<p>・・・</p>
	</div>
</div>
```

### @csv
csvデータをテーブルにする拡張書式。  
MarkdownのTableより&lt;th&gt;の指定が柔軟。  

#### ファイル指定
指定したCSVファイルをテーブルに展開する。
```markdown
@csv(path/to/target.csv)
@csv:th_col(path/to/target.csv)
@csv:th_row(path/to/target.csv)
```

:th_colは先頭列を&lt;th&gt;にする指定。  
:th_colは先頭列を&lt;th&gt;にする指定。  
省略した場合は全部&lt;td&gt;。  

#### インライン指定
コードブロック内のCSVデータをテーブルに展開する。  
````markdown
```@csv:th_row
a,b,c
d,e,f
CSV,テーブル,サンプル
```
````

:th_col, :th_rowの指定はファイル指定と同じ。  
(path/to/target.csv)は書かない（データはコードブロックにあるので）。

## その他
### 横線
次のクラスが&lt;ht&gt;に付加される。  
指定に毎に&lt;ht&gt;のスタイルを変える時に使う。

|線種|クラス|
|----|------|
|-の連続|solid-minus|
|-の破線|break-minus|
|_の連続|solid-under|
|_の破線|break-under|
|*の連続|solid-asta|
|*の破線|break-asta|