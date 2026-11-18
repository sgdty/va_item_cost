#!/usr/bin/env python3
"""
Valorant 武器皮肤 VP 价值自动抓取与维护脚本

核心逻辑：
1. 自动从 Valorant-API (https://valorant-api.com) 拉取最新全量数据：
   - `/v1/weapons/skins` (所有武器皮肤及品质等级)
   - `/v1/bundles` (所有商城捆绑包，用于辅助 100% 区分商城皮肤与通行证皮肤)
   - `/v1/themes` (所有皮肤主题系列)
2. 自动从 Valorant Wiki (Fandom MediaWiki API) 拉取精确历史定价与来源分类：
   - `Template:Skins/Store` (商城皮肤精确 VP 价格，包含联动溢价、特殊刀皮溢价)
   - `Template:Skins/BP` (通行证皮肤)
   - `Template:Skins/Gear` & `Template:Skins/Other` (特工契约与免费掉落皮肤)
3. 灵活通过命令行参数控制输出 `{"skin_uuid": vp_price}` 结构 JSON 的过滤策略：
   - `--omit-standard` / `--include-standard`: 控制是否省略符合常规品质定价的商店皮肤（默认省略）
   - `--omit-bp`: 控制是否省略通行证皮肤
   - `--omit-free`: 控制是否省略特工契约/免费皮肤
   - `--only-special-vp`: 仅保留特殊 VP 定价的商店皮肤（同时省略常规商店皮 + 通行证皮 + 免费皮，体积最小）
   - `--strict-theme-melee`: 将符合同系列枪械 2 倍价格的常规近战武器也视为常规皮肤省略
4. 支持增量对比与 Discord Webhook 消息通知：
   - 当检测到输出 JSON 内容有更新时，自动向指定的 Discord Webhook 推送更新详情。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALORANT_SKINS_API = "https://valorant-api.com/v1/weapons/skins"
VALORANT_BUNDLES_API = "https://valorant-api.com/v1/bundles"
VALORANT_THEMES_API = "https://valorant-api.com/v1/themes"
WIKI_API_BASE = "https://valorant.fandom.com/api.php"

SELECT_TIER = "12683d76-48d7-84a3-4e09-6985794f0445"
DELUXE_TIER = "0cebb8be-46d7-c12a-d306-e9907bfc5a25"
PREMIUM_TIER = "60bca009-4182-7998-dee7-b8a2558dc369"
EXCLUSIVE_TIER = "e046854e-406c-37f4-6607-19a9ba8426fc"
ULTRA_TIER = "411e4a55-4e59-7757-41f0-86a53f101bb5"

# Valorant 常规品质等级 (Content Tier UUID) 对应的默认枪械价格
STANDARD_GUN_PRICES: dict[str, int] = {
    SELECT_TIER: 875,     # Select (蓝)
    DELUXE_TIER: 1275,    # Deluxe (绿)
    PREMIUM_TIER: 1775,   # Premium (紫)
    EXCLUSIVE_TIER: 2175, # Exclusive (金 - 常规枪皮)
    ULTRA_TIER: 2475,     # Ultra (红 - 常规枪皮)
}

# 各品质等级对应的常规近战武器价格（等于同品质枪械价格的 2 倍）
STANDARD_MELEE_PRICES_BY_TIER: dict[str, int] = {
    SELECT_TIER: 1750,    # Select 刀
    DELUXE_TIER: 2550,    # Deluxe 刀
    PREMIUM_TIER: 3550,   # Premium 刀
    EXCLUSIVE_TIER: 4350, # Exclusive 刀
    ULTRA_TIER: 4950,     # Ultra 刀
}


def normalize_name(name: str) -> str:
    """标准化名称，消除空格、斜杠、引号、特殊字符差异以便精准对齐 Wiki 与 API"""
    s = name.lower().strip()
    s = s.replace("\r", " ").replace("\n", " ")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def fetch_json(url: str, cache_file: Path | None = None) -> dict[str, Any]:
    """拉取远程 JSON（兼容 macOS Python SSL 证书缺失、代理环境及 Cloudflare TLS 校验）"""
    if cache_file and cache_file.exists():
        raw = cache_file.read_text(encoding="utf-8")
        idx = raw.find('{"')
        if idx != -1:
            return json.loads(raw[idx:].strip())

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, headers=headers)

    # 1. 尝试标准 urllib 请求
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass

    # 2. 尝试忽略 macOS Python 本地 SSL 证书链校验问题 (CERTIFICATE_VERIFY_FAILED)
    try:
        unverified_ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=30, context=unverified_ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass

    # 3. 兜底使用系统自带 curl（自动走系统钥匙串证书、系统代理及 HTTP/2）
    res = subprocess.run(
        ["curl", "-sSL", "--max-time", "30", "-A", headers["User-Agent"], url],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(res.stdout)


def send_discord_notification(
    webhook_url: str,
    file_name: str,
    file_size_kb: float,
    total_records: int,
    stats: dict[str, Any],
    diff_info: dict[str, Any],
    skin_name_lookup: dict[str, str],
) -> None:
    """当 JSON 数据发生更新时，向 Discord Webhook 发送 Embed 卡片通知"""
    added = diff_info["added"]
    changed = diff_info["changed"]
    removed = diff_info["removed"]

    detail_lines: list[str] = []
    for uid, val in list(added.items())[:10]:
        sname = skin_name_lookup.get(uid, uid)
        detail_lines.append(f"➕ **{sname}** (`{uid[:8]}...`): `{val}`")
    for uid, (old_v, new_v) in list(changed.items())[:10]:
        sname = skin_name_lookup.get(uid, uid)
        detail_lines.append(f"🔄 **{sname}** (`{uid[:8]}...`): `{old_v}` → `{new_v}`")
    for uid in list(removed.keys())[:5]:
        sname = skin_name_lookup.get(uid, uid)
        detail_lines.append(f"➖ **{sname}** (`{uid[:8]}...`)")

    if len(added) + len(changed) + len(removed) > len(detail_lines):
        remaining = (len(added) + len(changed) + len(removed)) - len(detail_lines)
        detail_lines.append(f"... 以及其余 **{remaining}** 项变更")

    changes_text = "\n".join(detail_lines) if detail_lines else "文件格式或内容已更新"

    payload = {
        "username": "Valorant Skin Price Bot",
        "embeds": [
            {
                "title": f"🔔 Valorant 皮肤 VP 价格数据已更新 (`{file_name}`)",
                "color": 0xFF4655,
                "fields": [
                    {
                        "name": "📊 变更统计",
                        "value": (
                            f"新增: **{len(added)}** | "
                            f"修改: **{len(changed)}** | "
                            f"移除: **{len(removed)}**"
                        ),
                        "inline": True,
                    },
                    {
                        "name": "📁 文件信息",
                        "value": f"记录数: **{total_records}** 条 (`{file_size_kb:.1f} KB`)",
                        "inline": True,
                    },
                    {
                        "name": "📦 分类汇总",
                        "value": (
                            f"特殊价皮肤: `{stats['special_priced_skins_recorded']}` | "
                            f"常规商店皮(存/略): `{stats['standard_store_skins_recorded']}/{stats['standard_store_skins_omitted']}` | "
                            f"通行证(存/略): `{stats['battlepass_skins_recorded']}/{stats['battlepass_skins_omitted']}`"
                        ),
                        "inline": False,
                    },
                    {
                        "name": "📝 变更明细预览",
                        "value": changes_text[:1024],
                        "inline": False,
                    },
                ],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ValorantSkinPriceBot/1.0",
    }
    req = urllib.request.Request(webhook_url, data=body, headers=headers, method="POST")

    try:
        unverified_ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=15, context=unverified_ctx) as resp:
            if resp.status in (200, 204):
                print("📨 已成功向 Discord 频道发送更新通知！")
                return
    except Exception:
        pass

    subprocess.run(
        [
            "curl",
            "-sSL",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "-d",
            body.decode("utf-8"),
            webhook_url,
        ],
        check=False,
    )
    print("📨 已通过 curl 向 Discord 频道发送更新通知！")


def fetch_wiki_template(template_name: str, cache_file: Path | None = None) -> str:
    """从 Valorant Fandom Wiki 获取指定模板的 Wikitext"""
    params = urllib.parse.urlencode(
        {
            "action": "parse",
            "page": template_name,
            "prop": "wikitext",
            "format": "json",
        }
    )
    url = f"{WIKI_API_BASE}?{params}"
    data = fetch_json(url, cache_file=cache_file)
    return data["parse"]["wikitext"]["*"]


def parse_wiki_store_skins(wikitext: str) -> dict[str, int]:
    """
    解析 Template:Skins/Store 中的所有商城皮肤及其 VP 价格。
    返回: { normalized_skin_name: vp_price }
    """
    prices: dict[str, int] = {}
    rows = wikitext.split("|-")
    current_collection = ""

    for row in rows:
        col_match = re.search(r"\{\{collection\|([^|}]+)(?:\|([^}]+))?\}\}", row)
        if col_match:
            current_collection = (col_match.group(2) or col_match.group(1)).strip()

        vp_match = re.search(r"\{\{VP\|([\d,]+)\}\}", row)
        if not vp_match:
            continue
        vp_price = int(vp_match.group(1).replace(",", ""))

        file_match = re.search(r"\[\[File:([^|\]]+?)\.png", row)
        if file_match:
            file_skin_name = file_match.group(1).strip()
            prices[normalize_name(file_skin_name)] = vp_price

        melee_match = re.search(r"\[\[Melee\]\]:\s*<br>\s*([^\n|]+)", row)
        if melee_match:
            melee_name = melee_match.group(1).strip()
            prices[normalize_name(melee_name)] = vp_price
        else:
            weapon_matches = re.findall(r"^\|\[\[(?!File:|Melee)([^|\]]+)\]\]", row, flags=re.M)
            if weapon_matches and current_collection:
                weapon_name = weapon_matches[-1].strip()
                combined_name = f"{current_collection} {weapon_name}"
                prices[normalize_name(combined_name)] = vp_price

    return prices


def parse_wiki_bp_or_other_skins(wikitext: str) -> tuple[set[str], set[str]]:
    """
    解析 Template:Skins/BP、Template:Skins/Gear 或 Template:Skins/Other
    返回: (normalized_skin_names, normalized_collection_names)
    """
    skin_names: set[str] = set()
    collections: set[str] = set()
    rows = wikitext.split("|-")
    current_collection = ""

    for row in rows:
        col_match = re.search(r"\{\{collection\|([^|}]+)(?:\|([^}]+))?\}\}", row)
        if col_match:
            col_1 = col_match.group(1).strip()
            col_2 = (col_match.group(2) or col_1).strip()
            current_collection = col_2
            collections.add(normalize_name(col_1))
            collections.add(normalize_name(col_2))

        file_match = re.search(r"\[\[File:([^|\]]+?)\.png", row)
        if file_match:
            file_skin_name = file_match.group(1).strip()
            skin_names.add(normalize_name(file_skin_name))

        melee_match = re.search(r"\[\[Melee\]\]:\s*<br>\s*([^\n|]+)", row)
        if melee_match:
            skin_names.add(normalize_name(melee_match.group(1).strip()))
        elif current_collection:
            weapon_matches = re.findall(r"^\|\[\[(?!File:|Melee|Episode|Season)([^|\]]+)\]\]", row, flags=re.M)
            if weapon_matches:
                weapon_name = weapon_matches[0].strip()
                skin_names.add(normalize_name(f"{current_collection} {weapon_name}"))

    return skin_names, collections


def build_skin_price_map(
    skins_data: list[dict[str, Any]],
    bundles_data: list[dict[str, Any]],
    themes_data: list[dict[str, Any]],
    store_prices: dict[str, int],
    bp_skins: set[str],
    bp_collections: set[str],
    free_skins: set[str],
    bp_marker: str | int = "BP",
    free_marker: str | int = "FREE",
    omit_standard: bool = True,
    omit_bp: bool = False,
    omit_free: bool = False,
    strict_theme_melee: bool = False,
) -> tuple[dict[str, int | str], dict[str, Any]]:
    """
    根据过滤参数构建目标皮肤价格映射。
    返回: (result_map, stats)
    """
    bundle_norm_names = {normalize_name(b["displayName"]) for b in bundles_data if b.get("displayName")}
    theme_id_to_name = {
        t["uuid"]: t["displayName"]
        for t in themes_data
        if t.get("uuid") and t.get("displayName")
    }

    bp_theme_uuids: set[str] = set()
    store_theme_uuids: set[str] = set()
    theme_gun_tiers: dict[str, str] = {}
    theme_gun_store_prices: dict[str, int] = {}

    for skin in skins_data:
        name = skin.get("displayName", "")
        norm_name = normalize_name(name)
        theme_uuid = skin.get("themeUuid")
        tier_uuid = skin.get("contentTierUuid")
        asset_path = skin.get("assetPath", "")
        is_melee = "/Melee/" in asset_path

        if not is_melee and theme_uuid and tier_uuid:
            theme_gun_tiers[theme_uuid] = tier_uuid

        if norm_name in store_prices and theme_uuid:
            store_theme_uuids.add(theme_uuid)
            if not is_melee:
                theme_gun_store_prices[theme_uuid] = store_prices[norm_name]

        if norm_name in bp_skins and theme_uuid:
            bp_theme_uuids.add(theme_uuid)

    # 利用 /v1/themes 与 /v1/bundles 识别 Wiki 尚未及时更新的新赛季套装或通行证系列
    for theme_uuid, theme_name in theme_id_to_name.items():
        base_theme_name = re.sub(r"\s*(//\s*)?2\.0$", "", theme_name.strip(), flags=re.I)
        norm_theme = normalize_name(theme_name)
        norm_base_theme = normalize_name(base_theme_name)

        if norm_theme in bp_collections or norm_base_theme in bp_collections:
            bp_theme_uuids.add(theme_uuid)
        elif norm_theme in bundle_norm_names or norm_base_theme in bundle_norm_names:
            store_theme_uuids.add(theme_uuid)

    result_map: dict[str, int | str] = {}

    stats = {
        "total_api_skins": len(skins_data),
        "skipped_default_or_random": 0,
        "standard_store_skins_omitted": 0,
        "standard_store_skins_recorded": 0,
        "special_priced_skins_recorded": 0,
        "battlepass_skins_recorded": 0,
        "battlepass_skins_omitted": 0,
        "free_gear_skins_recorded": 0,
        "free_gear_skins_omitted": 0,
    }

    for skin in skins_data:
        skin_uuid = skin["uuid"]
        name = skin.get("displayName", "")
        norm_name = normalize_name(name)
        theme_uuid = skin.get("themeUuid")
        tier_uuid = skin.get("contentTierUuid")
        asset_path = skin.get("assetPath", "")
        is_melee = "/Melee/" in asset_path

        # 跳过游戏默认原皮 (Standard ...) 和随机收藏皮肤占位项
        if name.startswith("Standard ") or name == "Melee" or "Random Favorite" in name or not tier_uuid:
            stats["skipped_default_or_random"] += 1
            continue

        # 1. 优先匹配特工契约 / 免费掉落皮肤
        if norm_name in free_skins:
            if omit_free:
                stats["free_gear_skins_omitted"] += 1
            else:
                result_map[skin_uuid] = free_marker
                stats["free_gear_skins_recorded"] += 1
            continue

        # 2. 匹配通行证皮肤 (在 Wiki BP 表中，或者无商城捆绑包的 Select/Deluxe 系列皮肤与近战武器)
        effective_tier = theme_gun_tiers.get(theme_uuid, tier_uuid)
        is_bp = (
            norm_name in bp_skins
            or (theme_uuid in bp_theme_uuids and theme_uuid not in store_theme_uuids)
            or (
                theme_uuid not in store_theme_uuids
                and effective_tier in (SELECT_TIER, DELUXE_TIER)
                and len(skin.get("levels", [])) == 1
                and skin.get("wallpaper") is None
            )
        )
        if is_bp and norm_name not in store_prices:
            if omit_bp:
                stats["battlepass_skins_omitted"] += 1
            else:
                result_map[skin_uuid] = bp_marker
                stats["battlepass_skins_recorded"] += 1
            continue

        # 3. 计算商城皮肤的实际价格
        if norm_name in store_prices:
            actual_price = store_prices[norm_name]
        elif "champions 20" in name.lower():
            actual_price = 5350 if is_melee else 2675
        elif not is_melee:
            actual_price = theme_gun_store_prices.get(
                theme_uuid,
                STANDARD_GUN_PRICES.get(tier_uuid, 1775),
            )
        else:
            inferred_tier = theme_gun_tiers.get(theme_uuid, tier_uuid)
            actual_price = STANDARD_MELEE_PRICES_BY_TIER.get(inferred_tier, 4350)

        # 4. 判断是否属于“常规武器皮肤定价”
        if not is_melee:
            expected_price = STANDARD_GUN_PRICES.get(tier_uuid)
        else:
            if strict_theme_melee:
                inferred_tier = theme_gun_tiers.get(theme_uuid, tier_uuid)
                expected_price = STANDARD_MELEE_PRICES_BY_TIER.get(inferred_tier, 4350)
            else:
                # 近战武器在 Valorant-API 中本身的 contentTierUuid 统一为 Exclusive (4350 VP)
                expected_price = 4350

        is_standard_price = actual_price == expected_price

        if is_standard_price:
            if omit_standard:
                stats["standard_store_skins_omitted"] += 1
            else:
                result_map[skin_uuid] = actual_price
                stats["standard_store_skins_recorded"] += 1
        else:
            result_map[skin_uuid] = actual_price
            stats["special_priced_skins_recorded"] += 1

    return result_map, stats


def compute_diff(
    old_map: dict[str, Any], new_map: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """计算新旧 JSON 数据的增删改差异"""
    added = {k: v for k, v in new_map.items() if k not in old_map}
    removed = {k: v for k, v in old_map.items() if k not in new_map}
    changed = {
        k: (old_map[k], new_map[k])
        for k in new_map
        if k in old_map and old_map[k] != new_map[k]
    }
    return {"added": added, "removed": removed, "changed": changed}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="抓取并生成 Valorant 武器皮肤 VP 价值 JSON 文件（支持按需过滤常规皮肤、通行证及免费皮肤）"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="skin_vp_prices.json",
        help="输出的 JSON 文件路径 (默认: skin_vp_prices.json)",
    )

    # 常规商店皮肤过滤控制
    std_group = parser.add_mutually_exclusive_group()
    std_group.add_argument(
        "--omit-standard",
        dest="omit_standard",
        action="store_true",
        default=True,
        help="省略符合常规品质定价的商店皮肤 (默认开启)",
    )
    std_group.add_argument(
        "--include-standard",
        dest="omit_standard",
        action="store_false",
        help="保留符合常规品质定价的商店皮肤（输出全量商店皮肤价格）",
    )

    # 通行证与免费皮肤过滤控制
    parser.add_argument(
        "--omit-bp",
        action="store_true",
        help="省略通行证 (Battle Pass) 皮肤，不写入 JSON 文件",
    )
    parser.add_argument(
        "--omit-free",
        action="store_true",
        help="省略特工契约/活动免费皮肤，不写入 JSON 文件",
    )
    parser.add_argument(
        "--only-special-vp",
        action="store_true",
        help="极限精简模式：等价于同时开启 --omit-standard --omit-bp --omit-free，只保留特殊 VP 定价的商店皮肤",
    )
    parser.add_argument(
        "--only-store-vp",
        action="store_true",
        help="全量商店皮肤模式：等价于同时开启 --include-standard --omit-bp --omit-free，包含所有特殊定价与普通常规定价的商店皮肤，不包含通行证与任何免费皮肤",
    )

    # 近战武器常规价判定模式
    parser.add_argument(
        "--strict-theme-melee",
        action="store_true",
        help="将符合所属套装品质 2 倍定价的常规近战武器也视为常规皮肤省略，进一步缩小体积",
    )

    # 标记值与格式控制
    parser.add_argument(
        "--bp-value",
        default="BP",
        help="当保留通行证皮肤时，在 JSON 中的标记值 (默认: 'BP'，也可设为 '-1' 或 '0')",
    )
    parser.add_argument(
        "--free-value",
        default="FREE",
        help="当保留免费/契约皮肤时，在 JSON 中的标记值 (默认: 'FREE'，也可设为 '0')",
    )
    parser.add_argument(
        "--minify",
        action="store_true",
        help="输出单行无空格的最小化 JSON",
    )
    parser.add_argument(
        "--discord-webhook",
        default=os.environ.get("DISCORD_WEBHOOK_URL", ""),
        help="可选：Discord Webhook URL（默认从环境变量 DISCORD_WEBHOOK_URL 读取），仅在内容有更新时发送通知",
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="禁用 Discord Webhook 通知",
    )
    parser.add_argument(
        "--cache-dir",
        default="",
        help="可选：本地缓存目录（用于离线测试）",
    )
    args = parser.parse_args()

    if args.only_special_vp:
        omit_standard, omit_bp, omit_free = True, True, True
    elif args.only_store_vp:
        omit_standard, omit_bp, omit_free = False, True, True
    else:
        omit_standard, omit_bp, omit_free = args.omit_standard, args.omit_bp, args.omit_free

    bp_marker: int | str = int(args.bp_value) if args.bp_value.lstrip("-").isdigit() else args.bp_value
    free_marker: int | str = int(args.free_value) if args.free_value.lstrip("-").isdigit() else args.free_value
    cache_dir = Path(args.cache_dir) if args.cache_dir else None

    print("1. 正在获取 Valorant-API 武器皮肤、捆绑包与主题列表...")
    skins_data = fetch_json(
        VALORANT_SKINS_API,
        cache_file=(cache_dir / "skins.json") if cache_dir else None,
    )["data"]
    bundles_data = fetch_json(
        VALORANT_BUNDLES_API,
        cache_file=(cache_dir / "bundles.json") if cache_dir else None,
    )["data"]
    themes_data = fetch_json(
        VALORANT_THEMES_API,
        cache_file=(cache_dir / "themes.json") if cache_dir else None,
    )["data"]

    print("2. 正在从 Valorant Wiki 获取商城、通行证及契约皮肤数据...")
    store_wikitext = fetch_wiki_template(
        "Template:Skins/Store",
        cache_file=(cache_dir / "store.md") if cache_dir else None,
    )
    bp_wikitext = fetch_wiki_template(
        "Template:Skins/BP",
        cache_file=(cache_dir / "bp.md") if cache_dir else None,
    )
    gear_wikitext = fetch_wiki_template(
        "Template:Skins/Gear",
        cache_file=(cache_dir / "gear.md") if cache_dir else None,
    )
    other_wikitext = fetch_wiki_template(
        "Template:Skins/Other",
        cache_file=(cache_dir / "other.md") if cache_dir else None,
    )

    store_prices = parse_wiki_store_skins(store_wikitext)
    bp_skins, bp_collections = parse_wiki_bp_or_other_skins(bp_wikitext)
    gear_skins, _ = parse_wiki_bp_or_other_skins(gear_wikitext)
    other_skins, _ = parse_wiki_bp_or_other_skins(other_wikitext)
    free_skins = gear_skins | other_skins

    print("3. 正在根据过滤参数生成目标数据...")
    result_map, stats = build_skin_price_map(
        skins_data=skins_data,
        bundles_data=bundles_data,
        themes_data=themes_data,
        store_prices=store_prices,
        bp_skins=bp_skins,
        bp_collections=bp_collections,
        free_skins=free_skins,
        bp_marker=bp_marker,
        free_marker=free_marker,
        omit_standard=omit_standard,
        omit_bp=omit_bp,
        omit_free=omit_free,
        strict_theme_melee=args.strict_theme_melee,
    )

    out_path = Path(args.output)
    old_map: dict[str, Any] = {}
    if out_path.exists():
        try:
            old_map = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            old_map = {}

    diff_info = compute_diff(old_map, result_map)
    has_changes = bool(
        not out_path.exists()
        or diff_info["added"]
        or diff_info["changed"]
        or diff_info["removed"]
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    json_kwargs: dict[str, Any] = {"ensure_ascii": False, "sort_keys": True}
    if args.minify:
        json_kwargs["separators"] = (",", ":")
    else:
        json_kwargs["indent"] = 2

    out_path.write_text(json.dumps(result_map, **json_kwargs) + "\n", encoding="utf-8")
    file_size_kb = out_path.stat().st_size / 1024

    print(f"\n✅ 生成完成！文件已保存至: {out_path.resolve()} ({file_size_kb:.1f} KB, 共 {len(result_map)} 条记录)")
    print(f"   - API 皮肤总数: {stats['total_api_skins']} (过滤原皮/随机项: {stats['skipped_default_or_random']})")
    print(
        f"   - 常规价商店皮肤: 省略 {stats['standard_store_skins_omitted']} 个 / 记录 {stats['standard_store_skins_recorded']} 个"
    )
    print(f"   - 特殊价商店皮肤: 记录 {stats['special_priced_skins_recorded']} 个")
    print(
        f"   - 通行证皮肤:     省略 {stats['battlepass_skins_omitted']} 个 / 记录 {stats['battlepass_skins_recorded']} 个"
    )
    print(
        f"   - 免费/契约皮肤:  省略 {stats['free_gear_skins_omitted']} 个 / 记录 {stats['free_gear_skins_recorded']} 个"
    )

    if not args.no_discord and args.discord_webhook and has_changes:
        skin_name_lookup = {s["uuid"]: s.get("displayName", s["uuid"]) for s in skins_data if "uuid" in s}
        send_discord_notification(
            webhook_url=args.discord_webhook,
            file_name=out_path.name,
            file_size_kb=file_size_kb,
            total_records=len(result_map),
            stats=stats,
            diff_info=diff_info,
            skin_name_lookup=skin_name_lookup,
        )


if __name__ == "__main__":
    main()
