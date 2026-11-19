# Valorant Weapon Skin VP Prices (`va_item_cost`)

自动抓取并维护《无畏契约》（Valorant）武器皮肤的 VP（Valorant Points）价格数据，输出扁平化且去除空格与回车的单行 `{"skin_uuid":vp_price}` JSON 文件，并通过 GitHub Actions 定时自动同步最新皮肤及推送 Discord 更新通知。

## 数据文件说明

仓库每次自动更新会生成以下 4 份不同过滤策略的 JSON 文件（均已默认去除空格与换行）：

| 文件名 | 包含内容 | 适用场景 |
| :--- | :--- | :--- |
| `skin_vp_prices.min.json` | **仅特殊价格商店皮肤**（已省略常规价格商店皮肤、通行证皮肤和免费契约皮肤） | 客户端内置常规品质价格基线，且无需展示通行证/免费皮肤价格时（体积仅 ~11 KB） |
| `skin_vp_prices.ultra.min.json` | **极限压缩版**（在 `skin_vp_prices.min.json` 基础上，将符合“同套装枪械价格 2 倍”的常规近战武器也一并省略） | 客户端可结合 `themeUuid` 推导常规近战武器价格时（体积仅 ~8.7 KB） |
| `skin_vp_prices.json` | **特殊价格皮肤 + 通行证（`-1`）+ 免费皮（`0`）**（省略常规价格商店皮肤） | 客户端内置常规品质价格基线，同时需要区分通行证（`-1`）与免费契约皮肤（`0`）时（体积 ~29 KB） |
| `skin_vp_prices_store_all.json` | **全量付费商店皮肤（特殊定价 + 普通常规定价）**（不包含通行证与任何免费皮肤） | 客户端不想自行按品质等级计算常规价格，直接查表获取所有商店皮肤价格时（体积 ~37 KB） |

## 价格数值约定与常规价格基线（Content Tier）

- **正整数（如 `875`, `1275`, `1775`, `2175`, `2375`, `2475`, `2675`, `3550`, `4350`, `5350` 等）**：商店皮肤的实际 VP 售价
- **`-1`**：战斗通行证（Battle Pass）皮肤
- **`0`**：特工契约（Agent Gear）或免费活动掉落皮肤

当使用精简版 JSON（如 `skin_vp_prices.min.json` 或 `skin_vp_prices.json`）且某个商店皮肤未出现在文件中时，说明它符合以下**常规品质等级（Content Tier）默认定价**：

| 品质等级 (Tier) | `contentTierUuid` | 常规枪皮 (VP) | 常规刀皮 (VP) |
| :--- | :--- | :--- | :--- |
| **Select (蓝)** | `12683d76-48d7-84a3-4e09-6985794f0445` | `875` | `1750` |
| **Deluxe (绿)** | `0cebb8be-46d7-c12a-d306-e9907bfc5a25` | `1275` | `2550` |
| **Premium (紫)** | `60bca009-4182-7998-dee7-b8a2558dc369` | `1775` | `3550` |
| **Exclusive (金)** | `e046854e-406c-37f4-6607-19a9ba8426fc` | `2175` | `4350` |
| **Ultra (红)** | `411e4a55-4e59-7757-41f0-86a53f101bb5` | `2475` | `4950` |

## 命令行使用说明

脚本默认输出**去除空格和回车的单行最小化 JSON**（如需带缩进换行的可读格式可加 `--pretty`），且默认将通行证皮肤设为 `-1`、免费皮肤设为 `0`：

```bash
# 1. 极简版：仅保留特殊价格商店皮肤（省略常规商店皮 + 省略通行证 + 省略免费皮）
python3 fetch_skin_prices.py -o skin_vp_prices.min.json --only-special-vp

# 2. 极限压缩版：连符合所属套装 2 倍价格的常规近战刀也一并省略
python3 fetch_skin_prices.py -o skin_vp_prices.ultra.min.json --only-special-vp --strict-theme-melee

# 3. 精简版：省略常规商店皮，保留特殊价格商店皮 + 通行证 (-1) + 免费皮 (0)
python3 fetch_skin_prices.py -o skin_vp_prices.json --omit-standard

# 4. 全量商店皮肤版：包含所有特殊定价与普通常规定价的商店皮肤，不包含通行证与任何免费皮肤
python3 fetch_skin_prices.py -o skin_vp_prices_store_all.json --only-store-vp
```
