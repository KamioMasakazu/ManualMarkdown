#!/usr/bin/env python3
# MIT License
#
# Copyright (c) 2026-05-30 神尾政和
#
# Permission is hereby granted, free of charge, to any person obtaining a copy 
# of this software and associated documentation files (the "Software"), to deal 
# in the Software without restriction, including without limitation the rights 
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell 
# copies of the Software, and to permit persons to whom the Software is 
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in 
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR 
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, 
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE 
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER 
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, 
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE 
# SOFTWARE.
from __future__ import annotations
from typing import Self, Iterable, Optional, Sequence
import argparse
import os
import io
import base64
import csv
import html
from html.parser import HTMLParser
import mimetypes
import re
import sys
import shutil
import zlib
import urllib.request
import urllib.parse
from collections import deque
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
from pprint import pprint, pformat
import logging

###############################################################################
# ロガーとイニシャライザ
###############################################################################
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

def init_logger(logger: logging.Logger, debug: bool):
	""" ロガーのイニシャライザ
	Args:
		logger: ロガーオブジェクト
	"""
	logger = logging.getLogger(__name__)
	if debug: logger.setLevel(logging.DEBUG)
	else: logger.setLevel(logging.INFO)

	DBG_FMT = "%(name)s [%(levelname)s] %(message)s"
	stream_handler = logging.StreamHandler(sys.stderr)
	stream_handler.setFormatter(logging.Formatter(DBG_FMT))
	logger.addHandler(stream_handler)

###############################################################################
# 引数解析結果
###############################################################################
@dataclass(frozen=True)
class RenderOptions:
	"""レンダリング全体のオプション

	Attributes:
		input_path: 入力md
		output_path: 出力html（Noneならstdout）
		debug: デバッグモード
		head: <head>に指定したファイルを展開する
		bottom: </body>の直前に指定したファイルを展開する
		css_path: cssファイル/ディレクトリ（Noneならdefault.css）
		js_path: jsファイル/ディレクトリ（Noneなら無し）
		embed_image: 画像をdata URLで埋め込む
		no_format: HTMLをフォーマットしない
	"""

	input_path: Path
	output_path: Optional[Path]
	debug: bool
	head: Optional[str]
	bottom: Optional[list[str]]
	css_path: Optional[Path]
	js_path: Optional[Path]
	embed_image: bool
	no_format: bool

###############################################################################
# 行解析用
###############################################################################
# パースした行の種別
class LineType(Enum):
	CodeBlock = "CodeBlock" # コードブロック開始、終了行
	CustomCsvTable = "CustomCsvTable"	# CSVテーブルファイル指定
	Empty = "Empty"  # 空行
	Header = "Header"   # ヘッダ行
	Line = "Line"	# 横線
	List = "List"   # リスト行
	Plain = "Plain" # 通常のテキスト行
	Quotation = "QUotation"	# 引用行
	RawLine = "RawLine"  # trimも何もしないmarkdown中の行そのもの
	Table = "Table"	# テーブル

# パースした行のデータ
@dataclass(frozen=True)
class LineValue:
	"""行をチェックして分解した結果

	Attributes:
		type: 何の行か
		value: 分解した結果
	"""
	type: LineType
	value: any

# リストのデータ
@dataclass(frozen=True)
class ListData:
	type: str	# ulかolか
	lines: list[list[str] | Self]	# <li>から</li>の内容 

# テーブルのデータ
@dataclass(frozen=True)
class TableData:
	align: list[str]	# カラムごとのleft, center, right
	data: list[list[str]]

# 引用データ
@dataclass(frozen=True)
class QuotationData:
	data: list[str, Self]

#------------------------------------------------------------------------------
# フローデータ関連
#------------------------------------------------------------------------------
class Flow(Enum):
	Blank = "Blank"
	Node = "Node"
	Block = "@flow-block"
	StartParallel = "@start-parallel"
	EndParallel = "@end-parallel"

# フローデータ
@dataclass(frozen=True)
class FlowBox:
	data: list[FlowItem | FlowParallel]

# 平行フロー
@dataclass(frozen=True)
class FlowParallel:
	data: list[FlowBox]

# フローアイテム
@dataclass(frozen=True)
class FlowItem:
	data: list[str]


###############################################################################
# カスタムモード
###############################################################################
@dataclass(frozen=True)
class CustomMode:
	"""カスタムモードのパース結果

	Attributes:
		type: どの種類か
		option: オプション
		path: ファイルが指定されたときのパス
	"""
	type: str
	option: str
	title: str
	path: str

