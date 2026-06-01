# ManualMarkdown
マニュアル用MarkdownをHTMLに変換するツール。  
- コードブロック以外はHTMLエスケープを行う。
- 空行を置きたい場合は2以上の空白文字だけの行を書く。
- 画像その他でマークダウンに指定したパスはinputファイルのディレクトリからの相対パスとして処理する。
- 1つのHTMLだけで配布可能な様にcssやjsは埋め込む。画像埋め込みはオプション。
- @〜〜〜の指定で拡張書式を使用可能。
- 改行までを段落として扱う。改行のために文末にスペース2個は不要。
- ヘッダ行毎に階層化された&lt;section&gt;になる。&lt;h1&gt;が含まれるセクションだけは&lt;header&gt;に記述される。

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

### --head、--bottom
外部のjs、cssライブラリを使用したり、jsのコードを埋め込みたい場合のオプション。  
これらと@rawコードブロックを併用すればHTML+CSS+JSでできることは出力ファイルでも実現できる。

- --head [file ...]  
  &lt;head&gt;タグ内で--css、--jsオプションが展開する前に指定したファイルの内容をそのまま展開する。  
  CDNなどの外部ライブラリを読み込みたい時に使う。

- --bottom [file ...]  
  &lt;/body&gt;直前に指定したファイルの内容をそのまま展開する。  
  jsのコードを末尾に書きたい時などに使う（&lt;script&gt;タグを補完したりしないので必要なら記述すること）。

## 拡張書式
マニュアルを書くときに便利なMarkdown拡張がいくつかある。

### @box
サンプルにnoticeとinfoクラスがあるので参照。
枠で囲われた文章（注釈とか）を作るための書式。  

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

### @flowbox
操作手順を簡単なフロー図にするための書式。  
次の様なコードブロックを書くとHTMLでは図になるように変換する。  
上から下に流れる単純な図しか書けない。途中に並列した手順のどれかというのは書ける。  
インデントは見やすさのためで意味を持たない。

````markdown
```@flowbox
手順1
細かい作業説明

手順2
細かい操作説明
説明2行目

@start-parallel
	手順3-1-1

	手順3-1-2
	@flow-block
	手順3-2-1

	手順3-2-2

	手順3-3-3
	@flow-block
	手順3-3-1

	手順3-3-2
@end-parallel

手順4
```
````

コードブロック中の指示は次の通り。
- 1以上の文字列からなる連続した行は1項目。空行で項目を区切る。
- @start-parallel、@end-parallel  
  この間は並列であることを表す。
- @flow-block  
  次の@flow-block、@end-parallel、コードブロックまでが1つの塊であることを表す。コードブロックの先頭と@start-parallel直後は省略できる。

これは次のような表示に整形可能なHTMLになる。
```
　　　　　　「　　手順1
　　　　　　　細かい作業手順」
　　　　　　　　　　 ▼
　　　　　　「　　手順2
　　　　　　　細かい作業手順
　　　　　　　説明2行目　　　」
　　　　　　　　　　 ▼
-------------------------------------------
「手順3-1-1」　「手順3-2-1」　「手順3-3-1」
　　　▼ 　　　　　　▼ 　　　　　　▼
「手順3-1-2」　「手順3-2-2」　「手順3-3-3」
　　　▼ 　　　　　　▼ 　　　　　　▼
　　　　　　　 「手順3-2-3」
-------------------------------------------
　　　　　　　　　　 ▼
　　　　　　　　　「手順4」
```

### @plantuml
[plantuml](https://plantuml.com/ja/)の書式でUML図を書く。  
SVGがHTMLにインラインで埋め込まれる。  
plantumlのオープンサーバを使うのでインターネット接続できる必要がある。

````markdown
```@plantuml
@startuml
Alice->Bob : Hello
return ok
@enduml
```
````

### @raw
コードブロック内の文字列がそのまま展開される。  
下の例だと記述したHTMLがそのまま書かれる。
````
```@raw
<div style="border: solid black 1px;">
  <p>これは<br><span style="color:red;">そのまま</span><br>書き出される</p>
</div>
```
````

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