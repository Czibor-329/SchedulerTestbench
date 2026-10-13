"""从 PDF 中抽取纯文本的临时脚本。

本脚本仅用于把外部 PDF 论文转成文本以便阅读对比，不参与调度算法流程。
实现方式：直接解析 PDF 对象流，使用 zlib 解压 FlateDecode 内容流，再从内容
流中提取 Tj/TJ 操作符里的字符串字面量，按 TJ 数组中的字距调整值判定词间空格，
并按 Tm/Td/TD/T* 的换行语义还原阅读顺序。它不依赖任何第三方 PDF 库，只适用于
以文本对象存放正文的常规学术论文 PDF。
"""

from __future__ import annotations

import re
import sys
import zlib
from pathlib import Path

# 匹配 PDF 内容流中的字符串字面量：(...)
LITERAL_PATTERN = re.compile(rb"\((?:\\.|[^\\()])*\)")
# 匹配 TJ 数组整体：[ ... ]TJ
ARRAY_PATTERN = re.compile(rb"\[((?:[^\[\]\\]|\\.)*)\]\s*TJ")
# 匹配 PDF 字符串中的转义序列
ESCAPE_PATTERN = re.compile(rb"\\([nrtbf()\\]|[0-7]{1,3})")
# 匹配换行定位操作符
NEWLINE_OPERATOR_PATTERN = re.compile(rb"(T\*|TD|Td|Tm|ET)")
# 字距调整值小于该阈值时视为一个词间空格（单位：千分之一 em）
WORD_SPACE_THRESHOLD = -200.0


def decode_pdf_literal(raw: bytes) -> str:
    """把 PDF 字符串字面量（含括号）解码成可读文本。

    参数:
        raw: 形如 ``(Hello)`` 的原始字节，含两侧括号。

    返回:
        解码后的字符串；无法映射的字节按 latin-1 回退，保证不抛异常。
    """

    body = raw[1:-1]
    output: list[str] = []
    index = 0
    while index < len(body):
        character = body[index]
        if character == 0x5C:  # 反斜杠转义
            match = ESCAPE_PATTERN.match(body, index)
            if match:
                token = match.group(1)
                if token[:1].isdigit():
                    output.append(chr(int(token, 8)))
                else:
                    output.append(
                        {
                            b"n": "\n",
                            b"r": "\n",
                            b"t": "\t",
                            b"b": "",
                            b"f": "",
                            b"(": "(",
                            b")": ")",
                            b"\\": "\\",
                        }.get(token, "")
                    )
                index = match.end()
                continue
            index += 1
            continue
        output.append(bytes([character]).decode("latin-1"))
        index += 1
    return "".join(output)


def decode_tj_array(body: bytes) -> str:
    """解码一个 TJ 数组的内部字节，按字距调整还原空格。

    参数:
        body: 数组 ``[`` 与 ``]`` 之间的原始字节。

    返回:
        该数组渲染出的文本，词间空格已按字距调整补齐。
    """

    pieces: list[str] = []
    cursor = 0
    for match in re.finditer(rb"\((?:\\.|[^\\()])*\)|-?\d+\.?\d*", body):
        token = match.group(0)
        if match.start() > cursor and body[cursor : match.start()].strip(b" \r\n\t"):
            # 数组内出现非空白噪声时保留原始分隔，避免静默丢字
            pieces.append(" ")
        cursor = match.end()
        if token.startswith(b"("):
            pieces.append(decode_pdf_literal(token))
            continue
        try:
            adjustment = float(token)
        except ValueError:
            continue
        if adjustment < WORD_SPACE_THRESHOLD:
            pieces.append(" ")
    return "".join(pieces)


def extract_page_texts(pdf_bytes: bytes) -> list[str]:
    """逐个流对象解压并抽取文本，返回每个内容流对应的文本块。

    参数:
        pdf_bytes: 完整 PDF 文件字节。

    返回:
        文本块列表，顺序与文件中的流对象一致（通常近似页面顺序）。
    """

    texts: list[str] = []
    for match in re.finditer(rb"stream\r?\n", pdf_bytes):
        start = match.end()
        end = pdf_bytes.find(b"endstream", start)
        if end < 0:
            continue
        payload = pdf_bytes[start:end]
        try:
            content = zlib.decompress(payload)
        except zlib.error:
            continue
        if b"Tj" not in content and b"TJ" not in content:
            continue
        pieces: list[str] = []
        cursor = 0

        def consume(segment: bytes) -> None:
            """按出现顺序消费一段内容流中的文本显示操作符。"""

            position = 0
            for operator in re.finditer(
                rb"\[(?:[^\[\]\\]|\\.)*\]\s*TJ|\((?:\\.|[^\\()])*\)\s*Tj", segment
            ):
                if operator.start() > position and segment[position : operator.start()].strip(
                    b" \r\n\t"
                ):
                    pieces.append(" ")
                position = operator.end()
                raw_operator = operator.group(0)
                if raw_operator.endswith(b"TJ"):
                    array_match = ARRAY_PATTERN.match(
                        raw_operator[: raw_operator.rindex(b"]") + 1]
                    )
                    if array_match:
                        pieces.append(decode_tj_array(array_match.group(1)))
                        continue
                    pieces.append(decode_tj_array(raw_operator))
                    continue
                literal = LITERAL_PATTERN.match(raw_operator)
                if literal:
                    pieces.append(decode_pdf_literal(literal.group(0)))
            if segment[position:].strip(b" \r\n\t"):
                pieces.append(" ")

        for operator in NEWLINE_OPERATOR_PATTERN.finditer(content):
            segment = content[cursor : operator.start()]
            cursor = operator.end()
            consume(segment)
            if operator.group(1) in (b"T*", b"TD", b"Td", b"ET"):
                pieces.append("\n")
        consume(content[cursor:])
        joined = "".join(pieces)
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n{2,}", "\n", joined)
        if joined.strip():
            texts.append(joined.strip())
    return texts


def main() -> int:
    """入口：读取命令行给出的 PDF 路径并把文本写入指定文本文件。"""

    if len(sys.argv) != 3:
        print("用法: extract_pdf_text.py <输入.pdf> <输出.txt>", file=sys.stderr)
        return 2
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    pdf_bytes = source.read_bytes()
    texts = extract_page_texts(pdf_bytes)
    target.write_text("\n\n=== BLOCK BREAK ===\n\n".join(texts), encoding="utf-8")
    print(f"extracted {len(texts)} text streams -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
