#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
競技玩具研究所のベイブレードX商品一覧から、
beycost 用の products.json を生成するスクリプト。

旧館(FC2)は取得元によって503になることがあるため、
同一サイトの新館(Sakura)を優先し、旧館をフォールバックにしています。
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup, Tag


OUTPUT_PATH = Path(__file__).resolve().parents[1] / "products.json"

SOURCE_URLS = [
    "https://kyoganken.sakura.ne.jp/beyx/index.htm",
    "https://kyoganken.web.fc2.com/beyx/index.htm",
]

USER_AGENT = (
    "Mozilla/5.0 (compatible; beycost-data-updater/1.0; "
    "+https://github.com/ogatetsu-0501/beycost)"
)

PRODUCT_CODE_PATTERN = re.compile(r"^(BX|UX|CX)-\d+$")
PRICE_PATTERN = re.compile(r"[¥￥]\s*([0-9,]+)")
RANDOM_NUMBER_PATTERN = re.compile(r"^\s*\d{1,2}\s+")
BEY_PATTERN = re.compile(r"^(.+?)([0-9M]-[0-9]{2})([A-Za-z]+)$")

PRODUCT_TYPE_PREFIX_PATTERN = re.compile(
    r"^(?:スターター|スタータ|ブースター)\s+"
)

EXPLICIT_PART_SUFFIXES = {
    " ブレード": "",
    " ラチェット": "",
    " ビット": "",
}

TOOL_NAMES = [
    "ダブルエクストリームスタジアム",
    "ワイドエクストリームスタジアム",
    "インフィニティスタジアム",
    "エクストリームスタジアム",
    "ドラゴンワインダーランチャーＬ",
    "ロングワインダーランチャー",
    "ストリングランチャーＬ",
    "ストリングランチャーL",
    "ストリングランチャー",
    "ワインダーランチャーＬ",
    "ワインダーランチャーL",
    "ワインダーランチャー",
    "ホールドランチャー",
    "エントリーランチャー",
    "ラバーカスタムグリップ",
    "カスタムグリップ",
    "ランチャーグリップ",
    "ベイバトルパス",
    "3on3デッキケース",
    "ギヤケース",
    "ギアケース",
]


def clean_text(value: str) -> str:
    """HTML由来の空白を1つに揃えて比較しやすくします。"""
    return re.sub(r"\s+", " ", value).strip()


def fetch_soup() -> tuple[BeautifulSoup, str]:
    """新館→旧館の順に商品一覧ページを取得します。"""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    last_error: Exception | None = None

    for url in SOURCE_URLS:
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            return BeautifulSoup(response.text, "html.parser"), url
        except Exception as error:  # noqa: BLE001
            last_error = error

    raise RuntimeError(f"商品一覧を取得できませんでした: {last_error}")


def get_direct_cells(row: Tag) -> list[Tag]:
    """入れ子テーブルのセルを誤取得しないよう、行直下のセルだけを返します。"""
    return [
        child
        for child in row.children
        if isinstance(child, Tag) and child.name in {"td", "th"}
    ]


def get_list_items(cell: Tag) -> list[str]:
    """備考セル内の箇条書きだけを取り出します。"""
    return [
        clean_text(item.get_text(" ", strip=True))
        for item in cell.find_all("li")
        if clean_text(item.get_text(" ", strip=True))
    ]


def canonical_tool_name(value: str) -> str | None:
    """色違い表記を無視し、ツール名を共通名へ寄せます。"""
    for tool_name in TOOL_NAMES:
        if tool_name in value:
            if tool_name == "ギアケース":
                return "ギヤケース"
            if tool_name.endswith("L"):
                return tool_name[:-1] + "Ｌ"
            return tool_name

    return None


def remove_variant_suffix(value: str) -> str:
    """色・スペシャル表記を外し、構造パーツ名だけに寄せます。"""
    normalized = clean_text(value)
    normalized = RANDOM_NUMBER_PATTERN.sub("", normalized)

    # 商品内訳でよく付く装飾差分は、価格比較では同一パーツとして扱います。
    split_markers = [
        " メタルコート：",
        " スペシャルVer.",
        " スペシャルver.",
        " ゴールドVer.",
        " シルバーVer.",
        " ブロンズVer.",
        " レッドVer.",
        " ブルーVer.",
        " ブラックVer.",
        " ホワイトVer.",
        " 限定カラー",
        " FCバルセロナVer.",
    ]

    for marker in split_markers:
        marker_index = normalized.find(marker)

        if marker_index >= 0:
            normalized = normalized[:marker_index].strip()

    # 色違いランダムの末尾括弧を除きます。
    normalized = re.sub(r"[（(](?:緑|黒|赤紫|紫|金|赤|青|白)[）)]$", "", normalized).strip()

    return normalized


def decompose_bey(value: str) -> list[str]:
    """
    「ドランソード3-60F」のような名称を
    ブレード / ラチェット / ビットへ分解します。

    CXの分割ブレードについては、現段階では
    「ドランブレイブS」のようなブレード一式を1パーツとして扱います。
    """
    normalized = remove_variant_suffix(value)
    match = BEY_PATTERN.match(normalized)

    if not match:
        return [normalized] if normalized else []

    blade_name = match.group(1).strip()
    ratchet_name = match.group(2).strip()
    bit_name = match.group(3).strip()

    return [
        part_name
        for part_name in [blade_name, ratchet_name, bit_name]
        if part_name
    ]


