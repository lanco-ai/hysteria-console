"""Merchant-editable product notice defaults, independent of supplier copy."""

import re

CLAUDE_PRODUCT_IDS = frozenset({"1000000000005", "1000000000007", "1000000000008"})
GROK_PRODUCT_IDS = frozenset({"1000000000011"})
GEMINI_PRODUCT_IDS = frozenset({"2000000000001"})
MUSE_PRODUCT_IDS = frozenset({"2000000000002"})
DUOLINGO_PRODUCT_IDS = frozenset({"2000000000003"})

# Merchant-approved display copy; supplier plan names stay unchanged in source snapshots.
PRODUCT_TITLES = {
    "6": "codex额度充值",
    "1000000000005": "Claude Pro 直充",
}
# Merchant-approved spec names, by product and the supplier's spec name; an unknown name stays as it is.
VARIANT_LABELS = {
    "1": {"plus不可以覆盖": "plus新开卡密", "一卡一付": "plus续费卡密"},
}
# Short merchant-approved labels shown beside a product's title; a hot one is set in red.
PRODUCT_TAGS = {
    "1000000000005": ({"text": "美区官方 iOS 充值"}, {"text": "掉订阅全程质保"}),
    "2000000000001": ({"text": "支持最新 Gemini 模型"},),
    "2000000000002": ({"text": "质保首次登录"}, {"text": "不换 IP 不封号"}),
    "6000000000002": ({"text": "固定IP"}, {"text": "支持各大 AI 软件"}, {"text": "Claude 不封号"}),
    "6000000000005": ({"text": "官方渠道"},),
    "6000000000006": ({"text": "官方直充"}, {"text": "5折", "hot": True}, {"text": "自行查询资格"}),
    "6000000000007": ({"text": "新版"}, {"text": "Plus标准额度"}, {"text": "质保30天"}),
    "6000000000008": ({"text": "新版"}, {"text": "4倍Plus额度"}, {"text": "质保30天"}),
    "6000000000009": ({"text": "新版"}, {"text": "15倍Plus额度"}, {"text": "质保30天"}),
    "6000000000010": ({"text": "新版"}, {"text": "50倍Plus额度"}, {"text": "质保30天"}),
}

DUOLINGO_NOTICE = {
    "description": """## Duolingo Super｜12 个月订阅

**本人账号领取 · 兑换链接交付 · 延续自己的学习进度**

通过本店交付的兑换链接，在自己的多邻国账号上领取 Super 订阅。商品为订阅兑换权益，无需向本店提供账号密码或登录验证码。

### 套餐信息

- **订阅等级：** Duolingo Super。
- **订阅期限：** 12 个月，实际生效及到期时间以账号内显示为准。
- **领取方式：** 使用本人多邻国账号，通过兑换链接自行领取。
- **付款资料：** 此兑换商品无需绑定银行卡；领取时请核对页面显示的条件。

### Super 学习权益

- **无广告学习：** 减少课程间的广告打断，让练习更连贯。
- **无限能量／生命值：** 按当前版本开放的 Super 权益继续练习。
- **个性化复习：** 结合练习中心复习错题、巩固已学内容。

具体功能与可用入口以多邻国当前地区、课程及应用版本为准。本商品为 Super，Max 专属的 AI 视频通话、角色扮演等功能不属于本套餐承诺范围。

### 领取步骤

1. 在浏览器登录准备领取订阅的多邻国账号，确认账号无误。
2. 保持登录状态，在同一浏览器的新标签页打开本店交付的兑换链接。
3. 按页面提示点击「Claim Offer」并完成领取。
4. 返回账号的订阅页面，核对 Super 状态及到期时间。
5. 使用 App 时，请确认登录的是同一账号；必要时刷新或重新登录后查看权益。

### 购买前请确认

- 本商品存在地区及账号领取条件，请先向本店确认适用性。
- 已有订阅或曾领取过活动的账号，是否可兑换、叠加或延长，以实际兑换页面为准。
- 请妥善保管兑换链接，不要公开分享；领取前务必确认当前登录账号。
- 本商品按个人账号领取，不承诺家庭共享名额。""",
    "after_sales": """### 领取前的问题

如出现链接无法打开、兑换失败或权益未到账，请保留订单信息和报错截图，及时联系本店核查。截图请遮挡敏感信息，无需发送账号密码或登录验证码。

### 激活后的保障范围

**激活成功后不提供额外质保。** 领取后请立即检查 Super 订阅状态和到期时间；如与兑换页面显示不一致，请及时反馈并提供相关截图。

### 账号与领取条件

购买前请确认账号及地区符合领取要求。已有订阅、活动领取记录等可能影响兑换资格，具体以官方页面为准。本店不承诺绕过地区或账号限制，也不提供账号代管服务。""",
}