###############################################################################
# HTMLパーサ
###############################################################################
class HTMLFormatter(HTMLParser):
	VOID_ELEMENTS = {
		'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 
		'link', 'meta', 'param', 'source', 'track', 'wbr'
	}
	
	INLINE_TAGS = {
		'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'td', 'th', 'title', 'a'
	}

	# インデント整形はしないが、ブラウザ表示用にHTMLエスケープは必要なタグ
	NO_PARSE_TAG = {'pre'}

	# インデント整形もせず、HTMLエスケープも絶対に動かしてはいけないタグ
	NO_PARSE_NO_ESCAPE = {'svg', 'style', 'script'}

	def __init__(self):
		super().__init__()
		self.output = io.StringIO()
		self.indent_level = 0
		
		self.current_inline_tag = None
		self.inline_data = []

		self.no_parse = False	# タグの中身をインデントしないフラグ
		self.tag_stack = []

	# インデントを書く
	def _write_indent(self):
		if self.no_parse:
			return
		self.output.write("\t" * self.indent_level)

	# <!DOCTYPE ... > などの宣言を処理するメソッド
	def handle_decl(self, decl):
		self.output.write(f"<!{decl}>\n")

	def handle_starttag(self, tag, attrs):
		self.tag_stack.append(tag)

		if self.no_parse:
			attr_str = "".join([f' {k}="{v}"' for k, v in attrs if v is not None])
			self.output.write(f"<{tag}{attr_str}>")
			return

		if self.current_inline_tag:
			attr_str = "".join([f' {k}="{v}"' for k, v in attrs if v is not None])
			self.inline_data.append(f"<{tag}{attr_str}>")
			if tag not in self.VOID_ELEMENTS:
				self.indent_level += 1
			return

		if tag in self.INLINE_TAGS:
			self._write_indent()
			self.current_inline_tag = tag
			attr_str = "".join([f' {k}="{v}"' for k, v in attrs if v is not None])
			self.inline_data.append(f"<{tag}{attr_str}>")
			return
		
		# どちらかの抑止タグ群にヒットしたら no_parse モードに入る
		if tag in self.NO_PARSE_TAG or tag in self.NO_PARSE_NO_ESCAPE:
			self._write_indent()
			attr_str = "".join([f' {k}="{v}"' for k, v in attrs if v is not None])
			self.output.write(f"<{tag}{attr_str}>")
			self.no_parse = True
			if tag not in self.VOID_ELEMENTS:
				self.indent_level += 1
			return

		self._write_indent()
		attr_str = "".join([f' {k}="{v}"' for k, v in attrs if v is not None])
		self.output.write(f"<{tag}{attr_str}>\n")
		if tag not in self.VOID_ELEMENTS:
			self.indent_level += 1


	def handle_endtag(self, tag):
		if self.tag_stack and self.tag_stack[-1] == tag:
			self.tag_stack.pop()

		# 抑止モードの解除判定
		if tag in self.NO_PARSE_TAG or tag in self.NO_PARSE_NO_ESCAPE:
			self.no_parse = False
			if tag not in self.VOID_ELEMENTS:
				self.indent_level -= 1
			self._write_indent()
			self.output.write(f"</{tag}>\n")
			return

		if self.no_parse:
			self.output.write(f"</{tag}>")
			return

		if self.current_inline_tag:
			if tag == self.current_inline_tag:
				self.inline_data.append(f"</{tag}>")
				self.output.write("".join(self.inline_data) + "\n")
				self.current_inline_tag = None
				self.inline_data = []
			else:
				if tag not in self.VOID_ELEMENTS:
					self.indent_level -= 1
				self.inline_data.append(f"</{tag}>")
			return

		if tag in self.VOID_ELEMENTS:
			return
		self.indent_level -= 1
		self._write_indent()
		self.output.write(f"</{tag}>\n")

	def handle_data(self, data):
		# 整形抑止中の処理
		if self.no_parse:
			current_tag = self.tag_stack[-1] if self.tag_stack else None

			# 定義を分けたおかげで、判定条件が驚くほど直感的になりました
			if current_tag in self.NO_PARSE_TAG:
				self.output.write(html.escape(data))
			else:
				self.output.write(data)
			return

		# 通常テキストの処理
		cleaned_data = data.strip()
		if cleaned_data:
			escaped_data = html.escape(cleaned_data)

			if self.current_inline_tag:
				self.inline_data.append(escaped_data)
			else:
				self._write_indent()
				self.output.write(f"{escaped_data}\n")

	def get_result(self):
		return self.output.getvalue()

