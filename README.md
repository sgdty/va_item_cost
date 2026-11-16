# Valorant Weapon Skin VP Prices (`va_item_cost`)

自动抓取并维护《无畏契约》（Valorant）武器皮肤的 VP（Valorant Points）价格数据，输出扁平化的 `{"skin_uuid": vp_price}` JSON 文件，并通过 GitHub Actions 每日定时自动同步最新皮肤。

## 数据文件说明

| 文件名 | 说明 |
| :--- | :--- |
| `skin_vp_prices.min.json` | **极简版（仅特殊价格商店皮肤）**：已省略所有常规价格商店皮肤、通行证皮肤和免费契约皮肤，仅记录打破常规价格基线的特殊皮肤（如 `2375` 联动/超限套、`2675` 冠军套、VCT 战队胶囊及特殊价格近战武器） |
| `skin_vp_prices.ultra.min.json` | **极限压缩版**：在 `skin_vp_prices.min.json` 基础上，将符合“同套装枪械价格 2 倍”的常规近战武器也一并省略 |
| `skin_vp_prices.json` | **带通行证与免费皮标记的精简版**：省略常规价格商店皮肤，记录特殊价格皮肤、通行证皮肤（`BP` 或 `-1`）及免费契约皮肤（`FREE` 或 `0`） |
| `skin_vp_prices_full.json` | **全量版**：包含全部非原皮武器的价格与来源映射 |

## 常规商店皮肤价格基线（Content Tier）

当某个商店皮肤未出现在精简版 JSON 中时，直接根据其 `contentTierUuid` 计算常规价格：

| 品质等级 (Tier) | `contentTierUuid` | 常规枪皮 (VP) | 常规刀皮 (VP) |
| :--- | :--- | :--- | :--- |
| **Select (蓝)** | `12683d76-48d7-84a3-4e09-6985794f0445` | `875` | `1750` |
| **Deluxe (绿)** | `0cebb8be-46d7-c12a-d306-e9907bfc5a25` | `1275` | `2550` |
| **Premium (紫)** | `60bca009-4182-7998-dee7-b8a2558dc369` | `1775` | `3550` |
| **Exclusive (金)** | `e046854e-406c-37f4-6607-19a9ba8426fc` | `2175` | `4350` |
| **Ultra (红)** | `411e4a55-4e59-7757-41f0-86a53f101bb5` | `2475` | `4950` |

## 命令行使用说明

```bash
# 1. 仅保留特殊价格皮肤（省略常规商店皮 + 省略通行证 + 省略免费皮）
python3 fetch_skin_prices.py -o skin_vp_prices.min.json --only-special-vp --minify

# 2. 极限压缩模式（连符合所属套装 2 倍价格的常规近战刀也一并省略）
python3 fetch_skin_prices.py -o skin_vp_prices.ultra.min.json --only-special-vp --strict-theme-melee --minify

# 3. 省略常规商店皮，但保留通行证 (-1) 和免费皮 (0)
python3 fetch_skin_prices.py -o skin_vp_prices.json --omit-standard --bp-value -1 --free-value 0

# 4. 保留全部商店皮肤（含常规商店皮），但省略通行证和免费皮
python3 fetch_skin_prices.py -o skin_vp_prices_store_all.json --include-standard --omit-bp --omit-free --minify
```