CLAUDE_NOTICE = {
    "description": """# ⚠️ 充值前重要须知

⚠️ Important Notice Before Purchase: International customers, please use a translation tool and carefully read all instructions before making your purchase.

⚠️ Важная информация перед покупкой: международным клиентам, пожалуйста, используйте переводчик и внимательно ознакомьтесь со всеми инструкциями перед покупкой.

⚠️ نکات مهم قبل از خرید: مشتریان بین‌المللی لطفاً از ابزار ترجمه استفاده کرده و پیش از خرید، تمام دستورالعمل‌ها را با دقت مطالعه کنید.

以下情况充值可能无法到账，请下单前务必仔细检查。因以下情况导致订阅无法到账，均无法退款，请确认无误后再下单。

## ❌ 1️⃣ 当前订阅未到期

当前订阅未到期（例如 iOS、Android、信用卡），例如下图：

![其他渠道订阅未到期示例](/shop-notices/claude/active-subscription)

如果当前账户还有其他渠道的有效订阅，请勿下单充值，必须等待订阅到期降级为 Free 账户后才可以充值。

## ❌ 2️⃣ Billing 账单存在欠费或退款记录

请进入账户 Billing（账单）页面检查：

1. 存在未支付的欠费；
2. 存在退款记录；
3. 存在因退款或欠费产生的待处理金额。

如果账户存在欠费，充值金额可能会被官方系统优先用于抵扣欠款，从而导致充值无法正常开通订阅。

因此，下单前请务必确认 Billing 页面不存在欠费或异常账单。

## ❌ 3. 组织 ID（Organization ID）被隐性封禁

部分账户虽然可以正常登录官网，但其 Organization ID 实际上已经被官方限制或封禁。这种情况属于隐性封禁。

### 🔍 检测方法 1：发送消息测试

下单前，请先登录官网，在对话框中随便发送一条消息。

- ✅ 消息能够正常发送 → 可以继续检查其他条件
- ❌ 消息发送失败，并出现 Organization ID 已被封禁／限制等提示 → 请勿充值

![发送消息时组织 ID 被限制的示例](/shop-notices/claude/message-restriction)

### 🔍 检测方法 2：进入升级订阅页面

进入官网的升级订阅页面，随便点击 PRO 或 MAX 进入订阅页面。

如果在订阅页面右上角出现相关组织 ID 被封禁／限制的提示：

❌ 说明当前 Organization ID 存在隐性封禁，请勿下单充值。

![升级订阅页组织 ID 被限制的示例](/shop-notices/claude/upgrade-restriction)
""",
    "after_sales": """## 🛡️ 质保规则

掉订阅全程质保，按天数退差价。如果出现封号属于官方封控或者个人原因，无法提供任何质保！

## 📌 下单前请务必确认

1. 没有其他渠道的有效订阅
2. Billing 没有欠费／异常退款记录
3. Organization ID 没有被隐性封禁
4. 官网可以正常发送消息

确认以上条件全部正常后，再进行充值。

**充值前请仔细检查，充值成功后因账户自身原因导致无法到账的，无法退款。**""",
}

GROK_NOTICE = {
    "description": """# 宝贝详情

![Grok SuperGrok 月卡详情](/shop-notices/grok/supergrok-monthly)

## Grok SuperGrok 月卡

下单后由本店人工交付卡密与充值方式，请通过本店联系方式获取。

本商品卡密可囤 1 个月，超过 1 个月后不提供任何售后。

iOS 充值渠道，输入账号 ID 直接充值，全平台通用！

## Grok SuperGrok Monthly

After you order, we deliver the CDK and redemption instructions manually. Please contact us through this store.

The CDK can be stored for up to one month before redemption. We recommend redeeming it within that time.

This top-up is processed through iOS billing and works with accounts created on any platform. It can also replace an active subscription. Please note that any time remaining on your current subscription will not carry over or be added to the new subscription period.
""",
    "after_sales": """## 质保规则

掉订阅全程质保，按天数退差价。如果出现封号属于官方封控或者个人原因，无法提供任何质保！

## Warranty Policy

Your subscription is covered for the full one-month term. If the subscription is unexpectedly removed during that period, you will receive a prorated refund for the unused days. Account suspensions resulting from platform enforcement or user-related issues are not covered by the warranty.
""",
}

