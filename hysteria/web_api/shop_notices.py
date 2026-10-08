"""Merchant-editable product notice defaults, independent of supplier copy."""

import re

CLAUDE_PRODUCT_IDS = frozenset({"1000000000005", "1000000000007", "1000000000008"})
GROK_PRODUCT_IDS = frozenset({"1000000000011"})
GEMINI_PRODUCT_IDS = frozenset({"2000000000001"})

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

# Plain text: Gemini copy is not rendered as Markdown on the storefront.
GEMINI_NOTICE = {
    "description": """Gemini Pro（Google AI Pro）18 个月会员激活链接，独立账号使用，不是邀请加入。

· 含 5TB 云端存储，可添加 5 位家庭成员
· 无需绑定银行卡，任何地区均可激活
· 链接自交付起 7 天内有效，请尽快兑换

使用方法：在浏览器中打开收到的兑换链接，点击“Activate Offer”即可完成激活。如果页面提示链接不可用，请点击“Not Working”按钮获取新链接。""",
    "after_sales": """交付后 24 小时内如链接无法激活，请联系本店处理；激活成功后不再提供售后。""",
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