###############################################################################
# パーサ本体
###############################################################################
class MdParser:
	options: RenderOptions	#オプション
	title: str	# htmlの<title>
	in_header = False	# <header>を書き始めてから閉じるまで（Trueは書いてる間）
	header_stack: list[int]	# section階層の追跡用
	in_code_block: bool	# コードブロックの処理中はTrue
	code_block_str: str|None    # ```か````か
	copy_files: list[str]	# 出力用にコピーするファイルのリスト

	def __init__(self, options: RenderOptions):
		self.options = options
		self.title = "no title"
		self.header_stack = []
		self.in_code_block = False
		self.code_block_str = None
		self.copy_files = []

	#==========================================================================
	# パース処理関連
	#==========================================================================
	# ヘッダ行か？
	def _is_header_line(self, lines: deque[str]) -> LineValue | None:
		logger.debug("start: MdParser::_is_header_line()")
		line = lines[0]
		line = line.strip()
		m = re.match(r"(#{1,7})\s+(.+)", line)
		if not m:
			return None
		lines.popleft()

		# h1レベルだったらタイトルを同じに設定する
		if len(m.group(1)) == 1:
			self.title = m.group(2)

		logger.debug("end: MdParser::_is_header_line()")
		return LineValue(
			type = LineType.Header,
			value = {
				"level": len(m.group(1)),
				"str": m.group(2),
			}
		)

	# @csv行か
	def _is_csv_table(self, lines: deque[str]) -> LineValue | None:
		logger.debug("start: MdParser::_is_csv_table()")
		line = lines[0]
		line = line.strip()
		m = re.match(r"^@csv(?::.+)?\((.+?)\)$", line)
		if not m:
			return None
		lines.popleft()
		logger.debug("end: MdParser::_is_csv_table()")
		return LineValue (
			type = LineType.CustomCsvTable,
			value = line
		)

	# 横線か
	def _is_line(self, lines:deque[str]) -> LineValue | None:
		logger.debug("start: MdParser::_is_line()")
		line = lines[0]
		m = re.match(r"^[ ]{0,3}(?:(?:-[ ]*){3,}|(?:\*[ ]*){3,}|(?:_[ ]*){3,})$", line)
		if not m:
			return None
		lines.popleft()

		line_break = "solid"
		if " " in line: line_break = "break"

		line_char = "-"
		if "_" in line: line_char = "under"
		elif "*" in line: line_char = "asta"
		else: line_char = "minus"

		logger.debug("start: MdParser::_is_line()")
		return LineValue(
			type = LineType.Line,
			value = f"{line_break}-{line_char}"
		)

	# @flowboxの解析
	def _parse_flow_box(self, lines) -> FlowBox:
		if not isinstance(lines, deque):
			lines = deque(lines)

		ret = []
		current = []	# 現在処理中のノード
		parallel = None	# 平行手順ノード
		while len(lines) != 0:
			line = lines.popleft().strip()
			if line == Flow.StartParallel.value:
				# 平行手順開始
				parallel = FlowParallel([])
				stack_count = 1	# @start-parallelの入れ子管理用。0になったら終わり。
				plines = deque()	# 再起呼び出しのため平行手順の行を集める

				while len(lines) != 0:
					pline = lines.popleft().strip()

					if pline == Flow.StartParallel.value:
						stack_count += 1
					elif pline == Flow.EndParallel.value:
						stack_count -= 1
					elif pline == Flow.Block.value:
						# 次の@flow-blockに到達したら再起的に解析してデータを追加、行データをクリア
						decoded = self._parse_flow_box(plines)
						parallel.data.append(decoded)
						plines = deque()

					if stack_count == 0:
						# 平行手順の終端に達した
						break
					else:
						plines.append(pline)

				# ブロック終端に達した時データがあったら処理する
				if len(plines) != 0:
					decoded = self._parse_flow_box(plines)
					parallel.data.append(decoded)
				
				current.append(parallel)
				parallel = None
			elif len(line) == 0 or line == Flow.Block.value:
				# 空白行か@start-parallel〜@end-parallelの間にない@flow-blockはノードの終了
				if len(current) != 0:
					if all(isinstance(item, str) for item in current):
						ret.append(FlowItem(current))
					else:
						ret.append(current)
				current = []
			else:
				# 普通の文字列
				current.append(line)

		# 終端に達した時データがあったら結果に加える
		if len(current) != 0:
			if all(isinstance(item, str) for item in current):
				ret.append(FlowItem(current))
			else:
				ret.append(current)

		return FlowBox(ret)


	# コードブロック終わりか？ 終わりでなければ生の文字列を返す
	def _is_code_block_end(self, line: str) -> LineValue | None:
		logger.debug("start: MdParser::_is_code_block_end()")
		if not self.in_code_block:
			return None

		if line.strip() == self.code_block_str:
			self.code_block_str = None
			return  LineValue(
				type = LineType.CodeBlock,
				value = ""
			)

		logger.debug("end: MdParser::_is_code_block_end()")
		return LineValue(
			type = LineType.RawLine,
			value = line
		)

	# コードブロックの開始から終了までを処理する
	def _is_code_block(self, lines: deque[str]) -> LineValue | None:
		logger.debug("start: MdParser::_is_code_block()")
		line = lines[0]
		m = re.match(r"(`{3,4})(.*)", line)
		if not m:
			return None

		# コードブロックが始まった
		self.in_code_block = True
		self.code_block_str = m.group(1)
		mode = m.group(2)

		# コードブロックの始まりを捨てる
		lines.popleft()

		in_block = []   # コードブロック内の行
		while True:
			# コードブロックが閉じられないままファイルが終わった
			if len(lines) == 0:
				break

			line = lines.popleft()
			checked = self._is_code_block_end(line)
			if checked.type == LineType.CodeBlock:
				break
			elif checked.type == LineType.RawLine:
				in_block.append(checked.value)

		self.code_block_str =None
		self.in_code_block = False

		# 追加の解析が必要な場合
		mode_name = mode.split(":")[0]	# オプションで分割して名前だけ取り出す
		HANDLE = {
			"@flowbox": self._parse_flow_box
		}

		if mode_name in HANDLE:
			handler = HANDLE[mode_name]
			in_block = handler(in_block)

		logger.debug("end: MdParser::_is_code_block()")
		return LineValue(
			type = LineType.CodeBlock,
			value = {
				"mode": mode,
				"lines": in_block
			}
		)

	# リスト行処理のヘルパ関数
	def _parse_lines(self, list_lines: deque[str], current_indent: int = 0) -> ListData:
		"""
		インデント深さを考慮しながら、再帰的にリストをパースするヘルパ関数。
		"""
		list_type = None
		structured_lines = [] # ListData.lines に格納する要素のリスト
		current_li: list[str] = [] # 現在処理中の <li> のテキスト行をためるリスト

		while list_lines:
			next_line = list_lines[0]
			
			# 1. 現在の行のインデントの深さを計算（先頭のスペースの数）
			indent_len = len(next_line) - len(next_line.lstrip())
			
			# 【重要】もし次の行のインデントが、この関数の担当（current_indent）より浅くなったら、
			# 自分の持ち場は終わりなので、現在の処理を切り上げて親に制御を戻す。
			if indent_len < current_indent and next_line.strip():
				break
				
			# チェックが終わったので正式に1行取り出す
			line = list_lines.popleft()
			stripped_line = line.strip()
			
			# 2. リストマーカーの判定
			is_ul = stripped_line.startswith("- ")
			ol_match = re.match(r'^(\d+)\.\s', stripped_line)
			is_ol = bool(ol_match)
			
			# この階層（ListData）のリストタイプ（ul / ol）を初回行で決定
			if list_type is None:
				list_type = "ul" if is_ul else "ol"

			if (is_ul or is_ol) and indent_len == current_indent:
				# ◆ ルール1: 今の階層と同じ深さで新しいマーカーが始まった場合
				# 新しい <li> の開始なので、直前まで育てていた <li> があれば登録
				if current_li:
					structured_lines.append(current_li)
				
				# 新しい <li> の最初の1行を登録（マーカー部分は除去して中身だけに）
				content = stripped_line[2:] if is_ul else stripped_line[ol_match.end():]
				current_li = [content]
				
			elif (is_ul or is_ol) and indent_len > current_indent:
				# ◆ ルール3・4: インデントが深くなり、かつ新しいマーカーが始まった場合（子・孫要素）
				# まず、popleftしてしまった行を一旦先頭に戻す（子要素の関数に読ませるため）
				list_lines.appendleft(line)
				
				# 新しいインデント深さを指定して自分自身を再帰呼び出し
				child_list = self._parse_lines(list_lines, current_indent=indent_len)
				
				# 返ってきた子要素の ListData を、現在進行形の <li> (current_li) の中に格納する
				current_li.append(child_list)
				
			else:
				# ◆ ルール2: マーカーがなく、インデントされている（または同じ深さの）後続行
				# 現在の <li> の追加行としてテキストをそのまま追加
				current_li.append(stripped_line)

		# ループ終了後、最後に処理していた <li> を忘れずに登録
		if current_li:
			structured_lines.append(current_li)
			
		return ListData(type=list_type or "ul", lines=structured_lines)

	# リスト行を処理する
	def _is_list_lines(self, lines: deque[str]) -> LineValue | None:
		"""
		先頭行がリスト行か判定し、リストの終わりまでをパースして返す。
		"""
		logger.debug("start: MdParser::_is_list_lines()")
		if not lines:
			return None
		
		# 先頭行がリスト（- や 1. など）で始まっているかチェック
		first_line = lines[0]
		# タブをスペース4つに正規化した状態で判定
		first_line_norm = first_line.replace("\t", "    ")
		if not (first_line_norm.lstrip().startswith("- ") or re.match(r'^\s*\d+\.\s', first_line_norm)):
			return None
			
		# リストの終わり（空行）までを破壊的に取り出す
		list_lines = deque()
		while lines:
			# 次の行が空行（または空白のみの行）ならループを抜ける（空行自体はpopしない）
			if not lines[0].strip():
				break
				
			line = lines.popleft()
			# 仕様通り、この段階で行頭・行中のタブをスペース4つに置換して格納
			list_lines.append(line.replace("\t", "    "))
			
		# ヘルパ関数を呼び出して構造化データにする（初期インデントは0）
		list_data = self._parse_lines(list_lines, current_indent=0)

		logger.debug("end: MdParser::_is_list_lines()")
		return LineValue(
			type = LineType.List,
			value = list_data
		)

	# テーブルかどうか
	def _is_table(self, lines: deque[str]) -> LineValue | None:
		logger.debug("start: MdParser::_is_table()")

		# 少なくともヘッダ行と区切り行が必要
		if len(lines) < 2:
			return None
		
		# 先頭は|で始まっていること
		line1 = lines[0].strip()
		if len(line1) == 0 or line1[0] != "|":
			return None

		line2 = lines[1].strip()
		if len(line2) == 0 or line1[0] != "|":
			return None
		
		# 2行目のフォーマット確認
		if line2[-1] == "|": line2 = line2[1:-1]
		else: line2 = line2[1:]
		
		align = []
		ls2 = line2.split("|")
		for cell in ls2:
			if not re.match(r":?-+:?", cell):
				return None
			
			# ついでに位置指定を確定
			if cell[0] == ":" and cell[-1] == ":":
				align.append("center")
			elif cell[-1] == ":":
				align.append("right")
			else:
				align.append("left")

		# テーブルデータをパース
		data = []
		line_no = 0
		while True:
			# 最後に達した
			if len(lines) == 0:
				break

			l = lines[0].strip()

			# 2行めは区切り行なのでとばす
			if line_no == 1:
				lines.popleft()
				line_no += 1
				continue

			# テーブルではなくなった
			if len(l) == 0 or l[0] != "|":
				break

			if l[-1] == "|": l = l[1:-1]
			else: l = l[1:]

			s = [d.strip() for d in l.split("|")]
			data.append(s)
			lines.popleft()
			line_no += 1

		logger.debug("end: MdParser::_is_table()")
		return LineValue(
			type = LineType.Table,
			value = TableData(
				align = align,
				data = data
			)
		)

	# 引用行処理のヘルパ関数
	def _parse_quotation(self, lines: deque[str]) -> QuotationData:
		data = []
		while True:
			if len(lines) == 0:
				break

			l = lines[0].strip()

			if len(l) == 0 or l[0] != ">":	# このレベルのデータ
				data.append(lines.popleft().strip())
			else:	# 引用
				next_lines = deque()	# 再起呼び出しのために集める
				while True:
					if len(lines) == 0:
						break
					n = lines[0].strip()
					if n[0] == ">":
						next_lines.append(lines.popleft().strip()[1:].strip())
					else:
						break
				
				ret = self._parse_quotation(next_lines)
				data.append(ret)
		
		return QuotationData(
			data = data
		)

	# 引用か
	def _is_quotation(self, lines: deque[str]) -> LineValue| None:
		logger.debug("start: MdParser::_is_quotation()")
		quotation_lines = deque()

		while True:
			if len(lines) == 0:
				break

			line = lines[0].strip()
			if len(line) == 0:
				break
			elif line[0] == ">":
				# 先頭が">"なら除いて処理対象に追加
				quotation_lines.append(lines.popleft().strip()[1:].strip())
			else:
				break
		
		if len(quotation_lines) == 0:
			return None

		data = self._parse_quotation(quotation_lines)
		logger.debug("end: MdParser::_is_quotation()")
		return LineValue(
			type = LineType.Quotation,
			value = data
		)


	# 行種別チェックと値の分解
	def line_type(self, lines: deque[str]) -> LineValue:
		logger.debug("start: MdParser::line_type()")

		# ハンドラには優先順位がある
		HANDLER = [
			self._is_line,			# 横線
			self._is_code_block,	# コードブロック
			self._is_list_lines,	# リスト
			self._is_table,			# テーブル
			self._is_quotation,		# 引用
			self._is_csv_table,		# CSVテーブル
			self._is_header_line,	# ヘッダ行
		]

		for handle in HANDLER:
			ret = handle(lines)
			logger.debug(pformat(ret))
			if ret: return ret

		# ここに来たら通常行
		# 2個以上の空白文字だけの行は強制改行なので改行だけ取って残す
		line = lines.popleft().rstrip("\r\n")
		if not re.match(r"^\s{2,}$", line):
			line = line.strip()

		logger.debug("end: MdParser::line_type()")
		return LineValue(
			type = LineType.Plain,
			value = line
		)

	#==========================================================================
	# HTML化処理関連ユーティリティ関数
	#==========================================================================
	# mimeタイプを推測する
	def guess_mime_type(self, path: Path) -> str:
		mime, _ = mimetypes.guess_type(str(path))
		return mime or "application/octet-stream"
	
	# 画像ファイルを読み込んでbase64して返す
	def read_img_to_base64(self, path: Path) -> str:
		mime = self.guess_mime_type(path)
		data = base64.b64encode(path.read_bytes()).decode("ascii")
		return f"data:{mime};base64,{data}"

	# インラインの要素を置換する
	def _render_inline(self, text: str) -> str:
		text = html.escape(text)

		# プレースホルダーを管理する辞書
		placeholders = {}
		counter = 0

		# インラインコードを検出し、プレースホルダーに退避
		def stash_code(match):
			nonlocal counter
			code_content = match.group(2)
			placeholder = f"MARKDOWNCODEBLOCK{counter}XYZ"
			# コードは軽いのでそのままHTML化して保持
			placeholders[placeholder] = f"<code>{code_content}</code>"
			counter += 1
			return placeholder

		text = re.sub(r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)", stash_code, text)

		# 画像 (Image): ![alt](url)
		def stash_image(match):
			nonlocal counter
			alt_text = match.group(1)
			img_path_str = match.group(2)
			placeholder = f"MARKDOWNIMGTAG{counter}XYZ"
			
			# 【ここがポイント】ここではBase64変換せず、情報（タプル）だけを辞書に保存
			placeholders[placeholder] = ("image", alt_text, img_path_str)
			counter += 1
			return placeholder

		text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", stash_image, text)

		# リンク (Link): [text](url)
		def stash_link(match):
			nonlocal counter
			link_text = match.group(1)
			url = match.group(2)
			placeholder = f"MARKDOWNLINKTAG{counter}XYZ"
			
			# リンクも同様に情報だけを保存
			placeholders[placeholder] = ("link", link_text, url)
			counter += 1
			return placeholder

		text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", stash_link, text)

		# --------------------------------------------------
		# 軽いプレースホルダー文字列（XYZ）しか残っていないため、
		# 以下の正規表現処理は一瞬で終わります。
		# --------------------------------------------------

		# 取り消し線 (Strikethrough): ~~text~~
		text = re.sub(r"~~(.*?)~~", r"<del>\1</del>", text)

		# 太字 (Bold)&斜体(Italic): ***text*** または ___text}___
		text = re.sub(r"(\*\*\*|___)(.*?)\1", r"<strong><em>\2</em></strong>", text)

		# 太字 (Bold): **text** または __text__
		text = re.sub(r"(\*\*|__)(.*?)\1", r"<strong>\2</strong>", text)

		# 斜体 (Italic): *text* または _text_
		text = re.sub(r"(\*|_)(.*?)\1", r"<em>\2</em>", text)

		# --------------------------------------------------
		# 最後に1回だけ、置換と同時に「本当に必要な時だけ」Base64変換を行う
		# --------------------------------------------------
		for placeholder, data in placeholders.items():
			if isinstance(data, str):
				# インラインコード（すでにHTML文字列になっているもの）
				text = text.replace(placeholder, data)
			
			elif isinstance(data, tuple):
				tag_type = data[0]
				
				if tag_type == "image":
					_, alt_text, img_path_str = data
					if self.options.embed_image:
						try:
							# テキスト処理がすべて終わったこの瞬間に初めてファイルを読む
							src = self.options.input_path.parent / Path(img_path_str)	# Markdownの場所からの相対パスと判断
							src_value = self.read_img_to_base64(src)
						except Exception:
							logger.warning(f"img src={img_path_str} is not found.not encoded to base64 data.")
							src_value = img_path_str
					else:
						# html出力ディレクトリからの相対パスに直す。コピー用にもtのパスを保存。
						self.copy_files.append(img_path_str)
						src_value = f"./assets/{Path(img_path_str).name}"
					
					img_tag = f'<img src="{src_value}" alt="{alt_text}">'
					text = text.replace(placeholder, img_tag)
					
				elif tag_type == "link":
					_, link_text, url = data
					link_tag = f'<a href="{url}">{link_text}</a>'
					text = text.replace(placeholder, link_tag)

		return text

	#==========================================================================
	# HTML化標準書式（コードブロック以外）
	#==========================================================================
	# 通常行の追加
	# 空行は無視する
	# 2個以上の空白文字の行は<br>にして強制改行
	def _add_p(self, line: LineValue) -> str:
		if line.type != LineType.Plain:
			raise ValueError(f"value type is not {LineType.Plain}.")
		text = self._render_inline(line.value)

		if re.match(r"^\s{2,}$", text):
			text = "<br>"
		else:
			text = text.strip()	# 1個だけの空白文字に対処

		if not text:
			return ""
		return f"<p>{text}</p>\n"

	# hrを追加
	def _add_hr(self, line: LineValue) -> str:
		if line.type != LineType.Line:
			raise ValueError(f"value type is not {LineType.Line}.")
		return f'<hr class="{line.value}">\n'

	# ヘッダ行の追加
	def _add_h(self, line: LineValue) -> str:
		if line.type != LineType.Header:
			raise ValueError(f"value type is not {LineType.Header}.")

		h_lvl = line.value["level"]
		html = ""

		# 前のセクしションがあるかをチェックする。
		# ひとつ前のヘッダレベルをチェックして、引数のレベル以上なら必要なだけ閉じる
		# 引数のレベルの方が大きければ入れ子にする
		while True:
			if len(self.header_stack) == 0: break
			last_lvl = self.header_stack[-1]
			if h_lvl > last_lvl: break

			html += '</section>\n'
			self.header_stack.pop()

		# <header>を書いてる間に次の<section>に到達したら閉じる
		if self.in_header:
			html += '</header>\n'
			self.in_header = False

		if h_lvl == 1:
			html += '<header>\n'
			text = self._render_inline(line.value["str"])
			html += f'<h{h_lvl}>{text}</h{h_lvl}>\n'
			self.in_header = True
		else:
			# 新しいセクションを追加
			html += '<section>\n'
			text = self._render_inline(line.value["str"])
			html += f'<h{h_lvl}>{text}</h{h_lvl}>\n'
			self.header_stack.append(line.value["level"])

		return html

	# リスト記述のためのヘルパ
	def _add_list_helper(self, list_data: ListData) -> str:
		html = f'<{list_data.type}>\n'

		# list_data.lines の各要素は 1つの <li> 〜 </li> に対応する「リスト」
		for li_content in list_data.lines:
			html += '<li>\n'
			
			# <li> の中身（テキストや子ListData）を順番に処理
			for item in li_content:
				if isinstance(item, ListData):
					# 子ListDataを見つけたら再帰呼び出し（self. を忘れずに！）
					html += self._add_list_helper(item)
				else:
					# 通常のテキストであればそのまま出力
					text= self._render_inline(item)
					html += f'<div>{text}</div>\n'
			
			html += '</li>\n'
		html += f'</{list_data.type}>\n'
		return html

	# リストを記述
	def _add_list(self, checked: LineValue) -> str:
		if checked.type != LineType.List:
			raise ValueError(f"value type is not {LineType.List}.")

		return self._add_list_helper(checked.value)

	# テーブルを記述
	def _add_table(self, checked: LineValue) -> str:
		if checked.type != LineType.Table:
			raise ValueError(f"value type is not {LineType.Table}.")
		
		html = '<div class="table-wrap">\n'
		html += '<table>\n'
		# ヘッダ行
		html += '<thead>\n'
		html += '<tr>'
		for i, cell in enumerate(checked.value.data[0]):
				align = checked.value.align[i]
				html += f'<th class="{align}">{self._render_inline(cell)}</th>'
		html += '</tr>\n'
		html += '</thead>\n'

		# データ行
		html += '<tbody>\n'
		for row in checked.value.data[1:]:
			html += '<tr>'
			for i, cell in enumerate(row):
				align = checked.value.align[i]
				html += f'<td class="{align}">{self._render_inline(cell)}</td>'
			html += '</tr>\n'
		html += '</tbody>\n'
		html += '</table>\n'
		html += '</div>\n'
		return html

	# blockquoteを記述するためのヘルパ
	def _add_blockquote_helper(self, data: QuotationData) -> str:
		html = '<blockquote>\n'
		for d in data.data:
			if isinstance(d, QuotationData):
				html += self._add_blockquote_helper(d)
			else:
				html += f'<div>{self._render_inline(d)}</div>\n'
		html += '</blockquote>\n'
		return html

	# Blockquoteを記述
	def _add_blockquote(self, checked: LineValue) -> str:
		if checked.type != LineType.Quotation:
			raise ValueError(f"value type is not {LineType.Quotation}.")
		
		return self._add_blockquote_helper(checked.value)

	#==========================================================================
	# 拡張書式とコードブロック
	#==========================================================================
	# オプションをタグのclassにした文字列を返す。
	# tagは<と>をつけない。
	# lf = Falseにしたら最後に改行をつけない
	def _optioned_tag(self, tag: str, opts: list[str], *, lf=True) -> str:
		classed = ""
		if opts:
			classed = f"""<{tag} class="{' '.join(opts)}">"""
		else:
			classed = f"<{tag}>"

		if lf: classed + "\n"
		return classed

	# コードブロック内をそのまま書き出す
	def _add_custom_raw(self, mode: CustomMode, lines: list[str]) -> str:
		return "".join(lines)

	# titleのないboxの中身
	def _add_simple_box_inner(self, mode: CustomMode, lines: list[str]) -> str:
		html = ""

		for l in lines:
			l = l.strip()
			l = self._render_inline(l)
			html += f'<p>{l}</p>\n'

		return html

	# titleのあるboxの中身
	def _add_title_box_inner(self, mode: CustomMode, lines: list[str]) -> str:
		html = ""
		title = self._render_inline(mode.title)
		html += f'<div>{title}</div>\n'
		html += '<div>\n'
		for l in lines:
			l = l.strip()
			l = self._render_inline(l)
			html += f'<p>{l}</p>\n'
		html += '</div>\n'

		return html

	# boxカスタムモードでhtmlを書く
	def _add_custom_box(self, mode: CustomMode, lines: list[str]) -> str:
		if mode.type != "box":
			raise ValueError(f"custom mode is not box.")

		# 一番外の<div>
		html = self._optioned_tag('div', mode.option)

		if mode.title:
			html += self._add_title_box_inner(mode, lines)
		else:
			html += self._add_simple_box_inner(mode, lines)

		html += '</div>'
		return html

	# cvsテーブルカスタムモードでhtmlを書く
	def _add_custom_csv_table(self, mode: CustomMode, csv_data: list[str] | str) -> str:
		if mode.type != "csv":
			raise ValueError(f"cudtom mode is not csv.")

		data = ""
		if isinstance(csv_data, list):
			data = "".join(csv_data)
		elif isinstance(csv_data, str):
			data = csv_data
		else:
			raise TypeError(csv_data)
		
		filed = io.StringIO(data)
		reader = csv.reader(filed)

		# th_row, th_col以外のオプション
		classes = [opt for opt in mode.option if not opt in ["th_row", "th_col"]]

		html = '<div class="table-wrap">'
		html += self._optioned_tag('table', classes)
		for i, row in enumerate(reader):
			html += '<tr>\n'
			for j, cell in enumerate(row):
				cell = self._render_inline(cell)
				if "th_row" in mode.option and i == 0:
					html += f'<th>{cell}</th>\n'
				elif "th_col" in mode.option and j == 0:
					html += f'<th>{cell}</th>\n'
				else:
					html += f'<td>{cell}</td>\n'
			html += '</tr>\n'
		html += '</table>\n'
		html += '</div>\n'

		return html

	# 手順BOXのhtml書き出し
	def _add_flow_box(self, flow_data: FlowBox | FlowParallel | FlowItem | list) -> str:
		ARROW = '<div class="flow-next">▼</div>\n'
		html_str = ""

		if isinstance(flow_data, FlowBox):
			html_str += '<div class="flow-box">\n'
			# 各要素（FlowItem や FlowParallel）の間にのみ ▼ を挟む
			for i, d in enumerate(flow_data.data):
				html_str += self._add_flow_box(d)
				if i < len(flow_data.data) - 1:
					html_str += ARROW
			html_str += '</div>\n'

		elif isinstance(flow_data, FlowParallel):
			html_str += '<div class="flow-parallel">\n'
			for d in flow_data.data:
				html_str += self._add_flow_box(d)
			html_str += '</div>\n'

		elif isinstance(flow_data, FlowItem):
			html_str += '<div class="flow-item">\n'
			for d in flow_data.data:
				text = self._render_inline(d)
				html_str += f'\t<div>{text}</div>\n'
			html_str += '</div>\n'

		elif isinstance(flow_data, list):
			for i, d in enumerate(flow_data):
				html_str += self._add_flow_box(d)
				if i < len(flow_data) - 1:
					html_str += ARROW

		return html_str

	# 手順BOXのhtml書き出し
	def _add_flow_box_main(self, mode: CustomMode, flow_data: FlowBox | FlowParallel | FlowItem | list) -> str:
		html_str = self._optioned_tag('div', mode.option)
		html_str += self._add_flow_box(flow_data)
		html_str += '</div>\n'
		return html_str

	# pluntumlに送るためテキストをエンコードする
	def _encode_plantuml(self, text: str) -> str:
		# 1. UTF-8でバイト列に変換
		utf8_bytes = text.encode('utf-8')
		
		# 2. Deflate圧縮 (Zlibヘッダとチェックサムを省くため、wbits=-zlib.MAX_WBITSを指定)
		compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
		compressed_bytes = compressor.compress(utf8_bytes) + compressor.flush()
		
		# 3. 標準のBase64で一旦エンコード
		b64_bytes = base64.b64encode(compressed_bytes)
		
		# 4. PlantUML独自のBase64アルファベットへマッピング
		# (標準の 'A-Z', 'a-z', '0-9', '+', '/' を PlantUML独自の順序に置換)
		std_alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
		puml_alphabet = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"
		
		mapping = bytes.maketrans(std_alphabet, puml_alphabet)
		puml_bytes = b64_bytes.translate(mapping)
		
		return puml_bytes.decode('utf-8')

	# plantuml openserverにリクエストしてSVGを取得する
	def _add_svg_from_plantuml_openserver(self, mode: CustomMode, lines: list[str]) -> str:
		if len(lines) == 0:
			return ""
		text = "".join(lines)
		encoded = self._encode_plantuml(text)
		url = f"https://www.plantuml.com/plantuml/svg/{encoded}"
		# User-Agent をヘッダーに追加してBot判定を回避する
		headers = {
			'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
		}

		req = urllib.request.Request(url, headers = headers)
		try:
			with urllib.request.urlopen(req) as res:
				body = res.read().decode("utf-8")
				html_str = self._optioned_tag('div', mode.option, lf=False)
				html_str += f'{body}'
				html_str += '</div>\n'
				return html_str

		except urllib.error.HTTPError as e:
			logger.error(repr(e))
			return f'<div><p>error response form https://www.plantuml.com/, {e}</p></div>\n'

	# kroki.ioにリクエストしてSVGを取得する
	def _add_svg_from_kroki(self, mode: CustomMode, lines: list[str]) -> str:
		if len(lines) == 0:
			return ""
		text = "".join(lines)

		# 1. 圧縮してBase64エンコード
		compressed = zlib.compress(text.encode("utf-8"), 9)
		# 2. urlsafe_b64encode の結果（bytes）をデコードして文字列にし、末尾の '=' を削除
		encoded = (
			base64.urlsafe_b64encode(compressed).decode("utf-8").replace("=", "")
		)

		headers = {
			'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
		}
		url = f"https://kroki.io/{mode.path}/svg/{encoded}"

		req = urllib.request.Request(url, headers=headers)
		try:
			with urllib.request.urlopen(req) as res:
				body = res.read().decode("utf-8")
				html_str = self._optioned_tag('div', mode.option, lf=False)
				html_str += f'{body}'
				html_str += '</div>\n'
				return html_str

		except urllib.error.HTTPError as e:
			logger.error(repr(e))
			return f"<div><p>error response from https://kroki.io/, {e}</p></div>\n"
	
	# カスタムモードをパースする
	# @mode:option(path)の形式
	def _check_custom_mode(self, mode: str) -> CustomMode:
		path = None
		title = None
		option = None

		if len(mode) == 0 or mode[0] != "@":
			return None

		if "(" in mode and ")" in mode:
			m = re.match(r"(.+)\((.*)\)", mode)
			mode = m.group(1)
			path = m.group(2)
		
		if "[" in mode and "]"in mode:
			m = re.match(r"(.+)\[(.*)\]", mode)
			mode = m.group(1)
			title = m.group(2)
		
		if ":" in mode:
			mode, opt_str = mode.split(":", maxsplit=1)
			option = opt_str.split("&")
		
		return CustomMode(
			type = mode[1:],
			option = option,
			title = title,
			path = path,
		)

	# コードブロックを記述
	def _add_code_block(self, checked: LineValue) -> str:
		if checked.type != LineType.CodeBlock:
			raise ValueError(f"value type is not {LineType.CodeBlock}.")

		# カスタムモード
		mode = checked.value["mode"]
		custom_mode = self._check_custom_mode(mode)

		# コードブロック内の文字列のリスト
		lines = checked.value["lines"]

		html_str = ""
		if custom_mode != None:
			HANDLER = {
				"box": self._add_custom_box,
				"csv": self._add_custom_csv_table,
				"flowbox": self._add_flow_box_main,
				"kroki": self._add_svg_from_kroki,
				"plantuml": self._add_svg_from_plantuml_openserver,
				"raw": self._add_custom_raw,
			}
			if custom_mode.type in HANDLER:
				handle = HANDLER[custom_mode.type]
				html_str = handle(custom_mode, lines)
			else:
				logger.error(f"unknown custome mode '{custom_mode.type}'.")
		else:
			# 通常のコードブロック
			html_str += f'<code class="{mode}">\n' if mode else '<code>\n'
			html_str += '<pre>\n'
			for l in lines:
				html_str += html.escape(l)
			html_str += '</pre>'
			html_str += '</code>\n'
		
		return html_str

	# ファイル読み込み指定のCSVテーブル
	def _add_csv_table(self, checked: LineValue) -> str:
		if checked.type != LineType.CustomCsvTable:
			raise ValueError(f"value type is not {LineType.CustomCsvTable}.")
		
		html = ""
		custom_mode = self._check_custom_mode(checked.value)

		# ファイルの存在確認
		dir = self.options.input_path.parent
		fname = Path(custom_mode.path)
		csv = None
		if (dir/fname).exists():
			# input_fileのディレクトリからの相対パスか？
			csv = (dir/fname)
		else:
			# input_fileの相対パスでなかったらマークダウンの記述のパス
			csv = fname

		if not csv.exists():
			logger.warning(f"{csv} is not exist.")
			return html

		with open(csv, "r", encoding="utf-8") as f:
			csv = f.read()
			html = self._add_custom_csv_table(custom_mode, csv)

		return html

	#==========================================================================
	# マークダウンパース関連のHTML化処理全体
	#==========================================================================
	# htmlの追記と構造スタック操作
	def add_html(self, checked: LineValue) -> str:
		HANDLER = {
			LineType.Plain: self._add_p,
			LineType.Header: self._add_h,
			LineType.CodeBlock: self._add_code_block,
			LineType.List: self._add_list,
			LineType.Table: self._add_table,
			LineType.Quotation: self._add_blockquote,
			LineType.CustomCsvTable: self._add_csv_table,
			LineType.Line: self._add_hr,
		}

		if checked.type in HANDLER:
			handler = HANDLER[checked.type]
			return handler(checked)
		else:
			logger.info(f"Unknown checked type {checked.type}.")
			return ""

	# 最後に閉じていないタグを閉じる
	def finish_html(self) -> str:
		html = ""
		# sectionのクローズ
		while len(self.header_stack) != 0:
			hdr = self.header_stack.pop()
			html += f"</section>\n"

		return html

	# マークダウンパーサ
	def parse(self) -> str:
		with open(self.options.input_path, "r", encoding="utf-8") as f:
			lines = deque(f.readlines())

		html = ""
		while len(lines) != 0:
			# logger.debug("-----lines-----\n" + pformat(lines))
			checked = self.line_type(lines)
			# pprint(checked)
			html += self.add_html(checked)

		html += self.finish_html()
		return html

	#==========================================================================
	# マークダウンパース以外のHTML化処理
	#==========================================================================
	# --head、--bottomのファイルを展開する
	def expand_file_data(self, files: list[str] | None) -> str:
		if not files:
			return ""
		
		ret = ""
		for f in files:
			target = Path(f)
			if not target.exists():
				logger.warning(f'head:{target} no such file or directory.')
				continue

			if not target.is_file():
				logger(f"head:{target} is not file.")
				continue

			with open(target, "r", encoding="utf-8") as f:
				ret += f.read()

		return ret

	# CSSを読み込んで展開する
	def get_css(self):
		css_path = self.options.css_path
		ret = None

		if not css_path.exists():
			logger.warning(f'css:{css_path} no such file or directory.')

		if css_path.is_file():
			with open(css_path, "r", encoding='utf-8') as f:
				ret = '<style>\n'
				ret += f.read()
				ret += '</style>\n'
		elif css_path.is_dir():
			ret = ""
			for css in css_path.glob('*.css'):
				with open(css, "r", encoding="utf8") as f:
					ret += '<style>\n'
					ret += f.read()
					ret += '</style>\n'
		return ret

	# JSを読み込んで展開する
	def get_js(self):
		js_path = self.options.js_path
		ret = None

		if not js_path:
			return ret

		if not js_path.exists():
			logger.warning(f'javascript:{js_path} no such file or directory.')

		if js_path.is_file():
			with open(js_path, "r", encoding='utf-8') as f:
				ret = '<script>\n'
				ret += f.read()
				ret += '</script>\n'
		elif js_path.is_dir():
			ret = ""
			for css in js_path.glob('*.js'):
				with open(css, "r", encoding="utf8") as f:
					ret += '<script>\n'
					ret += f.read()
					ret += '</script>\n'
		return ret

	# HTMLの先頭部分
	def html_top(self) -> str:
		html = '<!DOCTYPE html>\n'
		html += '<html>\n'
		html += '<head>\n'
		html += '<meta charset="utf-8">\n'
		html += '<meta name="viewport" content="width=device-width">\n'
		html += f'<title>{self.title}</title>\n'
		html += self.expand_file_data(self.options.head)
		styles = self.get_css()
		if styles:
			html += styles
		scripts = self.get_js()
		if scripts:
			html += scripts
		html += '</head>\n'
		html += '<body>\n'
		html += '<article>\n'
		return html

	# HTMLの終わりの部分
	def html_bottom(self) -> str:
		html = '</article>\n'
		html += self.expand_file_data(self.options.bottom)
		html += '</body>\n'
		html += '</html>\n'
		return html

	#==========================================================================
	# 全体的な処理
	#==========================================================================
	def _output_html(self, html:str):
		dir = None
		fname = None

		# ディレクトリかファイルかの判断
		if self.options.output_path.exists():
			if self.options.output_path.is_dir():
				# パスが存在してディレクトリの時
				dir = self.options.output_path
				fname = self.options.input_path.stem + ".html"
			elif self.options.output_path.is_file():
				# パスが存在してファイルの時
				dir = str(self.options.output_path.parent)
				fname = self.options.output_path.name
		else:
			if self.options.output_path.suffix:
				# パスが存在せずファイル名に拡張子がある時はファイル
				dir = str(self.options.output_path.parent)
				fname = self.options.output_path.name
			else:
				# パスが存在せずファイル名に拡張子がない時はディレクトリ
				dir = self.options.output_path
				fname = self.options.input_path.stem + ".html"

		# ディレクトリがなかったら作る
		if not dir.exists():
			os.makedirs(dir, exist_ok=True)

		# html出力
		with open(dir / fname, mode="w", encoding="utf-8") as f:
			f.write(html)

		# 部品ファイルのコピー
		assets = dir / "assets"
		assets.mkdir(755, exist_ok = True)
		for cp_f in self.copy_files:
			src = self.options.input_path.parent / Path(cp_f)	# コピー元（Markdownの場所からの相対パスと判断）
			if not src.exists():
				logger.warning(f"{src} is not exist to copy assets.")
				continue

			dst = assets / src.name	# コピー先
			if dst.exists():
				logger.warning(f"{dst} is already exist. not copied.")
				continue
			shutil.copy(src, dst)

	# 出力形式判定
	def output(self, html: str):
		if not self.options.output_path:
			print(html)
		else:
			dir = self.options.output_path.parent
			self._output_html(html)

	# 全体を実行
	def run(self):
		article = self.parse()	# 先にやらないとtitleが入らない
		top = self.html_top()
		bottom = self.html_bottom()
		html = top + article + bottom

		# 整形する
		if not self.options.no_format:
			html_parser = HTMLFormatter()
			html_parser.feed(html)
			html = html_parser.get_result()

		self.output(html)