# Gemini's activity requirements and merchant terms are rendered as safe Markdown.
GEMINI_NOTICE = {
    "description": """# 💎 Gemini AI Pro｜18个月官方订阅

**印度 Jio 官方合作活动｜个人账号开通｜支持家庭共享**

通过印度 Jio 官方合作活动领取 Google AI Pro 订阅权益，**有效期18个月**。使用自己的 Google 账号即可领取，无需提供密码或验证码。

### 🚀 套餐权益

- **AI 模型：** 支持 Gemini Pro、Flash 等高级模型，具体版本以官方开放为准。
- **视频生成：** 支持 Veo 系列视频创作功能，具体额度以官方为准。
- **家庭共享：** 最多6人（本人+5位成员），共享权益以官方政策为准。
- **隐私独立：** 各成员使用自己的账号，个人聊天和文件互不可见。

### ⚡ 开通步骤

1. 登录自己的 Google 账号。
2. 复制本店发送的官方订阅领取链接。
3. 使用 Chrome 浏览器打开并按提示领取。
4. 领取成功，即可使用对应订阅权益。

### 🌍 购买前请自查

- Google 账号年龄已满18周岁。
- 账号地区符合活动要求。
- 付款资料及家庭地址符合 Google 相关政策。
- 符合印度 Jio 活动的实际领取资格。

账号地区查询：

[https://policies.google.com/terms?hl=zh_CN](https://policies.google.com/terms?hl=zh_CN)""",
    "after_sales": """### 📌 购买须知

- 本商品出售的是**官方活动订阅领取链接**，非成品账号。
- 订阅期限及具体 AI 权益以 Google 官方页面为准。
- 默认无额外质保。
- **虚拟商品，售出不退。**

**⚠️ 下单前请自行确认账号领取资格，避免因地区或账号限制导致无法开通。**""",
}

# Product details supplied by the merchant, not copied from the live supplier feed.
MUSE_NOTICE = {
    "description": """# Muse AI

## 套餐内容

- 商品资料标注含 **10 亿（1 Billion）Tokens**。
- 支持 **AI 图像生成**与 **AI 视频生成**。
- 适合短视频、长视频创作，包括 1 小时以上视频。

## 交付格式

邮箱地址 | 邮箱登录信息（或密码）

## 登录方法

### 方法一：OTP 获取页面

打开 [Muse OTP 获取页面](https://dev-api.cc/muse.php)，按页面提示获取登录验证码。

### 方法二：临时邮箱

1. 打开 [临时邮箱登录页](https://www.tmail.pk/login)，使用交付的邮箱地址和邮箱登录信息登录。
2. 进入 [Muse AI 官网](https://muse.ai/)或 Muse AI App，输入交付的邮箱地址。
3. 返回临时邮箱，查看 Muse 发来的 OTP 验证码。
4. 将 OTP 填入 Muse 登录页面，完成登录。

登录后建议将账户邮箱修改为自己的常用邮箱。

## 视频教程

[查看登录操作教程](https://www.youtube.com/watch?v=lpsRG9VSDuQ)""",
    "after_sales": "",
}

# Plain text: X Premium is delivered as an official gift to the buyer's handle.
X_PREMIUM_NOTICE = {
    "description": """X（推特）Premium 会员，通过官方礼物赠送到你的账号，付款后自动到账。

· 购买时请提供你的 X 用户名（@ 后面的部分），例如 @elonmusk 填写 elonmusk
· 无需提供密码，也不需要登录你的账号
· 请确认用户名准确无误，赠送成功后无法更换账号""",
    "after_sales": """因用户名填写错误导致赠送到其他账号的，不予退换；若长时间未到账，请联系本店处理。""",
}

# Supplier storefronts and contact handles. Customer-facing copy must never send
# buyers to the supplier, so any line naming one of these is dropped from saved
# and projected product copy. Redemption portals (aiflbchengzi.com, sub2buy.com)
# sell nothing and stay: buyers need them to redeem what this store delivers.
SUPPLIER_MARKERS = (
    "qiangyunai",
    "anligpt",
    "t.me/bkbk58",
    "t.me/buy_gptplus",
    "t.me/buygpt_plus",
    "prodseller",
    "lorry.site",
    "fanai.us.ci",
)


def strip_supplier_lines(text):
    """Remove every line that mentions a supplier storefront or contact."""
    if not isinstance(text, str) or not text:
        return text
    lowered = text.lower()
    if not any(marker in lowered for marker in SUPPLIER_MARKERS):
        return text
    kept = [
        line for line in text.split("\n")
        if not any(marker in line.lower() for marker in SUPPLIER_MARKERS)
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")


def sanitize_product_copy(products):
    return {
        product_id: {field: strip_supplier_lines(value) for field, value in copy.items()}
        if isinstance(copy, dict) else copy
        for product_id, copy in products.items()
    }
