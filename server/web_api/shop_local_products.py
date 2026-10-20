"""Private, store-owned products; no supplier fetch or persisted source cache."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

PRODUCT_BASE = 6_000_000_000_000
PRODUCT_ID = str(PRODUCT_BASE + 1)
TITLE = 'Claude 开通指导'
VARIANTS = (('1', '微软邮箱', 8500), ('2', '谷歌邮箱', 10500))
NOTICE = {
    'description': 'Claude 开通操作指导服务，仅适用于买家本人账号。\n\n'
                   '买家本人使用自己的微软邮箱或谷歌邮箱，按官方页面要求自行完成操作与官方验证。'
                   '本店提供操作步骤说明与问题排查指导，不接管账号，不收取密码、验证码或身份证件。\n\n'
                   '本服务仅提供操作指导，不包含账号或会员权益，不提供他人实名身份。'
                   '开通资格、验证要求及最终结果由官方规则决定。',
    'after_sales': '请通过本店联系方式核对指导进度；操作中遇到问题时，请联系本店说明问题，'
                   '以便核对和继续指导。',
}

VPN_PRODUCT_ID = str(PRODUCT_BASE + 2)
# The store's own lines as a client measures them, shown before the panel screenshots.
VPN_LATENCY_IMAGE = '/shop-notices/vpn/latency'
VPN_LATENCY_SECTION = f'''### 实测延迟

本店自用线路在客户端中的测速截图：美国 TCP（VLESS + REALITY）、UDP（Hysteria2 端口跳跃）和 TUIC 线路延迟约 150–210 ms，GPT、Google、Telegram、GitHub 等分流组按测速和可用性自动选择线路。延迟为截图时的测速结果，实际速度以你的服务器线路和所在网络为准。

![各线路与分流组的延迟测速，美国线路约 150–210 ms]({VPN_LATENCY_IMAGE})'''
VPN_PANEL_HEADING = '### 管理面板功能展示'
VPN_NOTICE = {
    'description': '''## VPN 自建搭建指导

本店指导你在自己的境外服务器上搭建私人网络服务，并配置便于日常管理的可视化面板。

> **服务器与域名：本店指导选购。** 选好后远程分步指导完成搭建。

### 指导内容

1. 服务器选购建议与系统初始化。
2. 部署 Hysteria2、Xray（VLESS Reality）、TUIC 等协议的服务端。
3. 搭建管理面板，设置使用者、流量额度、设备数、订阅与周期规则。
4. 各平台客户端的导入配置与常见问题排查。

''' + VPN_LATENCY_SECTION + '''

''' + VPN_PANEL_HEADING + '''

以下为本店自用网络管理面板的脱敏界面示例，域名、用户名及配置地址等关键信息已遮挡。点击图片可查看大图。图中流量、用户数和运行状态仅为截图时的示例，具体部署功能与配置以双方确认的指导范围为准。

#### 1. 用户管理与流量额度

- 为每位使用者单独设置流量额度和设备数，查看在线状态、已用流量与使用趋势。
- 按周期管理用量，超出额度自动停用、按周期自动重置。
- 管理订阅链接与专属面板入口，支持暂停使用、重置订阅和导出用量记录。

![用户管理与流量额度界面，域名和用户名已打码](/shop-notices/vpn/users)

#### 2. 流量分析与异常定位

- 汇总当小时、今日、近 7 天和本周期的用量。
- 通过逐小时趋势图、7 天 × 24 小时热图和用户排行，了解流量使用分布。
- 查看峰值小时、异常告警及历史明细，辅助排查异常用量。

![流量分析与用量热图，域名和用户标识已打码](/shop-notices/vpn/traffic)

#### 3. 订阅模板与路由配置

- 编辑和校验订阅模板 JSON，实时预览运行模式、节点和策略组。
- 集中查看规则集与规则数量，按实际需求配置分流规则。
- 了解订阅模板、路由规则和出口配置之间的关系，便于后续维护。

![订阅模板与路由配置界面，域名和配置地址已打码](/shop-notices/vpn/routing)

#### 4. 运维监控与线路状态

- 查看核心服务、证书、磁盘空间、定时任务和备份状态。
- 按协议查看线路用量和运行状态，辅助定位服务异常。
- 了解流量成本倍率的统计与校准方式，按实际配置管理计量策略。

![运维健康状态与线路用量界面，域名和系统账号已打码](/shop-notices/vpn/operations)

### 预约与使用

请先联系本店说明服务器情况、使用设备及希望实现的功能，确认指导范围后预约时间。请遵守所在地法律法规使用。''',
    'after_sales': '请通过本店联系方式预约指导时间；部署中遇到问题时，请联系本店说明服务器情况，'
                   '以便核对和继续指导。服务器、域名等第三方费用由买家自行承担。',
}

# The student offer ends on this day (Beijing time); the service then leaves the shop by itself.
STUDENT_PRODUCT_ID = str(PRODUCT_BASE + 3)
STUDENT_ENDS_AT = int(datetime(2026, 10, 31, 23, 59, 59, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
# The guide once said what the store does not provide; it now says the store helps choose the
# server and domain. A saved description that still has that line, plain or as a callout, shows
# the new one in its place; everything else the merchant wrote stays as it is.
RETIRED_VPN_LINE = re.compile(
    r'^(?:> ?)?\*\*本店不提供 VPN、节点或账号，服务器与域名由买家自备、费用自付。\*\*[ \t]*服务方式为远程分步指导。[ \t]*$', re.M)
VPN_GUIDE_LINE = '> **服务器与域名：本店指导选购。** 选好后远程分步指导完成搭建。'


def current_vpn_copy(copy):
    description = RETIRED_VPN_LINE.sub(VPN_GUIDE_LINE, copy.get('description', ''))
    return copy if description == copy.get('description', '') else {**copy, 'description': description}


STUDENT_NOTICE = {
    'description': 'ChatGPT 学生认证指导服务，2026 年 10 月 31 日截止，到期后自动下架。\n\n'
                   '适用于符合 OpenAI 学生优惠条件的买家本人账号：本店提供认证流程说明与问题排查指导，'
                   '买家本人按官方页面完成学生身份验证。不接管账号，不收取密码或验证码，'
                   '不提供他人学籍或身份信息。\n\n'
                   '认证资格、优惠内容及审核结果以 OpenAI 官方规则为准。',
    'after_sales': '请在截止日期前下单并完成认证；操作中遇到问题时，请联系本店说明情况，'
                   '以便核对和继续指导。',
}

GROK_IOS_PRODUCT_ID = str(PRODUCT_BASE + 4)
GROK_IOS_NOTICE = {
    'description': '''## SuperGrok 充值｜旧版 · iOS 渠道

**本人账号充值 · 人工发货 · 无需上号**

iOS 印度区正规充值（非零元购），iOS 内购到账，网页、Android、iOS 通用。下单后联系店主人工发卡密，按充值页面填写本人账号的 User ID 即可，无需提供密码。

### 规格：1 个月（月卡）

- **套餐：** 1 个月 SuperGrok 旧版订阅。
- **已有订阅：** 不支持覆盖，请等当前会员到期后再充值。
- **充值地址：** [6661231.xyz](https://6661231.xyz/)，填入卡密和 User ID 提交。

### 规格：3 个月（季卡）

- **套餐：** 一次到账 3 个月 SuperGrok 旧版订阅（30 美元档）。
- **到账：** 通常提交后 **3–15 分钟**，高峰期或个别订单会延迟，请耐心等待。
- **新号、老号均可充：** 建议用当前没有 Grok 会员的账号。已有会员的账号充值后，新套餐要等当前会员到期才更新，部分账号会延迟，**请勿重复充值**。
- **旧版会员：** 可能用不了 bot，额度也可能有问题。只保证充上 3 个月旧版会员，使用上的问题不负责，介意请勿下单。

### 充值前请确认

- **不可囤货，收到后请尽快使用，不承诺未使用卡密永久有效。**
- 确认登录的是需要充值的 Grok 账号，提交前再次核对 User ID。**填错 ID 也会消耗充值凭证，无法退款。**
- 提交后有效期暂未更新时，请先联系本店核查，**不要重复充值**。

### 如何获取 User ID

1. 在浏览器登录 **需要充值的 Grok 账号**。
2. 在同一浏览器打开[账号信息页面](https://grok.com/api/auth/session)。
3. 找到 **userId** 字段，只复制它的值。
4. 页面打不开或没有该字段时，请联系本店。

**只提供 User ID，不要发送整段页面内容、邮箱、密码、Cookie、Token 或其他登录会话信息。**

![Grok User ID 获取示意图，仅展示虚构 ID，可点击放大](/shop-notices/grok/userid-guide)

### 账号使用提醒

批量注册、多人共享、频繁更换 IP、提问或翻译敏感内容等，都可能导致封号。账号风控由平台决定，本商品不承诺账号免封。''',
    'after_sales': '''### 规格：1 个月（月卡）

按所购 1 个月订阅周期质保，期内掉订阅按剩余天数退差价。

### 规格：3 个月（季卡）

质保 90 天不掉订阅，期内掉订阅按剩余天数退差价。bot、额度等旧版会员的使用问题不在质保范围。

### 质保说明

掉订阅请提供订单信息和订阅状态截图联系本店。**账号封禁不在质保范围内。** User ID 填错可能消耗充值凭证，无法退款；充值延迟或失败时请保留订单号联系本店，不要重复提交。''',
}

# The guide before each spec had its own section. A saved field still exactly this text (ignoring
# spacing, bold and line-start callouts) shows today's, which is no longer; changed wording stays.
PREVIOUS_GROK_IOS_NOTICE = {
    'description': '''## SuperGrok 充值｜旧版 · iOS 渠道

**本人账号充值 · 人工发货 · 1 个月 / 3 个月可选**

本商品采用 iOS 印度区充值渠道。下单后请联系本店，由店主人工发放卡密或充值订单号，并确认对应的充值方式。无需提供账号密码，按充值页面要求填写本人账号的 User ID。

### 套餐规格

- **月卡：** 1 个月 SuperGrok 订阅。
- **季卡：** 一次到账 3 个月 SuperGrok 订阅，订阅质保 90 天。
- 充值成功后，使用同一账号在网页、Android 或 iOS 端查看权益；具体权益以账号订阅页面显示为准。

### 充值前请确认

- **不可囤货，收到后请尽快使用，不承诺未使用卡密永久有效。**
- **不支持覆盖已有订阅，请等待当前会员到期后再充值。** 新号、老号均请先核对当前订阅状态。
- 请确认登录的是需要充值的 Grok 账号，并在提交前再次核对 User ID。填错 ID 也可能消耗充值凭证，无法退款。
- 通常在提交后 **3–15 分钟**内完成，高峰期或个别订单可能延迟，以实际处理结果为准。
- 若已提交后发现会员有效期暂未更新，请先联系本店核查；部分账号需要等待原订阅到期或状态同步，**不要重复充值**。

### 充值步骤

1. 选择月卡或季卡，联系本店确认后下单。
2. 联系店主人工发货，领取卡密或充值订单号。
3. 确认账号与 User ID 无误后，按店主说明前往[充值页面](https://6661231.xyz/)提交。
4. 等待处理完成，再进入本人账号检查订阅状态和有效期；异常时保留订单号联系本店。

### 如何获取 User ID

1. 在浏览器登录 **需要充值的 Grok 账号**，确认没有登录错账号。
2. 在同一浏览器打开[账号信息页面](https://grok.com/api/auth/session)。
3. 找到 **userId** 字段，仅复制它对应的值，并在提交充值前再次核对。
4. 如页面无法打开或未显示该字段，请联系本店获取指导。

**只提供 User ID，不要发送整段页面内容、邮箱、密码、Cookie、Token 或其他登录会话信息。**

![Grok User ID 获取示意图，仅展示虚构 ID，可点击放大](/shop-notices/grok/userid-guide)

### 账号使用提醒

请妥善保管账号，避免多人共享或频繁切换登录环境，并按平台规则使用。账号风控与封禁由平台决定，本商品不承诺账号免封。''',
    'after_sales': '''### 订阅质保

- 月卡按所购 1 个月订阅周期提供订阅质保；季卡提供 90 天订阅质保。
- 质保期内掉订阅，请提供订单信息和订阅状态截图联系本店核查，按剩余未使用天数退还差价。
- **账号封禁不在质保范围内。** 本商品保障订阅，不保障账号免于平台风控。

### 充值异常

User ID 填错可能消耗充值凭证，无法退款。遇到延迟、失败或有效期未更新时，请保留订单号联系本店核查，不要重复提交充值。提供截图时请遮挡邮箱及登录会话等敏感信息。

### 卡密使用

收到后请尽快使用，不支持囤货，不承诺未使用卡密永久有效。人工发货与售后处理请通过本店联系方式联系店主。''',
}


def current_grok_ios_copy(copy):
    def comparable(text):
        return re.sub(r'\s+', '', re.sub(r'^> ', '', text.replace('**', ''), flags=re.M))

    def untouched(field):
        return comparable(copy.get(field, '')) == comparable(PREVIOUS_GROK_IOS_NOTICE[field])

    upgraded = {**copy, **{field: GROK_IOS_NOTICE[field] for field in GROK_IOS_NOTICE if untouched(field)}}
    return copy if upgraded == copy else upgraded


BUSINESS_PRODUCT_ID = str(PRODUCT_BASE + 5)
BUSINESS_NOTICE = {
    'description': '''## ChatGPT Business

**OpenAI 官方 ChatGPT Business 方案，本店人工代办开通。**

### 下单前请确认

- 席位数量、订阅周期和开通方式，以下单前与本店确认的为准。
- 开通所需的账号信息由本店告知；请只在本店确认后提供，不要发给其他人。
- 开通后，请在 ChatGPT 设置中核对方案和到期时间。''',
    'after_sales': '''开通异常或方案与确认内容不符时，请保留订单信息和截图联系本店核查。截图请遮挡邮箱等敏感信息。''',
}

DISCOUNT_PRODUCT_ID = str(PRODUCT_BASE + 6)
DISCOUNT_NOTICE = {
    'description': '''## ChatGPT 5折充值｜菲律宾区

**OpenAI 官方渠道直充，符合条件的账号可享半价充值，充值到你自己的账号。**

> **下单前请自行查询半价资格：** 请先自行查询当前账号是否具备半价充值资格，确认符合条件后再下单。不符合资格或尚未确认时，请勿下单。

### 下单前请确认

- **核对充值方案：** 充值方案、适用条件及到期时间，请在下单前核对。
- **充值后核对：** 充值完成后，请在 ChatGPT 设置中核对订阅状态和到期时间。''',
    'after_sales': '''账号不符合资格时请勿下单；已下单的请联系本店处理。充值异常时，请保留订单信息和截图联系本店核查，截图请遮挡邮箱等敏感信息。''',
}


def _new_grok_notice(plan, dollars, quota):
    return {
        'description': f'''## {plan}｜新版 · 1 个月

**正规充值 · 无需上号 · 提供 SSO 即可充值**

### 套餐信息

- **订阅档位：** {plan}（${dollars}/月）。
- **订阅期限：** 1 个月新版订阅。
- **套餐额度：** {quota}。
- **订阅质保：** 1 个月。

### 充值方式

下单后请联系本店，按店主说明**私下提供 SSO**，用于本次账号充值，无需提供账号密码。SSO 属于敏感登录会话信息，请仅通过双方确认的私下渠道提供，不要公开留言或分享。

充值完成后，请在本人账号内核对订阅档位及到期时间。

> **充值成功后不退不换，下单即默认同意以上规则。**''',
        'after_sales': '''### 订阅质保

提供 **1 个月订阅质保**。质保期内订阅状态异常时，请保留订单信息和订阅状态截图联系本店核查，截图请遮挡 SSO 等敏感信息。

### 购买规则

**充值成功后不退不换，下单即默认同意以上规则。**''',
    }


GROK_NEW_PRODUCTS = tuple(
    {'id': str(PRODUCT_BASE + offset), 'title': f'{plan}（新版 · 1个月）', 'category': 'Grok',
     'variants': (('1', '1 个月', cost),), 'notice': _new_grok_notice(plan, dollars, quota),
     'ends_at': None}
    for offset, plan, dollars, quota, cost in (
        (7, 'Grok Plus 1x', 8, 'Plus 标准额度', 0),
        (8, 'Grok Premium 4x', 30, '4 倍 Plus 额度', 0),
        (9, 'Grok Super 15x', 100, '15 倍 Plus 额度', 0),
        (10, 'Grok Ultra 50x', 200, '50 倍 Plus 额度', 0),
    )
)

# Every store-owned product: storefront category, variants (label, administrator-only cost),
# default copy, and the moment it leaves the shop, if any. Zero is the local cost default.
PRODUCTS = (
    {'id': PRODUCT_ID, 'title': TITLE, 'category': 'Claude', 'variants': VARIANTS,
     'notice': NOTICE, 'ends_at': None},
    {'id': VPN_PRODUCT_ID, 'title': 'VPN 搭建指导', 'category': 'VPN',
     'variants': (('1', '远程搭建指导', 0),), 'notice': VPN_NOTICE, 'ends_at': None},
    {'id': STUDENT_PRODUCT_ID, 'title': 'ChatGPT 学生认证', 'category': 'GPT',
     'variants': (('1', '标准规格', 0),), 'notice': STUDENT_NOTICE, 'ends_at': STUDENT_ENDS_AT},
    {'id': GROK_IOS_PRODUCT_ID, 'title': 'SuperGrok 充值（旧版 · iOS）', 'category': 'Grok',
     'variants': (('1', '1 个月（月卡）', 13500), ('2', '3 个月（季卡）', 25800)),
     'notice': GROK_IOS_NOTICE, 'ends_at': None},
    {'id': BUSINESS_PRODUCT_ID, 'title': 'ChatGPT Business', 'category': 'GPT',
     'variants': (('1', '标准规格', 0),), 'notice': BUSINESS_NOTICE, 'ends_at': None},
    {'id': DISCOUNT_PRODUCT_ID, 'title': 'ChatGPT 5折充值｜菲律宾区', 'category': 'GPT',
     'variants': (('1', '标准规格', 0),), 'notice': DISCOUNT_NOTICE, 'ends_at': None},
    *GROK_NEW_PRODUCTS,
)
BY_ID = {product['id']: product for product in PRODUCTS}


def catalog(now=None):
    """Return new rows for each read; prices remain administrator-only source costs.

    Without ``now`` every identity is returned, so saved names stay valid after a
    service has ended. With ``now`` ended services are left out.
    """
    return [
        {
            'key': f'{product["id"]}:{variant_id}',
            'product_id': product['id'],
            'title': product['title'],
            'public_title': product['title'],
            'label': label,
            'source_url': '',
            'cost_cents': cost,
            'available': True,
            'quantity': None,
            'sales': None,
        }
        for product in PRODUCTS
        if now is None or product['ends_at'] is None or now <= product['ends_at']
        for variant_id, label, cost in product['variants']
    ]


def category(product_id):
    return BY_ID[product_id]['category'] if product_id in BY_ID else 'Claude'


def ends_at(product_id):
    return BY_ID[product_id]['ends_at'] if product_id in BY_ID else None
