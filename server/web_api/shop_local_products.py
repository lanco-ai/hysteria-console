"""Private, store-owned services; no supplier fetch or persisted source cache."""

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
VPN_NOTICE = {
    'description': 'VPN 自建搭建指导服务：本店指导你在自己的境外服务器上搭建私人网络服务。'
                   '本店不提供 VPN、节点或账号，服务器与域名由买家自备、费用自付。\n\n'
                   '指导内容：\n'
                   '1. 服务器选购建议与系统初始化。\n'
                   '2. 部署 Hysteria2、Xray（VLESS Reality）、TUIC 等协议的服务端。\n'
                   '3. 搭建管理面板（参考本店自用的网络管理项目）：添加使用者，'
                   '为每位使用者单独设置流量额度和设备数，超出额度自动停用、按周期自动重置，'
                   '并生成订阅链接、查看实时流量。\n'
                   '4. 各平台客户端的导入配置与常见问题排查。\n\n'
                   '服务方式为远程分步指导。请遵守所在地法律法规使用。',
    'after_sales': '请通过本店联系方式预约指导时间；部署中遇到问题时，请联系本店说明服务器情况，'
                   '以便核对和继续指导。服务器、域名等第三方费用由买家自行承担。',
}

# The student offer ends on this day (Beijing time); the service then leaves the shop by itself.
STUDENT_PRODUCT_ID = str(PRODUCT_BASE + 3)
STUDENT_ENDS_AT = int(datetime(2026, 10, 31, 23, 59, 59, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
STUDENT_NOTICE = {
    'description': 'ChatGPT 学生认证指导服务，2026 年 10 月 31 日截止，到期后自动下架。\n\n'
                   '适用于符合 OpenAI 学生优惠条件的买家本人账号：本店提供认证流程说明与问题排查指导，'
                   '买家本人按官方页面完成学生身份验证。不接管账号，不收取密码或验证码，'
                   '不提供他人学籍或身份信息。\n\n'
                   '认证资格、优惠内容及审核结果以 OpenAI 官方规则为准。',
    'after_sales': '请在截止日期前下单并完成认证；操作中遇到问题时，请联系本店说明情况，'
                   '以便核对和继续指导。',
}

# Every store-owned product: storefront category, variants (label, administrator-only cost),
# default copy, and the moment it leaves the shop, if any. Self-run services cost nothing.
PRODUCTS = (
    {'id': PRODUCT_ID, 'title': TITLE, 'category': 'Claude', 'variants': VARIANTS,
     'notice': NOTICE, 'ends_at': None},
    {'id': VPN_PRODUCT_ID, 'title': 'VPN 搭建指导', 'category': 'VPN',
     'variants': (('1', '远程搭建指导', 0),), 'notice': VPN_NOTICE, 'ends_at': None},
    {'id': STUDENT_PRODUCT_ID, 'title': 'ChatGPT 学生认证', 'category': 'GPT',
     'variants': (('1', '标准规格', 0),), 'notice': STUDENT_NOTICE, 'ends_at': STUDENT_ENDS_AT},
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