def decompose_item(value: str) -> list[str]:
    """セット内容1行を、beycostで扱う個別パーツ名へ分解します。"""
    normalized = remove_variant_suffix(value)

    if not normalized:
        return []

    tool_name = canonical_tool_name(normalized)

    if tool_name:
        return [tool_name]

    for suffix, replacement in EXPLICIT_PART_SUFFIXES.items():
        if normalized.endswith(suffix):
            part_name = normalized[: -len(suffix)].strip() + replacement
            return [part_name] if part_name else []

    # セット内容の説明文が後ろに付く場合は、最初の名称だけを使います。
    first_token = normalized.split(" ", 1)[0]

    if BEY_PATTERN.match(first_token):
        return decompose_bey(first_token)

    if BEY_PATTERN.match(normalized):
        return decompose_bey(normalized)

    return [normalized]


def aggregate_parts(part_names: Iterable[str]) -> list[dict[str, object]]:
    """同じパーツが複数入っている場合は quantity に合算します。"""
    quantity_map: OrderedDict[str, int] = OrderedDict()

    for raw_name in part_names:
        name = clean_text(raw_name)

        if not name:
            continue

        quantity_map[name] = quantity_map.get(name, 0) + 1

    return [
        {"name": name, "quantity": quantity}
        for name, quantity in quantity_map.items()
    ]


def extract_launcher_from_note(note_text: str) -> list[str]:
    """スターター備考の「○○ランチャー付属」をパーツとして拾います。"""
    if "付属" not in note_text:
        return []

    tool_name = canonical_tool_name(note_text)
    return [tool_name] if tool_name else []


def get_product_base_item(product_name: str) -> str:
    """スターター/ブースター名からベイ名だけを取り出します。"""
    return PRODUCT_TYPE_PREFIX_PATTERN.sub("", product_name).strip()


def is_random_product(product_name: str, note_text: str) -> bool:
    """ランダム封入商品かを判定します。"""
    has_random_name = "ランダムブースター" in product_name
    has_random_note = "いずれか" in note_text
    return has_random_name or has_random_note


def parse_product_rows(soup: BeautifulSoup, source_url: str) -> list[dict[str, object]]:
    """製品情報一覧の表から商品価格と内容を抽出します。"""
    products: list[dict[str, object]] = []
    seen_product_ids: set[str] = set()

    for table in soup.find_all("table"):
        header_text = clean_text(table.get_text(" ", strip=True))
        has_product_code_header = "品番" in header_text
        has_price_header = "価格" in header_text

        if not has_product_code_header or not has_price_header:
            continue

        for row in table.find_all("tr"):
            cells = get_direct_cells(row)

            if len(cells) < 4:
                continue

            code = clean_text(cells[0].get_text(" ", strip=True))

            if not PRODUCT_CODE_PATTERN.match(code):
                continue

            product_name = clean_text(cells[1].get_text(" ", strip=True))
            note_cell = cells[2]
            note_text = clean_text(note_cell.get_text(" ", strip=True))
            price_text = clean_text(cells[3].get_text(" ", strip=True))

            price_match = PRICE_PATTERN.search(price_text)

            if not product_name or not price_match:
                continue

            price = int(price_match.group(1).replace(",", ""))
            list_items = get_list_items(note_cell)
            random_product = is_random_product(product_name, note_text)

            link = cells[1].find("a")
            detail_url = ""

            if link and link.get("href"):
                detail_url = str(link.get("href"))

            # ランダム商品は各封入候補を別商品として展開します。
            if random_product and list_items:
                for variant_index, item_text in enumerate(list_items, start=1):
                    part_names = decompose_item(item_text)

                    if not part_names:
                        continue

                    product_id = f"source-{code.lower()}-{variant_index:02d}"

                    if product_id in seen_product_ids:
                        continue

                    seen_product_ids.add(product_id)
                    variant_name = RANDOM_NUMBER_PATTERN.sub("", item_text).strip()

                    products.append(
                        {
                            "id": product_id,
                            "name": f"{code} {product_name} / {variant_name}",
                            "price": price,
                            "parts": aggregate_parts(part_names),
                            "isRandom": True,
                            "source": source_url,
                            "detailUrl": detail_url,
                        }
                    )

                continue

            part_names: list[str] = []

            if list_items:
                for item_text in list_items:
                    part_names.extend(decompose_item(item_text))
            else:
                base_item = get_product_base_item(product_name)
                product_tool_name = canonical_tool_name(base_item)

                if product_tool_name:
                    part_names.append(product_tool_name)
                else:
                    part_names.extend(decompose_item(base_item))

                part_names.extend(extract_launcher_from_note(note_text))

            parts = aggregate_parts(part_names)

            if not parts:
                continue

            product_id = f"source-{code.lower()}"

            if product_id in seen_product_ids:
                continue

            seen_product_ids.add(product_id)

            products.append(
                {
                    "id": product_id,
                    "name": f"{code} {product_name}",
                    "price": price,
                    "parts": parts,
                    "isRandom": False,
                    "source": source_url,
                    "detailUrl": detail_url,
                }
            )

    return products


def main() -> None:
    """商品一覧を取得し、JSONへ保存します。"""
    soup, source_url = fetch_soup()
    products = parse_product_rows(soup, source_url)

    if not products:
        raise RuntimeError("商品データを1件も抽出できませんでした。")

    OUTPUT_PATH.write_text(
        json.dumps(products, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"{len(products)}件を書き出しました: {OUTPUT_PATH}")
    print(f"source: {source_url}")


if __name__ == "__main__":
    main()