###############################################################################
# 引数解析
###############################################################################
def parse_args() -> RenderOptions:
	parser = argparse.ArgumentParser(
		prog="ManualMarkdown.py",
		description="Markdown to HTML for Manual writing.",
		formatter_class=argparse.RawTextHelpFormatter,
		epilog = """
マニュアル用のマークダウンをHTMLに変換する。

（注意）
・コードブロック以外はHTMLエスケープを行う。
・空行を置きたい場合は2以上の空白文字だけの行を書く。
・画像その他でマークダウンに指定したパスはinputファイルのディレクトリからの相対パスとして処理する。
・1つのHTMLだけで配布可能な様にcssやjsは埋め込む。画像埋め込みはオプション。
・@csv、@csv```...```、@box```...```拡張書式を持つ。
・改行までを段落として扱う。改行のために文末にスペース2個は不要。
・ヘッダ行毎に階層化された<section>になる。
"""
	)
	parser.add_argument("input", help="入力となるマークダウンファイル。")
	parser.add_argument("-d", "--debug", action="store_true", help="Debug mode.")
	parser.add_argument("-o", "--output", default=None, help="HTML出力先ファイル名かディレクトリ。")
	parser.add_argument("--head", type=str, nargs="*", help="<head>にファイルの内容をそのまま転記する。jsやcssを外部参照する用。")
	parser.add_argument("--bottom", type=str, nargs="*", help="</body>の直前にファイルの内容を展開する。")
	parser.add_argument("--css", default="./default.css", help="cssファイル/ディレクトリ。<head>の<link>タグ内に展開する。")
	parser.add_argument("--js", default=None, help="jsファイル/ディレクトリ。<head>の<script>タグ内に展開する。")
	parser.add_argument("--embed-image", action="store_true", help='画像ファイルをimgタグのsrc属性に"data:..."で埋め込む')
	parser.add_argument("--no-format", action="store_true", help="HTMLをフォーマットしない。")

	ns = parser.parse_args()

	input_path = Path(ns.input)
	output_path = Path(ns.output) if ns.output else None
	css_path = Path(ns.css) if ns.css else None
	js_path = Path(ns.js) if ns.js else None

	if not input_path.exists():
		print(f"{input_path} is not exist.", file=sys.stderr)
		sys.exit(1)

	return RenderOptions(
		input_path=input_path,
		output_path=output_path,
		debug = ns.debug,
		head = ns.head,
		bottom = ns.bottom,
		css_path=css_path,
		js_path=js_path,
		embed_image=bool(ns.embed_image),
		no_format = bool(ns.no_format)
	)

###############################################################################
# main
###############################################################################
def main():
	global logger
	options = parse_args()
	init_logger(logger, options.debug)
	md_parser = MdParser(options)
	md_parser.run()
	sys.exit(0)


if __name__ == "__main__":
	main()